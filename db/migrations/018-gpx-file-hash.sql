-- Add file_hash column to gpx_uploads for deduplication (idempotent)
-- SQLite doesn't support IF NOT EXISTS for columns, so we use a different approach

-- Check if file_hash column exists, add if not
SELECT CASE WHEN (SELECT COUNT(*) FROM pragma_table_info('gpx_uploads') WHERE name='file_hash') = 0 
THEN 'ALTER TABLE gpx_uploads ADD COLUMN file_hash TEXT' 
END;

-- Create index for fast hash lookups (idempotent)
CREATE INDEX IF NOT EXISTS idx_gpx_uploads_file_hash ON gpx_uploads(file_hash);
