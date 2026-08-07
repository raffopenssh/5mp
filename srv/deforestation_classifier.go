package srv

import (
	"database/sql"
	"fmt"
	"math"
	"strings"
)

// Deforestation classification types
const (
	DeforestSlashBurn    = "slash_burn"      // Agricultural clearing with fire
	DeforestLogging      = "logging"         // Commercial timber extraction
	DeforestMining       = "mining"          // Mining-related clearing
	DeforestInfra        = "infrastructure"  // Road/building construction
	DeforestEncroachment = "encroachment"    // Settlement expansion
	DeforestNatural      = "natural"         // Natural disturbance (storm, disease)
	DeforestUnknown      = "unknown"
)

// ClassifiedDeforestation holds deforestation event with classification
type ClassifiedDeforestation struct {
	ID              int64   `json:"id"`
	ParkID          string  `json:"park_id"`
	Year            int     `json:"year"`
	AreaKm2         float64 `json:"area_km2"`
	Lat             float64 `json:"lat"`
	Lon             float64 `json:"lon"`
	Classification  string  `json:"classification"`
	Confidence      float64 `json:"confidence"`
	Narrative       string  `json:"narrative"`
	OriginalPattern string  `json:"original_pattern"`
	PolygonIDs      string  `json:"polygon_ids,omitempty"` // Links to feature_geometries
	
	// Context
	NearestPlace      string  `json:"nearest_place,omitempty"`
	DistanceToPlace   float64 `json:"distance_to_place_km,omitempty"`
	NearestRiver      string  `json:"nearest_river,omitempty"`
	DistanceToRiver   float64 `json:"distance_to_river_km,omitempty"`
	NearestSettlement float64 `json:"nearest_settlement_km,omitempty"`
	
	// Fire correlation
	FiresSameYear     int     `json:"fires_same_year"`
	FiresPriorYear    int     `json:"fires_prior_year"`
	FireRatio         float64 `json:"fire_ratio"` // fires per km2 deforested
	
	// Spatial pattern
	IsLinear          bool    `json:"is_linear"`
	IsNearRoad        bool    `json:"is_near_road"`
	IsNearSettlement  bool    `json:"is_near_settlement"`
}

// ClassifyDeforestation determines the cause of deforestation
func (s *Server) ClassifyDeforestation(parkID string, df *ClassifiedDeforestation) {
	s.loadDeforestContext(parkID, df)
	
	scores := map[string]float64{
		DeforestSlashBurn:    s.scoreSlashBurn(df),
		DeforestLogging:      s.scoreLogging(df),
		DeforestMining:       s.scoreDeforestMining(df),
		DeforestEncroachment: s.scoreEncroachment(df),
		DeforestNatural:      s.scoreNatural(df),
	}
	
	bestClass := DeforestUnknown
	bestScore := 0.0
	for class, score := range scores {
		if score > bestScore {
			bestScore = score
			bestClass = class
		}
	}
	
	if bestScore < 0.25 {
		bestClass = DeforestUnknown
	}
	
	df.Classification = bestClass
	df.Confidence = bestScore
	df.Narrative = s.buildDeforestNarrativeText(df)
}

