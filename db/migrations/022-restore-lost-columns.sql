-- 022: restore the columns that migrations 015-030 used to add.
--
-- Those files were lost from the repository while remaining recorded as
-- executed in the live database (as "0NN-placeholder"), so production never
-- noticed: it does not run early migrations again. A FRESH database did — and
-- failed at 035, which rebuilds effort_data and selects columns nothing had
-- created. `New()` on an empty file therefore could not build a server at all,
-- which is what broke TestServerSetupAndHandlers.
--
-- This restores exactly the columns the live schema carries at this point, so
-- a fresh checkout converges on the same schema. It is a no-op in production:
-- 022 is already in the migrations table there.
--
-- Deliberately NOT written as one big CREATE-and-copy: 035 immediately rebuilds
-- both of these tables anyway, and the point here is only to make its SELECT
-- list resolvable.

ALTER TABLE effort_data ADD COLUMN avg_speed_kmh REAL;
ALTER TABLE effort_data ADD COLUMN avg_altitude_m REAL;


-- deforestation_events is the one lost TABLE a later migration touches (043
-- adds pixel_count to it). Every other table missing from this directory —
-- fire_grid_*, park_rivers_hydro, osm_places, roads_heigit … — is created by
-- the python pipeline with CREATE TABLE IF NOT EXISTS and is not referenced by
-- any migration, so it correctly stays out of here: this file exists to make
-- the migration chain self-consistent, not to become a second schema of record
-- for tables owned elsewhere.
CREATE TABLE IF NOT EXISTS deforestation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    area_km2 REAL,
    pattern_type TEXT,
    classification TEXT,
    narrative TEXT,
    polygon_ids TEXT,
    classification_confidence REAL DEFAULT 0,
    fires_same_year INTEGER DEFAULT 0,
    fire_ratio REAL DEFAULT 0,
    nearest_settlement_km REAL DEFAULT 0,
    classified_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_de_park ON deforestation_events(park_id);

-- park_settlements: the classification/enrichment columns were likewise added
-- by scripts directly on the live database (the settlement reclassify and fire
-- context pipelines), never by a migration. Migration 055 backfills provenance
-- FROM polygon_ids/narrative, so a fresh database failed there with
-- "no such column: polygon_ids" (TestServerSetupAndHandlers). Restore exactly
-- the columns the live schema carries before 055; a no-op in production, where
-- 022 is already recorded as executed.
ALTER TABLE park_settlements ADD COLUMN classification TEXT;
ALTER TABLE park_settlements ADD COLUMN classification_confidence REAL;
ALTER TABLE park_settlements ADD COLUMN narrative TEXT;
ALTER TABLE park_settlements ADD COLUMN polygon_ids TEXT;
ALTER TABLE park_settlements ADD COLUMN fires_1km INTEGER DEFAULT 0;
ALTER TABLE park_settlements ADD COLUMN fires_5km INTEGER DEFAULT 0;
ALTER TABLE park_settlements ADD COLUMN fire_seasonality TEXT;
ALTER TABLE park_settlements ADD COLUMN deforest_nearby_km2 REAL DEFAULT 0;
ALTER TABLE park_settlements ADD COLUMN classified_at TIMESTAMP;
