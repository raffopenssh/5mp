package srv

// Shared files: user-uploaded files behind expiring guest links.
//
// The problem this replaces: handing a colleague a file the app didn't build
// (a styled prediction GPKG, a report PDF) meant a bare `busybox httpd` on a
// spare port — unauthenticated, unexpiring, invisible to the Sharing sheet.
//
// The design is the GeoPackage export's lifecycle applied to a file we did
// not generate: 21-day TTL, an hourly sweeper that deletes bytes and row
// together, retention pushed out by any live guest link pointing at the
// download (mint, extend and sweep all re-assert it), a +30d extension, and
// the link's purpose tags shown on the file row. Sharing goes through the
// ordinary short-link machinery — a file share IS a guest link whose target
// is /api/files/{id}/download — so revocation, expiry, tags and the Sharing
// sheet all come for free and there is exactly one credential system.
//
// Scope: a file belongs to the login that uploaded it (pwd_ref, same handle
// short_links uses). Another login gets 404, not 403 (invariant 6). The
// download itself is readable by any authenticated session or guest — the id
// is a random token and being fetched via a link is the point — mirroring
// /api/geopackage/{id}/download. Uploads are refused in the shared test
// sandbox (test2026): a demo password anyone holds must not be able to fill
// the disk or serve files under this domain's name.

import (
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const (
	sharedFileDir     = "data/shared_files"
	sharedFileTTL     = 21 * 24 * time.Hour
	maxSharedFileSize = 1 << 30 // 1 GB — GPKGs and MBTiles are legitimately large
	// sharedFileBudgetDefault caps what one login may hold across uploads and
	// MBTiles builds. SHARED_FILE_BUDGET_GB overrides.
	sharedFileBudgetDefault = 20 << 30
)

// sharedFileBudgetBytes: the per-login storage budget.
func sharedFileBudgetBytes() int64 {
	v := strings.TrimSpace(os.Getenv("SHARED_FILE_BUDGET_GB"))
	if v == "" {
		v = secretsEnv("SHARED_FILE_BUDGET_GB")
	}
	if n, err := strconv.Atoi(v); err == nil && n > 0 {
		return int64(n) << 30
	}
	return sharedFileBudgetDefault
}

// sharedFileUsage: bytes this login currently holds.
func (s *Server) sharedFileUsage(ref string) int64 {
	if ref == "" {
		return 0
	}
	var n int64
	s.DB.QueryRow(`SELECT COALESCE(SUM(size_bytes),0) FROM shared_files WHERE pwd_ref = ?`, ref).Scan(&n)
	return n
}

type SharedFile struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	SizeBytes int64  `json:"size_bytes"`
	CreatedAt string `json:"created_at"`
	ExpiresAt string `json:"expires_at"`
	Downloads int    `json:"downloads"`
	URL       string `json:"download_url"`
	// LinkTags/LinkTag: purpose tags of live guest links pointing at this
	// file. Computed on read from short_link_tags — the link owns them, a
	// copy here would drift from a retag (same rule as GeoPackageJob).
	LinkTags []string `json:"link_tags,omitempty"`
	LinkTag  string   `json:"link_tag,omitempty"`
	// Kind: 'upload' | 'mbtiles'. Encrypted: at rest (every row since 064).
	// Private: the download answers only the owner or a guest key they
	// minted, never another login — set for files built from a private
	// tile source. MaxDownloads 0 = unlimited.
	Kind         string `json:"kind"`
	Encrypted    bool   `json:"encrypted"`
	Private      bool   `json:"private"`
	MaxDownloads int    `json:"max_downloads"`
	nonce        []byte
	ref          string
}

const sharedFileCols = `id, pwd_ref, name, size_bytes, created_at, expires_at, downloads,
	COALESCE(enc,0), nonce, COALESCE(max_downloads,0), COALESCE(kind,'upload'), COALESCE(private,0)`

func scanSharedFile(sc interface{ Scan(...interface{}) error }) (*SharedFile, error) {
	f := &SharedFile{}
	var enc, priv int
	if err := sc.Scan(&f.ID, &f.ref, &f.Name, &f.SizeBytes, &f.CreatedAt, &f.ExpiresAt, &f.Downloads,
		&enc, &f.nonce, &f.MaxDownloads, &f.Kind, &priv); err != nil {
		return nil, err
	}
	f.Encrypted = enc == 1
	f.Private = priv == 1
	f.URL = "/api/files/" + f.ID + "/download"
	return f, nil
}

