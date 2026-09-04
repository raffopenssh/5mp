package db

import (
	"database/sql"
	"embed"
	"errors"
	"fmt"
	"log/slog"
	"regexp"
	"sort"
	"strconv"

	_ "modernc.org/sqlite"
)

//go:generate go tool github.com/sqlc-dev/sqlc/cmd/sqlc generate

//go:embed migrations/*.sql
var migrationFS embed.FS

// Open opens an sqlite database and prepares pragmas suitable for a small web app.
func Open(path string) (*sql.DB, error) {
	// Pragmas go in the DSN, not through db.Exec: database/sql hands an Exec
	// to *one* pooled connection, so until 2026-09-04 only 1 of the 16
	// connections had busy_timeout/foreign_keys set and the other 15 ran with
	// the driver defaults (busy_timeout 0 → instant SQLITE_BUSY under the
	// nightly batch writers). modernc's `_pragma=` query parameters run on
	// every new connection.
	//
	//   busy_timeout=30000 — 30 s, not 5. Every write on this deployment
	//   competes with batch jobs that hold SQLite's single writer for minutes
	//   (the v5 fire chain, the AOI Hansen unit, the nightly rebuilds), and at
	//   5 s a user-initiated write during one of those failed outright. 30 s
	//   absorbs the gaps those jobs leave between their own commits; anything
	//   still busy after it surfaces as a 503 with Retry-After
	//   (srv/errors.go isDBLocked), which is the honest answer rather than a 500.
	//
	//   journal_size_limit=1 GiB — cap the WAL file after a checkpoint. SQLite
	//   never shrinks the -wal on its own: autocheckpoint (1000 pages) cannot
	//   pass a snapshot any open reader still holds, and once one is blocked
	//   every nightly job (fire update, reclassify, GFW, cropland, OSM enrich)
	//   appends behind it. On 2026-09-04 the -wal reached 28.9 GB against a
	//   22.7 GB database and took the VM to 95% disk. With this limit the next
	//   successful checkpoint truncates it; srv.StartWALCheckpointWorker forces
	//   that checkpoint periodically and reports when it cannot.
	dsn := "file:" + path +
		"?_pragma=foreign_keys(ON)" +
		"&_pragma=journal_mode(WAL)" +
		"&_pragma=busy_timeout(30000)" +
		"&_pragma=journal_size_limit(1073741824)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	if err := db.Ping(); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("open %s: %w", path, err)
	}
	// WAL allows many concurrent readers; writers serialize via busy_timeout.
	// Keep this comfortably above the worst-case nested-query fan-out: with
	// only 4 conns, handlers that ran queries while iterating rows deadlocked
	// the whole pool under concurrent load (star-report multi-park export).
	db.SetMaxOpenConns(16)
	db.SetMaxIdleConns(4)
	return db, nil
}

// RunMigrations executes database migrations in numeric order (NNN-*.sql),
// similar in spirit to exed's exedb.RunMigrations.
func RunMigrations(db *sql.DB) error {
	entries, err := migrationFS.ReadDir("migrations")
	if err != nil {
		return fmt.Errorf("read migrations dir: %w", err)
	}
	var migrations []string
	pat := regexp.MustCompile(`^(\d{3})-.*\.sql$`)
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		name := e.Name()
		if pat.MatchString(name) {
			migrations = append(migrations, name)
		}
	}
	sort.Strings(migrations)

	executed := make(map[int]bool)
	var tableName string
	err = db.QueryRow("SELECT name FROM sqlite_master WHERE type='table' AND name='migrations'").Scan(&tableName)
	switch {
	case err == nil:
		rows, err := db.Query("SELECT migration_number FROM migrations")
		if err != nil {
			return fmt.Errorf("query executed migrations: %w", err)
		}
		defer rows.Close()
		for rows.Next() {
			var n int
			if err := rows.Scan(&n); err != nil {
				return fmt.Errorf("scan migration number: %w", err)
			}
			executed[n] = true
		}
	case errors.Is(err, sql.ErrNoRows):
		slog.Info("db: migrations table not found; running all migrations")
	default:
		return fmt.Errorf("check migrations table: %w", err)
	}

	for _, m := range migrations {
		match := pat.FindStringSubmatch(m)
		if len(match) != 2 {
			return fmt.Errorf("invalid migration filename: %s", m)
		}
		n, err := strconv.Atoi(match[1])
		if err != nil {
			return fmt.Errorf("parse migration number %s: %w", m, err)
		}
		if executed[n] {
			continue
		}
		if err := executeMigration(db, m, n); err != nil {
			return fmt.Errorf("execute %s: %w", m, err)
		}
		slog.Info("db: applied migration", "file", m, "number", n)
	}
	return nil
}

func executeMigration(db *sql.DB, filename string, migrationNum int) error {
	content, err := migrationFS.ReadFile("migrations/" + filename)
	if err != nil {
		return fmt.Errorf("read %s: %w", filename, err)
	}
	if _, err := db.Exec(string(content)); err != nil {
		return fmt.Errorf("exec %s: %w", filename, err)
	}
	// Record successful migration (ignore errors if migrations table
	// was just created by this very migration, e.g. 001-base).
	_, _ = db.Exec(
		"INSERT OR IGNORE INTO migrations (migration_number, migration_name) VALUES (?, ?)",
		migrationNum, filename,
	)
	return nil
}
