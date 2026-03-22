package srv

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// autofetchSource mirrors the DB row.
type autofetchSource struct {
	ID         int64  `json:"id"`
	APIType    string `json:"api_type"`
	ServiceURL string `json:"service_url"`
	Username   string `json:"username"`
	Enabled    bool   `json:"enabled"`
	IntervalH  int    `json:"interval_h"`
	ParkNames  string `json:"park_names"`
	CreatedAt  string `json:"created_at"`
	LastRunAt  string `json:"last_run_at,omitempty"`
	LastStatus string `json:"last_status,omitempty"`
	LastPoints int    `json:"last_points"`
}

// HandleAPIAutofetchList returns configured sources (no credentials).
func (s *Server) HandleAPIAutofetchList(w http.ResponseWriter, r *http.Request) {
	rows, err := s.DB.QueryContext(r.Context(),
		`SELECT id, api_type, service_url, username, enabled, interval_h,
		        park_names, created_at, last_run_at, last_status, last_points
		 FROM autofetch_sources ORDER BY created_at DESC`)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	defer rows.Close()

	var sources []autofetchSource
	for rows.Next() {
		var src autofetchSource
		var lastRun, lastStatus sql.NullString
		var enabled int
		if err := rows.Scan(&src.ID, &src.APIType, &src.ServiceURL, &src.Username,
			&enabled, &src.IntervalH, &src.ParkNames, &src.CreatedAt,
			&lastRun, &lastStatus, &src.LastPoints); err != nil {
			continue
		}
		src.Enabled = enabled == 1
		if lastRun.Valid {
			src.LastRunAt = lastRun.String
		}
		if lastStatus.Valid {
			src.LastStatus = lastStatus.String
		}
		sources = append(sources, src)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"sources": sources})
}

// HandleAPIAutofetchAdd validates credentials, discovers parks, and stores.
func (s *Server) HandleAPIAutofetchAdd(w http.ResponseWriter, r *http.Request) {
	var req struct {
		APIType    string `json:"api_type"`
		ServiceURL string `json:"service_url"`
		Username   string `json:"username"`
		Password   string `json:"password"`
		IntervalH  int    `json:"interval_h"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid JSON"})
		return
	}
	req.ServiceURL = strings.TrimRight(req.ServiceURL, "/")
	if req.ServiceURL == "" || req.Username == "" || req.Password == "" {
		writeJSON(w, 400, map[string]string{"error": "service_url, username, and password are required"})
		return
	}
	if req.IntervalH <= 0 {
		req.IntervalH = 24
	}

	// Probe: authenticate and discover park names from patrol/subject data
	parkNames, err := probeEarthRanger(req.ServiceURL, req.Username, req.Password)
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": fmt.Sprintf("Connection failed: %v", err)})
		return
	}

	result, err := s.DB.ExecContext(r.Context(),
		`INSERT INTO autofetch_sources (api_type, service_url, username, password, interval_h, enabled, park_names)
		 VALUES (?, ?, ?, ?, ?, 1, ?)`,
		req.APIType, req.ServiceURL, req.Username, req.Password, req.IntervalH, parkNames)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	id, _ := result.LastInsertId()

	slog.Info("autofetch source added", "id", id, "url", req.ServiceURL, "parks", parkNames)
	writeJSON(w, 200, map[string]interface{}{"id": id, "park_names": parkNames})
}

// HandleAPIAutofetchDisable clears the password (user must re-enter to reactivate).
func (s *Server) HandleAPIAutofetchDisable(w http.ResponseWriter, r *http.Request) {
	id, _ := strconv.ParseInt(r.FormValue("id"), 10, 64)
	if id == 0 {
		writeJSON(w, 400, map[string]string{"error": "id required"})
		return
	}
	_, err := s.DB.ExecContext(r.Context(),
		`UPDATE autofetch_sources SET enabled = 0, password = '' WHERE id = ?`, id)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	slog.Info("autofetch source disabled", "id", id)
	writeJSON(w, 200, map[string]string{"ok": "disabled"})
}

// HandleAPIAutofetchEnable re-enables with a new password.
func (s *Server) HandleAPIAutofetchEnable(w http.ResponseWriter, r *http.Request) {
	var req struct {
		ID       int64  `json:"id"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid JSON"})
		return
	}
	if req.ID == 0 || req.Password == "" {
		writeJSON(w, 400, map[string]string{"error": "id and password required"})
		return
	}

	// Validate credentials before re-enabling
	var serviceURL, username string
	err := s.DB.QueryRowContext(r.Context(),
		`SELECT service_url, username FROM autofetch_sources WHERE id = ?`, req.ID).Scan(&serviceURL, &username)
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": "source not found"})
		return
	}

	if _, err := probeEarthRanger(serviceURL, username, req.Password); err != nil {
		writeJSON(w, 400, map[string]string{"error": fmt.Sprintf("Authentication failed: %v", err)})
		return
	}

	_, err = s.DB.ExecContext(r.Context(),
		`UPDATE autofetch_sources SET enabled = 1, password = ? WHERE id = ?`, req.Password, req.ID)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	slog.Info("autofetch source re-enabled", "id", req.ID)
	writeJSON(w, 200, map[string]string{"ok": "enabled"})
}

