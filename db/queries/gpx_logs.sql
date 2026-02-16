-- name: CreateGPXUploadLog :one
INSERT INTO gpx_upload_logs (
    upload_id, user_id, user_email, filename, upload_time,
    is_valid, total_points, validation_errors, validation_warnings,
    protected_area_id, protected_area_name,
    patrol_km, road_km, boundary_km, excluded_km,
    total_segments, patrol_segments, static_segments, excluded_segments,
    classified_segments_json, processing_status, rejection_reason,
    foot_segments, foot_km, foot_minutes,
    vehicle_segments, vehicle_km, vehicle_minutes,
    aircraft_segments, aircraft_km, aircraft_minutes,
    recon_segments, recon_km, recon_minutes,
    fast_vehicle_segments, fast_vehicle_km, fast_vehicle_minutes,
    transit_segments, transit_km, logistics_segments, logistics_km
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
RETURNING id;

-- name: ListGPXUploadLogs :many
SELECT 
    id, upload_id, user_id, user_email, filename, upload_time,
    is_valid, total_points, validation_errors, validation_warnings,
    protected_area_id, protected_area_name,
    patrol_km, road_km, boundary_km, excluded_km,
    total_segments, patrol_segments, static_segments, excluded_segments,
    processing_status, rejection_reason,
    foot_segments, foot_km, foot_minutes,
    vehicle_segments, vehicle_km, vehicle_minutes,
    aircraft_segments, aircraft_km, aircraft_minutes,
    recon_segments, recon_km, recon_minutes,
    fast_vehicle_segments, fast_vehicle_km, fast_vehicle_minutes,
    transit_segments, transit_km, logistics_segments, logistics_km
FROM gpx_upload_logs
ORDER BY upload_time DESC
LIMIT ? OFFSET ?;

-- name: ListGPXUploadLogsByPark :many
SELECT 
    id, upload_id, user_id, user_email, filename, upload_time,
    is_valid, total_points, validation_errors, validation_warnings,
    protected_area_id, protected_area_name,
    patrol_km, road_km, boundary_km, excluded_km,
    total_segments, patrol_segments, static_segments, excluded_segments,
    processing_status, rejection_reason
FROM gpx_upload_logs
WHERE protected_area_id = ?
ORDER BY upload_time DESC
LIMIT ? OFFSET ?;

-- name: GetGPXUploadLogStats :one
SELECT 
    COUNT(*) as total_uploads,
    SUM(CASE WHEN is_valid = 1 THEN 1 ELSE 0 END) as valid_uploads,
    SUM(CASE WHEN is_valid = 0 THEN 1 ELSE 0 END) as rejected_uploads,
    SUM(patrol_km) as total_patrol_km,
    SUM(road_km) as total_road_km,
    SUM(boundary_km) as total_boundary_km,
    SUM(excluded_km) as total_excluded_km,
    SUM(total_points) as total_points,
    SUM(foot_km) as total_foot_km,
    SUM(vehicle_km) as total_vehicle_km,
    SUM(aircraft_km) as total_aircraft_km,
    SUM(recon_km) as total_recon_km,
    SUM(fast_vehicle_km) as total_fast_vehicle_km,
    SUM(foot_minutes) as total_foot_minutes,
    SUM(vehicle_minutes) as total_vehicle_minutes,
    SUM(aircraft_minutes) as total_aircraft_minutes,
    SUM(recon_minutes) as total_recon_minutes
FROM gpx_upload_logs
WHERE upload_time >= ?;

-- name: GetGPXUploadLog :one
SELECT * FROM gpx_upload_logs WHERE id = ?;

-- name: UpdateGPXUploadLogUploadID :exec
UPDATE gpx_upload_logs SET upload_id = ? WHERE id = ?;
