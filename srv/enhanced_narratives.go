package srv

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"
)

// NarrativeContext holds all contextual data for generating rich narratives
type NarrativeContext struct {
	ParkID      string
	ParkName    string
	Climate     *ParkClimateData
	Rivers      []OSMPlace
	Places      []OSMPlace
	Waterbodies []WaterbodyFeature
}

// ParkClimateData for seasonal context
type ParkClimateData struct {
	TempAnnual      float64
	TempMax         float64
	TempMin         float64
	PrecipAnnual    int
	PrecipWettest   int
	PrecipDriest    int
	ClimateZone     string
	RainySeason     string
	DrySeason       string
}

// WaterbodyFeature for water proximity context
type WaterbodyFeature struct {
	ID    string
	Name  string
	Type  string
	Lat   float64
	Lon   float64
}

// getNarrativeContext loads all contextual data for a park
func (s *Server) getNarrativeContext(parkID, parkName string) *NarrativeContext {
	ctx := &NarrativeContext{
		ParkID:   parkID,
		ParkName: parkName,
	}
	
	// Load climate data
	var climate ParkClimateData
	err := s.DB.QueryRow(`
		SELECT temp_annual_c, temp_max_c, temp_min_c, 
		       precip_annual_mm, precip_wettest_mm, precip_driest_mm,
		       climate_zone, rainy_season, dry_season
		FROM park_climate WHERE park_id = ?
	`, parkID).Scan(
		&climate.TempAnnual, &climate.TempMax, &climate.TempMin,
		&climate.PrecipAnnual, &climate.PrecipWettest, &climate.PrecipDriest,
		&climate.ClimateZone, &climate.RainySeason, &climate.DrySeason,
	)
	if err == nil {
		ctx.Climate = &climate
	}
	
	// Load rivers
	rows, _ := s.DB.Query(`
		SELECT name, lat, lon FROM osm_places 
		WHERE park_id = ? AND place_type IN ('river', 'stream')
	`, parkID)
	if rows != nil {
		defer rows.Close()
		for rows.Next() {
			var p OSMPlace
			rows.Scan(&p.Name, &p.Lat, &p.Lon)
			ctx.Rivers = append(ctx.Rivers, p)
		}
	}
	
	// Load places
	rows2, _ := s.DB.Query(`
		SELECT name, place_type, lat, lon FROM osm_places 
		WHERE park_id = ? AND place_type IN ('town', 'village', 'hamlet', 'city')
		LIMIT 100
	`, parkID)
	if rows2 != nil {
		defer rows2.Close()
		for rows2.Next() {
			var p OSMPlace
			rows2.Scan(&p.Name, &p.PlaceType, &p.Lat, &p.Lon)
			ctx.Places = append(ctx.Places, p)
		}
	}
	
	// Load waterbodies
	rows3, _ := s.DB.Query(`
		SELECT waterbody_id, name, waterbody_type, lat, lon FROM park_waterbodies 
		WHERE park_id = ? AND name != ''
		LIMIT 50
	`, parkID)
	if rows3 != nil {
		defer rows3.Close()
		for rows3.Next() {
			var w WaterbodyFeature
			rows3.Scan(&w.ID, &w.Name, &w.Type, &w.Lat, &w.Lon)
			ctx.Waterbodies = append(ctx.Waterbodies, w)
		}
	}
	
	return ctx
}

// getSeasonForDate returns dry/wet season based on date and climate data
func (ctx *NarrativeContext) getSeasonForDate(dateStr string) string {
	if ctx.Climate == nil || ctx.Climate.DrySeason == "" {
		return ""
	}
	
	t, err := time.Parse("2006-01-02", dateStr)
	if err != nil {
		return ""
	}
	month := int(t.Month())
	
	drySeason := ctx.Climate.DrySeason
	if drySeason == "None" || drySeason == "" {
		return ""
	}
	if drySeason == "Year-round" {
		return "dry"
	}
	
	// Parse month range (e.g., "Dec-Feb", "Jun-Sep")
	dryMonths := parseMonthRange(drySeason)
	if dryMonths[month] {
		return "dry season"
	}
	return "wet season"
}

