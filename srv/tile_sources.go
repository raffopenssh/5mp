package srv

// Private tile sources: XYZ imagery a login adds for its own use.
//
// WHAT THIS IS AND IS NOT. The offline-tile builder ships only imagery this
// server may redistribute (TileSources in mbtiles.go, one entry, licence in
// the file). That is about what WE publish. What a user does with a tile
// service they have their own agreement with is their business — QGIS lets
// anyone paste an XYZ template, view it and cache it for private use, and so
// does this table. Nothing here names a provider: a source is a URL supplied
// at runtime, stored encrypted (it may carry the user's API key), reachable
// only by the login that added it, and any MBTiles built from it is encrypted
// on disk and visible only to that login (shared_files.private).
//
// The browser never sees the URL: tiles are fetched through
// /api/tile-sources/{id}/tile/{z}/{x}/{y}, owner-only, no disk cache (most
// imagery terms forbid caching beyond the session; the builder is the
// user's explicit, private exception). Another login gets 404 (invariant 6).
// Guests and the shared demo sandbox may not add or read sources: a source
// is a private agreement and test2026 is a password printed in the README.
//
// Validation is a probe, not a regex: the template must be https, carry
// {z}/{x}/{y} (or {q} for a quadkey scheme), resolve to a public address, and
// answer one real tile request with an image. A source that fails the probe
// is refused with the reason, not stored as "unverified".

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

type TileSourceRow struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	URL         string `json:"url,omitempty"` // owner only; never in a list another login can reach
	Host        string `json:"host"`
	Scheme      string `json:"scheme"` // xyz | tms | quadkey
	MinZoom     int    `json:"min_zoom"`
	MaxZoom     int    `json:"max_zoom"`
	TileSize    int    `json:"tile_size"`
	Attribution string `json:"attribution"`
	Kind        string `json:"kind"` // satellite | base | overlay
	VerifiedAt  string `json:"verified_at,omitempty"`
	ProbeBytes  int64  `json:"probe_bytes"`
	CreatedAt   string `json:"created_at"`
	// Tiles is the proxied template the map style uses. Derived.
	Tiles string `json:"tiles"`
	// Basemap is the id the frontend uses in switchBasemap/?basemap=.
	Basemap string `json:"basemap"`
}

const (
	tileSourcePrefix  = "src:" // basemap id / builder source-key prefix
	tileSourceMaxPer  = 12
	tileProbeTimeout  = 12 * time.Second
	tileProxyTimeout  = 20 * time.Second
)

var tileSourceUA = "5MP Conservation Monitoring (private tile source; https://github.com/raffopenssh/5mp)"

// tileSourcesAllowed: may this request add/read private sources at all?
func tileSourcesAllowed(r *http.Request) (string, bool) {
	if GuestFromRequest(r) != nil {
		return "", false
	}
	ref := shortCallerRef(r)
	if ref == "" || RequestEnv(r) == sandboxTenant {
		return "", false
	}
	return ref, true
}

// ---- validation -----------------------------------------------------------

var (
	tilePlaceholderRe = regexp.MustCompile(`\{(z|x|y|q|quadkey|s|-y)\}`)
	// {a-c}, {0-3}: a subdomain range, Leaflet/QGIS style. Kept verbatim in
	// the template and expanded per tile in tileURL.
	tileRangeRe = regexp.MustCompile(`\{([a-z0-9])-([a-z0-9])\}`)
)

