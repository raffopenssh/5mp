-- name: GetUser :one
SELECT * FROM users WHERE id = ?;

-- name: GetUserByEmail :one
SELECT * FROM users WHERE email = ?;

-- name: CreateUser :exec
INSERT INTO users (id, email, name, organization, organization_type, role, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?);

-- name: UpdateUserRole :exec
UPDATE users SET role = ?, approved_at = ?, approved_by = ? WHERE id = ?;

-- name: ListPendingUsers :many
SELECT * FROM users WHERE role = 'pending' ORDER BY created_at DESC;

-- name: ListApprovedUsers :many
SELECT * FROM users WHERE role IN ('approved', 'admin') ORDER BY created_at DESC;

-- name: ListAllUsers :many
SELECT * FROM users ORDER BY created_at DESC;

-- name: CreateGPXUpload :one
INSERT INTO gpx_uploads (user_id, filename, movement_type, protected_area_id, upload_date, start_time, end_time, total_distance_km, total_points, file_hash, processing_status, env)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
RETURNING id;

-- name: GetGPXUploadByHash :one
SELECT * FROM gpx_uploads WHERE file_hash = ? AND env = ?;

-- name: UpdateGPXUploadStatus :exec
UPDATE gpx_uploads SET processing_status = ?, error_message = ? WHERE id = ?;

-- name: GetGPXUpload :one
SELECT * FROM gpx_uploads WHERE id = ?;

-- name: ListGPXUploadsByUser :many
SELECT * FROM gpx_uploads WHERE user_id = ? ORDER BY upload_date DESC;

-- name: ListAllGPXUploads :many
SELECT * FROM gpx_uploads ORDER BY upload_date DESC LIMIT ? OFFSET ?;

-- name: GetOrCreateGridCell :one
INSERT INTO grid_cells (id, lat_center, lon_center, lat_min, lat_max, lon_min, lon_max)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET id = excluded.id
RETURNING *;

-- name: GetGridCell :one
SELECT * FROM grid_cells WHERE id = ?;

-- name: UpsertEffortData :exec
INSERT INTO effort_data (grid_cell_id, year, month, day, movement_type, total_distance_km, total_points, unique_uploads, protected_area_ids, avg_speed_kmh, avg_altitude_m, env)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(grid_cell_id, year, month, day, movement_type, env) DO UPDATE SET
    total_distance_km = effort_data.total_distance_km + excluded.total_distance_km,
    total_points = effort_data.total_points + excluded.total_points,
    unique_uploads = effort_data.unique_uploads + excluded.unique_uploads,
    avg_speed_kmh = CASE
        WHEN effort_data.avg_speed_kmh IS NULL THEN excluded.avg_speed_kmh
        WHEN excluded.avg_speed_kmh IS NULL THEN effort_data.avg_speed_kmh
        ELSE (effort_data.avg_speed_kmh * effort_data.total_distance_km + excluded.avg_speed_kmh * excluded.total_distance_km)
             / (effort_data.total_distance_km + excluded.total_distance_km)
        END,
    avg_altitude_m = CASE
        WHEN effort_data.avg_altitude_m IS NULL THEN excluded.avg_altitude_m
        WHEN excluded.avg_altitude_m IS NULL THEN effort_data.avg_altitude_m
        ELSE (effort_data.avg_altitude_m * effort_data.total_distance_km + excluded.avg_altitude_m * excluded.total_distance_km)
             / (effort_data.total_distance_km + excluded.total_distance_km)
        END;

-- name: GetEffortDataByBounds :many
SELECT e.*, g.lat_center, g.lon_center, g.lat_min, g.lat_max, g.lon_min, g.lon_max
FROM effort_data e
JOIN grid_cells g ON e.grid_cell_id = g.id
WHERE g.lat_center >= ? AND g.lat_center <= ?
  AND g.lon_center >= ? AND g.lon_center <= ?
  AND e.year >= ? AND e.year <= ?
  AND (? IS NULL OR e.month = ?)
  AND (? IS NULL OR e.movement_type = ?);

