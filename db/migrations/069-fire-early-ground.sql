-- 069: fire_early_ground — "traditional early-burn ground": the cells of an
-- area's front grid that burn ahead of their surroundings season after season.
--
-- WHY. Whole-season fire density predicts where the herds WILL BE (Gini
-- +0.29 over uniform-on-burnable); it is weak on where the season ENTERS —
-- the leading edge 10–60 d ahead of the front, where the leaflet flights go.
-- The recur rule (scripts/fire_vanguard/recur.py, prototyped on the XSA)
-- scores that target best in scripts/eval_fire_baseline.py: a 2.5 km cell
-- whose FIRST burn came >= 15 d before the local season front in >= 40 % of
-- the complete seasons held (docs/agents/fire.md "Early-burn ground").
--
-- One row per area, written only by scripts/fire_front.py (the same writer
-- as fire_season_front; same 04:40 --rotate cron). An area with fewer than
-- EARLY_MIN_SEASONS complete seasons gets a row with cells_json NULL and a
-- status in stats_json — an explicit "insufficient", never an empty success.
CREATE TABLE IF NOT EXISTS fire_early_ground (
    area_id         TEXT PRIMARY KEY,
    rule            TEXT NOT NULL,      -- 'recur'
    ahead_days      INTEGER NOT NULL,   -- 15: first burn this many days before the local front
    min_share       REAL NOT NULL,      -- 0.40: share of held seasons early
    seasons_held    INTEGER NOT NULL,   -- complete seasons with a front that entered the rule
    seasons_json    TEXT NOT NULL,      -- their labels, oldest first
    res REAL, x0 REAL, y0 REAL, nx INTEGER, ny INTEGER,
    cells_json      TEXT,               -- [[ix, iy, early, held, median_days_ahead, month_mode, usual_front_dos], ...]
    first_burn_json TEXT,               -- {season: [first-burn day-of-season per cell, or null]} — every season held, live too
    stats_json      TEXT,
    computed_at     TEXT NOT NULL
);
