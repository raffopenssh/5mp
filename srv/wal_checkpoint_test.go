package srv

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"srv.exe.dev/db"
)

// A *sql.Rows that is neither exhausted nor Closed keeps its pooled
// connection's read snapshot open after the handler returns. That snapshot
// pins the WAL: on 2026-09-10 it held frame 511 while 3.26M frames were
// appended behind it (13 GB). checkpointWAL must get past it by recycling
// idle connections instead of reporting "incomplete" every hour.
func TestCheckpointWALEvictsPinnedIdleReader(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "t.sqlite3")
	d, err := db.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer d.Close()
	if _, err := d.Exec(`CREATE TABLE notifications (id INTEGER PRIMARY KEY, park_id, notification_type, title, message, created_at);
		CREATE TABLE t (id INTEGER PRIMARY KEY, blob BLOB)`); err != nil {
		t.Fatal(err)
	}
	if _, err := d.Exec(`INSERT INTO t (blob) VALUES (zeroblob(1000)), (zeroblob(1000))`); err != nil {
		t.Fatal(err)
	}

	// Reproduce the 2026-09-10 pool state: in_use=0 yet a pooled connection
	// holds an open read transaction (the -shm read mark pinned at frame
	// 511 while mxFrame ran to 3.26M). database/sql hands the connection
	// back to the pool after Conn.Close(); modernc's IsValid only checks
	// sqlite3_is_interrupted, so the open BEGIN survives into the idle pool.
	c, err := d.Conn(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c.ExecContext(context.Background(), `BEGIN`); err != nil {
		t.Fatal(err)
	}
	var n int
	if err := c.QueryRowContext(context.Background(), `SELECT count(*) FROM t`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	c.Close() // back to the pool, snapshot still open
	if st := d.Stats(); st.InUse != 0 {
		t.Fatalf("setup: expected in_use=0, got %+v", st)
	}

	// Write enough to exceed walTruncateBytes so the TRUNCATE path runs.
	for i := 0; i < 300; i++ {
		if _, err := d.Exec(`INSERT INTO t (blob) VALUES (zeroblob(1<<20))`); err != nil {
			t.Fatal(err)
		}
	}
	before := walSize(path)
	if before < walTruncateBytes {
		t.Fatalf("test setup: wal only %d bytes", before)
	}

	s := &Server{DB: d}
	// Direct proof the idle snapshot blocks a plain checkpoint.
	if r := s.runCheckpoint(context.Background(), "TRUNCATE"); !r.stuck() {
		t.Fatalf("expected pinned idle reader to block checkpoint, got %+v", r)
	}
	s.checkpointWAL(context.Background(), path)
	if after := walSize(path); after != 0 {
		t.Fatalf("wal not truncated after recycle: before=%d after=%d marks=%s", before, after, walReadMarks(path))
	}
	if _, err := os.Stat(path + "-wal"); err != nil {
		t.Fatal(err)
	}
}
