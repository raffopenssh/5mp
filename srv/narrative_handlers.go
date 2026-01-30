package srv

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strings"
)

// OSMPlace represents a place from the osm_places table
type OSMPlace struct {
	ID        int64   `json:"id"`
	ParkID    string  `json:"park_id"`
	PlaceType string  `json:"place_type"`
	Name      string  `json:"name"`
	Lat       float64 `json:"lat"`
	Lon       float64 `json:"lon"`
	Distance  float64 `json:"distance_km,omitempty"` // Calculated distance from a point
}

// FireNarrative contains rich textual description of fire movements
type FireNarrative struct {
	ParkID     string           `json:"park_id"`
	ParkName   string           `json:"park_name"`
	Year       int              `json:"year"`
	Summary    string           `json:"summary"`
	Narratives []FireGroupStory `json:"narratives"`
	KeyPlaces  []OSMPlace       `json:"key_places"`
}

// FireGroupStory describes a single fire group's movement
type FireGroupStory struct {
	GroupNum      int      `json:"group_num"`
	OriginDesc    string   `json:"origin_desc"`
	DestDesc      string   `json:"dest_desc"`
	EntryDate     string   `json:"entry_date"`
	LastInside    string   `json:"last_inside"`
	DaysInside    int      `json:"days_inside"`
	FiresInside   int      `json:"fires_inside"`
	Outcome       string   `json:"outcome"`
	Narrative     string   `json:"narrative"`
	NearbyPlaces  []string `json:"nearby_places"`
	RiversCrossed []string `json:"rivers_crossed,omitempty"`
}

// DeforestationNarrative contains rich textual description of forest loss
type DeforestationNarrative struct {
	ParkID      string                    `json:"park_id"`
	ParkName    string                    `json:"park_name"`
	Summary     string                    `json:"summary"`
	YearlyStory []DeforestationYearStory  `json:"yearly_stories"`
	TotalLoss   float64                   `json:"total_loss_km2"`
	WorstYear   int                       `json:"worst_year"`
}

// DeforestationYearStory describes forest loss for a single year
type DeforestationYearStory struct {
	Year         int      `json:"year"`
	AreaKm2      float64  `json:"area_km2"`
	PatternType  string   `json:"pattern_type"`
	Narrative    string   `json:"narrative"`
	NearbyPlaces []string `json:"nearby_places"`
}

// SettlementNarrative contains description of settlements (placeholder)
type SettlementNarrative struct {
	ParkID   string `json:"park_id"`
	ParkName string `json:"park_name"`
	Summary  string `json:"summary"`
	Status   string `json:"status"` // "pending" until GHSL data ready
}

// haversineDistance calculates distance between two lat/lon points in km
func haversineDistance(lat1, lon1, lat2, lon2 float64) float64 {
	const R = 6371.0 // Earth's radius in km
	dLat := (lat2 - lat1) * math.Pi / 180
	dLon := (lon2 - lon1) * math.Pi / 180
	a := math.Sin(dLat/2)*math.Sin(dLat/2) +
		math.Cos(lat1*math.Pi/180)*math.Cos(lat2*math.Pi/180)*
			math.Sin(dLon/2)*math.Sin(dLon/2)
	c := 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))
	return R * c
}

// bearingTo calculates the initial bearing from point 1 to point 2 in degrees (0-360)
func bearingTo(lat1, lon1, lat2, lon2 float64) float64 {
	lat1Rad := lat1 * math.Pi / 180
	lat2Rad := lat2 * math.Pi / 180
	dLon := (lon2 - lon1) * math.Pi / 180

	y := math.Sin(dLon) * math.Cos(lat2Rad)
	x := math.Cos(lat1Rad)*math.Sin(lat2Rad) - math.Sin(lat1Rad)*math.Cos(lat2Rad)*math.Cos(dLon)
	bearing := math.Atan2(y, x) * 180 / math.Pi
	return math.Mod(bearing+360, 360) // Normalize to 0-360
}

