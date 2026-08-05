package srv

import (
	"encoding/json"
	"net/http"
)

// Test-environment isolation for client-derived data.
//
// Everything "learned" (learned_roads / learned_places / learned_airstrips,
// their history tables, gpx_learning_results, park_patrol_mcp, park_vehicle_stats,
// gpx_upload_logs, and the auto-approved road/airstrip rows in
// feature_geometries) is derived exclusively from real client patrol GPX:
// persistLearningJobs() bails out for env=="test" uploads, so no test upload
// ever contributes a learned row. Those tables therefore have no env column and
// need none — in the test tenant the correct answer is simply "nothing".
//
// Rather than filtering, read paths return empty and write paths refuse. This
// keeps client findings (road networks, airstrips, camp locations, patrol
// coverage) out of the shared-password test environment, and prevents a test
// session from approving/rejecting/deleting prod findings.
//
// If learned data ever needs to exist per-tenant, add an env column to those
// tables and filter instead of blanking.

// isTestEnv reports whether this request belongs to the test tenant.
func isTestEnv(r *http.Request) bool { return RequestEnv(r) == "test" }

// blockLearnedInTestEnv writes `empty` as JSON and returns true when the
// request is from the test tenant, so the caller can return early.
func blockLearnedInTestEnv(w http.ResponseWriter, r *http.Request, empty interface{}) bool {
	if !isTestEnv(r) {
		return false
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(w).Encode(empty)
	return true
}

// refuseWriteInTestEnv rejects mutations of client-derived data from the test
// tenant (approve/reject/rollback/delete of learned features and uploads).
func refuseWriteInTestEnv(w http.ResponseWriter, r *http.Request) bool {
	if !isTestEnv(r) {
		return false
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusForbidden)
	json.NewEncoder(w).Encode(map[string]string{
		"error": "not available in the test environment",
	})
	return true
}