-- name: GetEffortDataByYear :many
SELECT e.*, g.lat_center, g.lon_center
FROM effort_data e
JOIN grid_cells g ON e.grid_cell_id = g.id
WHERE e.year = ? AND e.day IS NULL AND e.movement_type = 'all';

-- name: GetEffortDataByYearMonth :many
SELECT e.*, g.lat_center, g.lon_center
FROM effort_data e
JOIN grid_cells g ON e.grid_cell_id = g.id
WHERE e.year = ? AND e.month = ? AND e.day IS NULL AND e.movement_type = 'all';

-- name: GetGlobalStats :one
SELECT 
    COUNT(DISTINCT e.grid_cell_id) as active_pixels,
    COALESCE(SUM(e.total_distance_km), 0) as total_distance_km,
    COALESCE(SUM(e.total_points), 0) as total_points,
    COALESCE(SUM(e.unique_uploads), 0) as total_uploads
FROM effort_data e
WHERE e.year = ? AND e.day IS NULL AND e.movement_type = 'all';

-- name: CreateTrackPoint :exec
INSERT INTO track_points (upload_id, lat, lon, elevation, timestamp, grid_cell_id, movement_type, env)
VALUES (?, ?, ?, ?, ?, ?, ?, ?);

-- name: GetTrackPointsByUpload :many
SELECT * FROM track_points WHERE upload_id = ? ORDER BY timestamp;

-- name: CountActivePixels :one
SELECT COUNT(DISTINCT grid_cell_id) as count FROM effort_data WHERE year = ?;

-- name: GetTotalDistanceByYear :one
SELECT COALESCE(SUM(total_distance_km), 0) as total FROM effort_data WHERE year = ? AND movement_type = 'all' AND day IS NULL;

-- Session queries
-- name: CreateSession :exec
INSERT INTO sessions (id, user_id, created_at, expires_at)
VALUES (?, ?, ?, ?);

-- name: GetSession :one
SELECT s.*, u.email, u.name, u.role 
FROM sessions s
JOIN users u ON s.user_id = u.id
WHERE s.id = ? AND s.expires_at > CURRENT_TIMESTAMP;

-- name: DeleteSession :exec
DELETE FROM sessions WHERE id = ?;

-- name: DeleteExpiredSessions :exec
DELETE FROM sessions WHERE expires_at <= CURRENT_TIMESTAMP;

-- name: DeleteUserSessions :exec
DELETE FROM sessions WHERE user_id = ?;

-- name: UpdateUserPassword :exec
UPDATE users SET password_hash = ? WHERE id = ?;

-- name: ListGPXUploadsWithCoords :many
SELECT 
    u.id, u.user_id, u.filename, u.movement_type, u.protected_area_id,
    u.upload_date, u.start_time, u.end_time, u.total_distance_km, u.total_points,
    AVG(t.lat) as centroid_lat,
    AVG(t.lon) as centroid_lon
FROM gpx_uploads u
LEFT JOIN track_points t ON u.id = t.upload_id
GROUP BY u.id
ORDER BY u.upload_date DESC
LIMIT ? OFFSET ?;

-- Park checklist queries

-- name: GetParkChecklistItems :many
SELECT * FROM park_checklist WHERE pa_id = ? ORDER BY item_id;

-- name: UpsertChecklistItem :exec
INSERT INTO park_checklist (pa_id, item_id, status, notes, document_url, updated_by, updated_at)
VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
ON CONFLICT(pa_id, item_id) DO UPDATE SET
    status = excluded.status,
    notes = excluded.notes,
    document_url = excluded.document_url,
    updated_by = excluded.updated_by,
    updated_at = CURRENT_TIMESTAMP;

-- name: GetChecklistStats :one
SELECT 
    COUNT(*) as total,
    SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) as complete,
    SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress,
    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending
FROM park_checklist WHERE pa_id = ?;

-- name: GetParkDocuments :many
SELECT * FROM park_documents WHERE pa_id = ? ORDER BY uploaded_at DESC;

