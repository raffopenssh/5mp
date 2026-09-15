package srv

import (
	"encoding/csv"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"sync"
	"time"
)

// ParkExportRow represents a single park's data for CSV export.
type ParkExportRow struct {
	ParkID           string
	Name             string
	Country          string
	AreaKm2          float64
	FireCount        int64
	FireGroups       int64  // fire_trajectory chains (feature_geometries)
	VanguardGroups   int64  // the vanguard population (vanguardRowsSQL) — KF chains where tracked, else plain chains that began 10–60 d ahead (NULL front → 0, and fire_groups says so)
	VanguardTracker  string // "kf" | "groups": which population vanguard_groups counts (srv/fire_season.go vanguardTracker)
	VanguardMoving   int64  // KF chains still moving (end_cause = ongoing: last seen within 3 d of the area's newest data); 0 for "groups"
	SettlementCount  int64
	DeforestationKm2 float64
	RoadlessPct      float64
}

// parkFireCountMemo holds the per-park inside-boundary detection count.
// The GROUP BY behind it walks 7.9M index entries and then the table for
// `protected_area_id` (idx_fire_infraction is not covering) — ~20 s warm,
// 60 s+ cold. Detections are append-only (daily FIRMS ingest), so
// MAX(rowid) is an O(1) fingerprint of the input: same rowid → same answer.
// When the fingerprint moves, the previous answer is served once more while
// one goroutine recomputes (stale-while-revalidate). The memo is persisted
// in server_memo (db/migrations/068) so a restart costs nothing, and
// WarmParkFireCounts fills it off the request path at startup — no request
// pays the full price unless the table is empty AND the process is fresh.
type parkFireCountMemo struct {
	mu         sync.Mutex
	maxRowid   int64
	counts     map[string]int64
	refreshing bool
}

var parkFireCounts = &parkFireCountMemo{}

const parkFireCountMemoKey = "park_fire_counts"

// WarmParkFireCounts loads the persisted memo (or computes it once) in the
// background so the first parks-CSV request after a restart is fast.
func (s *Server) WarmParkFireCounts() {
	time.Sleep(5 * time.Second) // let the listener come up first
	s.parkFireCounts()
}

func (s *Server) parkFireCounts() map[string]int64 {
	var maxRowid int64
	_ = s.DB.QueryRow(`SELECT COALESCE(MAX(rowid), 0) FROM fire_detections`).Scan(&maxRowid)

	m := parkFireCounts
	m.mu.Lock()
	if m.counts == nil {
		// Fresh process: the persisted memo, whatever its fingerprint —
		// a stale one is served once and refreshed below, exactly like an
		// in-memory stale answer.
		if counts, fp, ok := s.loadParkFireCountMemo(); ok {
			m.counts, m.maxRowid = counts, fp
		}
	}
	if m.counts != nil && (m.maxRowid == maxRowid || m.refreshing) {
		c := m.counts
		m.mu.Unlock()
		return c
	}
	if m.counts != nil {
		// Stale but present: hand back the old answer, refresh off the request.
		m.refreshing = true
		c := m.counts
		m.mu.Unlock()
		go func() {
			counts := s.queryParkFireCounts()
			m.mu.Lock()
			if counts != nil {
				m.counts, m.maxRowid = counts, maxRowid
			}
			m.refreshing = false
			m.mu.Unlock()
			if counts != nil {
				s.storeParkFireCountMemo(counts, maxRowid)
			}
		}()
		return c
	}
	m.mu.Unlock()

	counts := s.queryParkFireCounts()
	if counts == nil {
		return map[string]int64{}
	}
	m.mu.Lock()
	m.counts, m.maxRowid = counts, maxRowid
	m.mu.Unlock()
	s.storeParkFireCountMemo(counts, maxRowid)
	return counts
}

// loadParkFireCountMemo reads the persisted memo and the MAX(rowid) it was
// computed for. ok=false when there is none (or it does not parse).
func (s *Server) loadParkFireCountMemo() (map[string]int64, int64, bool) {
	var fp, val string
	if err := s.DB.QueryRow(`SELECT fingerprint, value_json FROM server_memo WHERE key = ?`, parkFireCountMemoKey).Scan(&fp, &val); err != nil {
		return nil, 0, false
	}
	var counts map[string]int64
	if json.Unmarshal([]byte(val), &counts) != nil || len(counts) == 0 {
		return nil, 0, false
	}
	rowid, err := strconv.ParseInt(fp, 10, 64)
	if err != nil {
		return nil, 0, false
	}
	return counts, rowid, true
}

