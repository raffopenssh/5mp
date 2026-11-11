-- Learner evidence as events (srv/learned_events.go).
--
-- learned_airstrips / learned_places / learned_roads kept counters
-- (landing_count, visit_count, match_count) that a re-delivered autofetch
-- window incremented twice and that nothing could recompute. Every landing,
-- take-off, stop and road traversal is now one row here, de-duplicated on
-- (tracker, kind, time), and the feature rows are aggregates over it.
--
-- Like the tables it feeds, this has no env column: it is written only from
-- client-tenant uploads (envFeedsLearning) and served only to the client
-- tenant (srv/test_env_guard.go).
CREATE TABLE IF NOT EXISTS learned_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,            -- landing | takeoff | stop | traversal | coverage (vehicle track time already read)
    tracker     TEXT NOT NULL DEFAULT '', -- opaque device identity (hash)
    t_start     TEXT NOT NULL,            -- RFC3339 UTC
    t_end       TEXT NOT NULL,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    heading_deg REAL,                     -- NULL = not measured
    subtype     TEXT NOT NULL DEFAULT '', -- fixed_wing | rotor_wing | foot | vehicle | ''
    journey     TEXT NOT NULL DEFAULT '',
    dwell_min   REAL,
    roll_json   TEXT,                     -- ground-run fixes [[lon,lat,kmh],...]
    feature_id  INTEGER NOT NULL,         -- learned_airstrips / _places / _roads id, by kind
    upload_id   INTEGER,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_learned_events_dedupe ON learned_events(park_id, kind, tracker, t_start);
CREATE INDEX IF NOT EXISTS idx_learned_events_feature ON learned_events(kind, feature_id);

-- Evidence, score and the position the feature was first seeded at (the
-- displayed lat/lon is re-derived from the events and may move; the seed,
-- which decides which events belong to the feature, must not).
ALTER TABLE learned_airstrips ADD COLUMN kind TEXT NOT NULL DEFAULT 'airfield';
ALTER TABLE learned_airstrips ADD COLUMN seed_lat REAL;
ALTER TABLE learned_airstrips ADD COLUMN seed_lon REAL;
ALTER TABLE learned_airstrips ADD COLUMN score REAL;
ALTER TABLE learned_airstrips ADD COLUMN evidence_json TEXT;
ALTER TABLE learned_airstrips ADD COLUMN status_reason TEXT;
ALTER TABLE learned_airstrips ADD COLUMN line_geojson TEXT;
ALTER TABLE learned_airstrips ADD COLUMN last_seen TEXT;

ALTER TABLE learned_places ADD COLUMN seed_lat REAL;
ALTER TABLE learned_places ADD COLUMN seed_lon REAL;
ALTER TABLE learned_places ADD COLUMN score REAL;
ALTER TABLE learned_places ADD COLUMN evidence_json TEXT;
ALTER TABLE learned_places ADD COLUMN status_reason TEXT;
ALTER TABLE learned_places ADD COLUMN last_seen TEXT;

ALTER TABLE learned_roads ADD COLUMN score REAL;
ALTER TABLE learned_roads ADD COLUMN evidence_json TEXT;
ALTER TABLE learned_roads ADD COLUMN status_reason TEXT;
ALTER TABLE learned_roads ADD COLUMN last_seen TEXT;
