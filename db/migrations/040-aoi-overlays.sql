-- 040: Areas of interest (AOI) — the drawn bbox promoted to a first-class
-- object: an arbitrary polygon + a fixed analysis window + an owner, with
-- data fetched *for it* over days by a queue-grinding cron.
--
-- Design rules (docs/PLAN_AOI_OVERLAY.md §0):
--  1. An AOI is NOT a park. It never enters keystones_with_boundaries.json and
--     is never a fire_detections.protected_area_id, so park_assigner cannot
--     steal detections from CAF_Chinko / SSD_Southern / COD_Bili-Uere /
--     COD_Garamba. An AOI selects fires by polygon, via aoi_fires.
--  2. Ingest is keyed by geography, not by owner: a private AOI may cause data
--     to be fetched, but the raw rows/tiles land in the shared stores. Only the
--     derived AOI-shaped artefacts are private.
--  3. The work is a resumable queue (aoi_datasets), ground down by a cron.

CREATE TABLE IF NOT EXISTS aois (
  id          TEXT PRIMARY KEY,       -- 'XSA_Study_Area', later 'aoi_<nanoid>'
  name        TEXT NOT NULL,
  geometry    TEXT NOT NULL,          -- GeoJSON Polygon/MultiPolygon
  bbox_minx REAL, bbox_miny REAL, bbox_maxx REAL, bbox_maxy REAL,
  area_km2    REAL,
  from_date   TEXT,                   -- analysis window; NULL = all available
  to_date     TEXT,
  owner_principal_id INTEGER,
  visibility  TEXT NOT NULL DEFAULT 'private',  -- 'private' | 'shared' | 'public'
  state       TEXT NOT NULL DEFAULT 'pending',  -- pending|ingesting|ready|failed
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  notes       TEXT
);
CREATE INDEX IF NOT EXISTS idx_aois_owner ON aois(owner_principal_id);

-- Who may see what. Generic on purpose: a principal is a password today, a
-- user or an NGO tomorrow, with no migration. Passwords are stored as a
-- sha256 prefix, never as the secret.
CREATE TABLE IF NOT EXISTS principals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,      -- 'password' | 'user' | 'org'
  ref  TEXT NOT NULL,      -- sha256(pwd)[:16] | user_id | org slug
  label TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(kind, ref)
);

CREATE TABLE IF NOT EXISTS aoi_grants (
  aoi_id TEXT NOT NULL REFERENCES aois(id) ON DELETE CASCADE,
  principal_id INTEGER NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
  scope TEXT NOT NULL DEFAULT 'view',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (aoi_id, principal_id, scope)
);

-- The work queue. One row per (aoi, dataset); scripts/aoi_runner.py grinds
-- these down under a lease, committing cursor+units_done after every unit so
-- a kill at any moment resumes cleanly.
CREATE TABLE IF NOT EXISTS aoi_datasets (
  aoi_id  TEXT NOT NULL REFERENCES aois(id) ON DELETE CASCADE,
  dataset TEXT NOT NULL,   -- fire_gap|fire_v5|gfw|ghsl|osm|hydro|gsw|basin|deforestation
  enabled INTEGER NOT NULL DEFAULT 1,
  priority INTEGER NOT NULL DEFAULT 100,      -- lower runs first
  state TEXT NOT NULL DEFAULT 'pending',      -- pending|running|done|failed|blocked
  depends_on TEXT,                            -- e.g. fire_v5 depends on fire_gap
  cursor TEXT,             -- JSON work queue + index; resumable mid-unit
  units_total INTEGER, units_done INTEGER DEFAULT 0,
  coverage REAL,           -- 0..1, drives the coverage table in the popup
  lease_owner TEXT, lease_until TIMESTAMP,
  last_run_at TIMESTAMP, next_run_at TIMESTAMP, detail TEXT,
  PRIMARY KEY (aoi_id, dataset)
);
CREATE INDEX IF NOT EXISTS idx_aoi_ds_state
  ON aoi_datasets(state, priority, next_run_at);

-- Point-in-polygon over millions of fire_detections rows is expensive; cache
-- the membership once and refresh incrementally.
CREATE TABLE IF NOT EXISTS aoi_fires (
  aoi_id TEXT NOT NULL, fire_id INTEGER NOT NULL,
  PRIMARY KEY (aoi_id, fire_id)
) WITHOUT ROWID;