// validateTileTemplate normalises and checks a template. Returns the
// template, the hostname, the scheme it implies, or a reason.
func validateTileTemplate(raw string) (tpl, host, scheme string, err error) {
	tpl = strings.TrimSpace(raw)
	if tpl == "" {
		return "", "", "", errors.New("a tile URL is required")
	}
	tpl = strings.ReplaceAll(tpl, "{quadkey}", "{q}")
	// Validate on a substituted copy: url.Parse tolerates braces but a real
	// tile URL is what has to be well-formed.
	probe := tileURL(tpl, 1, 1, 1)
	u, perr := url.Parse(probe)
	if perr != nil || u.Host == "" {
		return "", "", "", errors.New("that is not a valid URL")
	}
	if u.Scheme != "https" {
		return "", "", "", errors.New("only https:// tile URLs are accepted")
	}
	host = strings.ToLower(u.Hostname())
	// Display host: the template's own authority, shard placeholder and all
	// ("mt{s}.example" rather than whichever shard the probe happened to hit).
	if rest := strings.TrimPrefix(tpl, "https://"); rest != tpl {
		if i := strings.IndexAny(rest, "/?"); i > 0 {
			rest = rest[:i]
		}
		if h, _, ok := strings.Cut(rest, ":"); ok {
			rest = h
		}
		if rest != "" && !strings.Contains(rest, "@") {
			host = strings.ToLower(rest)
		}
	}
	if u.Hostname() == "localhost" || strings.HasSuffix(host, ".local") || strings.HasSuffix(host, ".internal") {
		return "", "", "", errors.New("the tile host must be a public server")
	}
	if ip := net.ParseIP(u.Hostname()); ip != nil && !ipIsPublic(ip) {
		return "", "", "", errors.New("the tile host must be a public address")
	}
	hasQ := strings.Contains(tpl, "{q}")
	hasXYZ := strings.Contains(tpl, "{z}") && strings.Contains(tpl, "{x}") &&
		(strings.Contains(tpl, "{y}") || strings.Contains(tpl, "{-y}"))
	switch {
	case hasQ && !hasXYZ:
		scheme = "quadkey"
	case hasXYZ && strings.Contains(tpl, "{-y}"):
		scheme = "tms"
	case hasXYZ:
		scheme = "xyz"
	default:
		return "", "", "", errors.New("the URL must contain {z}, {x} and {y} (or {q} for a quadkey service)")
	}
	return tpl, host, scheme, nil
}

func ipIsPublic(ip net.IP) bool {
	return !(ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() ||
		ip.IsLinkLocalMulticast() || ip.IsUnspecified() || ip.IsMulticast())
}

// tileURL substitutes one tile into a template of any supported scheme.
func tileURL(tpl string, z, x, y int) string {
	s := tpl
	if strings.Contains(s, "{q}") {
		s = strings.ReplaceAll(s, "{q}", tileToQuadKey(x, y, z))
	}
	s = strings.ReplaceAll(s, "{z}", strconv.Itoa(z))
	s = strings.ReplaceAll(s, "{x}", strconv.Itoa(x))
	s = strings.ReplaceAll(s, "{y}", strconv.Itoa(y))
	s = strings.ReplaceAll(s, "{-y}", strconv.Itoa((1<<uint(z))-1-y))
	// Bare {s}: numeric shard 0-3 (the convention of the templates this
	// replaced). A {a-c} / {0-3} range: one symbol out of it, by tile.
	s = strings.ReplaceAll(s, "{s}", strconv.Itoa((x+y)%4))
	s = tileRangeRe.ReplaceAllStringFunc(s, func(m string) string {
		lo, hi := m[1], m[3]
		if hi < lo {
			lo, hi = hi, lo
		}
		return string(lo + byte((x+y)%int(hi-lo+1)))
	})
	return s
}

// tileDialer refuses connections that resolve to non-public addresses, so a
// template naming a public hostname cannot be used to reach this VM's own
// services (SSRF). Applied to both the probe and the proxy.
var tileHTTPClient = &http.Client{
	Timeout: tileProxyTimeout,
	Transport: &http.Transport{
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			host, port, err := net.SplitHostPort(addr)
			if err != nil {
				return nil, err
			}
			ips, err := net.DefaultResolver.LookupIPAddr(ctx, host)
			if err != nil {
				return nil, err
			}
			for _, ip := range ips {
				if !ipIsPublic(ip.IP) {
					return nil, fmt.Errorf("tile host resolves to a non-public address")
				}
			}
			var d net.Dialer
			return d.DialContext(ctx, network, net.JoinHostPort(ips[0].IP.String(), port))
		},
		MaxIdleConnsPerHost: 8,
		IdleConnTimeout:     60 * time.Second,
	},
	CheckRedirect: func(req *http.Request, via []*http.Request) error {
		if len(via) >= 3 {
			return errors.New("too many redirects")
		}
		if req.URL.Scheme != "https" {
			return errors.New("redirected off https")
		}
		return nil
	},
}

// fetchSourceTile GETs one tile; returns bytes and content-type or an error
// that names the HTTP status.
func fetchSourceTile(tpl string, z, x, y int, timeout time.Duration) ([]byte, string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "GET", tileURL(tpl, z, x, y), nil)
	if err != nil {
		return nil, "", err
	}
	req.Header.Set("User-Agent", tileSourceUA)
	req.Header.Set("Accept", "image/*")
	resp, err := tileHTTPClient.Do(req)
	if err != nil {
		return nil, "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, "", fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	b, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return nil, "", err
	}
	ct := resp.Header.Get("Content-Type")
	if !looksLikeImage(b, ct) {
		return nil, ct, fmt.Errorf("the server answered with %s, not an image", firstWord(ct, "something"))
	}
	return b, sniffImageType(b, ct), nil
}

