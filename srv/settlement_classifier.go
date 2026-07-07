package srv

import (
	"database/sql"
	"fmt"
	"math"
	"strings"
)

// SettlementClassification types
const (
	ClassAgricultural    = "agricultural"     // Slash-and-burn, farm plots
	ClassMining          = "mining"           // Mining operation
	ClassRangerBase      = "ranger_base"      // Patrol/ranger station
	ClassFishing         = "fishing"          // Fishing camp near water
	ClassLogging         = "logging"          // Timber operation
	ClassPastoral        = "pastoral"         // Herding/grazing
	ClassResidential     = "residential"      // Permanent village
	ClassTemporaryCamp   = "temporary_camp"   // Seasonal/temporary
	ClassUnknown         = "unknown"
)

// ClassifiedSettlement holds settlement with classification data
type ClassifiedSettlement struct {
	ID              int64   `json:"id"`
	ParkID          string  `json:"park_id"`
	Lat             float64 `json:"lat"`
	Lon             float64 `json:"lon"`
	AreaM2          float64 `json:"area_m2"`
	PopulationEst   int64   `json:"population_est"`
	Classification  string  `json:"classification"`
	Confidence      float64 `json:"confidence"`
	Narrative       string  `json:"narrative"`
	
	// Context data used for classification
	NearestPlace    string  `json:"nearest_place,omitempty"`
	DistanceToPlace float64 `json:"distance_to_place_km,omitempty"`
	NearestRiver    string  `json:"nearest_river,omitempty"`
	DistanceToRiver float64 `json:"distance_to_river_km,omitempty"`
	NearestRoad     float64 `json:"nearest_road_km,omitempty"`
	
	// Fire metrics
	FiresWithin1km  int     `json:"fires_1km"`
	FiresWithin5km  int     `json:"fires_5km"`
	FireSeasonality string  `json:"fire_seasonality,omitempty"` // "dry", "wet", "year-round"
	
	// Deforestation metrics  
	DeforestNearby  float64 `json:"deforest_nearby_km2"`
	DeforestPattern string  `json:"deforest_pattern,omitempty"`

	// Mining evidence (Sentinel-2 river turbidity + GFW integrated alerts)
	TurbidityAlertKm   float64         `json:"turbidity_alert_km,omitempty"`   // distance to nearest turbidity onset
	TurbidityAlert     *TurbidityAlert `json:"turbidity_alert,omitempty"`      // the alert itself, when close
	GFWAlertsWithin5km int             `json:"gfw_alerts_5km,omitempty"`       // GFW integrated alerts within 5km
	PitSiteKm          float64         `json:"pit_site_km,omitempty"`          // distance to nearest detected pit cluster
	PitSite            *pitSite        `json:"pit_site,omitempty"`             // the pit detection, when close
}

// ClassifySettlement determines the type of a settlement based on multiple indicators
func (s *Server) ClassifySettlement(parkID string, settlement *ClassifiedSettlement) {
	// Load contextual data
	s.loadSettlementContext(parkID, settlement)
	
	// Score each classification
	scores := map[string]float64{
		ClassAgricultural:  s.scoreAgricultural(settlement),
		ClassMining:        s.scoreMining(settlement),
		ClassFishing:       s.scoreFishing(settlement),
		ClassPastoral:      s.scorePastoral(settlement),
		ClassResidential:   s.scoreResidential(settlement),
		ClassTemporaryCamp: s.scoreTemporary(settlement),
	}
	
	// Find highest score
	bestClass := ClassUnknown
	bestScore := 0.0
	for class, score := range scores {
		if score > bestScore {
			bestScore = score
			bestClass = class
		}
	}
	
	if bestScore < 0.3 {
		bestClass = ClassUnknown
	}
	
	settlement.Classification = bestClass
	settlement.Confidence = bestScore
	settlement.Narrative = s.buildSettlementNarrativeText(settlement)
}

