-- Publications for protected areas (from OpenAlex API)

CREATE TABLE IF NOT EXISTS pa_publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pa_id TEXT NOT NULL,          -- WDPA ID or internal PA ID
    openalex_id TEXT NOT NULL,    -- OpenAlex work ID for deduplication
    title TEXT NOT NULL,
    authors TEXT,                 -- JSON array of author names
    year INTEGER,
    doi TEXT,
    url TEXT,
    abstract TEXT,
    cited_by_count INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pa_id, openalex_id)
);

CREATE INDEX IF NOT EXISTS idx_publications_pa ON pa_publications(pa_id);
CREATE INDEX IF NOT EXISTS idx_publications_year ON pa_publications(year);

-- Track when we last searched for a PA's publications
CREATE TABLE IF NOT EXISTS pa_publication_sync (
    pa_id TEXT PRIMARY KEY,
    last_sync TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    result_count INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO migrations (migration_number, migration_name)
VALUES (003, '003-publications');
