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

// onboardCallerRef is the caller's pwd_ref, or "" when the request carries
// no password. Same handle as short_links/shared_files: a request belongs to
// the logins that made it, and another login must get "no such request"
// (404, not 403 — an id must not be an oracle, AGENTS.md invariant 6).
func onboardCallerRef(r *http.Request) string {
	if pwd := RequestPwd(r); pwd != "" {
		return principalRef(pwd)
	}
	return ""
}

// HandleAPIRequestOnboard records a request to onboard a new WDPA park.
// POST /api/onboarding/request?wdpa_id=NNN
//
// The WORK is global (a park is shared data; the ingest runs once no matter
// how many logins ask), but the REQUEST is scoped: each caller becomes a
// subscriber on the shared request row, gets lifecycle notifications in their
// own tenant, and may later cancel only their own subscription.
func (s *Server) HandleAPIRequestOnboard(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	wdpaID, err := strconv.Atoi(r.URL.Query().Get("wdpa_id"))
	if err != nil || wdpaID <= 0 {
		http.Error(w, `{"error":"missing or invalid wdpa_id"}`, http.StatusBadRequest)
		return
	}
	ref := onboardCallerRef(r)
	if ref == "" {
		// A guest capability or an unauthenticated caller cannot start a
		// multi-hour ingest.
		http.Error(w, `{"error":"onboarding requires a logged-in session"}`, http.StatusForbidden)
		return
	}

	// The request is tagged with the caller's tenant to route notifications
	// back and to keep the shared sandbox out: onboard_park.py skips requests
	// whose subscribers are all env='test', so a demo dwell never triggers a
	// real multi-hour ingest.
	env := RequestEnv(r)

	// Must be a known WDPA area.
	if s.WDPAIndex == nil {
		http.Error(w, `{"error":"wdpa index unavailable"}`, http.StatusServiceUnavailable)
		return
	}
	entry := s.WDPAIndex.GetByID(wdpaID)
	if entry == nil {
		http.Error(w, `{"error":"unknown wdpa_id"}`, http.StatusNotFound)
		return
	}

	// Must not already be loaded as a keystone. If it is — e.g. onboarded on
	// another tenant's request — the caller still becomes a subscriber on the
	// existing request row (if any), so their interest is on record and one
	// tenant's later cancel cannot remove a park another tenant uses. The data
	// itself is global and shared; nothing is re-ingested.
	if s.AreaStore != nil {
		idStr := strconv.Itoa(wdpaID)
		for _, a := range s.AreaStore.Areas {
			if a.WDPAID == idStr {
				var reqID int64
				if s.DB.QueryRow(`SELECT id FROM park_onboarding_requests WHERE wdpa_id = ?`,
					wdpaID).Scan(&reqID) == nil {
					s.DB.Exec(`INSERT OR IGNORE INTO park_onboarding_subscribers (request_id, pwd_ref, env)
						VALUES (?, ?, ?)`, reqID, ref, env)
				}
				json.NewEncoder(w).Encode(map[string]any{
					"status": "already_loaded", "park_id": a.ID,
				})
				return
			}
		}
	}

	// Existing request? Subscribe this caller to it (idempotent) — the ingest
	// runs once; a second tenant asking for the same park shares the row and
	// the eventual result rather than triggering a duplicate.
	var existingStatus string
	var existingID int64
	err = s.DB.QueryRow(`SELECT id, status FROM park_onboarding_requests WHERE wdpa_id = ?`,
		wdpaID).Scan(&existingID, &existingStatus)
	if err == nil {
		s.DB.Exec(`INSERT OR IGNORE INTO park_onboarding_subscribers (request_id, pwd_ref, env)
			VALUES (?, ?, ?)`, existingID, ref, env)
		// A new subscriber revives a dying/dead request: a removal another
		// tenant scheduled must not take out a park this caller just asked
		// for, and a removed park is simply queued again.
		switch existingStatus {
		case "remove_requested":
			s.DB.Exec(`UPDATE park_onboarding_requests SET status='ready' WHERE id = ?`, existingID)
			existingStatus = "ready"
		case "removed":
			s.DB.Exec(`UPDATE park_onboarding_requests SET status='pending', detail='' WHERE id = ?`, existingID)
			existingStatus = "pending"
		}
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
	s.DB.Exec(`INSERT OR IGNORE INTO park_onboarding_subscribers (request_id, pwd_ref, env)
		VALUES (?, ?, ?)`, reqID, ref, env)

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

// HandleAPICancelOnboard cancels the CALLER'S subscription to an onboarding
// request, and only tears down the shared work when no subscriber remains.
// POST /api/onboarding/cancel?wdpa_id=NNN
//
//   - status pending/failed  -> subscription removed; the request row is
//     deleted only when the last subscriber leaves
//     (undo toast path)
//   - status ready           -> subscription removed; status=remove_requested
//     only when the last subscriber leaves — another
//     tenant still using the park keeps it alive.
//     scripts/onboard_park.py removes the keystone +
//     derived data on its next nightly run (only
//     onboarded parks can be removed; original
//     keystones are refused by the script).
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

	// Scope: only a subscriber may act on the request, and a non-subscriber
	// gets the same 404 as a missing row — an id must not be an oracle.
	ref := onboardCallerRef(r)
	var one int
	if ref == "" || s.DB.QueryRow(`SELECT 1 FROM park_onboarding_subscribers
		WHERE request_id = ? AND pwd_ref = ?`, id, ref).Scan(&one) != nil {
		// remove_requested toggle-back is the one exception: the caller wants
		// back IN, and their subscription was already removed on the way out.
		if !(ref != "" && status == "remove_requested") {
			http.Error(w, `{"error":"no onboarding request for this wdpa_id"}`, http.StatusNotFound)
			return
		}
	}

	// subscribersLeft after this caller leaves.
	unsubscribe := func() int {
		s.DB.Exec(`DELETE FROM park_onboarding_subscribers WHERE request_id = ? AND pwd_ref = ?`, id, ref)
		var n int
		s.DB.QueryRow(`SELECT COUNT(*) FROM park_onboarding_subscribers WHERE request_id = ?`, id).Scan(&n)
		return n
	}
	env := RequestEnv(r)

	switch status {
	case "pending", "failed":
		left := unsubscribe()
		// The caller's own queued-notification goes either way, so their bell
		// doesn't advertise a park they no longer want.
		s.DB.Exec(`DELETE FROM notifications WHERE notification_type = 'park_onboarding' AND reference_id = ? AND env = ?`,
			strconv.Itoa(wdpaID), env)
		if left > 0 {
			// Another tenant still wants the park: the shared work stays queued.
			json.NewEncoder(w).Encode(map[string]any{"status": "cancelled", "name": name})
			return
		}
		s.DB.Exec(`DELETE FROM park_onboarding_requests WHERE id = ?`, id)
		s.DB.Exec(`DELETE FROM notifications WHERE notification_type = 'park_onboarding' AND reference_id = ?`,
			strconv.Itoa(wdpaID))
		json.NewEncoder(w).Encode(map[string]any{"status": "cancelled", "name": name})
	case "processing":
		http.Error(w, `{"error":"onboarding is running right now; try again later"}`, http.StatusConflict)
	case "ready":
		if left := unsubscribe(); left > 0 {
			// Someone else still uses the park. The caller is out; the park
			// stays. Never let one tenant delete another tenant's data.
			json.NewEncoder(w).Encode(map[string]any{
				"status": "unsubscribed", "park_id": parkID,
				"message": "Your request was withdrawn; the park stays because another account still uses it.",
			})
			return
		}
		s.DB.Exec(`UPDATE park_onboarding_requests SET status='remove_requested' WHERE id = ?`, id)
		msg := fmt.Sprintf("%s (%s) is scheduled for removal; the park and its derived layers will be dropped in the next nightly run.", name, parkID)
		s.DB.Exec(`INSERT INTO notifications (park_id, notification_type, title, message, reference_id, env)
			VALUES (?, 'park_onboarding', ?, ?, ?, ?)`,
			parkID, "Park removal scheduled: "+name, msg, strconv.Itoa(wdpaID), env)
		json.NewEncoder(w).Encode(map[string]any{"status": "remove_scheduled", "park_id": parkID, "message": msg})
	case "remove_requested":
		// Toggle back: user reconsidered the removal. Re-subscribe them.
		s.DB.Exec(`INSERT OR IGNORE INTO park_onboarding_subscribers (request_id, pwd_ref, env)
			VALUES (?, ?, ?)`, id, ref, env)
		s.DB.Exec(`UPDATE park_onboarding_requests SET status='ready' WHERE id = ?`, id)
		json.NewEncoder(w).Encode(map[string]any{"status": "remove_cancelled", "park_id": parkID})
	default:
		http.Error(w, `{"error":"unexpected status: `+status+`"}`, http.StatusConflict)
	}
}

// HandleAPIOnboardingStatus lists the CALLER'S onboarding requests (newest
// first). Scoped like short_links: a request row is visible only to its
// subscribers, so one tenant's search-dwell offers never reveal what another
// tenant asked for. A caller who wants a park someone else already queued
// simply requests it again and is folded into the shared row.
// GET /api/onboarding
func (s *Server) HandleAPIOnboardingStatus(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	ref := onboardCallerRef(r)
	if ref == "" {
		json.NewEncoder(w).Encode([]any{})
		return
	}
	rows, err := s.DB.Query(`
		SELECT q.id, q.wdpa_id, q.name, COALESCE(q.country,''), COALESCE(q.park_id,''),
		       q.status, COALESCE(q.detail,''), q.requested_at, COALESCE(q.processed_at,'')
		FROM park_onboarding_requests q
		JOIN park_onboarding_subscribers s ON s.request_id = q.id AND s.pwd_ref = ?
		ORDER BY q.requested_at DESC LIMIT 100`, ref)
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
