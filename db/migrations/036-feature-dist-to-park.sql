-- Distance (km) from a feature's trajectory to its park boundary; 0 = inside.
-- Written by scripts/load_fire_groups_to_db.py (from group JSON dist_to_park_km).
-- NULL = not yet computed (pre-backfill rows); Go read paths must treat NULL as relevant.
-- Used to gate notifications and per-park stats to groups inside or within 20km
-- of the park boundary (see docs/FIRE_100KM_BUFFER_PLAN.md).
ALTER TABLE feature_geometries ADD COLUMN dist_to_park_km REAL;

-- Populate from properties where the pipeline already wrote it
UPDATE feature_geometries
SET dist_to_park_km = json_extract(properties_json, '$.dist_to_park_km')
WHERE feature_type = 'fire_trajectory'
  AND properties_json IS NOT NULL
  AND json_extract(properties_json, '$.dist_to_park_km') IS NOT NULL;

-- Cheap approximation for existing rows: fully-inside groups are distance 0
UPDATE feature_geometries
SET dist_to_park_km = 0
WHERE feature_type = 'fire_trajectory'
  AND dist_to_park_km IS NULL
  AND COALESCE(json_extract(properties_json, '$.pct_inside'), 0) > 0;

CREATE INDEX IF NOT EXISTS idx_fg_dist_park ON feature_geometries(feature_type, park_id, dist_to_park_km);