// HandleAPIAutofetchDelete removes the source and all credentials.
func (s *Server) HandleAPIAutofetchDelete(w http.ResponseWriter, r *http.Request) {
	id, _ := strconv.ParseInt(r.FormValue("id"), 10, 64)
	if id == 0 {
		writeJSON(w, 400, map[string]string{"error": "id required"})
		return
	}
	_, err := s.DB.ExecContext(r.Context(),
		`DELETE FROM autofetch_sources WHERE id = ?`, id)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	slog.Info("autofetch source deleted", "id", id)
	writeJSON(w, 200, map[string]string{"ok": "deleted"})
}

// HandleAPIAutofetchRunNow triggers an immediate fetch for a source.
func (s *Server) HandleAPIAutofetchRunNow(w http.ResponseWriter, r *http.Request) {
	id, _ := strconv.ParseInt(r.FormValue("id"), 10, 64)
	if id == 0 {
		writeJSON(w, 400, map[string]string{"error": "id required"})
		return
	}
	go s.runAutofetchSource(context.Background(), id)
	writeJSON(w, 200, map[string]string{"ok": "started"})
}

// ── Background worker ────────────────────────────────────────────────────────

// StartAutofetchWorker checks sources every 15 minutes and runs overdue ones.
func (s *Server) StartAutofetchWorker(ctx context.Context) {
	ticker := time.NewTicker(15 * time.Minute)
	defer ticker.Stop()

	// Initial check after 1 minute (let server start up)
	time.Sleep(1 * time.Minute)
	s.runAutofetchDue(ctx)

	for {
		select {
		case <-ctx.Done():
			slog.Info("autofetch worker shutting down")
			return
		case <-ticker.C:
			s.runAutofetchDue(ctx)
		}
	}
}

// runAutofetchDue finds enabled sources whose interval has elapsed and runs them.
func (s *Server) runAutofetchDue(ctx context.Context) {
	rows, err := s.DB.QueryContext(ctx,
		`SELECT id, interval_h, last_run_at FROM autofetch_sources
		 WHERE enabled = 1 AND password != ''`)
	if err != nil {
		slog.Error("autofetch: query sources", "error", err)
		return
	}
	defer rows.Close()

	now := time.Now().UTC()
	var due []int64
	for rows.Next() {
		var id int64
		var intervalH int
		var lastRun sql.NullString
		if err := rows.Scan(&id, &intervalH, &lastRun); err != nil {
			continue
		}
		if !lastRun.Valid {
			due = append(due, id) // never run
			continue
		}
		parsed, err := time.Parse("2006-01-02 15:04:05", lastRun.String)
		if err != nil {
			parsed, err = time.Parse(time.RFC3339, lastRun.String)
		}
		if err != nil {
			due = append(due, id) // can't parse, run it
			continue
		}
		if now.Sub(parsed) >= time.Duration(intervalH)*time.Hour {
			due = append(due, id)
		}
	}

	for _, id := range due {
		s.runAutofetchSource(ctx, id)
	}
}

