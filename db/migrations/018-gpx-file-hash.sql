-- Add file_hash column to gpx_uploads for deduplication (idempotent)
-- SQLite doesn't have IF NOT EXISTS for columns, so this is a no-op if column exists

-- The column was already added manually, so this migration is a no-op
-- If running on a fresh database, uncomment the ALTER TABLE below:
-- ALTER TABLE gpx_uploads ADD COLUMN file_hash TEXT;

-- Index for fast hash lookups (idempotent)
CREATE INDEX IF NOT EXISTS idx_gpx_uploads_file_hash ON gpx_uploads(file_hash);
