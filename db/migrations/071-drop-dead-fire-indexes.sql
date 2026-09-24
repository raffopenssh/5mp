-- Drop two fire_detections indexes that cost 2.1 GB and served no query
-- (2026-09-24, measured on the 42.9M-row production table).
--
-- idx_fire_grid (580 MB): grid_cell_id is NULL on every fire_detections row.
--   The only writer that filled it (scripts/fire_data_manager.py) is not in
--   any pipeline; daily_fire_update.py never sets it. Nothing reads it.
--
-- idx_fire_location (1.5 GB): (latitude, longitude) is the leading prefix of
--   the UNIQUE autoindex (latitude, longitude, acq_date, acq_time, satellite).
--   EXPLAIN QUERY PLAN on every lat/lon-bounded query in srv/ and scripts/
--   (pure bbox, bbox+date, half-open band) picks the autoindex once this one
--   is gone, with the same `latitude>? AND latitude<?` search; a 2°×1° bbox
--   count went 0.21 s -> 0.31 s (wider index pages), nothing else changed.
DROP INDEX IF EXISTS idx_fire_grid;
DROP INDEX IF EXISTS idx_fire_location;
