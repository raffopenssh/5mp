package srv

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"time"
)

// walCheckpointInterval is how often the server forces a WAL checkpoint.
// The nightly cron chain (03:00 fire → 07:30 refresh) is the heavy writer;
// hourly means the WAL is reclaimed within an hour of each job finishing
// instead of accumulating until something happens to let autocheckpoint run.
const walCheckpointInterval = time.Hour

// walTruncateBytes is the WAL size above which the hourly checkpoint
// escalates from PASSIVE to TRUNCATE. PASSIVE never blocks anyone but also
// never shrinks the file; TRUNCATE holds the writer lock while it waits (up
// to busy_timeout) for readers to drain, so it is only worth paying for when
// there is something to reclaim. 256 MiB ≈ 65k pages: a normal hour of
// server writes stays under it, one nightly job does not.
const walTruncateBytes = 256 << 20

// walWarnBytes is the WAL size above which a checkpoint that could not
// complete is logged as an error and reported into the notification bell.
// The database is ~23 GB; a healthy WAL after a checkpoint is well under
// journal_size_limit (1 GiB, db/db.go). 4 GiB means checkpoints have been
// failing for several nightly runs, not one.
const walWarnBytes = 4 << 30

// StartWALCheckpointWorker forces a WAL checkpoint every hour and truncates
// the -wal file. Why the server has to do this rather than trusting SQLite:
// autocheckpoint is PASSIVE — it stops at the first frame any open reader
// still needs, and this process holds 16 pooled connections serving
// long-running queries (fire heat field, exports) around the clock. Once a
// checkpoint is blocked the WAL only grows; on 2026-09-04 it reached 28.9 GB
// against a 22.7 GB database and took the VM to 95% disk. A RESTART
// checkpoint waits (up to busy_timeout) for readers to finish and then
// prevents new ones from reading the old WAL, so the following TRUNCATE
// actually reclaims the file.
//
// A checkpoint that cannot complete is reported, not swallowed (invariant 1:
// a no-op must not read as success).
func (s *Server) StartWALCheckpointWorker(ctx context.Context, dbPath string) {
	ticker := time.NewTicker(walCheckpointInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.checkpointWAL(ctx, dbPath)
		}
	}
}

// checkpointWAL runs one checkpoint (PASSIVE, or TRUNCATE once the WAL is
// worth reclaiming) and reports the outcome.
func (s *Server) checkpointWAL(ctx context.Context, dbPath string) {
	before := walSize(dbPath)
	mode := "PASSIVE"
	if before > walTruncateBytes {
		mode = "TRUNCATE"
	}
	start := time.Now()
	// PRAGMA wal_checkpoint returns (busy, log, checkpointed): busy=1 means
	// it could not finish because of a reader; log/checkpointed are frame
	// counts (−1 when not in WAL mode).
	var busy, logFrames, ckptFrames int64
	err := s.DB.QueryRowContext(ctx, "PRAGMA wal_checkpoint("+mode+")").Scan(&busy, &logFrames, &ckptFrames)
	after := walSize(dbPath)
	took := time.Since(start).Round(time.Millisecond)
	switch {
	case err != nil:
		slog.Error("wal checkpoint failed", "error", err, "wal_bytes", after)
	case mode == "PASSIVE" && after <= walWarnBytes:
		// PASSIVE routinely stops short when readers are active; that is
		// only a problem once the file is large, and the next tick will
		// escalate. Log at debug so an idle hour is not noise.
		slog.Debug("wal checkpoint", "mode", mode, "busy", busy, "wal_bytes", after, "took", took)
	case busy != 0 || after > walWarnBytes:
		slog.Error("wal checkpoint incomplete", "mode", mode, "busy", busy, "wal_frames", logFrames,
			"checkpointed", ckptFrames, "wal_bytes_before", before, "wal_bytes_after", after, "took", took)
		if after > walWarnBytes {
			s.notifyWALStuck(ctx, after)
		}
	default:
		slog.Info("wal checkpoint", "mode", mode, "wal_frames", logFrames, "wal_bytes_before", before,
			"wal_bytes_after", after, "took", took)
	}
}

// notifyWALStuck writes a cron_status-shaped notification so the stuck WAL
// shows in the bell like a failed nightly job (docs/agents/ops.md, "Cron jobs
// report into the notification bell — by SHAPE"). One per day at most.
func (s *Server) notifyWALStuck(ctx context.Context, walBytes int64) {
	var recent int
	_ = s.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM notifications
		WHERE notification_type='wal_checkpoint_failed' AND created_at > datetime('now','-1 day')`).Scan(&recent)
	if recent > 0 {
		return
	}
	_, err := s.DB.ExecContext(ctx, `INSERT INTO notifications
		(park_id, notification_type, title, message, created_at)
		VALUES ('SYSTEM', 'wal_checkpoint_failed', 'Database WAL not being reclaimed', ?, datetime('now'))`,
		fmtBytesGB(walBytes)+" in db.sqlite3-wal after a forced checkpoint; a long-lived reader is blocking it. "+
			"Check `sudo journalctl -u 5mp | grep 'wal checkpoint'` and disk usage.")
	if err != nil {
		slog.Error("wal notification failed", "error", err)
	}
}

func walSize(dbPath string) int64 {
	st, err := os.Stat(dbPath + "-wal")
	if err != nil {
		return 0
	}
	return st.Size()
}

func fmtBytesGB(b int64) string {
	return fmt.Sprintf("%.1f GB", float64(b)/(1<<30))
}
