package srv

import (
	"encoding/json"
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

	// Parse query params
	yearStr := r.URL.Query().Get("year")
	monthStr := r.URL.Query().Get("month")

	year := int64(time.Now().Year())
	if yearStr != "" {
		if y, err := strconv.ParseInt(yearStr, 10, 64); err == nil {
			year = y
		}
	}

	var rows []dbgen.GetEffortDataByYearRow
	var rowsWithMonth []dbgen.GetEffortDataByYearMonthRow
	var err error

	if monthStr != "" {
		month, parseErr := strconv.ParseInt(monthStr, 10, 64)
		if parseErr != nil || month < 1 || month > 12 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "invalid month parameter"})
			return
		}
		rowsWithMonth, err = q.GetEffortDataByYearMonth(ctx, dbgen.GetEffortDataByYearMonthParams{
			Year:  year,
			Month: month,
		})
	} else {
		rows, err = q.GetEffortDataByYear(ctx, year)
	}

	if err != nil {
		slog.Error("failed to query effort data", "error", err)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": "database error"})
		return
	}

	// Find max distance for normalization
	var maxDistance float64
	if monthStr != "" {
		for _, row := range rowsWithMonth {
			if row.TotalDistanceKm > maxDistance {
				maxDistance = row.TotalDistanceKm
			}
		}
	} else {
		for _, row := range rows {
			if row.TotalDistanceKm > maxDistance {
				maxDistance = row.TotalDistanceKm
			}
		}
	}

	// Build GeoJSON features
	features := make([]GeoJSONFeature, 0)

	if monthStr != "" {
		for _, row := range rowsWithMonth {
			feature := buildGridFeature(
				row.GridCellID,
				row.LatCenter,
				row.LonCenter,
				row.TotalDistanceKm,
				row.TotalPoints,
				row.UniqueUploads,
				row.MovementType,
				maxDistance,
			)
			features = append(features, feature)
		}
	} else {
		for _, row := range rows {
			feature := buildGridFeature(
				row.GridCellID,
				row.LatCenter,
				row.LonCenter,
				row.TotalDistanceKm,
				row.TotalPoints,
				row.UniqueUploads,
				row.MovementType,
				maxDistance,
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
func buildGridFeature(gridCellID string, latCenter, lonCenter, totalDistanceKm float64, totalPoints, uniqueUploads int64, movementType string, maxDistance float64) GeoJSONFeature {
	// Calculate intensity (normalized 0-1)
	var intensity float64
	if maxDistance > 0 {
		intensity = totalDistanceKm / maxDistance
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
				"id":        area.ID,
				"name":      area.Name,
				"country":   area.Country,
				"buffer_km": area.BufferKm,
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
func (s *Server) HandleAPIAreasSearch(w http.ResponseWriter, r *http.Request) {
	if s.AreaStore == nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"error": "area store not configured"})
		return
	}

	query := r.URL.Query().Get("q")
	if query == "" {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode([]interface{}{})
		return
	}

	// Case-insensitive search
	queryLower := strings.ToLower(query)
	results := make([]map[string]interface{}, 0, 10)

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
			})

			if len(results) >= 10 {
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
		if u.ProtectedAreaID != nil {
			location = *u.ProtectedAreaID
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
