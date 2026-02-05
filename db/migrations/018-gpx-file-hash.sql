-- Add file_hash and processing_status columns to gpx_uploads for deduplication and tracking
-- SQLite doesn't have IF NOT EXISTS for columns, using CREATE TABLE to define schema

ALTER TABLE gpx_uploads ADD COLUMN file_hash TEXT;
ALTER TABLE gpx_uploads ADD COLUMN processing_status TEXT DEFAULT 'pending';
ALTER TABLE gpx_uploads ADD COLUMN error_message TEXT;

-- Index for fast hash lookups (idempotent)
CREATE INDEX IF NOT EXISTS idx_gpx_uploads_file_hash ON gpx_uploads(file_hash);