func (s *Server) storeParkFireCountMemo(counts map[string]int64, maxRowid int64) {
	b, err := json.Marshal(counts)
	if err != nil {
		return
	}
	_, _ = s.DB.Exec(`INSERT INTO server_memo(key, fingerprint, value_json, computed_at) VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
		ON CONFLICT(key) DO UPDATE SET fingerprint = excluded.fingerprint, value_json = excluded.value_json, computed_at = excluded.computed_at`,
		parkFireCountMemoKey, strconv.FormatInt(maxRowid, 10), string(b))
}

func (s *Server) queryParkFireCounts() map[string]int64 {
	rows, err := s.DB.Query(`
		SELECT protected_area_id, COUNT(*) AS fire_count
		FROM fire_detections
		WHERE protected_area_id IS NOT NULL AND protected_area_id != ''` + fireInsideOnlySQL + `
		GROUP BY protected_area_id`)
	if err != nil {
		return nil
	}
	defer rows.Close()
	counts := map[string]int64{}
	for rows.Next() {
		var parkID string
		var n int64
		if err := rows.Scan(&parkID, &n); err == nil {
			counts[parkID] = n
		}
	}
	if len(counts) == 0 {
		return nil // a no-op must not read as an answer (invariant 1)
	}
	return counts
}

// HandleAPIExportParks exports park data as CSV.
// GET /api/export/parks?format=csv
func (s *Server) HandleAPIExportParks(w http.ResponseWriter, r *http.Request) {
	format := r.URL.Query().Get("format")
	if format != "csv" {
		http.Error(w, "Only CSV format is supported. Use ?format=csv", http.StatusBadRequest)
		return
	}

	// Get park data from AreaStore
	if s.AreaStore == nil {
		http.Error(w, "Area store not configured", http.StatusServiceUnavailable)
		return
	}

	// Build map of park IDs to export rows from area data
	parkData := make(map[string]*ParkExportRow)
	for _, area := range s.AreaStore.Areas {
		parkData[area.ID] = &ParkExportRow{
			ParkID:  area.ID,
			Name:    area.Name,
			Country: area.Country,
			AreaKm2: area.AreaKm2,
		}
	}

	// Fire count per park, inside the boundary only — `protected_area_id`
	// alone is a 100 km catchment (srv/fire_containment.go), and a published
	// CSV is the figure people quote. Memoised: see parkFireCounts.
	for parkID, count := range s.parkFireCounts() {
		if row, ok := parkData[parkID]; ok {
			row.FireCount = count
		}
	}

	// Fire chains and the vanguard among them — from one table, so the two
	// columns are a ratio a reader may take. `vanguard` is NULL where the
	// area's season front is not built; it counts as 0 here because the CSV
	// has no NULL, and fire_groups beside it says how much was looked at.
	//
	// Two queries, not one: SUM(vanguard) over the whole table is not
	// covered by any index (4.3 s, reads every page); COUNT(*) per park is
	// covered by idx_fg_dist_park (0.1 s) and the vanguard rows by the
	// partial idx_fg_vanguard (51k rows, 0.1 s).
	chainRows, err := s.DB.Query(`
		SELECT park_id, COUNT(*) FROM feature_geometries
		WHERE feature_type = 'fire_trajectory' GROUP BY park_id`)
	if err == nil {
		defer chainRows.Close()
		for chainRows.Next() {
			var parkID string
			var n int64
			if err := chainRows.Scan(&parkID, &n); err == nil {
				if row, ok := parkData[parkID]; ok {
					row.FireGroups = n
				}
			}
		}
	}
	// One pass over the partial index for the count and the still-moving
	// count (json_extract on ~60k rows, ~0.1 s); the tracker word comes
	// from the ≤ 200-row fire_vanguard_kf table.
	vanRows, err := s.DB.Query(`
		SELECT park_id, COUNT(*),
		       SUM(CASE WHEN feature_type = 'fire_vanguard' AND json_extract(properties_json, '$.end_cause') = 'ongoing' THEN 1 ELSE 0 END)
		FROM feature_geometries INDEXED BY idx_fg_vanguard
		WHERE` + vanguardRowsSQL + ` GROUP BY park_id`)
	if err == nil {
		defer vanRows.Close()
		for vanRows.Next() {
			var parkID string
			var v, moving int64
			if err := vanRows.Scan(&parkID, &v, &moving); err == nil {
				if row, ok := parkData[parkID]; ok {
					row.VanguardGroups, row.VanguardMoving = v, moving
				}
			}
		}
	}
	for _, row := range parkData {
		row.VanguardTracker = "groups"
	}
	if kfRows, err := s.DB.Query(`SELECT area_id FROM fire_vanguard_kf`); err == nil {
		defer kfRows.Close()
		for kfRows.Next() {
			var id string
			if kfRows.Scan(&id) == nil {
				if row, ok := parkData[id]; ok {
					row.VanguardTracker = "kf"
				}
			}
		}
	}

	// Query park_settlements: count per park. Same filter as every other
	// settlement count in the app (srv/mining_flag.go) -- a CSV export that
	// disagreed with the panel above it would be read as the authoritative one.
	settlementRows, err := s.DB.Query(`
		SELECT park_id, COUNT(*) as settlement_count 
		FROM park_settlements 
		WHERE 1=1` + settlementFilterSQL("narrative", "polygon_ids") + `
		GROUP BY park_id
	`)
	if err == nil {
		defer settlementRows.Close()
		for settlementRows.Next() {
			var parkID string
			var count int64
			if err := settlementRows.Scan(&parkID, &count); err == nil {
				if row, ok := parkData[parkID]; ok {
					row.SettlementCount = count
				}
			}
		}
	}

	// Query deforestation_events: sum area per park
	deforestRows, err := s.DB.Query(`
		SELECT park_id, SUM(area_km2) as total_area 
		FROM deforestation_events 
		GROUP BY park_id
	`)
	if err == nil {
		defer deforestRows.Close()
		for deforestRows.Next() {
			var parkID string
			var totalArea float64
			if err := deforestRows.Scan(&parkID, &totalArea); err == nil {
				if row, ok := parkData[parkID]; ok {
					row.DeforestationKm2 = totalArea
				}
			}
		}
	}

	// Query osm_roadless_data: roadless percentage per park
	roadlessRows, err := s.DB.Query(`
		SELECT park_id, roadless_percentage 
		FROM osm_roadless_data 
		WHERE roadless_percentage IS NOT NULL
	`)
	if err == nil {
		defer roadlessRows.Close()
		for roadlessRows.Next() {
			var parkID string
			var pct float64
			if err := roadlessRows.Scan(&parkID, &pct); err == nil {
				if row, ok := parkData[parkID]; ok {
					row.RoadlessPct = pct
				}
			}
		}
	}

	// Set headers for CSV download
	filename := fmt.Sprintf("parks_export_%s.csv", time.Now().Format("2006-01-02"))
	w.Header().Set("Content-Type", "text/csv")
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%q", filename))

	// Write CSV
	csvWriter := csv.NewWriter(w)
	defer csvWriter.Flush()

	// Write header
	header := []string{"park_id", "name", "country", "area_km2", "fire_count", "fire_groups", "vanguard_groups", "vanguard_tracker", "vanguard_moving", "settlement_count", "deforestation_km2", "roadless_pct"}
	if err := csvWriter.Write(header); err != nil {
		http.Error(w, "Failed to write CSV header", http.StatusInternalServerError)
		return
	}

	// Write data rows
	for _, row := range parkData {
		record := []string{
			row.ParkID,
			row.Name,
			row.Country,
			fmt.Sprintf("%.2f", row.AreaKm2),
			fmt.Sprintf("%d", row.FireCount),
			fmt.Sprintf("%d", row.FireGroups),
			fmt.Sprintf("%d", row.VanguardGroups),
			row.VanguardTracker,
			fmt.Sprintf("%d", row.VanguardMoving),
			fmt.Sprintf("%d", row.SettlementCount),
			fmt.Sprintf("%.4f", row.DeforestationKm2),
			fmt.Sprintf("%.2f", row.RoadlessPct),
		}
		if err := csvWriter.Write(record); err != nil {
			return // Connection closed or error
		}
	}
}
