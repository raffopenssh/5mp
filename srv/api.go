package srv

import (
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"srv.exe.dev/db/dbgen"
	"srv.exe.dev/srv/areas"
	"srv.exe.dev/srv/auth"
)

// GeoJSON types for API responses

// GeoJSONFeatureCollection represents a GeoJSON FeatureCollection.
type GeoJSONFeatureCollection struct {
	Type     string           `json:"type"`
	Features []GeoJSONFeature `json:"features"`
}

// GeoJSONFeature represents a single GeoJSON feature.
type GeoJSONFeature struct {
	Type       string                 `json:"type"`
	Geometry   GeoJSONGeometry        `json:"geometry"`
	Properties map[string]interface{} `json:"properties"`
}

// GeoJSONGeometry represents a GeoJSON geometry.
type GeoJSONGeometry struct {
	Type        string      `json:"type"`
	Coordinates interface{} `json:"coordinates"`
}

// buildMonthINClause creates SQL IN clause like "(11, 12, 1, 2, 3, 4)"
func buildMonthINClause(months []int) string {
	if len(months) == 0 {
		return "(0)" // No months - will match nothing
	}
	var parts []string
	for _, m := range months {
		parts = append(parts, fmt.Sprintf("%d", m))
	}
	return "(" + strings.Join(parts, ", ") + ")"
}

// parseSeasonMonths converts season string like "Nov-Apr" or "Jun-Sep" to month numbers
func parseSeasonMonths(season string) []int {
	if season == "" || season == "None" {
		return nil
	}
	
	monthNames := map[string]int{
		"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
		"Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
	}
	
	parts := strings.Split(season, "-")
	if len(parts) != 2 {
		return nil
	}
	
	startMonth, startOk := monthNames[parts[0]]
	endMonth, endOk := monthNames[parts[1]]
	
	if !startOk || !endOk {
		return nil
	}
	
	var months []int
	if startMonth <= endMonth {
		// Simple range: Jan-Jun
		for m := startMonth; m <= endMonth; m++ {
			months = append(months, m)
		}
	} else {
		// Wraps around year: Nov-Apr
		for m := startMonth; m <= 12; m++ {
			months = append(months, m)
		}
		for m := 1; m <= endMonth; m++ {
			months = append(months, m)
		}
	}
	
	return months
}

// minDistanceToPolygon calculates the minimum distance (in km) from a point to a polygon
func minDistanceToPolygon(lat, lon float64, polygon [][]float64) float64 {
	// Check if point is inside polygon first
	if pointInPolygon(lat, lon, polygon) {
		return 0.0
	}
	
	// Find minimum distance to any edge
	minDist := math.MaxFloat64
	for i := 0; i < len(polygon)-1; i++ {
		p1Lat, p1Lon := polygon[i][1], polygon[i][0]
		p2Lat, p2Lon := polygon[i+1][1], polygon[i+1][0]
		dist := distanceToLineSegment(lat, lon, p1Lat, p1Lon, p2Lat, p2Lon)
		if dist < minDist {
			minDist = dist
		}
	}
	return minDist
}

// pointInPolygon checks if a point is inside a polygon using ray casting
func pointInPolygon(lat, lon float64, polygon [][]float64) bool {
	inside := false
	j := len(polygon) - 1
	for i := 0; i < len(polygon); i++ {
		xi, yi := polygon[i][0], polygon[i][1]
		xj, yj := polygon[j][0], polygon[j][1]
		
		if ((yi > lat) != (yj > lat)) && (lon < (xj-xi)*(lat-yi)/(yj-yi)+xi) {
			inside = !inside
		}
		j = i
	}
	return inside
}

// distanceToLineSegment calculates the shortest distance from a point to a line segment (in km)
func distanceToLineSegment(pLat, pLon, aLat, aLon, bLat, bLon float64) float64 {
	// Convert to simple distance calculation
	// Project point onto line segment and calculate distance
	
	// Vector from A to B
	dx := bLon - aLon
	dy := bLat - aLat
	
	if dx == 0 && dy == 0 {
		// A and B are the same point
		return haversineDistance(pLat, pLon, aLat, aLon)
	}
	
	// Calculate parameter t for projection
	t := ((pLon-aLon)*dx + (pLat-aLat)*dy) / (dx*dx + dy*dy)
	
	if t < 0 {
		// Closest point is A
		return haversineDistance(pLat, pLon, aLat, aLon)
	} else if t > 1 {
		// Closest point is B
		return haversineDistance(pLat, pLon, bLat, bLon)
	}
	
	// Closest point is on the segment
	closestLat := aLat + t*dy
	closestLon := aLon + t*dx
	return haversineDistance(pLat, pLon, closestLat, closestLon)
}

// HandleAPIExportPatrolPixels returns patrol pixel effort data for multiple parks in bulk
// GET /api/export/patrol-pixels?parks=ID1,ID2,ID3&from=YYYY-MM-DD&to=YYYY-MM-DD
func (s *Server) HandleAPIExportPatrolPixels(w http.ResponseWriter, r *http.Request) {
	parksStr := r.URL.Query().Get("parks")
	if parksStr == "" {
		http.Error(w, "parks parameter required", http.StatusBadRequest)
		return
	}
	
	parkIDs := strings.Split(parksStr, ",")
	if len(parkIDs) == 0 || len(parkIDs) > 100 {
		http.Error(w, "parks parameter must contain 1-100 park IDs", http.StatusBadRequest)
		return
	}
	
	fromStr := r.URL.Query().Get("from")
	toStr := r.URL.Query().Get("to")
	
	// Build bbox for each park (30km buffer) and collect all pixels
	type PixelData struct {
		ParkID      string  `json:"park_id"`
		ParkName    string  `json:"park_name"`
		Year        int     `json:"year"`
		Month       int     `json:"month"`
		Lat         float64 `json:"lat"`
		Lon         float64 `json:"lon"`
		Intensity   float64 `json:"intensity"`
		FootKm      float64 `json:"foot_km"`
		VehicleKm   float64 `json:"vehicle_km"`
		AircraftKm  float64 `json:"aircraft_km"`
	}
	
	var allPixels []PixelData
	
	for _, parkID := range parkIDs {
		parkID = strings.TrimSpace(parkID)
		if parkID == "" {
			continue
		}
		
		// Get park name from AreaStore
		parkName := parkID
		if s.AreaStore != nil {
			for _, area := range s.AreaStore.Areas {
				if area.ID == parkID {
					parkName = area.Name
					break
				}
			}
		}
		
		// Get park boundary from AreaStore
		if s.AreaStore == nil {
			continue
		}
		
		var parkArea *areas.ProtectedArea
		for i := range s.AreaStore.Areas {
			if s.AreaStore.Areas[i].ID == parkID {
				parkArea = &s.AreaStore.Areas[i]
				break
			}
		}
		
		if parkArea == nil {
			continue
		}
		
		// Parse boundary coordinates
		var coordsData [][][]float64
		if err := json.Unmarshal(parkArea.Geometry.Coordinates, &coordsData); err != nil || len(coordsData) == 0 {
			continue
		}
		
		coords := coordsData[0]
		if len(coords) == 0 {
			continue
		}
		
		// Calculate bounding box for initial query (with buffer for query efficiency)
		minLon, maxLon := coords[0][0], coords[0][0]
		minLat, maxLat := coords[0][1], coords[0][1]
		for _, c := range coords {
			if c[0] < minLon {
				minLon = c[0]
			}
			if c[0] > maxLon {
				maxLon = c[0]
			}
			if c[1] < minLat {
				minLat = c[1]
			}
			if c[1] > maxLat {
				maxLat = c[1]
			}
		}
		
		// Expand bbox slightly for initial query (35km to ensure we catch all cells within 30km of boundary)
		bufferDeg := 35.0 / 111.0
		queryMinLon := minLon - bufferDeg
		queryMaxLon := maxLon + bufferDeg
		queryMinLat := minLat - bufferDeg
		queryMaxLat := maxLat + bufferDeg
		
		// Use WorldClim precipitation data for accurate dry/rainy classification per grid cell
		// Falls back to park climate, then defaults if unavailable
		
		// Query intensity using WorldClim-aware function
		gridParams := GridQueryParams{
			FromYear: int64(2018),
			ToYear:   int64(2026),
			BBox:     &[4]float64{queryMinLon, queryMinLat, queryMaxLon, queryMaxLat},
		}
		if fromStr != "" {
			if t, err := time.Parse("2006-01-02", fromStr); err == nil {
				gridParams.FromYear = int64(t.Year())
				gridParams.FromMonth = int64(t.Month())
			}
		}
		if toStr != "" {
			if t, err := time.Parse("2006-01-02", toStr); err == nil {
				gridParams.ToYear = int64(t.Year())
				gridParams.ToMonth = int64(t.Month())
			}
		}
		
		intensityMap, err := s.QueryGridDataWithWorldClim(r.Context(), gridParams, parkID)
		if err != nil {
			continue
		}
		
		// Query patrol effort data for monthly details (aggregated from day-level records)
		query := `
			SELECT 
				e.grid_cell_id,
				e.year,
				e.month,
				g.lat_center,
				g.lon_center,
				e.movement_type,
				SUM(e.total_distance_km) as total_distance_km
			FROM effort_data e
			JOIN grid_cells g ON e.grid_cell_id = g.id
			WHERE e.movement_type IN ('foot', 'vehicle', 'aircraft')
			  AND g.lat_center BETWEEN ? AND ?
			  AND g.lon_center BETWEEN ? AND ?
		`
		args := []interface{}{queryMinLat, queryMaxLat, queryMinLon, queryMaxLon}
		
		if fromStr != "" {
			query += " AND (e.year > ? OR (e.year = ? AND e.month >= ?))"
			if t, err := time.Parse("2006-01-02", fromStr); err == nil {
				args = append(args, t.Year(), t.Year(), int(t.Month()))
			}
		}
		if toStr != "" {
			query += " AND (e.year < ? OR (e.year = ? AND e.month <= ?))"
			if t, err := time.Parse("2006-01-02", toStr); err == nil {
				args = append(args, t.Year(), t.Year(), int(t.Month()))
			}
		}
		
		query += " GROUP BY e.grid_cell_id, e.year, e.month, g.lat_center, g.lon_center, e.movement_type"
		query += " ORDER BY e.year DESC, e.month DESC LIMIT 5000"
		
		rows, err := s.DB.Query(query, args...)
		if err != nil {
			continue
		}
		
		// Group by year/month/grid_cell for monthly records
		type MonthKey struct {
			GridCellID string
			Year       int
			Month      int
		}
		monthlyData := make(map[MonthKey]*PixelData)
		
		for rows.Next() {
			var gridCellID, movementType string
			var year, month int
			var lat, lon, distanceKm float64
			
			if err := rows.Scan(&gridCellID, &year, &month, &lat, &lon, &movementType, &distanceKm); err != nil {
				continue
			}
			
			// Check if grid cell is within 30km of polygon boundary
			minDist := minDistanceToPolygon(lat, lon, coords)
			if minDist > 30.0 {
				continue // Skip cells beyond 30km buffer
			}
			
			// Create month key
			key := MonthKey{
				GridCellID: gridCellID,
				Year:       year,
				Month:      month,
			}
			
			// Initialize monthly pixel data
			if monthlyData[key] == nil {
				// Get intensity from map (already calculated via QueryGridData)
				intensity := intensityMap[gridCellID]
				
				monthlyData[key] = &PixelData{
					ParkID:    parkID,
					ParkName:  parkName,
					Year:      year,
					Month:     month,
					Lat:       lat,
					Lon:       lon,
					Intensity: intensity,
				}
			}
			
			// Aggregate movement types
			switch movementType {
			case "foot":
				monthlyData[key].FootKm += distanceKm
			case "vehicle":
				monthlyData[key].VehicleKm += distanceKm
			case "aircraft":
				monthlyData[key].AircraftKm += distanceKm
			}
		}
		rows.Close()
		
		// Add all monthly pixels (intensity already set from intensityMap)
		for _, pixelData := range monthlyData {
			allPixels = append(allPixels, *pixelData)
		}
	}
	
	// Sort by park, year desc, month desc
	sort.Slice(allPixels, func(i, j int) bool {
		if allPixels[i].ParkID != allPixels[j].ParkID {
			return allPixels[i].ParkID < allPixels[j].ParkID
		}
		if allPixels[i].Year != allPixels[j].Year {
			return allPixels[i].Year > allPixels[j].Year
		}
		return allPixels[i].Month > allPixels[j].Month
	})
	
	response := map[string]interface{}{
		"pixels":       allPixels,
		"total_pixels": len(allPixels),
		"parks":        len(parkIDs),
	}
	
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=300") // Cache for 5 minutes
	json.NewEncoder(w).Encode(response)
}

// HandleAPIGridCellEffort returns monthly effort data for a specific grid cell
// GET /api/grid/{id}/effort?from=YYYY-MM-DD&to=YYYY-MM-DD
func (s *Server) HandleAPIGridCellEffort(w http.ResponseWriter, r *http.Request) {
	gridCellID := r.PathValue("id")
	if gridCellID == "" {
		http.Error(w, "grid cell id required", http.StatusBadRequest)
		return
	}
	
	fromStr := r.URL.Query().Get("from")
	toStr := r.URL.Query().Get("to")
	
	// Build query for monthly effort data (aggregated from day-level records)
	query := `
		SELECT 
			e.year,
			e.month,
			e.movement_type,
			SUM(e.total_distance_km) as total_distance_km,
			SUM(e.total_points) as total_points
		FROM effort_data e
		WHERE e.grid_cell_id = ?
		  AND e.movement_type IN ('foot', 'vehicle', 'aircraft')
	`
	args := []interface{}{gridCellID}
	
	if fromStr != "" {
		query += " AND (e.year > ? OR (e.year = ? AND e.month >= ?))"
		if t, err := time.Parse("2006-01-02", fromStr); err == nil {
			args = append(args, t.Year(), t.Year(), int(t.Month()))
		}
	}
	if toStr != "" {
		query += " AND (e.year < ? OR (e.year = ? AND e.month <= ?))"
		if t, err := time.Parse("2006-01-02", toStr); err == nil {
			args = append(args, t.Year(), t.Year(), int(t.Month()))
		}
	}
	
	query += " GROUP BY e.year, e.month, e.movement_type"
	query += " ORDER BY e.year DESC, e.month DESC LIMIT 500"
	
	rows, err := s.DB.Query(query, args...)
	if err != nil {
		internalError(w, "grid query failed", err)
		return
	}
	defer rows.Close()
	
	type MonthlyEffort struct {
		Year        int     `json:"year"`
		Month       int     `json:"month"`
		FootKm      float64 `json:"foot_km"`
		VehicleKm   float64 `json:"vehicle_km"`
		AircraftKm  float64 `json:"aircraft_km"`
		FootPoints  int     `json:"foot_points"`
		VehiclePts  int     `json:"vehicle_points"`
		AircraftPts int     `json:"aircraft_points"`
	}
	
	// Group by year/month
	monthMap := make(map[string]*MonthlyEffort)
	
	for rows.Next() {
		var year, month, points int
		var movementType string
		var distanceKm float64
		
		if err := rows.Scan(&year, &month, &movementType, &distanceKm, &points); err != nil {
			continue
		}
		
		key := fmt.Sprintf("%d-%02d", year, month)
		if monthMap[key] == nil {
			monthMap[key] = &MonthlyEffort{
				Year:  year,
				Month: month,
			}
		}
		
		switch movementType {
		case "foot":
			monthMap[key].FootKm = distanceKm
			monthMap[key].FootPoints = points
		case "vehicle":
			monthMap[key].VehicleKm = distanceKm
			monthMap[key].VehiclePts = points
		case "aircraft":
			monthMap[key].AircraftKm = distanceKm
			monthMap[key].AircraftPts = points
		}
	}
	
	// Convert to sorted array
	var months []MonthlyEffort
	for _, m := range monthMap {
		// Calculate intensity (temporal frequency)
		// Dry season months (Nov-Apr) get full weight, rainy (May-Oct) get 30% weight
		months = append(months, *m)
	}
	
	// Sort by year desc, month desc
	sort.Slice(months, func(i, j int) bool {
		if months[i].Year != months[j].Year {
			return months[i].Year > months[j].Year
		}
		return months[i].Month > months[j].Month
	})
	
	response := map[string]interface{}{
		"grid_cell_id": gridCellID,
		"months":       months,
		"total_months": len(months),
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// HandleAPIGrid returns grid cell effort data as GeoJSON FeatureCollection.
// HandleAPIVersion returns version info and recent commits
func (s *Server) HandleAPIVersion(w http.ResponseWriter, r *http.Request) {
	commits := []string{}
	
	// Read commits from file generated at build time
	if data, err := os.ReadFile(".git-commits.txt"); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			if line = strings.TrimSpace(line); line != "" {
				commits = append(commits, line)
			}
		}
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"version": Version,
		"commits": commits,
	})
}

// Query params:
//   - year: filter by year (optional, defaults to current year)
//   - month: filter by month (optional, 1-12)
//   - from/to: date range (optional, format: YYYY-MM-DD)
//   - type: movement type filter (optional, comma-separated: foot,vehicle,aerial)
//   - bbox: bounding box filter (optional, format: minLng,minLat,maxLng,maxLat)
func (s *Server) HandleAPIGrid(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	// Parse query params
	yearStr := r.URL.Query().Get("year")
	monthStr := r.URL.Query().Get("month")
	fromStr := r.URL.Query().Get("from")
	toStr := r.URL.Query().Get("to")
	typeStr := r.URL.Query().Get("type")
	bboxStr := r.URL.Query().Get("bbox")

	// Build query params
	params := GridQueryParams{}
	now := time.Now()

	// Determine date range (year + month + day precision)
	if fromStr != "" || toStr != "" {
		params.FromYear = int64(now.Year() - 1)
		params.ToYear = int64(now.Year())
		if fromStr != "" {
			if t, err := time.Parse("2006-01-02", fromStr); err == nil {
				params.FromYear = int64(t.Year())
				params.FromMonth = int64(t.Month())
				params.FromDay = int64(t.Day())
			}
		}
		if toStr != "" {
			if t, err := time.Parse("2006-01-02", toStr); err == nil {
				params.ToYear = int64(t.Year())
				params.ToMonth = int64(t.Month())
				params.ToDay = int64(t.Day())
			}
		}
	} else if yearStr != "" {
		if y, err := strconv.ParseInt(yearStr, 10, 64); err == nil {
			params.FromYear = y
			params.ToYear = y
		}
	} else {
		params.FromYear = int64(now.Year())
		params.ToYear = int64(now.Year())
	}

	// Parse month
	if monthStr != "" {
		if month, err := strconv.ParseInt(monthStr, 10, 64); err == nil && month >= 1 && month <= 12 {
			params.Month = &month
		}
	}

	// Parse movement types
	if typeStr == "none" {
		// Explicit "none" means no types selected → return empty results
		params.MovementTypes = []string{"__none__"}
	} else if typeStr != "" {
		for _, t := range strings.Split(typeStr, ",") {
			t = strings.TrimSpace(t)
			// Map 'aerial' to fixed_wing + rotor_wing (backward compat)
			if t == "aerial" {
				params.MovementTypes = append(params.MovementTypes, "fixed_wing", "rotor_wing")
				continue
			}
			if t == "foot" || t == "vehicle" || t == "boat" || t == "fixed_wing" || t == "rotor_wing" {
				params.MovementTypes = append(params.MovementTypes, t)
			}
		}
	}

	// Parse bounding box
	if bboxStr != "" {
		parts := strings.Split(bboxStr, ",")
		if len(parts) == 4 {
			var bbox [4]float64
			valid := true
			for i, p := range parts {
				if v, err := strconv.ParseFloat(strings.TrimSpace(p), 64); err == nil {
					bbox[i] = v
				} else {
					valid = false
					break
				}
			}
			if valid {
				params.BBox = &bbox
			}
		}
	}

	// Execute query
	rows, err := s.QueryGridData(ctx, params)
	if err != nil {
		slog.Error("Failed to query grid data", "error", err)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "database error"})
		return
	}

	// Build features
	features := make([]GeoJSONFeature, 0, len(rows))
	movementTypeStr := "all"
	if len(params.MovementTypes) > 0 {
		movementTypeStr = strings.Join(params.MovementTypes, ",")
	}

	for _, row := range rows {
		feature := buildGridFeature(row, movementTypeStr, params)
		features = append(features, feature)
	}

	fc := GeoJSONFeatureCollection{
		Type:     "FeatureCollection",
		Features: features,
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=60")
	json.NewEncoder(w).Encode(fc)
}

