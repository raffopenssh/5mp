package srv

import (
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
func (s *Server) HandleAPIGrid(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)

	// Parse query params - support both year/month and from/to date range
	yearStr := r.URL.Query().Get("year")
	monthStr := r.URL.Query().Get("month")
	fromStr := r.URL.Query().Get("from")
	toStr := r.URL.Query().Get("to")

	// If from/to provided, extract years to query
	var years []int64
	if fromStr != "" || toStr != "" {
		now := time.Now()
		fromYear := int64(now.Year() - 1)
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
		for y := fromYear; y <= toYear; y++ {
			years = append(years, y)
		}
	} else if yearStr != "" {
		if y, err := strconv.ParseInt(yearStr, 10, 64); err == nil {
			years = []int64{y}
		}
	} else {
		// Default to current year
		years = []int64{int64(time.Now().Year())}
	}

	// Aggregate data across all requested years
	aggregated := make(map[string]*struct {
		LatCenter       float64
		LonCenter       float64
		TotalDistance   float64
		TotalPoints     int64
		UniqueUploads   int64
		MovementType    string
		CoveragePercent *float64
	})

	for _, year := range years {
		var rows []dbgen.GetEffortDataByYearRow
		var err error

		if monthStr != "" {
			month, parseErr := strconv.ParseInt(monthStr, 10, 64)
			if parseErr != nil || month < 1 || month > 12 {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusBadRequest)
				json.NewEncoder(w).Encode(map[string]string{"error": "invalid month parameter"})
				return
			}
			monthRows, err := q.GetEffortDataByYearMonth(ctx, dbgen.GetEffortDataByYearMonthParams{
				Year:  year,
				Month: month,
			})
			if err != nil {
				continue
			}
			// Convert to common row type
			for _, r := range monthRows {
				rows = append(rows, dbgen.GetEffortDataByYearRow{
					GridCellID:      r.GridCellID,
					LatCenter:       r.LatCenter,
					LonCenter:       r.LonCenter,
					TotalDistanceKm: r.TotalDistanceKm,
					TotalPoints:     r.TotalPoints,
					UniqueUploads:   r.UniqueUploads,
					MovementType:    r.MovementType,
					CoveragePercent: r.CoveragePercent,
				})
			}
		} else {
			rows, err = q.GetEffortDataByYear(ctx, year)
			if err != nil {
				continue
			}
		}

		for _, row := range rows {
			if agg, exists := aggregated[row.GridCellID]; exists {
				agg.TotalDistance += row.TotalDistanceKm
				agg.TotalPoints += row.TotalPoints
				agg.UniqueUploads += row.UniqueUploads
				// Take max coverage across periods
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

	// Build GeoJSON features from aggregated data
	features := make([]GeoJSONFeature, 0, len(aggregated))

	for gridCellID, agg := range aggregated {
		feature := buildGridFeature(
			gridCellID,
			agg.LatCenter,
			agg.LonCenter,
			agg.TotalDistance,
			agg.TotalPoints,
			agg.UniqueUploads,
			agg.MovementType,
			agg.CoveragePercent,
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
// coveragePercent is the spatial coverage (0-100) of subcells visited within the 10x10km cell.
func buildGridFeature(gridCellID string, latCenter, lonCenter, totalDistanceKm float64, totalPoints, uniqueUploads int64, movementType string, coveragePercent *float64) GeoJSONFeature {
	// Calculate intensity based on SPATIAL COVERAGE of the 100 sq km cell
	// Each 10x10km cell is divided into 100 subcells of ~1km x 1km
	// Intensity = percentage of subcells visited
	// 80% spatial coverage = full intensity (1.0)
	// >80% = overglow effect
	var intensity float64
	if coveragePercent != nil && *coveragePercent > 0 {
		// Use actual spatial coverage
		intensity = *coveragePercent / 80.0 // 80% coverage = 1.0 intensity
	} else {
		// Fallback: estimate from distance (legacy data)
		// Assume 1km of patrol = ~1% coverage (rough approximation)
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
		s.Auth.Logout(r.Context(), cookie.Value)
	}
	auth.ClearSessionCookie(w)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

// HandleAPIStats returns global statistics.
func (s *Server) HandleAPIStats(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)
	year := int64(time.Now().Year())

	stats, err := q.GetGlobalStats(ctx, year)
	if err != nil {
		slog.Error("failed to get global stats", "error", err)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "database error"})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=30")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"active_pixels":     stats.ActivePixels,
		"total_distance_km": stats.TotalDistanceKm,
		"total_patrols":     stats.TotalUploads,
	})
}

// HandleAPIAreasSearch searches protected areas by name.
// Query params:
//   - q: search query (required)
// Returns matching PAs with center coordinates for map navigation.
// Results include both loaded (keystone) PAs and unloaded WDPA PAs.
func (s *Server) HandleAPIAreasSearch(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query().Get("q")
	if query == "" {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode([]interface{}{})
		return
	}

	// Case-insensitive search
	queryLower := strings.ToLower(query)
	results := make([]map[string]interface{}, 0, 20)

	// Track WDPA IDs we've already added from loaded areas
	loadedWDPAIDs := make(map[string]bool)

	// First, search loaded areas (keystones) - these show in green
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if strings.Contains(strings.ToLower(area.Name), queryLower) {
				// Calculate center from bounding box
				latMin, latMax, lonMin, lonMax := area.GetBoundingBox()
				centerLat := (latMin + latMax) / 2
				centerLon := (lonMin + lonMax) / 2

				results = append(results, map[string]interface{}{
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

				if len(results) >= 10 {
					break
				}
			}
		}
	}

	// Then, search WDPA index for additional unloaded areas - these show in grey
	if s.WDPAIndex != nil && len(results) < 20 {
		wdpaResults := s.WDPAIndex.Search(query, 20-len(results))
		for _, entry := range wdpaResults {
			// Skip if already added from loaded areas
			wdpaIDStr := strconv.Itoa(entry.WDPAID)
			if loadedWDPAIDs[wdpaIDStr] {
				continue
			}

			results = append(results, map[string]interface{}{
				"name":        entry.Name,
				"country":     entry.Country,
				"wdpa_id":     wdpaIDStr,
				"area_km2":    entry.AreaKm2,
				"designation": entry.Designation,
				"loaded":      false, // This PA is NOT loaded in the system
			})

			if len(results) >= 20 {
				break
			}
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
