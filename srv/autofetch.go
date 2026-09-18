package srv

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"srv.exe.dev/db/dbgen"
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
	Title      string `json:"title"`
	Enabled    bool   `json:"enabled"`
	IntervalH  int    `json:"interval_h"`
	ParkNames  string `json:"park_names"`
	CreatedAt  string `json:"created_at"`
	LastRunAt  string `json:"last_run_at,omitempty"`
	LastStatus string `json:"last_status,omitempty"`
	LastPoints int    `json:"last_points"`
	NextRunAt  string `json:"next_run_at,omitempty"`
	// Mine: the caller owns this source and may edit it. A source shared with
	// the caller is listed read-only so they can see where their pixels come
	// from, but never who else is on it.
	Mine       bool              `json:"mine"`
	Visibility string            `json:"visibility"`
	Viewers    []autofetchViewer `json:"viewers,omitempty"`
	// Contribution summary, derived from the source's own env.
	Uploads int     `json:"uploads"`
	TotalKm float64 `json:"total_km"`
}

// autofetchViewer is a login the source is shared with. The label is the
// first three characters of the password plus an ellipsis -- enough to
// recognise a chip, not enough to reconstruct anything (SeedPrincipals).
type autofetchViewer struct {
	Ref   string `json:"ref"`
	Label string `json:"label"`
}

// ── Credential encryption ────────────────────────────────────────────────────
//
// Credentials are sealed with the shared master key (srv/filecrypt.go:
// SHARED_FILES_KEY or data/shared_files/.key), stored as "v2:" + base64.
// Rows written before that used a per-feature key (.autofetch_key or
// AUTOFETCH_SECRET); they are re-sealed at startup by migrateAutofetchLegacy
// and the legacy key is only ever consulted for rows without the prefix.

const (
	autofetchKeyFile  = ".autofetch_key"
	autofetchV2Prefix = "v2:"
)

// legacyAutofetchKey returns the pre-v2 AES key, or an error when none exists
// (a fresh install has no legacy rows and must not mint a key nobody uses).
func legacyAutofetchKey() ([]byte, error) {
	if secret := os.Getenv("AUTOFETCH_SECRET"); secret != "" {
		h := sha256.Sum256([]byte(secret))
		return h[:], nil
	}
	data, err := os.ReadFile(autofetchKeyFile)
	if err == nil && len(data) >= 32 {
		return data[:32], nil
	}
	return nil, fmt.Errorf("no legacy autofetch key")
}

// encryptPassword seals plaintext under the master key.
func encryptPassword(plaintext string) (string, error) {
	blob, err := sealSecret(plaintext)
	if err != nil {
		return "", err
	}
	return autofetchV2Prefix + base64.StdEncoding.EncodeToString(blob), nil
}

