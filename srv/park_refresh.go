package srv

import (
	"log"
	"net/http"
	"os/exec"
	"time"

	"encoding/json"
)

// HandleAPIRefreshPark refreshes derived data for a single park after its
// infrastructure context was enriched (roads/rivers/osm_places) by the daily
// rotation scans (analysis/gfw_alerts.py, analysis/river_turbidity.py).
//
// It:
//  1. Force-reclassifies park_settlements (Go classifier = current canonical
//     style for settlements; new places/roads/turbidity signals flow in).
//  2. Recomputes fire_narrative_cache for the park via the canonical v5
//     python pipeline (scripts/precompute_narratives_v5.py --park X).
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

	// 3. Recompute fire narrative cache for this park via the canonical v5
	//    python pipeline (reads feature_geometries, real v5 hash feature_ids).
	//    The old Go path (computeFireNarrativeForCache) read stale v2 JSON files
	//    and generated sequential _grp_N ids that don't exist in the features
	//    API, breaking fire pinning ("Feature not found").
	fireOK := false
	cmd := exec.Command("python3", "scripts/precompute_narratives_v5.py", "--park", parkID)
	if out, err := cmd.CombinedOutput(); err != nil {
		log.Printf("[RefreshPark] %s: precompute_narratives_v5 failed: %v\n%s", parkID, err, out)
	} else {
		fireOK = true
	}

	log.Printf("[RefreshPark] %s (%s): %d settlements reclassified, %d deforestation rows classified, fire narrative=%v (%v)",
		parkID, parkName, settCount, defoCount, fireOK, time.Since(start))

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"park_id":                  parkID,
		"settlements_reclassified": settCount,
		"deforestation_classified": defoCount,
		"fire_narrative_updated":   fireOK,
		"took_ms":                  time.Since(start).Milliseconds(),
	})
}
