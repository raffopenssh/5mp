-- Base schema
--
-- Migrations tracking table
CREATE TABLE IF NOT EXISTS migrations (
    migration_number INTEGER PRIMARY KEY,
    migration_name TEXT NOT NULL,
    executed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Users table with approval workflow
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    organization TEXT NOT NULL DEFAULT '',
    organization_type TEXT NOT NULL DEFAULT '', -- 'government', 'nonprofit', 'protected_area_manager'
    role TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'approved', 'admin'
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    approved_by TEXT
);

-- GPX uploads
CREATE TABLE IF NOT EXISTS gpx_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    movement_type TEXT NOT NULL, -- 'foot', 'vehicle', 'aircraft'
    protected_area_id TEXT, -- WDPA ID if associated with a protected area
    upload_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    total_distance_km REAL NOT NULL DEFAULT 0,
    total_points INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Grid cells (100 sq km pixels)
-- Using ~0.09 degrees at equator for 10km x 10km = 100 sq km
CREATE TABLE IF NOT EXISTS grid_cells (
    id TEXT PRIMARY KEY, -- format: "lat_lon" e.g. "12.3_45.6"
    lat_center REAL NOT NULL,
    lon_center REAL NOT NULL,
    lat_min REAL NOT NULL,
    lat_max REAL NOT NULL,
    lon_min REAL NOT NULL,
    lon_max REAL NOT NULL
);

-- Effort data per grid cell and time period
CREATE TABLE IF NOT EXISTS effort_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grid_cell_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL, -- 1-12
    day INTEGER, -- NULL for monthly/annual aggregation
    movement_type TEXT NOT NULL, -- 'foot', 'vehicle', 'aircraft', 'all'
    total_distance_km REAL NOT NULL DEFAULT 0,
    total_points INTEGER NOT NULL DEFAULT 0,
    unique_uploads INTEGER NOT NULL DEFAULT 0,
    protected_area_ids TEXT, -- JSON array of WDPA IDs
    FOREIGN KEY (grid_cell_id) REFERENCES grid_cells(id),
    UNIQUE(grid_cell_id, year, month, day, movement_type)
);

-- GPX track points (for detailed analysis)
CREATE TABLE IF NOT EXISTS track_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    elevation REAL,
    timestamp TIMESTAMP,
    grid_cell_id TEXT,
    FOREIGN KEY (upload_id) REFERENCES gpx_uploads(id),
    FOREIGN KEY (grid_cell_id) REFERENCES grid_cells(id)
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_effort_data_grid ON effort_data(grid_cell_id);
CREATE INDEX IF NOT EXISTS idx_effort_data_time ON effort_data(year, month, day);
CREATE INDEX IF NOT EXISTS idx_track_points_upload ON track_points(upload_id);
CREATE INDEX IF NOT EXISTS idx_track_points_grid ON track_points(grid_cell_id);
CREATE INDEX IF NOT EXISTS idx_gpx_uploads_user ON gpx_uploads(user_id);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

-- Visitors table (for view counting)
CREATE TABLE IF NOT EXISTS visitors (
    id TEXT PRIMARY KEY,
    view_count INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Record execution of this migration
INSERT OR IGNORE INTO migrations (migration_number, migration_name)
VALUES (001, '001-base');
