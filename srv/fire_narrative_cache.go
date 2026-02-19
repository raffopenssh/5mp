package srv

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"srv.exe.dev/srv/areas"
)

// PrecomputeFireNarratives computes and caches fire narratives for all parks
func (s *Server) PrecomputeFireNarratives(ctx context.Context) error {
	if s.AreaStore == nil {
		log.Println("[FireNarrativeCache] No area store, skipping")
		return nil
	}

	log.Printf("[FireNarrativeCache] Starting pre-computation for %d parks", len(s.AreaStore.Areas))
	start := time.Now()

	now := time.Now()
	fromYear := 2000
	toYear := now.Year()

	successCount := 0
	errorCount := 0

	for _, area := range s.AreaStore.Areas {
		select {
		case <-ctx.Done():
			log.Println("[FireNarrativeCache] Cancelled")
			return ctx.Err()
		default:
		}

		narrative := s.computeFireNarrativeForCache(area.ID, area.Name, fromYear, toYear)
		if narrative == nil {
			errorCount++
			continue
		}

		jsonData, err := json.Marshal(narrative)
		if err != nil {
			log.Printf("[FireNarrativeCache] JSON error for %s: %v", area.ID, err)
			errorCount++
			continue
		}

		_, err = s.DB.Exec(`
			INSERT INTO fire_narrative_cache (park_id, narrative_json, computed_at, from_year, to_year)
			VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
			ON CONFLICT(park_id) DO UPDATE SET
				narrative_json = excluded.narrative_json,
				computed_at = excluded.computed_at,
				from_year = excluded.from_year,
				to_year = excluded.to_year
		`, area.ID, string(jsonData), fromYear, toYear)
		if err != nil {
			log.Printf("[FireNarrativeCache] DB error for %s: %v", area.ID, err)
			errorCount++
			continue
		}

		successCount++
	}

	log.Printf("[FireNarrativeCache] Completed in %v: %d success, %d errors",
		time.Since(start), successCount, errorCount)
	return nil
}

// computeFireNarrativeForCache generates the full narrative without the slow trend subquery
func (s *Server) computeFireNarrativeForCache(parkID, parkName string, fromYear, toYear int) *FireNarrative {
	narrative := &FireNarrative{
		ParkID:   parkID,
		ParkName: parkName,
		Year:     toYear,
	}

	// Get aggregated fire data from pre-computed table (fast)
	var totalGroups, stoppedInside, transited int
	var avgDaysBurning float64
	var yearCount int

	err := s.DB.QueryRow(`
		SELECT 
			COUNT(DISTINCT year) as year_count,
			COALESCE(SUM(total_groups), 0) as total_groups,
			COALESCE(SUM(groups_stopped_inside), 0) as stopped,
			COALESCE(SUM(groups_transited), 0) as transited,
			COALESCE(AVG(avg_days_burning), 0) as avg_days
		FROM park_group_infractions 
		WHERE park_id = ? AND year >= ? AND year <= ? AND total_groups > 0
	`, parkID, fromYear, toYear).Scan(&yearCount, &totalGroups, &stoppedInside, &transited, &avgDaysBurning)

	if err != nil && err != sql.ErrNoRows {
		log.Printf("[FireNarrativeCache] Query error for %s: %v", parkID, err)
		return nil
	}

	// Get total fires using date range (uses index)
	var totalFires int
	s.DB.QueryRow(`
		SELECT COUNT(*) FROM fire_detections 
		WHERE protected_area_id = ? 
		  AND acq_date >= ? AND acq_date <= ?
	`, parkID,
		fmt.Sprintf("%d-01-01", fromYear),
		fmt.Sprintf("%d-12-31", toYear),
	).Scan(&totalFires)
	narrative.TotalFires = totalFires

	if totalGroups == 0 {
		// Even if DB table is empty, try to load from JSON files
		log.Printf("[FireNarrativeCache] DB empty for %s, trying JSON files (years %d-%d)", parkID, fromYear, toYear)
		ctx := s.getNarrativeContext(parkID, parkName)
		narrative.Narratives = s.getTrajectoryNarrativesFromJSON(parkID, fromYear, toYear, ctx)
		log.Printf("[FireNarrativeCache] JSON returned %d narratives for %s", len(narrative.Narratives), parkID)
		if len(narrative.Narratives) == 0 {
			narrative.Summary = "No significant fire group incursions recorded."
			return narrative
		}
		// Compute stats from loaded narratives
		for _, n := range narrative.Narratives {
			totalGroups++
			if n.Outcome == "STOPPED_INSIDE" {
				stoppedInside++
			} else if n.Outcome == "TRANSITED" {
				transited++
			}
			totalFires += n.FiresInside
		}
		narrative.TotalFires = totalFires
		if totalGroups > 0 {
			narrative.ResponseRate = float64(stoppedInside) / float64(totalGroups) * 100
		}
		narrative.Summary = s.buildFireSummary(parkName, fromYear, toYear, 1, totalFires, totalGroups, stoppedInside, transited, narrative.ResponseRate, "", 0)
		narrative.Trend = s.analyzeFireTrendFast(parkID, toYear)
		return narrative
	}

	// Response rate
	if totalGroups > 0 {
		narrative.ResponseRate = float64(stoppedInside) / float64(totalGroups) * 100
	}

	// Peak month
	var peakMonth string
	s.DB.QueryRow(`
		SELECT strftime('%m', acq_date) as month
		FROM fire_detections 
		WHERE protected_area_id = ? 
		  AND acq_date >= ? AND acq_date <= ?
		GROUP BY month ORDER BY COUNT(*) DESC LIMIT 1
	`, parkID,
		fmt.Sprintf("%d-01-01", fromYear),
		fmt.Sprintf("%d-12-31", toYear),
	).Scan(&peakMonth)
	monthNames := map[string]string{
		"01": "January", "02": "February", "03": "March", "04": "April",
		"05": "May", "06": "June", "07": "July", "08": "August",
		"09": "September", "10": "October", "11": "November", "12": "December",
	}
	narrative.PeakMonth = monthNames[peakMonth]

	// Build summary
	narrative.Summary = s.buildFireSummary(parkName, fromYear, toYear, yearCount, totalFires, totalGroups, stoppedInside, transited, narrative.ResponseRate, narrative.PeakMonth, avgDaysBurning)

	// Trend analysis using pre-computed table only (no fire_detections subquery)
	narrative.Trend = s.analyzeFireTrendFast(parkID, toYear)

	// Hotspots for most recent year
	narrative.Hotspots = s.analyzeFireHotspots(parkID, toYear, totalFires)

	// Get narrative context for enhanced location descriptions
	ctx := s.getNarrativeContext(parkID, parkName)
	
	// Trajectory narratives - try JSON files first (has proper outcome calculation),
	// then fall back to DB table
	narrative.Narratives = s.getTrajectoryNarrativesFromJSON(parkID, fromYear, toYear, ctx)
	if len(narrative.Narratives) == 0 {
		// Fall back to DB-based trajectories (legacy)
		narrative.Narratives = s.getTrajectoryNarratives(parkID, fromYear, toYear, ctx)
	}

	return narrative
}

