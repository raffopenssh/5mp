-- name: InsertPublication :exec
INSERT OR IGNORE INTO pa_publications (pa_id, openalex_id, title, authors, year, doi, url, abstract, cited_by_count)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);

-- name: GetPublicationsByPA :many
SELECT * FROM pa_publications WHERE pa_id = ? ORDER BY year DESC, cited_by_count DESC LIMIT 50;

-- name: GetPublicationCountByPA :one
SELECT COUNT(*) as count FROM pa_publications WHERE pa_id = ?;

-- name: UpsertPAPublicationSync :exec
INSERT INTO pa_publication_sync (pa_id, last_sync, result_count)
VALUES (?, CURRENT_TIMESTAMP, ?)
ON CONFLICT(pa_id) DO UPDATE SET last_sync = CURRENT_TIMESTAMP, result_count = excluded.result_count;

-- name: GetPAPublicationSync :one
SELECT * FROM pa_publication_sync WHERE pa_id = ?;

-- name: GetPAsNeedingPublicationSync :many
SELECT pa_id FROM pa_publication_sync 
WHERE last_sync < datetime('now', '-7 days')
ORDER BY last_sync ASC
LIMIT ?;

-- name: GetAllSyncedPAIDs :many
SELECT pa_id FROM pa_publication_sync;
