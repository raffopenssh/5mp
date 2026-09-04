package db

import (
	"path/filepath"
	"sync"
	"testing"
)

// Every pooled connection must carry the pragmas, not just the first one.
// Until 2026-09-04 they were applied with db.Exec, which reaches exactly one
// connection; the other 15 ran with busy_timeout=0 and no journal_size_limit.
func TestOpenPragmasOnEveryConnection(t *testing.T) {
	d, err := Open(filepath.Join(t.TempDir(), "t.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()

	// Hold several connections open at once so the pool must create fresh ones.
	const n = 8
	var wg sync.WaitGroup
	errs := make(chan error, n)
	start := make(chan struct{})
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			conn, err := d.Conn(t.Context())
			if err != nil {
				errs <- err
				return
			}
			defer conn.Close()
			<-start
			var busy, limit int64
			var mode string
			if err := conn.QueryRowContext(t.Context(), "PRAGMA busy_timeout").Scan(&busy); err != nil {
				errs <- err
				return
			}
			if err := conn.QueryRowContext(t.Context(), "PRAGMA journal_size_limit").Scan(&limit); err != nil {
				errs <- err
				return
			}
			if err := conn.QueryRowContext(t.Context(), "PRAGMA journal_mode").Scan(&mode); err != nil {
				errs <- err
				return
			}
			if busy != 30000 || limit != 1<<30 || mode != "wal" {
				t.Errorf("connection pragmas: busy_timeout=%d journal_size_limit=%d journal_mode=%s", busy, limit, mode)
			}
		}()
	}
	// Each goroutine holds its connection until all are released, so the
	// pool cannot satisfy them with one recycled connection.
	close(start)
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Fatal(err)
	}
}
