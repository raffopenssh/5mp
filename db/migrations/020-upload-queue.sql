-- Upload processing queue for async GPX handling
-- Allows uploads to return immediately while processing happens in background

CREATE TABLE IF NOT EXISTS upload_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'anonymous',
    user_email TEXT NOT NULL DEFAULT 'anonymous',
    filename TEXT NOT NULL,
    file_hash TEXT,
    file_content BLOB NOT NULL,  -- Raw GPX file content
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, processing, completed, failed
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    result_upload_id INTEGER,  -- ID in gpx_uploads table once processed
    result_json TEXT  -- Full response JSON for client
);

CREATE INDEX IF NOT EXISTS idx_upload_queue_status ON upload_queue(status);
CREATE INDEX IF NOT EXISTS idx_upload_queue_hash ON upload_queue(file_hash);