// bearingToCardinal converts a bearing in degrees to a cardinal/intercardinal direction
// Uses 16-point compass with boundaries: N=348.75-11.25, NNE=11.25-33.75, etc.
func bearingToCardinal(bearing float64) string {
	// 16-point compass directions
	directions := []string{
		"north", "north-northeast", "northeast", "east-northeast",
		"east", "east-southeast", "southeast", "south-southeast",
		"south", "south-southwest", "southwest", "west-southwest",
		"west", "west-northwest", "northwest", "north-northwest",
	}
	// Each direction covers 22.5 degrees, offset by 11.25 to center on cardinal points
	// Adding 11.25 shifts so that 0° is center of "north" range
	index := int(math.Floor((bearing+11.25)/22.5)) % 16
	return directions[index]
}

// bearingToCardinalWithDegrees returns a compass direction with bearing in degrees
// Example: "north-northeast (bearing 022°)"
func bearingToCardinalWithDegrees(bearing float64) string {
	cardinal := bearingToCardinal(bearing)
	return fmt.Sprintf("%s (bearing %03.0f°)", cardinal, bearing)
}

// formatPlaceWithDirection formats a place name with distance and direction from a reference point
func formatPlaceWithDirection(placeName, placeType string, distKm, refLat, refLon, placeLat, placeLon float64) string {
	bearing := bearingTo(refLat, refLon, placeLat, placeLon)
	direction := bearingToCardinal(bearing)
	
	if placeType == "river" || placeType == "stream" {
		return fmt.Sprintf("%.0fkm %s of %s", distKm, direction, placeName)
	}
	return fmt.Sprintf("%.0fkm %s of %s", distKm, direction, placeName)
}

// findNearestPlaces finds the nearest OSM places to a given coordinate
func (s *Server) findNearestPlaces(parkID string, lat, lon float64, limit int, placeTypes []string) ([]OSMPlace, error) {
	var places []OSMPlace
	
	// Build query - search within park and nearby (expand search area)
	query := `
		SELECT id, park_id, place_type, name, lat, lon
		FROM osm_places
		WHERE park_id = ?
		  AND lat BETWEEN ? AND ?
		  AND lon BETWEEN ? AND ?
	`
	args := []interface{}{parkID, lat - 1.0, lat + 1.0, lon - 1.0, lon + 1.0}
	
	if len(placeTypes) > 0 {
		placeholders := make([]string, len(placeTypes))
		for i := range placeTypes {
			placeholders[i] = "?"
			args = append(args, placeTypes[i])
		}
		query += " AND place_type IN (" + strings.Join(placeholders, ",") + ")"
	}
	
	rows, err := s.DB.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	
	for rows.Next() {
		var p OSMPlace
		if err := rows.Scan(&p.ID, &p.ParkID, &p.PlaceType, &p.Name, &p.Lat, &p.Lon); err != nil {
			continue
		}
		p.Distance = haversineDistance(lat, lon, p.Lat, p.Lon)
		places = append(places, p)
	}
	
	// Sort by distance
	sort.Slice(places, func(i, j int) bool {
		return places[i].Distance < places[j].Distance
	})
	
	// Limit results
	if len(places) > limit {
		places = places[:limit]
	}
	
	return places, nil
}

// describeLocation returns a human-readable description of a location
func (s *Server) describeLocation(parkID string, lat, lon float64) string {
	// Find nearest settlement
	settlements, _ := s.findNearestPlaces(parkID, lat, lon, 1, []string{"village", "hamlet", "town", "city"})
	
	// Find nearest river
	rivers, _ := s.findNearestPlaces(parkID, lat, lon, 1, []string{"river", "stream"})
	
	var parts []string
	
	if len(settlements) > 0 && settlements[0].Distance < 30 {
		p := settlements[0]
		if p.Distance < 5 {
			parts = append(parts, fmt.Sprintf("near %s", p.Name))
		} else {
			// Direction FROM settlement TO the location
			bearing := bearingTo(p.Lat, p.Lon, lat, lon)
			direction := bearingToCardinal(bearing)
			parts = append(parts, fmt.Sprintf("%.0f km %s of %s", p.Distance, direction, p.Name))
		}
	}
	
	if len(rivers) > 0 && rivers[0].Distance < 20 {
		p := rivers[0]
		if p.Distance < 3 {
			parts = append(parts, fmt.Sprintf("along the %s", p.Name))
		} else {
			bearing := bearingTo(p.Lat, p.Lon, lat, lon)
			direction := bearingToCardinal(bearing)
			parts = append(parts, fmt.Sprintf("%.0f km %s of the %s", p.Distance, direction, p.Name))
		}
	}
	
	if len(parts) == 0 {
		return fmt.Sprintf("at coordinates (%.3f°, %.3f°)", lat, lon)
	}
	
	return strings.Join(parts, ", ")
}