func firstWord(s, dflt string) string {
	s = strings.TrimSpace(strings.Split(s, ";")[0])
	if s == "" {
		return dflt
	}
	return s
}

func looksLikeImage(b []byte, ct string) bool {
	return sniffImageType(b, ct) != ""
}

// sniffImageType trusts bytes over headers: a provider's error page arrives
// as text/html with a 200 more often than one would like.
func sniffImageType(b []byte, ct string) string {
	switch {
	case len(b) > 3 && b[0] == 0xFF && b[1] == 0xD8:
		return "image/jpeg"
	case len(b) > 8 && string(b[1:4]) == "PNG":
		return "image/png"
	case len(b) > 12 && string(b[8:12]) == "WEBP":
		return "image/webp"
	case len(b) > 3 && string(b[:3]) == "GIF":
		return "image/gif"
	}
	if strings.HasPrefix(ct, "image/") && len(b) > 0 {
		return firstWord(ct, "")
	}
	return ""
}

// probeTileSource asks for a tile over land at a mid zoom (z=6 over central
// Africa, where every global service has coverage) and returns its size and
// type. Sources with a min zoom above 6 are probed at their min zoom instead.
func probeTileSource(tpl string, minZoom int) (int64, string, error) {
	z := 6
	if minZoom > z {
		z = minZoom
	}
	// lon 20, lat 5 → tile at zoom z
	x, y := lonLatToTile(20, 5, z)
	b, ct, err := fetchSourceTile(tpl, z, x, y, tileProbeTimeout)
	if err != nil {
		return 0, "", err
	}
	return int64(len(b)), ct, nil
}

// ---- persistence ------------------------------------------------------------

const tileSourceCols = `id, name, url_enc, host, scheme, min_zoom, max_zoom, tile_size, attribution, kind,
	COALESCE(verified_at,''), probe_bytes, created_at`

func scanTileSource(sc interface{ Scan(...interface{}) error }, withURL bool) (*TileSourceRow, error) {
	t := &TileSourceRow{}
	var enc []byte
	if err := sc.Scan(&t.ID, &t.Name, &enc, &t.Host, &t.Scheme, &t.MinZoom, &t.MaxZoom, &t.TileSize,
		&t.Attribution, &t.Kind, &t.VerifiedAt, &t.ProbeBytes, &t.CreatedAt); err != nil {
		return nil, err
	}
	if withURL {
		u, err := openSecret(enc)
		if err != nil {
			return nil, err
		}
		t.URL = u
	}
	t.Tiles = "/api/tile-sources/" + t.ID + "/tile/{z}/{x}/{y}"
	t.Basemap = tileSourcePrefix + t.ID
	return t, nil
}

// loadTileSource returns the row if it exists AND belongs to ref.
func (s *Server) loadTileSource(id, ref string, withURL bool) (*TileSourceRow, error) {
	row := s.DB.QueryRow(`SELECT `+tileSourceCols+` FROM tile_sources WHERE id = ? AND pwd_ref = ?`, id, ref)
	return scanTileSource(row, withURL)
}

// tileSourceForBuilder resolves a "src:<id>" key into a TileSource the
// MBTiles builder can use, owner-checked.
func (s *Server) tileSourceForBuilder(key, ref string) (TileSource, *TileSourceRow, bool) {
	id := strings.TrimPrefix(key, tileSourcePrefix)
	t, err := s.loadTileSource(id, ref, true)
	if err != nil {
		return TileSource{}, nil, false
	}
	return TileSource{
		Name:        t.Name,
		URLFormat:   t.URL,
		Scheme:      t.Scheme,
		MaxZoom:     t.MaxZoom,
		UpscaleTo:   t.MaxZoom,
		Headers:     map[string]string{"User-Agent": tileSourceUA},
		Attribution: t.Attribution,
		Licence:     "Private source added by the user; terms are those of the provider",
		Private:     true,
	}, t, true
}

// ---- handlers ---------------------------------------------------------------

