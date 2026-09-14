-- 066: fire_season_front — where the burning season had arrived by when,
-- per area and season, and the ground truth for a trajectory's LEAD.
--
-- WHY. Inside the burning season the tracker's day-to-day links carry no
-- information beyond the corridor (real ≈ day-shuffled, docs/agents/fire.md
-- § Link evidence). AHEAD of the season front they do (skill 0.42–0.51 for
-- every lead threshold 5…25 d). So "how far ahead of the season did this
-- fire burn" is the one property that separates the scouts' ignition chains
-- from the mass, and it needs the front to be measured against.
--
-- The front at a 2.5 km cell is the day by which a fifth of the land that
-- burns in any season we hold, within ~60 km, had burned this season. It is
-- causal (known the day it happens), so the same rule serves the archive and
-- this morning's detections. `usual` is the median over previous complete
-- seasons, and stands in live where this season's front has not arrived.
--
-- Written only by scripts/fire_front.py. Grids are little-endian int16
-- day-of-season, -1 = no front (too little burnable land in the window, or the
-- fraction never reached). `contours_json` holds the isochrones the map
-- draws, one GeoJSON Feature per CONTOUR_STEP_DAYS.
CREATE TABLE IF NOT EXISTS fire_season_front (
    area_id       TEXT    NOT NULL,   -- park id or AOI id
    season        TEXT    NOT NULL,   -- '2024/25' (or '2024' when the season starts in January)
    season_start  TEXT    NOT NULL,
    season_end    TEXT    NOT NULL,
    start_month   INTEGER NOT NULL,   -- the area's quietest month, measured
    complete      INTEGER NOT NULL,   -- 1 once the data reaches 330 days into the season
    latest_day    TEXT,
    res REAL NOT NULL, x0 REAL NOT NULL, y0 REAL NOT NULL, nx INTEGER NOT NULL, ny INTEGER NOT NULL,
    front         BLOB,
    usual         BLOB,
    contours_json TEXT,
    stats_json    TEXT,
    computed_at   TEXT    NOT NULL,
    PRIMARY KEY (area_id, season)
);

-- The lead of a loaded trajectory as COLUMNS, so "vanguard chains in this
-- rectangle" is an indexed question and not a json_extract over every
-- trajectory in view (711k rows for one AOI). properties_json carries the same
-- values plus the per-vertex leads; fire_front.py and
-- load_fire_groups_to_db.py write both. NULL lead_start = not yet measured
-- (the area's front has not been built), which is not the same as 0.
ALTER TABLE feature_geometries ADD COLUMN lead_start INTEGER;
ALTER TABLE feature_geometries ADD COLUMN vanguard INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_fg_vanguard
    ON feature_geometries(bbox_minx, bbox_maxx, bbox_miny, bbox_maxy, start_date)
    WHERE vanguard = 1;
