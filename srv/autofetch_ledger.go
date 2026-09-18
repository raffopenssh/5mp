package srv

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ── Exactly-once ingestion ───────────────────────────────────────────────────
//
// effort_data is additive, so a point imported twice is counted twice. The
// fetch window therefore cannot rely on time alone: autofetch_seen (migration
// 070) remembers every (subject, recorded_at) key a source has queued, the
// worker hands the script the keys inside the window (--seen) and the script
// reports the keys it kept (--new-keys). Both sides hash identically:
// sha256("<subject_id>|<recorded_at RFC3339 Z>")[:16] as hex. The ledger is
// bounded: rows older than autofetchLedgerKeep are pruned each run.

const (
	// autofetchOverlap is how far behind the high-water mark every run looks.
	// It is the late-sync tolerance: a device that uploads N hours after
	// recording is still ingested if N < autofetchOverlap. Duplicates inside
	// the overlap are removed by the ledger, so widening it costs only ER
	// request volume, never a double count.
	autofetchOverlap = 72 * time.Hour
	// autofetchLegacyOverlap is the pre-070 overlap, used while the ledger
	// cannot yet vouch for a wider window.
	autofetchLegacyOverlap = 30 * time.Minute
	// autofetchPartialGiveUp: consecutive partial runs after which the
	// high-water mark advances past the failing subjects.
	autofetchPartialGiveUp = 3
	// autofetchLedgerKeep must exceed autofetchOverlap: a key the window can
	// still see must still be in the ledger.
	autofetchLedgerKeep = 30 * 24 * time.Hour
	// autofetchRunTimeout bounds one script run; a hung ER endpoint must not
	// pin the worker (and its ER session) forever.
	autofetchRunTimeout = 45 * time.Minute
	// autofetchBackoffAfter: consecutive failures after which the retry
	// spacing grows (doubling per further failure, capped at
	// autofetchBackoffMax) so a source with a bad password or a dead host is
	// not retried at line rate against the ER account. The credential is
	// kept: the owner fixes it once, the next attempt succeeds, the streak
	// resets.
	autofetchBackoffAfter = 3
	autofetchBackoffMax   = 24 * time.Hour
)

// autofetchBackoff is the wait before the next attempt given the source's
// interval and its current failure streak.
func autofetchBackoff(interval time.Duration, failures int) time.Duration {
	if failures < autofetchBackoffAfter {
		return interval
	}
	d := interval
	for i := autofetchBackoffAfter; i <= failures && d < autofetchBackoffMax; i++ {
		d *= 2
	}
	if d > autofetchBackoffMax {
		d = autofetchBackoffMax
	}
	return d
}

// autofetchRunning is the per-source run lock: the scheduler and "Fetch now"
// must never fetch one source concurrently (two files, two imports).
var autofetchRunning sync.Map // id -> struct{}

func autofetchTryLock(id int64) bool {
	_, loaded := autofetchRunning.LoadOrStore(id, struct{}{})
	return !loaded
}

func autofetchUnlock(id int64) { autofetchRunning.Delete(id) }

// autofetchKeyHash is the Go side of the shared key function (kept in step
// with fetch_earthranger_gpx.py:point_key; TestAutofetchKeyHashParity pins it).
func autofetchKeyHash(subjectID, recordedAt string) int64 {
	sum := sha256.Sum256([]byte(subjectID + "|" + recordedAt))
	return autofetchHexKey(hex.EncodeToString(sum[:8]))
}

// autofetchHexKey parses the 16-hex-char key into the int64 the ledger stores.
func autofetchHexKey(h string) int64 {
	u, err := strconv.ParseUint(h, 16, 64)
	if err != nil {
		return 0
	}
	return int64(u)
}

