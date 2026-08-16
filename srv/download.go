package srv

import (
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"time"
)

// isDownloadStart reports whether this request begins a download, as opposed
// to resuming one. A client on a flaky link fetches one file in many Range
// requests; counting each 206 as a "download" inflates the stat and, worse,
// makes a resumed transfer look like N downloads. A request with no Range
// header, or one whose range starts at byte 0, is the start.
func isDownloadStart(r *http.Request) bool {
	rng := strings.ReplaceAll(r.Header.Get("Range"), " ", "")
	return rng == "" || strings.HasPrefix(rng, "bytes=0-")
}

// Long downloads must not inherit the server's WriteTimeout.
//
// http.Server.WriteTimeout is an *absolute* deadline on the whole response,
// set once when the request is read -- it is not an idle timeout. At 120 s
// that is fine for JSON and for a 100 MB export on a fast link, and it
// silently caps every large file at (120 s x client bandwidth):
//
//	3.86 GB histmap MBTiles, measured 2026-08-16 on localhost
//	  40 MB/s -> 3,860,185,088 bytes, 200, complete
//	  20 MB/s ->   877,516,168 bytes, connection closed mid-body (23%)
//	   5 MB/s ->   639,299,576 bytes, curl exit 18 (17%)
//
// The client sees a truncated body with a *correct* Content-Length, i.e. a
// network error -- which is why "the 3 GB download always fails" looked like
// a client or proxy problem for weeks. Anyone on less than ~32 MB/s could
// never finish, and the same arithmetic caps a GeoPackage or a satellite
// MBTiles at whatever 120 s buys.
//
// The fix is not a bigger constant (a 3.9 GB file on a 1 Mbit field link needs
// ~9 hours; any absolute number is wrong for some client). It is to replace
// the absolute deadline with an idle one: the deadline is pushed forward as
// bytes leave, so a *progressing* transfer never expires and a *stalled* one
// still dies within downloadIdleTimeout. Range requests then let a dropped
// transfer resume rather than restart.
//
// Use it in any handler that streams a file whose size is not bounded by a
// few MB (grep: ServeContent / ServeFile / io.Copy in this package).
const downloadIdleTimeout = 10 * time.Minute

// longDownload returns a ResponseWriter that keeps its own write deadline
// alive while bytes flow. Call it before writing headers; write to the
// returned writer.
//
// If the platform has no deadline support (a wrapped writer that does not
// unwrap, tests with httptest.ResponseRecorder), it degrades to the plain
// writer -- there is no deadline to extend in that case either.
func longDownload(w http.ResponseWriter) http.ResponseWriter {
	rc := http.NewResponseController(w)
	dw := &deadlineWriter{ResponseWriter: w, rc: rc, idle: downloadIdleTimeout}
	if !dw.extend() {
		return w
	}
	return dw
}

type deadlineWriter struct {
	http.ResponseWriter
	rc       *http.ResponseController
	idle     time.Duration
	next     time.Time // when the current deadline needs refreshing
	disabled bool
}

// extend pushes the write deadline out by idle, at most twice per idle
// window (one syscall per ~5 minutes of transfer, not one per 32 KB chunk).
func (w *deadlineWriter) extend() bool {
	if w.disabled {
		return false
	}
	now := time.Now()
	if !w.next.IsZero() && now.Before(w.next) {
		return true
	}
	if err := w.rc.SetWriteDeadline(now.Add(w.idle)); err != nil {
		if !errors.Is(err, http.ErrNotSupported) {
			slog.Warn("long download: cannot extend write deadline", "err", err)
		}
		w.disabled = true
		return false
	}
	w.next = now.Add(w.idle / 2)
	return true
}

func (w *deadlineWriter) Write(b []byte) (int, error) {
	w.extend()
	return w.ResponseWriter.Write(b)
}

func (w *deadlineWriter) WriteHeader(code int) {
	w.extend()
	w.ResponseWriter.WriteHeader(code)
}

func (w *deadlineWriter) Flush() {
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// Unwrap lets http.ResponseController and other middleware reach through.
func (w *deadlineWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

// longUpload is the mirror image for request *bodies*: http.Server.ReadTimeout
// (30 s here) is likewise absolute and likewise caps an upload at 30 s x the
// client's uplink -- a 2 GB GHSL zip or a 500 MB fire CSV cannot be posted from
// anything slower than a datacentre. Call it before ParseMultipartForm/io.Copy
// in any handler that accepts a file larger than a few MB; the deadline then
// advances while bytes arrive and only a stalled upload expires.
//
// It must extend the WRITE deadline too, and that is not symmetry for its own
// sake. WriteTimeout is armed when the request is *read*, not when the reply is
// written, so it burns down during the upload: a POST that takes longer than
// 120 s had its response killed before the handler could answer, even though
// the file was already stored on disk and in the table. The client saw a closed
// connection (or, behind the exe.dev proxy, the proxy's own HTML error page),
// so a 118 MB upload over a domestic uplink reported "Server error" for an
// upload that had in fact succeeded -- the worst failure mode available: a
// completed write reported as a failure, inviting a retry that duplicates it.
// Measured 2026-08-16 on localhost: 20 MB at 140 KB/s = 139 s, connection
// closed, no status line, row present in shared_files.
func longUpload(w http.ResponseWriter, r *http.Request) {
	if r.Body == nil {
		return
	}
	dr := &deadlineReader{ReadCloser: r.Body, rc: http.NewResponseController(w), idle: downloadIdleTimeout}
	if !dr.extend() {
		return
	}
	r.Body = dr
}

type deadlineReader struct {
	io.ReadCloser
	rc       *http.ResponseController
	idle     time.Duration
	next     time.Time
	disabled bool
}

func (r *deadlineReader) extend() bool {
	if r.disabled {
		return false
	}
	now := time.Now()
	if !r.next.IsZero() && now.Before(r.next) {
		return true
	}
	if err := r.rc.SetReadDeadline(now.Add(r.idle)); err != nil {
		if !errors.Is(err, http.ErrNotSupported) {
			slog.Warn("long upload: cannot extend read deadline", "err", err)
		}
		r.disabled = true
		return false
	}
	// The reply's deadline is burning down while the body arrives (see
	// longUpload): push it out by the same window, or a slow upload succeeds
	// and then cannot say so.
	if err := r.rc.SetWriteDeadline(now.Add(r.idle)); err != nil && !errors.Is(err, http.ErrNotSupported) {
		slog.Warn("long upload: cannot extend write deadline", "err", err)
	}
	r.next = now.Add(r.idle / 2)
	return true
}

func (r *deadlineReader) Read(p []byte) (int, error) {
	r.extend()
	return r.ReadCloser.Read(p)
}