func (s *Server) loadSettlementContext(parkID string, st *ClassifiedSettlement) {
	// Get nearest river
	var riverName sql.NullString
	var riverLat, riverLon sql.NullFloat64
	s.DB.QueryRow(`
		SELECT name, lat, lon FROM osm_places 
		WHERE park_id = ? AND place_type IN ('river', 'stream') AND name != ''
		ORDER BY (lat - ?)*(lat - ?) + (lon - ?)*(lon - ?)
		LIMIT 1
	`, parkID, st.Lat, st.Lat, st.Lon, st.Lon).Scan(&riverName, &riverLat, &riverLon)
	
	if riverName.Valid {
		st.NearestRiver = riverName.String
		st.DistanceToRiver = haversineDistance(st.Lat, st.Lon, riverLat.Float64, riverLon.Float64)
	}
	
	// Get nearest place
	var placeName sql.NullString
	var placeLat, placeLon sql.NullFloat64
	s.DB.QueryRow(`
		SELECT name, lat, lon FROM osm_places 
		WHERE park_id = ? AND place_type IN ('village', 'town', 'hamlet') AND name != ''
		ORDER BY (lat - ?)*(lat - ?) + (lon - ?)*(lon - ?)
		LIMIT 1
	`, parkID, st.Lat, st.Lat, st.Lon, st.Lon).Scan(&placeName, &placeLat, &placeLon)
	
	if placeName.Valid {
		st.NearestPlace = placeName.String
		st.DistanceToPlace = haversineDistance(st.Lat, st.Lon, placeLat.Float64, placeLon.Float64)
	}
	
	// Count fires at different distances
	s.DB.QueryRow(`
		SELECT 
			COUNT(CASE WHEN ABS(latitude - ?) < 0.01 AND ABS(longitude - ?) < 0.01 THEN 1 END),
			COUNT(CASE WHEN ABS(latitude - ?) < 0.05 AND ABS(longitude - ?) < 0.05 THEN 1 END)
		FROM fire_detections
		WHERE ABS(latitude - ?) < 0.1 AND ABS(longitude - ?) < 0.1
	`, st.Lat, st.Lon, st.Lat, st.Lon, st.Lat, st.Lon).Scan(&st.FiresWithin1km, &st.FiresWithin5km)
	
	// Get fire seasonality (which months have most fires)
	var dryFires, wetFires int
	s.DB.QueryRow(`
		SELECT 
			COUNT(CASE WHEN CAST(SUBSTR(acq_date, 6, 2) AS INT) IN (12, 1, 2, 3) THEN 1 END),
			COUNT(CASE WHEN CAST(SUBSTR(acq_date, 6, 2) AS INT) IN (4, 5, 6, 7, 8, 9, 10, 11) THEN 1 END)
		FROM fire_detections
		WHERE ABS(latitude - ?) < 0.05 AND ABS(longitude - ?) < 0.05
	`, st.Lat, st.Lon).Scan(&dryFires, &wetFires)
	
	if dryFires > wetFires*3 {
		st.FireSeasonality = "dry_season"
	} else if wetFires > dryFires*3 {
		st.FireSeasonality = "wet_season"
	} else if dryFires+wetFires > 0 {
		st.FireSeasonality = "year_round"
	}
	
	// Sum nearby deforestation
	s.DB.QueryRow(`
		SELECT COALESCE(SUM(area_km2), 0), GROUP_CONCAT(DISTINCT pattern_type)
		FROM deforestation_events
		WHERE park_id = ? 
		AND ABS(lat - ?) < 0.2 AND ABS(lon - ?) < 0.2
	`, parkID, st.Lat, st.Lon).Scan(&st.DeforestNearby, &st.DeforestPattern)
	
	// Get nearest road from roads_heigit (extract centroid from geojson)
	var roadLon, roadLat sql.NullFloat64
	s.DB.QueryRow(`
		SELECT 
			json_extract(geojson, '$.coordinates[0][0]') as lon,
			json_extract(geojson, '$.coordinates[0][1]') as lat
		FROM roads_heigit
		WHERE park_id = ? AND geojson IS NOT NULL
		ORDER BY 
			(json_extract(geojson, '$.coordinates[0][1]') - ?) * (json_extract(geojson, '$.coordinates[0][1]') - ?) +
			(json_extract(geojson, '$.coordinates[0][0]') - ?) * (json_extract(geojson, '$.coordinates[0][0]') - ?)
		LIMIT 1
	`, parkID, st.Lat, st.Lat, st.Lon, st.Lon).Scan(&roadLon, &roadLat)
	
	if roadLat.Valid && roadLon.Valid {
		st.NearestRoad = haversineDistance(st.Lat, st.Lon, roadLat.Float64, roadLon.Float64)
	}

	// Mining evidence: river turbidity onsets (Sentinel-2) + GFW alert clusters
	st.TurbidityAlertKm = 1e9
	if d, a := nearestTurbidityAlertKm(parkID, st.Lat, st.Lon); a != nil {
		st.TurbidityAlertKm = d
		if d < 15 {
			st.TurbidityAlert = a
		}
	}
	st.GFWAlertsWithin5km = gfwAlertsNearby(parkID, st.Lat, st.Lon, 5)
	st.PitSiteKm = 1e9
	if d, p := nearestPitSiteKm(parkID, st.Lat, st.Lon); p != nil {
		st.PitSiteKm = d
		if d < 5 {
			st.PitSite = p
		}
	}
}

