package srv

import (
	"encoding/json"
	"math"
	"net/http"
	"os"
	"time"
)

// pipelineStatusFile is written by scripts/daily_fire_update.py at the end of
// every run (success, degraded or fatal). It exists because an append-only log
// makes "cron never ran" look identical to "cron succeeded".
const pipelineStatusFile = "data/pipeline_status.json"

// staleAfter: the fire cron runs at 03:00 UTC daily. Two missed runs = stale.
const pipelineStaleAfter = 48 * time.Hour

// HandleAPIPipelineStatus reports the freshness/result of the nightly fire
// pipeline so the admin panel can show staleness instead of silence.
func (s *Server) HandleAPIPipelineStatus(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	enc := json.NewEncoder(w)

	data, err := os.ReadFile(pipelineStatusFile)
	if err != nil {
		enc.Encode(map[string]interface{}{
			"status":  "unknown",
			"stale":   true,
			"message": "no pipeline heartbeat yet (daily_fire_update.py has not run since this was deployed)",
		})
		return
	}

	var payload map[string]interface{}
	if err := json.Unmarshal(data, &payload); err != nil {
		enc.Encode(map[string]interface{}{
			"status":  "unknown",
			"stale":   true,
			"message": "pipeline heartbeat unparseable: " + err.Error(),
		})
		return
	}

	stale := true
	ageHours := -1.0
	if s, ok := payload["finished_at"].(string); ok {
		// python isoformat(), local time, no zone suffix
		if t, err := time.ParseInLocation("2006-01-02T15:04:05.999999", s, time.Local); err == nil {
			age := time.Since(t)
			ageHours = age.Hours()
			stale = age > pipelineStaleAfter
		}
	}
	payload["stale"] = stale
	// CARTO base-map proxy usage, so the admin panel shows how much of the
	// 5M/month fair-use allowance the tile proxy has spent (upstream fetches
	// only -- cache hits cost no quota). See srv/basemap.go.
	if month, n := CartoQuotaThisMonth(); month != "" {
		payload["carto_basemap"] = map[string]any{
			"month":            month,
			"upstream_fetches": n,
			"fair_use_limit":   5000000,
		}
	}
	if ageHours >= 0 {
		payload["age_hours"] = math.Round(ageHours*10) / 10
	}
	enc.Encode(payload)
}