// buildGridFeature creates a GeoJSON feature for a grid cell.
// Returns a Point at the center of the cell for circle visualization.
//
// Intensity model (time-window-aware):
//   - Distance weighted heavily by movement type: foot >> vehicle >> aircraft
//   - Subcell coverage is a primary factor (spatial thoroughness)
//   - Multi-type bonus: cells visited by multiple movement types score higher
//   - Visit-day frequency matters for longer windows
//   - Recency drives halo/glow opacity decay
func buildGridFeature(row GridRow, movementType string, params GridQueryParams) GeoJSONFeature {
	windowDays := computeWindowSpanDays(params)

	// Count distinct movement types present
	numTypes := 0
	if row.FootKm > 0 {
		numTypes++
	}
	if row.VehicleKm > 0 {
		numTypes++
	}
	if row.AircraftKm > 0 {
		numTypes++
	}

	// Aircraft effective weight modulated by speed and altitude.
	// Low speed (circling/surveying) = more valuable than high speed (transit).
	// Low altitude = better observation than high altitude.
	aircraftWeight := 0.01
	if row.AircraftKm > 0 {
		if row.AvgSpeedKmh != nil && *row.AvgSpeedKmh > 0 {
			speed := *row.AvgSpeedKmh
			if speed < 50 {
				aircraftWeight *= 3.0
			} else if speed < 200 {
				aircraftWeight *= 3.0 - 2.0*(speed-50)/150.0
			}
		}
		if row.AvgAltitudeM != nil && *row.AvgAltitudeM > 0 {
			alt := *row.AvgAltitudeM
			if alt < 300 {
				aircraftWeight *= 1.5
			} else if alt < 1500 {
				aircraftWeight *= 1.5 - 1.0*(alt-300)/1200.0
			} else {
				aircraftWeight *= 0.5
			}
		}
	}

	var intensity float64

	if windowDays > 180 {
		// Long window: seasonal month-frequency as base, with distance
		// and subcell coverage as differentiators.
		var monthBase float64
		if row.DryMonths > 0 || row.RainyMonths > 0 {
			actualWeight := float64(row.DryMonths) + float64(row.RainyMonths)*0.3
			expectedWeight := 6.0
			monthBase = actualWeight / expectedWeight
		} else if row.CoveragePercent != nil && *row.CoveragePercent > 0 {
			monthBase = *row.CoveragePercent / 80.0
		} else {
			monthBase = row.TotalDistanceKm / 80.0
		}

		// Distance differentiator: weighted by movement type.
		// Gives extra credit to cells with high ground effort.
		// Saturates at ~200 effective km (e.g. 200km foot or 1300km vehicle).
		wDist := row.FootKm*1.0 + row.VehicleKm*0.15 + row.AircraftKm*aircraftWeight
		distBonus := math.Log2(1.0+wDist) / math.Log2(1.0+200.0)
		if distBonus > 1.0 {
			distBonus = 1.0
		}

		// Subcell differentiator: more spatial coverage = more thorough
		subBonus := float64(row.SubcellCount) / 60.0
		if subBonus > 1.0 {
			subBonus = 1.0
		}

		// Blend: months 55%, distance 25%, subcell 20%
		intensity = monthBase*0.55 + distBonus*0.25 + subBonus*0.20
	} else {
		// Short-to-medium window (1-180 days).
		//
		// Distance weighted by movement type effort per km:
		//   foot: 1.0/km  - highest effort, most valuable
		//   vehicle: 0.15/km - moderate effort
		//   aircraft: 0.02/km - minimal per-km effort; value is in coverage breadth
		//
		// A 50km flight = 1.0 effective km (presence detected, not patrolled)
		// A 5km foot patrol = 5.0 effective km (thorough ground coverage)
		weightedDist := row.FootKm*1.0 + row.VehicleKm*0.15 + row.AircraftKm*aircraftWeight
		// Fall back to total if per-type breakdown unavailable (legacy data)
		if weightedDist == 0 && row.TotalDistanceKm > 0 {
			weightedDist = row.TotalDistanceKm * 0.33
		}

		// Threshold scales logarithmically with window size:
		//   1d → 8km, 3d → 16km, 7d → 24km, 14d → 31km,
		//   28d → 39km, 90d → 52km, 180d → 60km
		distThreshold := 8.0 * math.Log2(1.0+float64(windowDays))
		distFactor := weightedDist / distThreshold
		if distFactor > 1.0 {
			distFactor = 1.0
		}

		// Subcell spatial coverage: primary factor (0-100 subcells).
		// 30 subcells visited = full score. This represents 30% area coverage
		// of the 10x10 grid, which is excellent for a single time window.
		subcellFactor := float64(row.SubcellCount) / 30.0
		if subcellFactor > 1.0 {
			subcellFactor = 1.0
		}

		// Multi-type bonus: visiting with different movement types means
		// more comprehensive surveillance (ground + air = better).
		multiTypeBonus := 0.0
		if numTypes >= 2 {
			multiTypeBonus = 0.15 * float64(numTypes-1) // +0.15 for 2 types, +0.30 for 3
		}

		// Visit-day frequency (saturates at visit every 3 days)
		expectedVisitDays := float64(windowDays) / 3.0
		if expectedVisitDays < 1 {
			expectedVisitDays = 1
		}
		visitFactor := float64(row.VisitDays) / expectedVisitDays
		if visitFactor > 1.0 {
			visitFactor = 1.0
		}

		// Blend: subcell coverage and distance are primary, visit-days grow
		// with longer windows. Multi-type bonus stacks on top.
		//
		// For short windows (1d):
		//   distWeight=0.35, subcellWeight=0.35, visitWeight=0.30
		// For medium windows (30d):
		//   distWeight=0.25, subcellWeight=0.35, visitWeight=0.40
		// For long windows (180d):
		//   distWeight=0.15, subcellWeight=0.35, visitWeight=0.50
		visitWeight := 0.30 + 0.20*(1.0-1.0/(1.0+math.Log2(float64(windowDays))))
		distWeight := (1.0 - 0.35 - visitWeight)
		subcellWeight := 0.35

		intensity = distFactor*distWeight + subcellFactor*subcellWeight + visitFactor*visitWeight + multiTypeBonus
	}

	if intensity > 1.5 {
		intensity = 1.5
	}

	// Subcell spatial coverage: fraction of 100 possible subcells visited
	subcellCoverage := float64(row.SubcellCount) / 100.0
	if subcellCoverage > 1.0 {
		subcellCoverage = 1.0
	}

	// Recency: how fresh is the last visit relative to window end?
	recency := computeRecency(row.LastVisitDay, params)

	return GeoJSONFeature{
		Type: "Feature",
		Geometry: GeoJSONGeometry{
			Type:        "Point",
			Coordinates: []float64{row.LonCenter, row.LatCenter},
		},
		Properties: map[string]interface{}{
			"id":                row.GridCellID,
			"total_distance_km": row.TotalDistanceKm,
			"total_points":      row.TotalPoints,
			"unique_uploads":    row.UniqueUploads,
			"movement_type":     movementType,
			"intensity":         intensity,
			"coverage_percent":  row.CoveragePercent,
			"subcell_coverage":  subcellCoverage,
			"recency":           recency,
			"visit_days":        row.VisitDays,
			"foot_km":             row.FootKm,
			"vehicle_km":          row.VehicleKm,
			"aircraft_km":         row.AircraftKm,
			"boat_km":             row.BoatKm,
			"fixed_wing_km":       row.FixedWingKm,
			"rotor_wing_km":       row.RotorWingKm,
			"avg_speed_kmh":       row.AvgSpeedKmh,
			"avg_altitude_m":      row.AvgAltitudeM,
			"foot_speed_kmh":      row.FootSpeedKmh,
			"vehicle_speed_kmh":   row.VehicleSpeedKmh,
			"aircraft_speed_kmh":  row.AircraftSpeedKmh,
			"boat_speed_kmh":       row.BoatSpeedKmh,
			"fixed_wing_speed_kmh": row.FixedWingSpeedKmh,
			"rotor_wing_speed_kmh": row.RotorWingSpeedKmh,
			"foot_altitude_m":       row.FootAltitudeM,
			"vehicle_altitude_m":    row.VehicleAltitudeM,
			"aircraft_altitude_m":   row.AircraftAltitudeM,
			"boat_altitude_m":       row.BoatAltitudeM,
			"fixed_wing_altitude_m": row.FixedWingAltitudeM,
			"rotor_wing_altitude_m": row.RotorWingAltitudeM,
		},
	}
}

// HandleAPIAreas returns protected areas as GeoJSON FeatureCollection.
func (s *Server) HandleAPIAreas(w http.ResponseWriter, r *http.Request) {
	if s.AreaStore == nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"error": "area store not configured"})
		return
	}

	features := make([]GeoJSONFeature, 0, len(s.AreaStore.Areas))

	for _, area := range s.AreaStore.Areas {
		// Compute center from bounding box
		latMin, latMax, lonMin, lonMax := area.GetBoundingBox()
		centerLat := (latMin + latMax) / 2
		centerLon := (lonMin + lonMax) / 2

		// Use the polygon geometry directly from the area data
		feature := GeoJSONFeature{
			Type: "Feature",
			Geometry: GeoJSONGeometry{
				Type:        area.Geometry.Type,
				Coordinates: area.Geometry.Coordinates,
			},
			Properties: map[string]interface{}{
				"id":           area.ID,
				"name":         area.Name,
				"country":      area.Country,
				"country_code": area.CountryCode,
				"wdpa_id":      area.WDPAID,
				"area_km2":     area.AreaKm2,
				"partner":      area.Partner,
				"buffer_km":    area.BufferKm,
				"center":       []float64{centerLon, centerLat}, // GeoJSON [lon, lat]
			},
		}
		features = append(features, feature)
	}

	fc := GeoJSONFeatureCollection{
		Type:     "FeatureCollection",
		Features: features,
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	json.NewEncoder(w).Encode(fc)
}

// HandleAPILogin handles JSON login requests.
func (s *Server) HandleAPILogin(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid request"})
		return
	}

	sessionID, _, err := s.Auth.Login(r.Context(), req.Email, req.Password)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}

	auth.SetSessionCookie(w, sessionID)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

// HandleAPIRegister handles JSON registration requests.
func (s *Server) HandleAPIRegister(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid request"})
		return
	}

	if len(req.Password) < 8 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "password must be at least 8 characters"})
		return
	}

	err := s.Auth.Register(r.Context(), req.Email, req.Password, "", "", "")
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok", "message": "Registration successful. Account pending approval."})
}

// HandleAPILogout handles JSON logout requests.
func (s *Server) HandleAPILogout(w http.ResponseWriter, r *http.Request) {
	if cookie, err := r.Cookie(auth.SessionCookieName); err == nil {
		if err := s.Auth.Logout(r.Context(), cookie.Value); err != nil {
			// Session deletion failed, but we still clear the cookie
			// The error is already logged by Auth.Logout
			slog.Warn("API logout session deletion failed, continuing with cookie clear")
		}
	}
	auth.ClearSessionCookie(w)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

// HandleAPIStats returns global statistics filtered by date range and movement type.
// Query params:
//   - from: start date (YYYY-MM-DD)
//   - to: end date (YYYY-MM-DD)
//   - type: movement type filter (foot,vehicle,aerial)
//   - bbox: bounding box (minLng,minLat,maxLng,maxLat) - not yet implemented
func (s *Server) HandleAPIStats(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	// Parse date range and movement type filter
	fromStr := r.URL.Query().Get("from")
	toStr := r.URL.Query().Get("to")
	bboxStr := r.URL.Query().Get("bbox")
	typeStr := r.URL.Query().Get("type")

	// Parse bbox if provided (minLng,minLat,maxLng,maxLat)
	var bbox []float64
	if bboxStr != "" {
		parts := strings.Split(bboxStr, ",")
		if len(parts) == 4 {
			bbox = make([]float64, 4)
			for i, p := range parts {
				bbox[i], _ = strconv.ParseFloat(strings.TrimSpace(p), 64)
			}
		}
	}

	// Default to current year if no dates provided
	now := time.Now()
	fromYear := int64(now.Year())
	toYear := int64(now.Year())
	var fromMonth, toMonth, fromDay, toDay int64
	if fromStr != "" {
		if t, err := time.Parse("2006-01-02", fromStr); err == nil {
			fromYear = int64(t.Year())
			fromMonth = int64(t.Month())
			fromDay = int64(t.Day())
		}
	}
	if toStr != "" {
		if t, err := time.Parse("2006-01-02", toStr); err == nil {
			toYear = int64(t.Year())
			toMonth = int64(t.Month())
			toDay = int64(t.Day())
		}
	}

	// Parse movement types
	var movementTypes []string
	if typeStr == "none" {
		movementTypes = []string{"__none__"}
	} else if typeStr != "" {
		for _, t := range strings.Split(typeStr, ",") {
			t = strings.TrimSpace(t)
			// Map 'aerial' to fixed_wing + rotor_wing (backward compat)
			if t == "aerial" {
				movementTypes = append(movementTypes, "fixed_wing", "rotor_wing")
				continue
			}
			if t == "foot" || t == "vehicle" || t == "boat" || t == "fixed_wing" || t == "rotor_wing" {
				movementTypes = append(movementTypes, t)
			}
		}
	}

	// Aggregate stats across requested years
	var activePixels, totalUploads int64
	var totalDistanceKm float64
	// Build patrol stats query with optional bbox and movement type filter
	statsQuery := `
		SELECT 
			COUNT(DISTINCT e.grid_cell_id) as active_pixels,
			COALESCE(SUM(e.total_distance_km), 0) as total_distance_km,
			COALESCE(SUM(e.unique_uploads), 0) as total_uploads
		FROM effort_data e
		JOIN grid_cells g ON e.grid_cell_id = g.id
		WHERE 1=1
	`
	var args []interface{}
	// Day-level filtering when day precision is available
	if fromDay > 0 && toDay > 0 && fromMonth > 0 && toMonth > 0 {
		statsQuery += " AND ((e.day IS NOT NULL AND (e.year * 10000 + e.month * 100 + e.day) BETWEEN ? AND ?) OR (e.day IS NULL AND (e.year * 100 + e.month) BETWEEN ? AND ?))"
		args = append(args, fromYear*10000+fromMonth*100+fromDay, toYear*10000+toMonth*100+toDay, fromYear*100+fromMonth, toYear*100+toMonth)
	} else if fromMonth > 0 && toMonth > 0 {
		statsQuery += " AND (e.year * 100 + e.month) BETWEEN ? AND ?"
		args = append(args, fromYear*100+fromMonth, toYear*100+toMonth)
	} else {
		statsQuery += " AND e.year BETWEEN ? AND ?"
		args = append(args, fromYear, toYear)
	}

	// Movement type filter
	if len(movementTypes) > 0 && len(movementTypes) < 5 {
		placeholders := make([]string, len(movementTypes))
		for i, t := range movementTypes {
			placeholders[i] = "?"
			args = append(args, t)
		}
		statsQuery += fmt.Sprintf(" AND e.movement_type IN (%s)", strings.Join(placeholders, ","))
	} else {
		statsQuery += " AND e.movement_type = 'all'"
	}

	if len(bbox) == 4 {
		statsQuery += ` AND g.lat_center >= ? AND g.lat_center <= ?
		               AND g.lon_center >= ? AND g.lon_center <= ?`
		args = append(args, bbox[1], bbox[3], bbox[0], bbox[2])
	}

	err := s.DB.QueryRowContext(ctx, statsQuery, args...).Scan(&activePixels, &totalDistanceKm, &totalUploads)
	if err != nil {
		slog.Error("Failed to query patrol stats", "error", err)
	}

	// Get conservation summary data
	var totalFires, prevFires int
	var totalDeforestation, prevDeforestation float64
	var totalSettlements int

	// Fire stats from feature_geometries (fire_trajectory) with bbox + date filtering
	// Uses precomputed stat_value column (= fires_total) for fast aggregation
	{
		fireQuery := `SELECT COALESCE(SUM(stat_value), 0) FROM feature_geometries
			WHERE feature_type = 'fire_trajectory'`
		var fireArgs []interface{}
		if fromStr != "" {
			fireQuery += " AND start_date >= ?"
			fireArgs = append(fireArgs, fromStr)
		}
		if toStr != "" {
			fireQuery += " AND start_date <= ?"
			fireArgs = append(fireArgs, toStr)
		}
		if len(bbox) == 4 {
			fireQuery += " AND bbox_maxx >= ? AND bbox_minx <= ? AND bbox_maxy >= ? AND bbox_miny <= ?"
			fireArgs = append(fireArgs, bbox[0], bbox[2], bbox[1], bbox[3])
		}
		var totalFiresF float64
		s.DB.QueryRow(fireQuery, fireArgs...).Scan(&totalFiresF)
		totalFires = int(totalFiresF)

		// Previous period for trend
		if fromStr != "" && toStr != "" {
			fromTime, _ := time.Parse("2006-01-02", fromStr)
			toTime, _ := time.Parse("2006-01-02", toStr)
			duration := toTime.Sub(fromTime)
			prevFrom := fromTime.Add(-duration).Format("2006-01-02")
			prevTo := fromTime.Add(-24 * time.Hour).Format("2006-01-02")
			prevQuery := `SELECT COALESCE(SUM(stat_value), 0) FROM feature_geometries
				WHERE feature_type = 'fire_trajectory' AND start_date >= ? AND start_date <= ?`
			prevArgs := []interface{}{prevFrom, prevTo}
			if len(bbox) == 4 {
				prevQuery += " AND bbox_maxx >= ? AND bbox_minx <= ? AND bbox_maxy >= ? AND bbox_miny <= ?"
				prevArgs = append(prevArgs, bbox[0], bbox[2], bbox[1], bbox[3])
			}
			var prevFiresF float64
			s.DB.QueryRow(prevQuery, prevArgs...).Scan(&prevFiresF)
			prevFires = int(prevFiresF)
		}
	}

	// Deforestation stats from feature_geometries with bbox + date filtering
	// Uses precomputed stat_value column (= area_km2) for fast aggregation
	{
		deforestQuery := `SELECT COALESCE(SUM(stat_value), 0) FROM feature_geometries
			WHERE feature_type = 'deforestation'`
		var deforestArgs []interface{}
		if fromStr != "" {
			deforestQuery += " AND start_date >= ?"
			deforestArgs = append(deforestArgs, fromStr)
		}
		if toStr != "" {
			deforestQuery += " AND start_date <= ?"
			deforestArgs = append(deforestArgs, toStr)
		}
		if len(bbox) == 4 {
			deforestQuery += " AND bbox_maxx >= ? AND bbox_minx <= ? AND bbox_maxy >= ? AND bbox_miny <= ?"
			deforestArgs = append(deforestArgs, bbox[0], bbox[2], bbox[1], bbox[3])
		}
		s.DB.QueryRow(deforestQuery, deforestArgs...).Scan(&totalDeforestation)

		// Previous period for trend
		if fromStr != "" && toStr != "" {
			fromTime, _ := time.Parse("2006-01-02", fromStr)
			toTime, _ := time.Parse("2006-01-02", toStr)
			duration := toTime.Sub(fromTime)
			prevFrom := fromTime.Add(-duration).Format("2006-01-02")
			prevTo := fromTime.Add(-24 * time.Hour).Format("2006-01-02")
			prevQuery := `SELECT COALESCE(SUM(stat_value), 0) FROM feature_geometries
				WHERE feature_type = 'deforestation' AND start_date >= ? AND start_date <= ?`
			prevArgs := []interface{}{prevFrom, prevTo}
			if len(bbox) == 4 {
				prevQuery += " AND bbox_maxx >= ? AND bbox_minx <= ? AND bbox_maxy >= ? AND bbox_miny <= ?"
				prevArgs = append(prevArgs, bbox[0], bbox[2], bbox[1], bbox[3])
			}
			s.DB.QueryRow(prevQuery, prevArgs...).Scan(&prevDeforestation)
		}
	}

	// Total settlements (with optional bbox filter)
	if len(bbox) == 4 {
		s.DB.QueryRow(`
			SELECT COUNT(*) FROM park_settlements
			WHERE lon >= ? AND lon <= ? AND lat >= ? AND lat <= ?
		`, bbox[0], bbox[2], bbox[1], bbox[3]).Scan(&totalSettlements)
	} else {
		s.DB.QueryRow(`SELECT COUNT(*) FROM park_settlements`).Scan(&totalSettlements)
	}

	// Calculate trends
	fireTrend := "stable"
	if prevFires > 0 {
		change := float64(totalFires-prevFires) / float64(prevFires) * 100
		if change > 10 {
			fireTrend = "up"
		} else if change < -10 {
			fireTrend = "down"
		}
	}

	deforestTrend := "stable"
	if prevDeforestation > 0 {
		change := (totalDeforestation - prevDeforestation) / prevDeforestation * 100
		if change > 10 {
			deforestTrend = "worsening"
		} else if change < -10 {
			deforestTrend = "improving"
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-cache")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"active_pixels":       activePixels,
		"total_distance_km":   totalDistanceKm,
		"total_patrols":       totalUploads,
		"total_fires":         totalFires,
		"fire_trend":          fireTrend,
		"total_deforestation": totalDeforestation,
		"deforest_trend":      deforestTrend,
		"total_settlements":   totalSettlements,
	})
}

// HandleAPIAreasSearch searches protected areas, countries, and regions by name.
// Query params:
//   - q: search query (required)
// Returns matching results with center coordinates for map navigation.
// Results include:
//   - Loaded (keystone) PAs - shown in green
//   - Unloaded WDPA PAs - shown in grey
//   - Countries - for zooming to country view
//   - Administrative regions (GADM L1) - provinces, states, etc.
func (s *Server) HandleAPIAreasSearch(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query().Get("q")
	if query == "" {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode([]interface{}{})
		return
	}

	// Case-insensitive search
	queryLower := strings.ToLower(query)
	results := make([]map[string]interface{}, 0, 30)

	// Track WDPA IDs we've already added from loaded areas
	loadedWDPAIDs := make(map[string]bool)

	// 1. Search countries first (if query matches)
	if s.GADMStore != nil {
		countries := s.GADMStore.SearchCountries(query, 3)
		for _, c := range countries {
			results = append(results, map[string]interface{}{
				"type":    "country",
				"name":    c.Name,
				"code":    c.Code,
				"center":  c.Center,
				"bbox":    c.BBox,
			})
		}
	}

	// 2. Search loaded areas (keystones) - these show in green
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if strings.Contains(strings.ToLower(area.Name), queryLower) ||
				strings.Contains(strings.ToLower(area.Country), queryLower) {
				// Calculate center from bounding box
				latMin, latMax, lonMin, lonMax := area.GetBoundingBox()
				centerLat := (latMin + latMax) / 2
				centerLon := (lonMin + lonMax) / 2

				results = append(results, map[string]interface{}{
					"type":      "pa",
					"id":        area.ID,
					"name":      area.Name,
					"country":   area.Country,
					"wdpa_id":   area.WDPAID,
					"area_km2":  area.AreaKm2,
					"center":    []float64{centerLon, centerLat},
					"bbox":      []float64{lonMin, latMin, lonMax, latMax},
					"loaded":    true, // This PA is loaded in the system
				})

				loadedWDPAIDs[area.WDPAID] = true

				if len(results) >= 15 {
					break
				}
			}
		}
	}

	// 3. Search WDPA index for additional unloaded areas - these show in grey
	if s.WDPAIndex != nil && len(results) < 25 {
		wdpaResults := s.WDPAIndex.Search(query, 25-len(results))
		for _, entry := range wdpaResults {
			// Skip if already added from loaded areas
			wdpaIDStr := strconv.Itoa(entry.WDPAID)
			if loadedWDPAIDs[wdpaIDStr] {
				continue
			}

			results = append(results, map[string]interface{}{
				"type":        "pa",
				"name":        entry.Name,
				"country":     entry.Country,
				"wdpa_id":     wdpaIDStr,
				"area_km2":    entry.AreaKm2,
				"designation": entry.Designation,
				"loaded":      false, // This PA is NOT loaded in the system
			})

			if len(results) >= 25 {
				break
			}
		}
	}

	// 4. Search administrative regions (GADM L1)
	if s.GADMStore != nil && len(results) < 30 {
		regions := s.GADMStore.SearchRegions(query, 30-len(results))
		for _, r := range regions {
			results = append(results, map[string]interface{}{
				"type":         "region",
				"id":           r.ID,
				"name":         r.Name,
				"country":      r.Country,
				"country_code": r.CountryCode,
				"region_type":  r.Type,
				"center":       r.Center,
				"bbox":         r.BBox,
			})
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=60")
	json.NewEncoder(w).Encode(results)
}

// HandleAPIActivity returns recent upload activity.
func (s *Server) HandleAPIActivity(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)

	// Get recent uploads with coordinates
	uploads, err := q.ListGPXUploadsWithCoords(ctx, dbgen.ListGPXUploadsWithCoordsParams{
		Limit:  10,
		Offset: 0,
	})
	if err != nil {
		slog.Error("failed to get activity", "error", err)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "database error"})
		return
	}

	activities := make([]map[string]interface{}, 0, len(uploads))
	for _, u := range uploads {
		location := "Unknown"
		if u.ProtectedAreaID != nil && *u.ProtectedAreaID != "" {
			location = *u.ProtectedAreaID
		} else if u.CentroidLat != nil && u.CentroidLon != nil && s.AreaStore != nil {
			// Try to find which PA the coordinates fall within
			if area := s.AreaStore.FindArea(*u.CentroidLat, *u.CentroidLon); area != nil {
				location = area.Name
			}
		}
		// If location is still unknown but we have coordinates, try to find a meaningful name
		if location == "Unknown" && u.CentroidLat != nil && u.CentroidLon != nil {
			var placeName, countryName string
			var hasPlace, hasCountry bool

			// Try to find the nearest OSM place within 50km
			placeName, hasPlace = s.findNearestOSMPlace(*u.CentroidLat, *u.CentroidLon, 50.0)

			// Try to find the country
			countryName, hasCountry = s.findCountryByPoint(*u.CentroidLat, *u.CentroidLon)

			// Format the location string
			if hasPlace && hasCountry {
				location = fmt.Sprintf("Near %s, %s", placeName, countryName)
			} else if hasPlace {
				location = fmt.Sprintf("Near %s", placeName)
			} else if hasCountry {
				location = countryName
			} else {
				// Show coordinates for locations outside known areas
				location = fmt.Sprintf("%.2f°%s, %.2f°%s",
					absFloat(*u.CentroidLat), latDir(*u.CentroidLat),
					absFloat(*u.CentroidLon), lonDir(*u.CentroidLon))
			}
		}
		activity := map[string]interface{}{
			"upload_id": u.ID,
			"date":      u.UploadDate.Format("Jan 02"),
			"full_date": u.UploadDate.Format("2006-01-02"),
			"location":  location,
			"distance":  u.TotalDistanceKm,
			"type":      u.MovementType,
		}
		// Include coordinates if available
		if u.CentroidLat != nil && u.CentroidLon != nil {
			activity["lat"] = *u.CentroidLat
			activity["lon"] = *u.CentroidLon
		}
		// Get grid cells for this upload
		gridCells := []string{}
		rows, err := s.DB.QueryContext(ctx, `
			SELECT DISTINCT grid_cell_id FROM track_points 
			WHERE upload_id = ? AND grid_cell_id IS NOT NULL
		`, u.ID)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var cellID string
				if rows.Scan(&cellID) == nil {
					gridCells = append(gridCells, cellID)
				}
			}
		}
		activity["grid_cells"] = gridCells
		activities = append(activities, activity)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(activities)
}