// parseMonthRange parses "Jan-Mar" or "Nov-Feb" style strings into month map
func parseMonthRange(rangeStr string) map[int]bool {
	monthMap := map[string]int{
		"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
		"Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
	}
	
	result := make(map[int]bool)
	parts := strings.Split(rangeStr, "-")
	if len(parts) != 2 {
		return result
	}
	
	start, ok1 := monthMap[strings.TrimSpace(parts[0])]
	end, ok2 := monthMap[strings.TrimSpace(parts[1])]
	if !ok1 || !ok2 {
		return result
	}
	
	// Handle wrap-around (e.g., Nov-Feb)
	if start <= end {
		for m := start; m <= end; m++ {
			result[m] = true
		}
	} else {
		// Wraps around year
		for m := start; m <= 12; m++ {
			result[m] = true
		}
		for m := 1; m <= end; m++ {
			result[m] = true
		}
	}
	return result
}

// findNearestRiver finds the nearest river/stream to a point
func (ctx *NarrativeContext) findNearestRiver(lat, lon float64) (string, float64) {
	var nearest string
	minDist := math.MaxFloat64
	
	for _, r := range ctx.Rivers {
		dist := haversineDistanceKm(lat, lon, r.Lat, r.Lon)
		if dist < minDist {
			minDist = dist
			nearest = r.Name
		}
	}
	
	return nearest, minDist
}

// findNearestPlace finds the nearest town/village to a point
func (ctx *NarrativeContext) findNearestPlace(lat, lon float64) (string, string, float64) {
	var nearestName, nearestType string
	minDist := math.MaxFloat64
	
	for _, p := range ctx.Places {
		dist := haversineDistanceKm(lat, lon, p.Lat, p.Lon)
		if dist < minDist {
			minDist = dist
			nearestName = p.Name
			nearestType = p.PlaceType
		}
	}
	
	return nearestName, nearestType, minDist
}

// findNearestWaterbody finds the nearest named waterbody
func (ctx *NarrativeContext) findNearestWaterbody(lat, lon float64) (string, float64) {
	var nearest string
	minDist := math.MaxFloat64
	
	for _, w := range ctx.Waterbodies {
		dist := haversineDistanceKm(lat, lon, w.Lat, w.Lon)
		if dist < minDist {
			minDist = dist
			nearest = w.Name
		}
	}
	
	return nearest, minDist
}

// describeLocationWithContext returns a location description using all context
func (ctx *NarrativeContext) describeLocationWithContext(lat, lon float64) string {
	var parts []string
	
	// Find nearest place
	placeName, placeType, placeDist := ctx.findNearestPlace(lat, lon)
	if placeName != "" && placeDist < 100 {
		dir := getCardinalDirection(lat, lon, ctx.Places[0].Lat, ctx.Places[0].Lon)
		// Find the actual place for direction
		for _, p := range ctx.Places {
			if p.Name == placeName {
				dir = getCardinalDirection(p.Lat, p.Lon, lat, lon)
				break
			}
		}
		parts = append(parts, fmt.Sprintf("%.0fkm %s of %s (%s)", placeDist, dir, placeName, placeType))
	}
	
	// Find nearest river
	riverName, riverDist := ctx.findNearestRiver(lat, lon)
	if riverName != "" && riverDist < 50 {
		if riverDist < 5 {
			parts = append(parts, fmt.Sprintf("near %s River", riverName))
		} else {
			parts = append(parts, fmt.Sprintf("%.0fkm from %s River", riverDist, riverName))
		}
	}
	
	// Find nearest waterbody if no river
	if riverName == "" {
		wbName, wbDist := ctx.findNearestWaterbody(lat, lon)
		if wbName != "" && wbDist < 30 {
			parts = append(parts, fmt.Sprintf("near %s", wbName))
		}
	}
	
	if len(parts) == 0 {
		return fmt.Sprintf("at (%.3f°, %.3f°)", lat, lon)
	}
	
	return strings.Join(parts, ", ")
}

