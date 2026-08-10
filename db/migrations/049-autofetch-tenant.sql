-- Automated-fetch subscriptions belong to the tenant that created them.
--
-- An EarthRanger/PAMDAS subscription is not shared infrastructure: it names a
-- client's own server, their username, the parks they operate in, and it feeds
-- patrol tracks into that tenant's effort pixels (srv/tenant.go). Listing every
-- subscription to every access password leaked all four.
--
-- Existing rows are the client tenant's ('prod'), which is where their fetched
-- tracks already live.
ALTER TABLE autofetch_sources ADD COLUMN env TEXT NOT NULL DEFAULT 'prod';
CREATE INDEX IF NOT EXISTS idx_autofetch_env ON autofetch_sources(env);
