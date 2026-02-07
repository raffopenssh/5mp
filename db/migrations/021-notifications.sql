-- Notifications for new publications, fire alerts, etc.
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    park_id TEXT NOT NULL,
    notification_type TEXT NOT NULL,  -- 'new_publication', 'fire_alert', etc.
    title TEXT NOT NULL,
    message TEXT,
    reference_id TEXT,  -- ID of related object (publication ID, fire group ID, etc.)
    reference_url TEXT,  -- Optional direct link
    is_read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_notifications_park ON notifications(park_id);
CREATE INDEX IF NOT EXISTS idx_notifications_type ON notifications(notification_type);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(is_read) WHERE is_read = 0;
CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC);
