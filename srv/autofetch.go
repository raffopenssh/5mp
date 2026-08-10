package srv

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// autofetchSource mirrors the DB row (no credentials exposed).
type autofetchSource struct {
	ID         int64  `json:"id"`
	APIType    string `json:"api_type"`
	ServiceURL string `json:"service_url"`
	Username   string `json:"-"`
	Enabled    bool   `json:"enabled"`
	IntervalH  int    `json:"interval_h"`
	ParkNames  string `json:"park_names"`
	CreatedAt  string `json:"created_at"`
	LastRunAt  string `json:"last_run_at,omitempty"`
	LastStatus string `json:"last_status,omitempty"`
	LastPoints int    `json:"last_points"`
	NextRunAt  string `json:"next_run_at,omitempty"`
}

// ── Credential encryption ────────────────────────────────────────────────────
//
// Passwords are encrypted with AES-256-GCM before storage.
// The key is derived from the AUTOFETCH_SECRET environment variable.
// If the env var is unset, a per-installation random key is generated
// and written to .autofetch_key (gitignored, mode 0600).

const autofetchKeyFile = ".autofetch_key"

// autofetchKey returns the 32-byte AES key, derived from env or keyfile.
func autofetchKey() ([]byte, error) {
	if secret := os.Getenv("AUTOFETCH_SECRET"); secret != "" {
		h := sha256.Sum256([]byte(secret))
		return h[:], nil
	}
	// Fall back to on-disk key file
	data, err := os.ReadFile(autofetchKeyFile)
	if err == nil && len(data) >= 32 {
		return data[:32], nil
	}
	// Generate new key
	key := make([]byte, 32)
	if _, err := io.ReadFull(rand.Reader, key); err != nil {
		return nil, fmt.Errorf("generate autofetch key: %w", err)
	}
	if err := os.WriteFile(autofetchKeyFile, key, 0600); err != nil {
		slog.Warn("autofetch: could not write key file, using ephemeral key", "error", err)
	}
	return key, nil
}

// encryptPassword encrypts plaintext with AES-256-GCM, returns base64.
func encryptPassword(plaintext string) (string, error) {
	key, err := autofetchKey()
	if err != nil {
		return "", err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
		return "", err
	}
	sealed := gcm.Seal(nonce, nonce, []byte(plaintext), nil)
	return base64.StdEncoding.EncodeToString(sealed), nil
}

// decryptPassword decrypts a base64-encoded AES-256-GCM ciphertext.
func decryptPassword(ciphertext string) (string, error) {
	key, err := autofetchKey()
	if err != nil {
		return "", err
	}
	data, err := base64.StdEncoding.DecodeString(ciphertext)
	if err != nil {
		return "", fmt.Errorf("decode base64: %w", err)
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", err
	}
	nonceSize := gcm.NonceSize()
	if len(data) < nonceSize {
		return "", fmt.Errorf("ciphertext too short")
	}
	plaintext, err := gcm.Open(nil, data[:nonceSize], data[nonceSize:], nil)
	if err != nil {
		return "", fmt.Errorf("decrypt: %w", err)
	}
	return string(plaintext), nil
}

// HandleAPIAutofetchList returns configured sources (no credentials).
func (s *Server) HandleAPIAutofetchList(w http.ResponseWriter, r *http.Request) {
	// A subscription is the tenant's own: it names their tracking server, their
	// username, the parks they operate in, and it feeds their patrol pixels.
	// Scoped like every other client-derived table (srv/tenant.go).
	rows, err := s.DB.QueryContext(r.Context(),
		`SELECT id, api_type, service_url, username, enabled, interval_h,
		        park_names, created_at, last_run_at, last_status, last_points
		 FROM autofetch_sources WHERE env = ? ORDER BY created_at DESC`, RequestEnv(r))
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
		// Compute next run time
		if src.Enabled {
			if !lastRun.Valid {
				src.NextRunAt = "soon" // never run yet, will run on next tick
			} else {
				parsed, err := time.Parse("2006-01-02 15:04:05", lastRun.String)
				if err != nil {
					parsed, err = time.Parse(time.RFC3339, lastRun.String)
				}
				if err == nil {
					nextRun := parsed.Add(time.Duration(src.IntervalH) * time.Hour)
					src.NextRunAt = nextRun.UTC().Format("2006-01-02 15:04:05")
				}
			}
		}
		sources = append(sources, src)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"sources": sources})
}