// HandleAPIUpload handles file uploads via API.
func (s *Server) HandleAPIUpload(w http.ResponseWriter, r *http.Request) {
	user := s.Auth.GetUserFromRequest(r)
	if user == nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		json.NewEncoder(w).Encode(map[string]string{"error": "authentication required"})
		return
	}

	// Parse multipart form (max 50MB)
	if err := r.ParseMultipartForm(50 << 20); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "failed to parse form"})
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "no file provided"})
		return
	}
	defer file.Close()

	slog.Info("API upload received", "filename", header.Filename, "size", header.Size, "user", user.Email)

	// For now, just acknowledge receipt - actual processing will be added
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":   "ok",
		"filename": header.Filename,
		"size":     header.Size,
	})
}


// HandleAPIWDPASearch searches the WDPA index for protected areas.
func (s *Server) HandleAPIWDPASearch(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query().Get("q")
	if query == "" {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode([]interface{}{})
		return
	}

	if s.WDPAIndex == nil {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode([]interface{}{})
		return
	}

	// Search WDPA index
	entries := s.WDPAIndex.Search(query, 50)

	// Build set of loaded keystone WDPA IDs
	keystoneIDs := make(map[string]bool)
	if s.AreaStore != nil {
		for _, a := range s.AreaStore.Areas {
			if a.WDPAID != "" {
				keystoneIDs[a.WDPAID] = true
			}
		}
	}

	// Build response with loaded status
	results := make([]map[string]interface{}, 0, len(entries))
	for _, e := range entries {
		wdpaIDStr := fmt.Sprintf("%d", e.WDPAID)
		results = append(results, map[string]interface{}{
			"wdpa_id":      e.WDPAID,
			"name":         e.Name,
			"country":      e.Country,
			"country_code": e.CountryCode,
			"designation":  e.Designation,
			"area_km2":     e.AreaKm2,
			"loaded":       keystoneIDs[wdpaIDStr],
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(results)
}

// HandleAPIPublications returns publications for a protected area.
// GET /api/parks/{id}/publications
func (s *Server) HandleAPIPublications(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "missing park ID"})
		return
	}

	ctx := r.Context()
	q := dbgen.New(s.DB)

	// All publications are now stored with park ID (e.g., COD_Salonga)
	pubs, err := q.GetPublicationsByPA(ctx, parkID)
	if err != nil {
		slog.Error("failed to get publications", "pa_id", parkID, "error", err)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "database error"})
		return
	}

	// Transform to API response
	results := make([]map[string]interface{}, 0, len(pubs))
	for _, p := range pubs {
		item := map[string]interface{}{
			"id":       p.ID,
			"title":    p.Title,
		}
		if p.Authors != nil && *p.Authors != "" {
			var authors []string
			// Try to parse as JSON first
			if err := json.Unmarshal([]byte(*p.Authors), &authors); err != nil {
				// If not JSON, treat as plain text and create array
				authors = []string{*p.Authors}
			}
			item["authors"] = authors
		}
		if p.Year != nil {
			item["year"] = *p.Year
		}
		if p.Doi != nil {
			item["doi"] = *p.Doi
		}
		if p.Url != nil {
			item["url"] = *p.Url
		}
		if p.Abstract != nil {
			item["abstract"] = *p.Abstract
		}
		if p.CitedByCount != nil {
			item["cited_by_count"] = *p.CitedByCount
		}
		results = append(results, item)
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	json.NewEncoder(w).Encode(results)
}

// HandleAPIPublicationCount returns the publication count for a PA.
// GET /api/parks/{id}/publications/count
func (s *Server) HandleAPIPublicationCount(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "missing park ID"})
		return
	}

	ctx := r.Context()
	q := dbgen.New(s.DB)

	// All publications are now stored with park ID (e.g., COD_Salonga)
	count, err := q.GetPublicationCountByPA(ctx, parkID)
	if err != nil {
		count = 0
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	json.NewEncoder(w).Encode(map[string]interface{}{"count": count})
}

// ParkDataStatus represents the processing status for a park's various data sources
type ParkDataStatus struct {
	ParkID         string `json:"park_id"`
	FireAnalysis   *DataSourceStatus `json:"fire_analysis,omitempty"`
	GroupInfractions *DataSourceStatus `json:"group_infractions,omitempty"`
	Publications   *DataSourceStatus `json:"publications,omitempty"`
	GHSL           *DataSourceStatus `json:"ghsl,omitempty"`
	Roadless       *DataSourceStatus `json:"roadless,omitempty"`
}

type DataSourceStatus struct {
	Ready     bool   `json:"ready"`
	LastUpdate string `json:"last_update,omitempty"`
	Message   string `json:"message,omitempty"`
}

// HandleAPIParkDataStatus returns the processing status for various data sources for a park
func (s *Server) HandleAPIParkDataStatus(w http.ResponseWriter, r *http.Request) {
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
	
	status := ParkDataStatus{ParkID: parkID}
	
	// Check fire analysis
	var fireCount int
	var fireDate string
	err := s.DB.QueryRow(`SELECT COUNT(*), MAX(analyzed_at) FROM park_fire_analysis WHERE park_id = ?`, internalID).Scan(&fireCount, &fireDate)
	if err == nil && fireCount > 0 {
		status.FireAnalysis = &DataSourceStatus{Ready: true, LastUpdate: fireDate}
	} else {
		status.FireAnalysis = &DataSourceStatus{Ready: false, Message: "Fire analysis pending"}
	}
	
	// Check group infractions
	var groupCount int
	var groupDate string
	err = s.DB.QueryRow(`SELECT COUNT(*), MAX(analyzed_at) FROM park_group_infractions WHERE park_id = ?`, internalID).Scan(&groupCount, &groupDate)
	if err == nil && groupCount > 0 {
		status.GroupInfractions = &DataSourceStatus{Ready: true, LastUpdate: groupDate}
	} else {
		status.GroupInfractions = &DataSourceStatus{Ready: false, Message: "Group analysis pending"}
	}
	
	// Check publications
	var pubCount int
	var pubDate string
	err = s.DB.QueryRow(`SELECT COUNT(*), MAX(synced_at) FROM pa_publication_sync WHERE pa_id = ?`, parkID).Scan(&pubCount, &pubDate)
	if err == nil && pubCount > 0 {
		status.Publications = &DataSourceStatus{Ready: true, LastUpdate: pubDate}
	} else {
		status.Publications = &DataSourceStatus{Ready: false, Message: "Publication sync pending"}
	}
	
	// GHSL - not implemented yet
	status.GHSL = &DataSourceStatus{Ready: false, Message: "Coming soon"}
	
	// Roadless - not implemented yet
	status.Roadless = &DataSourceStatus{Ready: false, Message: "Coming soon"}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(status)
}

// HandleAPIParkInfractionSummary returns group infraction summary for modal display
func (s *Server) HandleAPIParkInfractionSummary(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	year := r.URL.Query().Get("year")
	if year == "" {
		year = "2023" // Default to most recent full year
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
	
	var result struct {
		Year              int     `json:"year"`
		TotalGroups       int     `json:"total_groups"`
		GroupsStoppedInside int   `json:"groups_stopped_inside"`
		GroupsTransited   int     `json:"groups_transited"`
		AvgDaysBurning    float64 `json:"avg_days_burning"`
		ResponseRate      float64 `json:"response_rate"` // % stopped inside
	}
	
	err := s.DB.QueryRow(`
		SELECT year, total_groups, groups_stopped_inside, groups_transited, avg_days_burning
		FROM park_group_infractions 
		WHERE park_id = ? AND year = ?
	`, internalID, year).Scan(&result.Year, &result.TotalGroups, &result.GroupsStoppedInside, &result.GroupsTransited, &result.AvgDaysBurning)
	
	if err != nil {
		// Return empty/zero result rather than error
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(result)
		return
	}
	
	if result.TotalGroups > 0 {
		result.ResponseRate = float64(result.GroupsStoppedInside) / float64(result.TotalGroups) * 100
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(result)
}

// HandleAPIParkFeatures returns GeoJSON features for a park
// Supports filtering by type (fire_trajectory, settlement, deforestation, road)
// and date range (start, end) for temporal features.
func (s *Server) HandleAPIParkFeatures(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	featureType := r.URL.Query().Get("type")
	featureID := r.URL.Query().Get("feature_id") // Fetch specific feature by ID
	startDate := r.URL.Query().Get("start")
	endDate := r.URL.Query().Get("end")
	limitStr := r.URL.Query().Get("limit")
	
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
	
	// Handle park boundary from AreaStore
	if featureType == "boundary" || featureType == "park" {
		s.handleParkBoundary(w, internalID)
		return
	}
	
	// Handle places from osm_places table
	if featureType == "place" {
		s.handlePlaceFeatures(w, internalID, limitStr)
		return
	}
	
	// Handle waterbodies from park_waterbodies table
	if featureType == "waterbody" || featureType == "water" {
		s.handleWaterbodyFeatures(w, internalID, limitStr)
		return
	}
	
	// Handle rivers from park_rivers_hydro table
	if featureType == "river" {
		s.handleRiverFeatures(w, internalID, limitStr)
		return
	}
	
	// Handle roads from roads_heigit table
	if featureType == "road" {
		s.handleRoadFeatures(w, internalID, limitStr)
		return
	}
	
	// Handle settlements with narrative from park_settlements
	if featureType == "settlement" {
		s.handleSettlementFeatures(w, internalID, limitStr, featureID)
		return
	}
	
	// Handle deforestation with narrative from deforestation_events
	if featureType == "deforestation" {
		s.handleDeforestationFeatures(w, internalID, limitStr, startDate, endDate, featureID)
		return
	}

	// Build query for feature_geometries table
	query := `
		SELECT feature_type, feature_id, geojson, start_date, end_date, properties_json
		FROM feature_geometries
		WHERE park_id = ?
	`
	args := []interface{}{internalID}
	
	if featureType != "" {
		query += " AND feature_type = ?"
		args = append(args, featureType)
	}
	
	// Allow fetching specific feature by ID (for pinning)
	if featureID != "" {
		query += " AND feature_id = ?"
		args = append(args, featureID)
	}
	
	// Filter by start_date being within the date range (not overlap)
	// This matches the UI filtering behavior for fire narratives
	if startDate != "" {
		query += " AND (start_date IS NULL OR start_date >= ?)"
		args = append(args, startDate)
	}
	
	if endDate != "" {
		query += " AND (start_date IS NULL OR start_date <= ?)"
		args = append(args, endDate)
	}
	
	query += " ORDER BY start_date DESC, feature_id"
	
	limit := 1000 // Default limit
	// If fetching by feature_id, don't limit
	if featureID == "" {
		if limitStr != "" {
			if l, err := strconv.Atoi(limitStr); err == nil && l > 0 && l <= 10000 {
				limit = l
			}
		}
		query += fmt.Sprintf(" LIMIT %d", limit)
	}
	
	rows, err := s.DB.Query(query, args...)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	
	// Build GeoJSON FeatureCollection
	type GeoJSONFeature struct {
		Type       string                 `json:"type"`
		Geometry   json.RawMessage        `json:"geometry"`
		Properties map[string]interface{} `json:"properties"`
	}
	
	type FeatureCollection struct {
		Type     string           `json:"type"`
		Features []GeoJSONFeature `json:"features"`
	}
	
	fc := FeatureCollection{
		Type:     "FeatureCollection",
		Features: []GeoJSONFeature{},
	}
	
	for rows.Next() {
		var fType, fID, geojson string
		var startDate, endDate, propsJSON sql.NullString
		
		if err := rows.Scan(&fType, &fID, &geojson, &startDate, &endDate, &propsJSON); err != nil {
			continue
		}
		
		// Parse properties
		props := make(map[string]interface{})
		if propsJSON.Valid {
			json.Unmarshal([]byte(propsJSON.String), &props)
		}
		props["feature_type"] = fType
		props["feature_id"] = fID
		if startDate.Valid {
			props["start_date"] = startDate.String
		}
		if endDate.Valid {
			props["end_date"] = endDate.String
		}
		
		fc.Features = append(fc.Features, GeoJSONFeature{
			Type:       "Feature",
			Geometry:   json.RawMessage(geojson),
			Properties: props,
		})
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(fc)
}

// handleParkBoundary returns the park boundary geometry from AreaStore
func (s *Server) handleParkBoundary(w http.ResponseWriter, parkID string) {
	if s.AreaStore == nil {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"type":     "FeatureCollection",
			"features": []interface{}{},
		})
		return
	}
	
	// Find the area in AreaStore
	var area *areas.ProtectedArea
	for i := range s.AreaStore.Areas {
		if s.AreaStore.Areas[i].ID == parkID {
			area = &s.AreaStore.Areas[i]
			break
		}
	}
	
	if area == nil || len(area.Geometry.Coordinates) == 0 {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"type":     "FeatureCollection",
			"features": []interface{}{},
		})
		return
	}
	
	// Return the boundary as a GeoJSON Feature
	feature := map[string]interface{}{
		"type":     "Feature",
		"geometry": area.Geometry,
		"properties": map[string]interface{}{
			"park_id":   area.ID,
			"name":      area.Name,
			"country":   area.Country,
			"area_km2":  area.AreaKm2,
			"wdpa_id":   area.WDPAID,
			"type":      "boundary",
		},
	}
	
	fc := map[string]interface{}{
		"type":     "FeatureCollection",
		"features": []interface{}{feature},
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(fc)
}

