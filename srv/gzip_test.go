package srv

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// The bug this pins: the middleware used to gzip every response, including
// binary downloads, while http.ServeContent had already set Content-Length from
// the file size. The browser was promised N bytes and got the compressed count,
// which surfaces as "network connection interrupted" on a file that transferred
// fine (measured on a 100 MB GeoPackage, 2026-08-10).
func TestGzipSkipsAttachments(t *testing.T) {
	body := strings.Repeat("A", 4096)
	h := GzipMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/geopackage+sqlite3")
		w.Header().Set("Content-Disposition", `attachment; filename="x.gpkg"`)
		w.Header().Set("Content-Length", "4096")
		w.Write([]byte(body))
	}))
	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/api/geopackage/x/download", nil)
	req.Header.Set("Accept-Encoding", "gzip")
	h.ServeHTTP(rec, req)

	if enc := rec.Header().Get("Content-Encoding"); enc != "" {
		t.Fatalf("attachment was compressed: Content-Encoding=%q", enc)
	}
	if got := rec.Header().Get("Content-Length"); got != "4096" {
		t.Fatalf("Content-Length=%q, want 4096", got)
	}
	if rec.Body.Len() != 4096 {
		t.Fatalf("body %d bytes, want 4096 (Content-Length must match the bytes sent)", rec.Body.Len())
	}
}

// A 206 body describes a byte span of the identity representation, so gzipping
// it makes Content-Range a lie and a resumed download unassemblable.
func TestGzipSkipsPartialContent(t *testing.T) {
	h := GzipMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		w.Header().Set("Content-Range", "bytes 0-9/1000")
		w.WriteHeader(http.StatusPartialContent)
		w.Write([]byte("0123456789"))
	}))
	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/x", nil)
	req.Header.Set("Accept-Encoding", "gzip")
	h.ServeHTTP(rec, req)
	if enc := rec.Header().Get("Content-Encoding"); enc != "" {
		t.Fatalf("206 was compressed: %q", enc)
	}
	if rec.Body.String() != "0123456789" {
		t.Fatalf("body=%q", rec.Body.String())
	}
}

// ...and text still compresses, or the fix would just be "turn gzip off".
func TestGzipCompressesJSON(t *testing.T) {
	h := GzipMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"a":"` + strings.Repeat("x", 4096) + `"}`))
	}))
	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/api/parks/x/stats", nil)
	req.Header.Set("Accept-Encoding", "gzip")
	h.ServeHTTP(rec, req)
	if rec.Header().Get("Content-Encoding") != "gzip" {
		t.Fatal("json was not compressed")
	}
	if rec.Header().Get("Content-Length") != "" {
		t.Fatal("stale Content-Length survived compression")
	}
	if rec.Body.Len() >= 4096 {
		t.Fatalf("compressed body is %d bytes, no smaller than the input", rec.Body.Len())
	}
}
