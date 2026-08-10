-- 047: geopackage_jobs — the GIS export ("GeoPackage" in the download menu).
--
-- A GeoPackage for one area is every layer the app holds, whole: for
-- XSA_Study_Area that is ~3.2M fire detections plus 38k trajectories, minutes
-- of work and hundreds of MB. That cannot run inside a request (WriteTimeout is
-- 120 s), so it is a job with a progress notification, exactly like the MBTiles
-- export and park onboarding.
--
-- Unlike the MBTiles queue this one is a CACHE, not a spool:
--
--  * The job is keyed by cache_key = (area, window, effort, env). A second
--    request for the same key returns the finished file instead of rebuilding
--    it — the file is a pure function of the key and the database, so two users
--    asking the same question should not each wait five minutes.
--  * Rows and files live 21 days (expires_at), then a sweeper deletes both.
--    Long enough that a link mailed to a colleague still works next week, short
--    enough that data/gpkg_output does not grow without bound. A refreshed
--    ingest is picked up by ?refresh=1, which forces a rebuild under the same
--    key.
--  * MBTiles keeps its file for 2 hours and holds jobs in memory only, so a
--    restart loses them. Here the table IS the state: the download link in a
--    notification has to survive a deploy, or the notification is a lie.
--
-- principal_id scopes visibility the same way AOIs do: a job over a private AOI
-- must not be listed or downloadable by another tenant. It is nullable because
-- a park export is public within the app (same rule as /features).
CREATE TABLE IF NOT EXISTS geopackage_jobs (
    id           TEXT PRIMARY KEY,            -- short random token, also the URL
    cache_key    TEXT NOT NULL,               -- area|from|to|effort|env
    area_id      TEXT NOT NULL,
    area_name    TEXT NOT NULL,
    is_aoi       INTEGER NOT NULL DEFAULT 0,
    principal_id INTEGER,                     -- owner, for AOI jobs
    env          TEXT NOT NULL DEFAULT 'prod',
    from_date    TEXT NOT NULL DEFAULT '',
    to_date      TEXT NOT NULL DEFAULT '',
    effort       INTEGER NOT NULL DEFAULT 0,
    state        TEXT NOT NULL DEFAULT 'pending',  -- pending|running|ready|failed
    progress     REAL NOT NULL DEFAULT 0,
    step         TEXT NOT NULL DEFAULT '',
    file_path    TEXT NOT NULL DEFAULT '',
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    layers_json  TEXT NOT NULL DEFAULT '[]',  -- [{name,count}] — the receipt
    error        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    expires_at   TEXT,
    downloads    INTEGER NOT NULL DEFAULT 0,
    last_download_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_gpkg_cache ON geopackage_jobs(cache_key, state);
CREATE INDEX IF NOT EXISTS idx_gpkg_area ON geopackage_jobs(area_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_gpkg_expiry ON geopackage_jobs(expires_at);
