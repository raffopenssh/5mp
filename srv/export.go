package srv

import (
	"encoding/csv"
	"fmt"
	"net/http"
	"time"
)

// ParkExportRow represents a single park's data for CSV export.
type ParkExportRow struct {
	ParkID           string
	Name             string
	Country          string
	AreaKm2          float64
	FireCount        int64
	SettlementCount  int64
	DeforestationKm2 float64
	RoadlessPct      float64
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

	// Query fire_detections: count per park
	fireRows, err := s.DB.Query(`
		SELECT protected_area_id, COUNT(*) as fire_count 
		FROM fire_detections 
		WHERE protected_area_id IS NOT NULL AND protected_area_id != ''
		GROUP BY protected_area_id
	`)
	if err == nil {
		defer fireRows.Close()
		for fireRows.Next() {
			var parkID string
			var count int64
			if err := fireRows.Scan(&parkID, &count); err == nil {
				if row, ok := parkData[parkID]; ok {
					row.FireCount = count
				}
			}
		}
	}

	// Query park_settlements: count per park
	settlementRows, err := s.DB.Query(`
		SELECT park_id, COUNT(*) as settlement_count 
		FROM park_settlements 
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
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%s", filename))

	// Write CSV
	csvWriter := csv.NewWriter(w)
	defer csvWriter.Flush()

	// Write header
	header := []string{"park_id", "name", "country", "area_km2", "fire_count", "settlement_count", "deforestation_km2", "roadless_pct"}
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
			fmt.Sprintf("%d", row.SettlementCount),
			fmt.Sprintf("%.4f", row.DeforestationKm2),
			fmt.Sprintf("%.2f", row.RoadlessPct),
		}
		if err := csvWriter.Write(record); err != nil {
			return // Connection closed or error
		}
	}
}