// sharedFileName keeps the original basename recognisable but safe: path
// stripped, anything outside [A-Za-z0-9._-] replaced, length capped, and a
// name that sanitises to nothing (or to dotfiles like "..") refused rather
// than invented — the filename is the only label the recipient ever sees.
var sharedFileNameRe = regexp.MustCompile(`[^A-Za-z0-9._-]+`)

func sharedFileName(raw string) string {
	n := filepath.Base(strings.TrimSpace(raw))
	n = sharedFileNameRe.ReplaceAllString(n, "_")
	n = strings.Trim(n, "._")
	if len(n) > 128 {
		ext := filepath.Ext(n)
		n = n[:128-len(ext)] + ext
	}
	if n == "" {
		return ""
	}
	return n
}

func sharedFilePath(id, name string) string {
	return filepath.Join(sharedFileDir, id, name)
}

// sharedFileDownloadTarget extracts the file id from a short-link target, or
// "" — the /api/files twin of gpkgDownloadTarget.
func sharedFileDownloadTarget(target string) string {
	const pre = "/api/files/"
	if !strings.HasPrefix(target, pre) {
		return ""
	}
	id, tail, ok := strings.Cut(strings.TrimPrefix(target, pre), "/")
	if !ok || id == "" {
		return ""
	}
	if tail != "download" && !strings.HasPrefix(tail, "download?") {
		return ""
	}
	return id
}

// retainSharedFileForLink keeps "a file a live guest link points at outlives
// the link": expiry pushed OUT to the link's, never pulled in. Called from
// the same three places as the GeoPackage twin: mint, extend, sweep.
func (s *Server) retainSharedFileForLink(target, linkExpires string) {
	id := sharedFileDownloadTarget(target)
	if id == "" || linkExpires == "" {
		return
	}
	s.DB.Exec(`UPDATE shared_files SET expires_at = ? WHERE id = ? AND expires_at < ?`,
		linkExpires, id, linkExpires)
}

// sweepSharedFiles deletes expired files and their rows, and re-asserts
// retention for live guest links. Called from the hourly GeoPackage sweep —
// one clock, not two.
func (s *Server) sweepSharedFiles(now string) {
	if lrows, err := s.DB.Query(`SELECT url, expires_at FROM short_links
		WHERE guest = 1 AND (revoked_at IS NULL OR revoked_at = '')
		  AND expires_at > ? AND url LIKE '/api/files/%'`, now); err == nil {
		for lrows.Next() {
			var u, exp string
			if lrows.Scan(&u, &exp) == nil {
				s.retainSharedFileForLink(u, exp)
			}
		}
		lrows.Close()
	}
	rows, err := s.DB.Query(`SELECT id FROM shared_files WHERE expires_at < ?
		OR (COALESCE(max_downloads,0) > 0 AND downloads >= max_downloads)`, now)
	if err != nil {
		return
	}
	var ids []string
	for rows.Next() {
		var id string
		if rows.Scan(&id) == nil {
			ids = append(ids, id)
		}
	}
	rows.Close()
	for _, id := range ids {
		os.RemoveAll(filepath.Join(sharedFileDir, id))
		s.DB.Exec(`DELETE FROM shared_files WHERE id = ?`, id)
		// A key whose target is gone must read "switched off", not 410 —
		// same rule as an explicit delete. (Expiry is already the key's;
		// this matters for the download-cap case.)
		u := "/api/files/" + id + "/download"
		s.DB.Exec(`UPDATE short_links SET revoked_at = ? WHERE guest = 1
			AND (revoked_at IS NULL OR revoked_at = '') AND (url = ? OR url LIKE ?)`, now, u, u+"?%")
	}
	if len(ids) > 0 {
		slog.Info("shared-file sweeper removed expired files", "count", len(ids))
	}
	// Orphan directories (row deleted by hand): same rule as gpkg — the id is
	// the directory name, so "is it still a row" is the whole check.
	entries, _ := os.ReadDir(sharedFileDir)
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		var n int
		s.DB.QueryRow(`SELECT COUNT(*) FROM shared_files WHERE id = ?`, e.Name()).Scan(&n)
		if n == 0 {
			dir := filepath.Join(sharedFileDir, e.Name())
			if st, err := os.Stat(dir); err == nil && time.Since(st.ModTime()) > time.Hour {
				os.RemoveAll(dir)
			}
		}
	}
}

