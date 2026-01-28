-- GHSL settlement data per park
-- Data from Global Human Settlement Layer (built-up areas)
CREATE TABLE IF NOT EXISTS ghsl_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    built_up_km2 REAL DEFAULT 0,         -- Total built-up area in km²
    settlement_count INTEGER DEFAULT 0,   -- Number of distinct settlements
    analyzed_at TEXT,
    UNIQUE(park_id)
);

CREATE INDEX IF NOT EXISTS idx_ghsl_park ON ghsl_data(park_id);

-- OSM roadless area data per park
-- Based on "A global map of roadless areas" methodology
CREATE TABLE IF NOT EXISTS osm_roadless_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    roadless_percentage REAL DEFAULT 0,   -- Percentage of park that is roadless (>1km from roads)
    total_road_km REAL DEFAULT 0,         -- Total road length in km within park
    analyzed_at TEXT,
    UNIQUE(park_id)
);

CREATE INDEX IF NOT EXISTS idx_roadless_park ON osm_roadless_data(park_id);
