package srv

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
)

// HandleAPIFeaturesInBBox returns GeoJSON features of a given type within a
// bounding box, optionally filtered by date range. Used by the stats-panel
// layer toggles (fires / deforestation / settlements in current view).
//
// GET /api/features-in-bbox?type=fire_trajectory|deforestation|settlement
//   &bbox=minLng,minLat,maxLng,maxLat&from=YYYY-MM-DD&to=YYYY-MM-DD&limit=1500
func (s *Server) HandleAPIFeaturesInBBox(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	featureType := q.Get("type")
	switch featureType {
	case "fire_trajectory", "deforestation", "settlement":
	default:
		http.Error(w, "invalid type (fire_trajectory|deforestation|settlement)", http.StatusBadRequest)
		return
	}

	parts := strings.Split(q.Get("bbox"), ",")
	if len(parts) != 4 {
		http.Error(w, "bbox required: minLng,minLat,maxLng,maxLat", http.StatusBadRequest)
		return
	}
	var bbox [4]float64
	for i, p := range parts {
		v, err := strconv.ParseFloat(strings.TrimSpace(p), 64)
		if err != nil {
			http.Error(w, "invalid bbox", http.StatusBadRequest)
			return
		}
		bbox[i] = v
	}

	limit := 1500
	if l, err := strconv.Atoi(q.Get("limit")); err == nil && l > 0 && l <= 4000 {
		limit = l
	}

	// Bbox overlap test on precomputed bbox columns (indexed).
	query := `
		SELECT feature_type, feature_id, park_id, geojson, start_date, end_date, properties_json
		FROM feature_geometries
		WHERE feature_type = ?
		  AND bbox_maxx >= ? AND bbox_minx <= ?
		  AND bbox_maxy >= ? AND bbox_miny <= ?
	` + aoiExcludeSQL("park_id")
	args := []interface{}{featureType, bbox[0], bbox[2], bbox[1], bbox[3]}

	// Date filters match UI narrative behavior: filter on start_date.
	// Settlements mostly lack dates, so NULL start_date always passes.
	if from := q.Get("from"); from != "" {
		query += " AND (start_date IS NULL OR start_date >= ?)"
		args = append(args, from)
	}
	if to := q.Get("to"); to != "" {
		query += " AND (start_date IS NULL OR start_date <= ?)"
		args = append(args, to)
	}

	// Largest/most significant features first so the limit keeps the
	// most visible ones.
	query += fmt.Sprintf(" ORDER BY stat_value DESC, start_date DESC LIMIT %d", limit)

	rows, err := s.DB.QueryContext(r.Context(), query, args...)
	if err != nil {
		internalError(w, "request failed", err)
		return
	}
	defer rows.Close()

	type geoFeature struct {
		Type       string                 `json:"type"`
		Geometry   json.RawMessage        `json:"geometry"`
		Properties map[string]interface{} `json:"properties"`
	}

	features := []geoFeature{}
	for rows.Next() {
		var fType, fID, parkID, geojson string
		var startDate, endDate, propsJSON sql.NullString
		if err := rows.Scan(&fType, &fID, &parkID, &geojson, &startDate, &endDate, &propsJSON); err != nil {
			continue
		}
		props := make(map[string]interface{})
		if propsJSON.Valid {
			json.Unmarshal([]byte(propsJSON.String), &props)
		}
		props["feature_type"] = fType
		props["feature_id"] = fID
		props["park_id"] = parkID
		if startDate.Valid {
			props["start_date"] = startDate.String
		}
		if endDate.Valid {
			props["end_date"] = endDate.String
		}
		features = append(features, geoFeature{
			Type:       "Feature",
			Geometry:   json.RawMessage(geojson),
			Properties: props,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"type":     "FeatureCollection",
		"features": features,
		"truncated": len(features) >= limit,
	})
}