// handlePlaceFeatures returns GeoJSON features for osm_places
func (s *Server) handlePlaceFeatures(w http.ResponseWriter, parkID string, limitStr string) {
	limit := 1000
	if limitStr != "" {
		if l, err := strconv.Atoi(limitStr); err == nil && l > 0 && l <= 10000 {
			limit = l
		}
	}

	rows, err := s.DB.Query(`
		SELECT id, place_type, name, lat, lon, osm_id, osm_tags
		FROM osm_places
		WHERE park_id = ?
		ORDER BY
			CASE place_type
				WHEN 'city' THEN 1
				WHEN 'town' THEN 2
				WHEN 'village' THEN 3
				WHEN 'hamlet' THEN 4
				ELSE 5
			END,
			name
		LIMIT ?
	`, parkID, limit)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type GeoJSONFeature struct {
		Type       string                 `json:"type"`
		Geometry   json.RawMessage        `json:"geometry"`
		Properties map[string]interface{} `json:"properties"`
	}

	type FeatureCollection struct {
		Type     string           `json:"type"`
		Features []GeoJSONFeature `json:"features"`
	}

	fc := FeatureCollection{
		Type:     "FeatureCollection",
		Features: []GeoJSONFeature{},
	}

	for rows.Next() {
		var id int
		var placeType, name string
		var lat, lon float64
		var osmID, osmTags sql.NullString

		if err := rows.Scan(&id, &placeType, &name, &lat, &lon, &osmID, &osmTags); err != nil {
			continue
		}

		// Create Point geometry from lat/lon
		geometry := json.RawMessage(fmt.Sprintf(`{"type":"Point","coordinates":[%f,%f]}`, lon, lat))

		props := map[string]interface{}{
			"feature_type": "place",
			"feature_id":   fmt.Sprintf("place_%d", id),
			"place_type":   placeType,
			"name":         name,
			"lat":          lat,
			"lon":          lon,
		}
		if osmID.Valid {
			props["osm_id"] = osmID.String
		}
		if osmTags.Valid && osmTags.String != "" {
			var tags map[string]interface{}
			if json.Unmarshal([]byte(osmTags.String), &tags) == nil {
				props["osm_tags"] = tags
			}
		}

		fc.Features = append(fc.Features, GeoJSONFeature{
			Type:       "Feature",
			Geometry:   geometry,
			Properties: props,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(fc)
}

// handleWaterbodyFeatures returns waterbody features as GeoJSON
func (s *Server) handleWaterbodyFeatures(w http.ResponseWriter, parkID string, limitStr string) {
	limit := 500
	if limitStr != "" {
		if l, err := strconv.Atoi(limitStr); err == nil && l > 0 && l <= 5000 {
			limit = l
		}
	}

	type GeoJSONFeature struct {
		Type       string                 `json:"type"`
		Geometry   json.RawMessage        `json:"geometry"`
		Properties map[string]interface{} `json:"properties"`
	}

	type FeatureCollection struct {
		Type     string           `json:"type"`
		Features []GeoJSONFeature `json:"features"`
	}

	fc := FeatureCollection{
		Type:     "FeatureCollection",
		Features: []GeoJSONFeature{},
	}

	// Get waterbodies from park_waterbodies table
	wbRows, err := s.DB.Query(`
		SELECT waterbody_id, name, waterbody_type, lat, lon, geojson
		FROM park_waterbodies
		WHERE park_id = ?
		LIMIT ?
	`, parkID, limit)
	if err == nil {
		defer wbRows.Close()
		for wbRows.Next() {
			var wbID, name, wbType string
			var lat, lon float64
			var geojson sql.NullString

			if err := wbRows.Scan(&wbID, &name, &wbType, &lat, &lon, &geojson); err != nil {
				continue
			}

			var geometry json.RawMessage
			if geojson.Valid && geojson.String != "" {
				geometry = json.RawMessage(geojson.String)
			} else {
				geometry = json.RawMessage(fmt.Sprintf(`{"type":"Point","coordinates":[%f,%f]}`, lon, lat))
			}

			displayName := name
			if displayName == "" {
				displayName = wbType
			}

			fc.Features = append(fc.Features, GeoJSONFeature{
				Type:     "Feature",
				Geometry: geometry,
				Properties: map[string]interface{}{
					"feature_type":   "waterbody",
					"feature_id":     wbID,
					"name":           displayName,
					"waterbody_type": wbType,
					"lat":            lat,
					"lon":            lon,
				},
			})
		}
	}

	// Also get rivers/streams/lakes from osm_places
	riverRows, err := s.DB.Query(`
		SELECT id, name, place_type, lat, lon, geojson
		FROM osm_places
		WHERE park_id = ? AND place_type IN ('river', 'stream', 'lake')
		LIMIT ?
	`, parkID, limit)
	if err == nil {
		defer riverRows.Close()
		for riverRows.Next() {
			var id int
			var name, placeType string
			var lat, lon float64
			var geojson sql.NullString

			if err := riverRows.Scan(&id, &name, &placeType, &lat, &lon, &geojson); err != nil {
				continue
			}

			var geometry json.RawMessage
			if geojson.Valid && geojson.String != "" {
				geometry = json.RawMessage(geojson.String)
			} else {
				// Create a point for rivers without geometry
				geometry = json.RawMessage(fmt.Sprintf(`{"type":"Point","coordinates":[%f,%f]}`, lon, lat))
			}

			fc.Features = append(fc.Features, GeoJSONFeature{
				Type:     "Feature",
				Geometry: geometry,
				Properties: map[string]interface{}{
					"feature_type":   "river",
					"feature_id":     fmt.Sprintf("river_%d", id),
					"name":           name,
					"waterbody_type": placeType,
					"lat":            lat,
					"lon":            lon,
				},
			})
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(fc)
}

// handleRiverFeatures returns GeoJSON features for HydroRIVERS data
func (s *Server) handleRiverFeatures(w http.ResponseWriter, parkID string, limitStr string) {
	limit := 500
	if limitStr != "" {
		if l, err := strconv.Atoi(limitStr); err == nil && l > 0 && l <= 2000 {
			limit = l
		}
	}

	type GeoJSONFeature struct {
		Type       string                 `json:"type"`
		Geometry   json.RawMessage        `json:"geometry"`
		Properties map[string]interface{} `json:"properties"`
	}

	type FeatureCollection struct {
		Type     string           `json:"type"`
		Features []GeoJSONFeature `json:"features"`
	}

	fc := FeatureCollection{
		Type:     "FeatureCollection",
		Features: []GeoJSONFeature{},
	}

	// Get rivers from park_rivers_hydro
	rows, err := s.DB.Query(`
		SELECT hyriv_id, COALESCE(name, ''), stream_order, ord_flow, length_km, lat, lon, geojson
		FROM park_rivers_hydro
		WHERE park_id = ? AND geojson IS NOT NULL
		ORDER BY stream_order DESC, length_km DESC
		LIMIT ?
	`, parkID, limit)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	for rows.Next() {
		var hyrivID int
		var name string
		var streamOrder, ordFlow int
		var lengthKm, lat, lon float64
		var geojson string

		if err := rows.Scan(&hyrivID, &name, &streamOrder, &ordFlow, &lengthKm, &lat, &lon, &geojson); err != nil {
			continue
		}

		fc.Features = append(fc.Features, GeoJSONFeature{
			Type:     "Feature",
			Geometry: json.RawMessage(geojson),
			Properties: map[string]interface{}{
				"feature_type":  "river",
				"feature_id":    fmt.Sprintf("river_%d", hyrivID),
				"hyriv_id":      hyrivID,
				"name":          name,
				"stream_order":  streamOrder,
				"ord_flow":      ordFlow,
				"length_km":     lengthKm,
				"lat":           lat,
				"lon":           lon,
			},
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(fc)
}

// handleRoadFeatures returns GeoJSON features for HeiGIT roads
func (s *Server) handleRoadFeatures(w http.ResponseWriter, parkID string, limitStr string) {
	limit := 500
	if limitStr != "" {
		if l, err := strconv.Atoi(limitStr); err == nil && l > 0 && l <= 2000 {
			limit = l
		}
	}

	type GeoJSONFeature struct {
		Type       string                 `json:"type"`
		Geometry   json.RawMessage        `json:"geometry"`
		Properties map[string]interface{} `json:"properties"`
	}

	type FeatureCollection struct {
		Type     string           `json:"type"`
		Features []GeoJSONFeature `json:"features"`
	}

	fc := FeatureCollection{
		Type:     "FeatureCollection",
		Features: []GeoJSONFeature{},
	}

	// Get roads from roads_heigit
	rows, err := s.DB.Query(`
		SELECT osm_id, COALESCE(name, ''), highway_type, COALESCE(surface, ''), 
		       COALESCE(passability, ''), length_km, geojson
		FROM roads_heigit
		WHERE park_id = ? AND geojson IS NOT NULL
		ORDER BY length_km DESC
		LIMIT ?
	`, parkID, limit)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	for rows.Next() {
		var osmID, name, highwayType, surface, passability string
		var lengthKm float64
		var geojson string

		if err := rows.Scan(&osmID, &name, &highwayType, &surface, &passability, &lengthKm, &geojson); err != nil {
			continue
		}

		fc.Features = append(fc.Features, GeoJSONFeature{
			Type:     "Feature",
			Geometry: json.RawMessage(geojson),
			Properties: map[string]interface{}{
				"feature_type":  "road",
				"feature_id":    fmt.Sprintf("road_%s", osmID),
				"osm_id":        osmID,
				"name":          name,
				"highway_type":  highwayType,
				"surface":       surface,
				"passability":   passability,
				"length_km":     lengthKm,
			},
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(fc)
}

// HandleAPIParkFeatureStats returns summary statistics for features in a park
func (s *Server) HandleAPIParkFeatureStats(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	
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
	
	type FeatureStats struct {
		FireTrajectories    int            `json:"fire_trajectories"`
		Settlements         int            `json:"settlements"`
		DeforestationEvents int            `json:"deforestation_events"`
		RoadSegments        int            `json:"road_segments"`
		Places              int            `json:"places"`
		Waterbodies         int            `json:"waterbodies"`
		Rivers              int            `json:"rivers"`
		SettlementsByClass  map[string]int `json:"settlements_by_class,omitempty"`
		DeforestByClass     map[string]int `json:"deforestation_by_class,omitempty"`
	}
	
	stats := FeatureStats{
		SettlementsByClass: make(map[string]int),
		DeforestByClass:    make(map[string]int),
	}
	
	// Count by feature type
	rows, err := s.DB.Query(`
		SELECT feature_type, COUNT(*) 
		FROM feature_geometries 
		WHERE park_id = ? 
		GROUP BY feature_type
	`, internalID)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var fType string
			var count int
			if rows.Scan(&fType, &count) == nil {
				switch fType {
				case "fire_trajectory":
					stats.FireTrajectories = count
				case "settlement":
					stats.Settlements = count
				case "deforestation":
					stats.DeforestationEvents = count
				case "road":
					stats.RoadSegments = count
				}
			}
		}
	}
	
	// Count places from osm_places
	var placesCount int
	s.DB.QueryRow(`SELECT COUNT(*) FROM osm_places WHERE park_id = ?`, internalID).Scan(&placesCount)
	stats.Places = placesCount
	
	// Count waterbodies
	var waterbodyCount int
	s.DB.QueryRow(`SELECT COUNT(*) FROM park_waterbodies WHERE park_id = ?`, internalID).Scan(&waterbodyCount)
	stats.Waterbodies = waterbodyCount
	
	// Count rivers from park_rivers
	var riverCount int
	s.DB.QueryRow(`SELECT COUNT(*) FROM park_rivers WHERE park_id = ?`, internalID).Scan(&riverCount)
	stats.Rivers = riverCount

	// Settlement classifications
	rows2, err := s.DB.Query(`
		SELECT classification, COUNT(*) 
		FROM park_settlements 
		WHERE park_id = ? AND classification != 'unclassified'
		GROUP BY classification
	`, internalID)
	if err == nil {
		defer rows2.Close()
		for rows2.Next() {
			var class string
			var count int
			if rows2.Scan(&class, &count) == nil {
				stats.SettlementsByClass[class] = count
			}
		}
	}
	
	// Deforestation classifications
	rows3, err := s.DB.Query(`
		SELECT classification, COUNT(*) 
		FROM deforestation_clusters 
		WHERE park_id = ? AND classification != 'unclassified'
		GROUP BY classification
	`, internalID)
	if err == nil {
		defer rows3.Close()
		for rows3.Next() {
			var class string
			var count int
			if rows3.Scan(&class, &count) == nil {
				stats.DeforestByClass[class] = count
			}
		}
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(stats)
}

// findNearestOSMPlace finds the nearest OSM place to the given coordinates within maxDistKm.
// This searches globally across all parks in the osm_places table.
func (s *Server) findNearestOSMPlace(lat, lon, maxDistKm float64) (name string, found bool) {
	// Convert km to approximate degrees (1 degree ~= 111km)
	maxDistDeg := maxDistKm / 111.0

	// Query osm_places within a bounding box
	rows, err := s.DB.Query(`
		SELECT name, lat, lon
		FROM osm_places
		WHERE lat BETWEEN ? AND ?
		  AND lon BETWEEN ? AND ?
		  AND name != ''
		LIMIT 100
	`, lat-maxDistDeg, lat+maxDistDeg, lon-maxDistDeg, lon+maxDistDeg)
	if err != nil {
		return "", false
	}
	defer rows.Close()

	var bestName string
	var bestDist float64 = maxDistKm + 1

	for rows.Next() {
		var placeName string
		var placeLat, placeLon float64
		if err := rows.Scan(&placeName, &placeLat, &placeLon); err != nil {
			continue
		}

		// Calculate distance using Haversine formula
		dist := haversineDistance(lat, lon, placeLat, placeLon)
		if dist < bestDist {
			bestDist = dist
			bestName = placeName
		}
	}

	if bestName != "" && bestDist <= maxDistKm {
		return bestName, true
	}
	return "", false
}

// findCountryByPoint finds the country that contains the given coordinates.
// Uses GADMStore's bounding boxes for a rough check.
func (s *Server) findCountryByPoint(lat, lon float64) (countryName string, found bool) {
	if s.GADMStore == nil {
		return "", false
	}

	// Check each country's bounding box
	for _, c := range s.GADMStore.Countries {
		if len(c.BBox) >= 4 {
			// BBox format: [minLon, minLat, maxLon, maxLat]
			minLon, minLat := c.BBox[0], c.BBox[1]
			maxLon, maxLat := c.BBox[2], c.BBox[3]
			if lat >= minLat && lat <= maxLat && lon >= minLon && lon <= maxLon {
				return c.Name, true
			}
		}
	}
	return "", false
}


// Helper functions for coordinate formatting
func absFloat(f float64) float64 {
	if f < 0 {
		return -f
	}
	return f
}

func latDir(lat float64) string {
	if lat >= 0 {
		return "N"
	}
	return "S"
}

func lonDir(lon float64) string {
	if lon >= 0 {
		return "E"
	}
	return "W"
}

// HandleAPIGPXUploadLogs returns GPX upload processing logs for admin panel
// GET /api/admin/gpx-logs
func (s *Server) HandleAPIGPXUploadLogs(w http.ResponseWriter, r *http.Request) {
	// Parse pagination
	limitStr := r.URL.Query().Get("limit")
	offsetStr := r.URL.Query().Get("offset")
	parkID := r.URL.Query().Get("park_id")
	
	limit := int64(50)
	offset := int64(0)
	if l, err := strconv.ParseInt(limitStr, 10, 64); err == nil && l > 0 && l <= 200 {
		limit = l
	}
	if o, err := strconv.ParseInt(offsetStr, 10, 64); err == nil && o >= 0 {
		offset = o
	}
	
	q := dbgen.New(s.DB)
	ctx := r.Context()
	
	var logsResult interface{}
	var err error
	
	if parkID != "" {
		logsResult, err = q.ListGPXUploadLogsByPark(ctx, dbgen.ListGPXUploadLogsByParkParams{
			ProtectedAreaID: &parkID,
			Limit:           limit,
			Offset:          offset,
		})
	} else {
		logsResult, err = q.ListGPXUploadLogs(ctx, dbgen.ListGPXUploadLogsParams{
			Limit:  limit,
			Offset: offset,
		})
	}
	
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	
	// Get summary stats for last 30 days
	thirtyDaysAgo := time.Now().AddDate(0, 0, -30)
	stats, _ := q.GetGPXUploadLogStats(ctx, thirtyDaysAgo)
	
	response := struct {
		Logs  interface{}                    `json:"logs"`
		Stats *dbgen.GetGPXUploadLogStatsRow `json:"stats,omitempty"`
	}{
		Logs:  logsResult,
		Stats: &stats,
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// HandleAPILearningResults returns GPX learning results for admin panel
// GET /api/admin/learning-results
func (s *Server) HandleAPILearningResults(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)

	parkID := r.URL.Query().Get("park_id")
	limitStr := r.URL.Query().Get("limit")
	offsetStr := r.URL.Query().Get("offset")

	limit := int64(50)
	if l, err := strconv.ParseInt(limitStr, 10, 64); err == nil && l > 0 && l <= 100 {
		limit = l
	}
	offset := int64(0)
	if o, err := strconv.ParseInt(offsetStr, 10, 64); err == nil && o >= 0 {
		offset = o
	}

	var results []dbgen.GpxLearningResult
	var err error

	if parkID != "" {
		results, err = q.GetLearningResultsByPark(ctx, dbgen.GetLearningResultsByParkParams{
			ParkID: parkID,
			Limit:  limit,
		})
	} else {
		results, err = q.GetAllLearningResults(ctx, dbgen.GetAllLearningResultsParams{
			Limit:  limit,
			Offset: offset,
		})
	}

	if err != nil {
		http.Error(w, "Failed to get learning results", http.StatusInternalServerError)
		return
	}

	// Get aggregate stats
	var stats struct {
		TotalResults     int     `json:"total_results"`
		TotalNewRoads    int     `json:"total_new_roads"`
		TotalNewRoadsKm  float64 `json:"total_new_roads_km"`
		TotalNewPlaces   int     `json:"total_new_places"`
		TotalNewAirstrips int    `json:"total_new_airstrips"`
	}

	for _, r := range results {
		stats.TotalResults++
		if r.NewRoadsFound != nil {
			stats.TotalNewRoads += int(*r.NewRoadsFound)
		}
		if r.NewRoadsKm != nil {
			stats.TotalNewRoadsKm += *r.NewRoadsKm
		}
		if r.NewPlacesFound != nil {
			stats.TotalNewPlaces += int(*r.NewPlacesFound)
		}
		if r.NewAirstripsFound != nil {
			stats.TotalNewAirstrips += int(*r.NewAirstripsFound)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"results": results,
		"stats":   stats,
	})
}

// HandleAPIPendingApprovals returns all pending features across all parks
// GET /api/admin/pending-approvals
func (s *Server) HandleAPIPendingApprovals(w http.ResponseWriter, r *http.Request) {
	limitStr := r.URL.Query().Get("limit")
	limit := 100
	if l, err := strconv.Atoi(limitStr); err == nil && l > 0 && l <= 500 {
		limit = l
	}
	
	type PendingFeature struct {
		Type          string   `json:"type"`
		ID            int64    `json:"id"`
		ParkID        string   `json:"park_id"`
		Lat           *float64 `json:"lat"`
		Lon           *float64 `json:"lon"`
		ConfidencePct *float64 `json:"confidence_pct"`
		Details       string   `json:"details"`
		CreatedAt     string   `json:"created_at"`
	}
	
	var features []PendingFeature
	
	// Get pending roads
	rows, err := s.DB.Query(`
		SELECT 'road' as type, id, park_id,
		       json_extract(geojson, '$.coordinates[0][1]') as lat,
		       json_extract(geojson, '$.coordinates[0][0]') as lon,
		       confidence_pct, 
		       COALESCE(printf('%.1f km, %d matches', length_m/1000.0, match_count), 'Unknown'),
		       datetime(created_at) as created_at
		FROM learned_roads WHERE status = 'pending'
		UNION ALL
		SELECT 'airstrip' as type, id, park_id, lat, lon, confidence_pct,
		       COALESCE(printf('%s, %d landings', aircraft_type, landing_count), 'Unknown'),
		       datetime(created_at) as created_at
		FROM learned_airstrips WHERE status = 'pending'
		UNION ALL
		SELECT 'place' as type, id, park_id, lat, lon, confidence_pct,
		       COALESCE(printf('%s, %d visits', place_type, visit_count), 'Unknown'),
		       datetime(created_at) as created_at
		FROM learned_places WHERE status = 'pending'
		ORDER BY confidence_pct DESC
		LIMIT ?
	`, limit)
	
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	
	for rows.Next() {
		var f PendingFeature
		if err := rows.Scan(&f.Type, &f.ID, &f.ParkID, &f.Lat, &f.Lon, &f.ConfidencePct, &f.Details, &f.CreatedAt); err != nil {
			continue
		}
		features = append(features, f)
	}
	
	// Get stats
	var stats struct {
		PendingRoads     int `json:"pending_roads"`
		PendingPlaces    int `json:"pending_places"`
		PendingAirstrips int `json:"pending_airstrips"`
		HighConfidence   int `json:"high_confidence"`
	}
	
	s.DB.QueryRow(`SELECT COUNT(*) FROM learned_roads WHERE status = 'pending'`).Scan(&stats.PendingRoads)
	s.DB.QueryRow(`SELECT COUNT(*) FROM learned_places WHERE status = 'pending'`).Scan(&stats.PendingPlaces)
	s.DB.QueryRow(`SELECT COUNT(*) FROM learned_airstrips WHERE status = 'pending'`).Scan(&stats.PendingAirstrips)
	s.DB.QueryRow(`
		SELECT COUNT(*) FROM (
			SELECT 1 FROM learned_roads WHERE status = 'pending' AND confidence_pct > 75
			UNION ALL SELECT 1 FROM learned_places WHERE status = 'pending' AND confidence_pct > 75
			UNION ALL SELECT 1 FROM learned_airstrips WHERE status = 'pending' AND confidence_pct > 75
		)
	`).Scan(&stats.HighConfidence)
	
	// Per-park data coverage (time range + points behind the learned features)
	coverage := make(map[string]interface{})
	seenParks := make(map[string]bool)
	for _, f := range features {
		if f.ParkID != "" && !seenParks[f.ParkID] {
			seenParks[f.ParkID] = true
			if cov := s.learnedCoverageForPark(f.ParkID); cov != nil {
				coverage[f.ParkID] = cov
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"features": features,
		"stats":    stats,
		"coverage": coverage,
	})
}

// HandleAPILearnedFeatures returns pending/approved learned features for a park
// GET /api/admin/learned-features?park_id=xxx&type=roads|places|airstrips
func (s *Server) HandleAPILearnedFeatures(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)

	parkID := r.URL.Query().Get("park_id")
	featureType := r.URL.Query().Get("type")

	if parkID == "" {
		http.Error(w, "park_id required", http.StatusBadRequest)
		return
	}

	response := make(map[string]interface{})

	switch featureType {
	case "roads":
		roads, err := q.GetLearnedRoadsByPark(ctx, parkID)
		if err == nil {
			response["roads"] = roads
		}
	case "places":
		places, err := q.GetLearnedPlacesByPark(ctx, parkID)
		if err == nil {
			response["places"] = places
		}
	case "airstrips":
		airstrips, err := q.GetLearnedAirstripsByPark(ctx, parkID)
		if err == nil {
			response["airstrips"] = airstrips
		}
	default:
		// Return all types
		roads, _ := q.GetLearnedRoadsByPark(ctx, parkID)
		places, _ := q.GetLearnedPlacesByPark(ctx, parkID)
		airstrips, _ := q.GetLearnedAirstripsByPark(ctx, parkID)
		stats, _ := q.GetVehicleStatsByPark(ctx, parkID)
		
		response["roads"] = roads
		response["places"] = places
		response["airstrips"] = airstrips
		response["vehicle_stats"] = stats
	}

	if cov := s.learnedCoverageForPark(parkID); cov != nil {
		response["coverage"] = cov
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// HandleAPIApproveLearnedFeature approves a learned feature
// POST /api/admin/approve-feature
func (s *Server) HandleAPIApproveLearnedFeature(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)

	var req struct {
		Type string `json:"type"` // road, place, airstrip
		ID   int64  `json:"id"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	user := s.Auth.GetUserFromRequest(r)
	approvedBy := "admin"
	if user != nil && user.Email != "" {
		approvedBy = user.Email
	}

	// Record history before changing state
	var err error
	switch req.Type {
	case "road":
		q.RecordRoadHistory(ctx, dbgen.RecordRoadHistoryParams{
			Action:   "approve",
			ActionBy: &approvedBy,
			ID:       req.ID,
		})
		err = q.ApproveRoad(ctx, dbgen.ApproveRoadParams{
			ApprovedBy: &approvedBy,
			ID:         req.ID,
		})
	case "place":
		q.RecordPlaceHistory(ctx, dbgen.RecordPlaceHistoryParams{
			Action:   "approve",
			ActionBy: &approvedBy,
			ID:       req.ID,
		})
		err = q.ApprovePlace(ctx, dbgen.ApprovePlaceParams{
			ApprovedBy: &approvedBy,
			ID:         req.ID,
		})
	case "airstrip":
		q.RecordAirstripHistory(ctx, dbgen.RecordAirstripHistoryParams{
			Action:   "approve",
			ActionBy: &approvedBy,
			ID:       req.ID,
		})
		err = q.ApproveAirstrip(ctx, dbgen.ApproveAirstripParams{
			ApprovedBy: &approvedBy,
			ID:         req.ID,
		})
	default:
		http.Error(w, "Invalid type", http.StatusBadRequest)
		return
	}

	if err != nil {
		http.Error(w, "Failed to approve feature", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "approved"})
}

// HandleAPIRejectLearnedFeature rejects a learned feature
// POST /api/admin/reject-feature
func (s *Server) HandleAPIRejectLearnedFeature(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)

	var req struct {
		Type string `json:"type"`
		ID   int64  `json:"id"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	user := s.Auth.GetUserFromRequest(r)
	rejectedBy := "admin"
	if user != nil && user.Email != "" {
		rejectedBy = user.Email
	}

	// Record history before rejection
	var err error
	switch req.Type {
	case "road":
		q.RecordRoadHistory(ctx, dbgen.RecordRoadHistoryParams{
			Action:   "reject",
			ActionBy: &rejectedBy,
			ID:       req.ID,
		})
		err = q.RejectRoad(ctx, dbgen.RejectRoadParams{
			ApprovedBy: &rejectedBy,
			ID:         req.ID,
		})
	case "place":
		q.RecordPlaceHistory(ctx, dbgen.RecordPlaceHistoryParams{
			Action:   "reject",
			ActionBy: &rejectedBy,
			ID:       req.ID,
		})
		err = q.RejectPlace(ctx, dbgen.RejectPlaceParams{
			ApprovedBy: &rejectedBy,
			ID:         req.ID,
		})
	case "airstrip":
		q.RecordAirstripHistory(ctx, dbgen.RecordAirstripHistoryParams{
			Action:   "reject",
			ActionBy: &rejectedBy,
			ID:       req.ID,
		})
		err = q.RejectAirstrip(ctx, dbgen.RejectAirstripParams{
			ApprovedBy: &rejectedBy,
			ID:         req.ID,
		})
	default:
		http.Error(w, "Invalid type", http.StatusBadRequest)
		return
	}

	if err != nil {
		http.Error(w, "Failed to reject feature", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "rejected"})
}

// HandleAPIBulkApprove approves multiple learned features at once
// POST /api/admin/bulk-approve
func (s *Server) HandleAPIBulkApprove(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)

	var req struct {
		Items []struct {
			Type string `json:"type"`
			ID   int64  `json:"id"`
		} `json:"items"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	user := s.Auth.GetUserFromRequest(r)
	approvedBy := "admin"
	if user != nil && user.Email != "" {
		approvedBy = user.Email
	}

	approved := 0
	for _, item := range req.Items {
		var err error
		switch item.Type {
		case "road":
			q.RecordRoadHistory(ctx, dbgen.RecordRoadHistoryParams{
				Action: "approve", ActionBy: &approvedBy, ID: item.ID,
			})
			err = q.ApproveRoad(ctx, dbgen.ApproveRoadParams{ApprovedBy: &approvedBy, ID: item.ID})
		case "place":
			q.RecordPlaceHistory(ctx, dbgen.RecordPlaceHistoryParams{
				Action: "approve", ActionBy: &approvedBy, ID: item.ID,
			})
			err = q.ApprovePlace(ctx, dbgen.ApprovePlaceParams{ApprovedBy: &approvedBy, ID: item.ID})
		case "airstrip":
			q.RecordAirstripHistory(ctx, dbgen.RecordAirstripHistoryParams{
				Action: "approve", ActionBy: &approvedBy, ID: item.ID,
			})
			err = q.ApproveAirstrip(ctx, dbgen.ApproveAirstripParams{ApprovedBy: &approvedBy, ID: item.ID})
		}
		if err == nil {
			approved++
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]int{"approved": approved})
}

// HandleAPIApproveHighConfidence approves all pending learned features above a
// confidence threshold.
// POST /api/admin/approve-high-confidence {"threshold": 75}
func (s *Server) HandleAPIApproveHighConfidence(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)

	var req struct {
		Threshold float64 `json:"threshold"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}
	if req.Threshold <= 0 {
		req.Threshold = 75
	}

	user := s.Auth.GetUserFromRequest(r)
	approvedBy := "admin"
	if user != nil && user.Email != "" {
		approvedBy = user.Email
	}

	approved := 0
	for _, t := range []struct {
		table string
		kind  string
	}{
		{"learned_roads", "road"},
		{"learned_places", "place"},
		{"learned_airstrips", "airstrip"},
	} {
		rows, err := s.DB.QueryContext(ctx,
			"SELECT id FROM "+t.table+" WHERE status = 'pending' AND confidence_pct > ?", req.Threshold)
		if err != nil {
			continue
		}
		var ids []int64
		for rows.Next() {
			var id int64
			if rows.Scan(&id) == nil {
				ids = append(ids, id)
			}
		}
		rows.Close()

		for _, id := range ids {
			var err error
			switch t.kind {
			case "road":
				q.RecordRoadHistory(ctx, dbgen.RecordRoadHistoryParams{Action: "approve", ActionBy: &approvedBy, ID: id})
				err = q.ApproveRoad(ctx, dbgen.ApproveRoadParams{ApprovedBy: &approvedBy, ID: id})
			case "place":
				q.RecordPlaceHistory(ctx, dbgen.RecordPlaceHistoryParams{Action: "approve", ActionBy: &approvedBy, ID: id})
				err = q.ApprovePlace(ctx, dbgen.ApprovePlaceParams{ApprovedBy: &approvedBy, ID: id})
			case "airstrip":
				q.RecordAirstripHistory(ctx, dbgen.RecordAirstripHistoryParams{Action: "approve", ActionBy: &approvedBy, ID: id})
				err = q.ApproveAirstrip(ctx, dbgen.ApproveAirstripParams{ApprovedBy: &approvedBy, ID: id})
			}
			if err == nil {
				approved++
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]int{"approved": approved})
}

// HandleAPIBulkReject rejects multiple learned features at once
// POST /api/admin/bulk-reject
func (s *Server) HandleAPIBulkReject(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)

	var req struct {
		Items []struct {
			Type string `json:"type"`
			ID   int64  `json:"id"`
		} `json:"items"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	user := s.Auth.GetUserFromRequest(r)
	rejectedBy := "admin"
	if user != nil && user.Email != "" {
		rejectedBy = user.Email
	}

	rejected := 0
	for _, item := range req.Items {
		var err error
		switch item.Type {
		case "road":
			q.RecordRoadHistory(ctx, dbgen.RecordRoadHistoryParams{
				Action: "reject", ActionBy: &rejectedBy, ID: item.ID,
			})
			err = q.RejectRoad(ctx, dbgen.RejectRoadParams{ApprovedBy: &rejectedBy, ID: item.ID})
		case "place":
			q.RecordPlaceHistory(ctx, dbgen.RecordPlaceHistoryParams{
				Action: "reject", ActionBy: &rejectedBy, ID: item.ID,
			})
			err = q.RejectPlace(ctx, dbgen.RejectPlaceParams{ApprovedBy: &rejectedBy, ID: item.ID})
		case "airstrip":
			q.RecordAirstripHistory(ctx, dbgen.RecordAirstripHistoryParams{
				Action: "reject", ActionBy: &rejectedBy, ID: item.ID,
			})
			err = q.RejectAirstrip(ctx, dbgen.RejectAirstripParams{ApprovedBy: &rejectedBy, ID: item.ID})
		}
		if err == nil {
			rejected++
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]int{"rejected": rejected})
}

// HandleAPIBulkDeleteUploads deletes multiple GPX uploads at once.
// POST /api/admin/bulk-delete-uploads
func (s *Server) HandleAPIBulkDeleteUploads(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	var req struct {
		IDs []int64 `json:"ids"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}
	if len(req.IDs) == 0 {
		http.Error(w, "No IDs provided", http.StatusBadRequest)
		return
	}

	deleted := 0
	for _, id := range req.IDs {
		var uploadID sql.NullInt64
		_ = s.DB.QueryRowContext(ctx, "SELECT upload_id FROM gpx_upload_logs WHERE id = ?", id).Scan(&uploadID)

		_, err := s.DB.ExecContext(ctx, "DELETE FROM gpx_upload_logs WHERE id = ?", id)
		if err != nil {
			continue
		}
		deleted++

		if uploadID.Valid {
			_, _ = s.DB.ExecContext(ctx, "DELETE FROM track_points WHERE upload_id = ?", uploadID.Int64)
			_, _ = s.DB.ExecContext(ctx, "DELETE FROM gpx_uploads WHERE id = ?", uploadID.Int64)
		}

		_, _ = s.DB.ExecContext(ctx, "DELETE FROM notifications WHERE notification_type = 'new_upload' AND reference_id = ?", fmt.Sprintf("%d", id))
	}

	// Clean up orphans
	_, _ = s.DB.ExecContext(ctx, `
		DELETE FROM track_points WHERE upload_id IN (
			SELECT u.id FROM gpx_uploads u
			LEFT JOIN gpx_upload_logs l ON l.upload_id = u.id
			WHERE l.id IS NULL
		)`)
	_, _ = s.DB.ExecContext(ctx, `
		DELETE FROM gpx_uploads WHERE id NOT IN (
			SELECT upload_id FROM gpx_upload_logs WHERE upload_id IS NOT NULL
		)`)

	go s.rebuildAllEffortData()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]int{"deleted": deleted})
}

// HandleAPIDeleteUpload deletes a GPX upload and its logs, then rebuilds effort_data.
// POST /api/admin/delete-upload
func (s *Server) HandleAPIDeleteUpload(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	var req struct {
		ID int64 `json:"id"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	// Look up the upload_id from the log so we can clean up related tables
	var uploadID sql.NullInt64
	_ = s.DB.QueryRowContext(ctx, "SELECT upload_id FROM gpx_upload_logs WHERE id = ?", req.ID).Scan(&uploadID)

	// Delete from gpx_upload_logs
	_, err := s.DB.ExecContext(ctx, "DELETE FROM gpx_upload_logs WHERE id = ?", req.ID)
	if err != nil {
		http.Error(w, "Failed to delete upload", http.StatusInternalServerError)
		return
	}

	// Clean up related tables if there was an associated gpx_uploads record
	if uploadID.Valid {
		_, _ = s.DB.ExecContext(ctx, "DELETE FROM track_points WHERE upload_id = ?", uploadID.Int64)
		_, _ = s.DB.ExecContext(ctx, "DELETE FROM gpx_uploads WHERE id = ?", uploadID.Int64)
	}

	// Clean up orphan gpx_uploads — records not referenced by any log entry.
	// These can accumulate when uploads are re-processed or logs deleted.
	_, _ = s.DB.ExecContext(ctx, `
		DELETE FROM track_points WHERE upload_id IN (
			SELECT u.id FROM gpx_uploads u
			LEFT JOIN gpx_upload_logs l ON l.upload_id = u.id
			WHERE l.id IS NULL
		)`)
	_, _ = s.DB.ExecContext(ctx, `
		DELETE FROM gpx_uploads WHERE id NOT IN (
			SELECT upload_id FROM gpx_upload_logs WHERE upload_id IS NOT NULL
		)`)

	// Clean up associated notification
	_, _ = s.DB.ExecContext(ctx, "DELETE FROM notifications WHERE notification_type = 'new_upload' AND reference_id = ?", fmt.Sprintf("%d", req.ID))

	// Trigger async effort_data rebuild from remaining uploads
	go s.rebuildAllEffortData()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "deleted"})
}

// HandleAPIUploadDetail returns detailed information about a GPX upload
// GET /api/admin/upload-detail?id=123
func (s *Server) HandleAPIUploadDetail(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	idStr := r.URL.Query().Get("id")
	if idStr == "" {
		http.Error(w, "Missing id parameter", http.StatusBadRequest)
		return
	}

	uploadID, err := strconv.ParseInt(idStr, 10, 64)
	if err != nil {
		http.Error(w, "Invalid id", http.StatusBadRequest)
		return
	}

	// Get upload log details
	var result struct {
		ID                 int64   `json:"id"`
		Filename           string  `json:"filename"`
		UploadTime         string  `json:"upload_time"`
		TotalPoints        int64   `json:"total_points"`
		ProtectedAreaID    *string `json:"protected_area_id"`
		ProtectedAreaName  *string `json:"protected_area_name"`
		ProcessingStatus   string  `json:"processing_status"`
		RejectionReason    *string `json:"rejection_reason"`
		ValidationErrors   *string `json:"validation_errors"`
		ValidationWarnings *string `json:"validation_warnings"`

		// Distance stats
		PatrolKm   float64 `json:"patrol_km"`
		RoadKm     float64 `json:"road_km"`
		BoundaryKm float64 `json:"boundary_km"`
		ExcludedKm float64 `json:"excluded_km"`

		// Segment counts
		TotalSegments    int64 `json:"total_segments"`
		PatrolSegments   int64 `json:"patrol_segments"`
		StaticSegments   int64 `json:"static_segments"`
		ExcludedSegments int64 `json:"excluded_segments"`

		// Movement type breakdown
		FootSegments     int64   `json:"foot_segments"`
		FootKm           float64 `json:"foot_km"`
		FootMinutes      float64 `json:"foot_minutes"`
		VehicleSegments  int64   `json:"vehicle_segments"`
		VehicleKm        float64 `json:"vehicle_km"`
		VehicleMinutes   float64 `json:"vehicle_minutes"`
		AircraftSegments int64   `json:"aircraft_segments"`
		AircraftKm       float64 `json:"aircraft_km"`
		AircraftMinutes  float64 `json:"aircraft_minutes"`

		// Activity breakdown
		LogisticsSegments int64   `json:"logistics_segments"`
		LogisticsKm       float64 `json:"logistics_km"`
		TransitSegments   int64   `json:"transit_segments"`
		TransitKm         float64 `json:"transit_km"`

		// Classified segments JSON
		ClassifiedSegmentsJSON *string `json:"classified_segments_json"`
	}

	err = s.DB.QueryRowContext(ctx, `
		SELECT 
			id, filename, upload_time, total_points,
			protected_area_id, protected_area_name,
			processing_status, rejection_reason,
			validation_errors, validation_warnings,
			patrol_km, road_km, boundary_km, excluded_km,
			total_segments, patrol_segments, static_segments, excluded_segments,
			foot_segments, foot_km, foot_minutes,
			vehicle_segments, vehicle_km, vehicle_minutes,
			aircraft_segments, aircraft_km, aircraft_minutes,
			COALESCE(logistics_segments, 0), COALESCE(logistics_km, 0),
			COALESCE(transit_segments, 0), COALESCE(transit_km, 0),
			classified_segments_json
		FROM gpx_upload_logs
		WHERE id = ?
	`, uploadID).Scan(
		&result.ID, &result.Filename, &result.UploadTime, &result.TotalPoints,
		&result.ProtectedAreaID, &result.ProtectedAreaName,
		&result.ProcessingStatus, &result.RejectionReason,
		&result.ValidationErrors, &result.ValidationWarnings,
		&result.PatrolKm, &result.RoadKm, &result.BoundaryKm, &result.ExcludedKm,
		&result.TotalSegments, &result.PatrolSegments, &result.StaticSegments, &result.ExcludedSegments,
		&result.FootSegments, &result.FootKm, &result.FootMinutes,
		&result.VehicleSegments, &result.VehicleKm, &result.VehicleMinutes,
		&result.AircraftSegments, &result.AircraftKm, &result.AircraftMinutes,
		&result.LogisticsSegments, &result.LogisticsKm,
		&result.TransitSegments, &result.TransitKm,
		&result.ClassifiedSegmentsJSON,
	)
	if err == sql.ErrNoRows {
		http.Error(w, "Upload not found", http.StatusNotFound)
		return
	}
	if err != nil {
		slog.Error("failed to fetch upload detail", "error", err)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(result)
}

// HandleAPIHideNotification hides a notification
// POST /api/admin/hide-notification
func (s *Server) HandleAPIHideNotification(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	var req struct {
		ID int64 `json:"id"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	// Mark notification as hidden (we'd need a hidden column, for now just delete)
	_, err := s.DB.ExecContext(ctx, "DELETE FROM gpx_upload_logs WHERE id = ?", req.ID)
	if err != nil {
		http.Error(w, "Failed to hide notification", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "hidden"})
}

// HandleAPIPatrolMCP returns the 90% MCP for patrol coverage
// GET /api/parks/{id}/patrol-mcp
func (s *Server) HandleAPIPatrolMCP(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Missing park ID", http.StatusBadRequest)
		return
	}

	ctx := r.Context()
	q := dbgen.New(s.DB)

	mcp, err := q.GetPatrolMCP(ctx, parkID)
	if err != nil {
		// Return empty MCP if none exists
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"park_id": parkID,
			"mcp_geojson": nil,
			"mcp_area_km2": 0,
			"point_count": 0,
			"message": "No patrol data available for MCP calculation",
		})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"park_id":      parkID,
		"mcp_geojson":  mcp.Mcp90Geojson,
		"mcp_area_km2": mcp.McpAreaKm2,
		"point_count":  mcp.PointCount,
		"updated_at":   mcp.UpdatedAt,
	})
}

// HandleAPIFeatureHistory returns the history of changes for a learned feature
// GET /api/admin/feature-history?type={road|airstrip|place}&id={id}
func (s *Server) HandleAPIFeatureHistory(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)

	featureType := r.URL.Query().Get("type")
	idStr := r.URL.Query().Get("id")
	id, err := strconv.ParseInt(idStr, 10, 64)
	if err != nil {
		http.Error(w, "Invalid ID", http.StatusBadRequest)
		return
	}

	var history interface{}

	switch featureType {
	case "road":
		history, err = q.GetRoadHistory(ctx, id)
	case "airstrip":
		history, err = q.GetAirstripHistory(ctx, id)
	case "place":
		history, err = q.GetPlaceHistory(ctx, id)
	default:
		http.Error(w, "Invalid type", http.StatusBadRequest)
		return
	}

	if err != nil {
		http.Error(w, "Failed to get history", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"type":    featureType,
		"id":      id,
		"history": history,
	})
}

// HandleAPIRollbackFeature rolls back a feature to a previous version
// POST /api/admin/rollback-feature
func (s *Server) HandleAPIRollbackFeature(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)

	var req struct {
		Type      string `json:"type"`
		ID        int64  `json:"id"`
		HistoryID int64  `json:"history_id"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	user := s.Auth.GetUserFromRequest(r)
	actionBy := "admin"
	if user != nil && user.Email != "" {
		actionBy = user.Email
	}

	var err error
	switch req.Type {
	case "road":
		// Get the history entry
		history, histErr := q.GetRoadHistory(ctx, req.ID)
		if histErr != nil || len(history) == 0 {
			http.Error(w, "History not found", http.StatusNotFound)
			return
		}
		
		// Find the specific history entry
		var target *dbgen.LearnedRoadsHistory
		for _, h := range history {
			if h.HistoryID == req.HistoryID {
				target = &h
				break
			}
		}
		if target == nil {
			http.Error(w, "History entry not found", http.StatusNotFound)
			return
		}
		
		// Record the rollback action
		q.RecordRoadHistory(ctx, dbgen.RecordRoadHistoryParams{
			Action:   "rollback",
			ActionBy: &actionBy,
			ID:       req.ID,
		})
		
		// Restore values
		status := "pending"
		if target.IsApproved != nil && *target.IsApproved == 1 {
			status = "approved"
		} else if target.IsRejected != nil && *target.IsRejected == 1 {
			status = "rejected"
		}
		
		err = q.RollbackRoad(ctx, dbgen.RollbackRoadParams{
			Geojson:       target.Geojson,
			LengthM:       target.DistanceKm,
			ConfidencePct: target.Confidence,
			Status:        &status,
			ID:            req.ID,
		})

	default:
		http.Error(w, "Rollback only supported for roads currently", http.StatusBadRequest)
		return
	}

	if err != nil {
		slog.Error("rollback failed", "error", err)
		http.Error(w, "Failed to rollback feature", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "rolled_back"})
}

// HandleAPILearnedFeatureStats returns statistics for learned features by park
// GET /api/parks/{id}/learned-stats
func (s *Server) HandleAPILearnedFeatureStats(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Missing park ID", http.StatusBadRequest)
		return
	}

	ctx := r.Context()
	q := dbgen.New(s.DB)

	stats, err := q.GetLearnedFeatureStats(ctx, parkID)
	if err != nil {
		http.Error(w, "Failed to get stats", http.StatusInternalServerError)
		return
	}

	// Get vehicle stats too
	vehicleStats, _ := q.GetVehicleStatsByPark(ctx, parkID)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"park_id": parkID,
		"roads": map[string]interface{}{
			"approved": stats.ApprovedRoads,
			"pending":  stats.PendingRoads,
			"total_km": stats.TotalRoadKm,
		},
		"airstrips": map[string]interface{}{
			"approved": stats.ApprovedAirstrips,
			"pending":  stats.PendingAirstrips,
		},
		"places": map[string]interface{}{
			"approved": stats.ApprovedPlaces,
			"pending":  stats.PendingPlaces,
		},
		"vehicle_stats": vehicleStats,
	})
}


// StarredItems represents the structure of starred items from the client
type StarredItems struct {
	Parks       []map[string]interface{} `json:"parks"`
	Countries   []map[string]interface{} `json:"countries"`
	Bboxes      []map[string]interface{} `json:"bboxes"`
	Narratives  []map[string]interface{} `json:"narratives"`
	Activities  []map[string]interface{} `json:"activities"`
}

// fetchParkNarrativeSummary fetches a brief narrative summary for RSS feeds
func (s *Server) fetchParkNarrativeSummary(parkID string) string {
	parts := []string{}
	
	// Fetch fire narrative from cache
	var fireNarrativeJSON string
	err := s.DB.QueryRow(`SELECT narrative_json FROM fire_narrative_cache WHERE park_id = ? LIMIT 1`, parkID).Scan(&fireNarrativeJSON)
	if err == nil && fireNarrativeJSON != "" {
		// Parse JSON to get summary
		var narrativeData map[string]interface{}
		if json.Unmarshal([]byte(fireNarrativeJSON), &narrativeData) == nil {
			if summary, ok := narrativeData["summary"].(string); ok && summary != "" {
				// Truncate to first 300 chars
				if len(summary) > 300 {
					summary = summary[:300] + "..."
				}
				parts = append(parts, "FIRE: " + summary)
			}
		}
	}
	
	// Fetch deforestation stats
	var deforestKm2 float64
	var deforestCount int
	err = s.DB.QueryRow(`
		SELECT 
			COUNT(*) as event_count,
			COALESCE(SUM(area_km2), 0) as total_loss_km2
		FROM feature_geometries
		WHERE park_id = ? AND feature_type = 'deforestation'
	`, parkID).Scan(&deforestCount, &deforestKm2)
	if err == nil && deforestKm2 > 0 {
		parts = append(parts, fmt.Sprintf("DEFORESTATION: %.2f km² lost across %d events", deforestKm2, deforestCount))
	}
	
	// Fetch settlement stats
	var settlementCount int
	var totalPopulation int
	err = s.DB.QueryRow(`
		SELECT 
			COUNT(*) as count,
			COALESCE(SUM(population_est), 0) as population
		FROM park_settlements
		WHERE park_id = ?
	`, parkID).Scan(&settlementCount, &totalPopulation)
	if err == nil && settlementCount > 0 {
		parts = append(parts, fmt.Sprintf("SETTLEMENTS: %d settlements, est. population %d", settlementCount, totalPopulation))
	}
	
	return strings.Join(parts, " | ")
}

// HandleAPIFeed generates an RSS feed for starred items or notifications
// GET /api/feed?stars=<base64-encoded-starred-items> - RSS for starred reports with updates
// GET /api/feed - RSS for recent notifications (main globe page)
func (s *Server) HandleAPIFeed(w http.ResponseWriter, r *http.Request) {
	starsParam := r.URL.Query().Get("stars")
	
	// If no stars parameter, return notifications feed
	if starsParam == "" {
		s.handleNotificationsFeed(w, r)
		return
	}

	// Decode base64 starred items
	decoded, err := base64.StdEncoding.DecodeString(starsParam)
	if err != nil {
		http.Error(w, "Invalid stars encoding", http.StatusBadRequest)
		return
	}

	var starred StarredItems
	if err := json.Unmarshal(decoded, &starred); err != nil {
		http.Error(w, "Invalid stars format", http.StatusBadRequest)
		return
	}

	// Build RSS feed
	now := time.Now().UTC()
	baseURL := "https://" + r.Host
	pwd := r.URL.Query().Get("pwd")
	if pwd != "" {
		baseURL += "?pwd=" + pwd
	}

	items := []string{}

	// Add park items with recent updates/notifications
	for _, park := range starred.Parks {
		name, _ := park["name"].(string)
		id, _ := park["id"].(string)
		country, _ := park["country"].(string)
		
		if name == "" || id == "" {
			continue
		}

		link := baseURL
		if pwd != "" {
			link = baseURL + "&popup=" + id
		} else {
			link = baseURL + "?popup=" + id
		}

		// Fetch recent notifications for this park (last 7 days)
		since := now.AddDate(0, 0, -7)
		notifQuery := `SELECT notification_type, title, message, created_at 
		               FROM notifications 
		               WHERE park_id = ? AND created_at > ? 
		               ORDER BY created_at DESC LIMIT 5`
		notifRows, err := s.DB.Query(notifQuery, id, since.Format("2006-01-02 15:04:05"))
		
		updates := []string{}
		if err == nil {
			defer notifRows.Close()
			for notifRows.Next() {
				var notifType, title, message, createdAt string
				if err := notifRows.Scan(&notifType, &title, &message, &createdAt); err == nil {
					updates = append(updates, fmt.Sprintf("- %s: %s", notifType, title))
				}
			}
		}

		// Fetch full narrative data to include in RSS
	narrative := s.fetchParkNarrativeSummary(id)
	
	description := fmt.Sprintf("Conservation monitoring for %s in %s", name, country)
	if narrative != "" {
		description += "\n\n" + narrative
	}
	if len(updates) > 0 {
		description += "\n\nRecent updates:\n" + strings.Join(updates, "\n")
	}

		items = append(items, fmt.Sprintf(`
	<item>
		<title>%s - %s</title>
		<link>%s</link>
		<description>%s</description>
		<pubDate>%s</pubDate>
		<guid>park-%s-%d</guid>
	</item>`, 
			escapeXML(name), escapeXML(country),
			escapeXML(link),
			escapeXML(description),
			now.Format(time.RFC1123Z),
			escapeXML(id), now.Unix()))
	}

	// Add narrative items
	for _, narr := range starred.Narratives {
		parkName, _ := narr["parkName"].(string)
		parkId, _ := narr["parkId"].(string)
		narrType, _ := narr["type"].(string)
		
		if parkName == "" || narrType == "" {
			continue
		}

		title := fmt.Sprintf("%s - %s", parkName, narrType)
		
		link := baseURL
		if parkId != "" {
			if pwd != "" {
				link = baseURL + "&popup=" + parkId
			} else {
				link = baseURL + "?popup=" + parkId
			}
		}

		items = append(items, fmt.Sprintf(`
		<item>
			<title>%s</title>
			<link>%s</link>
			<description>%s narrative for %s</description>
			<pubDate>%s</pubDate>
			<guid>narrative-%s-%s</guid>
		</item>`,
			escapeXML(title),
			escapeXML(link),
			escapeXML(narrType), escapeXML(parkName),
			now.Format(time.RFC1123Z),
			escapeXML(parkId), escapeXML(narrType)))
	}

	// Add bbox items
	for i, bbox := range starred.Bboxes {
		coords, _ := bbox["coords"].([]interface{})
		if len(coords) != 4 {
			continue
		}
		
		bboxStr := fmt.Sprintf("%.2f,%.2f,%.2f,%.2f", coords[0], coords[1], coords[2], coords[3])
		
		link := baseURL
		if pwd != "" {
			link = baseURL + "&bbox=" + bboxStr
		} else {
			link = baseURL + "?bbox=" + bboxStr
		}

		items = append(items, fmt.Sprintf(`
		<item>
			<title>Custom Area %d</title>
			<link>%s</link>
			<description>Monitoring area: %s</description>
			<pubDate>%s</pubDate>
			<guid>bbox-%s</guid>
		</item>`,
			i+1,
			escapeXML(link),
			escapeXML(bboxStr),
			now.Format(time.RFC1123Z),
			escapeXML(bboxStr)))
	}

	rss := fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
	<channel>
		<title>5MP Conservation Monitoring</title>
		<link>%s</link>
		<description>Updates for your starred conservation areas</description>
		<lastBuildDate>%s</lastBuildDate>
		<atom:link href="%s/api/feed?stars=%s" rel="self" type="application/rss+xml"/>%s
	</channel>
</rss>`,
		baseURL,
		now.Format(time.RFC1123Z),
		baseURL, starsParam,
		strings.Join(items, ""))

	w.Header().Set("Content-Type", "application/rss+xml; charset=utf-8")
	w.Write([]byte(rss))
}

// handleNotificationsFeed generates RSS feed from recent notifications
func (s *Server) handleNotificationsFeed(w http.ResponseWriter, r *http.Request) {
	limit := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 && parsed <= 200 {
			limit = parsed
		}
	}

	// Query recent notifications
	query := `SELECT id, park_id, notification_type, title, message, reference_url, created_at
	          FROM notifications ORDER BY created_at DESC LIMIT ?`
	rows, err := s.DB.Query(query, limit)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	baseURL := "https://" + r.Host
	pwd := r.URL.Query().Get("pwd")
	pwdParam := ""
	if pwd != "" {
		pwdParam = "?pwd=" + pwd
		baseURL += pwdParam
	}

	items := []string{}
	for rows.Next() {
		var id int64
		var parkID, notifType, title, message sql.NullString
		var refURL sql.NullString
		var createdAt string

		if err := rows.Scan(&id, &parkID, &notifType, &title, &message, &refURL, &createdAt); err != nil {
			continue
		}

		// Parse timestamp
		var pubDate time.Time
		if t, err := time.Parse("2006-01-02 15:04:05", createdAt); err == nil {
			pubDate = t
		} else if t, err := time.Parse(time.RFC3339, createdAt); err == nil {
			pubDate = t
		} else {
			pubDate = time.Now()
		}

		// Build link
		link := baseURL
		if parkID.Valid && parkID.String != "" {
			if pwdParam != "" {
				link = baseURL + "&popup=" + parkID.String
			} else {
				link = baseURL + "?popup=" + parkID.String
			}
		} else if refURL.Valid && refURL.String != "" {
			link = refURL.String
		}

		desc := ""
		if message.Valid {
			desc = message.String
		} else {
			desc = fmt.Sprintf("%s notification", notifType.String)
		}

		items = append(items, fmt.Sprintf(`
	<item>
		<title>%s</title>
		<link>%s</link>
		<description>%s</description>
		<pubDate>%s</pubDate>
		<guid>notification-%d</guid>
	</item>`,
			escapeXML(title.String),
			escapeXML(link),
			escapeXML(desc),
			pubDate.Format(time.RFC1123Z),
			id))
	}

	now := time.Now().UTC()
	feedURL := baseURL
	if pwdParam == "" {
		feedURL += "?feed=1"
	} else {
		feedURL += "&feed=1"
	}

	rss := fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
	<channel>
		<title>5MP Conservation Monitoring - Notifications</title>
		<link>%s</link>
		<description>Recent conservation monitoring notifications and alerts</description>
		<lastBuildDate>%s</lastBuildDate>
		<atom:link href="%s/api/feed%s" rel="self" type="application/rss+xml"/>%s
	</channel>
</rss>`,
		baseURL,
		now.Format(time.RFC1123Z),
		baseURL, pwdParam,
		strings.Join(items, ""))

	w.Header().Set("Content-Type", "application/rss+xml; charset=utf-8")
	w.Write([]byte(rss))
}

func escapeXML(s string) string {
	s = strings.ReplaceAll(s, "&", "&amp;")
	s = strings.ReplaceAll(s, "<", "&lt;")
	s = strings.ReplaceAll(s, ">", "&gt;")
	s = strings.ReplaceAll(s, "\"", "&quot;")
	s = strings.ReplaceAll(s, "'", "&apos;")
	return s
}

// HandleAPISettlementIntensity returns settlement visit intensity data for a park
// GET /api/parks/{id}/settlement-intensity
// Query params:
//   - from_year: start year (default: current year - 1)
//   - to_year: end year (default: current year)
//   - include_bases: include likely base settlements (default: false)
func (s *Server) HandleAPISettlementIntensity(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	fromYearStr := r.URL.Query().Get("from_year")
	toYearStr := r.URL.Query().Get("to_year")
	includeBasesStr := r.URL.Query().Get("include_bases")

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

	// Parse year range
	now := time.Now()
	fromYear := int64(now.Year() - 1)
	toYear := int64(now.Year())

	if fromYearStr != "" {
		if y, err := strconv.ParseInt(fromYearStr, 10, 64); err == nil {
			fromYear = y
		}
	}
	if toYearStr != "" {
		if y, err := strconv.ParseInt(toYearStr, 10, 64); err == nil {
			toYear = y
		}
	}

	// Parse include_bases flag
	includeBases := false
	if includeBasesStr == "true" || includeBasesStr == "1" {
		includeBases = true
	}

	q := dbgen.New(s.DB)
	rows, err := q.GetSettlementIntensityByPark(r.Context(), dbgen.GetSettlementIntensityByParkParams{
		ParkID: internalID,
		Year:   fromYear,
		Year_2: toYear,
	})
	if err != nil {
		slog.Error("failed to get settlement intensity", "error", err, "parkID", internalID)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	// Build response - GeoJSON FeatureCollection with intensity data
	type SettlementFeature struct {
		Type       string                 `json:"type"`
		Geometry   map[string]interface{} `json:"geometry"`
		Properties map[string]interface{} `json:"properties"`
	}

	type FeatureCollection struct {
		Type     string               `json:"type"`
		Features []SettlementFeature  `json:"features"`
	}

	fc := FeatureCollection{
		Type:     "FeatureCollection",
		Features: []SettlementFeature{},
	}

	for _, row := range rows {
		// Skip likely bases unless explicitly requested
		isBase := row.IsLikelyBase != nil && *row.IsLikelyBase == 1
		if isBase && !includeBases {
			continue
		}

		feature := SettlementFeature{
			Type: "Feature",
			Geometry: map[string]interface{}{
				"type":        "Point",
				"coordinates": []float64{row.Lon, row.Lat},
			},
			Properties: map[string]interface{}{
				"settlement_id":          row.SettlementID,
				"year":                   row.Year,
				"total_visits":           row.TotalVisits,
				"total_duration_minutes": row.TotalDurationMinutes,
				"unique_uploads":         row.UniqueUploads,
				"is_likely_base":         isBase,
			},
		}

		// Add optional fields
		if row.FootVisits != nil {
			feature.Properties["foot_visits"] = *row.FootVisits
		}
		if row.VehicleVisits != nil {
			feature.Properties["vehicle_visits"] = *row.VehicleVisits
		}
		if row.AircraftVisits != nil {
			feature.Properties["aircraft_visits"] = *row.AircraftVisits
		}
		if row.NearestPlace != nil {
			feature.Properties["nearest_place"] = *row.NearestPlace
		}
		if row.AreaM2 != nil {
			feature.Properties["area_m2"] = *row.AreaM2
		}
		if row.PopulationEst != nil {
			feature.Properties["population_est"] = *row.PopulationEst
		}
		if row.Month != nil {
			feature.Properties["month"] = *row.Month
		}

		fc.Features = append(fc.Features, feature)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(fc)
}

// HandleAPIParkClimate returns climate data for a park
func (s *Server) HandleAPIParkClimate(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	
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
	
	type ClimateData struct {
		ParkID          string  `json:"park_id"`
		TempAnnualC     float64 `json:"temp_annual_c"`
		TempMaxC        float64 `json:"temp_max_c"`
		TempMinC        float64 `json:"temp_min_c"`
		PrecipAnnualMM  int     `json:"precip_annual_mm"`
		PrecipWettestMM int     `json:"precip_wettest_mm"`
		PrecipDriestMM  int     `json:"precip_driest_mm"`
		ClimateZone     string  `json:"climate_zone"`
		RainySeason     string  `json:"rainy_season"`
		DrySeason       string  `json:"dry_season"`
	}
	
	var data ClimateData
	data.ParkID = internalID
	
	err := s.DB.QueryRow(`
		SELECT COALESCE(temp_annual_c, 0), COALESCE(temp_max_c, 0), COALESCE(temp_min_c, 0), 
		       COALESCE(precip_annual_mm, 0), COALESCE(precip_wettest_mm, 0), COALESCE(precip_driest_mm, 0),
		       COALESCE(climate_zone, ''), COALESCE(rainy_season, ''), COALESCE(dry_season, '')
		FROM park_climate
		WHERE park_id = ?
	`, internalID).Scan(&data.TempAnnualC, &data.TempMaxC, &data.TempMinC, 
		&data.PrecipAnnualMM, &data.PrecipWettestMM, &data.PrecipDriestMM,
		&data.ClimateZone, &data.RainySeason, &data.DrySeason)
	
	if err != nil {
		// Return empty data if not found
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(ClimateData{ParkID: internalID})
		return
	}
	
	// Fill in defaults if not set
	if data.ClimateZone == "" {
		if data.PrecipAnnualMM > 2000 {
			data.ClimateZone = "Tropical Rainforest"
		} else if data.PrecipAnnualMM > 800 && data.TempAnnualC > 22 {
			data.ClimateZone = "Tropical Savanna"
		} else if data.PrecipAnnualMM > 400 {
			data.ClimateZone = "Semi-Arid"
		} else {
			data.ClimateZone = "Arid"
		}
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(data)
}

// HandleAPIParkSpecies returns IUCN Red List species for a park
func (s *Server) HandleAPIParkSpecies(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	
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
	
	type Species struct {
		Binomial     string `json:"binomial"`
		CommonName   string `json:"common_name,omitempty"`
		Status       string `json:"status"`
		StatusName   string `json:"status_name"`
		Order        string `json:"order"`
		Family       string `json:"family"`
	}
	
	type SpeciesResponse struct {
		ParkID      string    `json:"park_id"`
		TotalCount  int       `json:"total_count"`
		Threatened  int       `json:"threatened"`
		Critical    int       `json:"critical"`
		Endangered  int       `json:"endangered"`
		Vulnerable  int       `json:"vulnerable"`
		Species     []Species `json:"species"`
	}
	
	statusNames := map[string]string{
		"CR": "Critically Endangered",
		"EN": "Endangered",
		"VU": "Vulnerable",
		"NT": "Near Threatened",
		"LC": "Least Concern",
		"DD": "Data Deficient",
	}
	
	rows, err := s.DB.Query(`
		SELECT binomial, common_name, status, species_order, family
		FROM park_species
		WHERE park_id = ?
		ORDER BY 
			CASE status 
				WHEN 'CR' THEN 1 
				WHEN 'EN' THEN 2 
				WHEN 'VU' THEN 3 
				WHEN 'NT' THEN 4 
				WHEN 'LC' THEN 5 
				ELSE 6 
			END, binomial
	`, internalID)
	
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(SpeciesResponse{ParkID: internalID})
		return
	}
	defer rows.Close()
	
	var resp SpeciesResponse
	resp.ParkID = internalID
	
	for rows.Next() {
		var sp Species
		var commonName, status, order, family sql.NullString
		rows.Scan(&sp.Binomial, &commonName, &status, &order, &family)
		
		sp.CommonName = commonName.String
		sp.Status = status.String
		sp.StatusName = statusNames[sp.Status]
		sp.Order = order.String
		sp.Family = family.String
		
		resp.Species = append(resp.Species, sp)
		resp.TotalCount++
		
		switch sp.Status {
		case "CR":
			resp.Critical++
			resp.Threatened++
		case "EN":
			resp.Endangered++
			resp.Threatened++
		case "VU":
			resp.Vulnerable++
			resp.Threatened++
		}
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// HandleAPIParkKML exports park data as KML for Google Earth
func (s *Server) HandleAPIParkKML(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}

	// Parse date filters
	fromDate := r.URL.Query().Get("from")
	toDate := r.URL.Query().Get("to")
	if fromDate == "" {
		fromDate = r.URL.Query().Get("start")
	}
	if toDate == "" {
		toDate = r.URL.Query().Get("end")
	}

	// Get park info
	parkName := parkID
	var boundary string
	for _, pa := range s.AreaStore.Areas {
		if pa.ID == parkID {
			parkName = pa.Name
			if pa.Geometry.Type != "" {
				if geomBytes, err := json.Marshal(pa.Geometry); err == nil {
					boundary = string(geomBytes)
				}
			}
			break
		}
	}

	// Build KML
	var kml strings.Builder
	kml.WriteString("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n")
	kml.WriteString("<kml xmlns=\"http://www.opengis.net/kml/2.2\">\n")
	kml.WriteString("<Document>\n")
	kml.WriteString(fmt.Sprintf("<name>%s - 5MP Conservation Data</name>\n", xmlEscape(parkName)))
	kml.WriteString("<description>Fire, settlement, and deforestation data from 5MP Conservation Monitoring</description>\n")

	// Define styles
	kml.WriteString("<Style id=\"boundary\"><LineStyle><color>ff00ff00</color><width>3</width></LineStyle><PolyStyle><color>2000ff00</color></PolyStyle></Style>\n")
	kml.WriteString("<Style id=\"fire\"><IconStyle><color>ff0000ff</color><Icon><href>http://maps.google.com/mapfiles/kml/shapes/firedept.png</href></Icon></IconStyle><LineStyle><color>ff0000ff</color><width>2</width></LineStyle></Style>\n")
	kml.WriteString("<Style id=\"settlement\"><IconStyle><color>ff00d7ff</color><Icon><href>http://maps.google.com/mapfiles/kml/shapes/homegardenbusiness.png</href></Icon></IconStyle><PolyStyle><color>5000d7ff</color></PolyStyle></Style>\n")
	kml.WriteString("<Style id=\"deforestation\"><IconStyle><color>ffff00ff</color><Icon><href>http://maps.google.com/mapfiles/kml/shapes/triangle.png</href></Icon></IconStyle><PolyStyle><color>50ff00ff</color></PolyStyle></Style>\n")
	kml.WriteString("<Style id=\"road\"><LineStyle><color>ff60a5fa</color><width>2</width></LineStyle></Style>\n")
	kml.WriteString("<Style id=\"place\"><IconStyle><color>ffffffff</color><scale>0.8</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle></Style>\n")
	kml.WriteString("<Style id=\"water\"><IconStyle><color>ffff9933</color><Icon><href>http://maps.google.com/mapfiles/kml/shapes/water.png</href></Icon></IconStyle><LineStyle><color>ffff9933</color><width>2</width></LineStyle><PolyStyle><color>50ff9933</color></PolyStyle></Style>\n")
	kml.WriteString("<Style id=\"patrol\"><IconStyle><scale>0</scale></IconStyle><PolyStyle><color>5022c55e</color></PolyStyle><LineStyle><color>8022c55e</color><width>1</width></LineStyle></Style>\n") // Semi-transparent green circles for patrol effort

	// Boundary folder
	if boundary != "" {
		kml.WriteString("<Folder><name>Park Boundary</name>\n")
		writeGeoJSONToKML(&kml, boundary, "boundary", parkName+" Boundary")
		kml.WriteString("</Folder>\n")
	}

	// Settlements folder with narratives
	kml.WriteString("<Folder><name>Settlements</name>\n")
	
	// Join feature_geometries with park_settlements using polygon_ids (same as tooltip logic)
	settlementQuery := `
		SELECT 
			fg.feature_id,
			fg.geojson,
			fg.properties_json,
			ps.narrative,
			ps.classification,
			ps.nearest_place
		FROM feature_geometries fg
		LEFT JOIN park_settlements ps ON fg.park_id = ps.park_id 
			AND (',' || ps.polygon_ids || ',') LIKE ('%,' || fg.feature_id || ',%')
		WHERE fg.park_id = ? AND fg.feature_type = 'settlement'
		LIMIT 1000
	`
	settlementRows, _ := s.DB.Query(settlementQuery, parkID)
	if settlementRows != nil {
		defer settlementRows.Close()
		for settlementRows.Next() {
			var featureID, geojson, props string
			var narrative, classification, nearestPlace sql.NullString
			settlementRows.Scan(&featureID, &geojson, &props, &narrative, &classification, &nearestPlace)
			
			var propMap map[string]interface{}
			json.Unmarshal([]byte(props), &propMap)
			
			// Build name
			name := "Settlement"
			if classification.Valid && classification.String != "" {
				name = strings.Title(classification.String) + " Settlement"
			}
			if pop, ok := propMap["population_est"].(float64); ok && pop > 0 {
				name = fmt.Sprintf("%s (pop: %.0f)", name, pop)
			}
			if nearestPlace.Valid && nearestPlace.String != "" {
				name = fmt.Sprintf("%s near %s", name, nearestPlace.String)
			}
			
			// Use narrative from park_settlements if available
			var description string
			if narrative.Valid && narrative.String != "" {
				description = narrative.String
			} else {
				// Build from properties
				var descParts []string
				if nearestPlace.Valid && nearestPlace.String != "" {
					descParts = append(descParts, "Near: "+nearestPlace.String)
				}
				if area, ok := propMap["area_m2"].(float64); ok {
					descParts = append(descParts, fmt.Sprintf("Area: %.0f m²", area))
				}
				if pop, ok := propMap["population_est"].(float64); ok {
					descParts = append(descParts, fmt.Sprintf("Population: %.0f", pop))
				}
				description = strings.Join(descParts, " | ")
			}
			
			writeGeoJSONToKMLWithDesc(&kml, geojson, "settlement", xmlEscape(name), description, "", "")
		}
	}
	kml.WriteString("</Folder>\n")

	// Deforestation folder with narratives
	kml.WriteString("<Folder><name>Deforestation</name>\n")
	
	// Join feature_geometries with deforestation_events using polygon_ids (same as tooltip logic)
	defoQuery := `
		SELECT 
			fg.feature_id,
			fg.geojson,
			fg.properties_json,
			fg.start_date,
			de.narrative,
			de.classification,
			de.pattern_type,
			de.area_km2,
			de.year
		FROM feature_geometries fg
		LEFT JOIN deforestation_events de ON fg.park_id = de.park_id 
			AND CAST(fg.properties_json->>'year' AS INTEGER) = de.year
			AND (',' || de.polygon_ids || ',') LIKE ('%,' || fg.feature_id || ',%')
		WHERE fg.park_id = ? AND fg.feature_type = 'deforestation'
	`
	defoArgs := []interface{}{parkID}
	if fromDate != "" {
		defoQuery += " AND (fg.start_date IS NULL OR fg.start_date >= ?)"
		defoArgs = append(defoArgs, fromDate)
	}
	if toDate != "" {
		defoQuery += " AND (fg.start_date IS NULL OR fg.start_date <= ?)"
		defoArgs = append(defoArgs, toDate)
	}
	defoQuery += " LIMIT 1000"
	defoRows, _ := s.DB.Query(defoQuery, defoArgs...)
	if defoRows != nil {
		defer defoRows.Close()
		for defoRows.Next() {
			var featureID, geojson, props string
			var startDateStr sql.NullString
			var narrative, classification, patternType sql.NullString
			var areaKm2 sql.NullFloat64
			var year sql.NullInt64
			defoRows.Scan(&featureID, &geojson, &props, &startDateStr, &narrative, &classification, &patternType, &areaKm2, &year)
			
			var propMap map[string]interface{}
			json.Unmarshal([]byte(props), &propMap)
			
			// Build name
			name := "Deforestation"
			if classification.Valid && classification.String != "" {
				name = strings.Title(classification.String)
			}
			if year.Valid {
				name = fmt.Sprintf("%s (%d)", name, year.Int64)
			} else if y, ok := propMap["year"].(float64); ok {
				name = fmt.Sprintf("%s (%d)", name, int(y))
			}
			if areaKm2.Valid && areaKm2.Float64 > 0 {
				name = fmt.Sprintf("%s - %.2f km²", name, areaKm2.Float64)
			} else if area, ok := propMap["area_km2"].(float64); ok {
				name = fmt.Sprintf("%s - %.2f km²", name, area)
			}
			
			// Use narrative from deforestation_events if available
			var description string
			if narrative.Valid && narrative.String != "" {
				description = narrative.String
			} else {
				// Build from properties
				var descParts []string
				if patternType.Valid && patternType.String != "" {
					descParts = append(descParts, "Pattern: "+patternType.String)
				}
				if place, ok := propMap["nearest_place"].(string); ok && place != "" {
					descParts = append(descParts, "Near: "+place)
				}
				if areaKm2.Valid {
					descParts = append(descParts, fmt.Sprintf("Area: %.2f km²", areaKm2.Float64))
				}
				description = strings.Join(descParts, " | ")
			}
			
			// Add timespan for year
			var startDate, endDate string
			if year.Valid {
				startDate = fmt.Sprintf("%d-01-01", year.Int64)
				endDate = fmt.Sprintf("%d-12-31", year.Int64)
			}
			
			writeGeoJSONToKMLWithDesc(&kml, geojson, "deforestation", xmlEscape(name), description, startDate, endDate)
		}
	}
	kml.WriteString("</Folder>\n")

	// Fire trajectories folder with narratives
	kml.WriteString("<Folder><name>Fire Trajectories</name>\n")
	
	fireQuery := `SELECT geojson, properties_json, start_date, end_date FROM feature_geometries WHERE park_id = ? AND feature_type = 'fire_trajectory'`
	fireArgs := []interface{}{parkID}
	if fromDate != "" {
		fireQuery += " AND (end_date IS NULL OR end_date >= ?)"
		fireArgs = append(fireArgs, fromDate)
	}
	if toDate != "" {
		fireQuery += " AND (start_date IS NULL OR start_date <= ?)"
		fireArgs = append(fireArgs, toDate)
	}
	fireQuery += " LIMIT 500"
	fireRows, _ := s.DB.Query(fireQuery, fireArgs...)
	if fireRows != nil {
		defer fireRows.Close()
		for fireRows.Next() {
			var geojson, props string
			var startDate, endDate sql.NullString
			fireRows.Scan(&geojson, &props, &startDate, &endDate)
			// Skip trajectories with lon=0 (bad data)
			if strings.Contains(geojson, `"coordinates": [0.0,`) || strings.Contains(geojson, `"coordinates": [0,`) || strings.Contains(geojson, `[0.0, `) {
				continue
			}
			var propMap map[string]interface{}
			json.Unmarshal([]byte(props), &propMap)
			
			// Build descriptive name from properties
			name := "Fire Event"
			if featureID, ok := propMap["feature_id"].(string); ok && featureID != "" {
				// Extract friendly name like "Alpha-5" from feature_id
				parts := strings.Split(featureID, "_grp_")
				if len(parts) == 2 {
					name = "Fire " + parts[1][:8] // Use first 8 chars of hash
				}
			}
			if groupName, ok := propMap["group_name"].(string); ok && groupName != "" {
				name = groupName
			}
			
			// Get narrative directly from properties_json
			description := ""
			if narrative, ok := propMap["narrative"].(string); ok && narrative != "" {
				description = narrative
			} else {
				// Build description from available properties
				var descParts []string
				if groupType, ok := propMap["group_type"].(string); ok {
					descParts = append(descParts, "Type: "+groupType)
				}
				if fires, ok := propMap["fires_total"].(float64); ok {
					descParts = append(descParts, fmt.Sprintf("Fire detections: %.0f", fires))
				}
				if days, ok := propMap["days"].(float64); ok {
					descParts = append(descParts, fmt.Sprintf("Duration: %.0f days", days))
				}
				if dist, ok := propMap["distance_km"].(float64); ok {
					descParts = append(descParts, fmt.Sprintf("Distance: %.1f km", dist))
				}
				if direction, ok := propMap["direction"].(string); ok {
					descParts = append(descParts, "Direction: "+direction)
				}
				if nearestPlace, ok := propMap["nearest_place"].(string); ok && nearestPlace != "" {
					if dist, ok := propMap["nearest_place_dist"].(float64); ok {
						descParts = append(descParts, fmt.Sprintf("Near %s (%.1fkm)", nearestPlace, dist))
					}
				}
				description = strings.Join(descParts, " | ")
			}
			
			writeGeoJSONToKMLWithDesc(&kml, geojson, "fire", xmlEscape(name), description, startDate.String, endDate.String)
		}
	}
	kml.WriteString("</Folder>\n")

	// Roads folder (from feature_geometries) - only create if data exists
	var roadPlacemarks []string
	roadRows, _ := s.DB.Query(`SELECT geojson, properties_json FROM feature_geometries WHERE park_id = ? AND feature_type = 'road' LIMIT 500`, parkID)
	if roadRows != nil {
		defer roadRows.Close()
		for roadRows.Next() {
			var geojson, props string
			roadRows.Scan(&geojson, &props)
			var propMap map[string]interface{}
			json.Unmarshal([]byte(props), &propMap)
			name := "Road"
			if n, ok := propMap["name"].(string); ok && n != "" {
				name = n
			}
			var pmb strings.Builder
			writeGeoJSONToKML(&pmb, geojson, "road", xmlEscape(name))
			roadPlacemarks = append(roadPlacemarks, pmb.String())
		}
	}
	if len(roadPlacemarks) > 0 {
		kml.WriteString("<Folder><name>Roads (Patrol Data)</name>\n")
		for _, pm := range roadPlacemarks {
			kml.WriteString(pm)
		}
		kml.WriteString("</Folder>\n")
	}

	// HeiGIT Roads folder (from roads_heigit - official road network) - only create if data exists
	var heigitPlacemarks []string
	heigitRows, _ := s.DB.Query(`SELECT osm_id, highway_type, surface, length_km, geojson FROM roads_heigit WHERE park_id = ? AND geojson IS NOT NULL LIMIT 1000`, parkID)
	if heigitRows != nil {
		defer heigitRows.Close()
		for heigitRows.Next() {
			var osmID, hwType, geojson string
			var surface sql.NullString
			var lengthKm sql.NullFloat64
			heigitRows.Scan(&osmID, &hwType, &surface, &lengthKm, &geojson)
			
			// Build name
			name := hwType
			if surface.Valid && surface.String != "" {
				name = fmt.Sprintf("%s (%s)", hwType, surface.String)
			}
			if lengthKm.Valid && lengthKm.Float64 > 0 {
				name = fmt.Sprintf("%s - %.1f km", name, lengthKm.Float64)
			}
			var pmb strings.Builder
			writeGeoJSONToKML(&pmb, geojson, "road", xmlEscape(name))
			heigitPlacemarks = append(heigitPlacemarks, pmb.String())
		}
	}
	if len(heigitPlacemarks) > 0 {
		kml.WriteString("<Folder><name>Roads (HeiGIT)</name>\n")
		for _, pm := range heigitPlacemarks {
			kml.WriteString(pm)
		}
		kml.WriteString("</Folder>\n")
	}

	// HydroRIVERS folder (from park_rivers_hydro - includes geometry) - only create if data exists
	var riverPlacemarks []string
	riverDataRows, _ := s.DB.Query(`SELECT hyriv_id, name, length_km, stream_order, lat, lon, geojson FROM park_rivers_hydro WHERE park_id = ? ORDER BY stream_order DESC, length_km DESC LIMIT 200`, parkID)
	if riverDataRows != nil {
		defer riverDataRows.Close()
		for riverDataRows.Next() {
			var hyrivID int64
			var riverName sql.NullString
			var lengthKm sql.NullFloat64
			var streamOrder sql.NullInt64
			var lat, lon float64
			var geojson sql.NullString
			riverDataRows.Scan(&hyrivID, &riverName, &lengthKm, &streamOrder, &lat, &lon, &geojson)
			
			// Build name
			name := "River"
			if riverName.Valid && riverName.String != "" {
				name = riverName.String
			}
			if lengthKm.Valid && lengthKm.Float64 > 0 {
				name = fmt.Sprintf("%s (%.1f km)", name, lengthKm.Float64)
			}
			if streamOrder.Valid {
				name = fmt.Sprintf("%s [order %d]", name, streamOrder.Int64)
			}
			
			var pmb strings.Builder
			// Use actual geometry if available, else point
			if geojson.Valid && geojson.String != "" {
				writeGeoJSONToKML(&pmb, geojson.String, "water", xmlEscape(name))
			} else {
				pointGeoJSON := fmt.Sprintf(`{"type":"Point","coordinates":[%f,%f]}`, lon, lat)
				writeGeoJSONToKML(&pmb, pointGeoJSON, "water", xmlEscape(name))
			}
			riverPlacemarks = append(riverPlacemarks, pmb.String())
		}
	}
	if len(riverPlacemarks) > 0 {
		kml.WriteString("<Folder><name>Rivers (HydroRIVERS)</name>\n")
		for _, pm := range riverPlacemarks {
			kml.WriteString(pm)
		}
		kml.WriteString("</Folder>\n")
	}

	// Lakes folder (from park_lakes_hydro) - only create if data exists
	var lakePlacemarks []string
	lakeRows, _ := s.DB.Query(`SELECT hylak_id, name, area_km2, depth_avg, lat, lon, geojson FROM park_lakes_hydro WHERE park_id = ? ORDER BY area_km2 DESC LIMIT 50`, parkID)
	if lakeRows != nil {
		defer lakeRows.Close()
		for lakeRows.Next() {
			var hylakID int64
			var lakeName sql.NullString
			var areaKm2, depthAvg sql.NullFloat64
			var lat, lon float64
			var geojson sql.NullString
			lakeRows.Scan(&hylakID, &lakeName, &areaKm2, &depthAvg, &lat, &lon, &geojson)
			
			// Build name
			name := "Lake"
			if lakeName.Valid && lakeName.String != "" {
				name = lakeName.String
			}
			if areaKm2.Valid && areaKm2.Float64 > 0 {
				name = fmt.Sprintf("%s (%.1f km²)", name, areaKm2.Float64)
			}
			if depthAvg.Valid && depthAvg.Float64 > 0 {
				name = fmt.Sprintf("%s [depth %.0fm]", name, depthAvg.Float64)
			}
			
			var pmb strings.Builder
			// Use actual geometry if available, else point
			if geojson.Valid && geojson.String != "" {
				writeGeoJSONToKML(&pmb, geojson.String, "water", xmlEscape(name))
			} else {
				pointGeoJSON := fmt.Sprintf(`{"type":"Point","coordinates":[%f,%f]}`, lon, lat)
				writeGeoJSONToKML(&pmb, pointGeoJSON, "water", xmlEscape(name))
			}
			lakePlacemarks = append(lakePlacemarks, pmb.String())
		}
	}
	if len(lakePlacemarks) > 0 {
		kml.WriteString("<Folder><name>Lakes (HydroLAKES)</name>\n")
		for _, pm := range lakePlacemarks {
			kml.WriteString(pm)
		}
		kml.WriteString("</Folder>\n")
	}

	// Places folder - only create if data exists
	var placePlacemarks []string
	placeRows, _ := s.DB.Query(`SELECT name, lat, lon, place_type FROM osm_places WHERE park_id = ? LIMIT 500`, parkID)
	if placeRows != nil {
		defer placeRows.Close()
		for placeRows.Next() {
			var name string
			var lat, lon float64
			var placeType string
			placeRows.Scan(&name, &lat, &lon, &placeType)
			pointGeoJSON := fmt.Sprintf(`{"type":"Point","coordinates":[%f,%f]}`, lon, lat)
			var pmb strings.Builder
			writeGeoJSONToKML(&pmb, pointGeoJSON, "place", xmlEscape(fmt.Sprintf("%s (%s)", name, placeType)))
			placePlacemarks = append(placePlacemarks, pmb.String())
		}
	}
	if len(placePlacemarks) > 0 {
		kml.WriteString("<Folder><name>Places</name>\n")
		for _, pm := range placePlacemarks {
			kml.WriteString(pm)
		}
		kml.WriteString("</Folder>\n")
	}

	// Patrol Effort folder - grid cells with 30km buffer, semi-transparent circles with timestamps
	var patrolPlacemarks []string
	
	// Calculate 30km buffer bbox around park
	var patrolBBox [4]float64 // minLon, minLat, maxLon, maxLat
	if boundary != "" {
		var geom map[string]interface{}
		if err := json.Unmarshal([]byte(boundary), &geom); err == nil {
			var coords [][]float64
			if geomType, ok := geom["type"].(string); ok {
				if geomType == "Polygon" {
					if coordsArr, ok := geom["coordinates"].([]interface{}); ok && len(coordsArr) > 0 {
						if ring, ok := coordsArr[0].([]interface{}); ok {
							for _, pt := range ring {
								if p, ok := pt.([]interface{}); ok && len(p) >= 2 {
									if lon, ok := p[0].(float64); ok {
										if lat, ok := p[1].(float64); ok {
											coords = append(coords, []float64{lon, lat})
										}
									}
								}
							}
						}
					}
				}
			}
			
			if len(coords) > 0 {
				// Find bounding box
				minLon, maxLon := coords[0][0], coords[0][0]
				minLat, maxLat := coords[0][1], coords[0][1]
				for _, c := range coords {
					if c[0] < minLon {
						minLon = c[0]
					}
					if c[0] > maxLon {
						maxLon = c[0]
					}
					if c[1] < minLat {
						minLat = c[1]
					}
					if c[1] > maxLat {
						maxLat = c[1]
					}
				}
				
				// Add 30km buffer (~0.27 degrees)
				bufferDeg := 30.0 / 111.0
				patrolBBox = [4]float64{minLon - bufferDeg, minLat - bufferDeg, maxLon + bufferDeg, maxLat + bufferDeg}
			}
		}
	}
	
	if patrolBBox[0] != 0 || patrolBBox[1] != 0 { // Valid bbox
		// Query effort data within bbox with date filters
		patrolQuery := `
			SELECT 
				e.grid_cell_id, 
				e.year, 
				e.month, 
				e.movement_type,
				SUM(e.total_distance_km) as total_distance_km,
				SUM(e.total_points) as total_points,
				g.lat_center,
				g.lon_center
			FROM effort_data e
			JOIN grid_cells g ON e.grid_cell_id = g.id
			WHERE e.movement_type = 'all'
				AND g.lat_center BETWEEN ? AND ?
				AND g.lon_center BETWEEN ? AND ?
		`
		patrolArgs := []interface{}{patrolBBox[1], patrolBBox[3], patrolBBox[0], patrolBBox[2]}
		
		if fromDate != "" {
			patrolQuery += " AND (e.year > ? OR (e.year = ? AND e.month >= ?))"
			if t, err := time.Parse("2006-01-02", fromDate); err == nil {
				patrolArgs = append(patrolArgs, t.Year(), t.Year(), int(t.Month()))
			}
		}
		if toDate != "" {
			patrolQuery += " AND (e.year < ? OR (e.year = ? AND e.month <= ?))"
			if t, err := time.Parse("2006-01-02", toDate); err == nil {
				patrolArgs = append(patrolArgs, t.Year(), t.Year(), int(t.Month()))
			}
		}
		
		patrolQuery += " GROUP BY e.grid_cell_id, e.year, e.month, e.movement_type, g.lat_center, g.lon_center"
		patrolQuery += " ORDER BY e.year DESC, e.month DESC LIMIT 1000"
		
		patrolRows, _ := s.DB.Query(patrolQuery, patrolArgs...)
		if patrolRows != nil {
			defer patrolRows.Close()
			for patrolRows.Next() {
				var gridCellID, movementType string
				var year, month int
				var distanceKm float64
				var points int
				var latCenter, lonCenter float64
				patrolRows.Scan(&gridCellID, &year, &month, &movementType, &distanceKm, &points, &latCenter, &lonCenter)
				
				// Create circle polygon for grid cell (approximate 0.1 degree circle)
				circlePoly := makeCircleKML(lonCenter, latCenter, 0.05) // ~5.5km radius for 0.1 degree grid
				
				// Build name and description
				name := fmt.Sprintf("Patrol %s - %04d-%02d", movementType, year, month)
				description := fmt.Sprintf("Type: %s<br>Distance: %.1f km<br>Points: %d<br>Grid: %s", 
					movementType, distanceKm, points, gridCellID)
				
				// TimeSpan for the month
				startDate := fmt.Sprintf("%04d-%02d-01", year, month)
				endDate := fmt.Sprintf("%04d-%02d-28", year, month) // Simplified
				
				var pmb strings.Builder
				pmb.WriteString(fmt.Sprintf("<Placemark><name>%s</name><styleUrl>#patrol</styleUrl>", xmlEscape(name)))
				pmb.WriteString("<description><![CDATA[" + description + "]]></description>")
				pmb.WriteString(fmt.Sprintf("<TimeSpan><begin>%s</begin><end>%s</end></TimeSpan>", startDate, endDate))
				pmb.WriteString(circlePoly)
				pmb.WriteString("</Placemark>\n")
				
				patrolPlacemarks = append(patrolPlacemarks, pmb.String())
			}
		}
	}
	
	if len(patrolPlacemarks) > 0 {
		kml.WriteString("<Folder><name>Patrol Effort (30km buffer)</name>\n")
		for _, pm := range patrolPlacemarks {
			kml.WriteString(pm)
		}
		kml.WriteString("</Folder>\n")
	}

	// Waterbodies folder - only create if data exists
	var wbPlacemarks []string
	wbRows, _ := s.DB.Query(`SELECT waterbody_id, name, waterbody_type, lat, lon, geojson FROM park_waterbodies WHERE park_id = ? LIMIT 500`, parkID)
	if wbRows != nil {
		defer wbRows.Close()
		for wbRows.Next() {
			var wbID, wbName, wbType, geojson string
			var lat, lon float64
			wbRows.Scan(&wbID, &wbName, &wbType, &lat, &lon, &geojson)
			displayName := wbName
			if displayName == "" {
				displayName = fmt.Sprintf("%s at %.3f, %.3f", wbType, lat, lon)
			}
			var pmb strings.Builder
			writeGeoJSONToKML(&pmb, geojson, "water", xmlEscape(displayName))
			wbPlacemarks = append(wbPlacemarks, pmb.String())
		}
	}
	if len(wbPlacemarks) > 0 {
		kml.WriteString("<Folder><name>Waterbodies</name>\n")
		for _, pm := range wbPlacemarks {
			kml.WriteString(pm)
		}
		kml.WriteString("</Folder>\n")
	}

	// Note: Removed duplicate "Rivers" folder from osm_places since we already have HydroRIVERS above

	kml.WriteString("</Document>\n</kml>")

	w.Header().Set("Content-Type", "application/vnd.google-earth.kml+xml")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s.kml"`, parkID))
	w.Write([]byte(kml.String()))
}

// writeGeoJSONToKMLWithDesc converts a GeoJSON geometry to KML placemark with description and optional timespan
func writeGeoJSONToKMLWithDesc(kml *strings.Builder, geojsonStr, styleID, name, description, startDate, endDate string) {
	var geom map[string]interface{}
	if err := json.Unmarshal([]byte(geojsonStr), &geom); err != nil {
		return
	}

	geomType, _ := geom["type"].(string)
	coords := geom["coordinates"]

	kml.WriteString(fmt.Sprintf("<Placemark><name>%s</name><styleUrl>#%s</styleUrl>", xmlEscape(name), styleID))
	
	// Add description if provided
	if description != "" {
		kml.WriteString("<description><![CDATA[" + description + "]]></description>")
	}
	
	// Add TimeSpan for Google Earth time slider
	if startDate != "" || endDate != "" {
		kml.WriteString("<TimeSpan>")
		if startDate != "" {
			kml.WriteString(fmt.Sprintf("<begin>%s</begin>", startDate))
		}
		if endDate != "" {
			kml.WriteString(fmt.Sprintf("<end>%s</end>", endDate))
		}
		kml.WriteString("</TimeSpan>")
	}

	switch geomType {
	case "Point":
		if c, ok := coords.([]interface{}); ok && len(c) >= 2 {
			kml.WriteString(fmt.Sprintf("<Point><coordinates>%v,%v,0</coordinates></Point>", c[0], c[1]))
		}
	case "LineString":
		kml.WriteString("<LineString><coordinates>")
		if c, ok := coords.([]interface{}); ok {
			for _, pt := range c {
				if p, ok := pt.([]interface{}); ok && len(p) >= 2 {
					kml.WriteString(fmt.Sprintf("%v,%v,0 ", p[0], p[1]))
				}
			}
		}
		kml.WriteString("</coordinates></LineString>")
	case "Polygon":
		kml.WriteString("<Polygon><outerBoundaryIs><LinearRing><coordinates>")
		if rings, ok := coords.([]interface{}); ok && len(rings) > 0 {
			if ring, ok := rings[0].([]interface{}); ok {
				for _, pt := range ring {
					if p, ok := pt.([]interface{}); ok && len(p) >= 2 {
						kml.WriteString(fmt.Sprintf("%v,%v,0 ", p[0], p[1]))
					}
				}
			}
		}
		kml.WriteString("</coordinates></LinearRing></outerBoundaryIs></Polygon>")
	case "MultiPolygon":
		if polys, ok := coords.([]interface{}); ok {
			for _, poly := range polys {
				kml.WriteString("<Polygon><outerBoundaryIs><LinearRing><coordinates>")
				if rings, ok := poly.([]interface{}); ok && len(rings) > 0 {
					if ring, ok := rings[0].([]interface{}); ok {
						for _, pt := range ring {
							if p, ok := pt.([]interface{}); ok && len(p) >= 2 {
								kml.WriteString(fmt.Sprintf("%v,%v,0 ", p[0], p[1]))
							}
						}
					}
				}
				kml.WriteString("</coordinates></LinearRing></outerBoundaryIs></Polygon>")
			}
		}
	}
	kml.WriteString("</Placemark>\n")
}

// writeGeoJSONToKML converts a GeoJSON geometry to KML placemark (backward compatibility)
func writeGeoJSONToKML(kml *strings.Builder, geojsonStr, styleID, name string) {
	var geom map[string]interface{}
	if err := json.Unmarshal([]byte(geojsonStr), &geom); err != nil {
		return
	}

	geomType, _ := geom["type"].(string)
	coords := geom["coordinates"]

	kml.WriteString(fmt.Sprintf("<Placemark><name>%s</name><styleUrl>#%s</styleUrl>", xmlEscape(name), styleID))

	switch geomType {
	case "Point":
		if c, ok := coords.([]interface{}); ok && len(c) >= 2 {
			kml.WriteString(fmt.Sprintf("<Point><coordinates>%v,%v,0</coordinates></Point>", c[0], c[1]))
		}
	case "LineString":
		kml.WriteString("<LineString><coordinates>")
		if c, ok := coords.([]interface{}); ok {
			for _, pt := range c {
				if p, ok := pt.([]interface{}); ok && len(p) >= 2 {
					kml.WriteString(fmt.Sprintf("%v,%v,0 ", p[0], p[1]))
				}
			}
		}
		kml.WriteString("</coordinates></LineString>")
	case "Polygon":
		kml.WriteString("<Polygon><outerBoundaryIs><LinearRing><coordinates>")
		if rings, ok := coords.([]interface{}); ok && len(rings) > 0 {
			if ring, ok := rings[0].([]interface{}); ok {
				for _, pt := range ring {
					if p, ok := pt.([]interface{}); ok && len(p) >= 2 {
						kml.WriteString(fmt.Sprintf("%v,%v,0 ", p[0], p[1]))
					}
				}
			}
		}
		kml.WriteString("</coordinates></LinearRing></outerBoundaryIs></Polygon>")
	case "MultiPolygon":
		if polys, ok := coords.([]interface{}); ok {
			for _, poly := range polys {
				kml.WriteString("<Polygon><outerBoundaryIs><LinearRing><coordinates>")
				if rings, ok := poly.([]interface{}); ok && len(rings) > 0 {
					if ring, ok := rings[0].([]interface{}); ok {
						for _, pt := range ring {
							if p, ok := pt.([]interface{}); ok && len(p) >= 2 {
								kml.WriteString(fmt.Sprintf("%v,%v,0 ", p[0], p[1]))
							}
						}
					}
				}
				kml.WriteString("</coordinates></LinearRing></outerBoundaryIs></Polygon>")
			}
		}
	}
	kml.WriteString("</Placemark>\n")
}

// HandleAPIParksExport returns enriched park data for CSV export
// GET /api/parks/export?from=&to=&country=&bbox=
func (s *Server) HandleAPIParksExport(w http.ResponseWriter, r *http.Request) {
	dateFrom := r.URL.Query().Get("from")
	dateTo := r.URL.Query().Get("to")
	country := r.URL.Query().Get("country")
	bboxStr := r.URL.Query().Get("bbox")
	
	type ParkExport struct {
		ID                  string  `json:"id"`
		WDPAID              string  `json:"wdpa_id"`
		Name                string  `json:"name"`
		Country             string  `json:"country"`
		AreaKm2             float64 `json:"area_km2"`
		FireDetections      int     `json:"fire_detections"`
		FireGroups          int     `json:"fire_groups"`
		DeforestationKm2    float64 `json:"deforestation_km2"`
		DeforestationEvents int     `json:"deforestation_events"`
		Settlements         int     `json:"settlements"`
		TotalPopulation     int     `json:"total_population"`
		RoadsKm             float64 `json:"roads_km"`
		RoadlessPct         float64 `json:"roadless_pct"`
		PatrolPixels        int     `json:"patrol_pixels"`
		PatrolDistanceKm    float64 `json:"patrol_distance_km"`
		PatrolIntensityMed  float64 `json:"patrol_intensity_median"`
		PublicationsCount   int     `json:"publications_count"`
		Lat                 float64 `json:"lat"`
		Lon                 float64 `json:"lon"`
	}
	
	var results []ParkExport
	
	// Build query with filters
	query := `
		WITH park_stats AS (
			SELECT 
				a.id, a.wdpa_id, a.name, a.country, a.area_km2, a.lat, a.lon,
				COALESCE(f.fire_count, 0) as fire_detections,
				COALESCE(fg.group_count, 0) as fire_groups,
				COALESCE(d.defo_km2, 0) as deforestation_km2,
				COALESCE(d.defo_events, 0) as deforestation_events,
				COALESCE(st.settlement_count, 0) as settlements,
				COALESCE(st.total_pop, 0) as total_population,
				COALESCE(rd.road_km, 0) as roads_km,
				COALESCE(rd.roadless_pct, 0) as roadless_pct,
				COALESCE(g.pixel_count, 0) as patrol_pixels,
				COALESCE(g.total_distance, 0) as patrol_distance_km,
				COALESCE(g.median_intensity, 0) as patrol_intensity_median,
				COALESCE(pub.pub_count, 0) as publications_count
			FROM (
				SELECT DISTINCT id, wdpa_id, name, country, area_km2, lat, lon 
				FROM (SELECT * FROM json_each(?))
			) a
			LEFT JOIN (
				SELECT park_id, COUNT(*) as fire_count 
				FROM fire_detections 
				WHERE ($1 = '' OR acq_date >= $1) AND ($2 = '' OR acq_date <= $2)
				GROUP BY park_id
			) f ON a.id = f.park_id
			LEFT JOIN (
				SELECT park_id, COUNT(DISTINCT group_name) as group_count 
				FROM fire_group_alerts GROUP BY park_id
			) fg ON a.id = fg.park_id
			LEFT JOIN (
				SELECT park_id, SUM(area_km2) as defo_km2, COUNT(*) as defo_events 
				FROM deforestation_events 
				WHERE ($1 = '' OR year >= CAST(substr($1,1,4) AS INTEGER))
				GROUP BY park_id
			) d ON a.id = d.park_id
			LEFT JOIN (
				SELECT park_id, COUNT(*) as settlement_count, SUM(population_est) as total_pop 
				FROM park_settlements GROUP BY park_id
			) st ON a.id = st.park_id
			LEFT JOIN (
				SELECT park_id, road_length_km as road_km, roadless_percentage as roadless_pct 
				FROM osm_roadless_data
			) rd ON a.id = rd.park_id
			LEFT JOIN (
				SELECT park_id, COUNT(*) as pixel_count, SUM(total_distance_km) as total_distance,
				       AVG(intensity) as median_intensity
				FROM grid_cells GROUP BY park_id
			) g ON a.id = g.park_id
			LEFT JOIN (
				SELECT pa_id, COUNT(*) as pub_count FROM pa_publications GROUP BY pa_id
			) pub ON a.wdpa_id = pub.pa_id
		)
		SELECT * FROM park_stats WHERE 1=1
	`
	
	// For now, return data from loaded areas
	if s.AreaStore == nil {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode([]ParkExport{})
		return
	}
	
	for _, area := range s.AreaStore.Areas {
		if country != "" && area.Country != country {
			continue
		}
		
		// Get stats for this park
		var fires, groups, defoEvents, settlements, pop, pixels, pubs int
		var defoKm2, roadsKm, roadlessPct, patrolDist, intensity float64
		
		s.DB.QueryRow(`SELECT COUNT(*) FROM fire_detections WHERE park_id = ? AND ($1 = '' OR acq_date >= $1) AND ($2 = '' OR acq_date <= $2)`, 
			area.ID, dateFrom, dateTo).Scan(&fires)
		s.DB.QueryRow(`SELECT COUNT(DISTINCT group_name) FROM fire_group_alerts WHERE park_id = ?`, area.ID).Scan(&groups)
		s.DB.QueryRow(`SELECT COALESCE(SUM(area_km2), 0), COUNT(*) FROM deforestation_events WHERE park_id = ?`, area.ID).Scan(&defoKm2, &defoEvents)
		s.DB.QueryRow(`SELECT COUNT(*), COALESCE(SUM(population_est), 0) FROM park_settlements WHERE park_id = ?`, area.ID).Scan(&settlements, &pop)
		s.DB.QueryRow(`SELECT COALESCE(road_length_km, 0), COALESCE(roadless_percentage, 0) FROM osm_roadless_data WHERE park_id = ?`, area.ID).Scan(&roadsKm, &roadlessPct)
		// Count grid cells within park bounds
		latMin, latMax, lonMin, lonMax := area.GetBoundingBox()
		s.DB.QueryRow(`
			SELECT COUNT(DISTINCT g.id), COALESCE(SUM(e.total_distance_km), 0)
			FROM grid_cells g
			LEFT JOIN effort_data e ON g.id = e.grid_cell_id
			WHERE g.lat_center >= ? AND g.lat_center <= ? AND g.lon_center >= ? AND g.lon_center <= ?`,
			latMin, latMax, lonMin, lonMax).Scan(&pixels, &patrolDist)
		s.DB.QueryRow(`SELECT COUNT(*) FROM pa_publications WHERE pa_id = ?`, area.WDPAID).Scan(&pubs)
		
		results = append(results, ParkExport{
			ID:                  area.ID,
			WDPAID:              area.WDPAID,
			Name:                area.Name,
			Country:             area.Country,
			AreaKm2:             area.AreaKm2,
			FireDetections:      fires,
			FireGroups:          groups,
			DeforestationKm2:    defoKm2,
			DeforestationEvents: defoEvents,
			Settlements:         settlements,
			TotalPopulation:     pop,
			RoadsKm:             roadsKm,
			RoadlessPct:         roadlessPct,
			PatrolPixels:        pixels,
			PatrolDistanceKm:    patrolDist,
			PatrolIntensityMed:  intensity,
			PublicationsCount:   pubs,
			Lat:                 0, // Calculated from geometry if needed
			Lon:                 0,
		})
	}
	
	_ = bboxStr // TODO: implement bbox filtering
	_ = query   // Complex query for future optimization
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(results)
}

// HandleAPIClassifiedSettlements returns settlements with AI classification
func (s *Server) HandleAPIClassifiedSettlements(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "park id required", http.StatusBadRequest)
		return
	}
	
	settlements := s.GetCachedClassifiedSettlements(parkID)
	
	// Group by classification
	byClass := make(map[string]int)
	for _, st := range settlements {
		byClass[st.Classification]++
	}
	
	response := map[string]interface{}{
		"park_id":       parkID,
		"total":         len(settlements),
		"by_class":      byClass,
		"settlements":   settlements,
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// HandleAPIClassifiedDeforestation returns deforestation events with AI classification
func (s *Server) HandleAPIClassifiedDeforestation(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "park id required", http.StatusBadRequest)
		return
	}
	
	events := s.GetCachedClassifiedDeforestation(parkID)
	
	// Group by classification
	byClass := make(map[string]int)
	totalArea := 0.0
	areaByClass := make(map[string]float64)
	for _, ev := range events {
		byClass[ev.Classification]++
		totalArea += ev.AreaKm2
		areaByClass[ev.Classification] += ev.AreaKm2
	}
	
	response := map[string]interface{}{
		"park_id":        parkID,
		"total_events":   len(events),
		"total_area_km2": totalArea,
		"by_class":       byClass,
		"area_by_class":  areaByClass,
		"events":         events,
	}
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// HandleAPIParkInfrastructure returns comprehensive infrastructure data for a park
// GET /api/parks/{id}/infrastructure
func (s *Server) HandleAPIParkInfrastructure(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	ctx := r.Context()
	
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
	
	type River struct {
		HyrivID      int64   `json:"hyriv_id"`
		Name         string  `json:"name"`
		LengthKm     float64 `json:"length_km"`
		DischargeCms float64 `json:"discharge_cms,omitempty"`
		StreamOrder  int     `json:"stream_order,omitempty"`
		Relation     string  `json:"relation"` // inside, crosses, nearby
		DistanceKm   float64 `json:"distance_km,omitempty"`
	}
	
	type Road struct {
		OsmID       string  `json:"osm_id"`
		HighwayType string  `json:"highway_type"`
		Surface     string  `json:"surface,omitempty"`
		Passability string  `json:"passability,omitempty"`
		LengthKm    float64 `json:"length_km"`
	}
	
	type Place struct {
		Name      string  `json:"name"`
		PlaceType string  `json:"place_type"`
		Lat       float64 `json:"lat"`
		Lon       float64 `json:"lon"`
	}
	
	type InfraResponse struct {
		Rivers       []River `json:"rivers"`
		Roads        []Road  `json:"roads"`
		Places       []Place `json:"places"`
		Summary      struct {
			TotalRivers     int     `json:"total_rivers"`
			TotalRoads      int     `json:"total_roads"`
			TotalPlaces     int     `json:"total_places"`
			TotalRoadKm     float64 `json:"total_road_km"`
			MajorRivers     []string `json:"major_rivers,omitempty"`
			RoadSurfaces    map[string]int `json:"road_surfaces,omitempty"`
		} `json:"summary"`
	}
	
	response := InfraResponse{
		Rivers: []River{},
		Roads:  []Road{},
		Places: []Place{},
	}
	response.Summary.RoadSurfaces = make(map[string]int)
	
	// Get rivers from park_rivers_hydro (top 20 by stream order)
	rows, err := s.DB.QueryContext(ctx, `
		SELECT hyriv_id, COALESCE(name, ''), COALESCE(length_km, 0), 
		       COALESCE(stream_order, 0), COALESCE(ord_flow, 0)
		FROM park_rivers_hydro 
		WHERE park_id = ?
		ORDER BY stream_order DESC, length_km DESC
		LIMIT 20
	`, internalID)
	if err == nil {
		defer rows.Close()
		majorRivers := []string{}
		for rows.Next() {
			var r River
			var ordFlow int
			if rows.Scan(&r.HyrivID, &r.Name, &r.LengthKm, &r.StreamOrder, &ordFlow) == nil {
				r.Relation = "inside"  // hydro data is all park rivers
				response.Rivers = append(response.Rivers, r)
				if r.Name != "" && r.StreamOrder >= 4 {
					majorRivers = append(majorRivers, r.Name)
				}
			}
		}
		response.Summary.MajorRivers = majorRivers
	}
	
	// Count total rivers
	s.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM park_rivers_hydro WHERE park_id = ?`, internalID).Scan(&response.Summary.TotalRivers)
	
	// Get roads with HeiGIT attributes
	roadRows, err := s.DB.QueryContext(ctx, `
		SELECT osm_id, COALESCE(highway_type, ''), COALESCE(surface, ''), 
		       COALESCE(passability, ''), COALESCE(length_km, 0)
		FROM roads_heigit 
		WHERE park_id = ?
		ORDER BY length_km DESC
		LIMIT 50
	`, internalID)
	if err == nil {
		defer roadRows.Close()
		var totalRoadKm float64
		for roadRows.Next() {
			var rd Road
			if roadRows.Scan(&rd.OsmID, &rd.HighwayType, &rd.Surface, &rd.Passability, &rd.LengthKm) == nil {
				response.Roads = append(response.Roads, rd)
				totalRoadKm += rd.LengthKm
				if rd.Surface != "" {
					response.Summary.RoadSurfaces[rd.Surface]++
				}
			}
		}
		response.Summary.TotalRoadKm = totalRoadKm
	}
	
	// Count total roads
	s.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM roads_heigit WHERE park_id = ?`, internalID).Scan(&response.Summary.TotalRoads)
	
	// Get OSM places (top 50)
	placeRows, err := s.DB.QueryContext(ctx, `
		SELECT name, place_type, lat, lon
		FROM osm_places 
		WHERE park_id = ?
		ORDER BY place_type, name
		LIMIT 50
	`, internalID)
	if err == nil {
		defer placeRows.Close()
		for placeRows.Next() {
			var p Place
			if placeRows.Scan(&p.Name, &p.PlaceType, &p.Lat, &p.Lon) == nil {
				response.Places = append(response.Places, p)
			}
		}
	}
	
	// Count total places
	s.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM osm_places WHERE park_id = ?`, internalID).Scan(&response.Summary.TotalPlaces)
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// HandleAPIMergedKML exports multiple parks as a single KML with folders
// GET /api/export/merged.kml?parks=ID1,ID2,ID3&from=&to=
func (s *Server) HandleAPIMergedKML(w http.ResponseWriter, r *http.Request) {
	parksParam := r.URL.Query().Get("parks")
	if parksParam == "" {
		http.Error(w, "parks parameter required (comma-separated IDs)", http.StatusBadRequest)
		return
	}
	
	parkIDs := strings.Split(parksParam, ",")
	if len(parkIDs) == 0 {
		http.Error(w, "No park IDs provided", http.StatusBadRequest)
		return
	}
	
	// Parse date filters
	fromDate := r.URL.Query().Get("from")
	toDate := r.URL.Query().Get("to")
	
	// Build KML header
	var kml strings.Builder
	kml.WriteString("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n")
	kml.WriteString("<kml xmlns=\"http://www.opengis.net/kml/2.2\">\n")
	kml.WriteString("<Document>\n")
	kml.WriteString(fmt.Sprintf("<name>5MP Conservation Data - %d Parks</name>\n", len(parkIDs)))
	kml.WriteString(fmt.Sprintf("<description>Fire, settlement, and deforestation data from 5MP Conservation Monitoring. Date range: %s to %s</description>\n", fromDate, toDate))
	
	// Define shared styles
	kml.WriteString("<Style id=\"boundary\"><LineStyle><color>ff00ff00</color><width>3</width></LineStyle><PolyStyle><color>2000ff00</color></PolyStyle></Style>\n")
	kml.WriteString("<Style id=\"fire\"><IconStyle><color>ff0000ff</color><Icon><href>http://maps.google.com/mapfiles/kml/shapes/firedept.png</href></Icon></IconStyle><LineStyle><color>ff0000ff</color><width>2</width></LineStyle></Style>\n")
	kml.WriteString("<Style id=\"settlement\"><IconStyle><color>ff00d7ff</color><Icon><href>http://maps.google.com/mapfiles/kml/shapes/homegardenbusiness.png</href></Icon></IconStyle><PolyStyle><color>5000d7ff</color></PolyStyle></Style>\n")
	kml.WriteString("<Style id=\"deforestation\"><IconStyle><color>ffff00ff</color><Icon><href>http://maps.google.com/mapfiles/kml/shapes/triangle.png</href></Icon></IconStyle><PolyStyle><color>50ff00ff</color></PolyStyle></Style>\n")
	kml.WriteString("<Style id=\"road\"><LineStyle><color>ff60a5fa</color><width>2</width></LineStyle></Style>\n")
	kml.WriteString("<Style id=\"place\"><IconStyle><color>ffffffff</color><scale>0.8</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle></Style>\n")
	
	// Process each park as a folder
	for _, parkID := range parkIDs {
		parkID = strings.TrimSpace(parkID)
		if parkID == "" {
			continue
		}
		
		// Get park info
		parkName := parkID
		var boundary string
		for _, pa := range s.AreaStore.Areas {
			if pa.ID == parkID {
				parkName = pa.Name
				if pa.Geometry.Type != "" {
					if geomBytes, err := json.Marshal(pa.Geometry); err == nil {
						boundary = string(geomBytes)
					}
				}
				break
			}
		}
		
		// Start park folder
		kml.WriteString(fmt.Sprintf("<Folder><name>%s</name>\n", xmlEscape(parkName)))
		
		// Boundary
		if boundary != "" {
			kml.WriteString("<Folder><name>Boundary</name>\n")
			writeGeoJSONToKML(&kml, boundary, "boundary", parkName)
			kml.WriteString("</Folder>\n")
		}
		
		// Build date filter
		dateFilter := ""
		if fromDate != "" {
			dateFilter = fmt.Sprintf(" AND start_date >= '%s'", fromDate)
		}
		if toDate != "" {
			dateFilter += fmt.Sprintf(" AND (end_date <= '%s' OR end_date IS NULL)", toDate)
		}
		
		// Fire trajectories
		kml.WriteString("<Folder><name>Fire Activity</name>\n")
		fireRows, _ := s.DB.Query(`SELECT geojson, properties_json, start_date, end_date 
			FROM feature_geometries WHERE park_id = ? AND feature_type = 'fire_trajectory'`+dateFilter+` LIMIT 500`, parkID)
		if fireRows != nil {
			for fireRows.Next() {
				var geojson, props string
				var startDate, endDate sql.NullString
				fireRows.Scan(&geojson, &props, &startDate, &endDate)
				var propMap map[string]interface{}
				json.Unmarshal([]byte(props), &propMap)
				name := fmt.Sprintf("Fire %v", propMap["feature_id"])
				desc := ""
				if narrative, ok := propMap["narrative"].(string); ok {
					desc = narrative
				}
				writeGeoJSONToKMLWithDesc(&kml, geojson, "fire", name, desc, startDate.String, endDate.String)
			}
			fireRows.Close()
		}
		kml.WriteString("</Folder>\n")
		
		// Settlements
		kml.WriteString("<Folder><name>Settlements</name>\n")
		settlementRows, _ := s.DB.Query(`SELECT geojson, properties_json FROM feature_geometries 
			WHERE park_id = ? AND feature_type = 'settlement' LIMIT 200`, parkID)
		if settlementRows != nil {
			for settlementRows.Next() {
				var geojson, props string
				settlementRows.Scan(&geojson, &props)
				var propMap map[string]interface{}
				json.Unmarshal([]byte(props), &propMap)
				name := "Settlement"
				if n, ok := propMap["name"].(string); ok && n != "" {
					name = n
				}
				writeGeoJSONToKML(&kml, geojson, "settlement", name)
			}
			settlementRows.Close()
		}
		kml.WriteString("</Folder>\n")
		
		// Deforestation
		kml.WriteString("<Folder><name>Deforestation</name>\n")
		deforestRows, _ := s.DB.Query(`SELECT geojson, properties_json FROM feature_geometries 
			WHERE park_id = ? AND feature_type = 'deforestation'`+dateFilter+` LIMIT 200`, parkID)
		if deforestRows != nil {
			for deforestRows.Next() {
				var geojson, props string
				deforestRows.Scan(&geojson, &props)
				var propMap map[string]interface{}
				json.Unmarshal([]byte(props), &propMap)
				name := "Forest Loss"
				if y, ok := propMap["year"].(float64); ok {
					name = fmt.Sprintf("Forest Loss %d", int(y))
				}
				writeGeoJSONToKML(&kml, geojson, "deforestation", name)
			}
			deforestRows.Close()
		}
		kml.WriteString("</Folder>\n")
		
		// Close park folder
		kml.WriteString("</Folder>\n")
	}
	
	kml.WriteString("</Document>\n</kml>")
	
	w.Header().Set("Content-Type", "application/vnd.google-earth.kml+xml")
	w.Header().Set("Content-Disposition", `attachment; filename="5mp_conservation_export.kml"`)
	w.Write([]byte(kml.String()))
}

// handleSettlementFeatures returns GeoJSON features for settlements with narratives
func (s *Server) handleSettlementFeatures(w http.ResponseWriter, parkID string, limitStr string, featureID string) {
	limit := 1000
	if limitStr != "" {
		if l, err := strconv.Atoi(limitStr); err == nil && l > 0 && l <= 10000 {
			limit = l
		}
	}

	// Join feature_geometries with park_settlements to get narrative
	// Use polygon_ids which contains comma-separated feature IDs
	query := `
		SELECT 
			fg.feature_id,
			fg.geojson,
			fg.properties_json,
			ps.narrative,
			ps.classification,
			ps.nearest_place,
			ps.distance_to_place_km
		FROM feature_geometries fg
		LEFT JOIN park_settlements ps ON fg.park_id = ps.park_id 
			AND (',' || ps.polygon_ids || ',') LIKE ('%,' || fg.feature_id || ',%')
		WHERE fg.park_id = ? AND fg.feature_type = 'settlement'
	`
	args := []interface{}{parkID}
	
	if featureID != "" {
		query += " AND fg.feature_id = ?"
		args = append(args, featureID)
	}
	
	query += " LIMIT ?"
	args = append(args, limit)
	
	rows, err := s.DB.Query(query, args...)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type GeoJSONFeature struct {
		Type       string                 `json:"type"`
		Geometry   json.RawMessage        `json:"geometry"`
		Properties map[string]interface{} `json:"properties"`
	}

	type FeatureCollection struct {
		Type     string           `json:"type"`
		Features []GeoJSONFeature `json:"features"`
	}

	fc := FeatureCollection{
		Type:     "FeatureCollection",
		Features: []GeoJSONFeature{},
	}

	for rows.Next() {
		var featureID, geojson string
		var propsJSON sql.NullString
		var narrative, classification, nearestPlace sql.NullString
		var distanceToPlace sql.NullFloat64

		if err := rows.Scan(&featureID, &geojson, &propsJSON, &narrative, &classification, &nearestPlace, &distanceToPlace); err != nil {
			continue
		}

		// Parse properties
		props := make(map[string]interface{})
		if propsJSON.Valid {
			json.Unmarshal([]byte(propsJSON.String), &props)
		}
		props["feature_type"] = "settlement"
		props["feature_id"] = featureID
		
		// Add narrative and other fields from park_settlements
		if narrative.Valid {
			props["narrative"] = narrative.String
		}
		if classification.Valid {
			props["classification"] = classification.String
		}
		if nearestPlace.Valid {
			props["nearest_place"] = nearestPlace.String
		}
		if distanceToPlace.Valid {
			props["distance_to_place_km"] = distanceToPlace.Float64
		}

		fc.Features = append(fc.Features, GeoJSONFeature{
			Type:       "Feature",
			Geometry:   json.RawMessage(geojson),
			Properties: props,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(fc)
}

// handleDeforestationFeatures returns GeoJSON features for deforestation with narratives
func (s *Server) handleDeforestationFeatures(w http.ResponseWriter, parkID string, limitStr string, startDate string, endDate string, featureID string) {
	limit := 1000
	if limitStr != "" {
		if l, err := strconv.Atoi(limitStr); err == nil && l > 0 && l <= 10000 {
			limit = l
		}
	}

	// Build query with date filters
	// Join on polygon_ids which contains comma-separated feature IDs
	query := `
		SELECT 
			fg.feature_id,
			fg.geojson,
			fg.properties_json,
			fg.start_date,
			de.narrative,
			de.classification,
			de.pattern_type
		FROM feature_geometries fg
		LEFT JOIN deforestation_events de ON fg.park_id = de.park_id 
			AND CAST(fg.properties_json->>'year' AS INTEGER) = de.year
			AND (',' || de.polygon_ids || ',') LIKE ('%,' || fg.feature_id || ',%')
		WHERE fg.park_id = ? AND fg.feature_type = 'deforestation'
	`
	args := []interface{}{parkID}

	if featureID != "" {
		query += " AND fg.feature_id = ?"
		args = append(args, featureID)
	}
	if startDate != "" {
		query += " AND (fg.start_date IS NULL OR fg.start_date >= ?)"
		args = append(args, startDate)
	}
	if endDate != "" {
		query += " AND (fg.start_date IS NULL OR fg.start_date <= ?)"
		args = append(args, endDate)
	}

	query += " ORDER BY fg.start_date DESC LIMIT ?"
	args = append(args, limit)

	rows, err := s.DB.Query(query, args...)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type GeoJSONFeature struct {
		Type       string                 `json:"type"`
		Geometry   json.RawMessage        `json:"geometry"`
		Properties map[string]interface{} `json:"properties"`
	}

	type FeatureCollection struct {
		Type     string           `json:"type"`
		Features []GeoJSONFeature `json:"features"`
	}

	fc := FeatureCollection{
		Type:     "FeatureCollection",
		Features: []GeoJSONFeature{},
	}

	for rows.Next() {
		var featureID, geojson string
		var propsJSON, startDateStr sql.NullString
		var narrative, classification, patternType sql.NullString

		if err := rows.Scan(&featureID, &geojson, &propsJSON, &startDateStr, &narrative, &classification, &patternType); err != nil {
			continue
		}

		// Parse properties
		props := make(map[string]interface{})
		if propsJSON.Valid {
			json.Unmarshal([]byte(propsJSON.String), &props)
		}
		props["feature_type"] = "deforestation"
		props["feature_id"] = featureID
		
		if startDateStr.Valid {
			props["start_date"] = startDateStr.String
		}
		
		// Add narrative and other fields from deforestation_events
		if narrative.Valid {
			props["narrative"] = narrative.String
		}
		if classification.Valid {
			props["classification"] = classification.String
		}
		if patternType.Valid {
			props["pattern_type"] = patternType.String
		}

		fc.Features = append(fc.Features, GeoJSONFeature{
			Type:       "Feature",
			Geometry:   json.RawMessage(geojson),
			Properties: props,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(fc)
}

// xmlEscape escapes special XML characters in strings for safe inclusion in XML/KML
func xmlEscape(s string) string {
	s = strings.ReplaceAll(s, "&", "&amp;")
	s = strings.ReplaceAll(s, "<", "&lt;")
	s = strings.ReplaceAll(s, ">", "&gt;")
	s = strings.ReplaceAll(s, "\"", "&quot;")
	s = strings.ReplaceAll(s, "'", "&apos;")
	return s
}

// makeCircleKML creates a KML Polygon representing a circle
func makeCircleKML(centerLon, centerLat, radiusDeg float64) string {
	var coords []string
	numPoints := 32 // Number of points in circle
	for i := 0; i <= numPoints; i++ {
		angle := float64(i) * 2.0 * 3.14159265359 / float64(numPoints)
		lat := centerLat + radiusDeg*math.Sin(angle)
		lon := centerLon + radiusDeg*math.Cos(angle)
		coords = append(coords, fmt.Sprintf("%f,%f,0", lon, lat))
	}
	return fmt.Sprintf("<Polygon><outerBoundaryIs><LinearRing><coordinates>%s</coordinates></LinearRing></outerBoundaryIs></Polygon>", strings.Join(coords, " "))
}

// HandleAPINearbyPlaces returns place names near a given lat/lon from osm_places.
func (s *Server) HandleAPINearbyPlaces(w http.ResponseWriter, r *http.Request) {
	latStr := r.URL.Query().Get("lat")
	lonStr := r.URL.Query().Get("lon")

	lat, err1 := strconv.ParseFloat(latStr, 64)
	lon, err2 := strconv.ParseFloat(lonStr, 64)
	if err1 != nil || err2 != nil {
		http.Error(w, "lat and lon required", http.StatusBadRequest)
		return
	}

	type placeResult struct {
		Name     string  `json:"name"`
		Type     string  `json:"type"`
		Distance float64 `json:"distance_km"`
	}

	// Priority order for place types
	typePriority := map[string]int{
		"city": 0, "town": 1, "village": 2, "hamlet": 3,
		"mountain": 4, "hill": 5, "lake": 6, "river": 7, "stream": 8,
	}

	// Search within ~5km (0.05°), expand to ~15km if nothing found
	var places []placeResult
	for _, radius := range []float64{0.05, 0.15} {
		rows, err := s.DB.QueryContext(r.Context(), `
			SELECT DISTINCT name, place_type, lat, lon
			FROM osm_places
			WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
			LIMIT 100
		`, lat-radius, lat+radius, lon-radius, lon+radius)
		if err != nil {
			break
		}

		seen := make(map[string]bool)
		var candidates []placeResult
		for rows.Next() {
			var name, ptype string
			var plat, plon float64
			if err := rows.Scan(&name, &ptype, &plat, &plon); err != nil {
				continue
			}
			if seen[name+":"+ptype] {
				continue
			}
			seen[name+":"+ptype] = true

			// Approximate distance in km
			dlat := (plat - lat) * 111.0
			dlon := (plon - lon) * 111.0 * math.Cos(lat*math.Pi/180)
			dist := math.Sqrt(dlat*dlat + dlon*dlon)

			candidates = append(candidates, placeResult{
				Name:     name,
				Type:     ptype,
				Distance: math.Round(dist*10) / 10,
			})
		}
		rows.Close()

		if len(candidates) > 0 {
			// Sort by priority then distance
			sort.Slice(candidates, func(i, j int) bool {
				pi := typePriority[candidates[i].Type]
				pj := typePriority[candidates[j].Type]
				if pi != pj {
					return pi < pj
				}
				return candidates[i].Distance < candidates[j].Distance
			})
			// Dedupe by name, keep best type
			nameSeen := make(map[string]bool)
			for _, c := range candidates {
				if nameSeen[c.Name] {
					continue
				}
				nameSeen[c.Name] = true
				places = append(places, c)
				if len(places) >= 5 {
					break
				}
			}
			break
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"places": places})
}
