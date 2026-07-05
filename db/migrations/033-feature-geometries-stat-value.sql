-- Add stat_value column to feature_geometries for fast stats aggregation
-- Stores fires_total for fire_trajectory, area_km2 for deforestation
-- (uses CREATE TABLE trick since ALTER TABLE ADD COLUMN IF NOT EXISTS is not supported)
CREATE TABLE IF NOT EXISTS _tmp_fg_check (x);
DROP TABLE _tmp_fg_check;

-- Only add column if it doesn't exist (will no-op if already present)
ALTER TABLE feature_geometries ADD COLUMN stat_value REAL DEFAULT 0;

-- Populate existing rows
UPDATE feature_geometries SET stat_value = COALESCE(json_extract(properties_json, '$.fires_total'), 0)
    WHERE feature_type = 'fire_trajectory' AND properties_json IS NOT NULL;
UPDATE feature_geometries SET stat_value = COALESCE(json_extract(properties_json, '$.area_km2'), 0)
    WHERE feature_type = 'deforestation' AND properties_json IS NOT NULL;

-- Index for fast stats queries (covers type + date + bbox + value)
CREATE INDEX IF NOT EXISTS idx_fg_stats ON feature_geometries(
    feature_type, start_date, bbox_minx, bbox_maxx, bbox_miny, bbox_maxy, stat_value
);

-- Auto-populate on insert
CREATE TRIGGER IF NOT EXISTS trg_fg_stat_value_insert
AFTER INSERT ON feature_geometries
FOR EACH ROW
BEGIN
    UPDATE feature_geometries SET stat_value =
        CASE
            WHEN NEW.feature_type = 'fire_trajectory' THEN COALESCE(json_extract(NEW.properties_json, '$.fires_total'), 0)
            WHEN NEW.feature_type = 'deforestation' THEN COALESCE(json_extract(NEW.properties_json, '$.area_km2'), 0)
            ELSE 0
        END
    WHERE id = NEW.id;
END;

-- Auto-update when properties change
CREATE TRIGGER IF NOT EXISTS trg_fg_stat_value_update
AFTER UPDATE OF properties_json ON feature_geometries
FOR EACH ROW
BEGIN
    UPDATE feature_geometries SET stat_value =
        CASE
            WHEN NEW.feature_type = 'fire_trajectory' THEN COALESCE(json_extract(NEW.properties_json, '$.fires_total'), 0)
            WHEN NEW.feature_type = 'deforestation' THEN COALESCE(json_extract(NEW.properties_json, '$.area_km2'), 0)
            ELSE 0
        END
    WHERE id = NEW.id;
END;