// HandleAPIFireNarrative returns rich textual description of fire movements
// GET /api/parks/{id}/fire-narrative
func (s *Server) HandleAPIFireNarrative(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}
	
	// Map WDPA ID to internal park_id if needed
	internalID := parkID
	parkName := parkID
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.WDPAID == parkID || area.ID == parkID {
				internalID = area.ID
				parkName = area.Name
				break
			}
		}
	}
	
	// Get most recent year's fire data with trajectories
	var year int
	var totalGroups, stoppedInside, transited int
	var trajJSON sql.NullString
	
	err := s.DB.QueryRow(`
		SELECT year, total_groups, groups_stopped_inside, groups_transited, trajectories_json
		FROM park_group_infractions 
		WHERE park_id = ? AND total_groups > 0
		ORDER BY year DESC
		LIMIT 1
	`, internalID).Scan(&year, &totalGroups, &stoppedInside, &transited, &trajJSON)
	
	narrative := FireNarrative{
		ParkID:   internalID,
		ParkName: parkName,
		Year:     year,
	}
	
	if err == sql.ErrNoRows || totalGroups == 0 {
		narrative.Summary = fmt.Sprintf("No significant fire group incursions recorded for %s.", parkName)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(narrative)
		return
	}
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	
	// Build summary
	var summaryParts []string
	summaryParts = append(summaryParts, fmt.Sprintf("In %d, %d fire group(s) entered %s.", year, totalGroups, parkName))
	if stoppedInside > 0 {
		summaryParts = append(summaryParts, fmt.Sprintf("%d group(s) stopped inside (possible ranger contact).", stoppedInside))
	}
	if transited > 0 {
		summaryParts = append(summaryParts, fmt.Sprintf("%d group(s) transited through without stopping.", transited))
	}
	narrative.Summary = strings.Join(summaryParts, " ")
	
	// Parse trajectories and build detailed stories
	if trajJSON.Valid && trajJSON.String != "" {
		var trajs []FireGroupTrajectory
		if json.Unmarshal([]byte(trajJSON.String), &trajs) == nil {
			for i, t := range trajs {
				story := FireGroupStory{
					GroupNum:    i + 1,
					EntryDate:   t.EntryDate,
					LastInside:  t.LastInside,
					DaysInside:  t.DaysInside,
					FiresInside: t.FiresInside,
					Outcome:     t.Outcome,
				}
				
				// Calculate trajectory bearing (azimuth) from origin to destination
				trajBearing := bearingTo(t.Origin.Lat, t.Origin.Lon, t.Destination.Lat, t.Destination.Lon)
				movementDesc := fmt.Sprintf("moving %s", bearingToCardinalWithDegrees(trajBearing))
				
				// Describe origin location
				story.OriginDesc = s.describeLocation(internalID, t.Origin.Lat, t.Origin.Lon)
				
				// If no nearby place found, include coordinates with movement direction
				if strings.HasPrefix(story.OriginDesc, "at coordinates") {
					story.OriginDesc = fmt.Sprintf("(%.3f°, %.3f°), %s",
						t.Origin.Lat, t.Origin.Lon, movementDesc)
				} else {
					// Add movement direction to location description
					story.OriginDesc = fmt.Sprintf("%s, %s", story.OriginDesc, movementDesc)
				}
				
				// Describe destination location
				story.DestDesc = s.describeLocation(internalID, t.Destination.Lat, t.Destination.Lon)
				
				// Find rivers that might have been crossed
				rivers, _ := s.findNearestPlaces(internalID, 
					(t.Origin.Lat+t.Destination.Lat)/2, 
					(t.Origin.Lon+t.Destination.Lon)/2, 
					3, []string{"river"})
				for _, r := range rivers {
					if r.Distance < 15 {
						story.RiversCrossed = append(story.RiversCrossed, r.Name)
					}
				}
				
				// Build narrative text
				var narr strings.Builder
				narr.WriteString(fmt.Sprintf("Fire group %d originated %s on %s. ", 
					i+1, story.OriginDesc, t.EntryDate))
				
				if len(story.RiversCrossed) > 0 {
					unique := uniqueStrings(story.RiversCrossed)
					if len(unique) == 1 {
						narr.WriteString(fmt.Sprintf("The group crossed near the %s. ", unique[0]))
					} else {
						narr.WriteString(fmt.Sprintf("The group crossed near the %s. ", strings.Join(unique, " and ")))
					}
				}
				
				daysWord := "days"
				if t.DaysInside == 1 {
					daysWord = "day"
				}
				narr.WriteString(fmt.Sprintf("Burned inside the park for %d %s (%d fire detections). ", 
					t.DaysInside, daysWord, t.FiresInside))
				
				switch t.Outcome {
				case "STOPPED_INSIDE":
					narr.WriteString(fmt.Sprintf("Last detected %s - fire stopped, possibly due to ranger intervention.", 
						story.DestDesc))
				case "TRANSITED":
					narr.WriteString(fmt.Sprintf("Exited the park %s on %s - transited without being stopped.", 
						story.DestDesc, t.LastInside))
				default:
					narr.WriteString(fmt.Sprintf("Last detected %s.", story.DestDesc))
				}
				
				story.Narrative = narr.String()
				narrative.Narratives = append(narrative.Narratives, story)
			}
		}
	}
	
	// Get key places in the park for context
	keyPlaces, _ := s.findNearestPlaces(internalID, 0, 0, 0, nil)
	if len(keyPlaces) == 0 {
		// Get all places for this park
		rows, err := s.DB.Query(`
			SELECT id, park_id, place_type, name, lat, lon
			FROM osm_places
			WHERE park_id = ?
			LIMIT 20
		`, internalID)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var p OSMPlace
				if rows.Scan(&p.ID, &p.ParkID, &p.PlaceType, &p.Name, &p.Lat, &p.Lon) == nil {
					narrative.KeyPlaces = append(narrative.KeyPlaces, p)
				}
			}
		}
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(narrative)
}

