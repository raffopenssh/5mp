package srv

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"
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
	
	// Deforestation data
	Deforestation *DeforestationStats `json:"deforestation,omitempty"`
	
	// Narrative insights
	Insights []string `json:"insights,omitempty"`
	
	// Fire timeline for charts
	FireTimeline []FireDayCount `json:"fire_timeline,omitempty"`
	
	// Multi-year fire trends
	FireTrend []YearlyFireSummary `json:"fire_trend,omitempty"`
}

type FireStats struct {
	Year              int     `json:"year"`
	GroupsEntered     int     `json:"groups_entered"`
	GroupsStoppedInside int   `json:"groups_stopped_inside"`
	GroupsTransited   int     `json:"groups_transited"`
	ResponseRate      float64 `json:"response_rate"`
	AvgDaysInside     float64 `json:"avg_days_inside"`
	TotalFires        int     `json:"total_fires"`
	PeakMonth         string  `json:"peak_month,omitempty"`
	Trajectories      []FireGroupTrajectory `json:"trajectories,omitempty"`
}

type FireGroupTrajectory struct {
	Origin      GeoPoint `json:"origin"`
	Destination GeoPoint `json:"dest"`
	EntryDate   string   `json:"entry_date"`
	LastInside  string   `json:"last_inside"`
	DaysInside  int      `json:"days_inside"`
	FiresInside int      `json:"fires_inside"`
	Outcome     string   `json:"outcome"`
	Path        []GeoPointWithDate `json:"path,omitempty"`
}

type GeoPoint struct {
	Lat  float64 `json:"lat"`
	Lon  float64 `json:"lon"`
	Date string  `json:"date,omitempty"`
}

type GeoPointWithDate struct {
	Lat   float64 `json:"lat"`
	Lon   float64 `json:"lon"`
	Date  string  `json:"date"`
	Fires int     `json:"fires,omitempty"`
}

type SettlementStats struct {
	BuiltUpKm2      float64 `json:"built_up_km2"`
	SettlementCount int     `json:"settlement_count"`
}

type RoadlessStats struct {
	RoadlessPercentage float64 `json:"roadless_percentage"`
	TotalRoadKm        float64 `json:"total_road_km"`
}

type DeforestationStats struct {
	TotalLossKm2 float64 `json:"total_loss_km2"`
	WorstYear    int     `json:"worst_year"`
	WorstYearKm2 float64 `json:"worst_year_km2"`
	Trend        string  `json:"trend"` // "improving", "worsening", "stable"
	YearlyData   []YearlyDeforestation `json:"yearly_data,omitempty"`
}

type YearlyDeforestation struct {
	Year    int     `json:"year"`
	LossKm2 float64 `json:"loss_km2"`
}

type FireDayCount struct {
	Date  string `json:"date"`
	Count int    `json:"count"`
}

type YearlyFireSummary struct {
	Year       int `json:"year"`
	TotalFires int `json:"total_fires"`
	Groups     int `json:"groups"`
}

