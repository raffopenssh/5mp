package srv

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"
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
	ParkID       string            `json:"park_id"`
	ParkName     string            `json:"park_name"`
	Year         int               `json:"year"`
	Summary      string            `json:"summary"`
	Narratives   []FireGroupStory  `json:"narratives"`
	KeyPlaces    []OSMPlace        `json:"key_places"`
	Hotspots     []FireHotspot     `json:"hotspots,omitempty"`
	Trend        *FireTrendAnalysis `json:"trend,omitempty"`
	ResponseRate float64           `json:"response_rate"`
	TotalFires   int               `json:"total_fires"`
	PeakMonth    string            `json:"peak_month,omitempty"`
}

// FireHotspot represents a geographic concentration of fire activity
type FireHotspot struct {
	Lat          float64  `json:"lat"`
	Lon          float64  `json:"lon"`
	FireCount    int      `json:"fire_count"`
	Percentage   float64  `json:"percentage"`
	Description  string   `json:"description"`
	NearbyPlaces []string `json:"nearby_places"`
}

// FireTrendAnalysis provides multi-year trend information
type FireTrendAnalysis struct {
	Years           []FireYearSummary `json:"years"`
	TrendDirection  string            `json:"trend_direction"` // increasing, decreasing, stable
	AvgResponseRate float64           `json:"avg_response_rate"`
	WorstYear       int               `json:"worst_year"`
	WorstYearGroups int               `json:"worst_year_groups"`
	BestYear        int               `json:"best_year"`
	BestYearRate    float64           `json:"best_year_rate"`
	Narrative       string            `json:"narrative"`
}

// FireYearSummary provides per-year fire statistics
type FireYearSummary struct {
	Year            int     `json:"year"`
	TotalGroups     int     `json:"total_groups"`
	StoppedInside   int     `json:"stopped_inside"`
	Transited       int     `json:"transited"`
	ResponseRate    float64 `json:"response_rate"`
	TotalFires      int     `json:"total_fires"`
	AvgDaysBurning  float64 `json:"avg_days_burning"`
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
	ParkID            string                    `json:"park_id"`
	ParkName          string                    `json:"park_name"`
	Summary           string                    `json:"summary"`
	YearlyStory       []DeforestationYearStory  `json:"yearly_stories"`
	TotalLoss         float64                   `json:"total_loss_km2"`
	WorstYear         int                       `json:"worst_year"`
	TrendDirection    string                    `json:"trend_direction"`       // "improving", "worsening", "stable"
	TrendPercentChange float64                  `json:"trend_percent_change"`  // percentage change between periods
	FiveYearAvgEarly  float64                   `json:"five_year_avg_early"`   // earliest 5-year average
	FiveYearAvgRecent float64                   `json:"five_year_avg_recent"`  // most recent 5-year average
	Hotspots          []DeforestationHotspot    `json:"hotspots,omitempty"`    // worst cluster hotspots
}

// DeforestationYearStory describes forest loss for a single year
type DeforestationYearStory struct {
	Year         int      `json:"year"`
	AreaKm2      float64  `json:"area_km2"`
	PatternType  string   `json:"pattern_type"`
	Narrative    string   `json:"narrative"`
	NearbyPlaces []string `json:"nearby_places"`
}

// DeforestationHotspot describes a significant cluster of deforestation
type DeforestationHotspot struct {
	Year        int     `json:"year"`
	ClusterID   int     `json:"cluster_id"`
	AreaKm2     float64 `json:"area_km2"`
	Lat         float64 `json:"lat"`
	Lon         float64 `json:"lon"`
	PatternType string  `json:"pattern_type"`
	Description string  `json:"description"`
}

// SettlementNarrative contains description of settlements and human-wildlife interface
type SettlementNarrative struct {
	ParkID              string               `json:"park_id"`
	ParkName            string               `json:"park_name"`
	Summary             string               `json:"summary"`
	Status              string               `json:"status"`
	SettlementCount     int                  `json:"settlement_count"`
	TotalPopulation     int64                `json:"total_population"`
	PopulationDensity   float64              `json:"population_density_per_km2"`
	ParkAreaKm2         float64              `json:"park_area_km2"`
	ConflictRisk        string               `json:"conflict_risk"`
	LargestSettlements  []SettlementDetail   `json:"largest_settlements"`
	RegionalBreakdown   []RegionSettlement   `json:"regional_breakdown,omitempty"`
}

// SettlementDetail describes a single settlement
type SettlementDetail struct {
	Name              string  `json:"name"`
	AreaM2            float64 `json:"area_m2"`
	Lat               float64 `json:"lat"`
	Lon               float64 `json:"lon"`
	Direction         string  `json:"direction"`
	NearestBoundaryKm float64 `json:"nearest_boundary_km,omitempty"`
}

