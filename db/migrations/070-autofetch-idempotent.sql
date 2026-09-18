-- Exactly-once ingestion for automated fetch (EarthRanger).
--
-- Every run fetched [last_run_at - 30 min, now] and effort_data is ADDITIVE
-- (UpsertEffortData sums), so the 30-minute overlap was imported twice on
-- every run: 5.5% of af12's stored track points were cross-upload duplicates
-- on 2026-09-18. File-hash de-duplication cannot catch it (the GPX carries
-- the run's own timestamp). And the overlap was too short for the failure it
-- was meant to absorb -- a ranger device that syncs hours or days late has
-- recorded_at before `since` and is never fetched at all.
--
-- autofetch_seen is a per-source ledger of (subject, recorded_at) keys the
-- worker has already queued: the script drops what the ledger knows, so the
-- window can be wide (autofetchOverlap) without counting anything twice.
-- Rows older than the widest window are pruned by the worker; the table is
-- bounded, not a history.
CREATE TABLE IF NOT EXISTS autofetch_seen (
    source_id   INTEGER NOT NULL,
    key_hash    INTEGER NOT NULL,          -- int64 of sha256(subject_id|recorded_at)[:16]
    recorded_at TEXT    NOT NULL,          -- RFC3339 UTC, for pruning and windowing
    PRIMARY KEY (source_id, key_hash)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_autofetch_seen_time ON autofetch_seen(source_id, recorded_at);

-- last_run_at is the ingestion high-water mark and must only advance on a
-- complete, successful run -- it used to advance on every attempt, so a
-- failed fetch froze a gap (AGENTS.md invariant 1). Scheduling uses
-- last_attempt_at instead, so a failing source retries at its interval
-- rather than every scheduler tick.
ALTER TABLE autofetch_sources ADD COLUMN last_attempt_at TIMESTAMP;
ALTER TABLE autofetch_sources ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0;
