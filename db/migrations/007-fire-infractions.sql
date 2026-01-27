-- Add infraction tracking to fire analysis

-- Create the park_fire_analysis table if it doesn't exist
CREATE TABLE IF NOT EXISTS park_fire_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL UNIQUE,
    analyzed_at TEXT,
    total_fires INTEGER DEFAULT 0,
    total_infractions INTEGER DEFAULT 0,
    infraction_rate REAL DEFAULT 0,
    peak_infraction_day TEXT,
    peak_infraction_count INTEGER DEFAULT 0,
    net_south_km REAL DEFAULT 0,
    avg_daily_movement_km REAL DEFAULT 0,
    monthly_stats_json TEXT,
    groups_stopped_inside INTEGER DEFAULT 0,
    groups_transited_through INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_park_fire_analysis_park ON park_fire_analysis(park_id);
