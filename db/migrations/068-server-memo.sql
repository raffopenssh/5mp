-- 068: server_memo — small derived answers that survive a restart.
--
-- WHY. The parks CSV (/api/export/parks) needs one fire count per park:
-- a GROUP BY over 7.9M inside-boundary detections that takes ~20 s warm and
-- 30–60 s cold. It was memoised in process memory keyed by MAX(rowid) of
-- fire_detections (append-only, so an O(1) fingerprint), which meant the
-- first request after every restart paid the full price. This table keeps
-- the memo across restarts: a row is (key, the fingerprint of its input,
-- the value). A reader compares the fingerprint first; a stale row is served
-- once while one goroutine recomputes (stale-while-revalidate), and a
-- fingerprint that does not match is never presented as current
-- (invariant 5: a derived answer must name its input).
CREATE TABLE IF NOT EXISTS server_memo (
    key         TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    computed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
