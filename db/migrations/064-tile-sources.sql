-- 064: private tile sources, per-login map preferences, encrypted shared files.
--
-- WHY. The offline-tile builder was rewritten in 2026-09 to copy only imagery
-- whose licence permits redistribution, which left it with one source. What
-- a user may do PRIVATELY is a different question from what this server may
-- PUBLISH: QGIS lets anyone paste an XYZ URL and cache it for their own use,
-- and so does this table. A source belongs to the login that added it, its
-- URL is stored encrypted (the operator of this server is not a party to a
-- user's agreement with an imagery provider and should not be able to read
-- it out of a backup), and any MBTiles built from it is stored encrypted and
-- visible only to that login (shared_files.enc). Nothing in this repository
-- names a provider; a source is an opaque URL supplied at runtime.
CREATE TABLE IF NOT EXISTS tile_sources (
    id          TEXT PRIMARY KEY,           -- random token
    pwd_ref     TEXT NOT NULL,              -- owner login (principalRef)
    name        TEXT NOT NULL,              -- the label the user typed
    url_enc     BLOB NOT NULL,              -- AES-GCM(master key) of the {z}/{x}/{y} template
    host        TEXT NOT NULL,              -- hostname only, for the licence check and the list
    scheme      TEXT NOT NULL DEFAULT 'xyz',-- 'xyz' | 'tms' | 'quadkey'
    min_zoom    INTEGER NOT NULL DEFAULT 0,
    max_zoom    INTEGER NOT NULL DEFAULT 19,
    tile_size   INTEGER NOT NULL DEFAULT 256,
    attribution TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'satellite', -- 'satellite' | 'base' | 'overlay'
    verified_at TEXT,                       -- last time a probe tile came back as an image
    probe_bytes INTEGER NOT NULL DEFAULT 0, -- size of that probe tile (seeds the MBTiles estimate)
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tile_sources_ref ON tile_sources(pwd_ref);

-- One row per login: which basemap the globe opens on and which one the
-- "satellite" gesture (hint toast, ?basemap=satellite) means. NULL = app default.
CREATE TABLE IF NOT EXISTS map_prefs (
    pwd_ref           TEXT PRIMARY KEY,
    default_base      TEXT,
    default_satellite TEXT,
    updated_at        TEXT NOT NULL
);

-- Shared files are encrypted at rest from this migration on. enc=0 rows are
-- files uploaded before it and are served as they are; enc=1 rows carry a
-- per-file nonce in the same table and are decrypted on the way out.
-- max_downloads: 0 = unlimited (uploads); MBTiles builds set 3.
ALTER TABLE shared_files ADD COLUMN enc INTEGER NOT NULL DEFAULT 0;
ALTER TABLE shared_files ADD COLUMN nonce BLOB;
ALTER TABLE shared_files ADD COLUMN max_downloads INTEGER NOT NULL DEFAULT 0;
ALTER TABLE shared_files ADD COLUMN kind TEXT NOT NULL DEFAULT 'upload';   -- 'upload' | 'mbtiles'
ALTER TABLE shared_files ADD COLUMN private INTEGER NOT NULL DEFAULT 0;    -- 1: download is owner/guest-key only