// HandleAPIAutofetchAdd validates credentials, discovers parks, encrypts password, and stores.
func (s *Server) HandleAPIAutofetchAdd(w http.ResponseWriter, r *http.Request) {
	var req struct {
		APIType       string `json:"api_type"`
		ServiceURL    string `json:"service_url"`
		Username      string `json:"username"`
		Password      string `json:"password"`
		IntervalH     int    `json:"interval_h"`
		BackfillSince string `json:"backfill_since"`
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

	// Probe: authenticate and discover park names
	parkNames, err := probeEarthRanger(req.ServiceURL, req.Username, req.Password)
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": fmt.Sprintf("Connection failed: %v", err)})
		return
	}

	// Encrypt password before storage
	encrypted, err := encryptPassword(req.Password)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "Failed to encrypt credentials"})
		return
	}

	result, err := s.DB.ExecContext(r.Context(),
		`INSERT INTO autofetch_sources (api_type, service_url, username, password, interval_h, enabled, park_names, env)
		 VALUES (?, ?, ?, ?, ?, 1, ?, ?)`,
		req.APIType, req.ServiceURL, req.Username, encrypted, req.IntervalH, parkNames, RequestEnv(r))
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	id, _ := result.LastInsertId()

	// If backfill_since is set, update last_run_at so the first run fetches from that date.
	if req.BackfillSince != "" {
		_, err = s.DB.ExecContext(r.Context(),
			`UPDATE autofetch_sources SET last_run_at = ? WHERE id = ?`,
			req.BackfillSince+" 00:00:00", id)
		if err != nil {
			slog.Warn("failed to set backfill_since", "id", id, "err", err)
		}
	}

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
		`UPDATE autofetch_sources SET enabled = 0, password = '' WHERE id = ? AND env = ?`,
		id, RequestEnv(r))
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
		`SELECT service_url, username FROM autofetch_sources WHERE id = ? AND env = ?`,
		req.ID, RequestEnv(r)).Scan(&serviceURL, &username)
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": "source not found"})
		return
	}

	if _, err := probeEarthRanger(serviceURL, username, req.Password); err != nil {
		writeJSON(w, 400, map[string]string{"error": fmt.Sprintf("Authentication failed: %v", err)})
		return
	}

	// Encrypt password before storage
	encrypted, err := encryptPassword(req.Password)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "Failed to encrypt credentials"})
		return
	}

	_, err = s.DB.ExecContext(r.Context(),
		`UPDATE autofetch_sources SET enabled = 1, password = ? WHERE id = ?`, encrypted, req.ID)
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
		`DELETE FROM autofetch_sources WHERE id = ? AND env = ?`, id, RequestEnv(r))
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
	// Ownership is checked HERE, not in the worker: runAutofetchSource is also
	// the scheduler's entry point and must not need a request to run.
	var owned int
	if s.DB.QueryRow(`SELECT 1 FROM autofetch_sources WHERE id = ? AND env = ?`,
		id, RequestEnv(r)).Scan(&owned) != nil {
		writeJSON(w, 404, map[string]string{"error": "source not found"})
		return
	}
	go s.runAutofetchSource(context.Background(), id)
	writeJSON(w, 200, map[string]string{"ok": "started"})
}

// ── Background worker ────────────────────────────────────────────────────────