// decryptPassword opens a stored credential, v2 or legacy.
func decryptPassword(ciphertext string) (string, error) {
	if strings.HasPrefix(ciphertext, autofetchV2Prefix) {
		blob, err := base64.StdEncoding.DecodeString(ciphertext[len(autofetchV2Prefix):])
		if err != nil {
			return "", fmt.Errorf("decode base64: %w", err)
		}
		return openSecret(blob)
	}
	key, err := legacyAutofetchKey()
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

// ── Ownership & legacy migration ─────────────────────────────────────────────

// autofetchDataEnv is the env a source's fetched tracks are filed under.
func autofetchDataEnv(id int64) string { return "af" + strconv.FormatInt(id, 10) }

// autofetchAllowed is the gate every autofetch handler passes first: a login
// (never a guest -- a link must not mint or read subscriptions) outside the
// public sandbox. Mirrors tileSourcesAllowed.
func autofetchAllowed(r *http.Request) (string, bool) {
	if GuestFromRequest(r) != nil {
		return "", false
	}
	ref := shortCallerRef(r)
	if ref == "" || RequestEnv(r) == sandboxTenant {
		return "", false
	}
	return ref, true
}

// autofetchOwned resolves id to a source the caller owns; 404 otherwise (an
// id must not be an oracle, AGENTS.md invariant 6).
func (s *Server) autofetchOwned(r *http.Request, id int64) bool {
	ref, ok := autofetchAllowed(r)
	if !ok || id == 0 {
		return false
	}
	var one int
	return s.DB.QueryRowContext(r.Context(),
		`SELECT 1 FROM autofetch_sources WHERE id = ? AND owner_ref = ?`, id, ref).Scan(&one) == nil
}

// envFeedsLearning reports whether GPX filed under env may feed the learned
// feature tables (roads, airstrips, camps -- which have no env column and are
// served only to the client tenant, srv/test_env_guard.go). The client tenant
// itself qualifies, and so does an autofetch env whose owner is a client
// tenant login: those tracks were client data before they had their own env.
func (s *Server) envFeedsLearning(ctx context.Context, env string) bool {
	if env == clientTenant {
		return true
	}
	if !strings.HasPrefix(env, "af") {
		return false
	}
	var owner string
	if err := s.DB.QueryRowContext(ctx,
		`SELECT owner_ref FROM autofetch_sources WHERE data_env = ?`, env).Scan(&owner); err != nil {
		return false
	}
	return tenantForRef(owner) == clientTenant
}

// migrateAutofetchLegacy finishes migration 065 for rows created before it:
//
//  1. owner_ref: the first configured password of the row's tenant (the
//     login that would have seen it before), visibility 'selected' with every
//     OTHER login of that tenant as a viewer -- so exactly the same people see
//     exactly the same pixels the morning after the deploy.
//  2. data_env: the fetched tracks move from the tenant env to 'af<id>'.
//     Uploads are recognised by filename ('autofetch-*.gpx'); subcell_visits
//     have no upload link, so their rows move by DATE RANGE and only when the
//     tenant's manual uploads and its autofetch uploads occupy disjoint
//     ranges -- otherwise they stay put and the caller is told (invariant 1).
//     effort_data is rebuilt from the re-tagged rows.
//  3. credentials: legacy ciphertexts are re-sealed under the master key.
//
// Idempotent: a row with owner_ref set is done.
func (s *Server) migrateAutofetchLegacy(ctx context.Context) {
	rows, err := s.DB.QueryContext(ctx,
		`SELECT id, env, password FROM autofetch_sources WHERE owner_ref = '' OR data_env = '' OR (password != '' AND password NOT LIKE 'v2:%')`)
	if err != nil {
		slog.Warn("autofetch migrate: query", "error", err)
		return
	}
	type row struct {
		id       int64
		env, pwd string
	}
	var todo []row
	for rows.Next() {
		var x row
		if rows.Scan(&x.id, &x.env, &x.pwd) == nil {
			todo = append(todo, x)
		}
	}
	rows.Close()
	if len(todo) == 0 {
		return
	}
	moved := false
	for _, x := range todo {
		// 3. credentials
		if x.pwd != "" && !strings.HasPrefix(x.pwd, autofetchV2Prefix) {
			if plain, err := decryptPassword(x.pwd); err == nil {
				if enc, err := encryptPassword(plain); err == nil {
					_, _ = s.DB.ExecContext(ctx, `UPDATE autofetch_sources SET password = ? WHERE id = ?`, enc, x.id)
				}
			} else {
				slog.Warn("autofetch migrate: legacy credential unreadable; source must be re-enabled", "id", x.id, "error", err)
			}
		}
		// 1. owner + viewers
		var owner string
		_ = s.DB.QueryRowContext(ctx, `SELECT owner_ref FROM autofetch_sources WHERE id = ?`, x.id).Scan(&owner)
		if owner == "" {
			var viewers []string
			for _, p := range validPasswords {
				p = strings.TrimSpace(p)
				if p == "" || tenantForPwd(p) != x.env {
					continue
				}
				if owner == "" {
					owner = principalRef(p)
				} else {
					viewers = append(viewers, principalRef(p))
				}
			}
			if owner == "" {
				slog.Warn("autofetch migrate: no login maps to tenant; source left ownerless", "id", x.id, "env", x.env)
				continue
			}
			_, _ = s.DB.ExecContext(ctx,
				`UPDATE autofetch_sources SET owner_ref = ?, visibility = 'selected' WHERE id = ?`, owner, x.id)
			for _, v := range viewers {
				_, _ = s.DB.ExecContext(ctx,
					`INSERT OR IGNORE INTO autofetch_viewers (source_id, principal_ref, label)
					 SELECT ?, ?, COALESCE((SELECT label FROM principals WHERE ref = ?), '')`, x.id, v, v)
			}
		}
		// 2. data env
		var dataEnv string
		_ = s.DB.QueryRowContext(ctx, `SELECT data_env FROM autofetch_sources WHERE id = ?`, x.id).Scan(&dataEnv)
		if dataEnv == "" {
			dataEnv = autofetchDataEnv(x.id)
			if s.moveAutofetchUploads(ctx, x.env, dataEnv) {
				moved = true
			}
			_, _ = s.DB.ExecContext(ctx, `UPDATE autofetch_sources SET data_env = ? WHERE id = ?`, dataEnv, x.id)
		}
		slog.Info("autofetch migrate: source finished", "id", x.id, "owner", owner, "data_env", dataEnv)
	}
	if moved {
		s.rebuildAllEffortData()
	}
	patrolACLVersion.Add(1)
}

// moveAutofetchUploads re-tags one tenant's autofetch uploads into dataEnv.
// Returns true when anything moved. Assumes one legacy source per tenant
// (true for every deployment that ran migration 049).
func (s *Server) moveAutofetchUploads(ctx context.Context, fromEnv, dataEnv string) bool {
	res, err := s.DB.ExecContext(ctx,
		`UPDATE gpx_uploads SET env = ? WHERE env = ? AND filename LIKE 'autofetch-%'`, dataEnv, fromEnv)
	if err != nil {
		slog.Warn("autofetch migrate: gpx_uploads", "error", err)
		return false
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return false
	}
	_, _ = s.DB.ExecContext(ctx,
		`UPDATE track_points SET env = ? WHERE upload_id IN (SELECT id FROM gpx_uploads WHERE env = ?)`, dataEnv, dataEnv)
	_, _ = s.DB.ExecContext(ctx,
		`UPDATE upload_queue SET env = ? WHERE env = ? AND filename LIKE 'autofetch-%'`, dataEnv, fromEnv)
	_, _ = s.DB.ExecContext(ctx,
		`UPDATE gpx_upload_logs SET env = ? WHERE upload_id IN (SELECT id FROM gpx_uploads WHERE env = ?)`, dataEnv, dataEnv)
	_, _ = s.DB.ExecContext(ctx,
		`UPDATE notifications SET env = ? WHERE notification_type = 'new_upload'
		   AND CAST(reference_id AS INTEGER) IN (SELECT id FROM gpx_uploads WHERE env = ?)`, dataEnv, dataEnv)
	// subcell_visits: by date, only when the ranges do not overlap.
	var manualMax, autoMin sql.NullString
	_ = s.DB.QueryRowContext(ctx, `SELECT MAX(substr(end_time,1,10)) FROM gpx_uploads WHERE env = ? AND filename NOT LIKE 'autofetch-%'`, fromEnv).Scan(&manualMax)
	_ = s.DB.QueryRowContext(ctx, `SELECT MIN(substr(start_time,1,10)) FROM gpx_uploads WHERE env = ?`, dataEnv).Scan(&autoMin)
	switch {
	case !autoMin.Valid:
		slog.Warn("autofetch migrate: subcell visits not moved (no autofetch start date)", "env", fromEnv)
	case manualMax.Valid && manualMax.String >= autoMin.String:
		slog.Warn("autofetch migrate: subcell visits NOT moved -- manual and autofetch date ranges overlap; coverage stays in the tenant env",
			"env", fromEnv, "manual_max", manualMax.String, "auto_min", autoMin.String)
	default:
		r2, err := s.DB.ExecContext(ctx,
			`UPDATE subcell_visits SET env = ? WHERE env = ? AND substr(visit_date,1,10) >= ?`, dataEnv, fromEnv, autoMin.String)
		if err == nil {
			m, _ := r2.RowsAffected()
			slog.Info("autofetch migrate: subcell visits moved", "env", fromEnv, "to", dataEnv, "rows", m, "since", autoMin.String)
		}
	}
	slog.Info("autofetch migrate: uploads moved", "env", fromEnv, "to", dataEnv, "uploads", n)
	return true
}

// ── Handlers ─────────────────────────────────────────────────────────────────

// HandleAPIAutofetchList returns the caller's own sources (editable) and the
// sources shared with them (read-only), never credentials.
func (s *Server) HandleAPIAutofetchList(w http.ResponseWriter, r *http.Request) {
	ref, ok := autofetchAllowed(r)
	if !ok {
		writeJSON(w, 200, map[string]interface{}{"sources": []autofetchSource{}, "readonly": true})
		return
	}
	rows, err := s.DB.QueryContext(r.Context(),
		`SELECT a.id, a.api_type, a.service_url, a.username, a.title, a.enabled, a.interval_h,
		        a.park_names, a.created_at, a.last_run_at, a.last_status, a.last_points,
		        a.owner_ref = ?, a.visibility, a.data_env
		 FROM autofetch_sources a
		 WHERE a.owner_ref = ? OR a.visibility = 'all'
		    OR (a.visibility = 'selected' AND EXISTS (
		          SELECT 1 FROM autofetch_viewers v WHERE v.source_id = a.id AND v.principal_ref = ?))
		 ORDER BY a.owner_ref = ? DESC, a.created_at DESC`, ref, ref, ref, ref)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	defer rows.Close()

	sources := []autofetchSource{}
	for rows.Next() {
		var src autofetchSource
		var lastRun, lastStatus sql.NullString
		var enabled, mine int
		var dataEnv string
		if err := rows.Scan(&src.ID, &src.APIType, &src.ServiceURL, &src.Username, &src.Title,
			&enabled, &src.IntervalH, &src.ParkNames, &src.CreatedAt,
			&lastRun, &lastStatus, &src.LastPoints, &mine, &src.Visibility, &dataEnv); err != nil {
			continue
		}
		src.Enabled = enabled == 1
		src.Mine = mine == 1
		if lastRun.Valid {
			src.LastRunAt = lastRun.String
		}
		if lastStatus.Valid {
			src.LastStatus = lastStatus.String
		}
		if src.Enabled {
			if !lastRun.Valid {
				src.NextRunAt = "soon"
			} else if parsed, err := parseSQLTime(lastRun.String); err == nil {
				src.NextRunAt = parsed.Add(time.Duration(src.IntervalH) * time.Hour).UTC().Format("2006-01-02 15:04:05")
			}
		}
		if dataEnv != "" {
			_ = s.DB.QueryRowContext(r.Context(),
				`SELECT COUNT(*), COALESCE(SUM(total_distance_km),0) FROM gpx_uploads WHERE env = ?`, dataEnv).
				Scan(&src.Uploads, &src.TotalKm)
		}
		if src.Mine {
			src.Viewers = s.autofetchViewers(r.Context(), src.ID)
		} else {
			// A grantee sees that it is shared, not the roster or the server login.
			src.Visibility = ""
		}
		sources = append(sources, src)
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(w).Encode(map[string]interface{}{"sources": sources})
}

func parseSQLTime(v string) (time.Time, error) {
	t, err := time.Parse("2006-01-02 15:04:05", v)
	if err != nil {
		t, err = time.Parse(time.RFC3339, v)
	}
	return t, err
}

func (s *Server) autofetchViewers(ctx context.Context, id int64) []autofetchViewer {
	out := []autofetchViewer{}
	rows, err := s.DB.QueryContext(ctx,
		`SELECT principal_ref, label FROM autofetch_viewers WHERE source_id = ? ORDER BY created_at`, id)
	if err != nil {
		return out
	}
	defer rows.Close()
	for rows.Next() {
		var v autofetchViewer
		if rows.Scan(&v.Ref, &v.Label) == nil {
			out = append(out, v)
		}
	}
	return out
}

// resolveViewerPasswords turns entered passwords into (ref, label) pairs. A
// password that is not a login here is reported by position, never echoed;
// the owner's own password is dropped (it is implied). Constant-time compare
// per candidate via isValidPassword.
func resolveViewerPasswords(pwds []string, ownerRef string) (viewers []autofetchViewer, badIdx []int) {
	seen := map[string]bool{}
	for i, p := range pwds {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		if !isValidPassword(p) {
			badIdx = append(badIdx, i)
			continue
		}
		ref := principalRef(p)
		if ref == ownerRef || seen[ref] {
			continue
		}
		seen[ref] = true
		viewers = append(viewers, autofetchViewer{Ref: ref, Label: p[:min(3, len(p))] + "…"})
	}
	return
}

func normVisibility(v string) string {
	switch v {
	case "all", "selected", "owner":
		return v
	}
	return ""
}

// autofetchViewerRL throttles password guessing through the viewer field:
// 20 attempts, then one every 3 s, per caller ref.
var autofetchViewerRL = newRateLimiter(1, 3*time.Second, 20)

// HandleAPIAutofetchAdd validates credentials, discovers parks, seals the
// password, and stores the source under the caller with its visibility.
func (s *Server) HandleAPIAutofetchAdd(w http.ResponseWriter, r *http.Request) {
	ref, ok := autofetchAllowed(r)
	if !ok {
		writeJSON(w, 403, map[string]string{"error": "automated fetch is available to account logins only"})
		return
	}
	var req struct {
		APIType       string   `json:"api_type"`
		ServiceURL    string   `json:"service_url"`
		Username      string   `json:"username"`
		Password      string   `json:"password"`
		Title         string   `json:"title"`
		IntervalH     int      `json:"interval_h"`
		BackfillSince string   `json:"backfill_since"`
		Visibility    string   `json:"visibility"`
		Viewers       []string `json:"viewers"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid JSON"})
		return
	}
	req.ServiceURL = strings.TrimRight(strings.TrimSpace(req.ServiceURL), "/")
	if req.ServiceURL == "" || req.Username == "" || req.Password == "" {
		writeJSON(w, 400, map[string]string{"error": "service_url, username, and password are required"})
		return
	}
	u, err := url.Parse(req.ServiceURL)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil {
		writeJSON(w, 400, map[string]string{"error": "service_url must be an https:// URL"})
		return
	}
	if err := autofetchHostAllowed(u.Host); err != nil {
		writeJSON(w, 400, map[string]string{"error": "service_url: " + err.Error()})
		return
	}
	if req.IntervalH <= 0 {
		req.IntervalH = 24
	}
	vis := normVisibility(req.Visibility)
	if vis == "" {
		vis = "owner"
	}
	var viewers []autofetchViewer
	if vis == "selected" {
		if !autofetchViewerRL.allow(ref) {
			writeJSON(w, 429, map[string]string{"error": "too many attempts; try again in a moment"})
			return
		}
		var bad []int
		viewers, bad = resolveViewerPasswords(req.Viewers, ref)
		if len(bad) > 0 {
			writeJSON(w, 400, map[string]interface{}{"error": "not a login on this server", "bad_viewers": bad})
			return
		}
	}

	parkNames, err := probeEarthRanger(req.ServiceURL, req.Username, req.Password)
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": fmt.Sprintf("Connection failed: %v", err)})
		return
	}
	encrypted, err := encryptPassword(req.Password)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "Failed to encrypt credentials"})
		return
	}
	title := strings.TrimSpace(req.Title)
	if len(title) > 80 {
		title = title[:80]
	}

	tx, err := s.DB.BeginTx(r.Context(), nil)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(r.Context(),
		`INSERT INTO autofetch_sources (api_type, service_url, username, password, interval_h, enabled,
		        park_names, env, owner_ref, visibility, title)
		 VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)`,
		req.APIType, req.ServiceURL, req.Username, encrypted, req.IntervalH, parkNames,
		RequestEnv(r), ref, vis, title)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	id, _ := result.LastInsertId()
	if _, err := tx.ExecContext(r.Context(),
		`UPDATE autofetch_sources SET data_env = ? WHERE id = ?`, autofetchDataEnv(id), id); err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	for _, v := range viewers {
		if _, err := tx.ExecContext(r.Context(),
			`INSERT OR IGNORE INTO autofetch_viewers (source_id, principal_ref, label) VALUES (?, ?, ?)`,
			id, v.Ref, v.Label); err != nil {
			writeJSON(w, 500, map[string]string{"error": err.Error()})
			return
		}
	}
	if req.BackfillSince != "" {
		if t, err := time.Parse("2006-01-02", req.BackfillSince); err == nil {
			_, _ = tx.ExecContext(r.Context(),
				`UPDATE autofetch_sources SET last_run_at = ? WHERE id = ?`, t.Format("2006-01-02")+" 00:00:00", id)
		}
	}
	if err := tx.Commit(); err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	patrolACLVersion.Add(1)
	slog.Info("autofetch source added", "id", id, "url", req.ServiceURL, "parks", parkNames, "visibility", vis)
	writeJSON(w, 200, map[string]interface{}{"id": id, "park_names": parkNames, "visibility": vis, "viewers": viewers})
}

// HandleAPIAutofetchVisibility sets who may see a source's contribution.
// Body: {id, visibility, viewers?: [passwords]} -- when viewers is given for
// 'selected' it REPLACES the roster; add/remove use the two endpoints below.
func (s *Server) HandleAPIAutofetchVisibility(w http.ResponseWriter, r *http.Request) {
	var req struct {
		ID         int64    `json:"id"`
		Visibility string   `json:"visibility"`
		Viewers    []string `json:"viewers"`
		Title      *string  `json:"title"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid JSON"})
		return
	}
	if !s.autofetchOwned(r, req.ID) {
		writeJSON(w, 404, map[string]string{"error": "source not found"})
		return
	}
	ref := shortCallerRef(r)
	if req.Title != nil {
		t := strings.TrimSpace(*req.Title)
		if len(t) > 80 {
			t = t[:80]
		}
		_, _ = s.DB.ExecContext(r.Context(), `UPDATE autofetch_sources SET title = ? WHERE id = ?`, t, req.ID)
	}
	if req.Visibility != "" {
		vis := normVisibility(req.Visibility)
		if vis == "" {
			writeJSON(w, 400, map[string]string{"error": "visibility must be owner, selected or all"})
			return
		}
		if vis == "selected" && req.Viewers != nil {
			if !autofetchViewerRL.allow(ref) {
				writeJSON(w, 429, map[string]string{"error": "too many attempts; try again in a moment"})
				return
			}
			viewers, bad := resolveViewerPasswords(req.Viewers, ref)
			if len(bad) > 0 {
				writeJSON(w, 400, map[string]interface{}{"error": "not a login on this server", "bad_viewers": bad})
				return
			}
			_, _ = s.DB.ExecContext(r.Context(), `DELETE FROM autofetch_viewers WHERE source_id = ?`, req.ID)
			for _, v := range viewers {
				_, _ = s.DB.ExecContext(r.Context(),
					`INSERT OR IGNORE INTO autofetch_viewers (source_id, principal_ref, label) VALUES (?, ?, ?)`,
					req.ID, v.Ref, v.Label)
			}
		}
		_, _ = s.DB.ExecContext(r.Context(), `UPDATE autofetch_sources SET visibility = ? WHERE id = ?`, vis, req.ID)
	}
	patrolACLVersion.Add(1)
	var vis string
	_ = s.DB.QueryRowContext(r.Context(), `SELECT visibility FROM autofetch_sources WHERE id = ?`, req.ID).Scan(&vis)
	writeJSON(w, 200, map[string]interface{}{"ok": true, "visibility": vis, "viewers": s.autofetchViewers(r.Context(), req.ID)})
}

// HandleAPIAutofetchViewerAdd adds one login (by password) to the roster.
func (s *Server) HandleAPIAutofetchViewerAdd(w http.ResponseWriter, r *http.Request) {
	var req struct {
		ID       int64  `json:"id"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid JSON"})
		return
	}
	if !s.autofetchOwned(r, req.ID) {
		writeJSON(w, 404, map[string]string{"error": "source not found"})
		return
	}
	ref := shortCallerRef(r)
	if !autofetchViewerRL.allow(ref) {
		writeJSON(w, 429, map[string]string{"error": "too many attempts; try again in a moment"})
		return
	}
	viewers, bad := resolveViewerPasswords([]string{req.Password}, ref)
	if len(bad) > 0 {
		writeJSON(w, 400, map[string]string{"error": "not a login on this server"})
		return
	}
	if len(viewers) == 0 {
		writeJSON(w, 400, map[string]string{"error": "that is your own login — you always see your sources"})
		return
	}
	_, _ = s.DB.ExecContext(r.Context(),
		`INSERT OR IGNORE INTO autofetch_viewers (source_id, principal_ref, label) VALUES (?, ?, ?)`,
		req.ID, viewers[0].Ref, viewers[0].Label)
	_, _ = s.DB.ExecContext(r.Context(),
		`UPDATE autofetch_sources SET visibility = 'selected' WHERE id = ?`, req.ID)
	patrolACLVersion.Add(1)
	writeJSON(w, 200, map[string]interface{}{"ok": true, "viewer": viewers[0], "viewers": s.autofetchViewers(r.Context(), req.ID)})
}

// HandleAPIAutofetchViewerRemove drops one login (by ref) from the roster.
func (s *Server) HandleAPIAutofetchViewerRemove(w http.ResponseWriter, r *http.Request) {
	var req struct {
		ID  int64  `json:"id"`
		Ref string `json:"ref"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "invalid JSON"})
		return
	}
	if !s.autofetchOwned(r, req.ID) {
		writeJSON(w, 404, map[string]string{"error": "source not found"})
		return
	}
	_, _ = s.DB.ExecContext(r.Context(),
		`DELETE FROM autofetch_viewers WHERE source_id = ? AND principal_ref = ?`, req.ID, req.Ref)
	patrolACLVersion.Add(1)
	writeJSON(w, 200, map[string]interface{}{"ok": true, "viewers": s.autofetchViewers(r.Context(), req.ID)})
}

