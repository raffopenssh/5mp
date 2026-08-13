-- Settlement surface vs extent, measured population, and the provenance that
-- says which is which.  docs/AOI_STRUCTURAL_FIXES.md F1/F2/F6/F12.
--
-- GHS_BUILT_S is a FRACTIONAL surface raster: a 100 m pixel holding 60 m² of
-- building is one whole pixel of the binary mask.  scripts/ghsl_tiles.py
-- vectorised that mask and reported the POLYGON's area as built-up area, so
-- `area_m2` was the ground a settlement's mask covers, not the surface built on
-- it — 6,798 km² against 181 km² over XSA_Study_Area, a 24x overstatement that
-- `population_est = area/1e4 * 200` then turned into 85 million people in one
-- AOI and a single "town" of 61.7 million.
--
-- Both numbers are wanted — extent to draw, surface to count — and AGENTS.md
-- invariant 7 says they must not share a name:
--
--     extent_m2   footprint of the built-up mask   (what you see)
--     area_m2     Σ of the raster's own values      (what is built)
--
-- POPULATION IS NOW MEASURED OR ABSENT.  `population_source` names the raster
-- a row's population was summed from (GHS_POP, same release/epoch/grid).  NULL
-- means UNMEASURED, and `population_est` must then be NULL too — never a
-- density constant, which is a guess wearing a measurement's clothes
-- (invariant 1, and the same `nil` problem as invariant 12).  It is a separate
-- column and not a narrative prefix on purpose: invariant 5 was learned when a
-- nightly reclassify laundered provenance by regenerating the text it lived in.
--
-- `epoch` travels with the row for the same reason.  E2030 is a PROJECTION;
-- every settlement figure in this app is a modelled 2030 state and nothing in
-- the schema said so.
--
-- `fire_context_at` is F6: fires_1km/fires_5km/fire_seasonality/
-- deforest_nearby_km2 default to 0, and 0 reads as "no fire near this
-- settlement" — the opposite of true for all 1,552 XSA rows, whose median is
-- 1,594 detections within 5 km.  Nothing failed; the enrichment simply was not
-- in the AOI path.  A timestamp distinguishes measured-zero from never-run, so
-- a reader can print "not computed" instead of a confident zero.
ALTER TABLE park_settlements ADD COLUMN extent_m2 REAL;
-- `area_source` says which of the two `area_m2` holds for THIS row, because a
-- backfill converts parks one at a time and the column therefore means two
-- different things on two different days.  A reader must be able to ask,
-- per row, rather than infer it from a date:
--     'ghsl_built_s_surface'  Σ of the fractional raster  (correct)
--     'ghsl_mask_extent'      area of the binary mask     (legacy, ~24x high)
ALTER TABLE park_settlements ADD COLUMN area_source TEXT;
ALTER TABLE park_settlements ADD COLUMN population_source TEXT;
ALTER TABLE park_settlements ADD COLUMN epoch TEXT;
ALTER TABLE park_settlements ADD COLUMN fire_context_at TIMESTAMP;

-- Deforestation: Hansen mapped canopy loss (2001-2023) and GFW alert counts
-- scaled by KM2_PER_ALERT (2024+) share `area_km2` and are drawn as one series,
-- which showed 313.6 km² in 2023 -> 0.7 km² in 2024 and reads as a 99.8%
-- collapse that is purely a unit change (F8).  `area_method` is the
-- discriminator; a chart must break between methods, never join them.
--
-- `needs_review` is F9: a (park, year) whose loss exceeds ~50x its 5-year
-- median is a provenance question, not a spike to draw — 203 of 2023's 313.6
-- km² over XSA sits in a block that recorded ~0.2 km²/yr for 22 years.
ALTER TABLE deforestation_events ADD COLUMN area_method TEXT;
ALTER TABLE deforestation_events ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0;

-- Backfill the discriminator from what each row already provably is: the
-- writer's feature-id prefix, falling back to the Hansen/alerts cutover year.
-- Derived, never typed (invariant 2).
UPDATE deforestation_events SET area_method =
  CASE WHEN polygon_ids LIKE 'deforest_gfw_%' THEN 'gfw_alert_count'
       WHEN year >= 2024 THEN 'gfw_alert_count'
       ELSE 'hansen_canopy_loss' END
WHERE area_method IS NULL;

-- Every settlement row predating this migration carries MASK area in area_m2
-- and a 200 people/ha constant in population_est.  They are not deleted here:
-- a wrong number and a missing one are both bad, and blanking 14,504 rows the
-- moment the migration runs would empty every park in the app for as long as
-- the backfill takes.  Instead each row states what it is, and the read path
-- refuses to serve a population whose source is not a raster
-- (settlementPopulationSQL, srv/settlement_provenance.go) — reversible, and it
-- keeps the extent, which was always honest under a different name.
--
-- `population_source = 'legacy_density_200_per_ha'` is therefore a work queue
-- as well as a label: scripts/backfill_settlement_surface.py re-derives one
-- park per run and overwrites it with the GHS_POP product id.
UPDATE park_settlements
   SET extent_m2 = area_m2,
       area_source = 'ghsl_mask_extent',
       population_source = 'legacy_density_200_per_ha'
 WHERE extent_m2 IS NULL AND population_source IS NULL
   AND polygon_ids IS NOT NULL AND polygon_ids != '';

-- Rows with no polygon_ids were never observed in GHSL built-up data at all:
-- they are retired pit/turbidity detector output (srv/mining_flag.go,
-- AGENTS.md invariant 5).  Their area came from a different detector entirely,
-- so they get their own label rather than being swept in with GHSL legacy.
UPDATE park_settlements
   SET population_source = 'retired_detector',
       area_source = 'retired_detector'
 WHERE population_source IS NULL
   AND (polygon_ids IS NULL OR polygon_ids = '');
