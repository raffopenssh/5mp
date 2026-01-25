-- Park documentation checklist tracking
CREATE TABLE IF NOT EXISTS park_checklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pa_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending, in_progress, complete, not_applicable
    notes TEXT,
    document_url TEXT,
    updated_by TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pa_id, item_id)
);

CREATE INDEX IF NOT EXISTS idx_park_checklist_pa ON park_checklist(pa_id);
CREATE INDEX IF NOT EXISTS idx_park_checklist_status ON park_checklist(status);

-- Park documents storage
CREATE TABLE IF NOT EXISTS park_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pa_id TEXT NOT NULL,
    category TEXT NOT NULL,
    item_id TEXT,
    title TEXT NOT NULL,
    description TEXT,
    file_url TEXT,
    file_type TEXT,
    uploaded_by TEXT,
    uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_park_documents_pa ON park_documents(pa_id);