// HandleAPIDeforestationNarrative returns rich textual description of forest loss
// GET /api/parks/{id}/deforestation-narrative
func (s *Server) HandleAPIDeforestationNarrative(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}
	
	// Map WDPA ID to internal park_id if needed
	internalID := parkID
	parkName := parkID
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.WDPAID == parkID || area.ID == parkID {
				internalID = area.ID
				parkName = area.Name
				break
			}
		}
	}
	
	narrative := DeforestationNarrative{
		ParkID:   internalID,
		ParkName: parkName,
	}
	
	// Query deforestation events
	rows, err := s.DB.Query(`
		SELECT year, area_km2, pattern_type, lat, lon, description
		FROM deforestation_events
		WHERE park_id = ?
		ORDER BY year DESC
	`, internalID)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	
	var totalLoss float64
	var worstYear int
	var worstLoss float64
	
	for rows.Next() {
		var year int
		var area float64
		var patternType sql.NullString
		var lat, lon float64
		var description sql.NullString
		
		if err := rows.Scan(&year, &area, &patternType, &lat, &lon, &description); err != nil {
			continue
		}
		
		totalLoss += area
		if area > worstLoss {
			worstLoss = area
			worstYear = year
		}
		
		story := DeforestationYearStory{
			Year:        year,
			AreaKm2:     area,
			PatternType: patternType.String,
		}
		
		// Find nearby places for context (settlements and rivers)
		settlements, _ := s.findNearestPlaces(internalID, lat, lon, 3, []string{"village", "hamlet", "town", "city"})
		rivers, _ := s.findNearestPlaces(internalID, lat, lon, 3, []string{"river", "stream"})
		
		seen := make(map[string]bool)
		for _, p := range settlements {
			key := p.Name
			if !seen[key] && p.Distance < 100 {
				seen[key] = true
				desc := formatPlaceWithDirection(p.Name, p.PlaceType, p.Distance, lat, lon, p.Lat, p.Lon)
				story.NearbyPlaces = append(story.NearbyPlaces, desc)
			}
		}
		for _, p := range rivers {
			key := p.Name
			if !seen[key] && p.Distance < 100 {
				seen[key] = true
				desc := formatPlaceWithDirection(p.Name+" River", p.PlaceType, p.Distance, lat, lon, p.Lat, p.Lon)
				story.NearbyPlaces = append(story.NearbyPlaces, desc)
			}
		}
		
		// Build narrative
		locationDesc := s.describeLocation(internalID, lat, lon)
		patternDesc := describePattern(patternType.String)
		
		story.Narrative = fmt.Sprintf("In %d, %.2f km² of forest was lost %s. %s",
			year, area, locationDesc, patternDesc)
		
		narrative.YearlyStory = append(narrative.YearlyStory, story)
	}
	
	narrative.TotalLoss = totalLoss
	narrative.WorstYear = worstYear
	
	// Build summary
	if totalLoss == 0 {
		narrative.Summary = fmt.Sprintf("No significant deforestation events recorded for %s.", parkName)
	} else {
		narrative.Summary = fmt.Sprintf("%s has experienced %.2f km² of forest loss across %d recorded years. The worst year was %d with %.2f km² lost.",
			parkName, totalLoss, len(narrative.YearlyStory), worstYear, worstLoss)
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(narrative)
}

