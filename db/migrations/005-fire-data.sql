-- NASA VIIRS fire detection data storage
-- Stores individual fire detections with all metadata from NASA FIRMS

CREATE TABLE IF NOT EXISTS fire_detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    brightness REAL,           -- Brightness temperature (Kelvin)
    scan REAL,                 -- Scan pixel size
    track REAL,                -- Track pixel size
    acq_date TEXT NOT NULL,    -- Acquisition date (YYYY-MM-DD)
    acq_time TEXT,             -- Acquisition time (HHMM)
    satellite TEXT,            -- VIIRS sensor: N (Suomi NPP), 1 (NOAA-20), 2 (NOAA-21)
    instrument TEXT,           -- VIIRS
    confidence TEXT,           -- low, nominal, high
    version TEXT,              -- Processing version
    bright_t31 REAL,           -- Brightness temp channel 31
    frp REAL,                  -- Fire Radiative Power (MW)
    daynight TEXT,             -- D=day, N=night
    
    -- Computed fields for efficient queries
    grid_cell_id TEXT,         -- References grid_cells table
    in_protected_area INTEGER DEFAULT 0,  -- 1 if inside any keystone PA
    protected_area_id TEXT,    -- Keystone PA ID if inside
    
    UNIQUE(latitude, longitude, acq_date, acq_time, satellite)
);

CREATE INDEX IF NOT EXISTS idx_fire_date ON fire_detections(acq_date);
CREATE INDEX IF NOT EXISTS idx_fire_location ON fire_detections(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_fire_grid ON fire_detections(grid_cell_id);
CREATE INDEX IF NOT EXISTS idx_fire_pa ON fire_detections(protected_area_id);
CREATE INDEX IF NOT EXISTS idx_fire_infraction ON fire_detections(in_protected_area, acq_date);

-- Track data fetch status per park (to know what dates we've fetched)
CREATE TABLE IF NOT EXISTS fire_data_sync (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,          -- Keystone PA ID
    bbox_west REAL NOT NULL,
    bbox_south REAL NOT NULL,
    bbox_east REAL NOT NULL,
    bbox_north REAL NOT NULL,
    buffer_km REAL NOT NULL DEFAULT 50,
    first_date TEXT,                -- Earliest date we have data for
    last_date TEXT,                 -- Latest date we have data for
    last_sync_at TEXT,
    total_detections INTEGER DEFAULT 0,
    UNIQUE(park_id)
);

-- Daily aggregated fire statistics per grid cell (for visualization)
CREATE TABLE IF NOT EXISTS fire_daily_grid (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grid_cell_id TEXT NOT NULL,
    date TEXT NOT NULL,             -- YYYY-MM-DD
    fire_count INTEGER NOT NULL DEFAULT 0,
    total_frp REAL DEFAULT 0,       -- Sum of Fire Radiative Power
    avg_confidence REAL,
    in_protected_area INTEGER DEFAULT 0,
    protected_area_id TEXT,
    UNIQUE(grid_cell_id, date),
    FOREIGN KEY (grid_cell_id) REFERENCES grid_cells(id)
);

CREATE INDEX IF NOT EXISTS idx_fire_daily_date ON fire_daily_grid(date);
CREATE INDEX IF NOT EXISTS idx_fire_daily_pa ON fire_daily_grid(protected_area_id, date);