// Scoring functions for each classification

func (s *Server) scoreAgricultural(st *ClassifiedSettlement) float64 {
	score := 0.0
	
	// High fires nearby + dry season = slash and burn
	if st.FiresWithin5km > 100 && st.FireSeasonality == "dry_season" {
		score += 0.4
	} else if st.FiresWithin5km > 50 {
		score += 0.2
	}
	
	// Scattered deforestation pattern
	if strings.Contains(st.DeforestPattern, "scattered") {
		score += 0.3
	}
	
	// Moderate deforestation nearby
	if st.DeforestNearby > 0.5 && st.DeforestNearby < 10 {
		score += 0.2
	}
	
	// Small to medium size
	if st.AreaM2 > 10000 && st.AreaM2 < 200000 {
		score += 0.1
	}
	
	return math.Min(score, 1.0)
}

func (s *Server) scoreMining(st *ClassifiedSettlement) float64 {
	score := 0.0
	
	// Near rivers (alluvial mining)
	if st.DistanceToRiver < 2 {
		score += 0.3
	}
	
	// Concentrated/linear deforestation
	if strings.Contains(st.DeforestPattern, "linear") || strings.Contains(st.DeforestPattern, "concentrated") {
		score += 0.3
	}
	
	// High deforestation
	if st.DeforestNearby > 5 {
		score += 0.2
	}
	
	// Few fires (mining doesn't need burning)
	if st.FiresWithin5km < 20 {
		score += 0.1
	}
	
	// Far from villages
	if st.DistanceToPlace > 20 {
		score += 0.1
	}
	
	// River turbidity onset nearby (Sentinel-2 sediment plume) — strongest
	// signal for alluvial gold mining; confirmed at Chinko headwaters.
	if st.TurbidityAlertKm < 3 {
		score += 0.5
	} else if st.TurbidityAlertKm < 10 {
		score += 0.3
	}
	
	// GFW integrated alerts clustered nearby (fresh canopy disturbance)
	if st.GFWAlertsWithin5km > 100 {
		score += 0.2
	} else if st.GFWAlertsWithin5km > 20 {
		score += 0.1
	}

	// Detected pit cluster nearby (Sentinel-2 bright-bare, persistent, riverside)
	if st.PitSiteKm < 1 {
		score += 0.5
	} else if st.PitSiteKm < 5 {
		score += 0.2
	}

	return math.Min(score, 1.0)
}

func (s *Server) scoreFishing(st *ClassifiedSettlement) float64 {
	score := 0.0
	
	// Very close to river/water
	if st.DistanceToRiver < 1 {
		score += 0.5
	} else if st.DistanceToRiver < 3 {
		score += 0.3
	}
	
	// Small settlement
	if st.AreaM2 < 50000 {
		score += 0.2
	}
	
	// Low fire activity
	if st.FiresWithin5km < 30 {
		score += 0.2
	}
	
	// Low deforestation
	if st.DeforestNearby < 0.5 {
		score += 0.1
	}
	
	return math.Min(score, 1.0)
}

func (s *Server) scorePastoral(st *ClassifiedSettlement) float64 {
	score := 0.0
	
	// Seasonal fires (grazing management)
	if st.FireSeasonality == "dry_season" && st.FiresWithin5km > 30 && st.FiresWithin5km < 200 {
		score += 0.3
	}
	
	// Little deforestation (grassland, not forest)
	if st.DeforestNearby < 0.3 {
		score += 0.3
	}
	
	// Far from rivers
	if st.DistanceToRiver > 5 {
		score += 0.2
	}
	
	// Small settlement
	if st.AreaM2 < 30000 {
		score += 0.2
	}
	
	return math.Min(score, 1.0)
}

func (s *Server) scoreResidential(st *ClassifiedSettlement) float64 {
	score := 0.0
	
	// Close to named places
	if st.DistanceToPlace < 5 {
		score += 0.4
	} else if st.DistanceToPlace < 15 {
		score += 0.2
	}
	
	// Larger settlement
	if st.AreaM2 > 100000 {
		score += 0.3
	} else if st.AreaM2 > 50000 {
		score += 0.2
	}
	
	// Higher population
	if st.PopulationEst > 500 {
		score += 0.2
	}
	
	// Moderate fire activity
	if st.FiresWithin5km > 20 && st.FiresWithin5km < 100 {
		score += 0.1
	}
	
	return math.Min(score, 1.0)
}

func (s *Server) scoreTemporary(st *ClassifiedSettlement) float64 {
	score := 0.0
	
	// Very small
	if st.AreaM2 < 20000 {
		score += 0.4
	}
	
	// Low population
	if st.PopulationEst < 100 {
		score += 0.2
	}
	
	// Far from villages
	if st.DistanceToPlace > 30 {
		score += 0.2
	}
	
	// Seasonal fires
	if st.FireSeasonality == "dry_season" {
		score += 0.2
	}
	
	return math.Min(score, 1.0)
}