// HandleAPITileSourcesList — GET /api/tile-sources. Owner's rows with URLs
// (the owner typed them). Empty list, not an error, for sessions that may
// not hold sources; `can_add` tells the UI whether to offer the form.
func (s *Server) HandleAPITileSourcesList(w http.ResponseWriter, r *http.Request) {
	out := []*TileSourceRow{}
	ref, ok := tileSourcesAllowed(r)
	if ok {
		rows, err := s.DB.Query(`SELECT `+tileSourceCols+` FROM tile_sources WHERE pwd_ref = ? ORDER BY created_at`, ref)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				if t, err := scanTileSource(rows, true); err == nil {
					out = append(out, t)
				}
			}
		}
	}
	prefs := s.mapPrefs(ref)
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"sources": out, "count": len(out), "can_add": ok, "max": tileSourceMaxPer,
		"prefs": prefs,
	})
}

type tileSourceBody struct {
	Name        string `json:"name"`
	URL         string `json:"url"`
	MinZoom     int    `json:"min_zoom"`
	MaxZoom     int    `json:"max_zoom"`
	TileSize    int    `json:"tile_size"`
	Attribution string `json:"attribution"`
	Kind        string `json:"kind"`
	// Probe only — validate and return what a save would store, store nothing.
	DryRun bool `json:"dry_run"`
}

