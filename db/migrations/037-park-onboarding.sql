-- 037: on-the-fly park onboarding requests.
-- Created when a logged-in (non-test) user searches for a park we don't have
-- but that matches WDPA. Processed nightly by scripts/onboard_park.py.
CREATE TABLE IF NOT EXISTS park_onboarding_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wdpa_id INTEGER NOT NULL UNIQUE,
    name TEXT NOT NULL,
    country TEXT,
    country_code TEXT,
    park_id TEXT,              -- assigned {ISO3}_{Name} id once onboarded
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|processing|ready|failed
    detail TEXT,               -- progress/error notes from onboard_park.py
    env TEXT NOT NULL DEFAULT 'prod',
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_por_status ON park_onboarding_requests(status);
