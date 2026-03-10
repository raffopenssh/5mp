package srv

import (
	"compress/gzip"
	"io"
	"net/http"
	"strings"
	"sync"
)

var gzipPool = sync.Pool{
	New: func() any {
		w, _ := gzip.NewWriterLevel(io.Discard, gzip.DefaultCompression)
		return w
	},
}

type gzipResponseWriter struct {
	http.ResponseWriter
	gw *gzip.Writer
}

func (w *gzipResponseWriter) Write(b []byte) (int, error) {
	return w.gw.Write(b)
}

// GzipMiddleware compresses responses for clients that accept gzip.
func GzipMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
			next.ServeHTTP(w, r)
			return
		}

		gw := gzipPool.Get().(*gzip.Writer)
		gw.Reset(w)
		defer func() {
			gw.Close()
			gzipPool.Put(gw)
		}()

		w.Header().Set("Content-Encoding", "gzip")
		w.Header().Del("Content-Length") // length will change
		w.Header().Set("Vary", "Accept-Encoding")

		next.ServeHTTP(&gzipResponseWriter{ResponseWriter: w, gw: gw}, r)
	})
}