// HandleAPIAutofetchDisable clears the password (user must re-enter to reactivate).
func (s *Server) HandleAPIAutofetchDisable(w http.ResponseWriter, r *http.Request) {
	id, _ := strconv.ParseInt(r.FormValue("id"), 10, 64)
	if !s.autofetchOwned(r, id) {
		writeJSON(w, 404, map[string]string{"error": "source not found"})
		return
	}
	if _, err := s.DB.ExecContext(r.Context(),
		`UPDATE autofetch_sources SET enabled = 0, password = '' WHERE id = ?`, id); err != nil {
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
	if !s.autofetchOwned(r, req.ID) {
		writeJSON(w, 404, map[string]string{"error": "source not found"})
		return
	}
	if req.Password == "" {
		writeJSON(w, 400, map[string]string{"error": "password required"})
		return
	}
	var serviceURL, username string
	if err := s.DB.QueryRowContext(r.Context(),
		`SELECT service_url, username FROM autofetch_sources WHERE id = ?`, req.ID).Scan(&serviceURL, &username); err != nil {
		writeJSON(w, 404, map[string]string{"error": "source not found"})
		return
	}
	if _, err := probeEarthRanger(serviceURL, username, req.Password); err != nil {
		writeJSON(w, 400, map[string]string{"error": fmt.Sprintf("Authentication failed: %v", err)})
		return
	}
	encrypted, err := encryptPassword(req.Password)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "Failed to encrypt credentials"})
		return
	}
	if _, err = s.DB.ExecContext(r.Context(),
		`UPDATE autofetch_sources SET enabled = 1, password = ?, consecutive_failures = 0 WHERE id = ?`, encrypted, req.ID); err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	slog.Info("autofetch source re-enabled", "id", req.ID)
	writeJSON(w, 200, map[string]string{"ok": "enabled"})
}