// Use haversineDistanceKm from upload.go

// getCardinalDirection returns direction from point1 to point2
func getCardinalDirection(lat1, lon1, lat2, lon2 float64) string {
	dLon := lon2 - lon1
	dLat := lat2 - lat1
	angle := math.Atan2(dLon, dLat) * 180 / math.Pi
	if angle < 0 {
		angle += 360
	}
	
	directions := []string{"north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"}
	index := int((angle + 22.5) / 45) % 8
	return directions[index]
}

// EnhancedFireGroupNarrative builds a rich narrative for a fire group using all context
func (s *Server) EnhancedFireGroupNarrative(ctx *NarrativeContext, groupNum int, traj map[string]interface{}) string {
	origin := traj["origin"].(map[string]interface{})
	dest := traj["dest"].(map[string]interface{})
	entryDate := traj["entry_date"].(string)
	_ = traj["last_inside"].(string) // available for future use
	daysInside := int(traj["days_inside"].(float64))
	firesInside := int(traj["fires_inside"].(float64))
	outcome := traj["outcome"].(string)
	
	originLat := origin["lat"].(float64)
	originLon := origin["lon"].(float64)
	destLat := dest["lat"].(float64)
	destLon := dest["lon"].(float64)
	
	var narr strings.Builder
	
	// Seasonal context
	season := ctx.getSeasonForDate(entryDate)
	seasonStr := ""
	if season != "" {
		seasonStr = fmt.Sprintf(" (%s)", season)
	}
	
	// Origin with context
	originDesc := ctx.describeLocationWithContext(originLat, originLon)
	
	// Movement description
	distance := haversineDistanceKm(originLat, originLon, destLat, destLon)
	bearing := math.Atan2(destLon-originLon, destLat-originLat) * 180 / math.Pi
	if bearing < 0 {
		bearing += 360
	}
	direction := getCardinalDirection(originLat, originLon, destLat, destLon)
	
	// Build narrative
	narr.WriteString(fmt.Sprintf("Group %d: ", groupNum))
	
	if distance < 5 {
		narr.WriteString(fmt.Sprintf("Localized burning %s%s, starting %s. ", 
			originDesc, seasonStr, formatDateShort(entryDate)))
	} else {
		narr.WriteString(fmt.Sprintf("Originated %s%s on %s, moved %.0fkm %s. ", 
			originDesc, seasonStr, formatDateShort(entryDate), distance, direction))
	}
	
	// Duration and outcome
	if daysInside == 1 {
		narr.WriteString(fmt.Sprintf("Active 1 day (%d fires). ", firesInside))
	} else {
		narr.WriteString(fmt.Sprintf("Active %d days (%d fires). ", daysInside, firesInside))
	}
	
	// Outcome with context
	destDesc := ctx.describeLocationWithContext(destLat, destLon)
	switch outcome {
	case "STOPPED_INSIDE":
		narr.WriteString(fmt.Sprintf("Stopped %s.", destDesc))
	case "TRANSITED":
		narr.WriteString(fmt.Sprintf("Transited through, exited %s.", destDesc))
	default:
		narr.WriteString(fmt.Sprintf("Last detected %s.", destDesc))
	}
	
	return narr.String()
}

// formatDateShort returns a short date format
func formatDateShort(dateStr string) string {
	t, err := time.Parse("2006-01-02", dateStr)
	if err != nil {
		return dateStr
	}
	return t.Format("Jan 2")
}

