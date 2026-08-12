package db

import (
	"database/sql"
	"path/filepath"
	"testing"
)

// A FRESH DATABASE MUST MIGRATE. Migrations 015-030 were lost from the repo
// while staying recorded as executed in the live database, so every column they
// added was invisible to a checkout: 035 then failed on `avg_speed_kmh` and
// `New()` could not build a server from an empty file. Nothing in production
// noticed, because production never runs the early migrations again — which is
// exactly why it needs a test.
func TestFreshDatabaseMigrates(t *testing.T) {
	path := filepath.Join(t.TempDir(), "fresh.sqlite3")
	d, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer d.Close()
	if err := RunMigrations(d); err != nil {
		t.Fatalf("fresh migrate: %v", err)
	}
}