// HandleAPIParkStats returns combined park statistics with insights
// GET /api/parks/{id}/stats
func (s *Server) HandleAPIParkStats(w http.ResponseWriter, r *http.Request) {
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
	
	stats := ParkStats{ParkID: parkID}
	var insights []string
	
	// Query aggregated fire infraction data across year range
	var fire FireStats
	var trajJSON sql.NullString
	err := s.DB.QueryRow(`
		SELECT 
			MAX(year) as year,
			SUM(total_groups) as total_groups,
			SUM(groups_stopped_inside) as stopped,
			SUM(groups_transited) as transited,
			AVG(avg_days_burning) as avg_days,
			(SELECT trajectories_json FROM park_group_infractions WHERE park_id = ? ORDER BY year DESC LIMIT 1) as traj
		FROM park_group_infractions 
		WHERE park_id = ? AND year >= ? AND year <= ?
	`, internalID, internalID, fromYear, toYear).Scan(&fire.Year, &fire.GroupsEntered, &fire.GroupsStoppedInside, &fire.GroupsTransited, &fire.AvgDaysInside, &trajJSON)
	
	if err == nil && fire.GroupsEntered > 0 {
		// Parse trajectory JSON if available
		if trajJSON.Valid && trajJSON.String != "" {
			var trajs []FireGroupTrajectory
			if json.Unmarshal([]byte(trajJSON.String), &trajs) == nil {
				fire.Trajectories = trajs
			}
		}
		fire.ResponseRate = float64(fire.GroupsStoppedInside) / float64(fire.GroupsEntered) * 100
		stats.Fire = &fire
		
		// Generate fire insights with trajectory details
		if fire.GroupsTransited > 0 {
			// Find example of transited group with origin/destination
			for _, t := range fire.Trajectories {
				if t.Outcome == "TRANSITED" {
					insights = append(insights, fmt.Sprintf(
						"⚠️ Fire group transited: originated at (%.2f°, %.2f°) on %s, burned inside for %d days, then continued to (%.2f°, %.2f°).",
						t.Origin.Lat, t.Origin.Lon, t.EntryDate, t.DaysInside, t.Destination.Lat, t.Destination.Lon))
					break
				}
			}
			if fire.GroupsTransited > 1 {
				insights = append(insights, fmt.Sprintf(
					"Total: %d fire groups transited through %s without being stopped.",
					fire.GroupsTransited, parkName))
			}
		}
		if fire.GroupsStoppedInside > 0 {
			// Find example of stopped group
			for _, t := range fire.Trajectories {
				if t.Outcome == "STOPPED_INSIDE" {
					insights = append(insights, fmt.Sprintf(
						"✓ Fire group stopped: entered from (%.2f°, %.2f°), burned for %d days (%d fires), then stopped - possible ranger contact.",
						t.Origin.Lat, t.Origin.Lon, t.DaysInside, t.FiresInside))
					break
				}
			}
			if fire.GroupsStoppedInside > 1 {
				insights = append(insights, fmt.Sprintf(
					"👍 %d groups total stopped inside - %.0f%% response rate.",
					fire.GroupsStoppedInside, fire.ResponseRate))
			}
		}
		if fire.AvgDaysInside > 7 {
			insights = append(insights, fmt.Sprintf(
				"🔥 Fire groups burned inside the park for an average of %.1f days - indicating sustained presence.",
				fire.AvgDaysInside))
		}
		if fire.ResponseRate >= 70 {
			insights = append(insights, "👍 High response rate suggests effective ranger patrol coverage.")
		} else if fire.ResponseRate < 40 && fire.GroupsEntered >= 5 {
			insights = append(insights, "⚠️ Low response rate may indicate gaps in patrol coverage or resources.")
		}
	}
	
	// Get total fire count and peak month
	var totalFires int
	var peakMonth string
	err = s.DB.QueryRow(`
		SELECT COUNT(*) FROM fire_detections WHERE protected_area_id = ?
	`, internalID).Scan(&totalFires)
	if err == nil && stats.Fire != nil {
		stats.Fire.TotalFires = totalFires
	}
	
	// Find peak month
	err = s.DB.QueryRow(`
		SELECT strftime('%m', acq_date) as month, COUNT(*) as cnt
		FROM fire_detections 
		WHERE protected_area_id = ?
		GROUP BY month
		ORDER BY cnt DESC
		LIMIT 1
	`, internalID).Scan(&peakMonth, &totalFires)
	if err == nil && stats.Fire != nil {
		monthNames := map[string]string{
			"01": "January", "02": "February", "03": "March", "04": "April",
			"05": "May", "06": "June", "07": "July", "08": "August",
			"09": "September", "10": "October", "11": "November", "12": "December",
		}
		stats.Fire.PeakMonth = monthNames[peakMonth]
		if stats.Fire.PeakMonth != "" {
			insights = append(insights, fmt.Sprintf(
				"📅 Peak fire activity occurs in %s - consider increasing patrols during this period.",
				stats.Fire.PeakMonth))
		}
	}
	
	// Get fire timeline (last 90 days with data)
	rows, err := s.DB.Query(`
		SELECT acq_date, COUNT(*) as cnt
		FROM fire_detections 
		WHERE protected_area_id = ?
		GROUP BY acq_date
		ORDER BY acq_date DESC
		LIMIT 90
	`, internalID)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var day FireDayCount
			if rows.Scan(&day.Date, &day.Count) == nil {
				stats.FireTimeline = append(stats.FireTimeline, day)
			}
		}
	}
	
	// Get multi-year fire trend with total fires per year
	rows, err = s.DB.Query(`
		SELECT 
			pgi.year,
			pgi.total_groups,
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
	`, internalID)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var yr YearlyFireSummary
			if rows.Scan(&yr.Year, &yr.Groups, &yr.TotalFires) == nil {
				stats.FireTrend = append(stats.FireTrend, yr)
			}
		}
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
		if settlement.SettlementCount > 0 {
			insights = append(insights, fmt.Sprintf(
				"🏘️ %d settlements detected inside the park (%.2f km² built-up area).",
				settlement.SettlementCount, settlement.BuiltUpKm2))
		} else if settlement.BuiltUpKm2 == 0 {
			insights = append(insights, "✓ No permanent settlements detected inside park boundaries.")
		}
	}
	
	// Query OSM roadless data
	var roadless RoadlessStats
	err = s.DB.QueryRow(`
		SELECT roadless_percentage, road_length_km
		FROM osm_roadless_data
		WHERE park_id = ?
	`, internalID).Scan(&roadless.RoadlessPercentage, &roadless.TotalRoadKm)
	
	if err == nil {
		stats.Roadless = &roadless
		if roadless.RoadlessPercentage >= 90 {
			insights = append(insights, fmt.Sprintf(
				"🌲 %.0f%% roadless wilderness - exceptional intact habitat with minimal human access.",
				roadless.RoadlessPercentage))
		} else if roadless.RoadlessPercentage >= 70 {
			insights = append(insights, fmt.Sprintf(
				"🌲 %.0f%% roadless wilderness - good habitat connectivity.",
				roadless.RoadlessPercentage))
		} else if roadless.RoadlessPercentage < 50 {
			insights = append(insights, fmt.Sprintf(
				"⚠️ Only %.0f%% roadless - significant road network may fragment habitat.",
				roadless.RoadlessPercentage))
		}
	}
	
	// Query deforestation data
	var deforestation DeforestationStats
	rows, err = s.DB.Query(`
		SELECT year, area_km2
		FROM deforestation_events
		WHERE park_id = ?
		ORDER BY year
	`, internalID)
	if err == nil {
		defer rows.Close()
		var totalLoss float64
		var worstYear int
		var worstYearKm2 float64
		var yearlyData []YearlyDeforestation
		var recentYearsTotal float64
		var olderYearsTotal float64
		var recentYearsCount int
		var olderYearsCount int
		
		for rows.Next() {
			var year int
			var areaKm2 float64
			if rows.Scan(&year, &areaKm2) == nil {
				totalLoss += areaKm2
				yearlyData = append(yearlyData, YearlyDeforestation{Year: year, LossKm2: areaKm2})
				if areaKm2 > worstYearKm2 {
					worstYear = year
					worstYearKm2 = areaKm2
				}
				// Calculate trend: compare last 5 years vs previous 5 years
				if year >= 2020 {
					recentYearsTotal += areaKm2
					recentYearsCount++
				} else if year >= 2015 {
					olderYearsTotal += areaKm2
					olderYearsCount++
				}
			}
		}
		
		if len(yearlyData) > 0 {
			deforestation.TotalLossKm2 = totalLoss
			deforestation.WorstYear = worstYear
			deforestation.WorstYearKm2 = worstYearKm2
			deforestation.YearlyData = yearlyData
			
			// Calculate trend based on average loss per year
			if recentYearsCount > 0 && olderYearsCount > 0 {
				recentAvg := recentYearsTotal / float64(recentYearsCount)
				olderAvg := olderYearsTotal / float64(olderYearsCount)
				if recentAvg > olderAvg*1.2 {
					deforestation.Trend = "worsening"
				} else if recentAvg < olderAvg*0.8 {
					deforestation.Trend = "improving"
				} else {
					deforestation.Trend = "stable"
				}
			} else {
				deforestation.Trend = "insufficient_data"
			}
			
			stats.Deforestation = &deforestation
			
			// Generate deforestation insights
			insights = append(insights, fmt.Sprintf(
				"🌳 Total forest loss: %.1f km² since 2001. Worst year was %d (%.1f km²).",
				totalLoss, worstYear, worstYearKm2))
			
			if deforestation.Trend == "worsening" {
				insights = append(insights, "⚠️ Deforestation trend is worsening - recent years show higher loss than 2015-2019.")
			} else if deforestation.Trend == "improving" {
				insights = append(insights, "✓ Deforestation trend is improving - recent years show lower loss than 2015-2019.")
			}
		}
	}
	
	stats.Insights = insights
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(stats)
}

