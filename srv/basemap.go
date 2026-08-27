package srv

import (
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// CARTO base-map tiles, fetched server-side with our API key.
//
// CARTO began requiring a key on the raster (PNG) basemaps in August 2026 and
// watermarks every unauthenticated tile with "API KEY REQUIRED" -- the tiles
// still arrive, so nothing 4xx's and no log line appears; the map simply reads
// as broken. (Measured 2026-08-27: keyed tile 22,653 B, unkeyed 26,128 B for
// the same z5 tile, the difference being the watermark.)
//
// Three constraints shape this file:
//
//   - The key must not reach the browser. The tile URLs in globe.html,
//     fire_animation.html and fire_analysis.html are fetched by the client, so
//     a `?key=` templated into them is published to anyone who opens devtools
//     -- and CARTO's terms say the key is ours and must not be shared. So the
//     client asks *us* and we ask CARTO.
//   - The free tier is 5 million tile requests per calendar month, counted
//     across raster and vector. A proxy without a cache would spend that
//     allowance on repeat views of the same twenty parks, so tiles land on
//     disk and are served from there; only a miss costs quota.
//   - Attribution ("(c) OpenStreetMap contributors, (c) CARTO") is what the
//     free tier is in exchange for. It stays in the map styles and in the
//     footer; do not remove it when touching a style.
//
// Route: GET /api/basemap/{style}/{z}/{x}/{y}  (y may carry "@2x" and ".png")

// cartoStyles maps the style names we accept to their upstream path segment.
// An allowlist, not a passthrough: the {style} segment comes from the URL, and
// forwarding it verbatim would make this an open proxy to any cartocdn path.
var cartoStyles = map[string]string{
	"dark_all":       "dark_all",
	"light_all":      "light_all",
	"voyager":        "rastertiles/voyager",
	"positron":       "light_all",
	"dark_nolabels":  "dark_nolabels",
	"light_nolabels": "light_nolabels",
}

var (
	cartoKeyOnce sync.Once
	cartoKey     string
)

// cartoAPIKey returns the CARTO base-map key from the environment, falling back
// to secrets.env (the untracked wallet systemd loads as EnvironmentFile).
//
// Absent, it names the variable once rather than failing: an unkeyed tile is a
// watermarked map, not a dead one, and a dev box without the wallet should
// still render. The warning is the tell -- a silently unkeyed deployment is
// exactly the failure this file exists to fix.
func cartoAPIKey() string {
	cartoKeyOnce.Do(func() {
		cartoKey = strings.TrimSpace(os.Getenv("CARTO_API_KEY"))
		if cartoKey == "" {
			cartoKey = secretsEnv("CARTO_API_KEY")
		}
		if cartoKey == "" {
			slog.Warn("CARTO_API_KEY is not set: base-map tiles will carry CARTO's 'API KEY REQUIRED' watermark",
				"var", "CARTO_API_KEY", "where", "secrets.env or environment")
		}
	})
	return cartoKey
}

const (
	cartoCacheDir = "data/basemap_cache"
	// Tiles change rarely (CARTO is considering freezing the raster
	// cartography entirely) and a stale road is harmless; a month of cache is
	// the difference between a few thousand upstream fetches and a few million.
	cartoCacheTTL = 30 * 24 * time.Hour
)

// HandleAPIBasemapTile serves one CARTO raster tile, from disk when we have it
// and from CARTO (with our key) when we do not.
func (s *Server) HandleAPIBasemapTile(w http.ResponseWriter, r *http.Request) {
	stylePath, ok := cartoStyles[r.PathValue("style")]
	if !ok {
		http.Error(w, "unknown basemap style", http.StatusNotFound)
		return
	}
	z, err1 := strconv.Atoi(r.PathValue("z"))
	x, err2 := strconv.Atoi(r.PathValue("x"))

	// The client asks for ".../{y}@2x.png": MapLibre and Leaflet both append
	// the extension, and @2x is CARTO's retina variant (a different upstream
	// path, so it must survive into the cache key).
	yStr := strings.TrimSuffix(r.PathValue("y"), ".png")
	retina := ""
	if strings.HasSuffix(yStr, "@2x") {
		retina = "@2x"
		yStr = strings.TrimSuffix(yStr, "@2x")
	}
	y, err3 := strconv.Atoi(yStr)
	if err1 != nil || err2 != nil || err3 != nil || z < 0 || z > 20 {
		http.Error(w, "bad tile coordinate", http.StatusBadRequest)
		return
	}
	if max := 1 << uint(z); x < 0 || y < 0 || x >= max || y >= max {
		http.Error(w, "tile out of range", http.StatusBadRequest)
		return
	}

	cacheFile := filepath.Join(cartoCacheDir,
		fmt.Sprintf("%s%s", r.PathValue("style"), retina),
		strconv.Itoa(z), strconv.Itoa(x), strconv.Itoa(y)+".png")

	if blob, err := readFreshTile(cacheFile); err == nil {
		serveTile(w, blob, "hit")
		return
	}

	blob, err := fetchCartoTile(stylePath, z, x, y, retina)
	if err != nil {
		// A stale tile beats a hole in the map: if CARTO is unreachable but we
		// fetched this tile last month, draw last month's.
		if stale, serr := os.ReadFile(cacheFile); serr == nil && len(stale) > 0 {
			serveTile(w, stale, "stale")
			return
		}
		slog.Warn("carto tile fetch failed", "style", stylePath, "z", z, "x", x, "y", y, "error", err)
		// 204, not 5xx: MapLibre logs an error and retries on a 500, which
		// amplifies an upstream outage into a request storm.
		w.WriteHeader(http.StatusNoContent)
		return
	}
	writeTileCache(cacheFile, blob)
	serveTile(w, blob, "miss")
}

func serveTile(w http.ResponseWriter, blob []byte, source string) {
	w.Header().Set("Content-Type", "image/png")
	// Private (invariant 9: this path is behind the password gate, and
	// PrivateCacheMiddleware would rewrite "public" anyway) but long-lived, so
	// a reader panning around does not re-ask us for tiles they already have.
	w.Header().Set("Cache-Control", "private, max-age=604800")
	w.Header().Set("X-Tile-Cache", source)
	// Says whether the bytes were fetched with a key, i.e. whether they can
	// carry the watermark. Tested by tests/api_tests.sh; a deployment that
	// lost its key answers "no" here instead of quietly looking broken.
	if cartoAPIKey() != "" {
		w.Header().Set("X-Carto-Key", "yes")
	} else {
		w.Header().Set("X-Carto-Key", "no")
	}
	w.Write(blob)
}

func readFreshTile(path string) ([]byte, error) {
	st, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if time.Since(st.ModTime()) > cartoCacheTTL {
		return nil, os.ErrDeadlineExceeded
	}
	blob, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(blob) == 0 {
		return nil, os.ErrNotExist
	}
	return blob, nil
}

// writeTileCache writes via a temp file + rename so a concurrent reader never
// sees a half-written PNG (two viewers panning the same area race here).
func writeTileCache(path string, blob []byte) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".tile-*")
	if err != nil {
		return
	}
	if _, err := tmp.Write(blob); err != nil {
		tmp.Close()
		os.Remove(tmp.Name())
		return
	}
	tmp.Close()
	if err := os.Rename(tmp.Name(), path); err != nil {
		os.Remove(tmp.Name())
	}
}

