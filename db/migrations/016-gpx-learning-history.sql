-- GPX Learning History Tables for Versioning and Rollback

-- Add version column to learned features tables if not exists
-- (SQLite doesn't support IF NOT EXISTS for ALTER TABLE, use try-catch pattern)

-- Create history tables for rollback
CREATE TABLE IF NOT EXISTS learned_roads_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_id INTEGER NOT NULL,
    park_id TEXT NOT NULL,
    geojson TEXT NOT NULL,
    distance_km REAL,
    match_count INTEGER DEFAULT 1,
    confidence REAL DEFAULT 0.25,
    is_approved INTEGER DEFAULT 0,
    is_rejected INTEGER DEFAULT 0,
    version INTEGER NOT NULL,
    action TEXT NOT NULL,  -- 'create', 'update', 'approve', 'reject', 'rollback'
    action_by TEXT,
    action_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS learned_airstrips_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_id INTEGER NOT NULL,
    park_id TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    heading_deg REAL,
    length_m REAL,
    aircraft_type TEXT,
    landing_count INTEGER DEFAULT 1,
    confidence REAL DEFAULT 0.25,
    is_approved INTEGER DEFAULT 0,
    is_rejected INTEGER DEFAULT 0,
    version INTEGER NOT NULL,
    action TEXT NOT NULL,
    action_by TEXT,
    action_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS learned_places_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_id INTEGER NOT NULL,
    park_id TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    place_type TEXT,
    visit_count INTEGER DEFAULT 1,
    avg_duration_minutes REAL,
    confidence REAL DEFAULT 0.25,
    is_approved INTEGER DEFAULT 0,
    is_rejected INTEGER DEFAULT 0,
    version INTEGER NOT NULL,
    action TEXT NOT NULL,
    action_by TEXT,
    action_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for history queries
CREATE INDEX IF NOT EXISTS idx_roads_history_park ON learned_roads_history(park_id);
CREATE INDEX IF NOT EXISTS idx_roads_history_original ON learned_roads_history(original_id);
CREATE INDEX IF NOT EXISTS idx_airstrips_history_park ON learned_airstrips_history(park_id);
CREATE INDEX IF NOT EXISTS idx_airstrips_history_original ON learned_airstrips_history(original_id);
CREATE INDEX IF NOT EXISTS idx_places_history_park ON learned_places_history(park_id);
CREATE INDEX IF NOT EXISTS idx_places_history_original ON learned_places_history(original_id);

