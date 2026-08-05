-- 039: contributing (upstream) basin + downstream trace per park, and a
-- generic HTTP response cache so courtesy APIs are hit at most once.
--
-- Rationale (docs/MINING_FINDINGS_2026-08.md §1): mining pressure on a park is
-- a *watershed* phenomenon; the pit/turbidity scanners were park-bbox-scoped
-- and therefore blind to the 8 confirmed pits 123 km upstream of CAF_Chinko.
CREATE TABLE IF NOT EXISTS park_basins (
    park_id      TEXT NOT NULL,
    kind         TEXT NOT NULL,        -- 'upstream' | 'downstream'
    outlet_lat   REAL NOT NULL,
    outlet_lon   REAL NOT NULL,
    area_km2     REAL,                 -- upstream: contributing area
    length_km    REAL,                 -- downstream: traced length
    geojson      TEXT NOT NULL,        -- Polygon/MultiPolygon or MultiLineString
    source       TEXT NOT NULL,        -- 'mghydro' | 'river-runner'
    meta         TEXT,                 -- JSON: outlet pick provenance, names...
    fetched_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (park_id, kind)
);

-- Every courtesy-API response is cached here: mghydro runs on a $25/mo shared
-- host and asks for ~5 s between calls (docs/MINING_DATA_SOURCES.md §5.1).
CREATE TABLE IF NOT EXISTS http_cache (
    url          TEXT PRIMARY KEY,
    status       INTEGER,
    body         BLOB,
    fetched_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Upstream river network with Strahler order, from mghydro /app/getwshed.
-- Stream order is the gate for turbidity work: Sentinel-2 cannot see the water
-- surface on 1st-2nd order streams under canopy (0 of 261 sampled network points
-- classified as water, docs/MINING_FINDINGS_2026-08.md §4), so plume detection
-- is only meaningful on >=3rd order reaches.
CREATE TABLE IF NOT EXISTS park_basin_rivers (
    park_id      TEXT NOT NULL,
    comid        INTEGER NOT NULL,
    stream_order INTEGER,
    length_km    REAL,
    geojson      TEXT NOT NULL,
    PRIMARY KEY (park_id, comid)
);
CREATE INDEX IF NOT EXISTS idx_pbr_order ON park_basin_rivers(park_id, stream_order);