// RegionSettlement groups settlements by geographic region within the park
type RegionSettlement struct {
	Region         string `json:"region"`
	SettlementCount int   `json:"settlement_count"`
	Population     int64  `json:"population"`
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
	
	// Parse time filter parameters - support multi-year ranges
	yearStr := r.URL.Query().Get("year")
	fromStr := r.URL.Query().Get("from")
	toStr := r.URL.Query().Get("to")
	
	var fromYear, toYear int
	now := time.Now()
	
	if yearStr != "" {
		if y, err := strconv.Atoi(yearStr); err == nil {
			fromYear = y
			toYear = y
		}
	} else {
		// Default: all available years
		fromYear = 2000
		toYear = now.Year()
		if fromStr != "" {
			if t, err := time.Parse("2006-01-02", fromStr); err == nil {
				fromYear = t.Year()
			}
		}
		if toStr != "" {
			if t, err := time.Parse("2006-01-02", toStr); err == nil {
				toYear = t.Year()
			}
		}
	}
	
	// Get aggregated fire data across year range
	var totalGroups, stoppedInside, transited int
	var avgDaysBurning float64
	var yearCount int
	
	err := s.DB.QueryRow(`
		SELECT 
			COUNT(DISTINCT year) as year_count,
			SUM(total_groups) as total_groups,
			SUM(groups_stopped_inside) as stopped,
			SUM(groups_transited) as transited,
			AVG(avg_days_burning) as avg_days
		FROM park_group_infractions 
		WHERE park_id = ? AND year >= ? AND year <= ? AND total_groups > 0
	`, internalID, fromYear, toYear).Scan(&yearCount, &totalGroups, &stoppedInside, &transited, &avgDaysBurning)
	
	// Use toYear as the "display year" for single-year or latest in range
	displayYear := toYear
	if fromYear == toYear {
		displayYear = fromYear
	}
	
	narrative := FireNarrative{
		ParkID:   internalID,
		ParkName: parkName,
		Year:     displayYear,
	}
	
	if err == sql.ErrNoRows || totalGroups == 0 {
		periodDesc := fmt.Sprintf("%d", fromYear)
		if fromYear != toYear {
			periodDesc = fmt.Sprintf("%d-%d", fromYear, toYear)
		}
		narrative.Summary = fmt.Sprintf("No significant fire group incursions recorded for %s in %s.", parkName, periodDesc)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(narrative)
		return
	}
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	
	// Calculate response rate
	if totalGroups > 0 {
		narrative.ResponseRate = float64(stoppedInside) / float64(totalGroups) * 100
	}
	
	// Get total fire count for the year range
	var totalFires int
	s.DB.QueryRow(`
		SELECT COUNT(*) FROM fire_detections 
		WHERE protected_area_id = ? 
		  AND CAST(strftime('%Y', acq_date) AS INTEGER) >= ? 
		  AND CAST(strftime('%Y', acq_date) AS INTEGER) <= ?
	`, internalID, fromYear, toYear).Scan(&totalFires)
	narrative.TotalFires = totalFires
	
	// Get peak month across the range
	var peakMonth string
	var peakCount int
	s.DB.QueryRow(`
		SELECT strftime('%m', acq_date) as month, COUNT(*) as cnt
		FROM fire_detections 
		WHERE protected_area_id = ? 
		  AND CAST(strftime('%Y', acq_date) AS INTEGER) >= ?
		  AND CAST(strftime('%Y', acq_date) AS INTEGER) <= ?
		GROUP BY month ORDER BY cnt DESC LIMIT 1
	`, internalID, fromYear, toYear).Scan(&peakMonth, &peakCount)
	monthNames := map[string]string{
		"01": "January", "02": "February", "03": "March", "04": "April",
		"05": "May", "06": "June", "07": "July", "08": "August",
		"09": "September", "10": "October", "11": "November", "12": "December",
	}
	narrative.PeakMonth = monthNames[peakMonth]
	
	// Build enhanced summary
	var summaryParts []string
	periodDesc := fmt.Sprintf("%d", fromYear)
	if fromYear != toYear {
		periodDesc = fmt.Sprintf("%d-%d", fromYear, toYear)
	}
	if yearCount > 1 {
		summaryParts = append(summaryParts, fmt.Sprintf("From %s, %s experienced %d fire detections across %d fire groups over %d years.",
			periodDesc, parkName, totalFires, totalGroups, yearCount))
	} else {
		summaryParts = append(summaryParts, fmt.Sprintf("In %s, %s experienced %d fire detections across %d distinct fire groups.",
			periodDesc, parkName, totalFires, totalGroups))
	}
	if stoppedInside > 0 {
		summaryParts = append(summaryParts, fmt.Sprintf("%d group(s) (%.0f%%) were stopped inside the park, suggesting effective ranger intervention.", 
			stoppedInside, narrative.ResponseRate))
	}
	if transited > 0 {
		summaryParts = append(summaryParts, fmt.Sprintf("%d group(s) transited through without being stopped.", transited))
	}
	if narrative.PeakMonth != "" {
		summaryParts = append(summaryParts, fmt.Sprintf("Peak fire activity occurred in %s.", narrative.PeakMonth))
	}
	if avgDaysBurning > 0 {
		summaryParts = append(summaryParts, fmt.Sprintf("Fire groups burned inside the park for an average of %.1f days.", avgDaysBurning))
	}
	narrative.Summary = strings.Join(summaryParts, " ")
	
	// Query trajectories from the most recent year in range for detailed stories
	var trajJSON sql.NullString
	s.DB.QueryRow(`
		SELECT trajectories_json FROM park_group_infractions 
		WHERE park_id = ? AND year >= ? AND year <= ? AND trajectories_json IS NOT NULL
		ORDER BY year DESC LIMIT 1
	`, internalID, fromYear, toYear).Scan(&trajJSON)
	
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
	
	// Generate hotspot analysis from fire_detections (works without trajectory JSON)
	narrative.Hotspots = s.analyzeFireHotspots(internalID, displayYear, totalFires)
	
	// Generate multi-year trend analysis
	narrative.Trend = s.analyzeFireTrend(internalID, displayYear)
	
	// If no trajectory-based narratives, generate hotspot-based narratives
	if len(narrative.Narratives) == 0 && len(narrative.Hotspots) > 0 {
		for i, hs := range narrative.Hotspots {
			if i >= 5 { // Limit to top 5 hotspots
				break
			}
			story := FireGroupStory{
				GroupNum:    i + 1,
				FiresInside: hs.FireCount,
				Outcome:     "HOTSPOT",
				Narrative:   hs.Description,
				NearbyPlaces: hs.NearbyPlaces,
			}
			narrative.Narratives = append(narrative.Narratives, story)
		}
	}
	
	// Get key places in the park for context
	keyPlaces, _ := s.findNearestPlaces(internalID, 0, 0, 0, nil)
	if len(keyPlaces) == 0 {
		rows, err := s.DB.Query(`
			SELECT id, park_id, place_type, name, lat, lon
			FROM osm_places WHERE park_id = ? LIMIT 20
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
	
	// Parse time filter parameters
	yearStr := r.URL.Query().Get("year")
	fromStr := r.URL.Query().Get("from")
	toStr := r.URL.Query().Get("to")
	
	var fromYear, toYear int
	if yearStr != "" {
		if y, err := strconv.Atoi(yearStr); err == nil {
			fromYear = y
			toYear = y
		}
	} else {
		// Default to all years if no filter
		fromYear = 1900
		toYear = 2100
		if fromStr != "" {
			if t, err := time.Parse("2006-01-02", fromStr); err == nil {
				fromYear = t.Year()
			}
		}
		if toStr != "" {
			if t, err := time.Parse("2006-01-02", toStr); err == nil {
				toYear = t.Year()
			}
		}
	}
	
	narrative := DeforestationNarrative{
		ParkID:   internalID,
		ParkName: parkName,
	}
	
	// Query deforestation events with time filter
	rows, err := s.DB.Query(`
		SELECT year, area_km2, pattern_type, lat, lon, description
		FROM deforestation_events
		WHERE park_id = ? AND year >= ? AND year <= ?
		ORDER BY year ASC
	`, internalID, fromYear, toYear)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	
	var totalLoss float64
	var worstYear int
	var worstLoss float64
	var yearlyAreas []struct {
		year int
		area float64
	}
	
	for rows.Next() {
		var year int
		var area float64
		var patternType sql.NullString
		var lat, lon float64
		var description sql.NullString
		
		if err := rows.Scan(&year, &area, &patternType, &lat, &lon, &description); err != nil {
			continue
		}
		
		yearlyAreas = append(yearlyAreas, struct {
			year int
			area float64
		}{year, area})
		
		totalLoss += area
		if area > worstLoss {
			worstLoss = area
			worstYear = year
		}
		
		// Determine actual pattern type from cluster data for this year
		actualPattern := s.determinePatternType(internalID, year, patternType.String)
		
		story := DeforestationYearStory{
			Year:        year,
			AreaKm2:     area,
			PatternType: actualPattern,
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
		
		// Build narrative with varied pattern description
		locationDesc := s.describeLocation(internalID, lat, lon)
		patternDesc := describePatternVaried(actualPattern, area, year)
		
		story.Narrative = fmt.Sprintf("In %d, %.2f km² of forest was lost %s. %s",
			year, area, locationDesc, patternDesc)
		
		narrative.YearlyStory = append(narrative.YearlyStory, story)
	}
	
	// Reverse to show most recent first
	for i, j := 0, len(narrative.YearlyStory)-1; i < j; i, j = i+1, j-1 {
		narrative.YearlyStory[i], narrative.YearlyStory[j] = narrative.YearlyStory[j], narrative.YearlyStory[i]
	}
	
	narrative.TotalLoss = totalLoss
	narrative.WorstYear = worstYear
	
	// Calculate 5-year rolling average trend
	narrative.TrendDirection, narrative.TrendPercentChange, 
		narrative.FiveYearAvgEarly, narrative.FiveYearAvgRecent = calculateTrend(yearlyAreas)
	
	// Fetch worst hotspots from clusters table
	narrative.Hotspots = s.fetchHotspots(internalID, 5)
	
	// Build summary with trend information
	if totalLoss == 0 {
		narrative.Summary = fmt.Sprintf("No significant deforestation events recorded for %s.", parkName)
	} else {
		trendDesc := describeTrend(narrative.TrendDirection, narrative.TrendPercentChange)
		narrative.Summary = fmt.Sprintf("%s has experienced %.2f km² of forest loss across %d recorded years. The worst year was %d with %.2f km² lost. %s",
			parkName, totalLoss, len(narrative.YearlyStory), worstYear, worstLoss, trendDesc)
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(narrative)
}

// HandleAPISettlementNarrative returns comprehensive narrative about settlements and human-wildlife interface
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
	var parkAreaKm2 float64
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.WDPAID == parkID || area.ID == parkID {
				internalID = area.ID
				parkName = area.Name
				parkAreaKm2 = area.AreaKm2
				break
			}
		}
	}
	
	narrative := SettlementNarrative{
		ParkID:      internalID,
		ParkName:    parkName,
		ParkAreaKm2: parkAreaKm2,
	}
	
	// Get settlement statistics from park_settlements table
	var settlementCount int
	var totalPopulation sql.NullFloat64
	err := s.DB.QueryRow(`
		SELECT COUNT(*) as count, COALESCE(SUM(population_est), 0) as total_pop
		FROM park_settlements
		WHERE park_id = ?
	`, internalID).Scan(&settlementCount, &totalPopulation)
	
	if err != nil {
		narrative.Status = "error"
		narrative.Summary = "Error retrieving settlement data."
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(narrative)
		return
	}
	
	narrative.SettlementCount = settlementCount
	narrative.TotalPopulation = int64(totalPopulation.Float64)
	
	// Calculate population density
	if parkAreaKm2 > 0 {
		narrative.PopulationDensity = totalPopulation.Float64 / parkAreaKm2
	}
	
	// Assess human-wildlife conflict risk
	narrative.ConflictRisk = assessConflictRisk(settlementCount, narrative.PopulationDensity)
	
	// Handle zero settlements case (pristine areas)
	if settlementCount == 0 {
		narrative.Status = "complete"
		narrative.ConflictRisk = "minimal"
		narrative.Summary = generatePristineNarrative(parkName, parkAreaKm2)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(narrative)
		return
	}
	
	// Get largest settlements
	largestRows, err := s.DB.Query(`
		SELECT 
			COALESCE(nearest_place, 'Unnamed settlement') as name,
			COALESCE(area_m2, 0) as area_m2,
			lat, lon,
			COALESCE(direction, '') as direction,
			COALESCE(distance_km, 0) as distance_km
		FROM park_settlements
		WHERE park_id = ?
		ORDER BY area_m2 DESC
	`, internalID)
	
	if err == nil {
		defer largestRows.Close()
		for largestRows.Next() {
			var sd SettlementDetail
			var distKm float64
			if err := largestRows.Scan(&sd.Name, &sd.AreaM2, &sd.Lat, &sd.Lon, &sd.Direction, &distKm); err == nil {
				sd.NearestBoundaryKm = distKm
				narrative.LargestSettlements = append(narrative.LargestSettlements, sd)
			}
		}
	}
	
	// Get regional breakdown by quadrant
	regionRows, err := s.DB.Query(`
		WITH park_center AS (
			SELECT AVG(lat) as center_lat, AVG(lon) as center_lon
			FROM park_settlements WHERE park_id = ?
		)
		SELECT 
			CASE 
				WHEN s.lat >= pc.center_lat AND s.lon >= pc.center_lon THEN 'Northeast'
				WHEN s.lat >= pc.center_lat AND s.lon < pc.center_lon THEN 'Northwest'
				WHEN s.lat < pc.center_lat AND s.lon >= pc.center_lon THEN 'Southeast'
				ELSE 'Southwest'
			END as region,
			COUNT(*) as count,
			COALESCE(SUM(population_est), 0) as population
		FROM park_settlements s, park_center pc
		WHERE s.park_id = ?
		GROUP BY region
		ORDER BY population DESC
	`, internalID, internalID)
	
	if err == nil {
		defer regionRows.Close()
		for regionRows.Next() {
			var rs RegionSettlement
			if err := regionRows.Scan(&rs.Region, &rs.SettlementCount, &rs.Population); err == nil {
				narrative.RegionalBreakdown = append(narrative.RegionalBreakdown, rs)
			}
		}
	}
	
	// Generate comprehensive narrative
	narrative.Status = "complete"
	narrative.Summary = generateSettlementNarrative(parkName, settlementCount, narrative.TotalPopulation, 
		narrative.PopulationDensity, narrative.ConflictRisk, narrative.LargestSettlements, narrative.RegionalBreakdown)
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(narrative)
}

// assessConflictRisk determines human-wildlife conflict risk level
func assessConflictRisk(settlementCount int, density float64) string {
	if settlementCount == 0 {
		return "minimal"
	}
	if density > 50 {
		return "critical"
	}
	if density > 20 || settlementCount > 50 {
		return "high"
	}
	if density > 5 || settlementCount > 20 {
		return "moderate"
	}
	return "low"
}

// generatePristineNarrative creates narrative for parks with no settlements
func generatePristineNarrative(parkName string, areaKm2 float64) string {
	var narrative strings.Builder
	narrative.WriteString(fmt.Sprintf("%s shows no detectable human settlements within park boundaries. ", parkName))
	
	if areaKm2 > 0 {
		narrative.WriteString(fmt.Sprintf("This %.0f km² protected area represents a pristine wilderness corridor with minimal direct human-wildlife interface. ", areaKm2))
	}
	
	narrative.WriteString("Conservation priority: Maintain buffer zones and monitor boundary areas for encroachment. ")
	narrative.WriteString("This intact habitat status is rare in the region and critical for wildlife movement corridors.")
	
	return narrative.String()
}

// formatArea formats area in appropriate units (m², ha, km²)
func formatArea(m2 float64) string {
	if m2 >= 1000000 {
		return fmt.Sprintf("%.1f km²", m2/1000000)
	}
	if m2 >= 10000 {
		return fmt.Sprintf("%.1f ha", m2/10000)
	}
	return fmt.Sprintf("%.0f m²", m2)
}

// generateSettlementNarrative creates a concise narrative for populated parks
func generateSettlementNarrative(parkName string, count int, totalPop int64, density float64, risk string, 
	largest []SettlementDetail, regions []RegionSettlement) string {
	
	// Calculate total built-up area
	var totalArea float64
	for _, s := range largest {
		totalArea += s.AreaM2
	}
	
	// Simple summary: count and total built-up area
	return fmt.Sprintf("%s contains %d settlements with %s total built-up area.", 
		parkName, count, formatArea(totalArea))
}

// formatPopulation formats population numbers with K/M suffixes
func formatPopulation(pop int64) string {
	if pop >= 1000000 {
		return fmt.Sprintf("%.1fM", float64(pop)/1000000)
	}
	if pop >= 1000 {
		return fmt.Sprintf("%.0fK", float64(pop)/1000)
	}
	return fmt.Sprintf("%d", pop)
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

// describePatternVaried provides varied pattern descriptions based on context
func describePatternVaried(pattern string, areaKm2 float64, year int) string {
	// Different phrasings for variety
	scatteredPhrases := []string{
		"The scattered pattern is consistent with smallholder agricultural expansion.",
		"Dispersed clearing suggests multiple small-scale farming operations.",
		"The diffuse pattern indicates gradual encroachment from various points.",
		"Multiple small clearings are typical of subsistence agriculture.",
	}
	
	clusterPhrases := []string{
		"The clustered pattern may indicate mining activity or localized clearing.",
		"Concentrated loss suggests organized clearing for commercial purposes.",
		"The tight cluster pattern is consistent with settlement expansion.",
		"Focused deforestation indicates a single major clearing event.",
	}
	
	stripPhrases := []string{
		"The linear pattern suggests road construction or logging track expansion.",
		"Linear clearing indicates infrastructure development or logging access.",
		"The strip pattern is consistent with road-building or utility corridors.",
	}
	
	edgePhrases := []string{
		"Loss concentrated along park boundaries indicates agricultural encroachment from surrounding communities.",
		"Edge-focused clearing reflects pressure from adjacent farming areas.",
		"Boundary-adjacent loss suggests expansion of neighboring settlements.",
	}
	
	// Use year as seed for deterministic variety
	index := year % 4
	
	switch pattern {
	case "strip":
		return stripPhrases[index%len(stripPhrases)]
	case "cluster":
		return clusterPhrases[index%len(clusterPhrases)]
	case "scattered":
		return scatteredPhrases[index%len(scatteredPhrases)]
	case "edge":
		return edgePhrases[index%len(edgePhrases)]
	default:
		// Provide context-based default
		if areaKm2 > 5 {
			return "The significant loss area warrants investigation into underlying causes."
		}
		return "Further analysis needed to determine the cause of forest loss."
	}
}

// calculateTrend computes the 5-year rolling average trend
func calculateTrend(yearlyAreas []struct {
	year int
	area float64
}) (direction string, percentChange, earlyAvg, recentAvg float64) {
	if len(yearlyAreas) < 5 {
		return "insufficient_data", 0, 0, 0
	}
	
	// Calculate early 5-year average (first 5 years)
	earlyYears := 5
	if len(yearlyAreas) < 10 {
		earlyYears = len(yearlyAreas) / 2
	}
	if earlyYears < 2 {
		earlyYears = 2
	}
	
	var earlySum float64
	for i := 0; i < earlyYears; i++ {
		earlySum += yearlyAreas[i].area
	}
	earlyAvg = earlySum / float64(earlyYears)
	
	// Calculate recent 5-year average (last 5 years)
	recentYears := 5
	if len(yearlyAreas) < 10 {
		recentYears = len(yearlyAreas) - earlyYears
	}
	if recentYears < 2 {
		recentYears = 2
	}
	
	var recentSum float64
	for i := len(yearlyAreas) - recentYears; i < len(yearlyAreas); i++ {
		recentSum += yearlyAreas[i].area
	}
	recentAvg = recentSum / float64(recentYears)
	
	// Calculate percent change
	if earlyAvg > 0 {
		percentChange = ((recentAvg - earlyAvg) / earlyAvg) * 100
	}
	
	// Determine trend direction (10% threshold for "stable")
	if percentChange > 10 {
		direction = "worsening"
	} else if percentChange < -10 {
		direction = "improving"
	} else {
		direction = "stable"
	}
	
	return direction, percentChange, earlyAvg, recentAvg
}

// describeTrend generates human-readable trend description
func describeTrend(direction string, percentChange float64) string {
	switch direction {
	case "worsening":
		return fmt.Sprintf("⚠️ TREND ALERT: Deforestation has increased by %.0f%% comparing recent years to earlier periods.", percentChange)
	case "improving":
		return fmt.Sprintf("✅ POSITIVE TREND: Deforestation has decreased by %.0f%% comparing recent years to earlier periods.", -percentChange)
	case "stable":
		return "Deforestation rates have remained relatively stable over the monitoring period."
	default:
		return "Insufficient data to determine long-term trend."
	}
}

// fetchHotspots retrieves the worst deforestation clusters for a park
func (s *Server) fetchHotspots(parkID string, limit int) []DeforestationHotspot {
	var hotspots []DeforestationHotspot
	
	rows, err := s.DB.Query(`
		SELECT year, cluster_id, area_km2, lat, lon, COALESCE(pattern_type, 'unknown'), COALESCE(description, '')
		FROM deforestation_clusters
		WHERE park_id = ?
		ORDER BY area_km2 DESC
		LIMIT ?
	`, parkID, limit)
	if err != nil {
		return hotspots
	}
	defer rows.Close()
	
	for rows.Next() {
		var h DeforestationHotspot
		if err := rows.Scan(&h.Year, &h.ClusterID, &h.AreaKm2, &h.Lat, &h.Lon, &h.PatternType, &h.Description); err != nil {
			continue
		}
		
		// Generate description if empty
		if h.Description == "" {
			locationDesc := s.describeLocation(parkID, h.Lat, h.Lon)
			h.Description = fmt.Sprintf("%.2f km² lost in %d %s", h.AreaKm2, h.Year, locationDesc)
		}
		
		hotspots = append(hotspots, h)
	}
	
	return hotspots
}

// determinePatternType analyzes cluster data to determine actual pattern type
func (s *Server) determinePatternType(parkID string, year int, defaultPattern string) string {
	// Query clusters for this park/year to analyze distribution
	var clusterCount int
	var totalArea float64
	var latMin, latMax, lonMin, lonMax sql.NullFloat64
	
	err := s.DB.QueryRow(`
		SELECT COUNT(*), COALESCE(SUM(area_km2), 0),
		       MIN(lat), MAX(lat), MIN(lon), MAX(lon)
		FROM deforestation_clusters
		WHERE park_id = ? AND year = ?
	`, parkID, year).Scan(&clusterCount, &totalArea, &latMin, &latMax, &lonMin, &lonMax)
	
	if err != nil || clusterCount == 0 {
		return defaultPattern
	}
	
	// Calculate geographic spread
	latSpread := 0.0
	lonSpread := 0.0
	if latMin.Valid && latMax.Valid {
		latSpread = latMax.Float64 - latMin.Float64
	}
	if lonMin.Valid && lonMax.Valid {
		lonSpread = lonMax.Float64 - lonMin.Float64
	}
	
	// Determine pattern based on cluster analysis
	if clusterCount == 1 {
		return "cluster" // Single concentrated area
	}
	
	// Check for linear (strip) pattern - one dimension much larger than other
	aspectRatio := 0.0
	if latSpread > 0 && lonSpread > 0 {
		if latSpread > lonSpread {
			aspectRatio = latSpread / lonSpread
		} else {
			aspectRatio = lonSpread / latSpread
		}
	}
	
	if aspectRatio > 3.0 {
		return "strip" // Linear pattern
	}
	
	// Check for cluster vs scattered based on density
	spreadArea := latSpread * lonSpread * 111 * 111 // Rough km² conversion
	if spreadArea > 0 {
		density := float64(clusterCount) / spreadArea
		if density > 0.5 { // High density of clusters
			return "cluster"
		}
	}
	
	// If many small clusters spread out
	if clusterCount > 5 {
		return "scattered"
	}
	
	// Check if clusters are from database with explicit pattern
	var clusterPattern sql.NullString
	s.DB.QueryRow(`
		SELECT pattern_type FROM deforestation_clusters
		WHERE park_id = ? AND year = ? AND pattern_type IS NOT NULL
		GROUP BY pattern_type
		ORDER BY COUNT(*) DESC
		LIMIT 1
	`, parkID, year).Scan(&clusterPattern)
	
	if clusterPattern.Valid && clusterPattern.String != "" {
		return clusterPattern.String
	}
	
	return defaultPattern
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

// analyzeFireHotspots identifies geographic concentrations of fire activity
func (s *Server) analyzeFireHotspots(parkID string, year int, totalFires int) []FireHotspot {
	var hotspots []FireHotspot
	
	// Query fire clusters by 0.1 degree grid cells
	rows, err := s.DB.Query(`
		SELECT 
			ROUND(latitude, 1) as lat_bucket,
			ROUND(longitude, 1) as lon_bucket,
			AVG(latitude) as avg_lat,
			AVG(longitude) as avg_lon,
			COUNT(*) as fire_count
		FROM fire_detections 
		WHERE protected_area_id = ? AND strftime('%Y', acq_date) = ?
		GROUP BY lat_bucket, lon_bucket
		HAVING fire_count >= 10
		ORDER BY fire_count DESC
		LIMIT 10
	`, parkID, fmt.Sprintf("%d", year))
	if err != nil {
		return hotspots
	}
	defer rows.Close()
	
	for rows.Next() {
		var latBucket, lonBucket, avgLat, avgLon float64
		var fireCount int
		if err := rows.Scan(&latBucket, &lonBucket, &avgLat, &avgLon, &fireCount); err != nil {
			continue
		}
		
		hs := FireHotspot{
			Lat:       avgLat,
			Lon:       avgLon,
			FireCount: fireCount,
		}
		if totalFires > 0 {
			hs.Percentage = float64(fireCount) / float64(totalFires) * 100
		}
		
		// Find nearby places for context
		settlements, _ := s.findNearestPlaces(parkID, avgLat, avgLon, 2, []string{"village", "hamlet", "town", "city"})
		rivers, _ := s.findNearestPlaces(parkID, avgLat, avgLon, 1, []string{"river", "stream"})
		
		var nearbyNames []string
		for _, p := range settlements {
			if p.Distance < 30 {
				nearbyNames = append(nearbyNames, fmt.Sprintf("%s (%.0fkm)", p.Name, p.Distance))
			}
		}
		for _, p := range rivers {
			if p.Distance < 20 {
				nearbyNames = append(nearbyNames, fmt.Sprintf("%s River (%.0fkm)", p.Name, p.Distance))
			}
		}
		hs.NearbyPlaces = nearbyNames
		
		// Build description
		locationDesc := s.describeLocation(parkID, avgLat, avgLon)
		hs.Description = fmt.Sprintf("Fire hotspot %s with %d detections (%.1f%% of park total). ",
			locationDesc, fireCount, hs.Percentage)
		if len(nearbyNames) > 0 {
			hs.Description += fmt.Sprintf("Nearby: %s.", strings.Join(nearbyNames, ", "))
		}
		
		hotspots = append(hotspots, hs)
	}
	
	return hotspots
}

// analyzeFireTrend provides multi-year trend analysis
func (s *Server) analyzeFireTrend(parkID string, currentYear int) *FireTrendAnalysis {
	trend := &FireTrendAnalysis{}
	
	// Get all years of data
	rows, err := s.DB.Query(`
		SELECT 
			pgi.year,
			pgi.total_groups,
			pgi.groups_stopped_inside,
			pgi.groups_transited,
			pgi.avg_days_burning,
			COALESCE(fd.fire_count, 0) as total_fires
		FROM park_group_infractions pgi
		LEFT JOIN (
			SELECT 
				protected_area_id,
				CAST(strftime('%Y', acq_date) AS INTEGER) as year,
				COUNT(*) as fire_count
			FROM fire_detections
			GROUP BY protected_area_id, strftime('%Y', acq_date)
		) fd ON pgi.park_id = fd.protected_area_id AND pgi.year = fd.year
		WHERE pgi.park_id = ?
		ORDER BY pgi.year
	`, parkID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	
	var totalResponseRate float64
	var yearCount int
	var worstGroups int
	var bestRate float64 = -1
	
	for rows.Next() {
		var ys FireYearSummary
		if err := rows.Scan(&ys.Year, &ys.TotalGroups, &ys.StoppedInside, &ys.Transited, &ys.AvgDaysBurning, &ys.TotalFires); err != nil {
			continue
		}
		if ys.TotalGroups > 0 {
			ys.ResponseRate = float64(ys.StoppedInside) / float64(ys.TotalGroups) * 100
			totalResponseRate += ys.ResponseRate
			yearCount++
			
			if ys.TotalGroups > worstGroups {
				worstGroups = ys.TotalGroups
				trend.WorstYear = ys.Year
				trend.WorstYearGroups = ys.TotalGroups
			}
			if bestRate < 0 || ys.ResponseRate > bestRate {
				bestRate = ys.ResponseRate
				trend.BestYear = ys.Year
				trend.BestYearRate = ys.ResponseRate
			}
		}
		trend.Years = append(trend.Years, ys)
	}
	
	if yearCount > 0 {
		trend.AvgResponseRate = totalResponseRate / float64(yearCount)
	}
	
	// Determine trend direction
	if len(trend.Years) >= 3 {
		recentAvg := 0.0
		earlyAvg := 0.0
		mid := len(trend.Years) / 2
		for i, y := range trend.Years {
			if i < mid {
				earlyAvg += float64(y.TotalGroups)
			} else {
				recentAvg += float64(y.TotalGroups)
			}
		}
		earlyAvg /= float64(mid)
		recentAvg /= float64(len(trend.Years) - mid)
		
		if recentAvg > earlyAvg*1.2 {
			trend.TrendDirection = "increasing"
		} else if recentAvg < earlyAvg*0.8 {
			trend.TrendDirection = "decreasing"
		} else {
			trend.TrendDirection = "stable"
		}
	}
	
	// Build trend narrative
	if len(trend.Years) > 1 {
		var narr strings.Builder
		narr.WriteString(fmt.Sprintf("Analysis of %d years of fire data (%d-%d). ",
			len(trend.Years), trend.Years[0].Year, trend.Years[len(trend.Years)-1].Year))
		
		switch trend.TrendDirection {
		case "increasing":
			narr.WriteString("⚠️ Fire pressure is INCREASING - enhanced monitoring recommended. ")
		case "decreasing":
			narr.WriteString("✓ Fire pressure is DECREASING - conservation efforts may be working. ")
		case "stable":
			narr.WriteString("Fire pressure remains relatively stable over the analysis period. ")
		}
		
		narr.WriteString(fmt.Sprintf("Average response rate: %.0f%%. ", trend.AvgResponseRate))
		if trend.WorstYear > 0 {
			narr.WriteString(fmt.Sprintf("Worst year: %d with %d fire groups. ", trend.WorstYear, trend.WorstYearGroups))
		}
		if trend.BestYear > 0 {
			narr.WriteString(fmt.Sprintf("Best response rate: %.0f%% in %d.", trend.BestYearRate, trend.BestYear))
		}
		trend.Narrative = narr.String()
	}
	
	return trend
}