// writeAutofetchSeen writes the ledger keys recorded at or after `since` to a
// temp file (one 16-hex-char key per line) and returns its path and count.
func (s *Server) writeAutofetchSeen(ctx context.Context, sourceID int64, since time.Time) (string, int, error) {
	f, err := os.CreateTemp("", "autofetch-seen-*.txt")
	if err != nil {
		return "", 0, err
	}
	defer f.Close()
	rows, err := s.DB.QueryContext(ctx,
		`SELECT key_hash FROM autofetch_seen WHERE source_id = ? AND recorded_at >= ?`,
		sourceID, since.UTC().Format(time.RFC3339))
	if err != nil {
		os.Remove(f.Name())
		return "", 0, err
	}
	defer rows.Close()
	w := bufio.NewWriter(f)
	n := 0
	for rows.Next() {
		var k int64
		if rows.Scan(&k) != nil {
			continue
		}
		fmt.Fprintf(w, "%016x\n", uint64(k))
		n++
	}
	if err := w.Flush(); err != nil {
		os.Remove(f.Name())
		return "", 0, err
	}
	return f.Name(), n, nil
}

// recordAutofetchSeen ingests the script's --new-keys file
// ("<hex16>\t<recorded_at>" per line) into the ledger, batched so the write
// lock is yielded (AGENTS.md invariant 16), then prunes rows older than
// autofetchLedgerKeep for this source only.
func (s *Server) recordAutofetchSeen(ctx context.Context, sourceID int64, path string) (int, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1<<20)
	const batch = 2000
	type kv struct {
		k  int64
		ts string
	}
	var buf []kv
	total := 0
	flush := func() error {
		if len(buf) == 0 {
			return nil
		}
		tx, err := s.DB.BeginTx(ctx, nil)
		if err != nil {
			return err
		}
		stmt, err := tx.PrepareContext(ctx,
			`INSERT OR IGNORE INTO autofetch_seen (source_id, key_hash, recorded_at) VALUES (?, ?, ?)`)
		if err != nil {
			tx.Rollback()
			return err
		}
		for _, e := range buf {
			if _, err := stmt.ExecContext(ctx, sourceID, e.k, e.ts); err != nil {
				stmt.Close()
				tx.Rollback()
				return err
			}
		}
		stmt.Close()
		if err := tx.Commit(); err != nil {
			return err
		}
		total += len(buf)
		buf = buf[:0]
		return nil
	}
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		h, ts, ok := strings.Cut(line, "\t")
		if !ok || len(h) != 16 {
			continue
		}
		k := autofetchHexKey(h)
		if k == 0 && h != "0000000000000000" {
			continue
		}
		buf = append(buf, kv{k: k, ts: ts})
		if len(buf) >= batch {
			if err := flush(); err != nil {
				return total, err
			}
		}
	}
	if err := flush(); err != nil {
		return total, err
	}
	_, _ = s.DB.ExecContext(ctx,
		`DELETE FROM autofetch_seen WHERE source_id = ? AND recorded_at < ?`,
		sourceID, time.Now().Add(-autofetchLedgerKeep).UTC().Format(time.RFC3339))
	return total, nil
}

// ── Service URL hygiene ──────────────────────────────────────────────────────

// autofetchHostAllowed refuses a service_url whose host resolves to a
// loopback, private, link-local or unspecified address. The server POSTs the
// user's ER credentials to that URL and later runs a script against it; a
// name that points inside this network is a request to talk to ourselves
// with someone's password, and there is no ER instance there.
func autofetchHostAllowed(host string) error {
	h := host
	if hp, _, err := net.SplitHostPort(host); err == nil {
		h = hp
	}
	if h == "" {
		return fmt.Errorf("empty host")
	}
	if strings.EqualFold(h, "localhost") || strings.HasSuffix(strings.ToLower(h), ".localhost") {
		return fmt.Errorf("%s is not a public host", h)
	}
	ips, err := net.LookupIP(h)
	if err != nil {
		return fmt.Errorf("cannot resolve %s", h)
	}
	if len(ips) == 0 {
		return fmt.Errorf("%s does not resolve", h)
	}
	for _, ip := range ips {
		if ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() ||
			ip.IsUnspecified() || ip.IsMulticast() {
			return fmt.Errorf("%s resolves to a non-public address", h)
		}
	}
	return nil
}
