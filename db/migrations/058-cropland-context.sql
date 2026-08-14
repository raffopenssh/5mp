-- 058: cropland context from GLAD global cropland extent (Potapov et al. 2021,
-- Nature Food, doi:10.1038/s43016-021-00429-z; 30 m Landsat, epochs 2003..2019).
--
-- WHY. Settlement classification scores 'agricultural' from fire counts and
-- deforestation shape — proxies. GLAD maps cropland directly (annual/perennial
-- herbaceous crops; pasture and shifting cultivation EXCLUDED by the source's
-- definition, which matters in a transhumance landscape: a pastoral camp
-- scoring 0 cropland is the dataset working, not missing data). Measured on
-- XSA_Study_Area before building any of this: settlements sit on 5.8x the
-- background cropland fraction, towns 57% vs villages 36% vs camps 19%
-- (any-cropland-within-1km), and 'recent'-persistence clusters are 53% vs
-- permanent 22% — the signal discriminates, so it earns columns.
--
-- The two epochs give a TREND per settlement: 2003 (epoch 2000-2003) vs 2019
-- (epoch 2016-2019). Both ride on the row so the trend is auditable and
-- re-derivable without re-reading rasters (same shape as surface_e2000_m2).
--
-- cropland_frac_YYYY is the mean cropland fraction of the 30 m pixels within
-- ~1 km of the cluster centroid, in [0,1]. The RADIUS is part of the
-- definition: this is "how much of this settlement's immediate landscape is
-- cropped", not "is the built footprint cropped" (a 30 m crop pixel under a
-- house is a misregistration, not a farm). NULL = unmeasured;
-- cropland_source says why ('glad_cropland_30m' when measured,
-- 'clip_missing' when the source raster could not be fetched — a field with
-- no crops and a raster that never downloaded are different states,
-- AGENTS.md invariant 1).
--
-- Writer: scripts/cropland.py (called by scripts/backfill_settlement_surface.py
-- after ghsl_epochs); stamp: data/cropland_state.json.
ALTER TABLE park_settlements ADD COLUMN cropland_frac_2019 REAL
    CHECK(cropland_frac_2019 IS NULL OR (cropland_frac_2019 >= 0 AND cropland_frac_2019 <= 1));
ALTER TABLE park_settlements ADD COLUMN cropland_frac_2003 REAL
    CHECK(cropland_frac_2003 IS NULL OR (cropland_frac_2003 >= 0 AND cropland_frac_2003 <= 1));
ALTER TABLE park_settlements ADD COLUMN cropland_source TEXT;

-- Same measurement over a deforestation event's centroid: "did this clearing
-- become cropland?" separates agricultural conversion from logging/natural
-- loss. Only the 2019 epoch here — an event has a year, and the question is
-- whether the ground is cropped NOW, not its trend.
ALTER TABLE deforestation_events ADD COLUMN cropland_frac_2019 REAL
    CHECK(cropland_frac_2019 IS NULL OR (cropland_frac_2019 >= 0 AND cropland_frac_2019 <= 1));
ALTER TABLE deforestation_events ADD COLUMN cropland_source TEXT;
