-- Add password_hash to users
ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT '';

-- Sessions table for login state
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- Record execution of this migration
INSERT OR IGNORE INTO migrations (migration_number, migration_name)
VALUES (002, '002-auth');
