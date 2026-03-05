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

// NATO phonetic alphabet for anonymous group naming
var groupNames = []string{
	"Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
	"India", "Juliet", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
	"Quebec", "Romeo", "Sierra", "Tango", "Uniform", "Victor", "Whiskey",
	"Xray", "Yankee", "Zulu",
}

func getGroupName(index int) string {
	if index < len(groupNames) {
		return groupNames[index]
	}
	cycle := index/len(groupNames) + 1
	baseIdx := index % len(groupNames)
	return fmt.Sprintf("%s-%d", groupNames[baseIdx], cycle)
}

// analyzeFireStatus determines status, emoji, detail, and priority for a fire group
func analyzeFireStatus(position string, direction string, speedKmDay, pctInside float64, daysSince, days int) (status, emoji, detail string, priority int) {
	// Gone dark detection
	if daysSince >= 3 {
		return "Gone dark", "🌙", "No detections for 3+ days", 60 // Low priority - investigate
	}
	if daysSince >= 2 {
		return "Cooling", "❄️", "No new fires in 2 days", 70 // Low priority
	}
	
	// Position-based status
	if position == "contained" {
		emoji = "📍"
		status = "Contained"
		detail = "Fully inside park"
		priority = 50 // Medium priority
	} else if position == "entirely_outside" {
		if pctInside == 0 && speedKmDay > 0 {
			emoji = "⚠️"
			status = "Approaching"
			detail = fmt.Sprintf("Outside, moving %s", direction)
			if speedKmDay > 2 {
				priority = 10 // HIGHEST - fast approaching
				detail += fmt.Sprintf(" at %.1fkm/day (fast)", speedKmDay)
			} else if speedKmDay > 0.5 {
				priority = 20 // High - approaching
				detail += fmt.Sprintf(" at %.1fkm/day", speedKmDay)
			} else {
				priority = 30
				detail += " (slow spread)"
			}
		} else {
			emoji = "🔥"
			status = "Outside"
			detail = "Outside park boundary"
			priority = 80 // Low priority
		}
	} else if position == "starts_inside" {
		emoji = "🚨"
		status = "Leaving"
		detail = fmt.Sprintf("Started inside, moving %s toward boundary", direction)
		priority = 55 // Medium-low
	} else if position == "ends_inside" {
		emoji = "⚡"
		status = "Entering"
		detail = fmt.Sprintf("Crossing into park from %s", direction)
		priority = 5 // CRITICAL - just entered
	} else if position == "transits" {
		emoji = "🌊"
		status = "Transiting"
		detail = fmt.Sprintf("Crossing park boundary, moving %s", direction)
		priority = 15 // High priority
	} else {
		emoji = "🔥"
		status = "Active"
		detail = fmt.Sprintf("Moving %s", direction)
		if pctInside > 50 {
			priority = 40 // Inside and spreading
		} else {
			priority = 60
		}
	}
	
	// Add velocity info if not already included
	if speedKmDay > 2 && status != "Approaching" {
		detail += fmt.Sprintf(" at %.1fkm/day (fast)", speedKmDay)
		priority -= 10 // Boost priority for fast-moving fires
	} else if speedKmDay > 0.5 && status != "Approaching" {
		detail += fmt.Sprintf(" at %.1fkm/day", speedKmDay)
	} else if speedKmDay > 0 && status != "Approaching" {
		detail += " (slow spread)"
	} else if days == 1 && speedKmDay == 0 {
		detail += " (new detection)"
	} else if speedKmDay == 0 && days > 1 {
		detail += " (stationary)"
	}
	
	return status, emoji, detail, priority
}

// firePoint represents a single fire detection
type firePoint struct {
	lat, lon, frp float64
	date, time    string
}

// FireCluster represents a daily fire cluster
type FireCluster struct {
	Date     string  `json:"date"`
	Lat      float64 `json:"lat"`
	Lon      float64 `json:"lon"`
	LatMin   float64 `json:"lat_min"`
	LatMax   float64 `json:"lat_max"`
	LonMin   float64 `json:"lon_min"`
	LonMax   float64 `json:"lon_max"`
	Fires    int     `json:"fires"`
	FRP      float64 `json:"frp"`
	SpreadKm float64 `json:"spread_km"`
}

// FireGroup represents a tracked fire group
type FireGroup struct {
	Name          string                   `json:"name"`
	FeatureID     string                   `json:"feature_id"`
	Type          string                   `json:"type"`
	IsActive      bool                     `json:"is_active"`
	IsInside      bool                     `json:"is_inside"`
	Status        string                   `json:"status"`
	StatusEmoji   string                   `json:"status_emoji"`
	StatusDetail  string                   `json:"status_detail"`
	Priority      int                      `json:"priority"`
	LastSeen      string                   `json:"last_seen"`
	DaysSince     int                      `json:"days_since_last"`
	DaysInside    int                      `json:"days_inside"`
	Metrics       map[string]interface{}   `json:"metrics"`
	Trajectory    []FireCluster            `json:"trajectory"`
	PointsInside  []map[string]interface{} `json:"points_inside,omitempty"`
}

// FireRealtimeResponse is the API response for real-time fire analysis
type FireRealtimeResponse struct {
	ParkID            string      `json:"park_id"`
	ParkName          string      `json:"park_name"`
	AnalysisPeriod    string      `json:"analysis_period"`
	TotalFires        int         `json:"total_fires"`
	TotalGroups       int         `json:"total_groups"`
	ActiveGroupsCount int         `json:"active_groups_count"`
	GroupsInsideCount int         `json:"groups_inside_count"`
	Groups            []FireGroup `json:"groups"`
	ActiveGroups      []FireGroup `json:"active_groups"`
	GroupsInside      []FireGroup `json:"groups_inside"`
	Narrative         string      `json:"narrative"`
}

