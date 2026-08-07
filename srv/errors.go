package srv

import (
	"context"
	"database/sql"
	"log/slog"
	"net/http"
	"strings"
	"time"
)

// internalError logs the real error and returns a generic message to the client.
func internalError(w http.ResponseWriter, msg string, err error) {
	slog.Error(msg, "error", err)
	http.Error(w, "Internal server error", http.StatusInternalServerError)
}

// isDBLocked matches on the message rather than an error type because the
// driver (modernc.org/sqlite) reports SQLITE_BUSY as a plain formatted error,
// and it reaches us wrapped by database/sql either way.
func isDBLocked(err error) bool {
	if err == nil {
		return false
	}
	s := err.Error()
	return strings.Contains(s, "database is locked") ||
		strings.Contains(s, "SQLITE_BUSY") ||
		strings.Contains(s, "database table is locked")
}

// execUserToggle runs a small user-initiated write that must not be allowed to
// fail just because a batch job is holding SQLite's single writer.
//
// The problem is specific to this deployment. Writes like "hide this area" or
// "stop fetching" are one-row UPDATEs on a table with a handful of rows, but
// they compete with the v5 fire chain, the AOI Hansen unit and the nightly
// rebuilds, each of which holds the write lock for minutes. The user is
// flipping a switch; "Internal server error" (or even an honest 503) is the
// wrong answer to that, because there is nothing for them to do differently
// and the switch will work perfectly ten seconds later.
//
// So we wait it out rather than reporting it. Batch writers commit in a stream
// of short transactions rather than one enormous one, so what is needed is
// persistence through the gaps, not a longer single timeout: db.Open's
// busy_timeout gives each attempt its own wait, and this retries around that
// until the request's own deadline. Safe to retry blindly because every caller
// is idempotent — setting state='archived' twice is setting it once.
//
// It deliberately does NOT wrap the heavy handlers (create, edit, delete): those
// write many rows across several tables and belong in a transaction whose
// failure the user does want to hear about.
func execUserToggle(ctx context.Context, db *sql.DB, query string, args ...any) (sql.Result, error) {
	// ~40 s of real patience. Longer than any gap between a batch job's
	// commits, shorter than a user's patience with a spinner.
	deadline := time.Now().Add(40 * time.Second)
	var res sql.Result
	var err error
	for attempt := 0; ; attempt++ {
		res, err = db.ExecContext(ctx, query, args...)
		if err == nil || !isDBLocked(err) || time.Now().After(deadline) {
			return res, err
		}
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		slog.Debug("user toggle waiting for write lock", "attempt", attempt)
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(250 * time.Millisecond):
		}
	}
}
