package srv

import (
	"encoding/csv"
	"fmt"
	"net/http"
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
	FireGroups       int64 // fire_trajectory chains (feature_geometries)
	VanguardGroups   int64 // of those, began 10–60 d ahead of the season front (NULL front → 0, and fire_groups says so)
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
// one goroutine recomputes (stale-while-revalidate); only the very first
// request after a restart pays the full price.
type parkFireCountMemo struct {
	mu         sync.Mutex
	maxRowid   int64
	counts     map[string]int64
	refreshing bool
}

var parkFireCounts = &parkFireCountMemo{}

func (s *Server) parkFireCounts() map[string]int64 {
	var maxRowid int64
	_ = s.DB.QueryRow(`SELECT COALESCE(MAX(rowid), 0) FROM fire_detections`).Scan(&maxRowid)

	m := parkFireCounts
	m.mu.Lock()
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
	return counts
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
	vanRows, err := s.DB.Query(`
		SELECT park_id, COUNT(*) FROM feature_geometries INDEXED BY idx_fg_vanguard
		WHERE vanguard = 1 GROUP BY park_id`)
	if err == nil {
		defer vanRows.Close()
		for vanRows.Next() {
			var parkID string
			var v int64
			if err := vanRows.Scan(&parkID, &v); err == nil {
				if row, ok := parkData[parkID]; ok {
					row.VanguardGroups = v
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
	header := []string{"park_id", "name", "country", "area_km2", "fire_count", "fire_groups", "vanguard_groups", "settlement_count", "deforestation_km2", "roadless_pct"}
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
			fmt.Sprintf("%d", row.SettlementCount),
			fmt.Sprintf("%.4f", row.DeforestationKm2),
			fmt.Sprintf("%.2f", row.RoadlessPct),
		}
		if err := csvWriter.Write(record); err != nil {
			return // Connection closed or error
		}
	}
}
