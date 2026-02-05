-- Add file_hash column to gpx_uploads for deduplication (idempotent)
-- SQLite doesn't support IF NOT EXISTS for columns

-- Add file_hash column if it doesn't exist (this will fail silently if it already exists)
ALTER TABLE gpx_uploads ADD COLUMN file_hash TEXT;

-- Create index for fast hash lookups (idempotent)
CREATE INDEX IF NOT EXISTS idx_gpx_uploads_file_hash ON gpx_uploads(file_hash);