// distanceKm calculates distance between two points in km
func distanceKmRT(lat1, lon1, lat2, lon2 float64) float64 {
	latDiff := math.Abs(lat2-lat1) * 111.0
	lonDiff := math.Abs(lon2-lon1) * 111.0 * math.Cos(math.Pi*(lat1+lat2)/360.0)
	return math.Sqrt(latDiff*latDiff + lonDiff*lonDiff)
}

// bearingDeg calculates bearing from point 1 to point 2
func bearingDegRT(lat1, lon1, lat2, lon2 float64) float64 {
	lat1R := lat1 * math.Pi / 180.0
	lat2R := lat2 * math.Pi / 180.0
	dlon := (lon2 - lon1) * math.Pi / 180.0

	x := math.Sin(dlon) * math.Cos(lat2R)
	y := math.Cos(lat1R)*math.Sin(lat2R) - math.Sin(lat1R)*math.Cos(lat2R)*math.Cos(dlon)

	bearing := math.Atan2(x, y) * 180.0 / math.Pi
	return math.Mod(bearing+360.0, 360.0)
}

// bearingToDirection converts bearing to compass direction
func bearingToDirectionRT(bearing float64) string {
	directions := []string{"N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
		"S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"}
	idx := int(math.Mod(bearing+11.25, 360.0) / 22.5)
	if idx < 0 {
		idx = 0
	}
	if idx >= len(directions) {
		idx = len(directions) - 1
	}
	return directions[idx]
}