func (s *Server) sharedFileSetLinkTags(f *SharedFile) {
	f.LinkTags = s.linkTagsForTarget("/api/files/" + f.ID + "/download")
	f.LinkTag = ""
	if len(f.LinkTags) > 0 {
		f.LinkTag = f.LinkTags[0]
	}
}

// linkTagsForTarget: purpose tags of live guest links pointing at a target —
// the generalisation gpkgLinkTags is a special case of.
func (s *Server) linkTagsForTarget(target string) []string {
	rows, err := s.DB.Query(`SELECT DISTINCT t.tag FROM short_link_tags t
		JOIN short_links l ON l.slug = t.slug
		WHERE l.guest = 1 AND (l.revoked_at IS NULL OR l.revoked_at = '')
		  AND COALESCE(l.expires_at,'') > ?
		  AND (l.url = ? OR l.url LIKE ?)
		ORDER BY t.tag`,
		time.Now().UTC().Format(time.RFC3339), target, target+"?%")
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var t string
		if rows.Scan(&t) == nil {
			out = append(out, t)
		}
	}
	return out
}

// ---- handlers ------------------------------------------------------------

// HandleAPISharedFileUpload — POST /api/files (multipart, field "file").
//
// Refused for guests (a capability must not create durable server state) and
// for the shared test sandbox (a password printed in the README must not be
// able to fill the disk). Both are also enforced structurally — the guest
// middleware blocks POSTs, and the sandbox has no pwd_ref worth owning — but
// this is the door that matters, so it gets its own lock.
func (s *Server) HandleAPISharedFileUpload(w http.ResponseWriter, r *http.Request) {
	if GuestFromRequest(r) != nil {
		http.Error(w, "read-only link", http.StatusForbidden)
		return
	}
	ref := shortCallerRef(r)
	if ref == "" {
		http.Error(w, "sign in to upload files", http.StatusForbidden)
		return
	}
	if RequestEnv(r) == sandboxTenant {
		writeJSON(w, http.StatusForbidden, map[string]string{
			"error": "file sharing is not available in the test environment"})
		return
	}
	if used, budget := s.sharedFileUsage(ref), sharedFileBudgetBytes(); used >= budget {
		writeJSON(w, http.StatusInsufficientStorage, map[string]interface{}{
			"error": fmt.Sprintf("storage budget of %s is used up (%s held) — delete a file first", gb(budget), gb(used)),
			"budget_used_bytes": used, "budget_max_bytes": budget})
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxSharedFileSize)
	// A large body must outlive the server's absolute ReadTimeout.
	longUpload(w, r)
	if err := r.ParseMultipartForm(32 << 20); err != nil {
		http.Error(w, "file too large (limit 1 GB) or malformed upload", http.StatusRequestEntityTooLarge)
		return
	}
	src, hdr, err := r.FormFile("file")
	if err != nil {
		http.Error(w, "no file in upload (field name: file)", http.StatusBadRequest)
		return
	}
	defer src.Close()
	name := sharedFileName(hdr.Filename)
	if name == "" {
		http.Error(w, "that filename cannot be used", http.StatusBadRequest)
		return
	}
	id := gpkgToken()
	dir := filepath.Join(sharedFileDir, id)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		http.Error(w, "could not store the file", http.StatusInternalServerError)
		return
	}
	dst, err := os.Create(sharedFilePath(id, name))
	if err != nil {
		os.RemoveAll(dir)
		http.Error(w, "could not store the file", http.StatusInternalServerError)
		return
	}
	nonce, nerr := newFileNonce()
	var ew io.Writer
	if nerr == nil {
		ew, nerr = encryptingWriter(dst, nonce)
	}
	if nerr != nil {
		dst.Close()
		os.RemoveAll(dir)
		http.Error(w, "encryption unavailable: "+nerr.Error(), http.StatusInternalServerError)
		return
	}
	n, err := io.Copy(ew, src)
	dst.Close()
	if err == nil && s.sharedFileUsage(ref)+n > sharedFileBudgetBytes() {
		os.RemoveAll(dir)
		writeJSON(w, http.StatusInsufficientStorage, map[string]interface{}{
			"error": fmt.Sprintf("this file would exceed your %s storage budget", gb(sharedFileBudgetBytes()))})
		return
	}
	if err != nil {
		// A partial file must not wear the advertised name (invariant 1's
		// cousin): delete everything, report unfinished.
		os.RemoveAll(dir)
		http.Error(w, "upload interrupted — try again", http.StatusInternalServerError)
		return
	}
	now := time.Now().UTC()
	f := &SharedFile{
		ID: id, Name: name, SizeBytes: n,
		CreatedAt: now.Format(time.RFC3339),
		ExpiresAt: now.Add(sharedFileTTL).Format(time.RFC3339),
		URL:       "/api/files/" + id + "/download",
		Kind:      "upload", Encrypted: true,
	}
	if _, err := s.DB.Exec(`INSERT INTO shared_files
		(id, pwd_ref, env, name, size_bytes, created_at, expires_at, enc, nonce, kind)
		VALUES (?,?,?,?,?,?,?,1,?,'upload')`,
		id, ref, RequestEnv(r), name, n, f.CreatedAt, f.ExpiresAt, nonce); err != nil {
		os.RemoveAll(dir)
		http.Error(w, "database error", http.StatusInternalServerError)
		return
	}
	slog.Info("shared file uploaded", "id", id, "name", name, "bytes", n)
	writeJSON(w, http.StatusOK, f)
}

