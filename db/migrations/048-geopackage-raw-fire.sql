-- A GeoPackage export can omit the raw VIIRS detections.
--
-- They are the single biggest layer by an order of magnitude (XSA: 6.9M points,
-- ~1.1 GB of a 1.4 GB file, and most of the 9-minute build) while the derived
-- fire_trajectories layer carries the same story in 38k features. So "with" and
-- "without" are two genuinely different downloads, not a setting — and because
-- the job table is a CACHE keyed by the question asked, the flag has to be part
-- of the row and of the cache key. Without it the two variants would collide
-- and the second request would be served the first one's file.
ALTER TABLE geopackage_jobs ADD COLUMN raw_fire INTEGER NOT NULL DEFAULT 1;