func (s *Server) loadDeforestContext(parkID string, df *ClassifiedDeforestation) {
	// Nearest river
	var riverName sql.NullString
	var riverLat, riverLon sql.NullFloat64
	s.DB.QueryRow(`
		SELECT name, lat, lon FROM osm_places 
		WHERE park_id = ? AND place_type IN ('river', 'stream') AND name != ''
		ORDER BY (lat - ?)*(lat - ?) + (lon - ?)*(lon - ?)
		LIMIT 1
	`, parkID, df.Lat, df.Lat, df.Lon, df.Lon).Scan(&riverName, &riverLat, &riverLon)
	
	if riverName.Valid {
		df.NearestRiver = riverName.String
		df.DistanceToRiver = haversineDistance(df.Lat, df.Lon, riverLat.Float64, riverLon.Float64)
	}
	
	// Nearest village/town
	var placeName sql.NullString
	var placeLat, placeLon sql.NullFloat64
	s.DB.QueryRow(`
		SELECT name, lat, lon FROM osm_places 
		WHERE park_id = ? AND place_type IN ('village', 'town', 'hamlet') AND name != ''
		ORDER BY (lat - ?)*(lat - ?) + (lon - ?)*(lon - ?)
		LIMIT 1
	`, parkID, df.Lat, df.Lat, df.Lon, df.Lon).Scan(&placeName, &placeLat, &placeLon)
	
	if placeName.Valid {
		df.NearestPlace = placeName.String
		df.DistanceToPlace = haversineDistance(df.Lat, df.Lon, placeLat.Float64, placeLon.Float64)
	}
	
	// Distance to nearest settlement (GHSL)
	var settDist sql.NullFloat64
	s.DB.QueryRow(`
		SELECT MIN(
			SQRT(POW((lat - ?) * 111, 2) + POW((lon - ?) * 111 * COS(? * 3.14159/180), 2))
		)
		FROM park_settlements WHERE park_id = ?
	`, df.Lat, df.Lon, df.Lat, parkID).Scan(&settDist)
	
	if settDist.Valid {
		df.NearestSettlement = settDist.Float64
		df.IsNearSettlement = settDist.Float64 < 5
	}
	
	// Fire correlation, same year then prior year.
	//
	// ⚠️ `latitude BETWEEN ? AND ?`, never `ABS(latitude - ?) < ?`: the ABS form
	// is non-sargable, drops idx_fire_location and covering-scans 42.9M rows
	// (~1000x slower, measured 2026-08-07). Two calls per deforestation event
	// across 221k events. Same bug class as _get_fire_density
	// (AGENTS.md "Areas of interest").
	yearStart := fmt.Sprintf("%d-01-01", df.Year)
	yearEnd := fmt.Sprintf("%d-12-31", df.Year)
	s.DB.QueryRow(`
		SELECT COUNT(*) FROM fire_detections
		WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
		AND acq_date BETWEEN ? AND ?
	`, df.Lat-0.1, df.Lat+0.1, df.Lon-0.1, df.Lon+0.1,
		yearStart, yearEnd).Scan(&df.FiresSameYear)
	
	priorStart := fmt.Sprintf("%d-01-01", df.Year-1)
	priorEnd := fmt.Sprintf("%d-12-31", df.Year-1)
	s.DB.QueryRow(`
		SELECT COUNT(*) FROM fire_detections
		WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
		AND acq_date BETWEEN ? AND ?
	`, df.Lat-0.1, df.Lat+0.1, df.Lon-0.1, df.Lon+0.1,
		priorStart, priorEnd).Scan(&df.FiresPriorYear)
	
	// Fire ratio
	if df.AreaKm2 > 0 {
		df.FireRatio = float64(df.FiresSameYear+df.FiresPriorYear) / df.AreaKm2
	}
	
	// Check if linear pattern
	df.IsLinear = strings.Contains(df.OriginalPattern, "linear") || strings.Contains(df.OriginalPattern, "edge")
	
	// Check if near road (from roads_heigit - extract from geojson)
	var roadDist sql.NullFloat64
	s.DB.QueryRow(`
		SELECT MIN(
			SQRT(
				POW((json_extract(geojson, '$.coordinates[0][1]') - ?) * 111, 2) + 
				POW((json_extract(geojson, '$.coordinates[0][0]') - ?) * 111 * COS(? * 3.14159/180), 2)
			)
		)
		FROM roads_heigit WHERE park_id = ? AND geojson IS NOT NULL
	`, df.Lat, df.Lon, df.Lat, parkID).Scan(&roadDist)
	
	if roadDist.Valid {
		df.IsNearRoad = roadDist.Float64 < 2 // Within 2km of a road
	}
}

// Scoring functions

func (s *Server) scoreSlashBurn(df *ClassifiedDeforestation) float64 {
	score := 0.0
	
	// High fire correlation is key indicator
	if df.FireRatio > 50 {
		score += 0.5
	} else if df.FireRatio > 20 {
		score += 0.3
	} else if df.FireRatio > 5 {
		score += 0.15
	}
	
	// Scattered pattern typical of smallholder agriculture
	if strings.Contains(df.OriginalPattern, "scattered") {
		score += 0.25
	}
	
	// Near settlements
	if df.IsNearSettlement {
		score += 0.15
	}
	
	// Small to medium area
	if df.AreaKm2 > 0.1 && df.AreaKm2 < 5 {
		score += 0.1
	}
	
	return math.Min(score, 1.0)
}

func (s *Server) scoreLogging(df *ClassifiedDeforestation) float64 {
	score := 0.0
	
	// Linear/edge patterns suggest road-based logging
	if df.IsLinear {
		score += 0.4
	}
	
	// Large area
	if df.AreaKm2 > 2 {
		score += 0.25
	} else if df.AreaKm2 > 0.5 {
		score += 0.15
	}
	
	// Low fire correlation (logging doesn't need fire)
	if df.FireRatio < 10 {
		score += 0.2
	}
	
	// Far from settlements
	if !df.IsNearSettlement && df.NearestSettlement > 10 {
		score += 0.15
	}
	
	return math.Min(score, 1.0)
}

func (s *Server) scoreDeforestMining(df *ClassifiedDeforestation) float64 {
	score := 0.0
	
	// Near rivers (alluvial mining)
	if df.DistanceToRiver < 2 {
		score += 0.4
	} else if df.DistanceToRiver < 5 {
		score += 0.2
	}
	
	// Concentrated pattern
	if strings.Contains(df.OriginalPattern, "concentrated") {
		score += 0.25
	}
	
	// Low fire activity
	if df.FireRatio < 5 {
		score += 0.2
	}
	
	// Moderate area
	if df.AreaKm2 > 0.5 && df.AreaKm2 < 10 {
		score += 0.15
	}
	
	return math.Min(score, 1.0)
}