-- name: InsertParkDocument :exec
INSERT INTO park_documents (pa_id, category, item_id, title, description, file_url, file_type, uploaded_by)
VALUES (?, ?, ?, ?, ?, ?, ?, ?);

-- Subcell visits tracking for spatial coverage

INSERT INTO subcell_visits (grid_cell_id, subcell_id, year, month, visit_count)
VALUES (?, ?, ?, ?, 1)
ON CONFLICT(grid_cell_id, subcell_id, year, month) DO UPDATE SET
    visit_count = visit_count + 1;

SELECT COUNT(DISTINCT subcell_id) as covered_subcells
FROM subcell_visits
WHERE grid_cell_id = ? AND year = ? AND month = ?;

SELECT COUNT(DISTINCT subcell_id) as covered_subcells
FROM subcell_visits
WHERE grid_cell_id = ? AND year = ?;

UPDATE effort_data SET coverage_percent = ? 
WHERE grid_cell_id = ? AND year = ? AND month = ? AND movement_type = ?;

-- Subcell visits tracking for spatial coverage (day granularity)

-- name: UpsertSubcellVisit :exec
INSERT INTO subcell_visits (grid_cell_id, subcell_id, visit_date, visit_count, env)
VALUES (?, ?, ?, 1, ?)
ON CONFLICT(grid_cell_id, subcell_id, visit_date, env) DO UPDATE SET
    visit_count = visit_count + 1;

-- name: GetSubcellCoverageByDateRange :one
SELECT COUNT(DISTINCT subcell_id) as covered_subcells
FROM subcell_visits
WHERE grid_cell_id = ? AND visit_date >= ? AND visit_date <= ?;

-- name: GetSubcellVisitsByDateRange :many
SELECT grid_cell_id, subcell_id, visit_date, visit_count
FROM subcell_visits
WHERE grid_cell_id = ? AND visit_date >= ? AND visit_date <= ?
ORDER BY visit_date;

-- name: UpdateEffortCoverage :exec
UPDATE effort_data SET coverage_percent = ? 
WHERE grid_cell_id = ? AND year = ? AND month = ? AND movement_type = ?;

-- name: GetEffortDataWithMonthCounts :many
SELECT 
    g.id as grid_cell_id,
    g.lat_center,
    g.lon_center,
    SUM(e.total_distance_km) as total_distance_km,
    SUM(e.total_points) as total_points,
    MAX(e.unique_uploads) as unique_uploads,
    MAX(e.coverage_percent) as coverage_percent,
    GROUP_CONCAT(DISTINCT e.month) as months_visited,
    COUNT(DISTINCT e.month) as month_count,
    COUNT(DISTINCT CASE WHEN e.month IN (11, 12, 1, 2, 3, 4) THEN e.month END) as dry_months,
    COUNT(DISTINCT CASE WHEN e.month IN (5, 6, 7, 8, 9, 10) THEN e.month END) as rainy_months
FROM grid_cells g
JOIN effort_data e ON e.grid_cell_id = g.id
WHERE e.year BETWEEN ? AND ? 
  AND e.day IS NULL 
  AND e.movement_type = 'all'
GROUP BY g.id, g.lat_center, g.lon_center;

-- Park management plan and document queries

-- name: GetParkDocumentsByCategory :many
SELECT id, pa_id, category, item_id, title, description, file_url, file_type, 
       uploaded_by, uploaded_at, year, summary
FROM park_documents 
WHERE pa_id = ? AND category = ?
ORDER BY year DESC, uploaded_at DESC;

-- name: GetAllParkDocuments :many
SELECT id, pa_id, category, item_id, title, description, file_url, file_type,
       uploaded_by, uploaded_at, year, summary
FROM park_documents 
WHERE pa_id = ?
ORDER BY category, year DESC, uploaded_at DESC;

-- name: InsertParkDocumentExtended :exec
INSERT INTO park_documents (pa_id, category, item_id, title, description, file_url, file_type, uploaded_by, year, summary)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);



-- name: CreateSettlementVisit :one
INSERT INTO settlement_visits (settlement_id, upload_id, park_id, visit_date, visit_start, visit_end, duration_minutes, movement_type, arriving_from, departing_to, year, month)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
RETURNING *;

