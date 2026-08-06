-- 042: AOI versions — an edit creates a new version, it never mutates.
--
-- Editing an AOI in place would be wrong for a reason specific to this object:
-- an AOI is not a saved view, it is *a question plus the days of ingest that
-- answered it*. Its derived rows (fire trajectories, settlements, deforestation
-- events) are keyed by its id and were computed for one polygon over one date
-- window. Change either and those rows silently become answers to a question
-- nobody asked — with no way to tell, because the id did not change.
--
-- So an edit forks: the old AOI is archived (state='archived', hidden from the
-- map and the default list) and a new row is created with the new geometry
-- and/or window, sharing a lineage. Nothing is deleted, so:
--
--   * a share link to the old version keeps resolving to the data it described
--   * "what did this look like before we extended the window" is answerable
--   * the expensive ingest of the old version is not thrown away, and where
--     the polygons overlap the new version's ingest hits warm caches anyway
--     (ingest is keyed by geography, not by owner — rule 2)
--
-- Users get archived versions back through search, scoped to what their
-- password can see. Versions are distinguished by their analysis window, which
-- is what actually differs between them in practice.

ALTER TABLE aois ADD COLUMN lineage_id TEXT;      -- shared by all versions; = the first version's id
ALTER TABLE aois ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE aois ADD COLUMN superseded_by TEXT;   -- id of the version that replaced this one
ALTER TABLE aois ADD COLUMN archived_at TIMESTAMP;

-- Existing AOIs are version 1 of their own lineage.
UPDATE aois SET lineage_id = id WHERE lineage_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_aois_lineage ON aois(lineage_id, version);
CREATE INDEX IF NOT EXISTS idx_aois_state ON aois(state);