// runAutofetchSource executes the Python fetch script for a single source.
func (s *Server) runAutofetchSource(ctx context.Context, id int64) {
	var apiType, serviceURL, username, password string
	var intervalH int
	err := s.DB.QueryRowContext(ctx,
		`SELECT api_type, service_url, username, password, interval_h
		 FROM autofetch_sources WHERE id = ? AND enabled = 1 AND password != ''`, id,
	).Scan(&apiType, &serviceURL, &username, &password, &intervalH)
	if err != nil {
		slog.Error("autofetch: source not found or disabled", "id", id, "error", err)
		return
	}

	slog.Info("autofetch: running", "id", id, "url", serviceURL)

	uploadURL := fmt.Sprintf("http://localhost:%s/api/upload/async?pwd=test2026", s.listenPort())

	cmd := exec.CommandContext(ctx, "python3", "scripts/fetch_earthranger_gpx.py",
		"--url", serviceURL,
		"--user", username,
		"--pass", password,
		"--upload-url", uploadURL,
		"--days", strconv.Itoa(max(intervalH/24, 1)),
	)

	output, err := cmd.CombinedOutput()

	status := "ok"
	points := 0
	if err != nil {
		status = fmt.Sprintf("error: %v", err)
		slog.Error("autofetch: script failed", "id", id, "error", err, "output", string(output))
	} else {
		// Parse JSON output from script
		var result struct {
			OK     bool   `json:"ok"`
			Points int    `json:"points"`
			Error  string `json:"error"`
		}
		if json.Unmarshal(output, &result) == nil {
			points = result.Points
			if !result.OK {
				status = fmt.Sprintf("error: %s", result.Error)
			}
		}
		slog.Info("autofetch: completed", "id", id, "points", points, "status", status)
	}

	_, _ = s.DB.ExecContext(ctx,
		`UPDATE autofetch_sources SET last_run_at = CURRENT_TIMESTAMP, last_status = ?, last_points = ? WHERE id = ?`,
		status, points, id)
}

// ── Helpers ───────────────────────────────────────────────────────────────

func writeJSON(w http.ResponseWriter, code int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

// probeEarthRanger authenticates and returns discovered park names.
func probeEarthRanger(serviceURL, username, password string) (string, error) {
	client := &http.Client{Timeout: 30 * time.Second}

	// Try common client IDs
	var token string
	for _, cid := range []string{"das_web_client", "er_mobile_tracker"} {
		resp, err := client.PostForm(serviceURL+"/oauth2/token",
			map[string][]string{
				"grant_type": {"password"},
				"username":   {username},
				"password":   {password},
				"client_id":  {cid},
			})
		if err != nil {
			return "", fmt.Errorf("cannot reach %s: %w", serviceURL, err)
		}
		defer resp.Body.Close()
		if resp.StatusCode == 200 {
			var tok struct {
				AccessToken string `json:"access_token"`
			}
			json.NewDecoder(resp.Body).Decode(&tok)
			if tok.AccessToken != "" {
				token = tok.AccessToken
				break
			}
		}
	}
	if token == "" {
		return "", fmt.Errorf("authentication failed (invalid credentials)")
	}

	// Discover park names from the PAMDAS instance hostname
	// e.g. "nyerere.pamdas.org" -> try to read patrol area names
	parkNames := extractParkName(serviceURL)

	return parkNames, nil
}

// extractParkName derives park name(s) from a PAMDAS URL.
// e.g. "https://nyerere.pamdas.org" -> "Nyerere NP"
func extractParkName(serviceURL string) string {
	// Known mappings for PAMDAS instances
	known := map[string]string{
		"nyerere":  "Nyerere NP, Ruaha NP",
		"chinko":   "Chinko",
		"virunga":  "Virunga NP",
		"garamba":  "Garamba NP",
		"odzala":   "Odzala-Kokoua NP",
		"akagera":  "Akagera NP",
		"zakouma":  "Zakouma NP",
		"pendjari": "Pendjari NP",
		"limpopo":  "Limpopo NP",
		"serengeti": "Serengeti NP",
	}

	url := strings.ToLower(serviceURL)
	for key, name := range known {
		if strings.Contains(url, key) {
			return name
		}
	}

	// Fallback: extract subdomain
	parts := strings.Split(strings.TrimPrefix(strings.TrimPrefix(url, "https://"), "http://"), ".")
	if len(parts) > 0 {
		name := parts[0]
		if len(name) > 0 {
			return strings.ToUpper(name[:1]) + name[1:]
		}
		return name
	}
	return serviceURL
}

// listenPort returns the port the server is listening on.
func (s *Server) listenPort() string {
	// Default to 8000; the server address is set at startup
	return "8000"
}
