package srv

import (
	"database/sql"
	"encoding/json"
	"net/http"
)

// ParkStats combines fire, settlement, and roadless data for a park
type ParkStats struct {
	ParkID string `json:"park_id"`
	
	// Fire infraction data
	Fire *FireStats `json:"fire,omitempty"`
	
	// Settlement/GHSL data
	Settlement *SettlementStats `json:"settlement,omitempty"`
	
	// Roadless data
	Roadless *RoadlessStats `json:"roadless,omitempty"`
}

type FireStats struct {
	Year              int     `json:"year"`
	GroupsEntered     int     `json:"groups_entered"`
	GroupsStoppedInside int   `json:"groups_stopped_inside"`
	GroupsTransited   int     `json:"groups_transited"`
	ResponseRate      float64 `json:"response_rate"`    // % stopped inside
	AvgDaysInside     float64 `json:"avg_days_inside"`
}

type SettlementStats struct {
	BuiltUpKm2      float64 `json:"built_up_km2"`
	SettlementCount int     `json:"settlement_count"`
}

type RoadlessStats struct {
	RoadlessPercentage float64 `json:"roadless_percentage"`
	TotalRoadKm        float64 `json:"total_road_km"`
}

// HandleAPIParkStats returns combined park statistics
// GET /api/parks/{id}/stats
func (s *Server) HandleAPIParkStats(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}
	
	// Map WDPA ID to internal park_id if needed
	internalID := parkID
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.WDPAID == parkID {
				internalID = area.ID
				break
			}
		}
	}
	
	stats := ParkStats{ParkID: parkID}
	
	// Query fire infraction data (most recent year)
	var fire FireStats
	err := s.DB.QueryRow(`
		SELECT year, total_groups, groups_stopped_inside, groups_transited, avg_days_burning
		FROM park_group_infractions 
		WHERE park_id = ?
		ORDER BY year DESC
		LIMIT 1
	`, internalID).Scan(&fire.Year, &fire.GroupsEntered, &fire.GroupsStoppedInside, &fire.GroupsTransited, &fire.AvgDaysInside)
	
	if err == nil {
		if fire.GroupsEntered > 0 {
			fire.ResponseRate = float64(fire.GroupsStoppedInside) / float64(fire.GroupsEntered) * 100
		}
		stats.Fire = &fire
	} else if err != sql.ErrNoRows {
		// Log error but continue
	}
	
	// Query GHSL settlement data
	var settlement SettlementStats
	err = s.DB.QueryRow(`
		SELECT built_up_km2, settlement_count
		FROM ghsl_data
		WHERE park_id = ?
	`, internalID).Scan(&settlement.BuiltUpKm2, &settlement.SettlementCount)
	
	if err == nil {
		stats.Settlement = &settlement
	}
	
	// Query OSM roadless data
	var roadless RoadlessStats
	err = s.DB.QueryRow(`
		SELECT roadless_percentage, total_road_km
		FROM osm_roadless_data
		WHERE park_id = ?
	`, internalID).Scan(&roadless.RoadlessPercentage, &roadless.TotalRoadKm)
	
	if err == nil {
		stats.Roadless = &roadless
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(stats)
}