var cartoClient = &http.Client{Timeout: 20 * time.Second}

func fetchCartoTile(stylePath string, z, x, y int, retina string) ([]byte, error) {
	url := fmt.Sprintf("https://basemaps.cartocdn.com/%s/%d/%d/%d%s.png", stylePath, z, x, y, retina)
	if key := cartoAPIKey(); key != "" {
		url += "?key=" + key
	}
	resp, err := cartoClient.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("carto status %d", resp.StatusCode)
	}
	blob, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return nil, err
	}
	if len(blob) == 0 {
		return nil, fmt.Errorf("empty tile body")
	}
	cartoQuota.count()
	return blob, nil
}

// --- quota -------------------------------------------------------------------

// cartoQuota counts *upstream* fetches per calendar month, because that is the
// number CARTO's 5M fair-use limit is about -- cache hits cost nothing and
// counting requests instead would overstate usage by an order of magnitude.
// Persisted so a restart does not reset the month, and logged at each 100k so
// approaching the limit is visible in the journal before CARTO gets in touch.
var cartoQuota = &quotaCounter{path: "data/basemap_quota.json"}

type quotaCounter struct {
	path string
	mu   sync.Mutex
	Mon  string `json:"month"`
	N    int    `json:"upstream_fetches"`
	load sync.Once
}

// ensureLoaded reads the persisted month/count. Called by both count() and the
// reporter, so a server that has served only cache hits still reports the
// month's real total rather than zero.
func (q *quotaCounter) ensureLoaded() {
	q.load.Do(func() {
		if data, err := os.ReadFile(q.path); err == nil {
			var st struct {
				Month string `json:"month"`
				N     int    `json:"upstream_fetches"`
			}
			if json.Unmarshal(data, &st) == nil {
				q.Mon, q.N = st.Month, st.N
			}
		}
	})
}

func (q *quotaCounter) count() {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.ensureLoaded()
	month := time.Now().UTC().Format("2006-01")
	if q.Mon != month {
		q.Mon, q.N = month, 0
	}
	q.N++
	if q.N%100000 == 0 {
		slog.Info("carto basemap upstream fetches this month", "month", q.Mon, "fetches", q.N, "fair_use_limit", 5000000)
	}
	// Every 100th fetch: a restart can lose at most 99 from the count, which
	// is noise against a 5M limit, and the file is 60 bytes.
	if q.N%100 == 0 || q.N == 1 {
		if data, err := json.Marshal(map[string]any{"month": q.Mon, "upstream_fetches": q.N}); err == nil {
			os.MkdirAll(filepath.Dir(q.path), 0o755)
			os.WriteFile(q.path, data, 0o644)
		}
	}
}

// CartoQuotaThisMonth reports the persisted+live upstream fetch count, for
// /api/pipeline-status and for tests.
func CartoQuotaThisMonth() (string, int) {
	cartoQuota.mu.Lock()
	defer cartoQuota.mu.Unlock()
	cartoQuota.ensureLoaded()
	if month := time.Now().UTC().Format("2006-01"); cartoQuota.Mon != month {
		// A stale month is not this month's usage: report the current month at
		// zero rather than last month's total under today's name.
		return month, 0
	}
	return cartoQuota.Mon, cartoQuota.N
}
