-- 067: fire_vanguard_kf — which areas carry Kalman seed-ahead vanguard chains.
--
-- WHY. The plain trajectories run over the whole field, so a chain that
-- began ahead of the season front is cut the moment the season's own fires
-- arrive around it (43 % of vanguard chains ended that way, measured on XSA
-- 2024/25). scripts/fire_vanguard_kf.py tracks the sparse field ahead of and
-- just behind the front with a Kalman filter, seeding tracks ONLY ahead of
-- the front and following them into the arriving season: km per chain 76 vs
-- 48, fires in ≥150 km chains 24,567 vs 9,714 against the day-shuffled null
-- (skill 0.60; docs/agents/fire.md § Kalman seed-ahead chains).
--
-- The chains are feature_geometries rows with feature_type='fire_vanguard'
-- and vanguard=1 (so idx_fg_vanguard covers them). This table says, per area,
-- that they exist: for every area listed here the vanguard layer, the
-- season summary and the report draw and count THESE chains; elsewhere the
-- plain groups flagged vanguard=1 stand in (srv/fire_season.go vanguardRowsSQL).
-- Written only by scripts/fire_vanguard_kf.py.
CREATE TABLE IF NOT EXISTS fire_vanguard_kf (
    area_id         TEXT PRIMARY KEY,
    computed_at     TEXT NOT NULL,
    tracker_version TEXT NOT NULL,   -- 'kf1'; a rotation redoes areas whose version is older
    seasons_json    TEXT NOT NULL,
    chains          INTEGER NOT NULL,
    stats_json      TEXT
);
