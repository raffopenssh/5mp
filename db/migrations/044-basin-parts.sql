-- 044: one row per OUTLET watershed, not just the merged union.
--
-- park_basins is keyed PRIMARY KEY (park_id, kind), so it can only ever hold
-- ONE upstream polygon per area: the union of every outlet's watershed. That
-- was fine for "how much land drains through this park" and wrong for
-- everything else. A park usually has several genuinely separate contributing
-- basins (CAF_Chinko drains via both the Chinko and the Mbari) and a large AOI
-- has many; unioning them into one MultiPolygon threw away which outlet each
-- lobe belonged to, so the map could not show "all watersheds" and a lobe
-- could not be attributed to the river that carries it.
--
-- Additive on purpose: park_basins keeps its merged row and every existing
-- reader keeps working. Readers that want the parts prefer this table and fall
-- back to the merged row when it is empty (parks fetched before this table
-- existed; a re-run backfills them for free out of http_cache).
CREATE TABLE IF NOT EXISTS park_basin_parts (
    park_id      TEXT NOT NULL,
    kind         TEXT NOT NULL,        -- 'upstream' | 'downstream'
    idx          INTEGER NOT NULL,     -- outlet rank, 0 = biggest/best
    outlet_lat   REAL NOT NULL,
    outlet_lon   REAL NOT NULL,
    river        TEXT,                 -- outlet's river name, when known
    area_km2     REAL,                 -- upstream
    length_km    REAL,                 -- downstream
    geojson      TEXT NOT NULL,
    source       TEXT NOT NULL,
    meta         TEXT,
    fetched_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (park_id, kind, idx)
);
CREATE INDEX IF NOT EXISTS idx_pbp_park ON park_basin_parts(park_id, kind);
