-- 045: narrative_cache — the deforestation narrative's answer to the same
-- problem fire_narrative_cache solved in 2026-07.
--
-- HandleAPIDeforestationNarrative enriches EVERY event with per-event nearby
-- places, rivers and a river/road lookup: ~21 ms of queries per row. A park has
-- a few hundred events (CAF_Chinko 273 -> 1.0 s), so it was never visibly slow.
-- XSA_Study_Area has 7,815 -> 2 m 27 s, past the 120 s WriteTimeout, so the AOI
-- popup's deforestation section returned nothing at all. The AOI did not break
-- the handler; it made an existing O(events) cost visible.
--
-- Deliberately generic (kind) and deliberately NOT park-only: an AOI id is a
-- park_id in every park-shaped table, so one cache serves both and there is one
-- code path to keep correct.
--
-- source_rev makes this self-invalidating: it is a cheap fingerprint of the
-- rows the narrative is derived from (COUNT + MAX(id)), so any rebuild —
-- python, the AOI runner, a manual reclassify — invalidates the entry without
-- having to know this table exists. That is the opposite of the fire cache's
-- Single Writer Rule, and on purpose: the fire cache holds v5 hash feature_ids
-- that only the python builder can mint, whereas this holds a pure function of
-- deforestation_events that any reader can recompute.
CREATE TABLE IF NOT EXISTS narrative_cache (
    park_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    params      TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL,
    source_rev  TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (park_id, kind, params)
);