func (s *Server) scoreEncroachment(df *ClassifiedDeforestation) float64 {
	score := 0.0
	
	// Very close to settlements
	if df.NearestSettlement < 2 {
		score += 0.5
	} else if df.NearestSettlement < 5 {
		score += 0.3
	}
	
	// Close to named places
	if df.DistanceToPlace < 10 {
		score += 0.2
	}
	
	// Edge/expanding pattern
	if strings.Contains(df.OriginalPattern, "edge") || strings.Contains(df.OriginalPattern, "expanding") {
		score += 0.2
	}
	
	// Moderate fire activity
	if df.FireRatio > 5 && df.FireRatio < 30 {
		score += 0.1
	}
	
	return math.Min(score, 1.0)
}

func (s *Server) scoreNatural(df *ClassifiedDeforestation) float64 {
	score := 0.0
	
	// Far from settlements AND places
	if df.NearestSettlement > 20 && df.DistanceToPlace > 30 {
		score += 0.3
	}
	
	// Very low fire activity
	if df.FireRatio < 2 {
		score += 0.3
	}
	
	// Minor/isolated pattern
	if strings.Contains(df.OriginalPattern, "minor") || strings.Contains(df.OriginalPattern, "isolated") {
		score += 0.2
	}
	
	// Small area
	if df.AreaKm2 < 0.5 {
		score += 0.2
	}
	
	return math.Min(score, 1.0)
}

func (s *Server) buildDeforestNarrativeText(df *ClassifiedDeforestation) string {
	// Build location string
	var locParts []string
	if df.NearestPlace != "" && df.DistanceToPlace < 50 {
		locParts = append(locParts, fmt.Sprintf("%.0fkm from %s", df.DistanceToPlace, df.NearestPlace))
	}
	if df.NearestRiver != "" && df.DistanceToRiver < 15 {
		locParts = append(locParts, fmt.Sprintf("%.1fkm from %s River", df.DistanceToRiver, df.NearestRiver))
	}
	
	location := ""
	if len(locParts) > 0 {
		location = strings.Join(locParts, ", ")
	} else {
		location = fmt.Sprintf("at (%.3f°, %.3f°)", df.Lat, df.Lon)
	}
	
	// Classification-specific narrative
	switch df.Classification {
	case DeforestSlashBurn:
		return fmt.Sprintf("In %d, %.2f km² cleared %s — likely slash-and-burn agriculture. %d fire detections in the area that year (%.0f fires/km²) indicate active burning for land preparation.",
			df.Year, df.AreaKm2, location, df.FiresSameYear, df.FireRatio)
		
	case DeforestLogging:
		return fmt.Sprintf("In %d, %.2f km² of forest loss %s shows linear clearing pattern suggesting commercial logging. Low fire activity (%d detections) consistent with mechanical extraction.",
			df.Year, df.AreaKm2, location, df.FiresSameYear)
		
	case DeforestMining:
		return fmt.Sprintf("In %d, %.2f km² cleared %s near %s River — pattern consistent with alluvial mining operations. Concentrated clearing with minimal fire use.",
			df.Year, df.AreaKm2, location, df.NearestRiver)
		
	case DeforestEncroachment:
		return fmt.Sprintf("In %d, %.2f km² lost %s — settlement encroachment pattern. Proximity to existing community (%.1fkm) suggests expansion of agricultural or residential land.",
			df.Year, df.AreaKm2, location, df.NearestSettlement)
		
	case DeforestNatural:
		return fmt.Sprintf("In %d, %.2f km² of canopy loss %s in remote area. Minimal human indicators suggest natural disturbance (windthrow, disease, or flooding).",
			df.Year, df.AreaKm2, location)
		
	default:
		return fmt.Sprintf("In %d, %.2f km² of forest loss detected %s. %d associated fire detections.",
			df.Year, df.AreaKm2, location, df.FiresSameYear)
	}
}

// GetClassifiedDeforestation returns deforestation events with classifications
func (s *Server) GetClassifiedDeforestation(parkID string) []ClassifiedDeforestation {
	rows, err := s.DB.Query(`
		SELECT id, park_id, year, area_km2, lat, lon, COALESCE(pattern_type, '')
		FROM deforestation_events
		WHERE park_id = ?
		ORDER BY year DESC
	`, parkID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	
	var events []ClassifiedDeforestation
	for rows.Next() {
		var df ClassifiedDeforestation
		err := rows.Scan(&df.ID, &df.ParkID, &df.Year, &df.AreaKm2, &df.Lat, &df.Lon, &df.OriginalPattern)
		if err != nil {
			continue
		}
		
		s.ClassifyDeforestation(parkID, &df)
		events = append(events, df)
	}
	
	return events
}

// Helper to get direction string
func getDirectionString(fromLat, fromLon, toLat, toLon float64) string {
	dLat := toLat - fromLat
	dLon := toLon - fromLon
	
	if math.Abs(dLat) < 0.01 && math.Abs(dLon) < 0.01 {
		return ""
	}
	
	angle := math.Atan2(dLon, dLat) * 180 / math.Pi
	if angle < 0 {
		angle += 360
	}
	
	dirs := []string{"north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"}
	idx := int((angle+22.5)/45) % 8
	return dirs[idx]
}
