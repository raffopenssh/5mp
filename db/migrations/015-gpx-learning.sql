-- GPX Learning and Pattern Detection Schema
-- Stores learned data from uploaded GPX files for intelligent place/road discovery

-- Learned vehicle speed statistics per park
CREATE TABLE IF NOT EXISTS park_vehicle_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    movement_type TEXT NOT NULL, -- 'vehicle', 'foot', 'aircraft_fixed', 'aircraft_rotor'
    total_distance_km REAL DEFAULT 0,
    total_time_hours REAL DEFAULT 0,
    median_speed_kmh REAL DEFAULT 0,
    max_speed_kmh REAL DEFAULT 0,
    p90_speed_kmh REAL DEFAULT 0,
    sample_count INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(park_id, movement_type)
);

-- Foot patrol MCP (Minimum Convex Polygon) areas
CREATE TABLE IF NOT EXISTS park_patrol_mcp (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    mcp_90_geojson TEXT, -- 90% MCP as GeoJSON polygon
    mcp_area_km2 REAL DEFAULT 0,
    centroid_lat REAL,
    centroid_lon REAL,
    point_count INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(park_id)
);

-- Learned roads from vehicle tracks (±20m match threshold)
CREATE TABLE IF NOT EXISTS learned_roads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    geojson TEXT NOT NULL, -- Simplified LineString (10m)
    length_m REAL DEFAULT 0,
    match_count INTEGER DEFAULT 1, -- How many times vehicles used this track
    confidence_pct REAL DEFAULT 0, -- Certainty percentage
    status TEXT DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
    approved_by TEXT,
    approved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Learned airstrips from aircraft takeoff/landing patterns
CREATE TABLE IF NOT EXISTS learned_airstrips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    heading_deg REAL, -- Primary runway heading
    length_m REAL DEFAULT 0, -- Estimated runway length
    aircraft_type TEXT DEFAULT 'mixed', -- 'fixed_wing', 'rotor_wing', 'mixed'
    landing_count INTEGER DEFAULT 0,
    takeoff_count INTEGER DEFAULT 0,
    confidence_pct REAL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    approved_by TEXT,
    approved_at TIMESTAMP,
    approach_json TEXT, -- First/last 2000m of approaches
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Learned places (HQ, outposts, camps) from stop patterns
CREATE TABLE IF NOT EXISTS learned_places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    place_type TEXT, -- 'headquarters', 'outpost', 'camp', 'gate', 'unknown'
    name TEXT,
    visit_count INTEGER DEFAULT 0,
    avg_duration_minutes REAL DEFAULT 0,
    confidence_pct REAL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    approved_by TEXT,
    approved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Simplified vehicle movement tracks (no timestamps, 10m simplified)
CREATE TABLE IF NOT EXISTS vehicle_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    upload_id INTEGER REFERENCES gpx_uploads(id),
    geojson TEXT NOT NULL, -- Simplified LineString
    length_m REAL DEFAULT 0,
    movement_type TEXT DEFAULT 'vehicle',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Aircraft approach/departure patterns (first/last 2000m)
CREATE TABLE IF NOT EXISTS aircraft_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    upload_id INTEGER REFERENCES gpx_uploads(id),
    pattern_type TEXT NOT NULL, -- 'approach', 'departure'
    aircraft_type TEXT, -- 'fixed_wing', 'rotor_wing'
    start_lat REAL,
    start_lon REAL,
    end_lat REAL,
    end_lon REAL,
    geojson TEXT, -- LineString of the pattern
    avg_speed_kmh REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Dataset versioning for rollback capability
CREATE TABLE IF NOT EXISTS dataset_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    dataset_type TEXT NOT NULL, -- 'roads', 'places', 'airstrips'
    version INTEGER NOT NULL,
    data_json TEXT NOT NULL, -- Full snapshot of the dataset
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 0
);

-- GPX Learning processing queue
CREATE TABLE IF NOT EXISTS gpx_learning_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER REFERENCES gpx_uploads(id),
    park_id TEXT,
    status TEXT DEFAULT 'pending', -- 'pending', 'processing', 'completed', 'failed'
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

-- Learning results log for admin panel
CREATE TABLE IF NOT EXISTS gpx_learning_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER REFERENCES gpx_uploads(id),
    park_id TEXT NOT NULL,
    park_name TEXT,
    
    -- Speed stats discovered
    vehicle_median_speed_kmh REAL,
    vehicle_max_speed_kmh REAL,
    foot_median_speed_kmh REAL,
    foot_max_speed_kmh REAL,
    
    -- Area stats
    foot_mcp_area_km2 REAL,
    
    -- Discoveries
    new_roads_found INTEGER DEFAULT 0,
    new_roads_km REAL DEFAULT 0,
    road_confidence_pct REAL,
    
    new_airstrips_found INTEGER DEFAULT 0,
    airstrip_confidence_pct REAL,
    
    new_places_found INTEGER DEFAULT 0,
    place_types_json TEXT, -- {"headquarters": 1, "outpost": 2, "camp": 3}
    place_confidence_pct REAL,
    
    -- Summary for admin
    summary_text TEXT,
    discoveries_json TEXT, -- Full details of all discoveries
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_learned_roads_park ON learned_roads(park_id);
CREATE INDEX IF NOT EXISTS idx_learned_airstrips_park ON learned_airstrips(park_id);
CREATE INDEX IF NOT EXISTS idx_learned_places_park ON learned_places(park_id);
CREATE INDEX IF NOT EXISTS idx_vehicle_tracks_park ON vehicle_tracks(park_id);
CREATE INDEX IF NOT EXISTS idx_aircraft_patterns_park ON aircraft_patterns(park_id);
CREATE INDEX IF NOT EXISTS idx_gpx_learning_queue_status ON gpx_learning_queue(status);
CREATE INDEX IF NOT EXISTS idx_gpx_learning_results_park ON gpx_learning_results(park_id);

-- Add version column to learned features tables if not exists
ALTER TABLE learned_roads ADD COLUMN version INTEGER DEFAULT 1;
ALTER TABLE learned_airstrips ADD COLUMN version INTEGER DEFAULT 1;
ALTER TABLE learned_places ADD COLUMN version INTEGER DEFAULT 1;

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
CREATE INDEX IF NOT EXISTS idx_airstrips_history_park ON learned_airstrips_history(park_id);
CREATE INDEX IF NOT EXISTS idx_places_history_park ON learned_places_history(park_id);

