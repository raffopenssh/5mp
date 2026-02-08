package srv

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"time"
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
		narrative.Summary = "No significant fire group incursions recorded."
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

	// Trajectory narratives from most recent year
	narrative.Narratives = s.getTrajectoryNarratives(parkID, fromYear, toYear)

	return narrative
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

// getTrajectoryNarratives extracts trajectory stories from park_group_infractions
func (s *Server) getTrajectoryNarratives(parkID string, fromYear, toYear int) []FireGroupStory {
	var stories []FireGroupStory

	var trajJSON sql.NullString
	s.DB.QueryRow(`
		SELECT trajectories_json FROM park_group_infractions 
		WHERE park_id = ? AND year >= ? AND year <= ? AND trajectories_json IS NOT NULL
		ORDER BY year DESC LIMIT 1
	`, parkID, fromYear, toYear).Scan(&trajJSON)

	if !trajJSON.Valid || trajJSON.String == "" {
		return stories
	}

	var trajs []FireGroupTrajectory
	if err := json.Unmarshal([]byte(trajJSON.String), &trajs); err != nil {
		return stories
	}

	for i, t := range trajs {
		story := FireGroupStory{
			GroupNum:    i + 1,
			EntryDate:   t.EntryDate,
			LastInside:  t.LastInside,
			DaysInside:  t.DaysInside,
			FiresInside: t.FiresInside,
			Outcome:     t.Outcome,
		}

		// Calculate trajectory bearing
		trajBearing := bearingTo(t.Origin.Lat, t.Origin.Lon, t.Destination.Lat, t.Destination.Lon)
		movementDesc := fmt.Sprintf("moving %s", bearingToCardinalWithDegrees(trajBearing))

		// Describe locations
		story.OriginDesc = s.describeLocation(parkID, t.Origin.Lat, t.Origin.Lon)
		if strings.HasPrefix(story.OriginDesc, "at coordinates") {
			story.OriginDesc = fmt.Sprintf("(%.3f°, %.3f°), %s", t.Origin.Lat, t.Origin.Lon, movementDesc)
		} else {
			story.OriginDesc = fmt.Sprintf("%s, %s", story.OriginDesc, movementDesc)
		}
		story.DestDesc = s.describeLocation(parkID, t.Destination.Lat, t.Destination.Lon)

		// Build narrative
		var narr strings.Builder
		narr.WriteString(fmt.Sprintf("Fire group %d originated %s on %s. ", i+1, story.OriginDesc, t.EntryDate))

		daysWord := "days"
		if t.DaysInside == 1 {
			daysWord = "day"
		}
		narr.WriteString(fmt.Sprintf("Burned inside the park for %d %s (%d fire detections). ", t.DaysInside, daysWord, t.FiresInside))

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

// StartFireNarrativeCacheWorker runs weekly pre-computation
func (s *Server) StartFireNarrativeCacheWorker(ctx context.Context) {
	log.Println("[FireNarrativeCacheWorker] Started")

	// Run immediately if cache is empty
	var count int
	s.DB.QueryRow("SELECT COUNT(*) FROM fire_narrative_cache").Scan(&count)
	if count == 0 {
		log.Println("[FireNarrativeCacheWorker] Cache empty, running initial computation")
		s.PrecomputeFireNarratives(ctx)
	}

	// Then run weekly (Sunday 2am UTC)
	ticker := time.NewTicker(1 * time.Hour)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			log.Println("[FireNarrativeCacheWorker] Stopped")
			return
		case <-ticker.C:
			now := time.Now().UTC()
			if now.Weekday() == time.Sunday && now.Hour() == 2 {
				s.PrecomputeFireNarratives(ctx)
			}
		}
	}
}
