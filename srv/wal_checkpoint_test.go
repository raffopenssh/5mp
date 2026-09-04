package srv

import (
	"context"
	"path/filepath"
	"testing"
)

// A WAL that grew past walTruncateBytes must be reclaimed by one tick of the
// checkpoint worker even while other pooled connections exist.
func TestCheckpointWALTruncates(t *testing.T) {
	dbPath := filepath.Join(t.TempDir(), "t.sqlite3")
	s, err := New(dbPath, "test")
	if err != nil {
		t.Fatal(err)
	}
	defer s.DB.Close()
	ctx := context.Background()

	// ~300 MiB of WAL: bigger than walTruncateBytes, smaller than the 1 GiB
	// journal_size_limit so that only the explicit TRUNCATE can shrink it.
	if _, err := s.DB.ExecContext(ctx, "CREATE TABLE blob_t(b BLOB)"); err != nil {
		t.Fatal(err)
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 300; i++ {
		if _, err := tx.ExecContext(ctx, "INSERT INTO blob_t VALUES (zeroblob(1048576))"); err != nil {
			t.Fatal(err)
		}
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
	if got := walSize(dbPath); got <= walTruncateBytes {
		t.Fatalf("setup: wal only %d bytes", got)
	}

	s.checkpointWAL(ctx, dbPath)
	if got := walSize(dbPath); got != 0 {
		t.Fatalf("wal after checkpoint = %d bytes, want 0", got)
	}
}
