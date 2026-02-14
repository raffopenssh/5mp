package srv

import (
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"srv.exe.dev/db/dbgen"
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

	// Determine year range
	if fromStr != "" || toStr != "" {
		params.FromYear = int64(now.Year() - 1)
		params.ToYear = int64(now.Year())
		if fromStr != "" {
			if t, err := time.Parse("2006-01-02", fromStr); err == nil {
				params.FromYear = int64(t.Year())
			}
		}
		if toStr != "" {
			if t, err := time.Parse("2006-01-02", toStr); err == nil {
				params.ToYear = int64(t.Year())
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
	if typeStr != "" {
		for _, t := range strings.Split(typeStr, ",") {
			t = strings.TrimSpace(t)
			// Map 'aerial' to 'aircraft' (database uses 'aircraft')
			if t == "aerial" {
				t = "aircraft"
			}
			if t == "foot" || t == "vehicle" || t == "aircraft" {
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
		feature := buildGridFeature(
			row.GridCellID,
			row.LatCenter,
			row.LonCenter,
			row.TotalDistanceKm,
			row.TotalPoints,
			row.UniqueUploads,
			movementTypeStr,
			row.CoveragePercent,
			row.DryMonths,
			row.RainyMonths,
		)
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
// dryMonths and rainyMonths are the count of distinct months visited in each season.
// For full patrol coverage, rangers need to visit each cell monthly during dry season.
func buildGridFeature(gridCellID string, latCenter, lonCenter, totalDistanceKm float64, totalPoints, uniqueUploads int64, movementType string, coveragePercent *float64, dryMonths, rainyMonths int64) GeoJSONFeature {
	// Calculate intensity based on TEMPORAL FREQUENCY of visits
	// 
	// For effective poacher/herder detection:
	// - Dry season (Nov-Apr = 6 months): Need monthly visits, weight = 1.0 per month
	// - Rainy season (May-Oct = 6 months): Limited access, weight = 0.3 per month
	// 
	// Full intensity (1.0) = visited all dry season months + some rainy months
	// Expected weighted visits = 6 * 1.0 (dry) + 6 * 0.3 (rainy) = 7.8
	// But for practical purposes, we use 6 dry months as the baseline (ignoring rainy)
	
	var intensity float64
	
	// Primary calculation: temporal frequency (monthly visits)
	if dryMonths > 0 || rainyMonths > 0 {
		// Weight: dry months count fully, rainy months count 30%
		actualWeight := float64(dryMonths) + float64(rainyMonths)*0.3
		// Expected: 6 dry months = full coverage for a year
		expectedWeight := 6.0
		intensity = actualWeight / expectedWeight
	} else if coveragePercent != nil && *coveragePercent > 0 {
		// Fallback: spatial coverage (legacy behavior)
		intensity = *coveragePercent / 80.0
	} else {
		// Last fallback: estimate from distance
		// ~80km patrol in a year = ~1 full coverage (very rough)
		intensity = totalDistanceKm / 80.0
	}
	
	if intensity > 1.5 {
		intensity = 1.5 // Cap for overglow effect
	}

	// Return Point at center of cell (GeoJSON uses [lon, lat] order)
	return GeoJSONFeature{
		Type: "Feature",
		Geometry: GeoJSONGeometry{
			Type:        "Point",
			Coordinates: []float64{lonCenter, latCenter},
		},
		Properties: map[string]interface{}{
			"id":                gridCellID,
			"total_distance_km": totalDistanceKm,
			"total_points":      totalPoints,
			"unique_uploads":    uniqueUploads,
			"movement_type":     movementType,
			"intensity":         intensity,
			"coverage_percent":  coveragePercent,
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

	// Parse date range
	fromStr := r.URL.Query().Get("from")
	toStr := r.URL.Query().Get("to")
	bboxStr := r.URL.Query().Get("bbox")
	// Note: type filter not yet implemented for stats - would need movement type aggregation

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
	if fromStr != "" {
		if t, err := time.Parse("2006-01-02", fromStr); err == nil {
			fromYear = int64(t.Year())
		}
	}
	if toStr != "" {
		if t, err := time.Parse("2006-01-02", toStr); err == nil {
			toYear = int64(t.Year())
		}
	}

	// Aggregate stats across requested years
	var activePixels, totalUploads int64
	var totalDistanceKm float64
	// Build patrol stats query with optional bbox filter
	statsQuery := `
		SELECT 
			COUNT(DISTINCT e.grid_cell_id) as active_pixels,
			COALESCE(SUM(e.total_distance_km), 0) as total_distance_km,
			COALESCE(SUM(e.unique_uploads), 0) as total_uploads
		FROM effort_data e
		JOIN grid_cells g ON e.grid_cell_id = g.id
		WHERE e.year BETWEEN ? AND ?
		  AND e.day IS NULL
		  AND e.movement_type = 'all'
	`
	args := []interface{}{fromYear, toYear}

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

	// Fire detections in selected time period (with optional bbox filter)
	if fromStr != "" && toStr != "" {
		if len(bbox) == 4 {
			s.DB.QueryRow(`
				SELECT COUNT(*) FROM fire_detections 
				WHERE acq_date >= ? AND acq_date <= ?
				AND longitude >= ? AND longitude <= ?
				AND latitude >= ? AND latitude <= ?
			`, fromStr, toStr, bbox[0], bbox[2], bbox[1], bbox[3]).Scan(&totalFires)
		} else {
			s.DB.QueryRow(`
				SELECT COUNT(*) FROM fire_detections 
				WHERE acq_date >= ? AND acq_date <= ?
			`, fromStr, toStr).Scan(&totalFires)
		}

		// Get previous period fires for trend calculation
		fromTime, _ := time.Parse("2006-01-02", fromStr)
		toTime, _ := time.Parse("2006-01-02", toStr)
		duration := toTime.Sub(fromTime)
		prevFrom := fromTime.Add(-duration).Format("2006-01-02")
		prevTo := fromTime.Add(-24 * time.Hour).Format("2006-01-02")
		if len(bbox) == 4 {
			s.DB.QueryRow(`
				SELECT COUNT(*) FROM fire_detections 
				WHERE acq_date >= ? AND acq_date <= ?
				AND longitude >= ? AND longitude <= ?
				AND latitude >= ? AND latitude <= ?
			`, prevFrom, prevTo, bbox[0], bbox[2], bbox[1], bbox[3]).Scan(&prevFires)
		} else {
			s.DB.QueryRow(`
				SELECT COUNT(*) FROM fire_detections 
				WHERE acq_date >= ? AND acq_date <= ?
			`, prevFrom, prevTo).Scan(&prevFires)
		}
	} else {
		// Default: current year
		s.DB.QueryRow(`
			SELECT COUNT(*) FROM fire_detections 
			WHERE CAST(strftime('%Y', acq_date) AS INTEGER) = ?
		`, now.Year()).Scan(&totalFires)
		// Previous year for trend
		s.DB.QueryRow(`
			SELECT COUNT(*) FROM fire_detections 
			WHERE CAST(strftime('%Y', acq_date) AS INTEGER) = ?
		`, now.Year()-1).Scan(&prevFires)
	}

	// Deforestation totals in selected years (with optional bbox filter)
	if len(bbox) == 4 {
		s.DB.QueryRow(`
			SELECT COALESCE(SUM(area_km2), 0) FROM deforestation_events 
			WHERE year >= ? AND year <= ?
			AND lon >= ? AND lon <= ? AND lat >= ? AND lat <= ?
		`, fromYear, toYear, bbox[0], bbox[2], bbox[1], bbox[3]).Scan(&totalDeforestation)
	} else {
		s.DB.QueryRow(`
			SELECT COALESCE(SUM(area_km2), 0) FROM deforestation_events 
			WHERE year >= ? AND year <= ?
		`, fromYear, toYear).Scan(&totalDeforestation)
	}

	// Previous period deforestation for trend
	yearSpan := toYear - fromYear + 1
	if len(bbox) == 4 {
		s.DB.QueryRow(`
			SELECT COALESCE(SUM(area_km2), 0) FROM deforestation_events 
			WHERE year >= ? AND year < ?
			AND lon >= ? AND lon <= ? AND lat >= ? AND lat <= ?
		`, fromYear-yearSpan, fromYear, bbox[0], bbox[2], bbox[1], bbox[3]).Scan(&prevDeforestation)
	} else {
		s.DB.QueryRow(`
			SELECT COALESCE(SUM(area_km2), 0) FROM deforestation_events 
			WHERE year >= ? AND year < ?
		`, fromYear-yearSpan, fromYear).Scan(&prevDeforestation)
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
	w.Header().Set("Cache-Control", "public, max-age=30")
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
			"date":     u.UploadDate.Format("Jan 02"),
			"location": location,
			"distance": u.TotalDistanceKm,
			"type":     u.MovementType,
		}
		// Include coordinates if available
		if u.CentroidLat != nil && u.CentroidLon != nil {
			activity["lat"] = *u.CentroidLat
			activity["lon"] = *u.CentroidLon
		}
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
	paID := r.PathValue("id")
	if paID == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "missing park ID"})
		return
	}

	ctx := r.Context()
	q := dbgen.New(s.DB)

	pubs, err := q.GetPublicationsByPA(ctx, paID)
	if err != nil {
		slog.Error("failed to get publications", "pa_id", paID, "error", err)
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
		if p.Authors != nil {
			var authors []string
			json.Unmarshal([]byte(*p.Authors), &authors)
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
	paID := r.PathValue("id")
	if paID == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "missing park ID"})
		return
	}

	ctx := r.Context()
	q := dbgen.New(s.DB)

	count, err := q.GetPublicationCountByPA(ctx, paID)
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
	
	if startDate != "" {
		query += " AND (end_date IS NULL OR end_date >= ?)"
		args = append(args, startDate)
	}
	
	if endDate != "" {
		query += " AND (start_date IS NULL OR start_date <= ?)"
		args = append(args, endDate)
	}
	
	query += " ORDER BY start_date DESC, feature_id"
	
	limit := 1000 // Default limit
	if limitStr != "" {
		if l, err := strconv.Atoi(limitStr); err == nil && l > 0 && l <= 10000 {
			limit = l
		}
	}
	query += fmt.Sprintf(" LIMIT %d", limit)
	
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

// handlePlaceFeatures returns GeoJSON features for osm_places
func (s *Server) handlePlaceFeatures(w http.ResponseWriter, parkID string, limitStr string) {
	limit := 1000
	if limitStr != "" {
		if l, err := strconv.Atoi(limitStr); err == nil && l > 0 && l <= 10000 {
			limit = l
		}
	}

	rows, err := s.DB.Query(`
		SELECT id, place_type, name, lat, lon, geojson, osm_id, osm_tags
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
		var geojson sql.NullString
		var osmID, osmTags sql.NullString

		if err := rows.Scan(&id, &placeType, &name, &lat, &lon, &geojson, &osmID, &osmTags); err != nil {
			continue
		}

		var geometry json.RawMessage
		if geojson.Valid && geojson.String != "" {
			geometry = json.RawMessage(geojson.String)
		} else {
			geometry = json.RawMessage(fmt.Sprintf(`{"type":"Point","coordinates":[%f,%f]}`, lon, lat))
		}

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
		ConfidencePct *float64 `json:"confidence_pct"`
		Details       string   `json:"details"`
		CreatedAt     string   `json:"created_at"`
	}
	
	var features []PendingFeature
	
	// Get pending roads
	rows, err := s.DB.Query(`
		SELECT 'road' as type, id, park_id, confidence_pct, 
		       COALESCE(printf('%.1f km, %d matches', length_m/1000.0, match_count), 'Unknown'),
		       datetime(created_at) as created_at
		FROM learned_roads WHERE status = 'pending'
		UNION ALL
		SELECT 'airstrip' as type, id, park_id, confidence_pct,
		       COALESCE(printf('%s, %d landings', aircraft_type, landing_count), 'Unknown'),
		       datetime(created_at) as created_at
		FROM learned_airstrips WHERE status = 'pending'
		UNION ALL
		SELECT 'place' as type, id, park_id, confidence_pct,
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
		if err := rows.Scan(&f.Type, &f.ID, &f.ParkID, &f.ConfidencePct, &f.Details, &f.CreatedAt); err != nil {
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
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"features": features,
		"stats":    stats,
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

// HandleAPIDeleteUpload deletes a GPX upload and its logs
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

	// Delete from gpx_upload_logs and related tables
	_, err := s.DB.ExecContext(ctx, "DELETE FROM gpx_upload_logs WHERE id = ?", req.ID)
	if err != nil {
		http.Error(w, "Failed to delete upload", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "deleted"})
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

// HandleAPIFeed generates an RSS feed for starred items
// GET /api/feed?stars=<base64-encoded-starred-items>&format=rss
func (s *Server) HandleAPIFeed(w http.ResponseWriter, r *http.Request) {
	starsParam := r.URL.Query().Get("stars")
	if starsParam == "" {
		http.Error(w, "Missing stars parameter", http.StatusBadRequest)
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

	// Add park items
	for _, park := range starred.Parks {
		name, _ := park["name"].(string)
		id, _ := park["id"].(string)
		country, _ := park["country"].(string)
		
		if name == "" {
			continue
		}

		link := baseURL
		if id != "" {
			if pwd != "" {
				link = baseURL + "&popup=" + id
			} else {
				link = baseURL + "?popup=" + id
			}
		}

		items = append(items, fmt.Sprintf(`
		<item>
			<title>%s - %s</title>
			<link>%s</link>
			<description>Conservation monitoring for %s in %s</description>
			<pubDate>%s</pubDate>
			<guid>park-%s</guid>
		</item>`, 
			escapeXML(name), escapeXML(country),
			escapeXML(link),
			escapeXML(name), escapeXML(country),
			now.Format(time.RFC1123Z),
			escapeXML(id)))
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
		SELECT temp_annual_c, temp_max_c, temp_min_c, precip_annual_mm, precip_wettest_mm, precip_driest_mm,
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
	kml.WriteString(fmt.Sprintf("<name>%s - 5MP Conservation Data</name>\n", parkName))
	kml.WriteString("<description>Fire, settlement, and deforestation data from 5MP Conservation Monitoring</description>\n")

	// Define styles
	kml.WriteString("<Style id=\"boundary\"><LineStyle><color>ff00ff00</color><width>3</width></LineStyle><PolyStyle><color>2000ff00</color></PolyStyle></Style>\n")
	kml.WriteString("<Style id=\"fire\"><IconStyle><color>ff0000ff</color><Icon><href>http://maps.google.com/mapfiles/kml/shapes/firedept.png</href></Icon></IconStyle><LineStyle><color>ff0000ff</color><width>2</width></LineStyle></Style>\n")
	kml.WriteString("<Style id=\"settlement\"><IconStyle><color>ff00d7ff</color><Icon><href>http://maps.google.com/mapfiles/kml/shapes/homegardenbusiness.png</href></Icon></IconStyle><PolyStyle><color>5000d7ff</color></PolyStyle></Style>\n")
	kml.WriteString("<Style id=\"deforestation\"><IconStyle><color>ffff00ff</color><Icon><href>http://maps.google.com/mapfiles/kml/shapes/triangle.png</href></Icon></IconStyle><PolyStyle><color>50ff00ff</color></PolyStyle></Style>\n")
	kml.WriteString("<Style id=\"road\"><LineStyle><color>ff60a5fa</color><width>2</width></LineStyle></Style>\n")
	kml.WriteString("<Style id=\"place\"><IconStyle><color>ffffffff</color><scale>0.8</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle></Style>\n")
	kml.WriteString("<Style id=\"water\"><IconStyle><color>ffff9933</color><Icon><href>http://maps.google.com/mapfiles/kml/shapes/water.png</href></Icon></IconStyle><LineStyle><color>ffff9933</color><width>2</width></LineStyle><PolyStyle><color>50ff9933</color></PolyStyle></Style>\n")

	// Boundary folder
	if boundary != "" {
		kml.WriteString("<Folder><name>Park Boundary</name>\n")
		writeGeoJSONToKML(&kml, boundary, "boundary", parkName+" Boundary")
		kml.WriteString("</Folder>\n")
	}

	// Settlements folder with narratives
	kml.WriteString("<Folder><name>Settlements</name>\n")
	
	// Get settlement narratives
	settlementNarratives := make(map[int]string)
	snRows, _ := s.DB.Query(`SELECT id, narrative FROM park_settlements WHERE park_id = ? AND narrative IS NOT NULL AND narrative != ''`, parkID)
	if snRows != nil {
		defer snRows.Close()
		for snRows.Next() {
			var id int
			var narrative string
			snRows.Scan(&id, &narrative)
			settlementNarratives[id] = narrative
		}
	}
	
	settlementRows, _ := s.DB.Query(`SELECT fg.geojson, fg.properties_json, ps.id, ps.classification 
		FROM feature_geometries fg
		LEFT JOIN park_settlements ps ON ps.park_id = fg.park_id AND ABS(ps.lat - fg.centroid_lat) < 0.001 AND ABS(ps.lon - fg.centroid_lon) < 0.001
		WHERE fg.park_id = ? AND fg.feature_type = 'settlement' LIMIT 1000`, parkID)
	if settlementRows != nil {
		defer settlementRows.Close()
		for settlementRows.Next() {
			var geojson, props string
			var settlementID sql.NullInt64
			var classification sql.NullString
			settlementRows.Scan(&geojson, &props, &settlementID, &classification)
			var propMap map[string]interface{}
			json.Unmarshal([]byte(props), &propMap)
			
			// Build name
			name := "Settlement"
			if classification.Valid && classification.String != "" {
				name = strings.Title(classification.String) + " Settlement"
			}
			if pop, ok := propMap["population_est"].(float64); ok {
				name = fmt.Sprintf("%s (pop: %.0f)", name, pop)
			}
			
			// Get narrative
			var description string
			if settlementID.Valid {
				if narr, exists := settlementNarratives[int(settlementID.Int64)]; exists {
					description = narr
				}
			}
			if description == "" {
				// Build from properties
				var descParts []string
				if place, ok := propMap["nearest_place"].(string); ok && place != "" {
					descParts = append(descParts, "Near: "+place)
				}
				if area, ok := propMap["area_m2"].(float64); ok {
					descParts = append(descParts, fmt.Sprintf("Area: %.0f m²", area))
				}
				description = strings.Join(descParts, "<br>")
			}
			writeGeoJSONToKMLWithDesc(&kml, geojson, "settlement", name, description, "", "")
		}
	}
	kml.WriteString("</Folder>\n")

	// Deforestation folder with narratives
	kml.WriteString("<Folder><name>Deforestation</name>\n")
	
	// Get deforestation narratives
	defoNarratives := make(map[int]string)
	dnRows, _ := s.DB.Query(`SELECT id, narrative FROM deforestation_events WHERE park_id = ? AND narrative IS NOT NULL AND narrative != ''`, parkID)
	if dnRows != nil {
		defer dnRows.Close()
		for dnRows.Next() {
			var id int
			var narrative string
			dnRows.Scan(&id, &narrative)
			defoNarratives[id] = narrative
		}
	}
	
	defoQuery := `SELECT fg.geojson, fg.properties_json, de.id, de.year, de.classification, de.area_km2 
		FROM feature_geometries fg
		LEFT JOIN deforestation_events de ON de.park_id = fg.park_id AND de.year = CAST(json_extract(fg.properties_json, '$.year') AS INTEGER)
		WHERE fg.park_id = ? AND fg.feature_type = 'deforestation'`
	defoArgs := []interface{}{parkID}
	if fromDate != "" {
		defoQuery += " AND (fg.end_date IS NULL OR fg.end_date >= ?)"
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
			var geojson, props string
			var defoID sql.NullInt64
			var defoYear sql.NullInt64
			var classification sql.NullString
			var areaKm2 sql.NullFloat64
			defoRows.Scan(&geojson, &props, &defoID, &defoYear, &classification, &areaKm2)
			
			var propMap map[string]interface{}
			json.Unmarshal([]byte(props), &propMap)
			
			// Build name
			name := "Deforestation"
			if classification.Valid && classification.String != "" {
				name = strings.Title(classification.String)
			}
			if defoYear.Valid {
				name = fmt.Sprintf("%s (%d)", name, defoYear.Int64)
			} else if year, ok := propMap["year"].(float64); ok {
				name = fmt.Sprintf("%s (%d)", name, int(year))
			}
			if areaKm2.Valid && areaKm2.Float64 > 0 {
				name = fmt.Sprintf("%s - %.2f km²", name, areaKm2.Float64)
			}
			
			// Get narrative
			var description string
			if defoID.Valid {
				if narr, exists := defoNarratives[int(defoID.Int64)]; exists {
					description = narr
				}
			}
			if description == "" {
				// Build from properties
				var descParts []string
				if place, ok := propMap["nearest_place"].(string); ok && place != "" {
					descParts = append(descParts, "Near: "+place)
				}
				if pattern, ok := propMap["pattern_type"].(string); ok && pattern != "" {
					descParts = append(descParts, "Pattern: "+pattern)
				}
				description = strings.Join(descParts, "<br>")
			}
			
			// Add timespan for year
			var startDate, endDate string
			if defoYear.Valid {
				startDate = fmt.Sprintf("%d-01-01", defoYear.Int64)
				endDate = fmt.Sprintf("%d-12-31", defoYear.Int64)
			}
			writeGeoJSONToKMLWithDesc(&kml, geojson, "deforestation", name, description, startDate, endDate)
		}
	}
	kml.WriteString("</Folder>\n")

	// Fire trajectories folder with narratives
	kml.WriteString("<Folder><name>Fire Trajectories</name>\n")
	
	// Get fire narratives for this park
	fireNarratives := make(map[int]string)
	narrativeRows, _ := s.DB.Query(`
		SELECT fg.group_id, fg.narrative 
		FROM fire_groups fg 
		WHERE fg.park_id = ? AND fg.narrative IS NOT NULL AND fg.narrative != ''`, parkID)
	if narrativeRows != nil {
		defer narrativeRows.Close()
		for narrativeRows.Next() {
			var groupID int
			var narrative string
			narrativeRows.Scan(&groupID, &narrative)
			fireNarratives[groupID] = narrative
		}
	}
	
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
			var propMap map[string]interface{}
			json.Unmarshal([]byte(props), &propMap)
			name := "Fire Trajectory"
			var description string
			groupID := 0
			if gid, ok := propMap["group_id"].(float64); ok {
				groupID = int(gid)
				name = fmt.Sprintf("Fire Group %d", groupID)
				// Get narrative if available
				if narrative, exists := fireNarratives[groupID]; exists {
					description = narrative
				}
			}
			// Build description from properties if no narrative
			if description == "" {
				var descParts []string
				if sd, ok := propMap["start_date"].(string); ok {
					descParts = append(descParts, "Start: "+sd)
				}
				if ed, ok := propMap["end_date"].(string); ok {
					descParts = append(descParts, "End: "+ed)
				}
				if fi, ok := propMap["fires_inside"].(float64); ok {
					descParts = append(descParts, fmt.Sprintf("Detections: %.0f", fi))
				}
				if di, ok := propMap["days_inside"].(float64); ok {
					descParts = append(descParts, fmt.Sprintf("Days inside: %.0f", di))
				}
				if oc, ok := propMap["outcome"].(string); ok {
					descParts = append(descParts, "Outcome: "+oc)
				}
				description = strings.Join(descParts, "<br>")
			}
			writeGeoJSONToKMLWithDesc(&kml, geojson, "fire", name, description, startDate.String, endDate.String)
		}
	}
	kml.WriteString("</Folder>\n")

	// Roads folder (from feature_geometries)
	kml.WriteString("<Folder><name>Roads (Patrol Data)</name>\n")
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
			writeGeoJSONToKML(&kml, geojson, "road", name)
		}
	}
	kml.WriteString("</Folder>\n")

	// HeiGIT Roads folder (from roads_heigit - official road network)
	kml.WriteString("<Folder><name>Roads (HeiGIT)</name>\n")
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
			writeGeoJSONToKML(&kml, geojson, "road", name)
		}
	}
	kml.WriteString("</Folder>\n")

	// HydroRIVERS folder (from park_rivers)
	kml.WriteString("<Folder><name>Rivers (HydroRIVERS)</name>\n")
	riverDataRows, _ := s.DB.Query(`SELECT hyriv_id, river_name, length_km, discharge_cms, stream_order, centroid_lon, centroid_lat FROM park_rivers WHERE park_id = ? ORDER BY discharge_cms DESC LIMIT 500`, parkID)
	if riverDataRows != nil {
		defer riverDataRows.Close()
		for riverDataRows.Next() {
			var hyrivID int64
			var riverName sql.NullString
			var lengthKm, dischargeCms sql.NullFloat64
			var streamOrder sql.NullInt64
			var centroidLon, centroidLat float64
			riverDataRows.Scan(&hyrivID, &riverName, &lengthKm, &dischargeCms, &streamOrder, &centroidLon, &centroidLat)
			
			// Build name
			name := "River"
			if riverName.Valid && riverName.String != "" {
				name = riverName.String
			}
			if dischargeCms.Valid && dischargeCms.Float64 > 0 {
				name = fmt.Sprintf("%s (%.1f m³/s)", name, dischargeCms.Float64)
			}
			if streamOrder.Valid {
				name = fmt.Sprintf("%s [order %d]", name, streamOrder.Int64)
			}
			
			// Create point for river centroid
			pointGeoJSON := fmt.Sprintf(`{"type":"Point","coordinates":[%f,%f]}`, centroidLon, centroidLat)
			writeGeoJSONToKML(&kml, pointGeoJSON, "water", name)
		}
	}
	kml.WriteString("</Folder>\n")

	// Places folder
	kml.WriteString("<Folder><name>Places</name>\n")
	placeRows, _ := s.DB.Query(`SELECT name, lat, lon, place_type FROM osm_places WHERE park_id = ? LIMIT 500`, parkID)
	if placeRows != nil {
		defer placeRows.Close()
		for placeRows.Next() {
			var name string
			var lat, lon float64
			var placeType string
			placeRows.Scan(&name, &lat, &lon, &placeType)
			pointGeoJSON := fmt.Sprintf(`{"type":"Point","coordinates":[%f,%f]}`, lon, lat)
			writeGeoJSONToKML(&kml, pointGeoJSON, "place", fmt.Sprintf("%s (%s)", name, placeType))
		}
	}
	kml.WriteString("</Folder>\n")

	// Waterbodies folder
	kml.WriteString("<Folder><name>Waterbodies</name>\n")
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
			writeGeoJSONToKML(&kml, geojson, "water", displayName)
		}
	}
	kml.WriteString("</Folder>\n")

	// Rivers folder (from osm_places)
	kml.WriteString("<Folder><name>Rivers</name>\n")
	riverRows, _ := s.DB.Query(`SELECT name, lat, lon, place_type, geojson FROM osm_places WHERE park_id = ? AND place_type IN ('river', 'stream', 'lake') LIMIT 500`, parkID)
	if riverRows != nil {
		defer riverRows.Close()
		for riverRows.Next() {
			var name string
			var lat, lon float64
			var placeType, geojson sql.NullString
			riverRows.Scan(&name, &lat, &lon, &placeType, &geojson)
			if geojson.Valid && geojson.String != "" {
				writeGeoJSONToKML(&kml, geojson.String, "water", name)
			} else {
				// Point for rivers without geometry
				pointGeoJSON := fmt.Sprintf(`{"type":"Point","coordinates":[%f,%f]}`, lon, lat)
				writeGeoJSONToKML(&kml, pointGeoJSON, "water", fmt.Sprintf("%s (%s)", name, placeType.String))
			}
		}
	}
	kml.WriteString("</Folder>\n")

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

	kml.WriteString(fmt.Sprintf("<Placemark><name>%s</name><styleUrl>#%s</styleUrl>", name, styleID))
	
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

	kml.WriteString(fmt.Sprintf("<Placemark><name>%s</name><styleUrl>#%s</styleUrl>", name, styleID))

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
		s.DB.QueryRow(`SELECT COUNT(*), COALESCE(SUM(total_distance_km), 0), COALESCE(AVG(intensity), 0) FROM grid_cells WHERE park_id = ?`, area.ID).Scan(&pixels, &patrolDist, &intensity)
		s.DB.QueryRow(`SELECT COUNT(*) FROM pa_publications WHERE pa_id = ?`, area.WDPAID).Scan(&pubs)
		
		results = append(results, ParkExport{
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
	
	// Get rivers (top 20 by discharge)
	rows, err := s.DB.QueryContext(ctx, `
		SELECT hyriv_id, COALESCE(river_name, ''), COALESCE(length_km, 0), 
		       COALESCE(discharge_cms, 0), COALESCE(stream_order, 0), relation, COALESCE(distance_km, 0)
		FROM park_rivers 
		WHERE park_id = ?
		ORDER BY discharge_cms DESC
		LIMIT 20
	`, internalID)
	if err == nil {
		defer rows.Close()
		majorRivers := []string{}
		for rows.Next() {
			var r River
			if rows.Scan(&r.HyrivID, &r.Name, &r.LengthKm, &r.DischargeCms, &r.StreamOrder, &r.Relation, &r.DistanceKm) == nil {
				response.Rivers = append(response.Rivers, r)
				if r.Name != "" && r.DischargeCms > 100 {
					majorRivers = append(majorRivers, r.Name)
				}
			}
		}
		response.Summary.MajorRivers = majorRivers
	}
	
	// Count total rivers
	s.DB.QueryRowContext(ctx, `SELECT COUNT(*) FROM park_rivers WHERE park_id = ?`, internalID).Scan(&response.Summary.TotalRivers)
	
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
