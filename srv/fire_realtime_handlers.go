package srv

import (
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
	Name         string                   `json:"name"`
	Type         string                   `json:"type"`
	IsActive     bool                     `json:"is_active"`
	IsInside     bool                     `json:"is_inside"`
	Status       string                   `json:"status"`
	LastSeen     string                   `json:"last_seen"`
	DaysSince    int                      `json:"days_since_last"`
	DaysInside   int                      `json:"days_inside"`
	Metrics      map[string]interface{}   `json:"metrics"`
	Trajectory   []FireCluster            `json:"trajectory"`
	PointsInside []map[string]interface{} `json:"points_inside,omitempty"`
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

	// Expand bbox by 300km buffer for trajectory detection
	bufferDeg := 300.0 / 111.0
	if minLat == 0 && maxLat == 0 {
		// Fallback: use fires from DB to determine bbox
		s.DB.QueryRow(`
			SELECT MIN(latitude), MAX(latitude), MIN(longitude), MAX(longitude)
			FROM fire_detections WHERE protected_area_id = ?
		`, internalID).Scan(&minLat, &maxLat, &minLon, &maxLon)
		if minLat == 0 && maxLat == 0 {
			// No data at all
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(FireRealtimeResponse{
				ParkID:    internalID,
				ParkName:  parkName,
				Narrative: fmt.Sprintf("No fire data available for %s.", parkName),
			})
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

	epsDeg := 15.0 / 111.0 // 15 km
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
	maxLinkKm := 25.0
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
