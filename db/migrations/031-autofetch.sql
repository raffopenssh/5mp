-- Automated fetch configuration for external GPS tracking APIs (e.g. EarthRanger/PAMDAS)
-- Credentials are stored encrypted-at-rest and deleted on removal.
-- When disabled, the password is wiped; user must re-enter to reactivate.

CREATE TABLE IF NOT EXISTS autofetch_sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    api_type    TEXT NOT NULL,            -- e.g. 'earthranger'
    service_url TEXT NOT NULL,            -- e.g. 'https://nyerere.pamdas.org'
    username    TEXT NOT NULL,
    password    TEXT NOT NULL DEFAULT '', -- cleared on disable
    interval_h  INTEGER NOT NULL DEFAULT 24,
    enabled     INTEGER NOT NULL DEFAULT 1,
    park_names  TEXT NOT NULL DEFAULT '', -- comma-separated, discovered on add
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_run_at TIMESTAMP,
    last_status TEXT,                     -- 'ok', 'error: ...'
    last_points INTEGER DEFAULT 0
);
