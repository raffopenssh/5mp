package srv

import (
	"encoding/json"
	"fmt"
	"net/http"
)

// ParkAnalysisData is passed to the park_analysis template
type ParkAnalysisData struct {
	ID       string  `json:"id"`
	Name     string  `json:"name"`
	Lat      float64 `json:"lat"`
	Lon      float64 `json:"lon"`
	BBoxJSON string  `json:"bbox_json"`
}

// HandleParkAnalysis serves the park analysis page
func (s *Server) HandleParkAnalysis(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}

	var parkData ParkAnalysisData
	parkData.ID = parkID
	parkData.BBoxJSON = "null"

	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.ID == parkID {
				parkData.Name = area.Name
				
				// Parse coordinates to get center and bbox
				var coords interface{}
				if err := json.Unmarshal(area.Geometry.Coordinates, &coords); err == nil {
					bbox := extractBBox(area.Geometry.Type, coords)
					if len(bbox) == 4 {
						parkData.Lat = (bbox[1] + bbox[3]) / 2
						parkData.Lon = (bbox[0] + bbox[2]) / 2
						bboxBytes, _ := json.Marshal(bbox)
						parkData.BBoxJSON = string(bboxBytes)
					}
				}
				break
			}
		}
	}

	if parkData.Name == "" {
		parkData.Name = parkID
	}

	s.renderTemplate(w, "park_analysis.html", parkData)
}

// extractBBox extracts bounding box from parsed coordinates
func extractBBox(geomType string, coords interface{}) []float64 {
	var minLon, minLat, maxLon, maxLat float64
	first := true
	
	var processCoord func(c interface{})
	processCoord = func(c interface{}) {
		switch v := c.(type) {
		case []interface{}:
			if len(v) >= 2 {
				// Check if this is a coordinate pair [lon, lat]
				if lon, ok := v[0].(float64); ok {
					if lat, ok := v[1].(float64); ok {
						if first {
							minLon, maxLon = lon, lon
							minLat, maxLat = lat, lat
							first = false
						} else {
							if lon < minLon { minLon = lon }
							if lon > maxLon { maxLon = lon }
							if lat < minLat { minLat = lat }
							if lat > maxLat { maxLat = lat }
						}
						return
					}
				}
			}
			// Not a coordinate pair, recurse into array
			for _, item := range v {
				processCoord(item)
			}
		}
	}
	
	processCoord(coords)
	
	if first {
		return nil
	}
	return []float64{minLon, minLat, maxLon, maxLat}
}

// HandleParkBoundary returns the park boundary as GeoJSON
func (s *Server) HandleParkBoundary(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}

	if s.AreaStore == nil {
		http.Error(w, "Area store not initialized", http.StatusInternalServerError)
		return
	}

	for _, area := range s.AreaStore.Areas {
		if area.ID == parkID {
			if len(area.Geometry.Coordinates) == 0 {
				http.Error(w, "No boundary data", http.StatusNotFound)
				return
			}

			// Parse raw coordinates
			var coords interface{}
			if err := json.Unmarshal(area.Geometry.Coordinates, &coords); err != nil {
				http.Error(w, "Invalid geometry", http.StatusInternalServerError)
				return
			}

			// Return as GeoJSON Feature
			feature := map[string]interface{}{
				"type": "Feature",
				"properties": map[string]interface{}{
					"id":   area.ID,
					"name": area.Name,
				},
				"geometry": map[string]interface{}{
					"type":        area.Geometry.Type,
					"coordinates": coords,
				},
			}

			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]interface{}{
				"type":     "FeatureCollection",
				"features": []interface{}{feature},
			})
			return
		}
	}

	http.Error(w, "Park not found", http.StatusNotFound)
}

// HandleParkRoads returns road data for a park from OSM
func (s *Server) HandleParkRoads(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}

	// Find park bbox
	var bbox []float64
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.ID == parkID {
				var coords interface{}
				if err := json.Unmarshal(area.Geometry.Coordinates, &coords); err == nil {
					bbox = extractBBox(area.Geometry.Type, coords)
				}
				break
			}
		}
	}

	if len(bbox) != 4 {
		http.Error(w, "Park bbox not found", http.StatusNotFound)
		return
	}

	// TODO: Query Overpass API for roads in bbox
	result := map[string]interface{}{
		"type":     "FeatureCollection",
		"features": []interface{}{},
		"properties": map[string]interface{}{
			"roadless_percent": 0.0,
			"total_road_km":    0.0,
			"data_source":      "osm",
			"note":             "Road data pending - Overpass API integration needed",
		},
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(result)
}

// FetchOSMRoads fetches road data from Overpass API
func FetchOSMRoads(bbox []float64) ([]byte, error) {
	if len(bbox) != 4 {
		return nil, fmt.Errorf("invalid bbox")
	}

	query := fmt.Sprintf(`
		[out:json][timeout:60];
		(
		  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|track)$"](%f,%f,%f,%f);
		);
		out geom;
	`, bbox[1], bbox[0], bbox[3], bbox[2])

	_ = query
	return nil, fmt.Errorf("not implemented")
}
