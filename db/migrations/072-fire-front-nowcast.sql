-- 072: fire_front_nowcast — the LIVE lead's basis where this season's front
-- has not arrived yet.
--
-- WHY. A lead is (front day − burn day). Where the front has not arrived
-- that needs a date in the future; `usual` (median front of previous
-- seasons, fire_season_front.usual) stood in and, replayed as-if-live, is
-- off by a median 13 d — more than the 10 d vanguard threshold. The nowcast
-- aligns seasons by PROGRESS: the causal sweep that dates the front (20 % of
-- the burnable land within 60 km burned) also dates the earlier stages
-- (1 / 2.5 / 5 / 10 %), and previous seasons say how long stage → front
-- takes. scripts/eval_front_nowcast.py: MAE 13.0 → 6.7 d, live vanguard F1
-- 0.67 → 0.82 (docs/agents/fire.md "Live lead: the staged nowcast").
--
-- One row per area, written only by scripts/fire_front.py. `usual` is
-- untouched and still computed: fire_front.LIVE_LEAD = 'usual' restores the
-- old rule, and groups tagged on the nowcast carry `lead_start_usual`.
-- Both blobs are zlib-compressed (~1 byte per cell); nothing per season.
CREATE TABLE IF NOT EXISTS fire_front_nowcast (
    area_id        TEXT PRIMARY KEY,
    start_month    INTEGER NOT NULL,
    fractions_json TEXT NOT NULL,   -- the stages, e.g. [0.01, 0.025, 0.05, 0.1]
    seasons_json   TEXT NOT NULL,   -- complete seasons behind `rem`, oldest first
    res REAL NOT NULL, x0 REAL NOT NULL, y0 REAL NOT NULL, nx INTEGER NOT NULL, ny INTEGER NOT NULL,
    rem            BLOB,            -- zlib(uint8 [stages, ny, nx]): median days stage → front; 255 = none
    season         TEXT,            -- the live season `nowcast` describes
    as_of          TEXT,            -- newest day it saw
    nowcast        BLOB,            -- zlib(int16 [ny, nx]): expected front day-of-season; -1 = none
    stats_json     TEXT,            -- status ok | insufficient (+ reason)
    computed_at    TEXT NOT NULL
);
