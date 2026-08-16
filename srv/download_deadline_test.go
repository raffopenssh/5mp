package srv

import (
	"bytes"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// TestLongDownloadOutlivesWriteTimeout is the regression test for the bug that
// made the 3.86 GB histmap MBTiles "always fail": http.Server.WriteTimeout is
// absolute, so a slow client is cut off mid-body no matter how healthy the
// transfer is. It must not be a no-op test -- so it first proves the *plain*
// writer is truncated under the same conditions, then that longDownload is not.
func TestLongDownloadOutlivesWriteTimeout(t *testing.T) {
	// Must exceed the kernel socket buffers (a few MB), or the handler's writes
	// all complete instantly and the deadline is never reached.
	const size = 32 << 20 // 32 MiB, drained at 256 KiB per 5 ms (~640 ms)
	dir := t.TempDir()
	path := filepath.Join(dir, "blob.bin")
	if err := os.WriteFile(path, bytes.Repeat([]byte("x"), size), 0o644); err != nil {
		t.Fatal(err)
	}

	serve := func(long bool) (int, error) {
		ln, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		mux := http.NewServeMux()
		mux.HandleFunc("/blob", func(w http.ResponseWriter, r *http.Request) {
			f, err := os.Open(path)
			if err != nil {
				t.Error(err)
				return
			}
			defer f.Close()
			st, _ := f.Stat()
			out := w
			if long {
				out = longDownload(w)
			}
			w.Header().Set("Content-Type", "application/octet-stream")
			http.ServeContent(out, r, "blob.bin", st.ModTime(), f)
		})
		srv := &http.Server{
			Handler: GzipMiddleware(mux),
			// Deliberately tiny: 200 ms of absolute write budget cannot cover
			// the ~640 ms the client takes to drain the body.
			WriteTimeout: 200 * time.Millisecond,
		}
		go srv.Serve(ln)
		defer srv.Close()

		req, _ := http.NewRequest("GET", fmt.Sprintf("http://%s/blob", ln.Addr()), nil)
		req.Header.Set("Accept-Encoding", "gzip") // the real client path
		resp, err := http.DefaultTransport.RoundTrip(req)
		if err != nil {
			return 0, err
		}
		defer resp.Body.Close()
		// Drain slowly: 256 KiB every 5 ms.
		var n int
		buf := make([]byte, 256<<10)
		for {
			time.Sleep(5 * time.Millisecond)
			k, err := resp.Body.Read(buf)
			n += k
			if err == io.EOF {
				return n, nil
			}
			if err != nil {
				return n, err
			}
		}
	}

	if n, err := serve(false); err == nil && n == size {
		t.Fatalf("control case did not truncate: got all %d bytes; the test no longer "+
			"exercises WriteTimeout (raise size or lower WriteTimeout)", n)
	} else {
		t.Logf("plain writer: %d/%d bytes, err=%v (expected truncation)", n, size, err)
	}

	n, err := serve(true)
	if err != nil {
		t.Fatalf("longDownload transfer failed after %d/%d bytes: %v", n, size, err)
	}
	if n != size {
		t.Fatalf("longDownload truncated: got %d bytes, want %d", n, size)
	}
}

// TestLongUploadOutlivesReadTimeout is the same argument for request bodies:
// ReadTimeout is absolute, so a slow uploader is cut off mid-body.
func TestLongUploadOutlivesReadTimeout(t *testing.T) {
	const size = 2 << 20

	serve := func(long bool) (int, error) {
		ln, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		got := make(chan int, 1)
		mux := http.NewServeMux()
		mux.HandleFunc("/up", func(w http.ResponseWriter, r *http.Request) {
			if long {
				longUpload(w, r)
			}
			n, err := io.Copy(io.Discard, r.Body)
			got <- int(n)
			if err != nil {
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			w.WriteHeader(http.StatusNoContent)
		})
		srv := &http.Server{Handler: mux, ReadTimeout: 300 * time.Millisecond}
		go srv.Serve(ln)
		defer srv.Close()

		pr, pw := io.Pipe()
		go func() {
			chunk := bytes.Repeat([]byte("y"), 64<<10)
			for sent := 0; sent < size; sent += len(chunk) {
				time.Sleep(20 * time.Millisecond)
				if _, err := pw.Write(chunk); err != nil {
					pw.CloseWithError(err)
					return
				}
			}
			pw.Close()
		}()
		req, _ := http.NewRequest("POST", fmt.Sprintf("http://%s/up", ln.Addr()), pr)
		req.ContentLength = size
		resp, err := http.DefaultTransport.RoundTrip(req)
		n := <-got
		if err != nil {
			return n, err
		}
		resp.Body.Close()
		return n, nil
	}

	if n, _ := serve(false); n == size {
		t.Fatalf("control case did not truncate: server read all %d bytes", n)
	}
	n, err := serve(true)
	if err != nil {
		t.Fatalf("longUpload transfer failed after %d/%d bytes: %v", n, size, err)
	}
	if n != size {
		t.Fatalf("longUpload truncated: server read %d bytes, want %d", n, size)
	}
}
