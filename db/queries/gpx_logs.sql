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
    boat_segments, boat_km, boat_minutes,
    fixed_wing_segments, fixed_wing_km, fixed_wing_minutes,
    rotor_wing_segments, rotor_wing_km, rotor_wing_minutes,
    recon_segments, recon_km, recon_minutes,
    fast_vehicle_segments, fast_vehicle_km, fast_vehicle_minutes,
    transit_segments, transit_km, logistics_segments, logistics_km, env
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    boat_segments, boat_km, boat_minutes,
    fixed_wing_segments, fixed_wing_km, fixed_wing_minutes,
    rotor_wing_segments, rotor_wing_km, rotor_wing_minutes,
    recon_segments, recon_km, recon_minutes,
    fast_vehicle_segments, fast_vehicle_km, fast_vehicle_minutes,
    transit_segments, transit_km, logistics_segments, logistics_km
FROM gpx_upload_logs
WHERE env IN (SELECT value FROM json_each(?))
ORDER BY upload_time DESC
LIMIT ? OFFSET ?;

-- name: ListGPXUploadLogsByPark :many
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
    boat_segments, boat_km, boat_minutes,
    fixed_wing_segments, fixed_wing_km, fixed_wing_minutes,
    rotor_wing_segments, rotor_wing_km, rotor_wing_minutes,
    recon_segments, recon_km, recon_minutes,
    fast_vehicle_segments, fast_vehicle_km, fast_vehicle_minutes,
    transit_segments, transit_km, logistics_segments, logistics_km
FROM gpx_upload_logs
WHERE protected_area_id = ? AND env IN (SELECT value FROM json_each(?))
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
    SUM(boat_km) as total_boat_km,
    SUM(fixed_wing_km) as total_fixed_wing_km,
    SUM(rotor_wing_km) as total_rotor_wing_km,
    SUM(recon_km) as total_recon_km,
    SUM(fast_vehicle_km) as total_fast_vehicle_km,
    SUM(foot_minutes) as total_foot_minutes,
    SUM(vehicle_minutes) as total_vehicle_minutes,
    SUM(aircraft_minutes) as total_aircraft_minutes,
    SUM(boat_minutes) as total_boat_minutes,
    SUM(fixed_wing_minutes) as total_fixed_wing_minutes,
    SUM(rotor_wing_minutes) as total_rotor_wing_minutes,
    SUM(recon_minutes) as total_recon_minutes
FROM gpx_upload_logs
WHERE upload_time >= ? AND env IN (SELECT value FROM json_each(?));

-- name: GetGPXUploadLog :one
SELECT * FROM gpx_upload_logs WHERE id = ?;

-- name: UpdateGPXUploadLogUploadID :exec
UPDATE gpx_upload_logs SET upload_id = ? WHERE id = ?;
