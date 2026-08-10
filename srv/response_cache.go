package srv

import (
	"bytes"
	"net/http"
	"strings"
	"sync"
	"time"
)

// ResponseCacheMiddleware is a small in-process cache for hot, read-only
// per-park API endpoints. The underlying data changes at most daily (3am
// fire cron), but every user opening a park popup fires the same handful of
// queries; on a 2-CPU box that serializes under load. Caching identical
// responses for a few minutes collapses that work, and singleflight ensures
// a burst of users on the same park computes each response only once.
//
// Scope is a strict prefix+suffix whitelist (see cacheablePath) — nothing
// user-specific, nothing mutating, GET only, 200s only.
type responseCacheEntry struct {
	body        []byte
	contentType string
	expires     time.Time
}

type responseCache struct {
	mu       sync.Mutex
	entries  map[string]*responseCacheEntry
	inFlight map[string]*sync.WaitGroup
	size     int
}

const (
	respCacheTTL      = 5 * time.Minute
	respCacheMaxEntry = 8 << 20   // 8 MB per response
	respCacheMaxTotal = 128 << 20 // 128 MB total
)

// cacheableSuffixes lists park-scoped GET endpoints that are safe to cache:
// pure functions of the database, no auth-dependent content.
var cacheableSuffixes = []string{
	"/stats", "/fire-narrative", "/fire-trend", "/fire-realtime",
	"/features", "/climate", "/species", "/fire-log", "/legal",
	"/narrative", "/rivers", "/roads", "/places",
}

func cacheablePath(r *http.Request) bool {
	if r.Method != http.MethodGet {
		return false
	}
	p := r.URL.Path
	if !strings.HasPrefix(p, "/api/parks/") {
		return false
	}
	for _, suf := range cacheableSuffixes {
		if strings.HasSuffix(p, suf) {
			return true
		}
	}
	return false
}

// cacheKey ignores the specific pwd value (auth already passed) so different
// users share entries, but MUST keep two discriminators.
//
// visibilityFingerprint: AOI-scoped responses depend on *who* is asking
// (docs/PLAN_AOI_OVERLAY.md §9). A visibility-blind key would serve a private
// AOI body from the shared cache to the next caller. It is included for every
// cacheable path, not just AOI ones, because "which endpoints are
// audience-dependent" is exactly the kind of list that goes stale; the cost is
// one cache entry per active password.
//
// RequestEnv: the env tenant. several cached endpoints are
// env-dependent (/stats patrol pixels, /features learned road+airstrip
// filtering), so a tenant-blind key serves prod client data to the test
// environment and vice versa.
func cacheKey(r *http.Request) string {
	q := r.URL.Query()
	q.Del("pwd")
	return RequestEnv(r) + "|" + visibilityFingerprint(r) + "|" + r.URL.Path + "?" + q.Encode()
}

type cacheRecorder struct {
	http.ResponseWriter
	status int
	buf    bytes.Buffer
}

func (cr *cacheRecorder) WriteHeader(code int) {
	cr.status = code
	cr.ResponseWriter.WriteHeader(code)
}

func (cr *cacheRecorder) Write(b []byte) (int, error) {
	if cr.status == 200 && cr.buf.Len()+len(b) <= respCacheMaxEntry {
		cr.buf.Write(b)
	}
	return cr.ResponseWriter.Write(b)
}

func (s *Server) ResponseCacheMiddleware(next http.Handler) http.Handler {
	rc := &responseCache{
		entries:  map[string]*responseCacheEntry{},
		inFlight: map[string]*sync.WaitGroup{},
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !cacheablePath(r) {
			next.ServeHTTP(w, r)
			return
		}
		key := cacheKey(r)

		for {
			rc.mu.Lock()
			if e := rc.entries[key]; e != nil && time.Now().Before(e.expires) {
				rc.mu.Unlock()
				w.Header().Set("Content-Type", e.contentType)
				w.Header().Set("Cache-Control", "public, max-age=300")
				w.Header().Set("X-Response-Cache", "HIT")
				w.Write(e.body)
				return
			}
			// Singleflight: if another request is computing this key, wait
			// for it and re-check the cache.
			if wg := rc.inFlight[key]; wg != nil {
				rc.mu.Unlock()
				wg.Wait()
				continue
			}
			wg := &sync.WaitGroup{}
			wg.Add(1)
			rc.inFlight[key] = wg
			rc.mu.Unlock()

			cr := &cacheRecorder{ResponseWriter: w, status: 200}
			w.Header().Set("Cache-Control", "public, max-age=300")
			next.ServeHTTP(cr, r)

			rc.mu.Lock()
			if cr.status == 200 && cr.buf.Len() > 0 && cr.buf.Len() <= respCacheMaxEntry {
				// Evict everything if over budget — simple and rare.
				if rc.size+cr.buf.Len() > respCacheMaxTotal {
					rc.entries = map[string]*responseCacheEntry{}
					rc.size = 0
				}
				body := append([]byte(nil), cr.buf.Bytes()...)
				rc.entries[key] = &responseCacheEntry{
					body:        body,
					contentType: cr.Header().Get("Content-Type"),
					expires:     time.Now().Add(respCacheTTL),
				}
				rc.size += len(body)
			}
			delete(rc.inFlight, key)
			rc.mu.Unlock()
			wg.Done()
			return
		}
	})
}