// HandleAPIAutofetchDelete removes the source, its roster and credentials.
// The fetched pixels stay in their env, now reachable by nobody: deleting a
// subscription is not the same as deleting a patrol history, and a mistaken
// click must be recoverable by an operator.
func (s *Server) HandleAPIAutofetchDelete(w http.ResponseWriter, r *http.Request) {
	id, _ := strconv.ParseInt(r.FormValue("id"), 10, 64)
	if !s.autofetchOwned(r, id) {
		writeJSON(w, 404, map[string]string{"error": "source not found"})
		return
	}
	_, _ = s.DB.ExecContext(r.Context(), `DELETE FROM autofetch_viewers WHERE source_id = ?`, id)
	if _, err := s.DB.ExecContext(r.Context(), `DELETE FROM autofetch_sources WHERE id = ?`, id); err != nil {
		writeJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	patrolACLVersion.Add(1)
	slog.Info("autofetch source deleted", "id", id)
	writeJSON(w, 200, map[string]string{"ok": "deleted"})
}

// HandleAPIAutofetchRunNow triggers an immediate fetch for a source.
func (s *Server) HandleAPIAutofetchRunNow(w http.ResponseWriter, r *http.Request) {
	id, _ := strconv.ParseInt(r.FormValue("id"), 10, 64)
	// Ownership is checked HERE, not in the worker: runAutofetchSource is also
	// the scheduler's entry point and must not need a request to run.
	if !s.autofetchOwned(r, id) {
		writeJSON(w, 404, map[string]string{"error": "source not found"})
		return
	}
	if _, running := autofetchRunning.Load(id); running {
		writeJSON(w, 409, map[string]string{"error": "a fetch for this source is already running"})
		return
	}
	go s.runAutofetchSource(context.Background(), id)
	writeJSON(w, 200, map[string]string{"ok": "started"})
}

// ── Background worker ────────────────────────────────────────────────────────

// StartAutofetchWorker checks sources every 15 minutes and runs overdue ones.
func (s *Server) StartAutofetchWorker(ctx context.Context) {
	slog.Info("autofetch worker started")
	// Finish migration 065 for pre-existing rows before anything is fetched
	// under the old tenant env again.
	s.migrateAutofetchLegacy(ctx)
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

// runAutofetchDue finds enabled sources whose interval has elapsed since the
// last ATTEMPT (not the last success: a failing source retries at its
// interval, not every tick) and runs them.
func (s *Server) runAutofetchDue(ctx context.Context) {
	rows, err := s.DB.QueryContext(ctx,
		`SELECT id, interval_h, COALESCE(last_attempt_at, last_run_at), consecutive_failures FROM autofetch_sources
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
		var intervalH, failures int
		var lastRun sql.NullString
		if err := rows.Scan(&id, &intervalH, &lastRun, &failures); err != nil {
			continue
		}
		if !lastRun.Valid {
			due = append(due, id) // never run
			continue
		}
		parsed, err := parseSQLTime(lastRun.String)
		if err != nil {
			due = append(due, id) // can't parse, run it
			continue
		}
		if now.Sub(parsed) >= autofetchBackoff(time.Duration(intervalH)*time.Hour, failures) {
			due = append(due, id)
		}
	}

	for _, id := range due {
		s.runAutofetchSource(ctx, id)
	}
}

// autofetchScriptResult is the JSON summary fetch_earthranger_gpx.py prints
// as its LAST line (warnings may precede it on stderr).
type autofetchScriptResult struct {
	OK       bool   `json:"ok"`
	Points   int    `json:"points"`
	Dropped  int    `json:"dropped"`
	Subjects int    `json:"subjects"`
	Partial  bool   `json:"partial"`
	Errors   int    `json:"errors"`
	Error    string `json:"error"`
	Auth     bool   `json:"auth_failed"`
}

// parseAutofetchOutput finds the summary in the script's combined output.
// No summary is an error (AGENTS.md invariant 1: a run that said nothing did
// not succeed), never "ok with zero points".
func parseAutofetchOutput(output []byte) (autofetchScriptResult, error) {
	lines := strings.Split(strings.TrimSpace(string(output)), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		l := strings.TrimSpace(lines[i])
		if !strings.HasPrefix(l, "{") {
			continue
		}
		var res autofetchScriptResult
		if err := json.Unmarshal([]byte(l), &res); err == nil {
			return res, nil
		}
	}
	tail := string(output)
	if len(tail) > 300 {
		tail = tail[len(tail)-300:]
	}
	return autofetchScriptResult{}, fmt.Errorf("script produced no summary: %s", strings.TrimSpace(tail))
}

// runAutofetchSource decrypts credentials and runs the Python fetch script.
//
// Window and idempotence: the run fetches [last_run_at - autofetchOverlap,
// now], the script drops every point the ledger (autofetch_seen) already
// holds and reports the keys it kept; those are recorded once the GPX is
// durably queued. last_run_at advances only on a complete success; a failed
// or partial run leaves it, so the next run re-covers the same window and
// the ledger keeps that from counting twice.
func (s *Server) runAutofetchSource(ctx context.Context, id int64) {
	if !autofetchTryLock(id) {
		slog.Info("autofetch: already running, skipped", "id", id)
		return
	}
	defer autofetchUnlock(id)
	ctx, cancel := context.WithTimeout(ctx, autofetchRunTimeout)
	defer cancel()

	var apiType, serviceURL, username, encryptedPw, dataEnv, tenantEnv, title string
	var intervalH, failures int
	var lastRunAt sql.NullString
	err := s.DB.QueryRowContext(ctx,
		`SELECT api_type, service_url, username, password, interval_h, last_run_at, data_env, env, title, consecutive_failures
		 FROM autofetch_sources WHERE id = ? AND enabled = 1 AND password != ''`, id,
	).Scan(&apiType, &serviceURL, &username, &encryptedPw, &intervalH, &lastRunAt, &dataEnv, &tenantEnv, &title, &failures)
	if err != nil {
		slog.Error("autofetch: source not found or disabled", "id", id, "error", err)
		return
	}
	_, _ = s.DB.ExecContext(ctx, `UPDATE autofetch_sources SET last_attempt_at = CURRENT_TIMESTAMP WHERE id = ?`, id)

	// fail records an attempt that must not advance the high-water mark.
	// It never touches the stored credential: only the owner's explicit
	// Pause does (HandleAPIAutofetchDisable). A streak of failures -- auth
	// or otherwise -- is answered with backoff (autofetchBackoff) and a
	// notification, not by purging a password someone will have to find again.
	fail := func(status string, authFailure bool) {
		failures++
		if authFailure {
			status = "auth " + status
		}
		slog.Error("autofetch: run failed", "id", id, "status", status, "consecutive", failures)
		_, _ = s.DB.ExecContext(ctx,
			`UPDATE autofetch_sources SET last_status = ?, consecutive_failures = ? WHERE id = ?`,
			status, failures, id)
		// Tell the tenant once per streak (and again if it persists), not every tick.
		if failures == 1 || failures == autofetchBackoffAfter || failures%10 == 0 {
			label := title
			if label == "" {
				label = extractParkName(serviceURL)
			}
			_, _ = s.DB.ExecContext(ctx,
				`INSERT INTO notifications (park_id, notification_type, title, message, reference_id, env, created_at)
				 VALUES ('', 'autofetch_failed', ?, ?, ?, ?, CURRENT_TIMESTAMP)`,
				"Automated fetch failed: "+label, status, strconv.FormatInt(id, 10), tenantEnv)
		}
	}

	password, err := decryptPassword(encryptedPw)
	if err != nil {
		fail("error: credential decryption failed", false)
		return
	}
	if dataEnv == "" {
		fail("error: source not migrated (no data env)", false)
		return
	}

	// Window: from the high-water mark less the late-sync overlap. A source
	// that never ran fetches --days (the backfill_since on add is a
	// last_run_at, so it takes this path too).
	//
	// The overlap may only reach back as far as the ledger can vouch for:
	// before migration 070 nothing was recorded, so a source with an empty
	// ledger keeps the historical 30-minute overlap (exactly the old run) and
	// widens to autofetchOverlap once the ledger covers it. Otherwise the
	// first run after the deploy would re-import three days it already holds.
	var hwm time.Time
	if lastRunAt.Valid && lastRunAt.String != "" {
		if parsed, perr := parseSQLTime(lastRunAt.String); perr == nil {
			hwm = parsed
		}
	}
	if hwm.IsZero() {
		hwm = time.Now().Add(-time.Duration(max(intervalH/24, 1)) * 24 * time.Hour)
	}
	since := hwm.Add(-autofetchOverlap)
	var ledgerMin sql.NullString
	_ = s.DB.QueryRowContext(ctx, `SELECT MIN(recorded_at) FROM autofetch_seen WHERE source_id = ?`, id).Scan(&ledgerMin)
	if !ledgerMin.Valid {
		since = hwm.Add(-autofetchLegacyOverlap)
	} else if lm, err := time.Parse(time.RFC3339, ledgerMin.String); err == nil && lm.After(since) {
		since = lm
	}
	sinceArg := since.UTC().Format(time.RFC3339)

	// A ledger that cannot be read degrades to the pre-070 behaviour (a
	// possible double count inside the overlap), never to a lost fetch.
	seenPath, seenN, err := s.writeAutofetchSeen(ctx, id, since)
	if err != nil {
		slog.Warn("autofetch: ledger unreadable, running without de-duplication", "id", id, "error", err)
		seenPath = ""
	} else {
		defer os.Remove(seenPath)
	}
	outFile, err := os.CreateTemp("", "autofetch-*.gpx")
	if err != nil {
		fail(fmt.Sprintf("error: temp file: %v", err), false)
		return
	}
	outPath := outFile.Name()
	outFile.Close()
	defer os.Remove(outPath)
	newKeysPath := outPath + ".keys"
	defer os.Remove(newKeysPath)

	slog.Info("autofetch: running", "id", id, "url", serviceURL, "since", sinceArg, "seen_keys", seenN)

	// The fetched tracks are filed under the source's OWN env (data_env), so
	// visibility is an ACL the read path applies (PatrolEnvs) and not a
	// question of which password the worker happened to use. The script writes
	// the GPX to a file and the worker queues it directly -- no HTTP, no
	// password in a URL.
	args := []string{"scripts/fetch_earthranger_gpx.py",
		"--url", serviceURL,
		"--user", username,
		"--out", outPath,
		"--since", sinceArg,
		"--new-keys", newKeysPath,
	}
	if seenPath != "" {
		args = append(args, "--seen", seenPath)
	}
	cmd := exec.CommandContext(ctx, "python3", args...)
	// Pass password via environment variable — not command-line args
	cmd.Env = append(os.Environ(), "EARTHRANGER_PASSWORD="+password)
	output, runErr := cmd.CombinedOutput()

	res, perr := parseAutofetchOutput(output)
	if perr != nil {
		if ctx.Err() != nil {
			fail(fmt.Sprintf("error: timed out after %s", autofetchRunTimeout), false)
			return
		}
		fail(fmt.Sprintf("error: %v", perr), false)
		return
	}
	if !res.OK {
		msg := res.Error
		if msg == "" && runErr != nil {
			msg = runErr.Error()
		}
		fail("error: "+msg, res.Auth)
		return
	}

	status := "ok"
	if res.Points > 0 {
		if err := s.queueAutofetchFile(ctx, outPath, dataEnv); err != nil {
			fail(fmt.Sprintf("error: queue upload: %v", err), false)
			return
		}
		if n, err := s.recordAutofetchSeen(ctx, id, newKeysPath); err != nil {
			// The GPX is queued; a ledger gap means the next run may re-offer
			// these points and be told so by the file-hash check at worst.
			slog.Warn("autofetch: ledger write incomplete", "id", id, "recorded", n, "error", err)
		}
	}
	if res.Partial {
		// Some subject fetches failed: keep what arrived, but do not move the
		// high-water mark past a hole (invariant 1); the ledger makes the
		// re-fetch free. A hole that persists is not retried forever, or the
		// window would grow without bound: after autofetchPartialGiveUp
		// consecutive partial runs the mark advances and the status says so.
		failures++
		if failures < autofetchPartialGiveUp {
			status = fmt.Sprintf("partial: %d subject fetches failed; window will be retried (%d/%d)", res.Errors, failures, autofetchPartialGiveUp)
			_, _ = s.DB.ExecContext(ctx,
				`UPDATE autofetch_sources SET last_status = ?, last_points = ?, consecutive_failures = ? WHERE id = ?`,
				status, res.Points, failures, id)
			slog.Warn("autofetch: partial", "id", id, "points", res.Points, "errors", res.Errors, "streak", failures)
			return
		}
		status = fmt.Sprintf("ok with gaps: %d subject fetches failed on %d consecutive runs; moved on", res.Errors, failures)
		slog.Warn("autofetch: persistent partial, advancing", "id", id, "errors", res.Errors)
	}
	slog.Info("autofetch: completed", "id", id, "points", res.Points, "dropped_seen", res.Dropped, "subjects", res.Subjects)
	_, _ = s.DB.ExecContext(ctx,
		`UPDATE autofetch_sources SET last_run_at = CURRENT_TIMESTAMP, last_status = ?, last_points = ?, consecutive_failures = 0 WHERE id = ?`,
		status, res.Points, id)
}

// queueAutofetchFile files the script's GPX into upload_queue under env, with
// the same size bound and hash de-duplication the HTTP endpoint applies.
func (s *Server) queueAutofetchFile(ctx context.Context, path, env string) error {
	st, err := os.Stat(path)
	if err != nil {
		return err
	}
	if st.Size() > maxUploadSize {
		return fmt.Errorf("fetched GPX is %d MB, over the %d MB upload bound", st.Size()>>20, maxUploadSize>>20)
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if len(content) == 0 {
		return fmt.Errorf("script wrote an empty file")
	}
	sum := sha256.Sum256(content)
	hash := hex.EncodeToString(sum[:])
	q := dbgen.New(s.DB)
	if ex, err := q.GetUploadQueueByHash(ctx, dbgen.GetUploadQueueByHashParams{FileHash: &hash, Env: env}); err == nil && ex.ID > 0 {
		return nil
	}
	if ex, err := q.GetGPXUploadByHash(ctx, dbgen.GetGPXUploadByHashParams{FileHash: &hash, Env: env}); err == nil && ex.ID > 0 {
		return nil
	}
	_, err = q.QueueUpload(ctx, dbgen.QueueUploadParams{
		UserID:      "autofetch",
		UserEmail:   "",
		Filename:    "autofetch-" + time.Now().UTC().Format("20060102") + ".gpx",
		FileHash:    &hash,
		FileContent: content,
		Env:         env,
	})
	return err
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
	// The form carries the user's ER password. Never follow a redirect with
	// it: a host that answers 307 -> http://<internal> would receive it.
	client := &http.Client{
		Timeout: 30 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return fmt.Errorf("service_url redirected; use the final https:// address")
		},
	}

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
		if resp.StatusCode/100 == 3 {
			return "", fmt.Errorf("service_url redirected; use the final https:// address")
		}
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
