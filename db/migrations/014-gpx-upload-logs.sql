-- GPX Upload Processing Logs
-- Stores detailed processing results for admin review

CREATE TABLE IF NOT EXISTS gpx_upload_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER REFERENCES gpx_uploads(id),
    user_id TEXT NOT NULL,
    user_email TEXT,
    filename TEXT NOT NULL,
    upload_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    -- Validation results
    is_valid BOOLEAN NOT NULL DEFAULT 1,
    total_points INTEGER NOT NULL DEFAULT 0,
    validation_errors TEXT, -- JSON array
    validation_warnings TEXT, -- JSON array
    
    -- Protected area detection
    protected_area_id TEXT,
    protected_area_name TEXT,
    
    -- Classification stats (in km)
    patrol_km REAL NOT NULL DEFAULT 0,
    road_km REAL NOT NULL DEFAULT 0,
    boundary_km REAL NOT NULL DEFAULT 0,
    excluded_km REAL NOT NULL DEFAULT 0,
    
    -- Segment counts
    total_segments INTEGER NOT NULL DEFAULT 0,
    patrol_segments INTEGER NOT NULL DEFAULT 0,
    static_segments INTEGER NOT NULL DEFAULT 0,
    excluded_segments INTEGER NOT NULL DEFAULT 0,
    
    -- Store classified segments as JSON (not original file)
    classified_segments_json TEXT,
    
    -- Processing status
    processing_status TEXT DEFAULT 'completed', -- completed, rejected, partial
    rejection_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_gpx_logs_upload_time ON gpx_upload_logs(upload_time DESC);
CREATE INDEX IF NOT EXISTS idx_gpx_logs_park ON gpx_upload_logs(protected_area_id);
CREATE INDEX IF NOT EXISTS idx_gpx_logs_user ON gpx_upload_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_gpx_logs_status ON gpx_upload_logs(processing_status);

-- Add columns to gpx_uploads for validation status if not exists
-- Note: SQLite doesn't support ADD COLUMN IF NOT EXISTS, so we check first
