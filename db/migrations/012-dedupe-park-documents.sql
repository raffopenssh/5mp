-- Deduplicate park_documents table and add unique constraint
-- This fixes an issue where the seed migration could be run multiple times

-- Remove duplicate documents, keeping only the row with the lowest id for each (pa_id, title) pair
DELETE FROM park_documents 
WHERE id NOT IN (
    SELECT MIN(id) 
    FROM park_documents 
    GROUP BY pa_id, title
);

-- Add unique constraint to prevent future duplicates
CREATE UNIQUE INDEX IF NOT EXISTS idx_park_documents_unique_pa_title ON park_documents(pa_id, title);
