-- Cropland CONVERSION on deforestation events (scripts/cropland.py,
-- version 2026-08-14b). Migration 058's cropland_frac_2019 KEEPS its original
-- semantics -- mean cropland fraction of the ~1 km box around the event
-- centroid, i.e. "is this clearing in a farming landscape" -- which is the
-- context question a cropland vector layer would otherwise answer. Two new
-- columns answer the attribution question over the event's OWN cleared
-- pixels (polygon_ids -> feature_geometries, rasterized onto the GLAD grid):
--   cropland_event_frac_2019   fraction of the cleared pixels mapped cropland
--                              in the 2016-2019 GLAD epoch.
--   cropland_conversion_frac   fraction of the cleared pixels cropped in 2019
--                              AND NOT in 2003 -- new cropland. area_km2 *
--                              cropland_conversion_frac sums to "km2 of
--                              deforestation attributable to cropland
--                              expansion". NULL = unmeasured (invariant 1).
ALTER TABLE deforestation_events ADD COLUMN cropland_conversion_frac REAL;
ALTER TABLE deforestation_events ADD COLUMN cropland_event_frac_2019 REAL;