// getSeasonalContext returns seasonal description based on climate data
func (s *Server) getSeasonalContext(parkID string, month int) string {
	var precipAnnual, precipWettest, precipDriest int
	err := s.DB.QueryRow(`
		SELECT precip_annual_mm, precip_wettest_mm, precip_driest_mm 
		FROM park_climate WHERE park_id = ?
	`, parkID).Scan(&precipAnnual, &precipWettest, &precipDriest)
	if err != nil {
		return ""
	}
	
	// Determine if the park has distinct seasons
	if precipAnnual == 0 || precipWettest == 0 {
		return ""
	}
	
	// Calculate seasonality index (ratio of wettest to driest month)
	seasonality := float64(precipWettest) / float64(precipDriest+1)
	
	if seasonality < 3 {
		return "year-round rainfall"
	}
	
	// Africa typically has rainy season in summer months
	// Northern hemisphere: June-Sept
	// Southern hemisphere: Nov-March
	// Equatorial: two rainy seasons
	switch {
	case month >= 6 && month <= 9:
		return "during the typical dry season"
	case month >= 11 || month <= 2:
		return "during the typical rainy season"
	default:
		return "during the transition season"
	}
}

// buildFireSummary creates the summary text
func (s *Server) buildFireSummary(parkName string, fromYear, toYear, yearCount, totalFires, totalGroups, stoppedInside, transited int, responseRate float64, peakMonth string, avgDaysBurning float64) string {
	var parts []string

	periodDesc := fmt.Sprintf("%d", fromYear)
	if fromYear != toYear {
		periodDesc = fmt.Sprintf("%d-%d", fromYear, toYear)
	}

	if yearCount > 1 {
		parts = append(parts,
			fmt.Sprintf("From %s, %s experienced %d fire detections across %d fire groups over %d years.",
				periodDesc, parkName, totalFires, totalGroups, yearCount))
	} else {
		parts = append(parts,
			fmt.Sprintf("In %s, %s experienced %d fire detections across %d distinct fire groups.",
				periodDesc, parkName, totalFires, totalGroups))
	}

	if stoppedInside > 0 {
		parts = append(parts,
			fmt.Sprintf("%d group(s) (%.0f%%) were stopped inside the park, suggesting effective ranger intervention.",
				stoppedInside, responseRate))
	}
	if transited > 0 {
		parts = append(parts,
			fmt.Sprintf("%d group(s) transited through without being stopped.", transited))
	}
	if peakMonth != "" {
		parts = append(parts,
			fmt.Sprintf("Peak fire activity occurred in %s.", peakMonth))
	}
	if avgDaysBurning > 0 {
		parts = append(parts,
			fmt.Sprintf("Fire groups burned inside the park for an average of %.1f days.", avgDaysBurning))
	}

	return strings.Join(parts, " ")
}

