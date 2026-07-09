package srv

// On-the-fly park onboarding.
//
// Flow: a logged-in (non-test) user searches for a park name that has no match
// among our loaded keystones but a good match in the WDPA index. The frontend
// (after a 15s dwell on such a search) POSTs /api/parks/request-onboard with
// the WDPA id. We record the request and tell the user data collection takes
// about a day. scripts/onboard_park.py (nightly cron, before the 3am fire
// update) picks up pending requests: fetches the boundary from Protected
// Planet, appends it to data/keystones_with_boundaries.json, backfills fires
// (all-time since 2018-04-01, ~25-30 min/park), GFW deforestation, GHSL settlements, hydro
// rivers/lakes when source data is present, runs the v5 fire pipeline, and
// restarts the server. The GFW/turbidity daily rotations then automatically
// prioritise the new park (never-scanned parks sort first).

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
)

// HandleAPIRequestOnboard records a request to onboard a new WDPA park.
// POST /api/onboarding/request?wdpa_id=NNN
func (s *Server) HandleAPIRequestOnboard(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	wdpaID, err := strconv.Atoi(r.URL.Query().Get("wdpa_id"))
	if err != nil || wdpaID <= 0 {
		http.Error(w, `{"error":"missing or invalid wdpa_id"}`, http.StatusBadRequest)
		return
	}

	// Test sessions can request but are tagged env=test; onboard_park.py only
	// processes prod requests, so test dwells never trigger real ingestion.
	env := RequestEnv(r)

	// Must be a known WDPA area.
	entry := s.WDPAIndex.GetByID(wdpaID)
	if entry == nil {
		http.Error(w, `{"error":"unknown wdpa_id"}`, http.StatusNotFound)
		return
	}

	// Must not already be loaded as a keystone.
	if s.AreaStore != nil {
		idStr := strconv.Itoa(wdpaID)
		for _, a := range s.AreaStore.Areas {
			if a.WDPAID == idStr {
				json.NewEncoder(w).Encode(map[string]any{
					"status": "already_loaded", "park_id": a.ID,
				})
				return
			}
		}
	}

	// Existing request? Return its status (idempotent).
	var existingStatus string
	var existingID int64
	err = s.DB.QueryRow(`SELECT id, status FROM park_onboarding_requests WHERE wdpa_id = ?`,
		wdpaID).Scan(&existingID, &existingStatus)
	if err == nil {
		json.NewEncoder(w).Encode(map[string]any{
			"status": "already_requested", "request_status": existingStatus, "request_id": existingID,
		})
		return
	}

	res, err := s.DB.Exec(`
		INSERT INTO park_onboarding_requests (wdpa_id, name, country, country_code, env)
		VALUES (?, ?, ?, ?, ?)`,
		wdpaID, entry.Name, entry.Country, entry.CountryCode, env)
	if err != nil {
		http.Error(w, `{"error":"failed to record request"}`, http.StatusInternalServerError)
		return
	}
	reqID, _ := res.LastInsertId()

	// User-facing notification: set expectations (data takes ~1 day).
	title := fmt.Sprintf("New park requested: %s", entry.Name)
	msg := fmt.Sprintf(
		"%s (%s, WDPA %d) is queued for onboarding. Boundary, fire history "+
			"(full archive since 2018), deforestation, settlement and river "+
			"layers take about a day to fetch and process — the park will appear "+
			"on the globe tomorrow and will be prioritised in the daily scans.",
		entry.Name, entry.Country, wdpaID)
	s.DB.Exec(`
		INSERT INTO notifications (park_id, notification_type, title, message, reference_id, env)
		VALUES (?, 'park_onboarding', ?, ?, ?, ?)`,
		pendingParkID(entry.CountryCode, entry.Name), title, msg, strconv.Itoa(wdpaID), env)

	json.NewEncoder(w).Encode(map[string]any{
		"status":     "requested",
		"request_id": reqID,
		"name":       entry.Name,
		"country":    entry.Country,
		"message":    msg,
	})
}