// HandleAPISharedFileList — GET /api/files: the caller's own files only.
// A guest (no pwd → empty ref) gets an empty list, not an error.
func (s *Server) HandleAPISharedFileList(w http.ResponseWriter, r *http.Request) {
	ref := shortCallerRef(r)
	out := []*SharedFile{}
	if ref != "" {
		rows, err := s.DB.Query(`SELECT `+sharedFileCols+`
			FROM shared_files WHERE pwd_ref = ? ORDER BY created_at DESC LIMIT 200`, ref)
		if err != nil {
			http.Error(w, "database error", http.StatusInternalServerError)
			return
		}
		defer rows.Close()
		for rows.Next() {
			if f, err := scanSharedFile(rows); err == nil {
				s.sharedFileSetLinkTags(f)
				out = append(out, f)
			}
		}
	}
	var used int64
	for _, f := range out {
		used += f.SizeBytes
	}
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusOK, map[string]interface{}{"files": out, "count": len(out),
		"can_upload":        ref != "" && RequestEnv(r) != sandboxTenant && GuestFromRequest(r) == nil,
		"budget_used_bytes": used, "budget_max_bytes": sharedFileBudgetBytes(),
		"disk_free_bytes":   getAvailableDiskSpace(sharedFileDir)})
}

// loadSharedFile resolves an id. ownerOnly enforces pwd_ref scope with 404
// (an id must not be an oracle); the download path passes false, because a
// random token fetched through a link is the feature.
func (s *Server) loadSharedFile(w http.ResponseWriter, r *http.Request, ownerOnly bool) (*SharedFile, bool) {
	id := r.PathValue("id")
	f, err := scanSharedFile(s.DB.QueryRow(`SELECT `+sharedFileCols+` FROM shared_files WHERE id = ?`, id))
	if err != nil || (ownerOnly && f.ref != shortCallerRef(r)) {
		http.NotFound(w, r)
		return nil, false
	}
	return f, true
}

