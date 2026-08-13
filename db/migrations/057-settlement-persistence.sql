-- 057: settlement persistence, measured from GHSL back-epochs.
--
-- WHY A NEW COLUMN AND NOT settlement_type. settlement_type's two words
-- ('temporary','permanent') were never the output of any measurement: the only
-- rules emitting 'temporary' required an area below the ingest floor, so the
-- column said 'permanent' everywhere and was retired to NULL (F12,
-- docs/agents/settlements.md). What CAN be measured is inter-epoch
-- persistence: GHS_BUILT_S R2023A publishes the same 100 m Mollweide grid for
-- epochs back to 1975, so "was this ground built in 2000?" is a raster read,
-- not a guess. That measurement answers a different question than
-- temporary/permanent (a 2021 village is RECENT, not seasonal), so it gets a
-- column whose words claim only what the rasters support:
--
--     permanent     built surface in E2000 >= 25% of today's (E2030) surface
--     established   >= 25% in E2015 but not in E2000
--     recent        below 25% in both back-epochs
--     NULL          unmeasured; persistence_source says why ('tile_missing'
--                   when an epoch tile could not be fetched — a pixel absent
--                   from an old epoch and a tile absent from the download are
--                   different states, AGENTS.md invariant 1)
--
-- persistence_source names the instrument ('ghsl_E2000+E2015'), per invariant
-- 5: a derived flag must name its input. The per-cluster epoch surfaces ride
-- along so the 25% rule is auditable and re-derivable without re-reading the
-- rasters. Writer: scripts/ghsl_epochs.py (called by
-- scripts/backfill_settlement_surface.py); stamp: data/ghsl_epoch_state.json.
ALTER TABLE park_settlements ADD COLUMN persistence TEXT
    CHECK(persistence IN ('permanent', 'established', 'recent'));
ALTER TABLE park_settlements ADD COLUMN persistence_source TEXT;
-- Built surface (m², Σ of the fractional BUILT_S raster) over this cluster's
-- CURRENT mask pixels, in each back-epoch. Same pixels the E2030 surface uses,
-- so the three numbers are comparable by construction.
ALTER TABLE park_settlements ADD COLUMN surface_e2000_m2 REAL;
ALTER TABLE park_settlements ADD COLUMN surface_e2015_m2 REAL;
