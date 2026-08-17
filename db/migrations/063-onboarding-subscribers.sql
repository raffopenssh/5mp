-- 063: scope park onboarding requests to the login that made them.
--
-- A park is GLOBAL data (docs/agents/auth.md: onboarding is not tenant-scoped
-- on the WORK side), but the REQUEST is the caller's: who asked for a park,
-- and who may cancel it, must not leak across tenants. Same pattern as
-- short_links/shared_files: pwd_ref = principalRef(password), never the
-- credential itself; rows are shared per wdpa_id so the ingest runs once no
-- matter how many logins ask (stay efficient), and each subscriber gets the
-- lifecycle notifications in their own env.
--
-- Existing park_onboarding_requests rows predate scoping and get no
-- subscriber row: like legacy short_links with empty pwd_ref, they are
-- reachable by nobody rather than by everybody.
CREATE TABLE IF NOT EXISTS park_onboarding_subscribers (
    request_id   INTEGER NOT NULL REFERENCES park_onboarding_requests(id) ON DELETE CASCADE,
    pwd_ref      TEXT NOT NULL,             -- principalRef of the requesting login
    env          TEXT NOT NULL DEFAULT 'prod',  -- tenant for notification routing
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (request_id, pwd_ref)
);
CREATE INDEX IF NOT EXISTS idx_pos_ref ON park_onboarding_subscribers(pwd_ref);