-- name: GetSettlementVisitsByPark :many
SELECT sv.*, ps.lat, ps.lon, ps.nearest_place
FROM settlement_visits sv
JOIN park_settlements ps ON sv.settlement_id = ps.id
WHERE sv.park_id = ? AND sv.year = ? AND (sv.month = ? OR ? IS NULL)
ORDER BY sv.visit_date DESC;

-- name: UpsertSettlementIntensity :exec
INSERT INTO settlement_intensity (settlement_id, park_id, year, month, total_visits, total_duration_minutes, unique_uploads, foot_visits, vehicle_visits, aircraft_visits, is_likely_base)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(settlement_id, year, month) DO UPDATE SET
    total_visits = total_visits + excluded.total_visits,
    total_duration_minutes = total_duration_minutes + excluded.total_duration_minutes,
    unique_uploads = unique_uploads + 1,
    foot_visits = foot_visits + excluded.foot_visits,
    vehicle_visits = vehicle_visits + excluded.vehicle_visits,
    aircraft_visits = aircraft_visits + excluded.aircraft_visits,
    is_likely_base = CASE WHEN total_visits + excluded.total_visits >= 10 THEN 1 ELSE 0 END,
    updated_at = CURRENT_TIMESTAMP;

-- name: GetSettlementIntensityByPark :many
SELECT si.*, ps.lat, ps.lon, ps.nearest_place, ps.area_m2, ps.population_est
FROM settlement_intensity si
JOIN park_settlements ps ON si.settlement_id = ps.id
WHERE si.park_id = ? AND si.year >= ? AND si.year <= ?
ORDER BY si.total_duration_minutes DESC;

-- name: GetParkBases :many
SELECT si.*, ps.lat, ps.lon, ps.nearest_place, ps.area_m2
FROM settlement_intensity si
JOIN park_settlements ps ON si.settlement_id = ps.id
WHERE si.park_id = ? AND si.is_likely_base = 1
ORDER BY si.total_duration_minutes DESC;

-- name: GetSettlementById :one
SELECT * FROM park_settlements WHERE id = ?;

-- name: FindNearestSettlement :one
SELECT id, park_id, lat, lon, nearest_place,
       ((?1 - lat) * (?1 - lat) + (?2 - lon) * (?2 - lon)) as dist_sq
FROM park_settlements
WHERE park_id = ?3
  AND ABS(lat - ?1) < 0.01 AND ABS(lon - ?2) < 0.01
ORDER BY dist_sq
LIMIT 1;

-- Upload Queue queries
-- name: QueueUpload :one
INSERT INTO upload_queue (user_id, user_email, filename, file_hash, file_content, status, env)
VALUES (?, ?, ?, ?, ?, 'pending', ?)
RETURNING *;

-- name: GetUploadQueueItem :one
SELECT * FROM upload_queue WHERE id = ?;

-- name: GetUploadQueueByHash :one
SELECT * FROM upload_queue WHERE file_hash = ? AND env = ? AND status != 'failed' LIMIT 1;

-- name: GetPendingUploads :many
SELECT id, user_id, user_email, filename, file_hash, file_content, status, created_at, env
FROM upload_queue WHERE status = 'pending' ORDER BY created_at LIMIT ?;

-- name: MarkUploadProcessing :exec
UPDATE upload_queue SET status = 'processing', started_at = CURRENT_TIMESTAMP WHERE id = ?;

-- name: MarkUploadCompleted :exec
UPDATE upload_queue SET status = 'completed', completed_at = CURRENT_TIMESTAMP, result_upload_id = ?, result_json = ? WHERE id = ?;

-- name: MarkUploadFailed :exec
UPDATE upload_queue SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error_message = ? WHERE id = ?;

-- name: GetUploadQueueStatus :one
SELECT id, status, error_message, result_upload_id, result_json, created_at, completed_at
FROM upload_queue WHERE id = ?;

-- name: CleanupOldQueueItems :exec
DELETE FROM upload_queue WHERE completed_at < datetime('now', '-7 days');