// analyzeFireTrendFast uses only park_group_infractions (no expensive subquery)
func (s *Server) analyzeFireTrendFast(parkID string, currentYear int) *FireTrendAnalysis {
	trend := &FireTrendAnalysis{}

	// Get park area for groups_per_km2 calculation
	var areaKm2 float64 = 1.0 // default to avoid division by zero
	var parkLat float64 = 0
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.ID == parkID {
				if area.AreaKm2 > 0 {
					areaKm2 = area.AreaKm2
				}
				// Get latitude from bbox center
				if area.Geometry.Type != "" {
					lat, _ := area.CenterLatLon()
					parkLat = lat
				}
				break
			}
		}
	}

	// Query only from pre-computed table
	rows, err := s.DB.Query(`
		SELECT 
			year,
			total_groups,
			groups_stopped_inside,
			groups_transited,
			avg_days_burning,
			COALESCE(total_fires_inside, 0) as total_fires
		FROM park_group_infractions
		WHERE park_id = ?
		ORDER BY year
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
		var avgDays sql.NullFloat64
		if err := rows.Scan(&ys.Year, &ys.TotalGroups, &ys.StoppedInside, &ys.Transited, &avgDays, &ys.TotalFires); err != nil {
			continue
		}
		if avgDays.Valid {
			ys.AvgDaysBurning = avgDays.Float64
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

	// Calculate groups_per_km2 for each year
	var totalGroupsPerKm2 float64
	for i := range trend.Years {
		if areaKm2 > 0 {
			trend.Years[i].GroupsPerKm2 = float64(trend.Years[i].TotalGroups) / areaKm2
			totalGroupsPerKm2 += trend.Years[i].GroupsPerKm2
		}
	}
	if len(trend.Years) > 0 {
		trend.AvgGroupsPerKm2 = totalGroupsPerKm2 / float64(len(trend.Years))
	}

	// Query monthly data from feature_geometries
	monthRows, err := s.DB.Query(`
		SELECT strftime('%Y-%m', start_date) as month, COUNT(*) as groups
		FROM feature_geometries 
		WHERE park_id = ? AND feature_type = 'fire_trajectory' AND start_date IS NOT NULL
		GROUP BY month 
		ORDER BY month
	`, parkID)
	if err == nil {
		defer monthRows.Close()
		monthCounts := make(map[int]int) // month number (1-12) -> total groups
		for monthRows.Next() {
			var month string
			var groups int
			if err := monthRows.Scan(&month, &groups); err == nil {
				ms := FireMonthSummary{Month: month, Groups: groups}
				if areaKm2 > 0 {
					ms.GroupsPerKm2 = float64(groups) / areaKm2
				}
				trend.Months = append(trend.Months, ms)
				
				// Track by calendar month for seasonality
				if len(month) >= 7 {
					var m int
					fmt.Sscanf(month[5:7], "%d", &m)
					monthCounts[m] += groups
				}
			}
		}
		
		// Determine peak months and seasonality
		var maxGroups int
		for _, g := range monthCounts {
			if g > maxGroups {
				maxGroups = g
			}
		}
		threshold := maxGroups * 7 / 10 // 70% of peak
		monthNames := []string{"", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"}
		for m := 1; m <= 12; m++ {
			if monthCounts[m] >= threshold {
				trend.PeakMonths = append(trend.PeakMonths, monthNames[m])
			}
		}
		
		// Generate seasonality description
		if len(trend.PeakMonths) > 0 {
			if len(trend.PeakMonths) <= 3 {
				trend.Seasonality = "Peak activity in " + joinWithAnd(trend.PeakMonths)
			} else {
				trend.Seasonality = fmt.Sprintf("Peak activity %s-%s", trend.PeakMonths[0], trend.PeakMonths[len(trend.PeakMonths)-1])
			}
		}
	}

	// Add latitude comparison
	if parkLat != 0 && s.AreaStore != nil {
		trend.LatitudeComparison = s.computeLatitudeComparison(parkID, parkLat, trend.AvgGroupsPerKm2)
	}

	// Determine trend direction
	if len(trend.Years) >= 2 {
		recentYears := trend.Years
		if len(recentYears) > 3 {
			recentYears = trend.Years[len(trend.Years)-3:]
		}
		firstGroups := recentYears[0].TotalGroups
		lastGroups := recentYears[len(recentYears)-1].TotalGroups
		if lastGroups > firstGroups*12/10 {
			trend.TrendDirection = "increasing"
		} else if lastGroups < firstGroups*8/10 {
			trend.TrendDirection = "decreasing"
		} else {
			trend.TrendDirection = "stable"
		}
	}

	return trend
}

// getTrajectoryNarratives extracts trajectory stories from park_group_infractions with enhanced context
func (s *Server) getTrajectoryNarratives(parkID string, fromYear, toYear int, ctx *NarrativeContext) []FireGroupStory {
	var stories []FireGroupStory

	// Get trajectories from ALL years in range
	rows, err := s.DB.Query(`
		SELECT year, trajectories_json FROM park_group_infractions 
		WHERE park_id = ? AND year >= ? AND year <= ? AND trajectories_json IS NOT NULL
		ORDER BY year ASC
	`, parkID, fromYear, toYear)
	if err != nil {
		return stories
	}
	defer rows.Close()

	var allTrajs []FireGroupTrajectory
	for rows.Next() {
		var year int
		var trajJSON sql.NullString
		if err := rows.Scan(&year, &trajJSON); err != nil || !trajJSON.Valid {
			continue
		}
		var trajs []FireGroupTrajectory
		if err := json.Unmarshal([]byte(trajJSON.String), &trajs); err != nil {
			continue
		}
		// Set year on each trajectory
		for i := range trajs {
			trajs[i].Year = year
		}
		allTrajs = append(allTrajs, trajs...)
	}

	if len(allTrajs) == 0 {
		return stories
	}

	trajs := allTrajs

	for i, t := range trajs {
		story := FireGroupStory{
			GroupNum:    i + 1,
			Year:        t.Year,
			EntryDate:   t.EntryDate,
			LastInside:  t.LastInside,
			DaysInside:  t.DaysInside,
			FiresInside: t.FiresInside,
			Outcome:     t.Outcome,
		}

		// Calculate trajectory bearing
		trajBearing := bearingTo(t.Origin.Lat, t.Origin.Lon, t.Destination.Lat, t.Destination.Lon)
		movementDesc := fmt.Sprintf("moving %s", bearingToCardinalWithDegrees(trajBearing))

		// Build verbose narrative with enhanced context
		var narr strings.Builder
		
		// Get seasonal context
		seasonStr := ""
		if ctx != nil {
			if season := ctx.getSeasonForDate(t.EntryDate); season != "" {
				seasonStr = fmt.Sprintf(" (%s)", season)
			}
		}
		
		// Describe origin with enhanced context if available
		if ctx != nil {
			originLoc := ctx.describeLocationWithContext(t.Origin.Lat, t.Origin.Lon)
			if originLoc != "" && !strings.HasPrefix(originLoc, "at (") {
				story.OriginDesc = fmt.Sprintf("%s, %s", originLoc, movementDesc)
			} else {
				story.OriginDesc = fmt.Sprintf("(%.3f°, %.3f°), %s", t.Origin.Lat, t.Origin.Lon, movementDesc)
			}
		} else {
			story.OriginDesc = s.describeLocation(parkID, t.Origin.Lat, t.Origin.Lon)
			if strings.HasPrefix(story.OriginDesc, "at coordinates") {
				story.OriginDesc = fmt.Sprintf("(%.3f°, %.3f°), %s", t.Origin.Lat, t.Origin.Lon, movementDesc)
			} else {
				story.OriginDesc = fmt.Sprintf("%s, %s", story.OriginDesc, movementDesc)
			}
		}
		
		// Describe destination with enhanced context
		if ctx != nil {
			story.DestDesc = ctx.describeLocationWithContext(t.Destination.Lat, t.Destination.Lon)
			if story.DestDesc == "" || strings.HasPrefix(story.DestDesc, "at (") {
				story.DestDesc = fmt.Sprintf("at coordinates (%.3f°, %.3f°)", t.Destination.Lat, t.Destination.Lon)
			}
		} else {
			story.DestDesc = s.describeLocation(parkID, t.Destination.Lat, t.Destination.Lon)
		}

		// Build verbose narrative
		narr.WriteString(fmt.Sprintf("Fire group %d originated %s on %s%s. ", 
			i+1, story.OriginDesc, t.EntryDate, seasonStr))

		daysWord := "days"
		if t.DaysInside == 1 {
			daysWord = "day"
		}
		narr.WriteString(fmt.Sprintf("Burned inside the park for %d %s (%d fire detections). ", 
			t.DaysInside, daysWord, t.FiresInside))

		switch t.Outcome {
		case "STOPPED_INSIDE":
			narr.WriteString(fmt.Sprintf("Last detected %s - fire stopped, possibly due to ranger intervention.", story.DestDesc))
		case "TRANSITED":
			narr.WriteString(fmt.Sprintf("Exited the park %s on %s - transited without being stopped.", story.DestDesc, t.LastInside))
		default:
			narr.WriteString(fmt.Sprintf("Last detected %s.", story.DestDesc))
		}

		story.Narrative = narr.String()
		stories = append(stories, story)
	}

	return stories
}

// TrajectoryV2 represents a fire trajectory from the v2 JSON files
type TrajectoryV2 struct {
	Fires              int               `json:"fires"`
	StartDate          string            `json:"start_date"`
	EndDate            string            `json:"end_date"`
	Days               int               `json:"days"`
	Direction          string            `json:"direction"`
	DistanceKm         float64           `json:"distance_km"`
	GroupType          string            `json:"group_type"`
	PctInside          float64           `json:"pct_inside"`
	CrossBorder        bool              `json:"cross_border"`
	AffectedParks      []string          `json:"affected_parks"`
	Year               int               `json:"year"`
	Narrative          string            `json:"narrative"`
	TrajectoryWithTime []TrajectoryPoint `json:"trajectory_with_time"`
}

type TrajectoryPoint struct {
	Lon  float64 `json:"lon"`
	Lat  float64 `json:"lat"`
	Date string  `json:"date"`
}

// getTrajectoryNarrativesFromJSON loads trajectories from v2 JSON files and computes outcomes
// based on whether the last detection point is inside the park boundary
func (s *Server) getTrajectoryNarrativesFromJSON(parkID string, fromYear, toYear int, ctx *NarrativeContext) []FireGroupStory {
	var stories []FireGroupStory

	// Load trajectory JSON file
	trajFile := fmt.Sprintf("data/fire_trajectories_v2/%s.json", parkID)
	data, err := os.ReadFile(trajFile)
	if err != nil {
		log.Printf("[FireNarrativeCache] No trajectory file for %s: %v", parkID, err)
		return stories
	}

	var trajs []TrajectoryV2
	if err := json.Unmarshal(data, &trajs); err != nil {
		log.Printf("[FireNarrativeCache] Error parsing trajectories for %s: %v", parkID, err)
		return stories
	}
	log.Printf("[FireNarrativeCache] Loaded %d trajectories for %s (years %d-%d)", len(trajs), parkID, fromYear, toYear)

	// Find the park in AreaStore for boundary checking
	var parkArea *areas.ProtectedArea
	if s.AreaStore != nil {
		for i := range s.AreaStore.Areas {
			if s.AreaStore.Areas[i].ID == parkID {
				parkArea = &s.AreaStore.Areas[i]
				break
			}
		}
	}

	groupNum := 0
	for _, t := range trajs {
		// Filter by year range
		if t.Year < fromYear || t.Year > toYear {
			continue
		}
		groupNum++

		// Determine outcome based on last point location
		outcome := "UNKNOWN"
		if len(t.TrajectoryWithTime) > 0 && parkArea != nil {
			lastPt := t.TrajectoryWithTime[len(t.TrajectoryWithTime)-1]
			// Use ContainsPoint with zero buffer for strict inside check
			if parkArea.ContainsPoint(lastPt.Lat, lastPt.Lon) {
				outcome = "STOPPED_INSIDE"
			} else {
				outcome = "TRANSITED"
			}
		}

		story := FireGroupStory{
			GroupNum:    groupNum,
			FeatureID:   fmt.Sprintf("%s_%d_grp_%d", parkID, t.Year, groupNum),
			Year:        t.Year,
			EntryDate:   t.StartDate,
			LastInside:  t.EndDate,
			DaysInside:  t.Days,
			FiresInside: t.Fires,
			Outcome:     outcome,
		}

		// Get origin and destination from trajectory
		if len(t.TrajectoryWithTime) > 0 {
			firstPt := t.TrajectoryWithTime[0]
			lastPt := t.TrajectoryWithTime[len(t.TrajectoryWithTime)-1]

			// Calculate bearing
			trajBearing := bearingTo(firstPt.Lat, firstPt.Lon, lastPt.Lat, lastPt.Lon)
			movementDesc := fmt.Sprintf("moving %s", bearingToCardinalWithDegrees(trajBearing))

			// Describe origin
			if ctx != nil {
				originLoc := ctx.describeLocationWithContext(firstPt.Lat, firstPt.Lon)
				if originLoc != "" && !strings.HasPrefix(originLoc, "at (") {
					story.OriginDesc = fmt.Sprintf("%s, %s", originLoc, movementDesc)
				} else {
					story.OriginDesc = fmt.Sprintf("(%.3f°, %.3f°), %s", firstPt.Lat, firstPt.Lon, movementDesc)
				}
				story.DestDesc = ctx.describeLocationWithContext(lastPt.Lat, lastPt.Lon)
				if story.DestDesc == "" || strings.HasPrefix(story.DestDesc, "at (") {
					story.DestDesc = fmt.Sprintf("at (%.3f°, %.3f°)", lastPt.Lat, lastPt.Lon)
				}
			} else {
				story.OriginDesc = fmt.Sprintf("(%.3f°, %.3f°), %s", firstPt.Lat, firstPt.Lon, movementDesc)
				story.DestDesc = fmt.Sprintf("at (%.3f°, %.3f°)", lastPt.Lat, lastPt.Lon)
			}
		}

		// Use existing narrative or build one
		if t.Narrative != "" {
			story.Narrative = t.Narrative
		} else {
			var narr strings.Builder
			narr.WriteString(fmt.Sprintf("Fire group originated %s on %s. ", story.OriginDesc, t.StartDate))
			daysWord := "days"
			if t.Days == 1 {
				daysWord = "day"
			}
			narr.WriteString(fmt.Sprintf("Burned for %d %s (%d detections). ", t.Days, daysWord, t.Fires))
			if outcome == "STOPPED_INSIDE" {
				narr.WriteString("Fire stopped inside the park.")
			} else if outcome == "TRANSITED" {
				narr.WriteString("Fire transited through the park.")
			}
			story.Narrative = narr.String()
		}

		stories = append(stories, story)
	}

	return stories
}

// GetCachedFireNarrative retrieves the cached narrative for a park
func (s *Server) GetCachedFireNarrative(parkID string) (*FireNarrative, time.Time, error) {
	var jsonData string
	var computedAt time.Time

	err := s.DB.QueryRow(`
		SELECT narrative_json, computed_at 
		FROM fire_narrative_cache 
		WHERE park_id = ?
	`, parkID).Scan(&jsonData, &computedAt)

	if err != nil {
		return nil, time.Time{}, err
	}

	var narrative FireNarrative
	if err := json.Unmarshal([]byte(jsonData), &narrative); err != nil {
		return nil, time.Time{}, err
	}

	return &narrative, computedAt, nil
}

// StartNarrativeCacheWorker runs pre-computation for all narrative types
// - Fire narratives: daily for recent fires (last 14 days), weekly full refresh
// - Settlement/Deforestation: annually (classifications don't change often)
func (s *Server) StartNarrativeCacheWorker(ctx context.Context) {
	log.Println("[NarrativeCacheWorker] Started")

	// Run immediately if cache is empty
	var fireCount, settCount int
	s.DB.QueryRow("SELECT COUNT(*) FROM fire_narrative_cache").Scan(&fireCount)
	s.DB.QueryRow("SELECT COUNT(*) FROM park_settlements WHERE classified_at IS NOT NULL").Scan(&settCount)
	
	if fireCount == 0 {
		log.Println("[NarrativeCacheWorker] Fire cache empty, running initial computation")
		s.PrecomputeFireNarratives(ctx)
	}
	
	if settCount == 0 {
		log.Println("[NarrativeCacheWorker] Classifications empty, running initial computation")
		s.PrecomputeAllClassifications(ctx)
	}

	// Check every hour for scheduled tasks
	ticker := time.NewTicker(1 * time.Hour)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			log.Println("[NarrativeCacheWorker] Stopped")
			return
		case <-ticker.C:
			now := time.Now().UTC()
			
			// Daily at 3am UTC: Refresh fire narratives for parks with recent fires (last 14 days)
			if now.Hour() == 3 {
				log.Println("[NarrativeCacheWorker] Running daily fire refresh for recent activity")
				s.PrecomputeRecentFireNarratives(ctx, 14)
			}
			
			// Weekly (Sunday 2am UTC): Full fire narrative refresh
			if now.Weekday() == time.Sunday && now.Hour() == 2 {
				log.Println("[NarrativeCacheWorker] Running weekly full fire refresh")
				s.PrecomputeFireNarratives(ctx)
			}
			
			// Annually (January 1st, 4am UTC): Refresh settlement/deforestation classifications
			if now.Month() == time.January && now.Day() == 1 && now.Hour() == 4 {
				log.Println("[NarrativeCacheWorker] Running annual classification refresh")
				s.PrecomputeAllClassifications(ctx)
			}
		}
	}
}

// PrecomputeRecentFireNarratives updates fire narratives only for parks with recent fire activity
func (s *Server) PrecomputeRecentFireNarratives(ctx context.Context, days int) {
	// Find parks with fires in the last N days
	cutoff := time.Now().AddDate(0, 0, -days).Format("2006-01-02")
	rows, err := s.DB.Query(`
		SELECT DISTINCT park_id 
		FROM fire_detections 
		WHERE acq_date >= ?
		GROUP BY park_id
		HAVING COUNT(*) > 0`, cutoff)
	if err != nil {
		log.Printf("[FireNarrativeCache] Error finding recent fire parks: %v", err)
		return
	}
	defer rows.Close()
	
	var parksWithFires []string
	for rows.Next() {
		var parkID string
		rows.Scan(&parkID)
		parksWithFires = append(parksWithFires, parkID)
	}
	
	if len(parksWithFires) == 0 {
		log.Println("[FireNarrativeCache] No parks with recent fire activity")
		return
	}
	
	log.Printf("[FireNarrativeCache] Refreshing %d parks with recent fire activity", len(parksWithFires))
	
	for _, parkID := range parksWithFires {
		select {
		case <-ctx.Done():
			return
		default:
		}
		// Compute and cache for this single park
		fromYear := time.Now().Year() - 25
		toYear := time.Now().Year()
		parkName := parkID
		for _, area := range s.AreaStore.Areas {
			if area.ID == parkID {
				parkName = area.Name
				break
			}
		}
		narrative := s.computeFireNarrativeForCache(parkID, parkName, fromYear, toYear)
		if narrative != nil {
			narrativeJSON, _ := json.Marshal(narrative)
			s.DB.Exec(`
				INSERT INTO fire_narrative_cache (park_id, narrative_json, computed_at, from_year, to_year)
				VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
				ON CONFLICT(park_id) DO UPDATE SET
					narrative_json = excluded.narrative_json,
					computed_at = CURRENT_TIMESTAMP,
					from_year = excluded.from_year,
					to_year = excluded.to_year`,
				parkID, string(narrativeJSON), fromYear, toYear)
		}
	}
}

// PrecomputeAllClassifications classifies settlements and deforestation for all parks
func (s *Server) PrecomputeAllClassifications(ctx context.Context) {
	if s.AreaStore == nil {
		log.Println("[Classification] No area store, skipping")
		return
	}
	
	log.Printf("[Classification] Starting for %d parks", len(s.AreaStore.Areas))
	start := time.Now()
	settCount, defoCount := 0, 0
	
	for i, area := range s.AreaStore.Areas {
		select {
		case <-ctx.Done():
			log.Println("[Classification] Cancelled")
			return
		default:
		}
		
		if i > 0 && i%20 == 0 {
			log.Printf("[Classification] Progress: %d/%d parks", i, len(s.AreaStore.Areas))
		}
		
		sc, dc := s.classifyParkData(area.ID)
		settCount += sc
		defoCount += dc
	}
	
	log.Printf("[Classification] Done in %v: %d settlements, %d deforestation events",
		time.Since(start), settCount, defoCount)
}

func (s *Server) classifyParkData(parkID string) (int, int) {
	settCount := s.classifyParkSettlements(parkID)
	defoCount := s.classifyParkDeforestation(parkID)
	return settCount, defoCount
}

func (s *Server) classifyParkSettlements(parkID string) int {
	// Only reclassify if unclassified or older than 1 year
	rows, err := s.DB.Query(`
		SELECT id, lat, lon, area_m2, population_est, nearest_place, distance_to_place_km
		FROM park_settlements
		WHERE park_id = ? AND (classified_at IS NULL OR classified_at < datetime('now', '-365 days'))
	`, parkID)
	if err != nil {
		return 0
	}
	defer rows.Close()
	
	count := 0
	for rows.Next() {
		var st ClassifiedSettlement
		var nearestPlace sql.NullString
		var distToPlace sql.NullFloat64
		
		err := rows.Scan(&st.ID, &st.Lat, &st.Lon, &st.AreaM2, &st.PopulationEst, &nearestPlace, &distToPlace)
		if err != nil {
			continue
		}
		
		if nearestPlace.Valid {
			st.NearestPlace = nearestPlace.String
		}
		if distToPlace.Valid {
			st.DistanceToPlace = distToPlace.Float64
		}
		
		st.ParkID = parkID
		s.ClassifySettlement(parkID, &st)
		
		s.DB.Exec(`
			UPDATE park_settlements SET
				classification = ?,
				classification_confidence = ?,
				narrative = ?,
				fires_5km = ?,
				fire_seasonality = ?,
				deforest_nearby_km2 = ?,
				classified_at = CURRENT_TIMESTAMP
			WHERE id = ?
		`, st.Classification, st.Confidence, st.Narrative,
			st.FiresWithin5km, st.FireSeasonality, st.DeforestNearby, st.ID)
		count++
	}
	return count
}

func (s *Server) classifyParkDeforestation(parkID string) int {
	// Only reclassify if unclassified or older than 1 year
	rows, err := s.DB.Query(`
		SELECT id, year, area_km2, lat, lon, COALESCE(pattern_type, '')
		FROM deforestation_events
		WHERE park_id = ? AND (classified_at IS NULL OR classified_at < datetime('now', '-365 days'))
	`, parkID)
	if err != nil {
		return 0
	}
	defer rows.Close()
	
	count := 0
	for rows.Next() {
		var df ClassifiedDeforestation
		err := rows.Scan(&df.ID, &df.Year, &df.AreaKm2, &df.Lat, &df.Lon, &df.OriginalPattern)
		if err != nil {
			continue
		}
		
		df.ParkID = parkID
		s.ClassifyDeforestation(parkID, &df)
		
		s.DB.Exec(`
			UPDATE deforestation_events SET
				classification = ?,
				classification_confidence = ?,
				narrative = ?,
				fires_same_year = ?,
				fire_ratio = ?,
				nearest_settlement_km = ?,
				classified_at = CURRENT_TIMESTAMP
			WHERE id = ?
		`, df.Classification, df.Confidence, df.Narrative,
			df.FiresSameYear, df.FireRatio, df.NearestSettlement, df.ID)
		count++
	}
	return count
}

// GetCachedClassifiedSettlements returns pre-computed classifications from DB
func (s *Server) GetCachedClassifiedSettlements(parkID string) []ClassifiedSettlement {
	rows, err := s.DB.Query(`
		SELECT id, park_id, lat, lon, area_m2, population_est,
			COALESCE(classification, 'unknown'), COALESCE(classification_confidence, 0),
			COALESCE(narrative, ''), COALESCE(nearest_place, ''), COALESCE(distance_to_place_km, 0),
			COALESCE(fires_5km, 0), COALESCE(fire_seasonality, ''), COALESCE(deforest_nearby_km2, 0)
		FROM park_settlements
		WHERE park_id = ?
		ORDER BY area_m2 DESC
	`, parkID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	
	var settlements []ClassifiedSettlement
	for rows.Next() {
		var st ClassifiedSettlement
		err := rows.Scan(&st.ID, &st.ParkID, &st.Lat, &st.Lon, &st.AreaM2, &st.PopulationEst,
			&st.Classification, &st.Confidence, &st.Narrative,
			&st.NearestPlace, &st.DistanceToPlace,
			&st.FiresWithin5km, &st.FireSeasonality, &st.DeforestNearby)
		if err != nil {
			continue
		}
		settlements = append(settlements, st)
	}
	return settlements
}

// GetCachedClassifiedDeforestation returns pre-computed classifications from DB
func (s *Server) GetCachedClassifiedDeforestation(parkID string) []ClassifiedDeforestation {
	rows, err := s.DB.Query(`
		SELECT id, park_id, year, area_km2, lat, lon,
			COALESCE(classification, 'unknown'), COALESCE(classification_confidence, 0),
			COALESCE(narrative, ''), COALESCE(pattern_type, ''),
			COALESCE(fires_same_year, 0), COALESCE(fire_ratio, 0), COALESCE(nearest_settlement_km, 0),
			COALESCE(polygon_ids, '')
		FROM deforestation_events
		WHERE park_id = ?
		ORDER BY year DESC, area_km2 DESC
	`, parkID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	
	var events []ClassifiedDeforestation
	for rows.Next() {
		var df ClassifiedDeforestation
		err := rows.Scan(&df.ID, &df.ParkID, &df.Year, &df.AreaKm2, &df.Lat, &df.Lon,
			&df.Classification, &df.Confidence, &df.Narrative, &df.OriginalPattern,
			&df.FiresSameYear, &df.FireRatio, &df.NearestSettlement, &df.PolygonIDs)
		if err != nil {
			continue
		}
		events = append(events, df)
	}
	return events
}

// joinWithAnd joins strings with commas and "and" before the last item
func joinWithAnd(items []string) string {
	switch len(items) {
	case 0:
		return ""
	case 1:
		return items[0]
	case 2:
		return items[0] + " and " + items[1]
	default:
		return strings.Join(items[:len(items)-1], ", ") + ", and " + items[len(items)-1]
	}
}

// computeLatitudeComparison compares a park's fire activity to others at similar latitude
func (s *Server) computeLatitudeComparison(parkID string, parkLat, avgGroupsPerKm2 float64) *LatitudeComparison {
	if s.AreaStore == nil || avgGroupsPerKm2 == 0 {
		return nil
	}

	// Define latitude band (±5 degrees)
	latMin := parkLat - 5
	latMax := parkLat + 5

	// Get all parks in this latitude band with their fire data
	var parksInBand []struct {
		id           string
		groupsPerKm2 float64
	}

	// Query groups per km2 for each park in the latitude band
	for _, area := range s.AreaStore.Areas {
		lat, _ := area.CenterLatLon()
		if lat >= latMin && lat <= latMax && area.AreaKm2 > 0 {
			// Get total groups for this park
			var totalGroups int
			s.DB.QueryRow(`
				SELECT COALESCE(SUM(total_groups), 0) 
				FROM park_group_infractions 
				WHERE park_id = ?
			`, area.ID).Scan(&totalGroups)
			
			if totalGroups > 0 {
				gpk := float64(totalGroups) / area.AreaKm2 / 7.0 // Average per year (approx 7 years of data)
				parksInBand = append(parksInBand, struct {
					id           string
					groupsPerKm2 float64
				}{area.ID, gpk})
			}
		}
	}

	if len(parksInBand) < 3 {
		return nil // Not enough data for meaningful comparison
	}

	// Calculate region average and percentile
	var regionTotal float64
	var betterCount int
	for _, p := range parksInBand {
		regionTotal += p.groupsPerKm2
		if p.groupsPerKm2 < avgGroupsPerKm2 {
			betterCount++
		}
	}
	regionAvg := regionTotal / float64(len(parksInBand))
	percentile := float64(betterCount) / float64(len(parksInBand)) * 100

	// Determine latitude band name
	bandName := "equatorial"
	if parkLat > 10 {
		bandName = "tropical north"
	} else if parkLat > 5 {
		bandName = "northern equatorial"
	} else if parkLat < -10 {
		bandName = "tropical south"
	} else if parkLat < -5 {
		bandName = "southern equatorial"
	}

	return &LatitudeComparison{
		ParkLatitude:    parkLat,
		AvgGroupsPerKm2: avgGroupsPerKm2,
		RegionAvg:       regionAvg,
		Percentile:      percentile,
		ComparedParks:   len(parksInBand),
		LatitudeBand:    bandName,
	}
}
