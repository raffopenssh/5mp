-- Park-level fire group infraction tracking
-- Tracks fire groups that entered, transited, or stopped inside protected areas

CREATE TABLE IF NOT EXISTS park_group_infractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    total_groups INTEGER DEFAULT 0,          -- Total fire groups that entered the park
    groups_stopped_inside INTEGER DEFAULT 0,  -- Groups that stopped burning inside (rangers may have contacted)
    groups_transited INTEGER DEFAULT 0,       -- Groups that passed through without stopping
    avg_days_burning REAL DEFAULT 0,          -- Average days each group was burning inside
    analyzed_at TEXT,
    UNIQUE(park_id, year)
);

CREATE INDEX IF NOT EXISTS idx_park_group_infractions_park ON park_group_infractions(park_id);
CREATE INDEX IF NOT EXISTS idx_park_group_infractions_year ON park_group_infractions(year);
