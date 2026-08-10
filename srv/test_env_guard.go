package srv

import (
	"encoding/json"
	"net/http"
)

// Tenant isolation for client-derived data.
//
// Everything "learned" (learned_roads / learned_places / learned_airstrips,
// their history tables, gpx_learning_results, park_patrol_mcp, park_vehicle_stats,
// gpx_upload_logs, and the auto-approved road/airstrip rows in
// feature_geometries) is derived exclusively from real client patrol GPX:
// persistLearningJobs() bails out for uploads outside the client tenant, so no
// sandbox or other-tenant upload ever contributes a learned row. Those tables therefore have no env column and
// need none — in the test tenant the correct answer is simply "nothing".
//
// Rather than filtering, read paths return empty and write paths refuse. This
// keeps client findings (road networks, airstrips, camp locations, patrol
// coverage) out of the shared-password test environment, and prevents a test
// session from approving/rejecting/deleting prod findings.
//
// If learned data ever needs to exist per-tenant, add an env column to those
// tables and filter instead of blanking.

// isTestEnv reports whether this request is OUTSIDE the client tenant that
// owns the patrol data. It is not only the test2026 sandbox any more: any
// password not mapped to the client tenant (srv/tenant.go) gets the same
// treatment, because the question the callers ask is "may this request see
// client-derived data", and that has exactly one answer per tenant.
func isTestEnv(r *http.Request) bool { return RequestEnv(r) != clientTenant }

// blockLearnedInTestEnv writes `empty` as JSON and returns true when the
// request is outside the client tenant, so the caller can return early.
func blockLearnedInTestEnv(w http.ResponseWriter, r *http.Request, empty interface{}) bool {
	if !isTestEnv(r) {
		return false
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(w).Encode(empty)
	return true
}

// refuseWriteInTestEnv rejects mutations of client-derived data from outside
// the client tenant (approve/reject/rollback/delete of learned features and
// uploads).
func refuseWriteInTestEnv(w http.ResponseWriter, r *http.Request) bool {
	if !isTestEnv(r) {
		return false
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusForbidden)
	json.NewEncoder(w).Encode(map[string]string{
		"error": "not available in this environment",
	})
	return true
}
