package srv

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
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
// Query params:
//   - year: filter by year (optional, defaults to current year)
//   - month: filter by month (optional, 1-12)
//   - from/to: date range (optional, format: YYYY-MM-DD)
func (s *Server) HandleAPIGrid(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)

	// Parse query params - support both year/month and from/to date range
	yearStr := r.URL.Query().Get("year")
	monthStr := r.URL.Query().Get("month")
	fromStr := r.URL.Query().Get("from")
	toStr := r.URL.Query().Get("to")

	// Determine year range for query
	var fromYear, toYear int64
	now := time.Now()
	if fromStr != "" || toStr != "" {
		fromYear = int64(now.Year() - 1)
		toYear = int64(now.Year())
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
	} else if yearStr != "" {
		if y, err := strconv.ParseInt(yearStr, 10, 64); err == nil {
			fromYear = y
			toYear = y
		}
	} else {
		// Default to current year
		fromYear = int64(now.Year())
		toYear = int64(now.Year())
	}

	var features []GeoJSONFeature

	// Special case: single month query (no month counting needed)
	if monthStr != "" {
		month, parseErr := strconv.ParseInt(monthStr, 10, 64)
		if parseErr != nil || month < 1 || month > 12 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "invalid month parameter"})
			return
		}
		// Query each year and aggregate
		aggregated := make(map[string]*struct {
			LatCenter       float64
			LonCenter       float64
			TotalDistance   float64
			TotalPoints     int64
			UniqueUploads   int64
			MovementType    string
			CoveragePercent *float64
		})
		for year := fromYear; year <= toYear; year++ {
			rows, err := q.GetEffortDataByYearMonth(ctx, dbgen.GetEffortDataByYearMonthParams{
				Year:  year,
				Month: month,
			})
			if err != nil {
				continue
			}
			for _, row := range rows {
				if agg, exists := aggregated[row.GridCellID]; exists {
					agg.TotalDistance += row.TotalDistanceKm
					agg.TotalPoints += row.TotalPoints
					agg.UniqueUploads += row.UniqueUploads
					if row.CoveragePercent != nil && (agg.CoveragePercent == nil || *row.CoveragePercent > *agg.CoveragePercent) {
						agg.CoveragePercent = row.CoveragePercent
					}
				} else {
					aggregated[row.GridCellID] = &struct {
						LatCenter       float64
						LonCenter       float64
						TotalDistance   float64
						TotalPoints     int64
						UniqueUploads   int64
						MovementType    string
						CoveragePercent *float64
					}{
						LatCenter:       row.LatCenter,
						LonCenter:       row.LonCenter,
						TotalDistance:   row.TotalDistanceKm,
						TotalPoints:     row.TotalPoints,
						UniqueUploads:   row.UniqueUploads,
						MovementType:    row.MovementType,
						CoveragePercent: row.CoveragePercent,
					}
				}
			}
		}
		features = make([]GeoJSONFeature, 0, len(aggregated))
		for gridCellID, agg := range aggregated {
			// For single month, use 1 dry or rainy month based on which season
			var dryMonths, rainyMonths int64
			if month >= 11 || month <= 4 { // Nov-Apr = dry season
				dryMonths = 1
			} else {
				rainyMonths = 1
			}
			feature := buildGridFeature(
				gridCellID,
				agg.LatCenter,
				agg.LonCenter,
				agg.TotalDistance,
				agg.TotalPoints,
				agg.UniqueUploads,
				agg.MovementType,
				agg.CoveragePercent,
				dryMonths,
				rainyMonths,
			)
			features = append(features, feature)
		}
	} else {
		// Use the optimized SQL query that calculates month counts
		rows, err := q.GetEffortDataWithMonthCounts(ctx, dbgen.GetEffortDataWithMonthCountsParams{
			Year:   fromYear,
			Year_2: toYear,
		})
		if err != nil {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "database error"})
			return
		}

		features = make([]GeoJSONFeature, 0, len(rows))
		for _, row := range rows {
			// Convert nullable fields from SQL
			var totalDistance float64
			if row.TotalDistanceKm != nil {
				totalDistance = *row.TotalDistanceKm
			}
			var totalPoints int64
			if row.TotalPoints != nil {
				totalPoints = int64(*row.TotalPoints)
			}
			var uniqueUploads int64
			if row.UniqueUploads != nil {
				if v, ok := row.UniqueUploads.(int64); ok {
					uniqueUploads = v
				}
			}
			var coveragePercent *float64
			if row.CoveragePercent != nil {
				if v, ok := row.CoveragePercent.(float64); ok {
					coveragePercent = &v
				}
			}

			feature := buildGridFeature(
				row.GridCellID,
				row.LatCenter,
				row.LonCenter,
				totalDistance,
				totalPoints,
				uniqueUploads,
				"all", // movement_type is always 'all' in this query
				coveragePercent,
				row.DryMonths,
				row.RainyMonths,
			)
			features = append(features, feature)
		}
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
		// Use the polygon geometry directly from the area data
		feature := GeoJSONFeature{
			Type: "Feature",
			Geometry: GeoJSONGeometry{
				Type:        area.Geometry.Type,
				Coordinates: area.Geometry.Coordinates,
			},
			Properties: map[string]interface{}{
				"id":          area.ID,
				"name":        area.Name,
				"country":     area.Country,
				"country_code": area.CountryCode,
				"wdpa_id":     area.WDPAID,
				"area_km2":    area.AreaKm2,
				"partner":     area.Partner,
				"buffer_km":   area.BufferKm,
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
	q := dbgen.New(s.DB)

	// Parse date range
	fromStr := r.URL.Query().Get("from")
	toStr := r.URL.Query().Get("to")
	_ = r.URL.Query().Get("type") // typeFilter - reserved for future movement type filtering
	bboxStr := r.URL.Query().Get("bbox")

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
	seenPixels := make(map[string]bool)

	for year := fromYear; year <= toYear; year++ {
		var effortRows []dbgen.GetEffortDataByYearRow
		var err error

		// Use bbox-filtered query if bbox is provided
		if len(bbox) == 4 {
			bboxRows, bboxErr := q.GetEffortDataByBounds(ctx, dbgen.GetEffortDataByBoundsParams{
				LatCenter:    bbox[1], // minLat
				LatCenter_2:  bbox[3], // maxLat
				LonCenter:    bbox[0], // minLng
				LonCenter_2:  bbox[2], // maxLng
				Year:         year,
				Year_2:       year,
				Column7:      nil, // month (NULL)
				Month:        0,
				Column9:      nil, // movement_type (NULL = all)
				MovementType: "all",
			})
			if bboxErr == nil {
				// Convert to common format
				for _, row := range bboxRows {
					// Skip if bbox filtering and cell is outside bounds
					if row.LatCenter < bbox[1] || row.LatCenter > bbox[3] ||
						row.LonCenter < bbox[0] || row.LonCenter > bbox[2] {
						continue
					}

					// Only count "all" type to avoid double counting
					if row.MovementType != "all" {
						continue
					}

					if !seenPixels[row.GridCellID] {
						seenPixels[row.GridCellID] = true
						activePixels++
					}
					totalDistanceKm += row.TotalDistanceKm
					totalUploads += row.UniqueUploads
				}
			}
			continue
		}

		effortRows, err = q.GetEffortDataByYear(ctx, year)
		if err != nil {
			continue
		}

		for _, row := range effortRows {
			// Only count "all" type to avoid double counting
			if row.MovementType != "all" {
				continue
			}

			if !seenPixels[row.GridCellID] {
				seenPixels[row.GridCellID] = true
				activePixels++
			}
			totalDistanceKm += row.TotalDistanceKm
			totalUploads += row.UniqueUploads
		}
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

	// Deforestation totals in selected years
	s.DB.QueryRow(`
		SELECT COALESCE(SUM(area_km2), 0) FROM deforestation_events 
		WHERE year >= ? AND year <= ?
	`, fromYear, toYear).Scan(&totalDeforestation)

	// Previous period deforestation for trend
	yearSpan := toYear - fromYear + 1
	s.DB.QueryRow(`
		SELECT COALESCE(SUM(area_km2), 0) FROM deforestation_events 
		WHERE year >= ? AND year < ?
	`, fromYear-yearSpan, fromYear).Scan(&prevDeforestation)

	// Total settlements across all parks
	s.DB.QueryRow(`SELECT COUNT(*) FROM park_settlements`).Scan(&totalSettlements)

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
	w.Header().Set("Cache-Control", "public, max-age=300")
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
	
	// Build query
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

	var err error
	switch req.Type {
	case "road":
		err = q.ApproveLearnedRoad(ctx, dbgen.ApproveLearnedRoadParams{
			ApprovedBy: &approvedBy,
			ID:         req.ID,
		})
	case "place":
		err = q.ApproveLearnedPlace(ctx, dbgen.ApproveLearnedPlaceParams{
			ApprovedBy: &approvedBy,
			ID:         req.ID,
		})
	case "airstrip":
		err = q.ApproveLearnedAirstrip(ctx, dbgen.ApproveLearnedAirstripParams{
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

	var err error
	switch req.Type {
	case "road":
		err = q.RejectLearnedRoad(ctx, dbgen.RejectLearnedRoadParams{
			ApprovedBy: &rejectedBy,
			ID:         req.ID,
		})
	default:
		http.Error(w, "Invalid type - only roads support rejection", http.StatusBadRequest)
		return
	}

	if err != nil {
		http.Error(w, "Failed to reject feature", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "rejected"})
}
