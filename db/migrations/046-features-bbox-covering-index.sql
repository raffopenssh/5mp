-- features-in-bbox pass 1 must be covering, or park_id costs 12k row lookups.
--
-- idx_fg_stats already covers the bbox+date+rank columns, but the AOI
-- visibility filter (aoiExcludeSQL / aoiScopeSQL) reads park_id, which is not
-- in it — so SQLite found the candidate rows from the index and then fetched
-- each full row from the table just to read one short string. A
-- feature_geometries row carries its geojson (fire trajectories average ~350
-- bytes but run to 100 KB), so that lookup dominates: 2.5-3.0 s for a
-- 3-degree window over CAF, of which the index scan itself is 0.15 s.
--
-- Adding park_id (and the centroid inputs) makes pass 1 index-only.
-- id is the rowid and is implicitly present.
CREATE INDEX IF NOT EXISTS idx_fg_bbox_scan ON feature_geometries(
    feature_type,
    bbox_minx, bbox_maxx, bbox_miny, bbox_maxy,
    start_date,
    stat_value,
    park_id
);
