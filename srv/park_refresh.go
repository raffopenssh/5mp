package srv

import (
	"encoding/json"
	"log"
	"net/http"
	"time"
)

// HandleAPIRefreshPark refreshes derived data for a single park after its
// infrastructure context was enriched (roads/rivers/osm_places) by the daily
// rotation scans (analysis/gfw_alerts.py, analysis/river_turbidity.py).
//
// It:
//  1. Force-reclassifies park_settlements (Go classifier = current canonical
//     style for settlements; new places/roads/turbidity signals flow in).
//  2. Recomputes fire_narrative_cache for the park (same code path as
//     PrecomputeRecentFireNarratives).
//
// Deforestation narratives are intentionally NOT touched here: their
// canonical style is the python pipeline (scripts/rebuild_events_enhanced.py);
// scripts/daily_park_refresh.py reclassifies them in python before calling
// this endpoint.
//
// Registered with RequireAdminOrLocal so the localhost cron can call it.
func (s *Server) HandleAPIRefreshPark(w http.ResponseWriter, r *http.Request) {
	parkID := r.URL.Query().Get("park")
	if parkID == "" {
		http.Error(w, "missing ?park=", http.StatusBadRequest)
		return
	}

	parkName := parkID
	found := false
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.ID == parkID {
				parkName = area.Name
				found = true
				break
			}
		}
	}
	if !found {
		http.Error(w, "unknown park: "+parkID, http.StatusNotFound)
		return
	}

	start := time.Now()

	// 1. Force settlement reclassification (skips turbidity-note rows).
	settCount := s.ClassifyParkSettlementsForce(parkID)

	// 2. Classify any never-classified deforestation rows (Go fallback only;
	//    python-classified rows are never touched — see classifyParkDeforestation).
	defoCount := s.classifyParkDeforestation(parkID)

	// 3. Recompute fire narrative cache for this park.
	fromYear := time.Now().Year() - 25
	toYear := time.Now().Year()
	fireOK := false
	if narrative := s.computeFireNarrativeForCache(parkID, parkName, fromYear, toYear); narrative != nil {
		narrativeJSON, _ := json.Marshal(narrative)
		_, err := s.DB.Exec(`
			INSERT INTO fire_narrative_cache (park_id, narrative_json, computed_at, from_year, to_year)
			VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
			ON CONFLICT(park_id) DO UPDATE SET
				narrative_json = excluded.narrative_json,
				computed_at = CURRENT_TIMESTAMP,
				from_year = excluded.from_year,
				to_year = excluded.to_year`,
			parkID, string(narrativeJSON), fromYear, toYear)
		fireOK = err == nil
	}

	log.Printf("[RefreshPark] %s: %d settlements reclassified, %d deforestation rows classified, fire narrative=%v (%v)",
		parkID, settCount, defoCount, fireOK, time.Since(start))

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"park_id":                 parkID,
		"settlements_reclassified": settCount,
		"deforestation_classified": defoCount,
		"fire_narrative_updated":   fireOK,
		"took_ms":                  time.Since(start).Milliseconds(),
	})
}
