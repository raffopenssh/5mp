-- Fire group alerts for real-time monitoring
-- Tracks when fire groups enter, stay in, or leave protected areas

CREATE TABLE IF NOT EXISTS fire_group_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    group_name TEXT NOT NULL,  -- NATO phonetic name (Alpha, Bravo, etc.)
    alert_type TEXT NOT NULL,  -- 'entered', 'active_inside', 'left'
    first_detected_at TIMESTAMP NOT NULL,
    last_updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    left_at TIMESTAMP,  -- When group left the park (NULL if still inside)
    fire_count INTEGER DEFAULT 0,
    days_active INTEGER DEFAULT 1,
    centroid_lat REAL,
    centroid_lon REAL,
    latest_lat REAL,
    latest_lon REAL,
    movement_direction TEXT,  -- 'north', 'south', 'east', 'west', 'stationary'
    is_dismissed BOOLEAN DEFAULT FALSE,  -- User can dismiss alerts
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(park_id, group_name, first_detected_at)
);

CREATE INDEX IF NOT EXISTS idx_fire_group_alerts_park ON fire_group_alerts(park_id);
CREATE INDEX IF NOT EXISTS idx_fire_group_alerts_type ON fire_group_alerts(alert_type);
CREATE INDEX IF NOT EXISTS idx_fire_group_alerts_active ON fire_group_alerts(left_at) WHERE left_at IS NULL;