// HandleAPIFireRealtime returns real-time fire trajectory analysis
// GET /api/parks/{id}/fire-realtime
func (s *Server) HandleAPIFireRealtime(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}

	// Map to internal ID
	internalID := parkID
	parkName := parkID
	var minLat, maxLat, minLon, maxLon float64

	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.WDPAID == parkID || area.ID == parkID {
				internalID = area.ID
				parkName = area.Name
				// Get bbox from area if available
				if len(area.Geometry.Coordinates) > 0 {
					var coords [][][][]float64 // MultiPolygon
					if err := json.Unmarshal(area.Geometry.Coordinates, &coords); err == nil {
						minLat, maxLat = 90.0, -90.0
						minLon, maxLon = 180.0, -180.0
						for _, polygon := range coords {
							for _, ring := range polygon {
								for _, coord := range ring {
									if len(coord) >= 2 {
										lon, lat := coord[0], coord[1]
										if lat < minLat {
											minLat = lat
										}
										if lat > maxLat {
											maxLat = lat
										}
										if lon < minLon {
											minLon = lon
										}
										if lon > maxLon {
											maxLon = lon
										}
									}
								}
							}
						}
					}
				}
				break
			}
		}
	}

	// Get analysis window (default 28 days)
	daysStr := r.URL.Query().Get("days")
	days := 28
	if daysStr != "" {
		if d, err := strconv.Atoi(daysStr); err == nil && d > 0 && d <= 90 {
			days = d
		}
	}

	endDate := time.Now()
	startDate := endDate.AddDate(0, 0, -days)

	// Always use feature_geometries (v5 trajectory data from pipeline)
	// This ensures consistency between realtime and historical views
	s.handleFireRealtimeFromFeatures(w, r, internalID, parkName, startDate, endDate, days)
	return

	// Legacy code below - kept for reference but not used
	var fireCount int
	s.DB.QueryRow(`SELECT COUNT(*) FROM fire_detections WHERE acq_date >= ?`, startDate.Format("2006-01-02")).Scan(&fireCount)
	_ = fireCount // unused
	
	// Expand bbox by 300km buffer for trajectory detection
	bufferDeg := 300.0 / 111.0
	if minLat == 0 && maxLat == 0 {
		// Fallback: use fires from DB to determine bbox
		s.DB.QueryRow(`
			SELECT MIN(latitude), MAX(latitude), MIN(longitude), MAX(longitude)
			FROM fire_detections WHERE protected_area_id = ?
		`, internalID).Scan(&minLat, &maxLat, &minLon, &maxLon)
		if minLat == 0 && maxLat == 0 {
			// No data at all - use feature_geometries
			s.handleFireRealtimeFromFeatures(w, r, internalID, parkName, startDate, endDate, days)
			return
		}
	}

	queryMinLat := minLat - bufferDeg
	queryMaxLat := maxLat + bufferDeg
	queryMinLon := minLon - bufferDeg
	queryMaxLon := maxLon + bufferDeg

	// Get fire detections
	rows, err := s.DB.Query(`
		SELECT latitude, longitude, acq_date, acq_time, COALESCE(frp, 0) as frp
		FROM fire_detections
		WHERE latitude BETWEEN ? AND ?
		  AND longitude BETWEEN ? AND ?
		  AND acq_date BETWEEN ? AND ?
		ORDER BY acq_date, acq_time
	`, queryMinLat, queryMaxLat, queryMinLon, queryMaxLon,
		startDate.Format("2006-01-02"), endDate.Format("2006-01-02"))

	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	// Collect fires
	var fires []firePoint
	for rows.Next() {
		var f firePoint
		rows.Scan(&f.lat, &f.lon, &f.date, &f.time, &f.frp)
		fires = append(fires, f)
	}

	if len(fires) < 10 {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(FireRealtimeResponse{
			ParkID:         internalID,
			ParkName:       parkName,
			AnalysisPeriod: fmt.Sprintf("%s to %s", startDate.Format("2006-01-02"), endDate.Format("2006-01-02")),
			TotalFires:     len(fires),
			Narrative:      fmt.Sprintf("Minimal fire activity detected near %s in the past %d days.", parkName, days),
		})
		return
	}

	// Detect daily clusters
	dailyClusters := detectDailyClustersRT(fires)

	// Track trajectories
	trajectories := trackClustersRT(dailyClusters)

	// Analyze each trajectory
	today := endDate.Format("2006-01-02")
	var groups []FireGroup

	for i, traj := range trajectories {
		if len(traj) < 3 {
			continue
		}

		group := analyzeTrajectoryRT(traj, i, minLat, maxLat, minLon, maxLon, today)
		groups = append(groups, group)
	}

	// Sort by activity and inside status
	sort.Slice(groups, func(i, j int) bool {
		if groups[i].IsActive != groups[j].IsActive {
			return groups[i].IsActive
		}
		if groups[i].IsInside != groups[j].IsInside {
			return groups[i].IsInside
		}
		return groups[i].DaysInside > groups[j].DaysInside
	})

	// Filter active groups
	var activeGroups, groupsInside []FireGroup
	for _, g := range groups {
		if g.IsActive {
			activeGroups = append(activeGroups, g)
			if g.IsInside {
				groupsInside = append(groupsInside, g)
			}
		}
	}

	// Generate narrative
	narrative := generateFireNarrativeRT(parkName, groups, activeGroups, groupsInside, days)

	response := FireRealtimeResponse{
		ParkID:            internalID,
		ParkName:          parkName,
		AnalysisPeriod:    fmt.Sprintf("%s to %s", startDate.Format("2006-01-02"), endDate.Format("2006-01-02")),
		TotalFires:        len(fires),
		TotalGroups:       len(groups),
		ActiveGroupsCount: len(activeGroups),
		GroupsInsideCount: len(groupsInside),
		Groups:            groups,
		ActiveGroups:      activeGroups,
		GroupsInside:      groupsInside,
		Narrative:         narrative,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func detectDailyClustersRT(fires []firePoint) map[string][]FireCluster {
	// Group fires by date
	byDate := make(map[string][]firePoint)
	for _, f := range fires {
		byDate[f.date] = append(byDate[f.date], f)
	}

	epsDeg := 30.0 / 111.0 // 30 km clustering radius
	minFires := 5

	dailyClusters := make(map[string][]FireCluster)

	for date, dayFires := range byDate {
		if len(dayFires) < minFires {
			continue
		}

		// Simple grid-based clustering
		type cellKey struct{ latCell, lonCell int }
		grid := make(map[cellKey][]firePoint)
		for _, f := range dayFires {
			cell := cellKey{int(f.lat / epsDeg), int(f.lon / epsDeg)}
			grid[cell] = append(grid[cell], f)
		}

		var clusters []FireCluster
		for cell, cellFires := range grid {
			if len(cellFires) < minFires/2 {
				continue
			}

			// Merge adjacent cells
			merged := make([]firePoint, len(cellFires))
			copy(merged, cellFires)
			for dx := -1; dx <= 1; dx++ {
				for dy := -1; dy <= 1; dy++ {
					if dx == 0 && dy == 0 {
						continue
					}
					adj := cellKey{cell.latCell + dx, cell.lonCell + dy}
					if adjFires, ok := grid[adj]; ok {
						merged = append(merged, adjFires...)
					}
				}
			}

			if len(merged) >= minFires {
				var sumLat, sumLon, sumFRP float64
				minLatC, maxLatC := 90.0, -90.0
				minLonC, maxLonC := 180.0, -180.0
				for _, f := range merged {
					sumLat += f.lat
					sumLon += f.lon
					sumFRP += f.frp
					if f.lat < minLatC {
						minLatC = f.lat
					}
					if f.lat > maxLatC {
						maxLatC = f.lat
					}
					if f.lon < minLonC {
						minLonC = f.lon
					}
					if f.lon > maxLonC {
						maxLonC = f.lon
					}
				}
				avgLat := sumLat / float64(len(merged))
				avgLon := sumLon / float64(len(merged))

				spreadKm := math.Max(
					(maxLatC-minLatC)*111.0,
					(maxLonC-minLonC)*111.0*math.Cos(avgLat*math.Pi/180.0),
				)

				clusters = append(clusters, FireCluster{
					Date:     date,
					Lat:      avgLat,
					Lon:      avgLon,
					LatMin:   minLatC,
					LatMax:   maxLatC,
					LonMin:   minLonC,
					LonMax:   maxLonC,
					Fires:    len(merged),
					FRP:      sumFRP,
					SpreadKm: spreadKm,
				})
			}
		}

		if len(clusters) > 0 {
			dailyClusters[date] = clusters
		}
	}

	return dailyClusters
}

func trackClustersRT(dailyClusters map[string][]FireCluster) [][]FireCluster {
	maxLinkKm := 50.0 // 50 km max link between days (2x clustering radius)
	maxGapDays := 3

	var sortedDates []string
	for date := range dailyClusters {
		sortedDates = append(sortedDates, date)
	}
	sort.Strings(sortedDates)

	if len(sortedDates) == 0 {
		return nil
	}

	used := make(map[string]bool)
	var trajectories [][]FireCluster

	for startIdx, startDate := range sortedDates {
		for _, cluster := range dailyClusters[startDate] {
			key := fmt.Sprintf("%s_%f_%f", startDate, cluster.Lat, cluster.Lon)
			if used[key] {
				continue
			}

			traj := []FireCluster{cluster}
			used[key] = true
			current := cluster

			for nextIdx := startIdx + 1; nextIdx < len(sortedDates); nextIdx++ {
				nextDate := sortedDates[nextIdx]

				// Calculate date gap
				d1, _ := time.Parse("2006-01-02", current.Date)
				d2, _ := time.Parse("2006-01-02", nextDate)
				dateGap := int(d2.Sub(d1).Hours() / 24)

				if dateGap > maxGapDays {
					break
				}

				var best *FireCluster
				bestDist := maxLinkKm + 1

				for i := range dailyClusters[nextDate] {
					nc := &dailyClusters[nextDate][i]
					nkey := fmt.Sprintf("%s_%f_%f", nextDate, nc.Lat, nc.Lon)
					if used[nkey] {
						continue
					}

					dist := distanceKmRT(current.Lat, current.Lon, nc.Lat, nc.Lon)
					if dist <= maxLinkKm && dist < bestDist {
						best = nc
						bestDist = dist
					}
				}

				if best != nil {
					traj = append(traj, *best)
					used[fmt.Sprintf("%s_%f_%f", best.Date, best.Lat, best.Lon)] = true
					current = *best
				}
			}

			if len(traj) >= 3 {
				trajectories = append(trajectories, traj)
			}
		}
	}

	return trajectories
}

func analyzeTrajectoryRT(traj []FireCluster, index int, minLat, maxLat, minLon, maxLon float64, today string) FireGroup {
	name := getGroupName(index)

	start, end := traj[0], traj[len(traj)-1]

	// Calculate metrics
	totalFires := 0
	for _, c := range traj {
		totalFires += c.Fires
	}

	days := len(traj)
	netSouth := (start.Lat - end.Lat) * 111.0
	netEast := (end.Lon - start.Lon) * 111.0
	totalDist := distanceKmRT(start.Lat, start.Lon, end.Lat, end.Lon)

	var movements []float64
	for i := 1; i < len(traj); i++ {
		d := distanceKmRT(traj[i-1].Lat, traj[i-1].Lon, traj[i].Lat, traj[i].Lon)
		movements = append(movements, d)
	}

	avgSpeed := 0.0
	maxSpeed := 0.0
	if len(movements) > 0 {
		sum := 0.0
		for _, m := range movements {
			sum += m
			if m > maxSpeed {
				maxSpeed = m
			}
		}
		avgSpeed = sum / float64(len(movements))
	}

	avgSpread := 0.0
	for _, c := range traj {
		avgSpread += c.SpreadKm
	}
	avgSpread /= float64(len(traj))

	brg := bearingDegRT(start.Lat, start.Lon, end.Lat, end.Lon)
	direction := bearingToDirectionRT(brg)

	// Classify type
	var groupType string
	if avgSpeed > 30 {
		groupType = "management_fast"
	} else if avgSpeed > 15 {
		if avgSpread > 30 {
			groupType = "management_vehicle"
		} else {
			groupType = "herder_fast"
		}
	} else if avgSpeed > 5 {
		if netSouth > 20 {
			groupType = "transhumance"
		} else {
			groupType = "herder_local"
		}
	} else if avgSpeed > 2 {
		if days > 10 && netSouth > 15 {
			groupType = "transhumance_slow"
		} else {
			groupType = "local_burning"
		}
	} else {
		if days > 7 {
			groupType = "village_persistent"
		} else {
			groupType = "local_stationary"
		}
	}

	// Check points inside park
	var pointsInside []map[string]interface{}
	for _, pt := range traj {
		if pt.Lat >= minLat && pt.Lat <= maxLat && pt.Lon >= minLon && pt.Lon <= maxLon {
			pointsInside = append(pointsInside, map[string]interface{}{
				"date":  pt.Date,
				"lat":   pt.Lat,
				"lon":   pt.Lon,
				"fires": pt.Fires,
			})
		}
	}

	// Determine status
	lastDate, _ := time.Parse("2006-01-02", end.Date)
	todayDate, _ := time.Parse("2006-01-02", today)
	daysSince := int(todayDate.Sub(lastDate).Hours() / 24)

	isActive := daysSince <= 3
	isInside := false
	status := "OUTSIDE"

	if len(pointsInside) > 0 {
		lastInside := pointsInside[len(pointsInside)-1]
		if lastInside["date"] == end.Date {
			isInside = true
			if isActive {
				status = "ACTIVE_INSIDE"
			} else {
				status = "STOPPED_INSIDE"
			}
		} else {
			status = "EXITED"
		}
	}

	return FireGroup{
		Name:       name,
		Type:       groupType,
		IsActive:   isActive,
		IsInside:   isInside,
		Status:     status,
		LastSeen:   end.Date,
		DaysSince:  daysSince,
		DaysInside: len(pointsInside),
		Metrics: map[string]interface{}{
			"days":              days,
			"fires":             totalFires,
			"net_south_km":      math.Round(netSouth*10) / 10,
			"net_east_km":       math.Round(netEast*10) / 10,
			"total_distance_km": math.Round(totalDist*10) / 10,
			"avg_speed_km_day":  math.Round(avgSpeed*10) / 10,
			"max_speed_km_day":  math.Round(maxSpeed*10) / 10,
			"avg_spread_km":     math.Round(avgSpread*10) / 10,
			"bearing":           math.Round(brg*10) / 10,
			"direction":         direction,
			"start_date":        start.Date,
			"end_date":          end.Date,
			"start_lat":         math.Round(start.Lat*10000) / 10000,
			"start_lon":         math.Round(start.Lon*10000) / 10000,
			"end_lat":           math.Round(end.Lat*10000) / 10000,
			"end_lon":           math.Round(end.Lon*10000) / 10000,
		},
		Trajectory:   traj,
		PointsInside: pointsInside,
	}
}

func generateFireNarrativeRT(parkName string, allGroups, activeGroups, groupsInside []FireGroup, days int) string {
	var parts []string

	if len(allGroups) == 0 {
		return fmt.Sprintf("No significant fire group activity detected near %s over the past %d days.", parkName, days)
	}

	// Summary
	parts = append(parts, fmt.Sprintf("Over the past %d days, %d distinct fire groups were tracked near %s.", days, len(allGroups), parkName))

	// Active groups inside - PRIORITY
	if len(groupsInside) > 0 {
		parts = append(parts, "")
		parts = append(parts, "⚠️ **ACTIVE INCURSIONS:**")
		for _, g := range groupsInside {
			metrics := g.Metrics
			direction, _ := metrics["direction"].(string)
			speed, _ := metrics["avg_speed_km_day"].(float64)

			freshness := "TODAY"
			if g.DaysSince > 0 {
				if g.DaysSince == 1 {
					freshness = "1 day ago"
				} else {
					freshness = fmt.Sprintf("%d days ago", g.DaysSince)
				}
			}

			lastFires := 0
			if len(g.PointsInside) > 0 {
				if f, ok := g.PointsInside[len(g.PointsInside)-1]["fires"].(int); ok {
					lastFires = f
				}
			}

			trajDays, _ := metrics["days"].(int)

			parts = append(parts, fmt.Sprintf("• **Group %s**: Active inside park, last detected %s. Tracked for %d days moving %s at ~%.1f km/day. Inside park for %d days with %d fires at last detection.",
				g.Name, freshness, trajDays, direction, speed, g.DaysInside, lastFires))
		}
	}

	// Other active groups (approaching or nearby)
	var approaching []FireGroup
	for _, g := range activeGroups {
		if !g.IsInside {
			approaching = append(approaching, g)
		}
	}

	if len(approaching) > 0 {
		parts = append(parts, "")
		parts = append(parts, "**Nearby Active Groups:**")
		limit := 5
		if len(approaching) < limit {
			limit = len(approaching)
		}
		for _, g := range approaching[:limit] {
			metrics := g.Metrics
			direction, _ := metrics["direction"].(string)
			speed, _ := metrics["avg_speed_km_day"].(float64)
			trajDays, _ := metrics["days"].(int)

			parts = append(parts, fmt.Sprintf("• **Group %s**: Moving %s, %d days tracked, ~%.1f km/day. Last seen %d day(s) ago.",
				g.Name, direction, trajDays, speed, g.DaysSince))
		}
	}

	// Groups that transited
	var transited []FireGroup
	for _, g := range allGroups {
		if g.Status == "EXITED" && g.DaysInside > 0 {
			transited = append(transited, g)
		}
	}

	if len(transited) > 0 {
		parts = append(parts, "")
		parts = append(parts, fmt.Sprintf("**Recent Transits:** %d groups passed through the park this period.", len(transited)))
		limit := 3
		if len(transited) < limit {
			limit = len(transited)
		}
		for _, g := range transited[:limit] {
			parts = append(parts, fmt.Sprintf("• Group %s: Spent %d days inside before exiting.", g.Name, g.DaysInside))
		}
	}

	// Groups that stopped inside
	var stoppedInside []FireGroup
	for _, g := range allGroups {
		if g.Status == "STOPPED_INSIDE" && !g.IsActive {
			stoppedInside = append(stoppedInside, g)
		}
	}

	if len(stoppedInside) > 0 {
		parts = append(parts, "")
		parts = append(parts, fmt.Sprintf("**Potential Staff Contact:** %d groups stopped burning inside the park (possible ranger intervention or end of activity).", len(stoppedInside)))
	}

	return strings.Join(parts, "\n")
}

// FireGroupAlert represents an alert for fire group activity in a park.
type FireGroupAlert struct {
	ID                int64      `json:"id"`
	ParkID            string     `json:"park_id"`
	ParkName          string     `json:"park_name,omitempty"`
	GroupName         string     `json:"group_name"`
	AlertType         string     `json:"alert_type"`
	FirstDetectedAt   time.Time  `json:"first_detected_at"`
	LastUpdatedAt     time.Time  `json:"last_updated_at"`
	LeftAt            *time.Time `json:"left_at,omitempty"`
	FireCount         int        `json:"fire_count"`
	DaysActive        int        `json:"days_active"`
	CentroidLat       *float64   `json:"centroid_lat,omitempty"`
	CentroidLon       *float64   `json:"centroid_lon,omitempty"`
	LatestLat         *float64   `json:"latest_lat,omitempty"`
	LatestLon         *float64   `json:"latest_lon,omitempty"`
	MovementDirection string     `json:"movement_direction,omitempty"`
	Message           string     `json:"message,omitempty"`
}

// HandleAPIFireAlerts returns recent fire group alerts for notifications.
func (s *Server) HandleAPIFireAlerts(w http.ResponseWriter, r *http.Request) {
	limitStr := r.URL.Query().Get("limit")
	limit := 20
	if limitStr != "" {
		if l, err := strconv.Atoi(limitStr); err == nil && l > 0 && l <= 50000 {
			limit = l
		}
	}

	query := `
		SELECT id, park_id, group_name, alert_type, first_detected_at, last_updated_at,
		       left_at, fire_count, days_active, centroid_lat, centroid_lon,
		       latest_lat, latest_lon, movement_direction
		FROM fire_group_alerts
		WHERE is_dismissed = 0
		  AND (left_at IS NULL OR left_at > datetime('now', '-1 day'))
		ORDER BY CASE alert_type WHEN 'entered' THEN 0 WHEN 'active_inside' THEN 1 WHEN 'active' THEN 2 ELSE 3 END,
		         CASE WHEN left_at IS NULL THEN 0 ELSE 1 END, last_updated_at DESC
		LIMIT ?
	`
	// Query fire alerts

	rows, err := s.DB.Query(query, limit)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	alerts := make([]FireGroupAlert, 0)
	rowCount := 0
	for rows.Next() {
		rowCount++
		var a FireGroupAlert
		var leftAt, direction sql.NullString
		var centroidLat, centroidLon, latestLat, latestLon sql.NullFloat64
		err := rows.Scan(&a.ID, &a.ParkID, &a.GroupName, &a.AlertType,
			&a.FirstDetectedAt, &a.LastUpdatedAt, &leftAt,
			&a.FireCount, &a.DaysActive, &centroidLat, &centroidLon,
			&latestLat, &latestLon, &direction)
		if err != nil {
			fmt.Printf("[Fire Alerts] Scan error: %v\n", err)
			continue
		}
		if leftAt.Valid {
			t, _ := time.Parse("2006-01-02 15:04:05", leftAt.String)
			a.LeftAt = &t
		}
		if direction.Valid {
			a.MovementDirection = direction.String
		}
		if centroidLat.Valid {
			a.CentroidLat = &centroidLat.Float64
		}
		if centroidLon.Valid {
			a.CentroidLon = &centroidLon.Float64
		}
		if latestLat.Valid {
			a.LatestLat = &latestLat.Float64
		}
		if latestLon.Valid {
			a.LatestLon = &latestLon.Float64
		}

		if s.AreaStore != nil {
			for _, area := range s.AreaStore.Areas {
				if area.ID == a.ParkID {
					a.ParkName = area.Name
					break
				}
			}
		}
		if a.ParkName == "" {
			a.ParkName = a.ParkID
		}

		switch a.AlertType {
		case "entered":
			a.Message = fmt.Sprintf("🔥 Fire group %s entered %s", a.GroupName, a.ParkName)
		case "active_inside":
			if a.DaysActive == 1 {
				a.Message = fmt.Sprintf("🔥 Fire group %s active in %s (%d fires)", a.GroupName, a.ParkName, a.FireCount)
			} else {
				a.Message = fmt.Sprintf("🔥 Fire group %s active in %s for %d days (%d fires)", a.GroupName, a.ParkName, a.DaysActive, a.FireCount)
			}
		case "left":
			a.Message = fmt.Sprintf("✓ Fire group %s left %s", a.GroupName, a.ParkName)
		default:
			a.Message = fmt.Sprintf("Fire group %s in %s", a.GroupName, a.ParkName)
		}
		alerts = append(alerts, a)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(alerts)
}

// UpdateFireGroupAlerts analyzes parks with recent fire activity and updates alerts.
func (s *Server) UpdateFireGroupAlerts() error {
	// Always use feature_geometries (v5 pipeline output) for consistency
	// This ensures alert group_names match the trajectory feature_ids
	return s.updateFireGroupAlertsFromFeatures()
}

// updateFireGroupAlertsFromFeatures creates alerts from feature_geometries
func (s *Server) updateFireGroupAlertsFromFeatures() error {
	now := time.Now()
	cutoff := now.AddDate(0, 0, -14).Format("2006-01-02") // 14 days for active alerts

	
	// Get recent active fire groups
	rows, err := s.DB.Query(`
		SELECT park_id, feature_id, properties_json, start_date, end_date
		FROM feature_geometries
		WHERE feature_type = 'fire_trajectory' AND end_date >= ?
		ORDER BY end_date DESC
	`, cutoff)
	if err != nil {

		return err
	}
	defer rows.Close()
	
	alertsByPark := make(map[string]int)
	
	for rows.Next() {
		var parkID, featureID, propsJSON string
		var startDate, endDate sql.NullString
		if rows.Scan(&parkID, &featureID, &propsJSON, &startDate, &endDate) != nil {
			continue
		}
		
		var props map[string]interface{}
		json.Unmarshal([]byte(propsJSON), &props)
		
		fires := 0
		if f, ok := props["fires_total"].(float64); ok {
			fires = int(f)
		}
		daysActive := 1
		if d, ok := props["days"].(float64); ok {
			daysActive = int(d)
		}
		// groupType unused for now - could add column to alerts table
		_ = props["group_type"]
		
		// Determine alert type
		alertType := "active"
		if endDate.Valid {
			if t, err := time.Parse("2006-01-02", endDate.String); err == nil {
				daysSince := int(time.Since(t).Hours() / 24)
				if daysSince > 3 {
					alertType = "cooling"
				}
			}
		}
		
		// Check if alert already exists
		var existingID int64
		s.DB.QueryRow(`SELECT id FROM fire_group_alerts WHERE park_id = ? AND group_name = ?`,
			parkID, featureID).Scan(&existingID)
		
		if existingID > 0 {
			// Update existing
			s.DB.Exec(`UPDATE fire_group_alerts SET 
				alert_type = ?, last_updated_at = ?, fire_count = ?, days_active = ?
				WHERE id = ?`,
				alertType, now, fires, daysActive, existingID)
		} else {
			// Insert new - use startDate if available, otherwise use now
			firstDetected := now.Format("2006-01-02 15:04:05")
			if startDate.Valid {
				firstDetected = startDate.String
			}
			_, err := s.DB.Exec(`INSERT INTO fire_group_alerts 
				(park_id, group_name, alert_type, fire_count, days_active, first_detected_at, last_updated_at)
				VALUES (?, ?, ?, ?, ?, ?, ?)`,
				parkID, featureID, alertType, fires, daysActive, firstDetected, now)
			if err != nil {
				// Log but continue
				fmt.Printf("Error inserting fire alert: %v\n", err)
			}
		}
		
		alertsByPark[parkID]++
	}
	
	totalAlerts := 0
	for _, count := range alertsByPark {
		totalAlerts += count
	}
	fmt.Printf("[Fire Alerts] Created/updated %d alerts across %d parks\n", totalAlerts, len(alertsByPark))
	
	// Clean up old alerts
	s.DB.Exec(`DELETE FROM fire_group_alerts WHERE left_at IS NOT NULL AND left_at < datetime('now', '-1 day')`)
	
	return nil
}

func (s *Server) updateParkFireAlerts(parkID string) {
	now := time.Now()
	startDate := now.AddDate(0, 0, -28)

	rows, err := s.DB.Query(`SELECT latitude, longitude, acq_date FROM fire_detections
		WHERE protected_area_id = ? AND acq_date >= ? ORDER BY acq_date`,
		parkID, startDate.Format("2006-01-02"))
	if err != nil {
		return
	}
	defer rows.Close()

	var fires []firePoint
	for rows.Next() {
		var f firePoint
		if rows.Scan(&f.lat, &f.lon, &f.date) == nil {
			fires = append(fires, f)
		}
	}

	if len(fires) < 5 {
		return
	}

	clusters := detectDailyClustersRT(fires)
	trajectories := trackClustersRT(clusters)

	var minLat, maxLat, minLon, maxLon float64 = -90, 90, -180, 180
	if s.AreaStore != nil {
		for i := range s.AreaStore.Areas {
			if s.AreaStore.Areas[i].ID == parkID {
				minLat, maxLat, minLon, maxLon = s.AreaStore.Areas[i].GetBoundingBox()
				break
			}
		}
	}

	existingRows, _ := s.DB.Query(`SELECT id, group_name FROM fire_group_alerts WHERE park_id = ? AND left_at IS NULL`, parkID)
	existingAlerts := make(map[string]int64)
	if existingRows != nil {
		for existingRows.Next() {
			var id int64
			var name string
			if existingRows.Scan(&id, &name) == nil {
				existingAlerts[name] = id
			}
		}
		existingRows.Close()
	}

	activeGroups := make(map[string]bool)

	for i, traj := range trajectories {
		if len(traj) < 2 {
			continue
		}

		name := getGroupName(i)
		last := traj[len(traj)-1]

		isInside := last.Lat >= minLat && last.Lat <= maxLat && last.Lon >= minLon && last.Lon <= maxLon
		lastDate, _ := time.Parse("2006-01-02", last.Date)
		isActive := time.Since(lastDate) < 72*time.Hour

		if !isInside || !isActive {
			continue
		}

		activeGroups[name] = true
		first := traj[0]

		var sumLat, sumLon float64
		var totalFires int
		for _, c := range traj {
			sumLat += c.Lat * float64(c.Fires)
			sumLon += c.Lon * float64(c.Fires)
			totalFires += c.Fires
		}
		centroidLat := sumLat / float64(totalFires)
		centroidLon := sumLon / float64(totalFires)

		netSouth := (first.Lat - last.Lat) * 111
		netEast := (last.Lon - first.Lon) * 111 * math.Cos(centroidLat*math.Pi/180)
		direction := "stationary"
		if math.Abs(netSouth) > 5 || math.Abs(netEast) > 5 {
			if math.Abs(netSouth) > math.Abs(netEast) {
				if netSouth > 0 {
					direction = "south"
				} else {
					direction = "north"
				}
			} else {
				if netEast > 0 {
					direction = "east"
				} else {
					direction = "west"
				}
			}
		}

		existingID, exists := existingAlerts[name]
		firstDate, _ := time.Parse("2006-01-02", first.Date)

		if !exists {
			s.DB.Exec(`INSERT INTO fire_group_alerts 
				(park_id, group_name, alert_type, first_detected_at, last_updated_at,
				 fire_count, days_active, centroid_lat, centroid_lon, latest_lat, latest_lon, movement_direction)
				VALUES (?, ?, 'entered', ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
				parkID, name, firstDate, now, totalFires, len(traj), centroidLat, centroidLon, last.Lat, last.Lon, direction)
		} else {
			alertType := "active_inside"
			if len(traj) <= 1 {
				alertType = "entered"
			}
			s.DB.Exec(`UPDATE fire_group_alerts SET alert_type = ?, last_updated_at = ?, fire_count = ?, days_active = ?,
				latest_lat = ?, latest_lon = ?, movement_direction = ? WHERE id = ?`,
				alertType, now, totalFires, len(traj), last.Lat, last.Lon, direction, existingID)
		}
	}

	for name, id := range existingAlerts {
		if !activeGroups[name] {
			s.DB.Exec(`UPDATE fire_group_alerts SET alert_type = 'left', left_at = ?, last_updated_at = ? WHERE id = ?`, now, now, id)
		}
	}
}

// HandleAPIUpdateFireAlerts is an admin endpoint to trigger alert updates.
func (s *Server) HandleAPIUpdateFireAlerts(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if err := s.UpdateFireGroupAlerts(); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

// handleFireRealtimeFromFeatures serves fire-realtime data from feature_geometries
// Used when fire_detections table is empty (pipeline-based approach)
func (s *Server) handleFireRealtimeFromFeatures(w http.ResponseWriter, r *http.Request, 
    parkID, parkName string, startDate, endDate time.Time, days int) {
    
    // Query recent fire trajectories from feature_geometries with persistent names
    rows, err := s.DB.Query(`
        SELECT fg.feature_id, fg.geojson, fg.properties_json, fg.start_date, fg.end_date,
               COALESCE(fgn.friendly_name, fg.feature_id) as display_name
        FROM feature_geometries fg
        LEFT JOIN fire_group_names fgn ON fg.park_id = fgn.park_id AND fg.feature_id = fgn.feature_id
        WHERE fg.park_id = ? AND fg.feature_type = 'fire_trajectory'
          AND fg.start_date >= ? 
        ORDER BY fg.start_date DESC
    `, parkID, startDate.Format("2006-01-02"))
    
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    defer rows.Close()
    
    var groups []FireGroup
    totalFires := 0
    activeCount := 0
    insideCount := 0
    
    groupIndex := 0
    for rows.Next() {
        var featureID, geojson, propsJSON, displayName string
        var startDateStr, endDateStr sql.NullString
        
        if err := rows.Scan(&featureID, &geojson, &propsJSON, &startDateStr, &endDateStr, &displayName); err != nil {
            continue
        }
        
        var props map[string]interface{}
        json.Unmarshal([]byte(propsJSON), &props)
        
        fires := int(props["fires_total"].(float64))
        totalFires += fires
        
        daysActive := int(props["days"].(float64))
        direction := ""
        if d, ok := props["direction"].(string); ok {
            direction = d
        }
        distKm := 0.0
        if d, ok := props["distance_km"].(float64); ok {
            distKm = d
        }
        avgSpeed := 0.0
        if d, ok := props["avg_speed_km_day"].(float64); ok {
            avgSpeed = d
        }
        groupType := "unknown"
        if t, ok := props["group_type"].(string); ok {
            groupType = t
        }
        narrative := ""
        if n, ok := props["narrative"].(string); ok {
            narrative = n
        }
        
        // Determine if active (last seen within 3 days)
        // Use fractional days to match notification API logic
        lastSeen := endDateStr.String
        daysSince := 0
        daysSinceFractional := 0.0
        if lastSeen != "" {
            if t, err := time.Parse("2006-01-02", lastSeen); err == nil {
                daysSinceFractional = time.Since(t).Hours() / 24.0
                daysSince = int(daysSinceFractional)
            }
        }
        isActive := daysSinceFractional <= 3.0
        if isActive {
            activeCount++
        }
        
        // Parse trajectory from geojson
        var geom struct {
            Coordinates [][]float64 `json:"coordinates"`
        }
        json.Unmarshal([]byte(geojson), &geom)
        
        var trajectory []FireCluster
        for i, coord := range geom.Coordinates {
            if len(coord) >= 2 {
                trajectory = append(trajectory, FireCluster{
                    Date:  startDateStr.String,
                    Lat:   coord[1],
                    Lon:   coord[0],
                    Fires: fires / max(1, len(geom.Coordinates)),
                })
                if i == 0 && startDateStr.Valid {
                    trajectory[i].Date = startDateStr.String
                }
            }
        }
        
        // Analyze fire status using enhanced logic
        position := ""
        if p, ok := props["position"].(string); ok {
            position = p
        }
        pctInside := 0.0
        if p, ok := props["pct_inside"].(float64); ok {
            pctInside = p
        }
        
        status, emoji, detail, priority := analyzeFireStatus(position, direction, avgSpeed, pctInside, daysSince, daysActive)
        
        group := FireGroup{
            Name:         displayName,  // Use persistent friendly name from fire_group_names
            FeatureID:    featureID,    // Include feature_id for stable identification
            Type:         groupType,
            IsActive:     isActive,
            IsInside:     true, // From feature_geometries, assume inside
            Status:       status,
            StatusEmoji:  emoji,
            StatusDetail: detail,
            Priority:     priority,
            LastSeen:     lastSeen,
            DaysSince:    daysSince,
            DaysInside:   daysActive,
            Metrics: map[string]interface{}{
                "fires":        fires,
                "days":         daysActive,
                "distance_km":  distKm,
                "avg_speed":    avgSpeed,
                "direction":    direction,
                "narrative":    narrative,
            },
            Trajectory: trajectory,
        }
        
        groups = append(groups, group)
        insideCount++
        groupIndex++
    }
    
    // Sort by priority (lowest number = highest priority), then by last seen
    sort.Slice(groups, func(i, j int) bool {
        if groups[i].Priority != groups[j].Priority {
            return groups[i].Priority < groups[j].Priority
        }
        return groups[i].LastSeen > groups[j].LastSeen
    })
    
    // Limit to reasonable number
    if len(groups) > 100 {
        groups = groups[:100]
    }
    
    // Build active and inside lists
    var activeGroups, groupsInside []FireGroup
    for _, g := range groups {
        if g.IsActive {
            activeGroups = append(activeGroups, g)
        }
        if g.IsInside {
            groupsInside = append(groupsInside, g)
        }
    }
    
    // Build narrative
    narrative := fmt.Sprintf("In the past %d days, %s has %d tracked fire groups with %d total fire detections.",
        days, parkName, len(groups), totalFires)
    if len(activeGroups) > 0 {
        narrative += fmt.Sprintf(" %d groups are currently active.", len(activeGroups))
    }
    
    resp := FireRealtimeResponse{
        ParkID:            parkID,
        ParkName:          parkName,
        AnalysisPeriod:    fmt.Sprintf("%s to %s", startDate.Format("2006-01-02"), endDate.Format("2006-01-02")),
        TotalFires:        totalFires,
        TotalGroups:       len(groups),
        ActiveGroupsCount: len(activeGroups),
        GroupsInsideCount: insideCount,
        Groups:            groups,
        ActiveGroups:      activeGroups,
        GroupsInside:      groupsInside,
        Narrative:         narrative,
    }
    
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(resp)
}
