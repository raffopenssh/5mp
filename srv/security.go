package srv

import (
	"net/http"
	"strings"
)

// SecurityHeadersMiddleware adds standard security headers to all responses.
func SecurityHeadersMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Referrer-Policy", "strict-origin-when-cross-origin")
		w.Header().Set("Permissions-Policy", "camera=(), microphone=(), geolocation=(self)")
		w.Header().Set("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")
		next.ServeHTTP(w, r)
	})
}

// PrivateCacheMiddleware downgrades `Cache-Control: public` to `private` on
// every authenticated response, and marks them as varying by Cookie.
//
// This is not hygiene, it is a correctness fix. Handlers behind the password
// gate answer *per tenant* (srv/tenant.go): /api/grid, /api/stats,
// /api/fire-frames?layer=effort and the exports all return one account's
// patrol data and another account's nothing. A `public` response is cacheable
// by any shared cache **and by the browser's own HTTP cache, which is keyed on
// the URL, not on the cookie** — so switching accounts in one browser served
// the previous account's body from disk. Measured 2026-08-10: after visiting
// as the AOI owner (0 pixels), the same browser as the client account got
// `{"features":[]}` from cache while curl got 259 features, i.e. "the pixels
// disappeared" with a correct server.
//
// Downgrading rather than deleting keeps the revalidation behaviour handlers
// asked for (max-age, ETag) while confining the copy to the one browser that
// authenticated. The server-side ResponseCacheMiddleware is unaffected: its key
// already carries the tenant and the visibility fingerprint.
//
// Anything genuinely public must be served outside this chain (static assets,
// /robots.txt, /sitemap.xml already are).
func PrivateCacheMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if isPublicPath(r.URL.Path) {
			next.ServeHTTP(w, r)
			return
		}
		next.ServeHTTP(&privateCacheWriter{ResponseWriter: w}, r)
	})
}

type privateCacheWriter struct {
	http.ResponseWriter
	fixed bool
}

func (w *privateCacheWriter) fix() {
	if w.fixed {
		return
	}
	w.fixed = true
	h := w.Header()
	if cc := h.Get("Cache-Control"); cc != "" {
		if strings.Contains(cc, "public") {
			h.Set("Cache-Control", strings.ReplaceAll(cc, "public", "private"))
		} else if !strings.Contains(cc, "private") && !strings.Contains(cc, "no-store") {
			h.Set("Cache-Control", "private, "+cc)
		}
	}
	// A cache that ignores the cookie would key two tenants onto one entry.
	h.Add("Vary", "Cookie")
}

func (w *privateCacheWriter) WriteHeader(code int) {
	w.fix()
	w.ResponseWriter.WriteHeader(code)
}

func (w *privateCacheWriter) Write(b []byte) (int, error) {
	w.fix()
	return w.ResponseWriter.Write(b)
}

func (w *privateCacheWriter) Flush() {
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// Unwrap lets http.ResponseController reach the underlying writer.
func (w *privateCacheWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }
