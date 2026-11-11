package srv

// History backfill for an autofetch source: re-fetch what the source's
// EarthRanger still holds, window by window, through the NORMAL upload path
// (upload_queue -> UploadQueueProcessor -> learner), so every track is
// classified and learned by the code that is running now.
//
// It exists because gpx_upload_logs kept only <=300 sampled points per segment
// and upload_queue kept raw GPX for 7 days: a classifier change could not be
// replayed. Every window is therefore also archived gzip'd (autofetchArchive),
// as is every file the 6-hourly worker queues from now on.
//
// Politeness is the first requirement: this talks to a park's production
// EarthRanger. One window at a time, one request at a time, a pause before
// every request (the script's --pause), the roster listed once, 429/5xx
// backed off, and a state file so no window is ever asked for twice.

import (
	"bufio"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"srv.exe.dev/db/dbgen"
)

// AutofetchArchiveDir is where raw autofetch GPX is kept, gzip'd, one
// directory per data env. Ranger tracks: gitignored, files 0600.
const AutofetchArchiveDir = "data/autofetch_archive"

// archiveAutofetchGPX writes content gzip'd under the env's archive directory.
// name is the file's stem (no extension). Idempotent: an existing file with
// the same name is left alone.
func archiveAutofetchGPX(env, name string, content []byte) (string, error) {
	dir := filepath.Join(AutofetchArchiveDir, filepath.Base(env))
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	p := filepath.Join(dir, name+".gpx.gz")
	if _, err := os.Stat(p); err == nil {
		return p, nil
	}
	tmp, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return "", err
	}
	zw := gzip.NewWriter(tmp)
	if _, err = zw.Write(content); err == nil {
		err = zw.Close()
	}
	if cerr := tmp.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(tmp.Name())
		return "", err
	}
	_ = os.Chmod(tmp.Name(), 0o600)
	return p, os.Rename(tmp.Name(), p)
}

// BackfillWindow is one date window of a backfill and what became of it.
type BackfillWindow struct {
	From     string `json:"from"` // RFC3339
	To       string `json:"to"`
	Status   string `json:"status"`        // fetched | queued | done | empty | partial | failed
	Via      string `json:"via,omitempty"` // "earthranger" | "samples"
	Points   int    `json:"points"`
	Subjects int    `json:"subjects,omitempty"`
	Requests int    `json:"requests,omitempty"`
	QueueID  int64  `json:"queue_id,omitempty"`
	Archive  string `json:"archive,omitempty"`
	Error    string `json:"error,omitempty"`
	At       string `json:"at,omitempty"`
}

// BackfillState is the resumable record, one file per source.
type BackfillState struct {
	SourceID int64             `json:"source_id"`
	DataEnv  string            `json:"data_env"`
	Windows  []*BackfillWindow `json:"windows"`
	path     string
}

func LoadBackfillState(path string, sourceID int64) (*BackfillState, error) {
	st := &BackfillState{SourceID: sourceID, path: path}
	b, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return st, nil
	}
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(b, st); err != nil {
		return nil, err
	}
	st.path = path
	if st.SourceID != sourceID {
		return nil, fmt.Errorf("%s belongs to source %d, not %d", path, st.SourceID, sourceID)
	}
	return st, nil
}

func (st *BackfillState) Save() error {
	if err := os.MkdirAll(filepath.Dir(st.path), 0o755); err != nil {
		return err
	}
	sort.Slice(st.Windows, func(i, j int) bool { return st.Windows[i].From < st.Windows[j].From })
	b, _ := json.MarshalIndent(st, "", " ")
	tmp := st.path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, st.path)
}

func (st *BackfillState) window(from, to string) *BackfillWindow {
	for _, w := range st.Windows {
		if w.From == from && w.To == to {
			return w
		}
	}
	w := &BackfillWindow{From: from, To: to}
	st.Windows = append(st.Windows, w)
	return w
}

// BackfillOptions configures RunAutofetchBackfill.
type BackfillOptions struct {
	SourceID   int64
	From, To   time.Time
	WindowDays int
	Pause      float64 // seconds before every EarthRanger request
	StatePath  string
	FetchOnly  bool // archive, do not queue (probe / pre-fetch)
	// ResetCursor: set the source's last_run_at to the end of each processed
	// window even if that moves it BACK (a rebuild from scratch).
	ResetCursor bool
	MaxWindows  int // 0 = all
	Log         func(format string, a ...any)
}

// RunAutofetchBackfill walks [From, To) oldest-first. Each window is fetched
// (once: an archived window is re-read from disk, never from EarthRanger),
// queued under the source's data env, and WAITED for -- so the learner sees
// history in time order and the SQLite writer is only ever held by one
// upload at a time. It stops at the first partial or failed window rather
// than leaving a hole behind an advancing cursor (AGENTS.md invariant 1).
func RunAutofetchBackfill(ctx context.Context, db *sql.DB, o BackfillOptions) (*BackfillState, error) {
	logf := o.Log
	if logf == nil {
		logf = func(string, ...any) {}
	}
	var serviceURL, username, enc, dataEnv string
	if err := db.QueryRowContext(ctx,
		`SELECT service_url, username, password, data_env FROM autofetch_sources WHERE id = ?`, o.SourceID,
	).Scan(&serviceURL, &username, &enc, &dataEnv); err != nil {
		return nil, fmt.Errorf("autofetch source %d: %w", o.SourceID, err)
	}
	if dataEnv == "" {
		return nil, fmt.Errorf("source %d has no data env", o.SourceID)
	}
	if enc == "" {
		return nil, fmt.Errorf("source %d has no stored credential (paused?)", o.SourceID)
	}
	password, err := decryptPassword(enc)
	if err != nil {
		return nil, fmt.Errorf("credential: %w", err)
	}
	st, err := LoadBackfillState(o.StatePath, o.SourceID)
	if err != nil {
		return nil, err
	}
	st.DataEnv = dataEnv
	if err := os.MkdirAll(filepath.Dir(o.StatePath), 0o755); err != nil {
		return nil, err
	}
	if o.WindowDays <= 0 {
		o.WindowDays = 7
	}
	roster := filepath.Join(filepath.Dir(o.StatePath), fmt.Sprintf("roster-%d.json", o.SourceID))
	if fi, err := os.Stat(roster); err == nil && time.Since(fi.ModTime()) > 12*time.Hour {
		os.Remove(roster) // last_position_date in it decides which subjects are skipped
	}
	srvShim := &Server{DB: db}
	q := dbgen.New(db)
	done := 0
	for lo := o.From; lo.Before(o.To); lo = lo.AddDate(0, 0, o.WindowDays) {
		if err := ctx.Err(); err != nil {
			return st, err
		}
		hi := lo.AddDate(0, 0, o.WindowDays)
		if hi.After(o.To) {
			hi = o.To
		}
		w := st.window(lo.UTC().Format(time.RFC3339), hi.UTC().Format(time.RFC3339))
		if w.Status == "done" || w.Status == "empty" || (o.FetchOnly && w.Status == "fetched") {
			continue
		}
		if o.MaxWindows > 0 && done >= o.MaxWindows {
			break
		}
		done++
		// Keep the 6-hourly worker from running this source underneath us: it
		// is due on last_attempt_at.
		_, _ = db.ExecContext(ctx, `UPDATE autofetch_sources SET last_attempt_at = CURRENT_TIMESTAMP WHERE id = ?`, o.SourceID)

		stem := "backfill-" + lo.UTC().Format("20060102") + "-" + hi.UTC().Format("20060102T1504")
		arch := filepath.Join(AutofetchArchiveDir, dataEnv, stem+".gpx.gz")
		keysPath := filepath.Join(filepath.Dir(o.StatePath), stem+".keys")
		var content []byte
		if w.Status == "fetched" || w.Status == "queued" {
			content, err = readGzip(arch)
			if err != nil {
				w.Status = "" // archive gone: fetch again
			}
		}
		if w.Status != "fetched" && w.Status != "queued" {
			out, err := os.CreateTemp("", "backfill-*.gpx")
			if err != nil {
				return st, err
			}
			out.Close()
			defer os.Remove(out.Name())
			cmd := exec.CommandContext(ctx, "python3", "scripts/fetch_earthranger_gpx.py",
				"--url", serviceURL, "--user", username, "--out", out.Name(),
				"--since", w.From, "--until", w.To,
				"--pause", fmt.Sprint(o.Pause), "--roster-cache", roster, "--new-keys", keysPath)
			cmd.Env = append(os.Environ(), "EARTHRANGER_PASSWORD="+password)
			raw, _ := cmd.CombinedOutput()
			res, perr := parseAutofetchOutput(raw)
			w.At = time.Now().UTC().Format(time.RFC3339)
			if perr != nil || !res.OK {
				w.Status, w.Error = "failed", res.Error
				if perr != nil {
					w.Error = perr.Error()
				}
				_ = st.Save()
				return st, fmt.Errorf("window %s: %s", w.From, w.Error)
			}
			var extra struct {
				Requests int `json:"requests"`
			}
			if i := strings.LastIndex(string(raw), "\n{"); i >= 0 {
				_ = json.Unmarshal([]byte(strings.TrimSpace(string(raw)[i:])), &extra)
			} else {
				_ = json.Unmarshal([]byte(strings.TrimSpace(string(raw))), &extra)
			}
			w.Points, w.Subjects, w.Requests, w.Via, w.Error = res.Points, res.Subjects, extra.Requests, "earthranger", ""
			if res.Partial {
				// Some subject fetches failed: a hole. Do not queue half a
				// week and call it the week.
				w.Status, w.Error = "partial", fmt.Sprintf("%d subject fetches failed", res.Errors)
				_ = st.Save()
				return st, fmt.Errorf("window %s: %s (re-run to retry)", w.From, w.Error)
			}
			if res.Points == 0 {
				w.Status = "empty"
				logf("%s .. %s  empty (%d requests)", w.From[:10], w.To[:10], w.Requests)
				if err := st.Save(); err != nil {
					return st, err
				}
				continue
			}
			content, err = os.ReadFile(out.Name())
			if err != nil {
				return st, err
			}
			if w.Archive, err = archiveAutofetchGPX(dataEnv, stem, content); err != nil {
				return st, err
			}
			w.Status = "fetched"
			if err := st.Save(); err != nil {
				return st, err
			}
			logf("%s .. %s  fetched %d points, %d subjects, %d requests", w.From[:10], w.To[:10], w.Points, w.Subjects, w.Requests)
		}
		if o.FetchOnly {
			continue
		}
		if w.Status == "fetched" {
			sum := sha256.Sum256(content)
			hash := hex.EncodeToString(sum[:])
			if ex, err := q.GetUploadQueueByHash(ctx, dbgen.GetUploadQueueByHashParams{FileHash: &hash, Env: dataEnv}); err == nil && ex.ID > 0 {
				w.QueueID = ex.ID
			} else {
				row, err := q.QueueUpload(ctx, dbgen.QueueUploadParams{
					UserID: "autofetch", Filename: "autofetch-" + lo.UTC().Format("20060102") + ".gpx",
					FileHash: &hash, FileContent: content, Env: dataEnv,
				})
				if err != nil {
					return st, fmt.Errorf("queue window %s: %w", w.From, err)
				}
				w.QueueID = row.ID
			}
			w.Status = "queued"
			if err := st.Save(); err != nil {
				return st, err
			}
		}
		// Wait for the server to process it, and for the learner to drain.
		if err := waitUploadProcessed(ctx, db, w.QueueID); err != nil {
			w.Status, w.Error = "failed", err.Error()
			_ = st.Save()
			return st, fmt.Errorf("window %s: %w", w.From, err)
		}
		// Ledger: the 6-hourly run must not re-import what this just did.
		if cutoff := time.Now().Add(-autofetchLedgerKeep); hi.After(cutoff) {
			if n, err := srvShim.recordAutofetchSeen(ctx, o.SourceID, keysPath); err != nil {
				logf("  ledger incomplete (%d recorded): %v", n, err)
			}
		}
		os.Remove(keysPath)
		w.Status, w.At = "done", time.Now().UTC().Format(time.RFC3339)
		if err := st.Save(); err != nil {
			return st, err
		}
		logf("%s .. %s  processed (queue %d)", w.From[:10], w.To[:10], w.QueueID)
		// The 6-hourly worker resumes from where this window's data ends:
		// its fetch time (the archive's mtime) if the window was still open
		// when it was fetched, else the window's end.
		resume := hi
		if fi, err := os.Stat(arch); err == nil && fi.ModTime().Before(hi) {
			resume = fi.ModTime()
		}
		_, _ = db.ExecContext(ctx, `UPDATE autofetch_sources SET last_run_at = ? WHERE id = ? AND (last_run_at IS NULL OR last_run_at < ? OR ?)`,
			resume.UTC().Format("2006-01-02 15:04:05"), o.SourceID, resume.UTC().Format("2006-01-02 15:04:05"), o.ResetCursor)
	}
	return st, nil
}

func readGzip(path string) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	zr, err := gzip.NewReader(bufio.NewReader(f))
	if err != nil {
		return nil, err
	}
	var buf strings.Builder
	if _, err := bufio.NewReader(zr).WriteTo(&buf); err != nil {
		return nil, err
	}
	return []byte(buf.String()), nil
}

// waitUploadProcessed blocks until the queue row is completed and no learning
// job is pending or running. A failed row is an error; a server that is not
// running shows as a row that never leaves 'pending' and times out.
func waitUploadProcessed(ctx context.Context, db *sql.DB, queueID int64) error {
	deadline := time.Now().Add(45 * time.Minute)
	for time.Now().Before(deadline) {
		var status string
		var msg sql.NullString
		if err := db.QueryRowContext(ctx, `SELECT status, error_message FROM upload_queue WHERE id = ?`, queueID).Scan(&status, &msg); err != nil {
			return err
		}
		if status == "failed" {
			return fmt.Errorf("upload %d failed: %s", queueID, msg.String)
		}
		if status == "completed" {
			var busy int
			_ = db.QueryRowContext(ctx, `SELECT COUNT(*) FROM gpx_learning_queue WHERE status IN ('pending','processing')`).Scan(&busy)
			if busy == 0 {
				return nil
			}
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(3 * time.Second):
		}
	}
	return fmt.Errorf("upload %d not processed after 45 min (is the 5mp service running?)", queueID)
}