// HandleAPISharedFileDownload — GET /api/files/{id}/download.
func (s *Server) HandleAPISharedFileDownload(w http.ResponseWriter, r *http.Request) {
	f, ok := s.loadSharedFile(w, r, false)
	if !ok {
		return
	}
	// A private file answers its owner, or a guest key the owner minted
	// whose target is this very file. Any other login: 404, not 403.
	if f.Private && f.ref != shortCallerRef(r) {
		g := GuestFromRequest(r)
		if g == nil || !s.guestTargets(g.Slug, f.URL) {
			http.NotFound(w, r)
			return
		}
	}
	if f.MaxDownloads > 0 && f.Downloads >= f.MaxDownloads {
		http.Error(w, "this file has reached its download limit", http.StatusGone)
		return
	}
	fh, err := os.Open(sharedFilePath(f.ID, f.Name))
	if err != nil {
		http.Error(w, "file is no longer available", http.StatusGone)
		return
	}
	defer fh.Close()
	var body io.ReadSeeker = fh
	if f.Encrypted {
		dec, derr := newDecryptingReadSeeker(fh, f.nonce)
		if derr != nil {
			http.Error(w, "file cannot be decrypted on this server", http.StatusInternalServerError)
			return
		}
		body = dec
	}
	if isDownloadStart(r) {
		s.DB.Exec(`UPDATE shared_files SET downloads = downloads + 1, last_download_at = ? WHERE id = ?`,
			time.Now().UTC().Format(time.RFC3339), f.ID)
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", `attachment; filename="`+f.Name+`"`)
	// ServeContent owns Content-Length/Range; a stable modtime is what makes
	// If-Range resumes work (time.Now() would invalidate every partial file).
	mod := time.Now()
	if st, err := fh.Stat(); err == nil {
		mod = st.ModTime()
	}
	http.ServeContent(longDownload(w), r, f.Name, mod, body)
}

// guestTargets: does this live guest key point at exactly this target?
func (s *Server) guestTargets(slug, target string) bool {
	var n int
	s.DB.QueryRow(`SELECT COUNT(*) FROM short_links WHERE slug = ? AND guest = 1
		AND (revoked_at IS NULL OR revoked_at = '') AND (url = ? OR url LIKE ?)`, slug, target, target+"?%").Scan(&n)
	return n > 0
}

// HandleAPISharedFileExtend — POST /api/files/{id}/extend {days:30}.
// Owner only; same arithmetic as the GeoPackage extend (later of now/current).
func (s *Server) HandleAPISharedFileExtend(w http.ResponseWriter, r *http.Request) {
	if GuestFromRequest(r) != nil {
		http.Error(w, "read-only link", http.StatusForbidden)
		return
	}
	f, ok := s.loadSharedFile(w, r, true)
	if !ok {
		return
	}
	var body struct {
		Days int `json:"days"`
	}
	decodeJSONBody(r, &body)
	if body.Days <= 0 {
		body.Days = 30
	}
	now := time.Now().UTC()
	base := now
	if t, err := time.Parse(time.RFC3339, f.ExpiresAt); err == nil && t.After(base) {
		base = t
	}
	exp := base.Add(time.Duration(body.Days) * 24 * time.Hour).Format(time.RFC3339)
	if _, err := s.DB.Exec(`UPDATE shared_files SET expires_at = ? WHERE id = ?`, exp, f.ID); err != nil {
		http.Error(w, "database error", http.StatusInternalServerError)
		return
	}
	f.ExpiresAt = exp
	s.sharedFileSetLinkTags(f)
	writeJSON(w, http.StatusOK, f)
}

// HandleAPISharedFileDelete — DELETE /api/files/{id}. Owner only. Bytes and
// row go together — a row without bytes is a link that 410s while listed as
// alive. Live guest links pointing at it are switched off in the same stroke:
// a key whose target is gone must read "switched off", not resolve to an
// error page the sender never chose.
func (s *Server) HandleAPISharedFileDelete(w http.ResponseWriter, r *http.Request) {
	if GuestFromRequest(r) != nil {
		http.Error(w, "read-only link", http.StatusForbidden)
		return
	}
	f, ok := s.loadSharedFile(w, r, true)
	if !ok {
		return
	}
	os.RemoveAll(filepath.Join(sharedFileDir, f.ID))
	s.DB.Exec(`DELETE FROM shared_files WHERE id = ?`, f.ID)
	s.DB.Exec(`UPDATE short_links SET revoked_at = ? WHERE guest = 1
		AND (revoked_at IS NULL OR revoked_at = '')
		AND (url = ? OR url LIKE ?)`,
		time.Now().UTC().Format(time.RFC3339), f.URL, f.URL+"?%")
	writeJSON(w, http.StatusOK, map[string]interface{}{"deleted": true, "id": f.ID})
}
