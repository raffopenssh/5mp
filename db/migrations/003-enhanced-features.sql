-- Migration 003: Enhanced Features for Pattern Detection and Map Display
-- Date: 2026-02-02
--
-- Adds:
-- 1. feature_geometries table for centralized GeoJSON storage
-- 2. Classification columns to park_settlements
-- 3. Classification columns to deforestation_clusters
-- 4. Date range columns to park_group_infractions

-- Feature geometries table for map display
CREATE TABLE IF NOT EXISTS feature_geometries (
    id INTEGER PRIMARY KEY,
    feature_type TEXT NOT NULL,  -- 'fire_trajectory', 'deforestation', 'settlement', 'road'
    feature_id TEXT NOT NULL,    -- Reference to source (e.g., 'CAF_Chinko_2024_grp_1')
    park_id TEXT NOT NULL,
    geojson TEXT NOT NULL,       -- Full GeoJSON geometry
    bbox_minx REAL,
    bbox_miny REAL,
    bbox_maxx REAL,
    bbox_maxy REAL,
    start_date TEXT,             -- ISO date for time slider
    end_date TEXT,
    properties_json TEXT,        -- Additional properties for popup
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(feature_type, feature_id)
);

CREATE INDEX IF NOT EXISTS idx_fg_park_type ON feature_geometries(park_id, feature_type);
CREATE INDEX IF NOT EXISTS idx_fg_dates ON feature_geometries(start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_fg_bbox ON feature_geometries(bbox_minx, bbox_miny, bbox_maxx, bbox_maxy);

-- Settlement classification columns (use ALTER TABLE with error handling in app)
-- These will be added by the classify_features.py script if not present:
--   classification TEXT DEFAULT 'unclassified'
--   confidence REAL DEFAULT 0.0
--   distance_to_road_m REAL
--   distance_to_river_m REAL
--   footprint_geojson TEXT
--   population_2020 INTEGER
--   population_2030 INTEGER

-- Deforestation cluster classification columns (added by script):
--   classification TEXT DEFAULT 'unclassified'
--   confidence REAL DEFAULT 0.0
--   distance_to_road_m REAL
--   distance_to_settlement_m REAL
--   distance_to_river_m REAL
--   polygon_geojson TEXT
--   start_date TEXT
--   end_date TEXT

-- Fire infraction date range columns (added by script):
--   bbox_geojson TEXT
--   start_date TEXT
--   end_date TEXT