// HandleAPICancelOnboard cancels a pending onboarding request or schedules
// removal of an already-onboarded park. POST /api/onboarding/cancel?wdpa_id=NNN
//
// - status pending/failed  -> request row deleted immediately (undo toast path)
// - status ready           -> status=remove_requested; scripts/onboard_park.py
//                             removes the keystone + derived data on its next
//                             nightly run (only onboarded parks can be removed;
//                             original keystones are refused by the script).
func (s *Server) HandleAPICancelOnboard(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	wdpaID, err := strconv.Atoi(r.URL.Query().Get("wdpa_id"))
	if err != nil || wdpaID <= 0 {
		http.Error(w, `{"error":"missing or invalid wdpa_id"}`, http.StatusBadRequest)
		return
	}

	var id int64
	var status, name, parkID string
	err = s.DB.QueryRow(`SELECT id, status, name, COALESCE(park_id,'') FROM park_onboarding_requests WHERE wdpa_id = ?`,
		wdpaID).Scan(&id, &status, &name, &parkID)
	if err != nil {
		http.Error(w, `{"error":"no onboarding request for this wdpa_id"}`, http.StatusNotFound)
		return
	}

	switch status {
	case "pending", "failed":
		s.DB.Exec(`DELETE FROM park_onboarding_requests WHERE id = ?`, id)
		// Drop the queued-notification too so the bell doesn't advertise a park
		// that will never arrive.
		s.DB.Exec(`DELETE FROM notifications WHERE notification_type = 'park_onboarding' AND reference_id = ?`,
			strconv.Itoa(wdpaID))
		json.NewEncoder(w).Encode(map[string]any{"status": "cancelled", "name": name})
	case "processing":
		http.Error(w, `{"error":"onboarding is running right now; try again later"}`, http.StatusConflict)
	case "ready":
		s.DB.Exec(`UPDATE park_onboarding_requests SET status='remove_requested' WHERE id = ?`, id)
		msg := fmt.Sprintf("%s (%s) is scheduled for removal; the park and its derived layers will be dropped in the next nightly run.", name, parkID)
		s.DB.Exec(`INSERT INTO notifications (park_id, notification_type, title, message, reference_id, env)
			VALUES (?, 'park_onboarding', ?, ?, ?, ?)`,
			parkID, "Park removal scheduled: "+name, msg, strconv.Itoa(wdpaID), RequestEnv(r))
		json.NewEncoder(w).Encode(map[string]any{"status": "remove_scheduled", "park_id": parkID, "message": msg})
	case "remove_requested":
		// Toggle back: user reconsidered the removal.
		s.DB.Exec(`UPDATE park_onboarding_requests SET status='ready' WHERE id = ?`, id)
		json.NewEncoder(w).Encode(map[string]any{"status": "remove_cancelled", "park_id": parkID})
	default:
		http.Error(w, `{"error":"unexpected status: `+status+`"}`, http.StatusConflict)
	}
}

// HandleAPIOnboardingStatus lists onboarding requests (newest first).
// GET /api/onboarding
func (s *Server) HandleAPIOnboardingStatus(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	rows, err := s.DB.Query(`
		SELECT id, wdpa_id, name, COALESCE(country,''), COALESCE(park_id,''),
		       status, COALESCE(detail,''), requested_at, COALESCE(processed_at,'')
		FROM park_onboarding_requests ORDER BY requested_at DESC LIMIT 100`)
	if err != nil {
		json.NewEncoder(w).Encode([]any{})
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var id, wdpaID int
		var name, country, parkID, status, detail, reqAt, procAt string
		if rows.Scan(&id, &wdpaID, &name, &country, &parkID, &status, &detail, &reqAt, &procAt) == nil {
			out = append(out, map[string]any{
				"id": id, "wdpa_id": wdpaID, "name": name, "country": country,
				"park_id": parkID, "status": status, "detail": detail,
				"requested_at": reqAt, "processed_at": procAt,
			})
		}
	}
	json.NewEncoder(w).Encode(out)
}

// pendingParkID mirrors the {ISO3}_{SanitizedName} id scheme used by
// scripts/onboard_park.py so the notification can be associated with the
// park once it exists.
func pendingParkID(iso3, name string) string {
	clean := make([]rune, 0, len(name))
	for _, r := range name {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9':
			clean = append(clean, r)
		case r == ' ', r == '-', r == '_':
			clean = append(clean, '_')
		}
	}
	id := strings.Trim(strings.Join(strings.FieldsFunc(string(clean), func(r rune) bool { return r == '_' }), "_"), "_")
	if iso3 == "" {
		iso3 = "XXX"
	}
	return strings.ToUpper(iso3) + "_" + id
}
