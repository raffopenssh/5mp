-- Extend park_documents table for management plans and reports
-- Add year field for document publication/validity year
-- Add summary field for brief document description

ALTER TABLE park_documents ADD COLUMN year INTEGER;
ALTER TABLE park_documents ADD COLUMN summary TEXT;

-- Create index on category for efficient filtering
CREATE INDEX IF NOT EXISTS idx_park_documents_category ON park_documents(category);
CREATE INDEX IF NOT EXISTS idx_park_documents_year ON park_documents(year);