// EnhancedDeforestationNarrative builds narrative for a deforestation event
func (s *Server) EnhancedDeforestationNarrative(ctx *NarrativeContext, year int, areaKm2 float64, lat, lon float64, patternType string) string {
	var narr strings.Builder
	
	// Location
	locDesc := ctx.describeLocationWithContext(lat, lon)
	
	// Size classification
	var sizeDesc string
	switch {
	case areaKm2 < 0.1:
		sizeDesc = "minor clearing"
	case areaKm2 < 0.5:
		sizeDesc = "moderate loss"
	case areaKm2 < 2:
		sizeDesc = "significant clearing"
	default:
		sizeDesc = "extensive loss"
	}
	
	// Pattern interpretation
	var patternDesc string
	switch patternType {
	case "scattered":
		patternDesc = "scattered clearings suggest smallholder activity"
	case "linear":
		patternDesc = "linear pattern indicates road/trail development"
	case "concentrated":
		patternDesc = "concentrated pattern suggests agricultural expansion"
	case "minor":
		patternDesc = "minor disturbance, possibly natural"
	default:
		patternDesc = "mixed clearing pattern"
	}
	
	narr.WriteString(fmt.Sprintf("%d: %.2f km² %s %s. %s.", 
		year, areaKm2, sizeDesc, locDesc, strings.Title(patternDesc)))
	
	return narr.String()
}

// EnhancedSettlementNarrative builds narrative for a settlement cluster
func (s *Server) EnhancedSettlementNarrative(ctx *NarrativeContext, settlementType string, population int, areaM2 float64, lat, lon float64) string {
	var narr strings.Builder
	
	// Location
	locDesc := ctx.describeLocationWithContext(lat, lon)
	
	// Settlement characterization
	var typeDesc string
	switch {
	case population > 1000:
		typeDesc = "major settlement"
	case population > 500:
		typeDesc = "village"
	case population > 100:
		typeDesc = "hamlet"
	case areaM2 > 10000:
		typeDesc = "compound"
	default:
		typeDesc = "small settlement"
	}
	
	// Area in readable units
	var areaStr string
	if areaM2 < 1000 {
		areaStr = fmt.Sprintf("%.0f m²", areaM2)
	} else {
		areaStr = fmt.Sprintf("%.1f ha", areaM2/10000)
	}
	
	narr.WriteString(fmt.Sprintf("%s (~%d residents, %s) %s", 
		strings.Title(typeDesc), population, areaStr, locDesc))
	
	return narr.String()
}

// GetNarrativesByYear returns narratives grouped by year for UI display
type YearNarratives struct {
	Year       int      `json:"year"`
	FireCount  int      `json:"fire_count"`
	FireGroups int      `json:"fire_groups"`
	Stories    []string `json:"stories"`
}

// getYearlyFireNarratives generates year-grouped fire narratives
func (s *Server) getYearlyFireNarratives(parkID, parkName string) []YearNarratives {
	ctx := s.getNarrativeContext(parkID, parkName)
	
	// Get all years of data
	rows, err := s.DB.Query(`
		SELECT year, total_groups, trajectories_json
		FROM park_group_infractions
		WHERE park_id = ?
		ORDER BY year DESC
	`, parkID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	
	var yearlyNarrs []YearNarratives
	
	for rows.Next() {
		var year, totalGroups int
		var trajJSON string
		rows.Scan(&year, &totalGroups, &trajJSON)
		
		if trajJSON == "" {
			continue
		}
		
		// Parse trajectories
		var trajs []map[string]interface{}
		if err := json.Unmarshal([]byte(trajJSON), &trajs); err != nil {
			continue
		}
		
		yn := YearNarratives{
			Year:       year,
			FireGroups: len(trajs),
		}
		
		// Sum fires and build stories (limit to top 5 by fires_inside)
		type trajWithFires struct {
			idx   int
			traj  map[string]interface{}
			fires int
		}
		var sortedTrajs []trajWithFires
		for i, t := range trajs {
			fires := int(t["fires_inside"].(float64))
			yn.FireCount += fires
			sortedTrajs = append(sortedTrajs, trajWithFires{i + 1, t, fires})
		}
		
		// Sort by fires descending
		sort.Slice(sortedTrajs, func(i, j int) bool {
			return sortedTrajs[i].fires > sortedTrajs[j].fires
		})
		
		// Generate stories for top 5
		for i, st := range sortedTrajs {
			if i >= 5 {
				break
			}
			story := s.EnhancedFireGroupNarrative(ctx, st.idx, st.traj)
			yn.Stories = append(yn.Stories, story)
		}
		
		yearlyNarrs = append(yearlyNarrs, yn)
	}
	
	return yearlyNarrs
}