func (s *Server) buildSettlementNarrativeText(st *ClassifiedSettlement) string {
	var parts []string
	
	// Location context
	if st.NearestPlace != "" && st.DistanceToPlace < 50 {
		parts = append(parts, fmt.Sprintf("%.0fkm from %s", st.DistanceToPlace, st.NearestPlace))
	}
	
	if st.NearestRiver != "" && st.DistanceToRiver < 10 {
		parts = append(parts, fmt.Sprintf("%.1fkm from %s River", st.DistanceToRiver, st.NearestRiver))
	}
	
	location := ""
	if len(parts) > 0 {
		location = strings.Join(parts, ", ")
	} else {
		location = fmt.Sprintf("at coordinates (%.3f°, %.3f°)", st.Lat, st.Lon)
	}
	
	// Classification-specific narrative
	switch st.Classification {
	case ClassAgricultural:
		return fmt.Sprintf("Agricultural settlement %s. Fire activity (%d detections within 5km) concentrated in dry season suggests slash-and-burn farming. %.2f km² deforestation nearby indicates active land conversion.",
			location, st.FiresWithin5km, st.DeforestNearby)
		
	case ClassMining:
		base := fmt.Sprintf("Possible mining site %s. Low fire activity but %.2f km² of forest loss with %s pattern. Proximity to %s suggests alluvial extraction.",
			location, st.DeforestNearby, st.DeforestPattern, st.NearestRiver)
		if st.TurbidityAlert != nil {
			base += fmt.Sprintf(" Sentinel-2 shows a sediment plume on the %s %.1fkm away (~%.0fkm of river turbid downstream, %s) — consistent with active alluvial gold washing.",
				st.TurbidityAlert.River, st.TurbidityAlertKm, st.TurbidityAlert.DownstreamTurbidKm, st.TurbidityAlert.Date)
		}
		if st.GFWAlertsWithin5km > 20 {
			base += fmt.Sprintf(" %d GFW canopy-disturbance alerts within 5km corroborate recent ground activity.", st.GFWAlertsWithin5km)
		}
		return base
		
	case ClassFishing:
		return fmt.Sprintf("Fishing camp %s, %.1fkm from %s River. Small footprint (%.0f m²) and minimal forest disturbance consistent with seasonal fishing activity.",
			location, st.DistanceToRiver, st.NearestRiver, st.AreaM2)
		
	case ClassPastoral:
		return fmt.Sprintf("Pastoral settlement %s. Seasonal fire pattern suggests grazing management. Minimal deforestation (%.2f km²) indicates grassland/savanna habitat.",
			location, st.DeforestNearby)
		
	case ClassResidential:
		return fmt.Sprintf("Permanent settlement %s near %s. Population ~%d with %.0f m² built area. Established community with moderate surrounding land use.",
			location, st.NearestPlace, st.PopulationEst, st.AreaM2)
		
	case ClassTemporaryCamp:
		return fmt.Sprintf("Temporary camp %s. Small footprint (%.0f m²) and remote location suggest seasonal occupation, possibly hunting or resource extraction.",
			location, st.AreaM2)
		
	default:
		return fmt.Sprintf("Unclassified settlement %s. %.0f m² built area, %d fire detections nearby.",
			location, st.AreaM2, st.FiresWithin5km)
	}
}

// GetClassifiedSettlements returns all settlements for a park with classifications
func (s *Server) GetClassifiedSettlements(parkID string) []ClassifiedSettlement {
	rows, err := s.DB.Query(`
		SELECT id, park_id, lat, lon, area_m2, population_est, nearest_place, distance_to_place_km
		FROM park_settlements
		WHERE park_id = ?
		ORDER BY area_m2 DESC
	`, parkID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	
	var settlements []ClassifiedSettlement
	for rows.Next() {
		var st ClassifiedSettlement
		var nearestPlace sql.NullString
		var distToPlace sql.NullFloat64
		
		err := rows.Scan(&st.ID, &st.ParkID, &st.Lat, &st.Lon, &st.AreaM2, &st.PopulationEst, &nearestPlace, &distToPlace)
		if err != nil {
			continue
		}
		
		if nearestPlace.Valid {
			st.NearestPlace = nearestPlace.String
			st.DistanceToPlace = distToPlace.Float64
		}
		
		// Classify this settlement
		s.ClassifySettlement(parkID, &st)
		settlements = append(settlements, st)
	}
	
	return settlements
}