// HandleAPIParkFireLog returns detailed fire event log for a park
// GET /api/parks/{id}/fire-log
func (s *Server) HandleAPIParkFireLog(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}
	
	// Map to internal ID
	internalID := parkID
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.WDPAID == parkID || area.ID == parkID {
				internalID = area.ID
				break
			}
		}
	}
	
	type FireEvent struct {
		Date      string  `json:"date"`
		Fires     int     `json:"fires"`
		AvgFRP    float64 `json:"avg_frp"`
		MaxFRP    float64 `json:"max_frp"`
	}
	
	rows, err := s.DB.Query(`
		SELECT acq_date, COUNT(*) as fires, AVG(frp) as avg_frp, MAX(frp) as max_frp
		FROM fire_detections 
		WHERE protected_area_id = ?
		GROUP BY acq_date
		ORDER BY acq_date DESC
		LIMIT 365
	`, internalID)
	
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	
	var events []FireEvent
	for rows.Next() {
		var e FireEvent
		var avgFRP, maxFRP sql.NullFloat64
		if err := rows.Scan(&e.Date, &e.Fires, &avgFRP, &maxFRP); err == nil {
			e.AvgFRP = avgFRP.Float64
			e.MaxFRP = maxFRP.Float64
			events = append(events, e)
		}
	}
	
	// Generate narrative log entries
	type LogEntry struct {
		Date    string `json:"date"`
		Message string `json:"message"`
		Level   string `json:"level"` // info, warning, critical
	}
	
	var log []LogEntry
	for _, e := range events {
		level := "info"
		var msg string
		
		if e.Fires >= 100 {
			level = "critical"
			msg = fmt.Sprintf("🔥 Major fire event: %d active fires detected (avg intensity: %.1f MW)", e.Fires, e.AvgFRP)
		} else if e.Fires >= 50 {
			level = "warning"
			msg = fmt.Sprintf("⚠️ Elevated fire activity: %d fires detected", e.Fires)
		} else if e.Fires >= 20 {
			msg = fmt.Sprintf("Fire activity: %d detections", e.Fires)
		} else if e.MaxFRP > 50 {
			level = "warning"
			msg = fmt.Sprintf("High-intensity fire detected (%.0f MW peak)", e.MaxFRP)
		} else {
			msg = fmt.Sprintf("%d fire detections", e.Fires)
		}
		
		// Format date nicely
		dateParts := strings.Split(e.Date, "-")
		if len(dateParts) == 3 {
			months := []string{"", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"}
			monthNum := 0
			fmt.Sscanf(dateParts[1], "%d", &monthNum)
			if monthNum > 0 && monthNum <= 12 {
				e.Date = fmt.Sprintf("%s %s, %s", months[monthNum], dateParts[2], dateParts[0])
			}
		}
		
		log = append(log, LogEntry{Date: e.Date, Message: msg, Level: level})
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"events": events,
		"log":    log,
	})
}
