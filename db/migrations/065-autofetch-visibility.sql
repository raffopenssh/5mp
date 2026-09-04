-- Per-source contribution visibility for automated-fetch subscriptions.
--
-- A subscription used to be owned by a TENANT (env, migration 049) and its
-- fetched tracks were filed straight into that tenant's patrol pixels, so
-- every login sharing the tenant saw them and nobody else could. Now each
-- source has an owner (a login, by principal ref), a visibility rule and its
-- own data env: fetched tracks land in `data_env` ('af<id>'), and the read
-- path (PatrolEnvs, srv/guest.go) widens a caller's env set with the sources
-- that are visible to it -- its own, those shared with everyone, or those it
-- was granted by ref. Visibility is therefore an ACL on an env, not a copy of
-- the data; changing it never rewrites a pixel.
--
-- Legacy rows (empty owner_ref) are finished by migrateAutofetchLegacy at
-- startup, which needs the configured passwords and so cannot live in SQL.
ALTER TABLE autofetch_sources ADD COLUMN owner_ref TEXT NOT NULL DEFAULT '';
ALTER TABLE autofetch_sources ADD COLUMN visibility TEXT NOT NULL DEFAULT 'owner'
    CHECK (visibility IN ('owner','selected','all'));
ALTER TABLE autofetch_sources ADD COLUMN data_env TEXT NOT NULL DEFAULT '';
ALTER TABLE autofetch_sources ADD COLUMN title TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS autofetch_viewers (
    source_id     INTEGER NOT NULL REFERENCES autofetch_sources(id) ON DELETE CASCADE,
    principal_ref TEXT    NOT NULL,   -- sha256(pwd)[:16], never the password
    label         TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_id, principal_ref)
);
CREATE INDEX IF NOT EXISTS idx_autofetch_viewers_ref ON autofetch_viewers(principal_ref);

-- Upload logs are the same patrol data by another name (invariant: the
-- learned-feature gate, srv/test_env_guard.go). They were gated only by
-- "is this the client tenant", so every prod login saw every prod upload
-- and an autofetch env had no logs at all. Tag each log with its upload's
-- env so the list, the stats and the detail view scope by PatrolEnvs exactly
-- as the pixels and the new_upload notifications do.
ALTER TABLE gpx_upload_logs ADD COLUMN env TEXT NOT NULL DEFAULT 'prod';
UPDATE gpx_upload_logs SET env = COALESCE(
    (SELECT u.env FROM gpx_uploads u WHERE u.id = gpx_upload_logs.upload_id), env);
CREATE INDEX IF NOT EXISTS idx_gpx_upload_logs_env ON gpx_upload_logs(env);
