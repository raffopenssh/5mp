-- Performance indexes for per-park stats/narrative/alert endpoints (2026-07-09).
-- All statements are idempotent (IF NOT EXISTS) so they can be pre-built
-- manually on a live DB before deploying (the fire index takes ~4min over 6M rows).

-- /api/parks/{id}/stats + fire-log + fire-trend: the old single-column
-- idx_fire_pa forced row lookups + full strftime() scans (~1.2s per request);
-- (protected_area_id, acq_date) makes them index-only range scans (<0.1s).
CREATE INDEX IF NOT EXISTS idx_fire_pa_date ON fire_detections(protected_area_id, acq_date);

-- Redundant now: composite index covers the protected_area_id prefix.
DROP INDEX IF EXISTS idx_fire_pa;

-- /api/parks/{id}/fire-narrative weekly trend (addWeeklyDataToTrend) and
-- fire-trend weekly groups: GROUP BY over start_date for one park+type.
-- idx_fg_park_type covered (park_id, feature_type) but not start_date,
-- causing ~0.2s of row fetches per call.
CREATE INDEX IF NOT EXISTS idx_fg_park_type_date ON feature_geometries(park_id, feature_type, start_date);

-- Redundant now: covered by the composite above.
DROP INDEX IF EXISTS idx_fg_park_type;

-- Fire group alert scanner (fire_realtime_handlers.go): "active groups" scan
-- filters feature_type + end_date >= cutoff + dist_to_park_km; idx_fg_dist_park
-- only narrowed by feature_type (~0.7s full-type scan every cycle).
CREATE INDEX IF NOT EXISTS idx_fg_type_end ON feature_geometries(feature_type, end_date, dist_to_park_km);