// HandleAPITileSourcesCreate — POST /api/tile-sources.
func (s *Server) HandleAPITileSourcesCreate(w http.ResponseWriter, r *http.Request) {
	ref, ok := tileSourcesAllowed(r)
	if !ok {
		writeJSON(w, http.StatusForbidden, map[string]string{
			"error": "private tile sources are not available for this session"})
		return
	}
	var b tileSourceBody
	if err := decodeJSONBody(r, &b); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "bad request body"})
		return
	}
	tpl, host, scheme, err := validateTileTemplate(b.URL)
	if err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error(), "field": "url"})
		return
	}
	if b.MinZoom < 0 {
		b.MinZoom = 0
	}
	if b.MaxZoom <= 0 || b.MaxZoom > 22 {
		b.MaxZoom = 19
	}
	if b.MaxZoom < b.MinZoom {
		b.MaxZoom = b.MinZoom
	}
	if b.TileSize != 512 {
		b.TileSize = 256
	}
	if b.Kind != "base" && b.Kind != "overlay" {
		b.Kind = "satellite"
	}
	name := strings.TrimSpace(b.Name)
	if name == "" {
		name = host
	}
	if len(name) > 60 {
		name = name[:60]
	}
	attribution := strings.TrimSpace(b.Attribution)
	if len(attribution) > 300 {
		attribution = attribution[:300]
	}
	// The probe is the validation.
	n, ct, err := probeTileSource(tpl, b.MinZoom)
	if err != nil {
		writeJSON(w, http.StatusUnprocessableEntity, map[string]string{
			"error": "no tile came back: " + err.Error(), "field": "url", "host": host})
		return
	}
	now := time.Now().UTC().Format(time.RFC3339)
	t := &TileSourceRow{Name: name, Host: host, Scheme: scheme, MinZoom: b.MinZoom, MaxZoom: b.MaxZoom,
		TileSize: b.TileSize, Attribution: attribution, Kind: b.Kind, VerifiedAt: now, ProbeBytes: n, CreatedAt: now}
	if b.DryRun {
		t.URL = tpl
		writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true, "probe": map[string]interface{}{
			"bytes": n, "content_type": ct}, "source": t})
		return
	}
	var have int
	s.DB.QueryRow(`SELECT COUNT(*) FROM tile_sources WHERE pwd_ref = ?`, ref).Scan(&have)
	if have >= tileSourceMaxPer {
		writeJSON(w, http.StatusConflict, map[string]string{
			"error": fmt.Sprintf("this login already holds %d sources (the maximum); remove one first", have)})
		return
	}
	enc, err := sealSecret(tpl)
	if err != nil {
		http.Error(w, "encryption unavailable: "+err.Error(), http.StatusInternalServerError)
		return
	}
	t.ID = gpkgToken()
	if _, err := s.DB.Exec(`INSERT INTO tile_sources (id, pwd_ref, name, url_enc, host, scheme, min_zoom, max_zoom,
		tile_size, attribution, kind, verified_at, probe_bytes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
		t.ID, ref, t.Name, enc, t.Host, t.Scheme, t.MinZoom, t.MaxZoom, t.TileSize, t.Attribution, t.Kind,
		now, n, now); err != nil {
		http.Error(w, "database error", http.StatusInternalServerError)
		return
	}
	t.URL = tpl
	t.Tiles = "/api/tile-sources/" + t.ID + "/tile/{z}/{x}/{y}"
	t.Basemap = tileSourcePrefix + t.ID
	slog.Info("tile source added", "id", t.ID, "host", host, "scheme", scheme, "probe_bytes", n)
	writeJSON(w, http.StatusOK, map[string]interface{}{"source": t, "probe": map[string]interface{}{"bytes": n, "content_type": ct}})
}

// HandleAPITileSourcesUpdate — PATCH /api/tile-sources/{id}: name, zooms,
// attribution, kind. The URL is not editable — add a new source (the probe is
// what makes a row trustworthy, and a row's MBTiles carry its attribution).
func (s *Server) HandleAPITileSourcesUpdate(w http.ResponseWriter, r *http.Request) {
	ref, ok := tileSourcesAllowed(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	t, err := s.loadTileSource(r.PathValue("id"), ref, true)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	var b tileSourceBody
	decodeJSONBody(r, &b)
	if n := strings.TrimSpace(b.Name); n != "" {
		if len(n) > 60 {
			n = n[:60]
		}
		t.Name = n
	}
	if b.MaxZoom > 0 && b.MaxZoom <= 22 {
		t.MaxZoom = b.MaxZoom
	}
	if b.MinZoom >= 0 && b.MinZoom <= t.MaxZoom {
		t.MinZoom = b.MinZoom
	}
	if b.Kind == "base" || b.Kind == "overlay" || b.Kind == "satellite" {
		t.Kind = b.Kind
	}
	if b.Attribution != "" || r.URL.Query().Get("clear_attribution") == "1" {
		a := strings.TrimSpace(b.Attribution)
		if len(a) > 300 {
			a = a[:300]
		}
		t.Attribution = a
	}
	s.DB.Exec(`UPDATE tile_sources SET name=?, min_zoom=?, max_zoom=?, attribution=?, kind=? WHERE id=? AND pwd_ref=?`,
		t.Name, t.MinZoom, t.MaxZoom, t.Attribution, t.Kind, t.ID, ref)
	writeJSON(w, http.StatusOK, map[string]interface{}{"source": t})
}

// HandleAPITileSourcesVerify — POST /api/tile-sources/{id}/verify: re-probe.
func (s *Server) HandleAPITileSourcesVerify(w http.ResponseWriter, r *http.Request) {
	ref, ok := tileSourcesAllowed(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	t, err := s.loadTileSource(r.PathValue("id"), ref, true)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	n, ct, perr := probeTileSource(t.URL, t.MinZoom)
	if perr != nil {
		writeJSON(w, http.StatusOK, map[string]interface{}{"ok": false, "error": perr.Error(), "source": t})
		return
	}
	now := time.Now().UTC().Format(time.RFC3339)
	s.DB.Exec(`UPDATE tile_sources SET verified_at=?, probe_bytes=? WHERE id=?`, now, n, t.ID)
	t.VerifiedAt, t.ProbeBytes = now, n
	writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true, "probe": map[string]interface{}{"bytes": n, "content_type": ct}, "source": t})
}

// HandleAPITileSourcesDelete — DELETE /api/tile-sources/{id}. A preference
// pointing at the row is cleared in the same stroke.
func (s *Server) HandleAPITileSourcesDelete(w http.ResponseWriter, r *http.Request) {
	ref, ok := tileSourcesAllowed(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	t, err := s.loadTileSource(r.PathValue("id"), ref, false)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	s.DB.Exec(`DELETE FROM tile_sources WHERE id = ? AND pwd_ref = ?`, t.ID, ref)
	bm := tileSourcePrefix + t.ID
	s.DB.Exec(`UPDATE map_prefs SET default_base = NULL WHERE pwd_ref = ? AND default_base = ?`, ref, bm)
	s.DB.Exec(`UPDATE map_prefs SET default_satellite = NULL WHERE pwd_ref = ? AND default_satellite = ?`, ref, bm)
	writeJSON(w, http.StatusOK, map[string]interface{}{"deleted": true, "id": t.ID})
}

// HandleAPITileSourceTile — GET /api/tile-sources/{id}/tile/{z}/{x}/{y}.
// Owner only. Pass-through with no disk cache; the browser may keep it for
// the session (private, 1 h).
func (s *Server) HandleAPITileSourceTile(w http.ResponseWriter, r *http.Request) {
	ref, ok := tileSourcesAllowed(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	t, err := s.loadTileSource(r.PathValue("id"), ref, true)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	z, e1 := strconv.Atoi(r.PathValue("z"))
	x, e2 := strconv.Atoi(r.PathValue("x"))
	yStr := strings.TrimSuffix(strings.TrimSuffix(r.PathValue("y"), ".png"), ".jpg")
	y, e3 := strconv.Atoi(yStr)
	if e1 != nil || e2 != nil || e3 != nil || z < 0 || z > 22 {
		http.Error(w, "bad tile coordinate", http.StatusBadRequest)
		return
	}
	if max := 1 << uint(z); x < 0 || y < 0 || x >= max || y >= max {
		http.Error(w, "tile out of range", http.StatusBadRequest)
		return
	}
	if z > t.MaxZoom || z < t.MinZoom {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	b, ct, err := fetchSourceTile(t.URL, z, x, y, tileProxyTimeout)
	if err != nil {
		// 204 not 5xx: MapLibre retries a 500 and turns an upstream outage
		// into a request storm (same rule as the CARTO proxy).
		w.Header().Set("X-Tile-Error", firstWord(err.Error(), "fetch failed"))
		w.WriteHeader(http.StatusNoContent)
		return
	}
	w.Header().Set("Content-Type", ct)
	w.Header().Set("Cache-Control", "private, max-age=3600")
	w.Write(b)
}

// ---- map preferences ----------------------------------------------------------

type MapPrefs struct {
	DefaultBase      string `json:"default_base"`
	DefaultSatellite string `json:"default_satellite"`
}

func (s *Server) mapPrefs(ref string) MapPrefs {
	var p MapPrefs
	if ref == "" {
		return p
	}
	var b, sat *string
	if err := s.DB.QueryRow(`SELECT default_base, default_satellite FROM map_prefs WHERE pwd_ref = ?`, ref).Scan(&b, &sat); err == nil {
		if b != nil {
			p.DefaultBase = *b
		}
		if sat != nil {
			p.DefaultSatellite = *sat
		}
	}
	return p
}

// builtinBasemaps are the ids the frontend always has. A preference naming
// anything else must name a source the login owns.
var builtinBasemaps = map[string]bool{"dark": true, "satellite-s2": true}

func (s *Server) basemapIDValid(id, ref string) bool {
	if id == "" || builtinBasemaps[id] {
		return true
	}
	if !strings.HasPrefix(id, tileSourcePrefix) || ref == "" {
		return false
	}
	_, err := s.loadTileSource(strings.TrimPrefix(id, tileSourcePrefix), ref, false)
	return err == nil
}

// HandleAPIMapPrefs — GET/PUT /api/map-prefs. PUT {default_base?, default_satellite?}
// ("" clears). Guests get the empty prefs on GET and 403 on PUT.
func (s *Server) HandleAPIMapPrefs(w http.ResponseWriter, r *http.Request) {
	ref := shortCallerRef(r)
	if GuestFromRequest(r) != nil {
		ref = ""
	}
	if r.Method == http.MethodGet {
		w.Header().Set("Cache-Control", "no-store")
		writeJSON(w, http.StatusOK, s.mapPrefs(ref))
		return
	}
	if ref == "" {
		http.Error(w, "sign in to save map preferences", http.StatusForbidden)
		return
	}
	var b map[string]*string
	if err := decodeJSONBody(r, &b); err != nil {
		http.Error(w, "bad request body", http.StatusBadRequest)
		return
	}
	cur := s.mapPrefs(ref)
	if v, ok := b["default_base"]; ok && v != nil {
		if !s.basemapIDValid(*v, ref) {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "unknown basemap"})
			return
		}
		cur.DefaultBase = *v
	}
	if v, ok := b["default_satellite"]; ok && v != nil {
		if *v == "dark" || !s.basemapIDValid(*v, ref) {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": "unknown satellite basemap"})
			return
		}
		cur.DefaultSatellite = *v
	}
	nullable := func(v string) interface{} {
		if v == "" {
			return nil
		}
		return v
	}
	s.DB.Exec(`INSERT INTO map_prefs (pwd_ref, default_base, default_satellite, updated_at) VALUES (?,?,?,?)
		ON CONFLICT(pwd_ref) DO UPDATE SET default_base=excluded.default_base,
		default_satellite=excluded.default_satellite, updated_at=excluded.updated_at`,
		ref, nullable(cur.DefaultBase), nullable(cur.DefaultSatellite), time.Now().UTC().Format(time.RFC3339))
	writeJSON(w, http.StatusOK, cur)
}