// HandleAPISettlementNarrative returns description of settlements (placeholder)
// GET /api/parks/{id}/settlement-narrative
func (s *Server) HandleAPISettlementNarrative(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}
	
	// Map WDPA ID to internal park_id if needed
	internalID := parkID
	parkName := parkID
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.WDPAID == parkID || area.ID == parkID {
				internalID = area.ID
				parkName = area.Name
				break
			}
		}
	}
	
	// Check if we have GHSL data
	var builtUp float64
	var settlementCount int
	err := s.DB.QueryRow(`
		SELECT built_up_km2, settlement_count
		FROM ghsl_data
		WHERE park_id = ?
	`, internalID).Scan(&builtUp, &settlementCount)
	
	narrative := SettlementNarrative{
		ParkID:   internalID,
		ParkName: parkName,
	}
	
	if err == sql.ErrNoRows {
		narrative.Status = "pending"
		narrative.Summary = fmt.Sprintf("Settlement analysis for %s is pending. GHSL (Global Human Settlement Layer) data has not yet been processed for this park.", parkName)
	} else if err != nil {
		narrative.Status = "error"
		narrative.Summary = "Error retrieving settlement data."
	} else {
		narrative.Status = "complete"
		if settlementCount == 0 && builtUp == 0 {
			narrative.Summary = fmt.Sprintf("No permanent settlements detected inside %s boundaries. This suggests good protection from human encroachment.", parkName)
		} else {
			narrative.Summary = fmt.Sprintf("%s contains %d detected settlement(s) covering approximately %.2f km² of built-up area. Further analysis with OSM place data can provide specific village and hamlet names.", parkName, settlementCount, builtUp)
		}
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(narrative)
}

// Helper function to describe deforestation patterns
func describePattern(pattern string) string {
	switch pattern {
	case "strip":
		return "The linear pattern suggests road construction or logging track expansion."
	case "cluster":
		return "The clustered pattern may indicate mining activity or localized clearing."
	case "scattered":
		return "The scattered pattern is consistent with smallholder agricultural expansion."
	case "edge":
		return "Loss concentrated along park boundaries indicates agricultural encroachment from surrounding communities."
	default:
		return ""
	}
}

// Helper function to get unique strings from a slice
func uniqueStrings(input []string) []string {
	seen := make(map[string]bool)
	var result []string
	for _, s := range input {
		if !seen[s] {
			seen[s] = true
			result = append(result, s)
		}
	}
	return result
}
