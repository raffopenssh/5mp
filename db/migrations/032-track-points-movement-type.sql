-- Add movement_type column to track_points so effort data can be rebuilt
-- with per-point movement classification instead of proportional distribution.
ALTER TABLE track_points ADD COLUMN movement_type TEXT;
