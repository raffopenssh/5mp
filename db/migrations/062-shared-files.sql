-- 062: shared_files — user-uploaded files behind expiring guest links.
--
-- The ad-hoc way to hand a colleague a file this app didn't build (a styled
-- prediction GPKG, a PDF) was a bare `busybox httpd` on a spare port:
-- unauthenticated, unexpiring, invisible to the Sharing sheet. This table
-- gives such files the SAME lifecycle the GeoPackage exports already have:
-- 21-day TTL, a sweeper that deletes bytes+row together, retention pushed out
-- by any live guest link that points at the download, +30d extension.
--
-- Scoped by pwd_ref like short_links: a file belongs to the login that
-- uploaded it, another login gets 404 (an id is not an oracle). The download
-- itself is open to any authenticated session — the id is a random token and
-- the point of the row is to be shared — mirroring geopackage_jobs.
CREATE TABLE IF NOT EXISTS shared_files (
    id          TEXT PRIMARY KEY,          -- short random token, also the URL
    pwd_ref     TEXT NOT NULL,             -- owner login (principalRef), '' reachable by nobody
    env         TEXT NOT NULL DEFAULT 'prod',
    name        TEXT NOT NULL,             -- sanitised original filename
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,             -- sweeper deletes file+row after this
    downloads   INTEGER NOT NULL DEFAULT 0,
    last_download_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_shared_files_ref ON shared_files(pwd_ref);
CREATE INDEX IF NOT EXISTS idx_shared_files_expires ON shared_files(expires_at);