// StartAutofetchWorker checks sources every 15 minutes and runs overdue ones.
func (s *Server) StartAutofetchWorker(ctx context.Context) {
	slog.Info("autofetch worker started")
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

// runAutofetchSource decrypts credentials and runs the Python fetch script.
func (s *Server) runAutofetchSource(ctx context.Context, id int64) {
	var apiType, serviceURL, username, encryptedPw, env string
	var intervalH int
	var lastRunAt sql.NullString
	err := s.DB.QueryRowContext(ctx,
		`SELECT api_type, service_url, username, password, interval_h, last_run_at, env
		 FROM autofetch_sources WHERE id = ? AND enabled = 1 AND password != ''`, id,
	).Scan(&apiType, &serviceURL, &username, &encryptedPw, &intervalH, &lastRunAt, &env)
	if err != nil {
		slog.Error("autofetch: source not found or disabled", "id", id, "error", err)
		return
	}

	// Decrypt password
	password, err := decryptPassword(encryptedPw)
	if err != nil {
		slog.Error("autofetch: failed to decrypt credentials", "id", id, "error", err)
		_, _ = s.DB.ExecContext(ctx,
			`UPDATE autofetch_sources SET last_status = 'error: credential decryption failed' WHERE id = ?`, id)
		return
	}

	// Calculate --since timestamp from last_run_at.
	// If we have a previous run time, fetch only data since then (with 30min overlap for safety).
	// This avoids refetching the full --days window on manual "Run Now".
	var sinceArg string
	if lastRunAt.Valid && lastRunAt.String != "" {
		parsed, parseErr := time.Parse("2006-01-02 15:04:05", lastRunAt.String)
		if parseErr != nil {
			parsed, parseErr = time.Parse(time.RFC3339, lastRunAt.String)
		}
		if parseErr == nil {
			// Overlap by 30 minutes to catch any edge-case data
			sinceTime := parsed.Add(-30 * time.Minute)
			sinceArg = sinceTime.UTC().Format(time.RFC3339)
		}
	}

	slog.Info("autofetch: running", "id", id, "url", serviceURL, "since", sinceArg)

	// The fetched tracks are the subscribing tenant's patrol data, so they must
	// land in THAT tenant -- not in whichever password happens to be first in
	// ACCESS_PASSWORDS. The upload endpoint derives the tenant from the
	// password, so the worker looks up a password for this source's env; if the
	// mapping no longer names one (a password was removed from PASSWORD_ENVS),
	// it refuses rather than silently filing the tracks under someone else.
	pwd := pwdForTenant(env)
	if pwd == "" {
		slog.Error("autofetch: no access password maps to this source's tenant", "id", id, "env", env)
		_, _ = s.DB.ExecContext(ctx,
			`UPDATE autofetch_sources SET last_status = 'error: no password configured for this account' WHERE id = ?`, id)
		return
	}
	uploadURL := fmt.Sprintf("http://localhost:%s/api/upload/async?pwd=%s", s.listenPort(), url.QueryEscape(pwd))

	args := []string{"scripts/fetch_earthranger_gpx.py",
		"--url", serviceURL,
		"--user", username,
		"--upload-url", uploadURL,
	}
	if sinceArg != "" {
		args = append(args, "--since", sinceArg)
	} else {
		args = append(args, "--days", strconv.Itoa(max(intervalH/24, 1)))
	}

	cmd := exec.CommandContext(ctx, "python3", args...)
	// Pass password via environment variable — not command-line args
	cmd.Env = append(os.Environ(), "EARTHRANGER_PASSWORD="+password)

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

// ── Upload queue cleanup ─────────────────────────────────────────────────────

// StartUploadQueueCleanup periodically purges completed upload_queue entries
// older than 7 days to remove stored file BLOBs.
func (s *Server) StartUploadQueueCleanup(ctx context.Context) {
	ticker := time.NewTicker(6 * time.Hour)
	defer ticker.Stop()

	// Initial cleanup after 5 minutes
	time.Sleep(5 * time.Minute)
	s.cleanupUploadQueue(ctx)

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.cleanupUploadQueue(ctx)
		}
	}
}

func (s *Server) cleanupUploadQueue(ctx context.Context) {
	result, err := s.DB.ExecContext(ctx,
		`DELETE FROM upload_queue WHERE completed_at < datetime('now', '-7 days')`)
	if err != nil {
		slog.Error("upload queue cleanup failed", "error", err)
		return
	}
	if n, _ := result.RowsAffected(); n > 0 {
		slog.Info("upload queue cleanup", "deleted", n)
	}
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
	parkNames := extractParkName(serviceURL)

	return parkNames, nil
}

// extractParkName derives park name(s) from a PAMDAS URL.
func extractParkName(serviceURL string) string {
	known := map[string]string{
		"nyerere":   "Nyerere NP, Ruaha NP",
		"chinko":    "Chinko",
		"virunga":   "Virunga NP",
		"garamba":   "Garamba NP",
		"odzala":    "Odzala-Kokoua NP",
		"akagera":   "Akagera NP",
		"zakouma":   "Zakouma NP",
		"pendjari":  "Pendjari NP",
		"limpopo":   "Limpopo NP",
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
	return "8000"
}

// HandleAPIAutofetchScript serves the fetch_earthranger_gpx.py source code
// so users can inspect exactly what runs during autofetch.
func (s *Server) HandleAPIAutofetchScript(w http.ResponseWriter, r *http.Request) {
	data, err := os.ReadFile("scripts/fetch_earthranger_gpx.py")
	if err != nil {
		http.Error(w, "Script not found", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Write(data)
}
