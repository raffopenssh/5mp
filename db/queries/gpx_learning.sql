-- name: QueueGPXLearning :one
INSERT INTO gpx_learning_queue (upload_id, park_id, status)
VALUES (?, ?, 'pending')
RETURNING *;

-- name: GetPendingLearningJobs :many
SELECT * FROM gpx_learning_queue WHERE status = 'pending' ORDER BY created_at LIMIT ?;

-- name: UpdateLearningJobStatus :exec
UPDATE gpx_learning_queue SET status = @status, error_message = @error_message WHERE id = @id;

-- name: StartLearningJob :exec
UPDATE gpx_learning_queue SET status = 'processing', started_at = CURRENT_TIMESTAMP WHERE id = ?;

-- name: CompleteLearningJob :exec
UPDATE gpx_learning_queue SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?;

-- name: FailLearningJob :exec
UPDATE gpx_learning_queue SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error_message = ? WHERE id = ?;

-- name: UpsertVehicleStats :exec
INSERT INTO park_vehicle_stats (park_id, movement_type, total_distance_km, total_time_hours, median_speed_kmh, max_speed_kmh, p90_speed_kmh, sample_count)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(park_id, movement_type) DO UPDATE SET
    total_distance_km = total_distance_km + excluded.total_distance_km,
    total_time_hours = total_time_hours + excluded.total_time_hours,
    median_speed_kmh = excluded.median_speed_kmh,
    max_speed_kmh = CASE WHEN excluded.max_speed_kmh > max_speed_kmh THEN excluded.max_speed_kmh ELSE max_speed_kmh END,
    p90_speed_kmh = excluded.p90_speed_kmh,
    sample_count = sample_count + excluded.sample_count,
    updated_at = CURRENT_TIMESTAMP;

-- name: CreateVehicleTrack :one
INSERT INTO vehicle_tracks (park_id, upload_id, geojson, length_m, movement_type)
VALUES (?, ?, ?, ?, ?)
RETURNING id;

-- name: CreateAircraftPattern :one
INSERT INTO aircraft_patterns (park_id, upload_id, pattern_type, aircraft_type, start_lat, start_lon, end_lat, end_lon, geojson, avg_speed_kmh)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
RETURNING id;

-- name: FindNearbyVehicleTracks :many
SELECT id, park_id, geojson, length_m FROM vehicle_tracks 
WHERE park_id = ? LIMIT 1000;

-- name: CreateLearnedRoad :one
INSERT INTO learned_roads (park_id, geojson, length_m, match_count, confidence_pct)
VALUES (?, ?, ?, ?, ?)
RETURNING id;

-- name: UpdateLearnedRoadMatch :exec
UPDATE learned_roads SET match_count = match_count + 1, confidence_pct = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;

-- name: CreateLearnedAirstrip :one
INSERT INTO learned_airstrips (park_id, lat, lon, heading_deg, length_m, aircraft_type, landing_count, takeoff_count, confidence_pct, approach_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
RETURNING id;

-- name: FindNearbyAirstrips :many
SELECT * FROM learned_airstrips WHERE park_id = ? AND ABS(lat - ?) < 0.01 AND ABS(lon - ?) < 0.01;

-- name: UpdateAirstripStats :exec
UPDATE learned_airstrips SET landing_count = landing_count + ?, takeoff_count = takeoff_count + ?, confidence_pct = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;

-- name: CreateLearnedPlace :one
INSERT INTO learned_places (park_id, lat, lon, place_type, visit_count, avg_duration_minutes, confidence_pct)
VALUES (?, ?, ?, ?, ?, ?, ?)
RETURNING id;

-- name: FindNearbyPlaces :many
SELECT * FROM learned_places WHERE park_id = ? AND ABS(lat - ?) < 0.005 AND ABS(lon - ?) < 0.005;

-- name: UpdatePlaceStats :exec
UPDATE learned_places SET visit_count = visit_count + 1, avg_duration_minutes = ?, confidence_pct = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;

-- name: CreateLearningResult :one
INSERT INTO gpx_learning_results (upload_id, park_id, park_name, vehicle_median_speed_kmh, vehicle_max_speed_kmh, foot_median_speed_kmh, foot_max_speed_kmh, foot_mcp_area_km2, new_roads_found, new_roads_km, road_confidence_pct, new_airstrips_found, airstrip_confidence_pct, new_places_found, place_types_json, place_confidence_pct, summary_text, discoveries_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
RETURNING id;

-- name: GetLearningResultsByPark :many
SELECT * FROM gpx_learning_results WHERE park_id = ? ORDER BY created_at DESC LIMIT ?;

-- name: GetAllLearningResults :many
SELECT * FROM gpx_learning_results ORDER BY created_at DESC LIMIT ? OFFSET ?;

-- name: GetPendingApprovals :many
SELECT 'road' as type, id, park_id, confidence_pct, created_at FROM learned_roads WHERE status = 'pending'
UNION ALL
SELECT 'airstrip' as type, id, park_id, confidence_pct, created_at FROM learned_airstrips WHERE status = 'pending'
UNION ALL
SELECT 'place' as type, id, park_id, confidence_pct, created_at FROM learned_places WHERE status = 'pending'
ORDER BY created_at DESC LIMIT ?;

-- name: ApproveLearnedRoad :exec
UPDATE learned_roads SET status = 'approved', approved_by = ?, approved_at = CURRENT_TIMESTAMP WHERE id = ?;

-- name: RejectLearnedRoad :exec
UPDATE learned_roads SET status = 'rejected', approved_by = ?, approved_at = CURRENT_TIMESTAMP WHERE id = ?;

-- name: ApproveLearnedAirstrip :exec
UPDATE learned_airstrips SET status = 'approved', approved_by = ?, approved_at = CURRENT_TIMESTAMP WHERE id = ?;

-- name: ApproveLearnedPlace :exec
UPDATE learned_places SET status = 'approved', approved_by = ?, approved_at = CURRENT_TIMESTAMP WHERE id = ?;

-- name: GetVehicleStatsByPark :many
SELECT * FROM park_vehicle_stats WHERE park_id = ?;

-- name: GetLearnedRoadsByPark :many
SELECT * FROM learned_roads WHERE park_id = ? AND status IN ('pending', 'approved') ORDER BY confidence_pct DESC;

-- name: GetLearnedAirstripsByPark :many
SELECT * FROM learned_airstrips WHERE park_id = ? AND status IN ('pending', 'approved') ORDER BY confidence_pct DESC;

-- name: GetLearnedPlacesByPark :many
SELECT * FROM learned_places WHERE park_id = ? AND status IN ('pending', 'approved') ORDER BY confidence_pct DESC;
