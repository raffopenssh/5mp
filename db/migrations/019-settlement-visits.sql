-- Track patrol visits to settlements for intensity visualization
-- This helps distinguish park bases from regular settlements

CREATE TABLE IF NOT EXISTS settlement_visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    settlement_id INTEGER NOT NULL REFERENCES park_settlements(id),
    upload_id INTEGER REFERENCES gpx_uploads(id),
    park_id TEXT NOT NULL,
    
    -- Visit timing
    visit_date DATE NOT NULL,
    visit_start TIMESTAMP,
    visit_end TIMESTAMP,
    duration_minutes REAL NOT NULL DEFAULT 0,
    
    -- Movement context
    movement_type TEXT NOT NULL, -- 'foot', 'vehicle', 'aircraft'
    arriving_from TEXT, -- direction or place name
    departing_to TEXT,
    
    -- Aggregation helpers
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(settlement_id, upload_id, visit_start)
);

CREATE INDEX IF NOT EXISTS idx_settlement_visits_settlement ON settlement_visits(settlement_id);
CREATE INDEX IF NOT EXISTS idx_settlement_visits_park ON settlement_visits(park_id);
CREATE INDEX IF NOT EXISTS idx_settlement_visits_date ON settlement_visits(visit_date);
CREATE INDEX IF NOT EXISTS idx_settlement_visits_year_month ON settlement_visits(year, month);

-- Aggregated settlement intensity data
CREATE TABLE IF NOT EXISTS settlement_intensity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    settlement_id INTEGER NOT NULL REFERENCES park_settlements(id),
    park_id TEXT NOT NULL,
    
    -- Time period
    year INTEGER NOT NULL,
    month INTEGER, -- NULL for annual aggregation
    
    -- Intensity metrics
    total_visits INTEGER NOT NULL DEFAULT 0,
    total_duration_minutes REAL NOT NULL DEFAULT 0,
    unique_uploads INTEGER NOT NULL DEFAULT 0,
    
    -- Movement breakdown
    foot_visits INTEGER DEFAULT 0,
    vehicle_visits INTEGER DEFAULT 0,
    aircraft_visits INTEGER DEFAULT 0,
    
    -- Classification hint
    is_likely_base INTEGER DEFAULT 0, -- 1 if frequently visited (potential park base)
    
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(settlement_id, year, month)
);

CREATE INDEX IF NOT EXISTS idx_settlement_intensity_park ON settlement_intensity(park_id);
CREATE INDEX IF NOT EXISTS idx_settlement_intensity_year ON settlement_intensity(year, month);
CREATE INDEX IF NOT EXISTS idx_settlement_intensity_base ON settlement_intensity(is_likely_base) WHERE is_likely_base = 1;
