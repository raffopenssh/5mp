package srv

import (
	"encoding/json"
	"net/http"
)

// GET /api/parks/{id}/fire-trend?from=YYYY-MM-DD&to=YYYY-MM-DD
// Weekly buckets: fire detections inside park, fire groups started,
// groups stopped inside (position contained/ends_inside/started_inside).
func (s *Server) HandleAPIFireTrend(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	internalID := parkID
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.WDPAID == parkID || area.ID == parkID {
				internalID = area.ID
				break
			}
		}
	}
	from := r.URL.Query().Get("from")
	to := r.URL.Query().Get("to")
	if from == "" {
		from = "2020-01-01"
	}
	if to == "" {
		to = "2100-01-01"
	}

	type week struct {
		Week    string `json:"week"` // Monday of ISO week
		Fires   int    `json:"fires"`
		Groups  int    `json:"groups"`
		Stopped int    `json:"stopped"`
	}
	byWeek := map[string]*week{}
	get := func(k string) *week {
		if byWeek[k] == nil {
			byWeek[k] = &week{Week: k}
		}
		return byWeek[k]
	}

	// Fires inside park per ISO week (Monday). Aggregate by acq_date first
	// (index-only on idx_fire_pa_date), then bucket the ~2k distinct dates —
	// avoids a per-row date() call over 500k+ rows on big parks (4x faster).
	rows, err := s.DB.Query(`
		SELECT date(d, 'weekday 0', '-6 days') wk, SUM(c)
		FROM (
			SELECT acq_date d, COUNT(*) c
			FROM fire_detections
			WHERE protected_area_id = ? AND acq_date >= ? AND acq_date <= ?
			GROUP BY acq_date
		)
		GROUP BY wk`, internalID, from, to)
	if err == nil {
		for rows.Next() {
			var wk string
			var n int
			if rows.Scan(&wk, &n) == nil {
				get(wk).Fires = n
			}
		}
		rows.Close()
	}

	// Fire groups by start week + stopped-inside outcome
	rows, err = s.DB.Query(`
		SELECT date(start_date, 'weekday 0', '-6 days') wk,
		       COUNT(*),
		       SUM(CASE WHEN json_extract(properties_json, '$.position') IN ('contained','ends_inside','started_inside') THEN 1 ELSE 0 END)
		FROM feature_geometries
		WHERE park_id = ? AND feature_type = 'fire_trajectory'
		  AND start_date >= ? AND start_date <= ?
		GROUP BY wk`, internalID, from, to)
	if err == nil {
		for rows.Next() {
			var wk string
			var groups, stopped int
			if rows.Scan(&wk, &groups, &stopped) == nil {
				g := get(wk)
				g.Groups = groups
				g.Stopped = stopped
			}
		}
		rows.Close()
	}

	// Sorted output
	keys := make([]string, 0, len(byWeek))
	for k := range byWeek {
		keys = append(keys, k)
	}
	// simple insertion sort (small n)
	for i := 1; i < len(keys); i++ {
		for j := i; j > 0 && keys[j] < keys[j-1]; j-- {
			keys[j], keys[j-1] = keys[j-1], keys[j]
		}
	}
	out := make([]*week, 0, len(keys))
	for _, k := range keys {
		out = append(out, byWeek[k])
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"park_id": internalID,
		"weeks":   out,
	})
}
