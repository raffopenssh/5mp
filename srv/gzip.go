package srv

import (
	"compress/gzip"
	"net/http"
	"strings"
	"sync"
)

// gzipResponseWriter compresses a response -- but only once it knows what the
// response is.
//
// The middleware used to set `Content-Encoding: gzip` and swap in a gzip.Writer
// *before* calling the handler, i.e. before anything knew the content type.
// That is wrong for every binary download we serve, and wrong in the worst way:
//
//   - `http.ServeContent` sets `Content-Length` from the file's size, which the
//     middleware then could not correct because the bytes it sends are the
//     compressed ones. The browser is told 101,113,856 bytes, receives
//     51,381,359, and reports a *network error* -- "Die Netzwerkverbindung wurde
//     unterbrochen" on a 100 MB GeoPackage that transferred perfectly.
//     Measured 2026-08-10 on /api/geopackage/{id}/download.
//   - It also broke Range: a 206 body would be gzipped while Content-Range still
//     described the raw byte span, so a resumed field download of the 3.6 GB
//     historical-map MBTiles could not be reassembled.
//   - And it spent CPU deflating SQLite, MBTiles, PNG and zip payloads that are
//     already compressed, for ~0 gain.
//
// So the decision is deferred to the first Write (or explicit WriteHeader),
// when the handler has set its Content-Type. Compress text-ish payloads,
// pass everything else straight through untouched -- headers included, which is
// what makes ServeContent's Content-Length and Range handling correct again.
type gzipResponseWriter struct {
	http.ResponseWriter
	gz *gzip.Writer

	decided bool
	useGz   bool
	status  int
}

// compressibleType reports whether a Content-Type is worth deflating. Empty
// means "the handler never said", in which case we let the caller sniff.
func compressibleType(ct string) bool {
	ct = strings.ToLower(strings.TrimSpace(ct))
	if i := strings.IndexByte(ct, ';'); i >= 0 {
		ct = strings.TrimSpace(ct[:i])
	}
	if ct == "" {
		return false
	}
	if strings.HasPrefix(ct, "text/") {
		return true
	}
	switch ct {
	case "application/json", "application/javascript", "application/x-javascript",
		"application/xml", "application/xhtml+xml", "application/rss+xml",
		"application/atom+xml", "application/geo+json", "application/vnd.geo+json",
		"application/ld+json", "application/manifest+json", "application/wasm",
		"image/svg+xml", "application/vnd.google-earth.kml+xml":
		return true
	}
	return strings.HasSuffix(ct, "+json") || strings.HasSuffix(ct, "+xml")
}

// decide picks compression once, using the headers the handler has set. body may
// be the first chunk about to be written, used only to sniff a missing type.
func (w *gzipResponseWriter) decide(body []byte) {
	if w.decided {
		return
	}
	w.decided = true
	h := w.ResponseWriter.Header()

	switch {
	case h.Get("Content-Encoding") != "": // handler compressed it itself
	case h.Get("Content-Range") != "", w.status == http.StatusPartialContent:
		// A byte range describes the *identity* representation.
	case w.status == http.StatusNoContent, w.status == http.StatusNotModified:
	case strings.HasPrefix(h.Get("Content-Disposition"), "attachment"):
		// A download is a file on someone's disk; keep it byte-exact and
		// resumable. (KML/GPX are attachments too -- they lose a little
		// bandwidth here and gain a working Range request.)
	default:
		ct := h.Get("Content-Type")
		if ct == "" && len(body) > 0 {
			ct = http.DetectContentType(body)
			h.Set("Content-Type", ct) // pin it: gzip would defeat Go's sniffing
		}
		w.useGz = compressibleType(ct)
	}

	h.Add("Vary", "Accept-Encoding")
	if w.useGz {
		h.Set("Content-Encoding", "gzip")
		// The length of the compressed body is not knowable here.
		h.Del("Content-Length")
		// Range on a compressed body would be a different byte space.
		h.Del("Accept-Ranges")
	}
}

func (w *gzipResponseWriter) WriteHeader(code int) {
	w.status = code
	w.decide(nil)
	w.ResponseWriter.WriteHeader(code)
}

func (w *gzipResponseWriter) Write(b []byte) (int, error) {
	if !w.decided {
		w.decide(b)
	}
	if w.useGz {
		return w.gz.Write(b)
	}
	return w.ResponseWriter.Write(b)
}

// Flush implements http.Flusher for streaming responses.
func (w *gzipResponseWriter) Flush() {
	if w.useGz {
		w.gz.Flush()
	}
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// Unwrap lets http.ResponseController reach the underlying writer.
func (w *gzipResponseWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

var gzipPool = sync.Pool{
	New: func() any {
		gz, _ := gzip.NewWriterLevel(nil, gzip.BestSpeed)
		return gz
	},
}

// GzipMiddleware compresses text-ish responses for clients that accept gzip.
// See gzipResponseWriter: the choice is made after the handler sets its
// Content-Type, so binary downloads pass through untouched and keep their
// Content-Length and Range support.
func GzipMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
			next.ServeHTTP(w, r)
			return
		}

		// Skip for Server-Sent Events
		if strings.Contains(r.Header.Get("Accept"), "text/event-stream") {
			next.ServeHTTP(w, r)
			return
		}

		gz := gzipPool.Get().(*gzip.Writer)
		defer gzipPool.Put(gz)
		gz.Reset(w)

		grw := &gzipResponseWriter{ResponseWriter: w, gz: gz, status: http.StatusOK}
		next.ServeHTTP(grw, r)
		if grw.useGz {
			gz.Close()
		}
	})
}
