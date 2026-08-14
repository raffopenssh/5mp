package srv

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"
)

// OSMPlace represents a place from the osm_places table
type OSMPlace struct {
	ID        int64   `json:"id"`
	ParkID    string  `json:"park_id"`
	PlaceType string  `json:"place_type"`
	Name      string  `json:"name"`
	Lat       float64 `json:"lat"`
	Lon       float64 `json:"lon"`
	Distance  float64 `json:"distance_km,omitempty"` // Calculated distance from a point
}

// FireNarrative contains rich textual description of fire movements
type FireNarrative struct {
	ParkID     string             `json:"park_id"`
	ParkName   string             `json:"park_name"`
	Year       int                `json:"year"`
	Summary    string             `json:"summary"`
	Narratives []FireGroupStory   `json:"narratives"`
	KeyPlaces  []OSMPlace         `json:"key_places"`
	Hotspots   []FireHotspot      `json:"hotspots,omitempty"`
	Trend      *FireTrendAnalysis `json:"trend,omitempty"`
	// ContainmentMeaningful is false for an AOI, and every containment figure
	// below it is then absent rather than zero. See suppressContainmentForAOI.
	ContainmentMeaningful bool `json:"containment_meaningful"`
	// omitempty on purpose: for an AOI the field is REMOVED, not zeroed, so a
	// reader cannot mistake "not applicable" for "nothing was stopped".
	ResponseRate float64 `json:"response_rate,omitempty"`
	TotalFires   int     `json:"total_fires"`
	// TWO SURFACES SAYING ONE WORD MUST SAY WHICH THING THEY COUNT
	// (invariant 7). This `total_fires` is a sum over fire GROUPS whose
	// trajectories pass near or inside the park (the v5 chain, which computes
	// pct_inside by point-in-polygon); /api/parks/{id}/stats counts DETECTIONS
	// inside the boundary. Chinko: 302,900 against 145,743, and neither number
	// is wrong. The basis travels with the number so a reader can see they are
	// different units rather than concluding the data is broken.
	TotalFiresBasis string  `json:"total_fires_basis,omitempty"`
	TotalFRP        float64 `json:"total_frp,omitempty"` // Total Fire Radiative Power
	PeakMonth       string  `json:"peak_month,omitempty"`
	// v3 aggregate fields
	TotalGroups         int `json:"total_groups,omitempty"`
	ManagementFires     int `json:"management_fires,omitempty"`
	CrossBorderGroups   int `json:"cross_border_groups,omitempty"`
	OutsideParkGroups   int `json:"outside_park_groups,omitempty"`
	StoppedInsideGroups int `json:"stopped_inside_groups,omitempty"`
	TransitedGroups     int `json:"transited_groups,omitempty"`
	// v5 trajectory analysis fields
	TrajectoryTypes map[string]int `json:"trajectory_types,omitempty"`
	ErraticCount    int            `json:"erratic_count,omitempty"`
	ZigzagCount     int            `json:"zigzag_count,omitempty"`
	CleanCount      int            `json:"clean_count,omitempty"`
	AvgZigzagRatio  float64        `json:"avg_zigzag_ratio,omitempty"`
	Seasons         map[string]int `json:"seasons,omitempty"`
	Directions      map[string]int `json:"directions,omitempty"`
}

// FireHotspot represents a geographic concentration of fire activity
type FireHotspot struct {
	Lat          float64  `json:"lat"`
	Lon          float64  `json:"lon"`
	FireCount    int      `json:"fire_count"`
	Percentage   float64  `json:"percentage"`
	Description  string   `json:"description"`
	NearbyPlaces []string `json:"nearby_places"`
}

// FireTrendAnalysis provides multi-year trend information
type FireTrendAnalysis struct {
	Years              []FireYearSummary   `json:"years"`
	Months             []FireMonthSummary  `json:"months,omitempty"`
	Weeks              []FireWeekSummary   `json:"weeks,omitempty"`
	TrendDirection     string              `json:"trend_direction"` // increasing, decreasing, stable
	AvgResponseRate    float64             `json:"avg_response_rate,omitempty"`
	WorstYear          int                 `json:"worst_year"`
	WorstYearGroups    int                 `json:"worst_year_groups"`
	BestYear           int                 `json:"best_year"`
	BestYearRate       float64             `json:"best_year_rate"`
	Narrative          string              `json:"narrative"`
	AvgGroupsPerKm2    float64             `json:"avg_groups_per_km2,omitempty"`
	PeakMonths         []string            `json:"peak_months,omitempty"` // Months with highest activity
	Seasonality        string              `json:"seasonality,omitempty"` // e.g., "dry season peaks Jun-Aug"
	LatitudeComparison *LatitudeComparison `json:"latitude_comparison,omitempty"`
}

// FireMonthSummary provides per-month fire statistics
type FireMonthSummary struct {
	Month        string  `json:"month"` // YYYY-MM format
	Groups       int     `json:"groups"`
	GroupsPerKm2 float64 `json:"groups_per_km2,omitempty"`
}

// FireWeekSummary provides per-week fire statistics
type FireWeekSummary struct {
	Week         string  `json:"week"` // YYYY-Www format (ISO week)
	Groups       int     `json:"groups"`
	GroupsPerKm2 float64 `json:"groups_per_km2,omitempty"`
}

// LatitudeComparison compares this park to others at similar latitude
type LatitudeComparison struct {
	ParkLatitude    float64 `json:"park_latitude"`
	AvgGroupsPerKm2 float64 `json:"avg_groups_per_km2"`
	RegionAvg       float64 `json:"region_avg"`
	Percentile      float64 `json:"percentile"` // 0-100, lower is better
	ComparedParks   int     `json:"compared_parks"`
	LatitudeBand    string  `json:"latitude_band"` // e.g., "equatorial", "tropical north", etc.
}

// FireYearSummary provides per-year fire statistics
type FireYearSummary struct {
	Year            int     `json:"year"`
	TotalGroups     int     `json:"total_groups"`
	GroupsPerKm2    float64 `json:"groups_per_km2,omitempty"`
	StoppedInside   int     `json:"stopped_inside,omitempty"`
	Transited       int     `json:"transited,omitempty"`
	ResponseRate    float64 `json:"response_rate,omitempty"`
	TotalFires      int     `json:"total_fires"`
	TotalFRP        float64 `json:"total_frp,omitempty"` // Fire Radiative Power (MW)
	AvgDaysBurning  float64 `json:"avg_days_burning"`
	AvgSpeedKmDay   float64 `json:"avg_speed_km_day,omitempty"` // Average trajectory speed
	ManagementFires int     `json:"management_fires,omitempty"`
}

// FireGroupStory describes a single fire group's movement
type FireGroupStory struct {
	GroupNum  int    `json:"group_num"`
	FeatureID string `json:"feature_id,omitempty"`
	Year      int    `json:"year,omitempty"`
	GeoJSONID int64  `json:"geojson_id,omitempty"`
	// Legacy v2 "inside the park" fields. The v5 pipeline never populates these
	// (they were empty strings/zeros on every narrative of every position, not
	// just entirely_outside ones), so they are omitempty rather than always-on
	// nulls in the payload. The frontend already falls back:
	// `n.entry_date || n.start_date`. Drop entirely once nothing reads them.
	OriginDesc    string   `json:"origin_desc,omitempty"`
	DestDesc      string   `json:"dest_desc,omitempty"`
	EntryDate     string   `json:"entry_date,omitempty"`
	LastInside    string   `json:"last_inside,omitempty"`
	DaysInside    int      `json:"days_inside,omitempty"`
	FiresInside   int      `json:"fires_inside,omitempty"`
	Outcome       string   `json:"outcome,omitempty"`
	Narrative     string   `json:"narrative"`
	NearbyPlaces  []string `json:"nearby_places,omitempty"`
	RiversCrossed []string `json:"rivers_crossed,omitempty"`
	// v3 fields
	StartDate     string  `json:"start_date,omitempty"`
	EndDate       string  `json:"end_date,omitempty"`
	Days          int     `json:"days,omitempty"`
	FiresTotal    int     `json:"fires_total,omitempty"`
	TotalFRP      float64 `json:"total_frp,omitempty"`
	DistanceKm    float64 `json:"distance_km,omitempty"`
	AvgSpeedKmDay float64 `json:"avg_speed_km_day,omitempty"`
	Direction     string  `json:"direction,omitempty"`
	GroupType     string  `json:"group_type,omitempty"`
	Position      string  `json:"position,omitempty"` // starts_inside, ends_inside, transits, entirely_outside
	PctInside     float64 `json:"pct_inside,omitempty"`
	CrossBorder   bool    `json:"cross_border,omitempty"`
	Season        string  `json:"season,omitempty"`
	// v5 trajectory analysis
	TrajectoryType string  `json:"trajectory_type,omitempty"`
	ZigzagRatio    float64 `json:"zigzag_ratio,omitempty"`
}

// DeforestationNarrative contains rich textual description of forest loss
type DeforestationNarrative struct {
	ParkID             string                    `json:"park_id"`
	ParkName           string                    `json:"park_name"`
	Summary            string                    `json:"summary"`
	YearlyStory        []DeforestationYearStory  `json:"yearly_stories"`
	TotalLoss          float64                   `json:"total_loss_km2"`
	PolygonCount       int                       `json:"polygon_count,omitempty"`
	WorstYear          int                       `json:"worst_year"`
	TrendDirection     string                    `json:"trend_direction"`      // "improving", "worsening", "stable"
	TrendPercentChange float64                   `json:"trend_percent_change"` // percentage change between periods
	FiveYearAvgEarly   float64                   `json:"five_year_avg_early"`  // earliest 5-year average
	FiveYearAvgRecent  float64                   `json:"five_year_avg_recent"` // most recent 5-year average
	Hotspots           []DeforestationHotspot    `json:"hotspots,omitempty"`   // worst cluster hotspots
	ByClassification   map[string]int            `json:"by_classification,omitempty"`
	AreaByClass        map[string]float64        `json:"area_by_classification,omitempty"`
	ClassifiedEvents   []ClassifiedDeforestation `json:"classified_events,omitempty"`
}

// DeforestationYearStory describes forest loss for a single year
type DeforestationYearStory struct {
	Year           int      `json:"year"`
	AreaKm2        float64  `json:"area_km2"`
	PatternType    string   `json:"pattern_type"`
	Classification string   `json:"classification,omitempty"`
	GeoJSONID      int64    `json:"geojson_id,omitempty"`
	PolygonIDs     string   `json:"polygon_ids,omitempty"`
	Narrative      string   `json:"narrative"`
	NearbyPlaces   []string `json:"nearby_places"`
	// AreaMethod names the quantity AreaKm2 holds: 'hansen_canopy_loss' is
	// mapped canopy loss, 'gfw_alert_count' is an alert count times
	// KM2_PER_ALERT. Plotted as one series they showed 313.6 km² in 2023 → 0.7
	// in 2024, which reads as a 99.8% collapse and is a unit change
	// (docs/AOI_STRUCTURAL_FIXES.md F8; AGENTS.md invariant 7).
	AreaMethod string `json:"area_method,omitempty"`
	// NeedsReview marks a (park, year) whose loss is ≥50× its 5-year median
	// within the same method — a provenance question, not a spike to draw (F9).
	NeedsReview bool `json:"needs_review,omitempty"`
}

// DeforestationHotspot describes a significant cluster of deforestation
type DeforestationHotspot struct {
	Year        int     `json:"year"`
	ClusterID   int     `json:"cluster_id"`
	AreaKm2     float64 `json:"area_km2"`
	Lat         float64 `json:"lat"`
	Lon         float64 `json:"lon"`
	PatternType string  `json:"pattern_type"`
	Description string  `json:"description"`
}

// SettlementNarrative contains description of settlements and human-wildlife interface
type SettlementNarrative struct {
	ParkID          string `json:"park_id"`
	ParkName        string `json:"park_name"`
	Summary         string `json:"summary"`
	Status          string `json:"status"`
	SettlementCount int    `json:"settlement_count"`
	PolygonCount    int    `json:"polygon_count,omitempty"`
	TotalPopulation int64  `json:"total_population"`
	// How many of SettlementCount contributed to TotalPopulation. A population
	// that was never measured must not read as a population of zero
	// (AGENTS.md invariant 12; docs/AOI_STRUCTURAL_FIXES.md F2).
	PopulationMeasuredFor int                    `json:"population_measured_for"`
	Population2030        int64                  `json:"population_2030,omitempty"`
	PopulationDensity     float64                `json:"population_density_per_km2"`
	ParkAreaKm2           float64                `json:"park_area_km2"`
	ConflictRisk          string                 `json:"conflict_risk"`
	LargestSettlements    []SettlementDetail     `json:"largest_settlements"`
	RegionalBreakdown     []RegionSettlement     `json:"regional_breakdown,omitempty"`
	ByClassification      map[string]int         `json:"by_classification,omitempty"`
	// ByPersistence is MEASURED from GHSL back-epochs (scripts/ghsl_epochs.py):
	// permanent (built by 2000) / established (by 2015) / recent (after 2015).
	// Rows whose epoch tiles were unavailable are counted in
	// PersistenceUnmeasured rather than silently omitted — an unmeasured
	// cluster is a different state than a recent one (invariant 12).
	ByPersistence         map[string]int         `json:"by_persistence,omitempty"`
	PersistenceUnmeasured int                    `json:"persistence_unmeasured,omitempty"`
	// Cropland context, MEASURED from GLAD 30 m cropland extent
	// (scripts/cropland.py): count of settlements with >=3% mapped cropland
	// within ~1 km, the mean fraction over measured rows for both epochs
	// (2000-2003 vs 2016-2019 — the TREND), and how many rows were measured
	// at all. Basis named per invariant 7; unmeasured is not zero.
	CroplandSettlements   int                    `json:"cropland_settlements,omitempty"`
	CroplandMeasuredFor   int                    `json:"cropland_measured_for,omitempty"`
	CroplandMeanFrac2019  float64                `json:"cropland_mean_frac_2019,omitempty"`
	CroplandMeanFrac2003  float64                `json:"cropland_mean_frac_2003,omitempty"`
	ClassifiedList        []ClassifiedSettlement `json:"classified_settlements,omitempty"`
}

// SettlementDetail describes a single settlement
type SettlementDetail struct {
	ID             int64  `json:"id,omitempty"`
	GeoJSONID      int64  `json:"geojson_id,omitempty"`
	PolygonIDs     string `json:"polygon_ids,omitempty"`
	Name           string `json:"name"`
	Classification string `json:"classification,omitempty"`
	Narrative      string `json:"narrative,omitempty"`
	// AreaM2 is built-up SURFACE (Σ of the fractional raster) and is 0 for a
	// row the backfill has not reached; ExtentM2 is the mask footprint, which
	// every row has. They differ by ~24× and must never share a name
	// (AGENTS.md invariant 7; docs/AOI_STRUCTURAL_FIXES.md F1).
	AreaM2   float64 `json:"area_m2"`
	ExtentM2 float64 `json:"extent_m2,omitempty"`
	// PopulationMeasured false means UNMEASURED, so PopulationEst's zero is not
	// a count of people.
	PopulationMeasured bool    `json:"population_measured"`
	PopulationEst      int64   `json:"population_est,omitempty"`
	Population2030     int64   `json:"population_2030,omitempty"`
	Lat                float64 `json:"lat"`
	Lon                float64 `json:"lon"`
	Direction          string  `json:"direction"`
	// Distance to the nearest NAMED PLACE, not to the park boundary. It was
	// serialised as `nearest_boundary_km` until 2026-08-13 and globe.html duly
	// scored it as boundary proximity ("< 10 km => encroachment priority"),
	// which is a different question with a different answer: the largest
	// settlement in CAF_Chinko sits 70.9 km from Yalinga and well inside the
	// park. The value was always this; only the name was wrong (AGENTS.md
	// invariant 7 -- a number must name what it measures).
	DistanceToPlaceKm float64 `json:"distance_to_place_km,omitempty"`
}

// RegionSettlement groups settlements by geographic region within the park
type RegionSettlement struct {
	Region          string `json:"region"`
	SettlementCount int    `json:"settlement_count"`
	Population      int64  `json:"population"`
}

// haversineDistance calculates distance between two lat/lon points in km
func haversineDistance(lat1, lon1, lat2, lon2 float64) float64 {
	const R = 6371.0 // Earth's radius in km
	dLat := (lat2 - lat1) * math.Pi / 180
	dLon := (lon2 - lon1) * math.Pi / 180
	a := math.Sin(dLat/2)*math.Sin(dLat/2) +
		math.Cos(lat1*math.Pi/180)*math.Cos(lat2*math.Pi/180)*
			math.Sin(dLon/2)*math.Sin(dLon/2)
	c := 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))
	return R * c
}

// bearingTo calculates the initial bearing from point 1 to point 2 in degrees (0-360)
func bearingTo(lat1, lon1, lat2, lon2 float64) float64 {
	lat1Rad := lat1 * math.Pi / 180
	lat2Rad := lat2 * math.Pi / 180
	dLon := (lon2 - lon1) * math.Pi / 180

	y := math.Sin(dLon) * math.Cos(lat2Rad)
	x := math.Cos(lat1Rad)*math.Sin(lat2Rad) - math.Sin(lat1Rad)*math.Cos(lat2Rad)*math.Cos(dLon)
	bearing := math.Atan2(y, x) * 180 / math.Pi
	return math.Mod(bearing+360, 360) // Normalize to 0-360
}

// bearingToCardinal converts a bearing in degrees to a cardinal/intercardinal direction
// Uses 16-point compass with boundaries: N=348.75-11.25, NNE=11.25-33.75, etc.
func bearingToCardinal(bearing float64) string {
	// 16-point compass directions
	directions := []string{
		"north", "north-northeast", "northeast", "east-northeast",
		"east", "east-southeast", "southeast", "south-southeast",
		"south", "south-southwest", "southwest", "west-southwest",
		"west", "west-northwest", "northwest", "north-northwest",
	}
	// Each direction covers 22.5 degrees, offset by 11.25 to center on cardinal points
	// Adding 11.25 shifts so that 0° is center of "north" range
	index := int(math.Floor((bearing+11.25)/22.5)) % 16
	return directions[index]
}

// bearingToCardinalWithDegrees returns a compass direction with bearing in degrees
// Example: "north-northeast (bearing 022°)"
func bearingToCardinalWithDegrees(bearing float64) string {
	cardinal := bearingToCardinal(bearing)
	return fmt.Sprintf("%s (bearing %03.0f°)", cardinal, bearing)
}

// bearingToCardinalSimple returns a simple 8-point compass direction
func bearingToCardinalSimple(bearing float64) string {
	directions := []string{"north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"}
	index := int(math.Floor((bearing+22.5)/45)) % 8
	return directions[index]
}

// formatPlaceWithDirection formats a place name with distance and direction from a reference point
func formatPlaceWithDirection(placeName, placeType string, distKm, refLat, refLon, placeLat, placeLon float64) string {
	bearing := bearingTo(refLat, refLon, placeLat, placeLon)
	direction := bearingToCardinal(bearing)

	if placeType == "river" || placeType == "stream" {
		return fmt.Sprintf("%.0fkm %s of %s", distKm, direction, placeName)
	}
	return fmt.Sprintf("%.0fkm %s of %s", distKm, direction, placeName)
}

// findNearestPlaces finds the nearest OSM places to a given coordinate
func (s *Server) findNearestPlaces(parkID string, lat, lon float64, limit int, placeTypes []string) ([]OSMPlace, error) {
	var places []OSMPlace

	// Build query - search within park and nearby (expand search area)
	query := `
		SELECT id, park_id, place_type, name, lat, lon
		FROM osm_places
		WHERE park_id = ?
		  AND lat BETWEEN ? AND ?
		  AND lon BETWEEN ? AND ?
	`
	args := []interface{}{parkID, lat - 1.0, lat + 1.0, lon - 1.0, lon + 1.0}

	if len(placeTypes) > 0 {
		placeholders := make([]string, len(placeTypes))
		for i := range placeTypes {
			placeholders[i] = "?"
			args = append(args, placeTypes[i])
		}
		query += " AND place_type IN (" + strings.Join(placeholders, ",") + ")"
	}

	rows, err := s.DB.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	for rows.Next() {
		var p OSMPlace
		if err := rows.Scan(&p.ID, &p.ParkID, &p.PlaceType, &p.Name, &p.Lat, &p.Lon); err != nil {
			continue
		}
		p.Distance = haversineDistance(lat, lon, p.Lat, p.Lon)
		places = append(places, p)
	}

	// Sort by distance
	sort.Slice(places, func(i, j int) bool {
		return places[i].Distance < places[j].Distance
	})

	// Limit results
	if len(places) > limit {
		places = places[:limit]
	}

	return places, nil
}

// describeLocation returns a human-readable description of a location
func (s *Server) describeLocation(parkID string, lat, lon float64) string {
	// Find nearest settlement
	settlements, _ := s.findNearestPlaces(parkID, lat, lon, 1, []string{"village", "hamlet", "town", "city"})

	var parts []string

	if len(settlements) > 0 && settlements[0].Distance < 30 {
		p := settlements[0]
		placeType := "(" + p.PlaceType + ")"
		if p.PlaceType == "" {
			placeType = ""
		}
		if p.Distance < 5 {
			parts = append(parts, fmt.Sprintf("near %s %s", p.Name, placeType))
		} else {
			// Direction FROM settlement TO the location
			bearing := bearingTo(p.Lat, p.Lon, lat, lon)
			direction := bearingToCardinal(bearing)
			parts = append(parts, fmt.Sprintf("%.0fkm %s of %s %s", p.Distance, direction, p.Name, placeType))
		}
	}

	// Use HydroRIVERS for better river data
	hydroRivers, _ := s.findNearestRiverToPoint(parkID, lat, lon, 1)
	if len(hydroRivers) > 0 && hydroRivers[0].DistanceKm < 30 {
		r := hydroRivers[0]
		riverDesc := getRiverDescription(r)
		if r.DistanceKm < 3 {
			parts = append(parts, fmt.Sprintf("along %s (%s)", r.Name, riverDesc))
		} else {
			parts = append(parts, fmt.Sprintf("%.0fkm from %s", r.DistanceKm, r.Name))
		}
	} else {
		// Fallback to OSM rivers
		rivers, _ := s.findNearestPlaces(parkID, lat, lon, 1, []string{"river", "stream"})
		if len(rivers) > 0 && rivers[0].Distance < 20 {
			p := rivers[0]
			if p.Distance < 3 {
				parts = append(parts, fmt.Sprintf("along the %s", p.Name))
			} else {
				parts = append(parts, fmt.Sprintf("%.0fkm from %s", p.Distance, p.Name))
			}
		}
	}

	if len(parts) == 0 {
		return fmt.Sprintf("at coordinates (%.3f°, %.3f°)", lat, lon)
	}

	return strings.Join(parts, ", ")
}

// HandleAPIFireNarrative returns rich textual description of fire movements
// GET /api/parks/{id}/fire-narrative
func (s *Server) HandleAPIFireNarrative(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}

	// Map WDPA ID to internal park_id if needed
	internalID := parkID
	parkName := parkID
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.WDPAID == parkID || area.ID == parkID {
				internalID = area.ID
				parkName = area.Name
				break
			}
		}
	}

	// Check for stats-only format
	if r.URL.Query().Get("format") == "stats" {
		// Also support start/end as aliases for from/to
		startDate := r.URL.Query().Get("start")
		endDate := r.URL.Query().Get("end")
		if startDate == "" {
			startDate = r.URL.Query().Get("from")
		}
		if endDate == "" {
			endDate = r.URL.Query().Get("to")
		}
		s.handleFireNarrativeStats(w, internalID, parkName, startDate, endDate)
		return
	}

	// Always try to serve from cache - cache contains full history (2000-present)
	// Date filters are applied by the UI to subset the cached data
	if cached, computedAt, err := s.GetCachedFireNarrative(internalID); err == nil {
		// Add weekly data computed from feature_geometries (not in cache)
		if cached.Trend != nil && len(cached.Trend.Weeks) == 0 {
			s.addWeeklyDataToTrend(internalID, cached.Trend)
		}
		suppressContainmentForAOI(internalID, cached)
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Header().Set("X-Cache-Date", computedAt.Format(time.RFC3339))
		json.NewEncoder(w).Encode(cached)
		return
	}

	// Cache miss - compute on demand (slow path)
	w.Header().Set("X-Cache", "MISS")

	// Parse time filter parameters - support multi-year ranges
	yearStr := r.URL.Query().Get("year")
	fromStr := r.URL.Query().Get("from")
	toStr := r.URL.Query().Get("to")
	if s := r.URL.Query().Get("start"); s != "" {
		fromStr = s
	}
	if s := r.URL.Query().Get("end"); s != "" {
		toStr = s
	}

	var fromYear, toYear int
	now := time.Now()

	if yearStr != "" {
		if y, err := strconv.Atoi(yearStr); err == nil {
			fromYear = y
			toYear = y
		}
	} else {
		// Default: all available years
		fromYear = 2000
		toYear = now.Year()
		if fromStr != "" {
			if t, err := time.Parse("2006-01-02", fromStr); err == nil {
				fromYear = t.Year()
			} else if y, err := strconv.Atoi(fromStr); err == nil {
				fromYear = y
			}
		}
		if toStr != "" {
			if t, err := time.Parse("2006-01-02", toStr); err == nil {
				toYear = t.Year()
			} else if y, err := strconv.Atoi(toStr); err == nil {
				toYear = y
			}
		}
	}

	// Get aggregated fire data across year range
	var totalGroups, stoppedInside, transited int
	var avgDaysBurning float64
	var yearCount int

	err := s.DB.QueryRow(`
		SELECT 
			COUNT(DISTINCT year) as year_count,
			SUM(total_groups) as total_groups,
			SUM(groups_stopped_inside) as stopped,
			SUM(groups_transited) as transited,
			AVG(avg_days_burning) as avg_days
		FROM park_group_infractions 
		WHERE park_id = ? AND year >= ? AND year <= ? AND total_groups > 0
	`, internalID, fromYear, toYear).Scan(&yearCount, &totalGroups, &stoppedInside, &transited, &avgDaysBurning)

	// Use toYear as the "display year" for single-year or latest in range
	displayYear := toYear
	if fromYear == toYear {
		displayYear = fromYear
	}

	narrative := FireNarrative{
		ParkID:   internalID,
		ParkName: parkName,
		Year:     displayYear,
	}

	if err == sql.ErrNoRows || totalGroups == 0 {
		// DB table empty, try JSON files
		ctx := s.getNarrativeContext(internalID, parkName)
		narratives := s.getTrajectoryNarrativesFromJSON(internalID, fromYear, toYear, ctx)
		if len(narratives) > 0 {
			// Compute stats from JSON narratives
			narrative.Narratives = narratives
			for _, n := range narratives {
				totalGroups++
				if n.Outcome == "STOPPED_INSIDE" {
					stoppedInside++
				} else if n.Outcome == "TRANSITED" {
					transited++
				}
				narrative.TotalFires += n.FiresInside
			}
			if totalGroups > 0 {
				narrative.ResponseRate = float64(stoppedInside) / float64(totalGroups) * 100
			}
			// Build summary
			narrative.Summary = s.buildFireSummary(parkName, fromYear, toYear, 1, narrative.TotalFires, totalGroups, stoppedInside, transited, narrative.ResponseRate, "", 0)
			narrative.Trend = s.analyzeFireTrendFast(internalID, toYear)
			suppressContainmentForAOI(internalID, &narrative)
			// NOTE: do NOT save to fire_narrative_cache here — this fallback is
			// v2-JSON-derived (sequential _grp_N ids) and would overwrite the
			// canonical v5 cache written by scripts/precompute_narratives_v5.py.
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(narrative)
			return
		}
		// No JSON data either
		periodDesc := fmt.Sprintf("%d", fromYear)
		if fromYear != toYear {
			periodDesc = fmt.Sprintf("%d-%d", fromYear, toYear)
		}
		narrative.Summary = fmt.Sprintf("No significant fire group incursions recorded for %s in %s.", parkName, periodDesc)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(narrative)
		return
	}
	if err != nil {
		internalError(w, "request failed", err)
		return
	}

	// Calculate response rate
	if totalGroups > 0 {
		narrative.ResponseRate = float64(stoppedInside) / float64(totalGroups) * 100
	}

	// Get total fire count for the year range (inside the boundary only —
	// srv/fire_containment.go)
	var totalFires int
	s.DB.QueryRow(`
		SELECT COUNT(*) FROM fire_detections 
		WHERE protected_area_id = ?`+fireInsideSQL+`
		  AND CAST(strftime('%Y', acq_date) AS INTEGER) >= ? 
		  AND CAST(strftime('%Y', acq_date) AS INTEGER) <= ?
	`, internalID, fromYear, toYear).Scan(&totalFires)
	narrative.TotalFires = totalFires
	narrative.TotalFiresBasis = "detections inside the park boundary"

	// Get peak month across the range
	var peakMonth string
	var peakCount int
	s.DB.QueryRow(`
		SELECT strftime('%m', acq_date) as month, COUNT(*) as cnt
		FROM fire_detections 
		WHERE protected_area_id = ?`+fireInsideSQL+`
		  AND CAST(strftime('%Y', acq_date) AS INTEGER) >= ?
		  AND CAST(strftime('%Y', acq_date) AS INTEGER) <= ?
		GROUP BY month ORDER BY cnt DESC LIMIT 1
	`, internalID, fromYear, toYear).Scan(&peakMonth, &peakCount)
	monthNames := map[string]string{
		"01": "January", "02": "February", "03": "March", "04": "April",
		"05": "May", "06": "June", "07": "July", "08": "August",
		"09": "September", "10": "October", "11": "November", "12": "December",
	}
	narrative.PeakMonth = monthNames[peakMonth]

	// Build enhanced summary
	var summaryParts []string
	periodDesc := fmt.Sprintf("%d", fromYear)
	if fromYear != toYear {
		periodDesc = fmt.Sprintf("%d-%d", fromYear, toYear)
	}
	if yearCount > 1 {
		summaryParts = append(summaryParts, fmt.Sprintf("From %s, %s experienced %d fire detections across %d fire groups over %d years.",
			periodDesc, parkName, totalFires, totalGroups, yearCount))
	} else {
		summaryParts = append(summaryParts, fmt.Sprintf("In %s, %s experienced %d fire detections across %d distinct fire groups.",
			periodDesc, parkName, totalFires, totalGroups))
	}
	if stoppedInside > 0 {
		summaryParts = append(summaryParts, fmt.Sprintf("%d group(s) (%.0f%%) were stopped inside the park, suggesting effective ranger intervention.",
			stoppedInside, narrative.ResponseRate))
	}
	if transited > 0 {
		summaryParts = append(summaryParts, fmt.Sprintf("%d group(s) transited through without being stopped.", transited))
	}
	if narrative.PeakMonth != "" {
		summaryParts = append(summaryParts, fmt.Sprintf("Peak fire activity occurred in %s.", narrative.PeakMonth))
	}
	if avgDaysBurning > 0 {
		summaryParts = append(summaryParts, fmt.Sprintf("Fire groups burned inside the park for an average of %.1f days.", avgDaysBurning))
	}
	narrative.Summary = strings.Join(summaryParts, " ")

	// Query trajectories from the most recent year in range for detailed stories
	var trajJSON sql.NullString
	s.DB.QueryRow(`
		SELECT trajectories_json FROM park_group_infractions 
		WHERE park_id = ? AND year >= ? AND year <= ? AND trajectories_json IS NOT NULL
		ORDER BY year DESC LIMIT 1
	`, internalID, fromYear, toYear).Scan(&trajJSON)

	// Parse trajectories and build detailed stories
	if trajJSON.Valid && trajJSON.String != "" {
		var trajs []FireGroupTrajectory
		if json.Unmarshal([]byte(trajJSON.String), &trajs) == nil {
			for i, t := range trajs {
				story := FireGroupStory{
					GroupNum:    i + 1,
					EntryDate:   t.EntryDate,
					LastInside:  t.LastInside,
					DaysInside:  t.DaysInside,
					FiresInside: t.FiresInside,
					Outcome:     t.Outcome,
				}

				// Calculate trajectory bearing (azimuth) from origin to destination
				trajBearing := bearingTo(t.Origin.Lat, t.Origin.Lon, t.Destination.Lat, t.Destination.Lon)
				movementDesc := fmt.Sprintf("moving %s", bearingToCardinalWithDegrees(trajBearing))

				// Describe origin location
				story.OriginDesc = s.describeLocation(internalID, t.Origin.Lat, t.Origin.Lon)

				// If no nearby place found, include coordinates with movement direction
				if strings.HasPrefix(story.OriginDesc, "at coordinates") {
					story.OriginDesc = fmt.Sprintf("(%.3f°, %.3f°), %s",
						t.Origin.Lat, t.Origin.Lon, movementDesc)
				} else {
					// Add movement direction to location description
					story.OriginDesc = fmt.Sprintf("%s, %s", story.OriginDesc, movementDesc)
				}

				// Describe destination location
				story.DestDesc = s.describeLocation(internalID, t.Destination.Lat, t.Destination.Lon)

				// Find rivers that might have been crossed (using HydroRIVERS)
				hydroRivers, _ := s.findNearestRiverToPoint(internalID,
					(t.Origin.Lat+t.Destination.Lat)/2,
					(t.Origin.Lon+t.Destination.Lon)/2,
					5)
				for _, r := range hydroRivers {
					if r.DistanceKm < 15 && r.Name != "" {
						// Add river with size context
						riverDesc := r.Name
						if r.DischargeCMS > 100 {
							riverDesc = r.Name + " (major)"
						}
						story.RiversCrossed = append(story.RiversCrossed, riverDesc)
					}
				}
				// Fallback to OSM places if no HydroRIVERS found
				if len(story.RiversCrossed) == 0 {
					osmRivers, _ := s.findNearestPlaces(internalID,
						(t.Origin.Lat+t.Destination.Lat)/2,
						(t.Origin.Lon+t.Destination.Lon)/2,
						3, []string{"river"})
					for _, r := range osmRivers {
						if r.Distance < 15 {
							story.RiversCrossed = append(story.RiversCrossed, r.Name)
						}
					}
				}

				// Build narrative text
				var narr strings.Builder
				// Add seasonal context based on date
				seasonDesc := ""
				if entryTime, err := time.Parse("2006-01-02", t.EntryDate); err == nil {
					month := int(entryTime.Month())
					// Query climate data for seasonality
					var precipWettest, precipDriest int
					err := s.DB.QueryRow(`
							SELECT COALESCE(precip_wettest_mm, 0), COALESCE(precip_driest_mm, 0) 
							FROM park_climate WHERE park_id = ?
						`, parkID).Scan(&precipWettest, &precipDriest)
					if err == nil && precipWettest > 0 && precipDriest >= 0 {
						seasonality := float64(precipWettest) / float64(precipDriest+1)
						if seasonality > 3 {
							// Distinct dry/wet seasons
							if month >= 6 && month <= 9 {
								seasonDesc = " (dry season)"
							} else if month >= 11 || month <= 2 {
								seasonDesc = " (wet season)"
							}
						}
					}
				}

				narr.WriteString(fmt.Sprintf("Fire group %d originated %s on %s%s. ",
					i+1, story.OriginDesc, t.EntryDate, seasonDesc))

				if len(story.RiversCrossed) > 0 {
					unique := uniqueStrings(story.RiversCrossed)
					if len(unique) == 1 {
						narr.WriteString(fmt.Sprintf("The group crossed near the %s. ", unique[0]))
					} else {
						narr.WriteString(fmt.Sprintf("The group crossed near the %s. ", strings.Join(unique, " and ")))
					}
				}

				daysWord := "days"
				if t.DaysInside == 1 {
					daysWord = "day"
				}
				narr.WriteString(fmt.Sprintf("Burned inside the park for %d %s (%d fire detections). ",
					t.DaysInside, daysWord, t.FiresInside))

				switch t.Outcome {
				case "STOPPED_INSIDE":
					narr.WriteString(fmt.Sprintf("Last detected %s - fire stopped, possibly due to ranger intervention.",
						story.DestDesc))
				case "TRANSITED":
					narr.WriteString(fmt.Sprintf("Exited the park %s on %s - transited without being stopped.",
						story.DestDesc, t.LastInside))
				default:
					narr.WriteString(fmt.Sprintf("Last detected %s.", story.DestDesc))
				}

				story.Narrative = narr.String()

				// Look up geojson_id from feature_geometries
				var geoID sql.NullInt64
				s.DB.QueryRow(`
					SELECT id FROM feature_geometries
					WHERE park_id = ? AND feature_type = 'fire_trajectory' AND start_date = ?
					LIMIT 1
				`, internalID, t.EntryDate).Scan(&geoID)
				if geoID.Valid {
					story.GeoJSONID = geoID.Int64
				}

				narrative.Narratives = append(narrative.Narratives, story)
			}
		}
	}

	// Generate hotspot analysis from fire_detections (works without trajectory JSON)
	narrative.Hotspots = s.analyzeFireHotspots(internalID, displayYear, totalFires)

	// Generate multi-year trend analysis
	narrative.Trend = s.analyzeFireTrend(internalID, displayYear)

	// If no trajectory-based narratives, generate hotspot-based narratives
	if len(narrative.Narratives) == 0 && len(narrative.Hotspots) > 0 {
		for i, hs := range narrative.Hotspots {
			if i >= 5 { // Limit to top 5 hotspots
				break
			}
			story := FireGroupStory{
				GroupNum:     i + 1,
				FiresInside:  hs.FireCount,
				Outcome:      "HOTSPOT",
				Narrative:    hs.Description,
				NearbyPlaces: hs.NearbyPlaces,
			}
			narrative.Narratives = append(narrative.Narratives, story)
		}
	}

	// Get key places in the park for context
	keyPlaces, _ := s.findNearestPlaces(internalID, 0, 0, 0, nil)
	if len(keyPlaces) == 0 {
		rows, err := s.DB.Query(`
			SELECT id, park_id, place_type, name, lat, lon
			FROM osm_places WHERE park_id = ? LIMIT 20
		`, internalID)
		if err == nil {
			defer rows.Close()
			for rows.Next() {
				var p OSMPlace
				if rows.Scan(&p.ID, &p.ParkID, &p.PlaceType, &p.Name, &p.Lat, &p.Lon) == nil {
					narrative.KeyPlaces = append(narrative.KeyPlaces, p)
				}
			}
		}
	}

	suppressContainmentForAOI(internalID, &narrative)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(narrative)
}

// HandleAPIDeforestationNarrative returns rich textual description of forest loss
// GET /api/parks/{id}/deforestation-narrative
func (s *Server) HandleAPIDeforestationNarrative(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}

	// Map WDPA ID to internal park_id if needed
	internalID := parkID
	parkName := parkID
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.WDPAID == parkID || area.ID == parkID {
				internalID = area.ID
				parkName = area.Name
				break
			}
		}
	}

	// Parse time filter parameters
	// Support both start/end and from/to query params
	yearStr := r.URL.Query().Get("year")
	fromStr := r.URL.Query().Get("from")
	toStr := r.URL.Query().Get("to")
	if s := r.URL.Query().Get("start"); s != "" {
		fromStr = s
	}
	if s := r.URL.Query().Get("end"); s != "" {
		toStr = s
	}

	var fromYear, toYear int
	if yearStr != "" {
		if y, err := strconv.Atoi(yearStr); err == nil {
			fromYear = y
			toYear = y
		}
	} else {
		// Default to all years if no filter
		fromYear = 1900
		toYear = 2100
		if fromStr != "" {
			if t, err := time.Parse("2006-01-02", fromStr); err == nil {
				fromYear = t.Year()
			} else if y, err := strconv.Atoi(fromStr); err == nil {
				fromYear = y
			}
		}
		if toStr != "" {
			if t, err := time.Parse("2006-01-02", toStr); err == nil {
				toYear = t.Year()
			} else if y, err := strconv.Atoi(toStr); err == nil {
				toYear = y
			}
		}
	}

	// Check for stats-only format
	if r.URL.Query().Get("format") == "stats" {
		startStr := r.URL.Query().Get("start")
		endStr := r.URL.Query().Get("end")
		startYr := fromYear
		endYr := toYear
		if startStr != "" {
			if y, err := strconv.Atoi(startStr); err == nil {
				startYr = y
			}
		}
		if endStr != "" {
			if y, err := strconv.Atoi(endStr); err == nil {
				endYr = y
			}
		}
		s.handleDeforestationNarrativeStats(w, internalID, parkName, startYr, endYr)
		return
	}

	// Every event below costs ~21 ms of per-event enrichment (nearby places,
	// rivers, roads, pattern). A park has hundreds of events so nobody noticed;
	// an AOI has thousands, and XSA_Study_Area's 7,815 took 2m27s — past the
	// 120 s WriteTimeout, so the section rendered as if it had no data. Same
	// cache-first shape as the fire narrative above it.
	cacheParams := fmt.Sprintf("%d-%d", fromYear, toYear)
	srcRev := s.narrativeSourceRev("deforestation_events", internalID)
	if payload, computedAt, ok := s.getCachedNarrative(internalID, "deforestation", cacheParams, srcRev); ok {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Cache", "HIT")
		w.Header().Set("X-Cache-Date", computedAt)
		w.Write(payload)
		return
	}
	w.Header().Set("X-Cache", "MISS")

	// One memo for the whole request: consecutive events share a ~0.25° cell, so
	// this collapses thousands of near-identical bbox scans into a few dozen
	// without changing the answer (see geoMemo).
	memo := newGeoMemo(s, internalID)

	narrative := DeforestationNarrative{
		ParkID:   internalID,
		ParkName: parkName,
	}

	// Query deforestation events with time filter, including geojson_id
	rows, err := s.DB.Query(`
		SELECT de.year, de.area_km2, de.pattern_type, de.lat, de.lon, de.narrative,
		       de.id, COALESCE(de.classification, ''),
		       COALESCE(de.area_method, ''), COALESCE(de.needs_review, 0)
		FROM deforestation_events de
		WHERE de.park_id = ? AND de.year >= ? AND de.year <= ?
		ORDER BY de.year ASC
	`, internalID, fromYear, toYear)
	if err != nil {
		internalError(w, "request failed", err)
		return
	}
	defer rows.Close()

	var totalLoss float64
	var worstYear int
	var worstLoss float64
	var yearlyAreas []struct {
		year int
		area float64
		lat  float64
		lon  float64
	}
	classificationCounts := make(map[string]int)

	// Track geographic shift over time for trend analysis
	var earlyYearsLat, earlyYearsLon float64
	var recentYearsLat, recentYearsLon float64
	var earlyCount, recentCount int

	// Buffer all rows before doing per-row enrichment queries: nested queries
	// while rows are open hold a pool connection AND request another, which
	// deadlocks the whole server under concurrent load (MaxOpenConns=4).
	type defRow struct {
		year           int
		area           float64
		patternType    sql.NullString
		lat, lon       float64
		geojsonID      sql.NullInt64
		classification string
		areaMethod     string
		needsReview    bool
	}
	var defRows []defRow
	for rows.Next() {
		var dr defRow
		var description sql.NullString
		if err := rows.Scan(&dr.year, &dr.area, &dr.patternType, &dr.lat, &dr.lon, &description, &dr.geojsonID, &dr.classification, &dr.areaMethod, &dr.needsReview); err != nil {
			continue
		}
		defRows = append(defRows, dr)
	}
	rows.Close()

	for _, dr := range defRows {
		year, area, patternType := dr.year, dr.area, dr.patternType
		lat, lon := dr.lat, dr.lon
		geojsonID, classification := dr.geojsonID, dr.classification

		yearlyAreas = append(yearlyAreas, struct {
			year int
			area float64
			lat  float64
			lon  float64
		}{year, area, lat, lon})

		totalLoss += area
		if area > worstLoss {
			worstLoss = area
			worstYear = year
		}

		// Track classification
		clsKey := classification
		if clsKey == "" {
			clsKey = "unclassified"
		}
		classificationCounts[clsKey]++

		// Determine actual pattern type from cluster data for this year
		actualPattern := s.determinePatternType(internalID, year, patternType.String)

		story := DeforestationYearStory{
			Year:           year,
			AreaKm2:        area,
			PatternType:    actualPattern,
			Classification: clsKey,
			// F8/F9: which quantity `area_km2` is, and whether this year is a
			// step change the pipeline flagged for provenance review. Both
			// travel with the row so a chart cannot join two units into one
			// line, or draw a spike as if it were landscape.
			AreaMethod:  dr.areaMethod,
			NeedsReview: dr.needsReview,
		}
		if geojsonID.Valid {
			story.GeoJSONID = geojsonID.Int64
		}

		// Find nearby places for context (settlements and rivers)
		settlements := memo.nearestPlaces(lat, lon, 3, []string{"village", "hamlet", "town", "city"}, "settle")

		// Use HydroRIVERS for better river data
		var rivers []OSMPlace
		hydroRivers, memoOK := memo.nearestRivers(lat, lon, 3)
		if !memoOK {
			hydroRivers, _ = s.findNearestRiverToPoint(internalID, lat, lon, 3)
		}
		for _, hr := range hydroRivers {
			rivers = append(rivers, OSMPlace{
				Name:      hr.Name,
				PlaceType: getRiverDescription(hr),
				Lat:       hr.CentroidLat,
				Lon:       hr.CentroidLon,
				Distance:  hr.DistanceKm,
			})
		}
		// Fallback to OSM places if no HydroRIVERS
		if len(rivers) == 0 {
			rivers = memo.nearestPlaces(lat, lon, 3, []string{"river", "stream"}, "riv")
		}

		// Check if near road
		_ = s.isNearRoad(internalID, lat, lon, 5.0)

		seen := make(map[string]bool)
		for _, p := range settlements {
			key := p.Name
			if !seen[key] && p.Distance < 100 {
				seen[key] = true
				desc := formatPlaceWithDirection(p.Name, p.PlaceType, p.Distance, lat, lon, p.Lat, p.Lon)
				story.NearbyPlaces = append(story.NearbyPlaces, desc)
			}
		}
		for _, p := range rivers {
			key := p.Name
			if !seen[key] && p.Distance < 100 {
				seen[key] = true
				desc := formatPlaceWithDirection(p.Name+" River", p.PlaceType, p.Distance, lat, lon, p.Lat, p.Lon)
				story.NearbyPlaces = append(story.NearbyPlaces, desc)
			}
		}

		// Build rich narrative description
		story.Narrative = s.buildDeforestationNarrative(internalID, year, area, lat, lon, actualPattern, settlements, rivers)

		narrative.YearlyStory = append(narrative.YearlyStory, story)
	}

	// Analyze geographic shift if we have enough data
	if len(yearlyAreas) >= 4 {
		midpoint := len(yearlyAreas) / 2
		for i := 0; i < midpoint; i++ {
			earlyYearsLat += yearlyAreas[i].lat
			earlyYearsLon += yearlyAreas[i].lon
			earlyCount++
		}
		for i := midpoint; i < len(yearlyAreas); i++ {
			recentYearsLat += yearlyAreas[i].lat
			recentYearsLon += yearlyAreas[i].lon
			recentCount++
		}
		if earlyCount > 0 && recentCount > 0 {
			earlyYearsLat /= float64(earlyCount)
			earlyYearsLon /= float64(earlyCount)
			recentYearsLat /= float64(recentCount)
			recentYearsLon /= float64(recentCount)
		}
	}

	// Reverse to show most recent first
	for i, j := 0, len(narrative.YearlyStory)-1; i < j; i, j = i+1, j-1 {
		narrative.YearlyStory[i], narrative.YearlyStory[j] = narrative.YearlyStory[j], narrative.YearlyStory[i]
	}

	narrative.TotalLoss = totalLoss
	narrative.WorstYear = worstYear

	// Get polygon count from feature_geometries (for map display)
	var defPolygonCount int
	s.DB.QueryRow(`SELECT COUNT(*) FROM feature_geometries WHERE park_id = ? AND feature_type = 'deforestation'`, internalID).Scan(&defPolygonCount)
	narrative.PolygonCount = defPolygonCount

	// Calculate 5-year rolling average trend
	simpleYearlyAreas := make([]struct {
		year int
		area float64
	}, len(yearlyAreas))
	for i, ya := range yearlyAreas {
		simpleYearlyAreas[i] = struct {
			year int
			area float64
		}{ya.year, ya.area}
	}
	narrative.TrendDirection, narrative.TrendPercentChange,
		narrative.FiveYearAvgEarly, narrative.FiveYearAvgRecent = calculateTrend(simpleYearlyAreas)

	// Fetch worst hotspots from clusters table
	narrative.Hotspots = s.fetchHotspots(internalID, 5)

	// Add classification breakdown
	if len(classificationCounts) > 0 {
		narrative.ByClassification = classificationCounts
	}

	// Build summary with trend and geographic shift information
	if totalLoss == 0 {
		narrative.Summary = fmt.Sprintf("No significant deforestation events recorded for %s.", parkName)
	} else {
		// yearlyAreas is chronological (the story list is reversed above), so its
		// ends give the true observed year span.
		spanFrom, spanTo := 0, 0
		if len(yearlyAreas) > 0 {
			spanFrom = yearlyAreas[0].year
			spanTo = yearlyAreas[len(yearlyAreas)-1].year
			if spanFrom > spanTo {
				spanFrom, spanTo = spanTo, spanFrom
			}
		}
		narrative.Summary = s.buildDeforestationSummary(parkName, totalLoss, len(narrative.YearlyStory),
			worstYear, worstLoss, narrative.TrendDirection, narrative.TrendPercentChange,
			earlyYearsLat, earlyYearsLon, recentYearsLat, recentYearsLon, earlyCount, recentCount, internalID,
			spanFrom, spanTo)
	}

	// Get classified deforestation events with individual narratives
	allEvents := s.GetCachedClassifiedDeforestation(internalID)

	// Filter by year range
	var filteredEvents []ClassifiedDeforestation
	for _, ev := range allEvents {
		if ev.Year >= fromYear && ev.Year <= toYear {
			filteredEvents = append(filteredEvents, ev)
		}
	}
	narrative.ClassifiedEvents = filteredEvents

	if len(narrative.ClassifiedEvents) > 0 {
		narrative.ByClassification = make(map[string]int)
		narrative.AreaByClass = make(map[string]float64)
		for _, ev := range narrative.ClassifiedEvents {
			narrative.ByClassification[ev.Classification]++
			narrative.AreaByClass[ev.Classification] += ev.AreaKm2
		}
	}

	w.Header().Set("Content-Type", "application/json")
	payload, err := json.Marshal(narrative)
	if err != nil {
		internalError(w, "request failed", err)
		return
	}
	s.putCachedNarrative(internalID, "deforestation", cacheParams, srcRev, payload)
	w.Write(payload)
}

// HandleAPISettlementNarrative returns comprehensive narrative about settlements and human-wildlife interface
// GET /api/parks/{id}/settlement-narrative
func (s *Server) HandleAPISettlementNarrative(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}

	// Map WDPA ID to internal park_id if needed
	internalID := parkID
	parkName := parkID
	var parkAreaKm2 float64
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.WDPAID == parkID || area.ID == parkID {
				internalID = area.ID
				parkName = area.Name
				parkAreaKm2 = area.AreaKm2
				break
			}
		}
	}

	// Check for stats-only format
	if r.URL.Query().Get("format") == "stats" {
		s.handleSettlementNarrativeStats(w, internalID, parkName)
		return
	}

	narrative := SettlementNarrative{
		ParkID:      internalID,
		ParkName:    parkName,
		ParkAreaKm2: parkAreaKm2,
	}

	// Get settlement statistics from park_settlements table.
	//
	// Population is served only where it was MEASURED (a GHS_POP zonal sum);
	// a row still carrying the 200 people/ha constant contributes nothing and
	// is counted separately, so the total names the rows it is a total OF
	// rather than presenting a partial sum as a whole one
	// (srv/settlement_provenance.go, docs/AOI_STRUCTURAL_FIXES.md F2).
	var settlementCount int
	var totalPopulation sql.NullFloat64
	var popMeasured sql.NullInt64
	err := s.DB.QueryRow(`
		SELECT COUNT(*) as count,
		       SUM(`+settlementPopulationSQL("")+`) as total_pop,
		       `+settlementPopulationMeasuredSQL("")+` as measured
		FROM park_settlements
		WHERE park_id = ?`+settlementFilterSQL("narrative", "polygon_ids")+`
	`, internalID).Scan(&settlementCount, &totalPopulation, &popMeasured)

	if err != nil {
		narrative.Status = "error"
		narrative.Summary = "Error retrieving settlement data."
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(narrative)
		return
	}

	narrative.SettlementCount = settlementCount
	if totalPopulation.Valid {
		narrative.TotalPopulation = int64(totalPopulation.Float64)
	}
	// `population_measured_for` is the denominator of TotalPopulation. 0 with a
	// non-zero settlement_count means NOT MEASURED, not "nobody lives there" —
	// the frontend prints that distinction rather than a confident zero.
	narrative.PopulationMeasuredFor = int(popMeasured.Int64)

	// Get polygon count from feature_geometries (for map display). This is the
	// number of built-up FOOTPRINTS, which is larger than settlement_count
	// because a settlement is a cluster of them — the two are different units
	// and the field names say so.
	var polygonCount int
	s.DB.QueryRow(`SELECT COUNT(*) FROM feature_geometries WHERE park_id = ? AND feature_type = 'settlement'`, internalID).Scan(&polygonCount)
	narrative.PolygonCount = polygonCount

	// Calculate population density
	if parkAreaKm2 > 0 {
		narrative.PopulationDensity = totalPopulation.Float64 / parkAreaKm2
	}

	// Assess human-wildlife conflict risk
	narrative.ConflictRisk = assessConflictRisk(settlementCount, narrative.PopulationDensity)

	// Handle zero settlements case (pristine areas)
	if settlementCount == 0 {
		narrative.Status = "complete"
		narrative.ConflictRisk = "minimal"
		narrative.Summary = generatePristineNarrative(parkName, parkAreaKm2)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(narrative)
		return
	}

	// Get largest settlements with polygon_ids, classification, and narrative
	largestRows, err := s.DB.Query(`
		SELECT 
			s.id,
			COALESCE(s.nearest_place, 'Unnamed settlement') as name,
			COALESCE(s.classification, '') as classification,
			COALESCE(s.narrative, '') as narrative,
			COALESCE(s.polygon_ids, '') as polygon_ids,
			COALESCE(`+settlementSurfaceSQL("s")+`, 0) as area_m2,
			`+settlementExtentSQL("s")+` as extent_m2,
			`+settlementPopulationSQL("s")+` as pop_est,
			s.lat, s.lon,
			COALESCE(s.direction_from_place, '') as direction,
			COALESCE(s.distance_to_place_km, 0) as distance_km
		FROM park_settlements s
		WHERE s.park_id = ?`+settlementFilterSQL("s.narrative", "s.polygon_ids")+`
		ORDER BY `+settlementExtentSQL("s")+` DESC
	`, internalID)

	if err == nil {
		defer largestRows.Close()
		for largestRows.Next() {
			var sd SettlementDetail
			var distKm float64
			var settNarrative, polygonIDs string
			var popEst sql.NullInt64
			if err := largestRows.Scan(&sd.ID, &sd.Name, &sd.Classification, &settNarrative, &polygonIDs, &sd.AreaM2, &sd.ExtentM2, &popEst, &sd.Lat, &sd.Lon, &sd.Direction, &distKm); err == nil {
				if popEst.Valid {
					sd.PopulationEst = popEst.Int64
					sd.PopulationMeasured = true
				}
				sd.DistanceToPlaceKm = distKm
				// mining labels + their "suspected alluvial extraction" prose are
				// retired (docs/MINING_FINDINGS_2026-08.md §10)
				sd.Narrative = publicSettlementNarrative(sd.Classification, settNarrative)
				sd.Classification = publicSettlementClass(sd.Classification)
				sd.PolygonIDs = polygonIDs
				narrative.LargestSettlements = append(narrative.LargestSettlements, sd)
			}
		}
	}

	// Get regional breakdown by quadrant.
	//
	// The filter belongs on BOTH halves. The centroid is not decoration: it is
	// the origin the four quadrants are measured from, so an unfiltered centre
	// moves every row's quadrant label. And an unfiltered outer query made this
	// table the one surface still counting retired detector output -- 241 rows
	// where the summary above it said 172 (COD_Bili-Uere, 2026-08-13). See
	// srv/mining_flag.go.
	regionRows, err := s.DB.Query(`
		WITH park_center AS (
			SELECT AVG(lat) as center_lat, AVG(lon) as center_lon
			FROM park_settlements WHERE park_id = ?`+settlementFilterSQL("narrative", "polygon_ids")+`
		)
		SELECT 
			CASE 
				WHEN s.lat >= pc.center_lat AND s.lon >= pc.center_lon THEN 'Northeast'
				WHEN s.lat >= pc.center_lat AND s.lon < pc.center_lon THEN 'Northwest'
				WHEN s.lat < pc.center_lat AND s.lon >= pc.center_lon THEN 'Southeast'
				ELSE 'Southwest'
			END as region,
			COUNT(*) as count,
			COALESCE(SUM(`+settlementPopulationSQL("s")+`), 0) as population
		FROM park_settlements s, park_center pc
		WHERE s.park_id = ?`+settlementFilterSQL("s.narrative", "s.polygon_ids")+`
		GROUP BY region
		ORDER BY population DESC
	`, internalID, internalID)

	if err == nil {
		defer regionRows.Close()
		for regionRows.Next() {
			var rs RegionSettlement
			if err := regionRows.Scan(&rs.Region, &rs.SettlementCount, &rs.Population); err == nil {
				narrative.RegionalBreakdown = append(narrative.RegionalBreakdown, rs)
			}
		}
	}

	// Get classification breakdown
	classRows, err := s.DB.Query(`
		SELECT COALESCE(classification, 'unclassified'), COUNT(*)
		FROM park_settlements
		WHERE park_id = ?`+settlementFilterSQL("narrative", "polygon_ids")+`
		GROUP BY classification
	`, internalID)
	if err == nil {
		defer classRows.Close()
		classMap := make(map[string]int)
		for classRows.Next() {
			var cls string
			var cnt int
			if classRows.Scan(&cls, &cnt) == nil {
				classMap[publicSettlementClass(cls)] += cnt
			}
		}
		if len(classMap) > 0 {
			narrative.ByClassification = classMap
		}
	}

	// Persistence breakdown, MEASURED from GHSL back-epochs
	// (scripts/ghsl_epochs.py). 'unmeasured' (tile_missing) is counted apart:
	// a cluster whose epoch tile never downloaded is not a recent one
	// (invariant 1 — a pixel absent from an old epoch and a tile absent from
	// the download are different states).
	persRows, err := s.DB.Query(`
		SELECT COALESCE(persistence, ''), COUNT(*)
		FROM park_settlements
		WHERE park_id = ?`+settlementFilterSQL("narrative", "polygon_ids")+`
		GROUP BY 1
	`, internalID)
	if err == nil {
		defer persRows.Close()
		persMap := make(map[string]int)
		for persRows.Next() {
			var p string
			var cnt int
			if persRows.Scan(&p, &cnt) == nil {
				if p == "" {
					narrative.PersistenceUnmeasured += cnt
				} else {
					persMap[p] += cnt
				}
			}
		}
		if len(persMap) > 0 {
			narrative.ByPersistence = persMap
		}
	}

	// Cropland aggregate (GLAD 30 m, scripts/cropland.py). Scoped to measured
	// rows only — the mean of a column where NULL reads as 0 would launder
	// unmeasured into crop-free (invariant 1).
	s.DB.QueryRow(`
		SELECT COUNT(*),
		       COALESCE(SUM(CASE WHEN cropland_frac_2019 >= 0.03 THEN 1 ELSE 0 END), 0),
		       COALESCE(AVG(cropland_frac_2019), 0),
		       COALESCE(AVG(cropland_frac_2003), 0)
		FROM park_settlements
		WHERE park_id = ? AND cropland_frac_2019 IS NOT NULL`+
		settlementFilterSQL("narrative", "polygon_ids"),
		internalID).Scan(&narrative.CroplandMeasuredFor, &narrative.CroplandSettlements,
		&narrative.CroplandMeanFrac2019, &narrative.CroplandMeanFrac2003)

	// Get classified settlements with individual narratives
	narrative.ClassifiedList = s.GetCachedClassifiedSettlements(internalID)

	// Update ByClassification from classified data
	if len(narrative.ClassifiedList) > 0 {
		narrative.ByClassification = make(map[string]int)
		for _, cs := range narrative.ClassifiedList {
			narrative.ByClassification[cs.Classification]++
		}
	}

	// Generate comprehensive narrative
	narrative.Status = "complete"
	narrative.Summary = generateSettlementNarrative(parkName, settlementCount, narrative.TotalPopulation,
		narrative.PopulationDensity, narrative.ConflictRisk, narrative.LargestSettlements, narrative.RegionalBreakdown)
	if s := persistenceSentence(narrative.ByPersistence, narrative.PersistenceUnmeasured, settlementCount); s != "" {
		narrative.Summary += " " + s
	}
	if s := croplandNarrativeSentence(narrative.CroplandMeasuredFor, narrative.CroplandSettlements,
		narrative.CroplandMeanFrac2003, narrative.CroplandMeanFrac2019, settlementCount); s != "" {
		narrative.Summary += " " + s
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(narrative)
}

// persistenceSentence summarises GHSL back-epoch persistence for the
// settlement narrative: "Of the N settlements, X already existed in 2000, Y
// appeared between 2000 and 2015, and Z are recent (after 2015)." Counts are
// derived from the measured breakdown, never typed (invariant 2). Returns ""
// when nothing was measured — an absent measurement is not a sentence about
// zero (invariant 12); when only some clusters were measurable the sentence
// names its denominator.
// croplandNarrativeSentence summarises measured GLAD cropland context for the
// settlement narrative, naming its denominator (measured rows, not all rows)
// and the trend between the 2000-2003 and 2016-2019 epochs. Returns "" when
// nothing was measured -- an absent measurement is not a sentence about zero
// (invariant 12). "Cropland" here excludes pasture and shifting cultivation
// by the source dataset's definition.
func croplandNarrativeSentence(measured, withCrop int, mean03, mean19 float64, total int) string {
	if measured == 0 {
		return ""
	}
	if withCrop == 0 {
		return fmt.Sprintf("None of the %d settlements measured against GLAD 30m cropland extent has mapped cropland within 1km (pasture and shifting cultivation are excluded from that dataset).", measured)
	}
	s := fmt.Sprintf("%d of %d measured settlements have mapped cropland (>=3%% of the surrounding 1km, GLAD 30m, 2016-2019 epoch)", withCrop, measured)
	if mean19 >= mean03*1.5 && mean19-mean03 >= 0.01 {
		s += fmt.Sprintf("; mean cropland cover around settlements grew from %.1f%% (2000-2003) to %.1f%%", mean03*100, mean19*100)
	} else if mean03 >= mean19*1.5 && mean03-mean19 >= 0.01 {
		s += fmt.Sprintf("; mean cropland cover around settlements fell from %.1f%% (2000-2003) to %.1f%%", mean03*100, mean19*100)
	}
	return s + "."
}

func persistenceSentence(byPersistence map[string]int, unmeasured, total int) string {
	if len(byPersistence) == 0 {
		return ""
	}
	measured := 0
	for _, n := range byPersistence {
		measured += n
	}
	plural := func(n int, sing, pl string) string {
		if n == 1 {
			return sing
		}
		return pl
	}
	var parts []string
	if n := byPersistence["permanent"]; n > 0 {
		parts = append(parts, fmt.Sprintf("%d already existed in 2000", n))
	}
	if n := byPersistence["established"]; n > 0 {
		parts = append(parts, fmt.Sprintf("%d appeared between 2000 and 2015", n))
	}
	if n := byPersistence["recent"]; n > 0 {
		parts = append(parts, fmt.Sprintf("%d %s recent (no built surface before 2015)", n, plural(n, "is", "are")))
	}
	if len(parts) == 0 {
		return ""
	}
	subject := fmt.Sprintf("Of the %d settlements", measured)
	if unmeasured > 0 {
		subject = fmt.Sprintf("Of the %d settlements with epoch coverage (%d unmeasured)", measured, unmeasured)
	}
	return subject + ", " + strings.Join(parts, ", ") + " — measured from GHSL built-up epochs (E2000/E2015)."
}

// assessConflictRisk determines human-wildlife conflict risk level
func assessConflictRisk(settlementCount int, density float64) string {
	if settlementCount == 0 {
		return "minimal"
	}
	if density > 50 {
		return "critical"
	}
	if density > 20 || settlementCount > 50 {
		return "high"
	}
	if density > 5 || settlementCount > 20 {
		return "moderate"
	}
	return "low"
}

// generatePristineNarrative creates narrative for parks with no settlements
func generatePristineNarrative(parkName string, areaKm2 float64) string {
	var narrative strings.Builder
	narrative.WriteString(fmt.Sprintf("%s shows no detectable human settlements within park boundaries. ", parkName))

	if areaKm2 > 0 {
		narrative.WriteString(fmt.Sprintf("This %.0f km² protected area represents a pristine wilderness corridor with minimal direct human-wildlife interface. ", areaKm2))
	}

	narrative.WriteString("Conservation priority: Maintain buffer zones and monitor boundary areas for encroachment. ")
	narrative.WriteString("This intact habitat status is rare in the region and critical for wildlife movement corridors.")

	return narrative.String()
}

// formatArea formats area in appropriate units (m², ha, km²)
func formatArea(m2 float64) string {
	if m2 >= 1000000 {
		return fmt.Sprintf("%.1f km²", m2/1000000)
	}
	if m2 >= 10000 {
		return fmt.Sprintf("%.1f ha", m2/10000)
	}
	return fmt.Sprintf("%.0f m²", m2)
}

// generateSettlementNarrative creates a rich, story-like narrative using OSM place references
func generateSettlementNarrative(parkName string, count int, totalPop int64, density float64, risk string,
	largest []SettlementDetail, regions []RegionSettlement) string {

	var narrative strings.Builder

	// Calculate total built-up area
	var totalArea float64
	for _, s := range largest {
		totalArea += s.AreaM2
	}

	// Opening: Overall settlement footprint
	narrative.WriteString(fmt.Sprintf("%s shows %s of built-up area across %d detected settlements. ",
		parkName, formatArea(totalArea), count))

	// Assess and describe the settlement pattern
	pattern := assessSettlementPattern(largest)
	narrative.WriteString(describeSettlementPattern(pattern, count))

	// Describe distribution using place names (like fire/deforestation narratives)
	if len(largest) >= 3 {
		// Find the geographic center of largest settlements
		var sumLat, sumLon float64
		for i := 0; i < 3 && i < len(largest); i++ {
			sumLat += largest[i].Lat
			sumLon += largest[i].Lon
		}
		centerLat := sumLat / 3
		centerLon := sumLon / 3

		// Use largest settlement's place name as reference
		if largest[0].Name != "" && largest[0].Name != "Unnamed settlement" {
			narrative.WriteString(fmt.Sprintf("Settlement activity is concentrated near %s. ", largest[0].Name))

			// Describe spread from this center
			if len(largest) >= 2 && largest[1].Name != "" {
				bearing := bearingTo(largest[0].Lat, largest[0].Lon, largest[1].Lat, largest[1].Lon)
				dir := bearingToCardinal(bearing)
				dist := haversineDistance(largest[0].Lat, largest[0].Lon, largest[1].Lat, largest[1].Lon)
				narrative.WriteString(fmt.Sprintf("Additional pressure extends %.0f km %s toward %s. ",
					dist, strings.ToLower(dir), largest[1].Name))
			}
		} else {
			// Fall back to coordinate-based description
			narrative.WriteString(fmt.Sprintf("Settlement activity centered around %.2f°, %.2f°. ", centerLat, centerLon))
		}
	}

	// List key settlements with rich location descriptions
	if len(largest) > 0 {
		narrative.WriteString("\n\nKey built-up areas: ")
		maxToDescribe := 5
		if len(largest) < maxToDescribe {
			maxToDescribe = len(largest)
		}

		for i := 0; i < maxToDescribe; i++ {
			s := largest[i]
			if i > 0 {
				narrative.WriteString(" ")
			}
			narrative.WriteString(describeSettlementWithPattern(s, i+1))
		}
	}

	// Encroachment analysis using place-based references
	if len(largest) >= 2 {
		narrative.WriteString("\n\n")
		narrative.WriteString(generatePlaceBasedEncroachment(largest))
	}

	return narrative.String()
}

// assessSettlementPattern determines the spatial pattern of settlements
func assessSettlementPattern(settlements []SettlementDetail) string {
	if len(settlements) == 0 {
		return "none"
	}
	if len(settlements) == 1 {
		return "isolated"
	}

	// Calculate spread and clustering
	var totalDist float64
	var pairCount int
	for i := 0; i < len(settlements) && i < 10; i++ {
		for j := i + 1; j < len(settlements) && j < 10; j++ {
			dist := haversineDistance(settlements[i].Lat, settlements[i].Lon,
				settlements[j].Lat, settlements[j].Lon)
			totalDist += dist
			pairCount++
		}
	}

	if pairCount == 0 {
		return "isolated"
	}

	avgDist := totalDist / float64(pairCount)

	// Check for linear pattern (settlements along a line/road)
	if len(settlements) >= 3 {
		if isLinearPattern(settlements[:min(10, len(settlements))]) {
			return "linear"
		}
	}

	// Classify based on average distance
	switch {
	case avgDist < 5:
		return "clustered"
	case avgDist < 15:
		return "scattered"
	default:
		return "dispersed"
	}
}

// isLinearPattern checks if settlements follow a linear pattern (e.g., along a road)
func isLinearPattern(settlements []SettlementDetail) bool {
	if len(settlements) < 3 {
		return false
	}
	// Simple check: see if bearings between consecutive settlements are similar
	var bearings []float64
	for i := 0; i < len(settlements)-1; i++ {
		b := bearingTo(settlements[i].Lat, settlements[i].Lon,
			settlements[i+1].Lat, settlements[i+1].Lon)
		bearings = append(bearings, b)
	}

	// Check variance in bearings
	if len(bearings) < 2 {
		return false
	}
	var sum float64
	for _, b := range bearings {
		sum += b
	}
	avg := sum / float64(len(bearings))

	var variance float64
	for _, b := range bearings {
		diff := b - avg
		// Handle wrap-around at 360
		if diff > 180 {
			diff -= 360
		}
		if diff < -180 {
			diff += 360
		}
		variance += diff * diff
	}
	variance /= float64(len(bearings))

	// Low variance suggests linear pattern
	return variance < 2500 // ~50 degree standard deviation
}

// describeSettlementPattern returns a description of the settlement pattern
func describeSettlementPattern(pattern string, count int) string {
	switch pattern {
	case "clustered":
		return "Settlements form a dense cluster, suggesting a major population center or urban expansion. "
	case "linear":
		return "Settlements follow a linear pattern, likely along a road or river corridor. "
	case "scattered":
		return "Scattered settlement pattern indicates dispersed agricultural or pastoral communities. "
	case "dispersed":
		return "Widely dispersed settlements suggest isolated homesteads or temporary camps. "
	case "isolated":
		return "Single isolated settlement detected. "
	default:
		if count > 50 {
			return "Extensive settlement activity detected across multiple areas. "
		}
		return ""
	}
}

// describeSettlementWithPattern creates a location description for a single settlement
func describeSettlementWithPattern(s SettlementDetail, rank int) string {
	areaStr := formatArea(s.AreaM2)

	if s.Name != "" && s.Name != "Unnamed settlement" {
		if s.Direction != "" && s.DistanceToPlaceKm > 0 && s.DistanceToPlaceKm < 50 {
			return fmt.Sprintf("%s near %s (%.1f km %s).", areaStr, s.Name, s.DistanceToPlaceKm, s.Direction)
		}
		return fmt.Sprintf("%s near %s.", areaStr, s.Name)
	}
	return fmt.Sprintf("%s detected at %.3f°, %.3f°.", areaStr, s.Lat, s.Lon)
}

// generatePlaceBasedEncroachment creates encroachment summary using place names
func generatePlaceBasedEncroachment(settlements []SettlementDetail) string {
	if len(settlements) < 2 {
		return ""
	}

	var summary strings.Builder

	// Find the largest settlement as primary reference
	primary := settlements[0]

	// Collect place names for context
	var placeNames []string
	for i := 0; i < len(settlements) && i < 5; i++ {
		if settlements[i].Name != "" && settlements[i].Name != "Unnamed settlement" {
			placeNames = append(placeNames, settlements[i].Name)
		}
	}

	if len(placeNames) == 0 {
		return "Settlement pressure detected but no named places identified for reference."
	}

	// Determine encroachment direction from settlement positions
	var avgLat, avgLon float64
	for _, s := range settlements {
		avgLat += s.Lat
		avgLon += s.Lon
	}
	avgLat /= float64(len(settlements))
	avgLon /= float64(len(settlements))

	// Direction from center to largest settlement indicates pressure direction
	bearing := bearingTo(avgLat, avgLon, primary.Lat, primary.Lon)
	pressureDir := bearingToCardinal(bearing)

	if len(placeNames) == 1 {
		summary.WriteString(fmt.Sprintf("Primary encroachment pressure originates from the %s, near %s.",
			strings.ToLower(pressureDir), placeNames[0]))
	} else if len(placeNames) == 2 {
		summary.WriteString(fmt.Sprintf("Encroachment pressure concentrated near %s and %s, advancing from the %s.",
			placeNames[0], placeNames[1], strings.ToLower(pressureDir)))
	} else {
		summary.WriteString(fmt.Sprintf("Encroachment pressure originates from the %s boundary, particularly near %s, %s, and %s.",
			strings.ToLower(pressureDir), placeNames[0], placeNames[1], placeNames[2]))
	}

	return summary.String()
}

// formatPopulation formats population numbers with K/M suffixes
func formatPopulation(pop int64) string {
	if pop >= 1000000 {
		return fmt.Sprintf("%.1fM", float64(pop)/1000000)
	}
	if pop >= 1000 {
		return fmt.Sprintf("%.0fK", float64(pop)/1000)
	}
	return fmt.Sprintf("%d", pop)
}

// Helper function to describe deforestation patterns
func describePattern(pattern string) string {
	switch pattern {
	case "strip":
		return "The linear pattern suggests road construction or logging track expansion."
	case "cluster":
		return "The clustered pattern may indicate mining activity or localized clearing."
	case "scattered":
		return "The scattered pattern is consistent with smallholder agricultural expansion."
	case "edge":
		return "Loss concentrated along park boundaries indicates agricultural encroachment from surrounding communities."
	default:
		return ""
	}
}

// describePatternVaried provides varied pattern descriptions based on context
func describePatternVaried(pattern string, areaKm2 float64, year int) string {
	// Different phrasings for variety
	scatteredPhrases := []string{
		"The scattered pattern is consistent with smallholder agricultural expansion.",
		"Dispersed clearing suggests multiple small-scale farming operations.",
		"The diffuse pattern indicates gradual encroachment from various points.",
		"Multiple small clearings are typical of subsistence agriculture.",
	}

	clusterPhrases := []string{
		"The clustered pattern may indicate mining activity or localized clearing.",
		"Concentrated loss suggests organized clearing for commercial purposes.",
		"The tight cluster pattern is consistent with settlement expansion.",
		"Focused deforestation indicates a single major clearing event.",
	}

	stripPhrases := []string{
		"The linear pattern suggests road construction or logging track expansion.",
		"Linear clearing indicates infrastructure development or logging access.",
		"The strip pattern is consistent with road-building or utility corridors.",
	}

	edgePhrases := []string{
		"Loss concentrated along park boundaries indicates agricultural encroachment from surrounding communities.",
		"Edge-focused clearing reflects pressure from adjacent farming areas.",
		"Boundary-adjacent loss suggests expansion of neighboring settlements.",
	}

	// Use year as seed for deterministic variety
	index := year % 4

	switch pattern {
	case "strip":
		return stripPhrases[index%len(stripPhrases)]
	case "cluster":
		return clusterPhrases[index%len(clusterPhrases)]
	case "scattered":
		return scatteredPhrases[index%len(scatteredPhrases)]
	case "edge":
		return edgePhrases[index%len(edgePhrases)]
	default:
		// Provide context-based default
		if areaKm2 > 5 {
			return "The significant loss area warrants investigation into underlying causes."
		}
		return "Further analysis needed to determine the cause of forest loss."
	}
}

// buildDeforestationNarrative creates a rich, location-based narrative for a single year's deforestation
func (s *Server) buildDeforestationNarrative(parkID string, year int, area, lat, lon float64, pattern string, settlements, rivers []OSMPlace) string {
	var narr strings.Builder

	// Determine magnitude description
	var magnitude string
	switch {
	case area > 20:
		magnitude = "severe"
	case area > 10:
		magnitude = "substantial"
	case area > 5:
		magnitude = "significant"
	case area > 2:
		magnitude = "moderate"
	default:
		magnitude = "localized"
	}

	// Determine sector based on coordinates relative to park center
	sector := s.describeParkSector(parkID, lat, lon)

	// Find the closest settlement for primary location reference
	var primaryPlace string
	var primaryDist float64
	var primaryLat, primaryLon float64
	for _, p := range settlements {
		if p.Distance < 30 {
			primaryPlace = p.Name
			primaryDist = p.Distance
			primaryLat, primaryLon = p.Lat, p.Lon
			break
		}
	}
	// Nearest named place at any distance - used for wording that must not
	// render an empty name (settlements[] is distance-sorted).
	var nearestPlace string
	var nearestDist float64
	if len(settlements) > 0 {
		nearestPlace = settlements[0].Name
		nearestDist = settlements[0].Distance
	}

	// Find nearby river for geographic context
	var riverContext string
	for _, r := range rivers {
		if r.Distance < 15 {
			if r.Distance < 3 {
				riverContext = fmt.Sprintf("along the %s", r.Name)
			} else {
				riverContext = fmt.Sprintf("near the %s", r.Name)
			}
			break
		}
	}

	// Build opening sentence with location
	if primaryPlace != "" && primaryDist < 5 {
		narr.WriteString(fmt.Sprintf("In %d, %s forest loss (%s) was concentrated near %s",
			year, magnitude, formatLossArea(area), primaryPlace))
	} else if primaryPlace != "" {
		bearing := bearingTo(lat, lon, primaryLat, primaryLon)
		direction := bearingToCardinal(bearing)
		// Reverse direction since we want "X of settlement"
		reverseDir := reverseCardinal(direction)
		narr.WriteString(fmt.Sprintf("In %d, %s forest loss (%s) occurred %.0f km %s of %s",
			year, magnitude, formatLossArea(area), primaryDist, reverseDir, primaryPlace))
	} else if sector != "" {
		narr.WriteString(fmt.Sprintf("In %d, %s forest loss (%s) was detected in the %s",
			year, magnitude, formatLossArea(area), sector))
	} else {
		narr.WriteString(fmt.Sprintf("In %d, %s of forest was lost", year, formatLossArea(area)))
	}

	// Add river context
	if riverContext != "" {
		narr.WriteString(fmt.Sprintf(", %s", riverContext))
	}
	narr.WriteString(". ")

	// Add pattern-specific description with geographic context
	switch pattern {
	case "scattered":
		if primaryPlace != "" && len(settlements) > 1 {
			narr.WriteString(fmt.Sprintf("The scattered clearing pattern suggests smallholder agricultural expansion, "+
				"with multiple small plots appearing between %s and surrounding communities.", primaryPlace))
		} else if nearestPlace != "" && nearestDist > 30 {
			// Remote: no community is close enough to blame for smallholder expansion.
			narr.WriteString(fmt.Sprintf("Dispersed clearing this far from settlement "+
				"(nearest is %s, %.0f km away) is more consistent with natural canopy "+
				"disturbance than with farming.", nearestPlace, nearestDist))
		} else {
			narr.WriteString("Dispersed clearing indicates gradual encroachment from multiple entry points, " +
				"typical of subsistence farming expansion.")
		}
	case "cluster":
		if primaryPlace != "" {
			narr.WriteString(fmt.Sprintf("The concentrated loss pattern near %s suggests organized clearing, "+
				"possibly for commercial agriculture or settlement expansion.", primaryPlace))
		} else {
			narr.WriteString("Clustered deforestation indicates a single major clearing operation, " +
				"possibly mining activity or commercial land conversion.")
		}
	case "strip":
		if riverContext != "" {
			narr.WriteString(fmt.Sprintf("Linear clearing %s suggests road construction or logging track development "+
				"following the river corridor.", riverContext))
		} else if primaryPlace != "" {
			narr.WriteString(fmt.Sprintf("The strip pattern indicates infrastructure development, "+
				"likely a road or logging track extending toward %s.", primaryPlace))
		} else {
			narr.WriteString("Linear clearing pattern indicates road construction or logging access routes.")
		}
	case "edge":
		if primaryPlace != "" {
			narr.WriteString(fmt.Sprintf("Forest loss concentrated along park boundaries near %s "+
				"reflects agricultural pressure from surrounding communities.", primaryPlace))
		} else {
			narr.WriteString("Edge-focused clearing indicates encroachment from communities adjacent to the protected area.")
		}
	default:
		if area > 5 {
			narr.WriteString("The significant extent of clearing warrants investigation to determine drivers.")
		}
	}

	// The pattern switch can add nothing (small event, unknown pattern), which
	// left a trailing space after the opening sentence.
	return strings.TrimSpace(narr.String())
}

// pluralize renders "1 recorded event" / "273 recorded events".
func pluralize(n int, noun string) string {
	if n == 1 {
		return fmt.Sprintf("%d %s", n, noun)
	}
	return fmt.Sprintf("%d %ss", n, noun)
}

// formatLossArea renders a clearing area so that sub-hectare events don't all
// print as "0.00 km²". GFW's 30 m pixel is 0.0009 km², so most single-pixel
// events fall far below two decimals of a km².
func formatLossArea(areaKm2 float64) string {
	if areaKm2 < 0.01 {
		// Below a hectare, report m²: GFW's 30 m pixel is 900 m², and "0.0 ha"
		// is what a single-pixel event used to print.
		return fmt.Sprintf("%.0f m²", areaKm2*1e6)
	}
	if areaKm2 < 1 {
		return fmt.Sprintf("%.0f ha", areaKm2*100)
	}
	return fmt.Sprintf("%.2f km²", areaKm2)
}

// buildDeforestationSummary creates a comprehensive summary with geographic shift analysis
func (s *Server) buildDeforestationSummary(parkName string, totalLoss float64, eventCount int,
	worstYear int, worstLoss float64, trendDir string, trendPct float64,
	earlyLat, earlyLon, recentLat, recentLon float64, earlyCount, recentCount int, parkID string,
	yearSpanFrom, yearSpanTo int) string {

	var parts []string

	// Opening statement.
	// NOTE: yearly_stories is a per-EVENT list (multiple rows share a year), so
	// its length is an event count, not a year count. It used to be passed as
	// `yearCount`, producing "across 273 recorded years" for CAF_Chinko.
	span := ""
	if yearSpanFrom > 0 && yearSpanTo >= yearSpanFrom {
		if yearSpanFrom == yearSpanTo {
			span = fmt.Sprintf(" in %d", yearSpanFrom)
		} else {
			span = fmt.Sprintf(" between %d and %d", yearSpanFrom, yearSpanTo)
		}
	}
	parts = append(parts, fmt.Sprintf("%s has experienced %.2f km² of cumulative forest loss across %s%s.",
		parkName, totalLoss, pluralize(eventCount, "recorded event"), span))

	// Worst year with location context
	if worstYear > 0 {
		parts = append(parts, fmt.Sprintf("The most severe year was %d with %.2f km² lost.", worstYear, worstLoss))
	}

	// Trend description
	trendDesc := describeTrend(trendDir, trendPct)
	if trendDesc != "" {
		parts = append(parts, trendDesc)
	}

	// Geographic shift analysis
	if earlyCount > 0 && recentCount > 0 {
		// Calculate distance and direction of shift
		distKm := haversineDistance(earlyLat, earlyLon, recentLat, recentLon)
		if distKm > 5 { // Only mention if shift is significant (>5km)
			bearing := bearingTo(earlyLat, earlyLon, recentLat, recentLon)
			direction := bearingToCardinal(bearing)

			// Try to describe the shift in terms of places
			earlyDesc := s.describeLocation(parkID, earlyLat, earlyLon)
			recentDesc := s.describeLocation(parkID, recentLat, recentLon)

			if !strings.HasPrefix(earlyDesc, "at coordinates") && !strings.HasPrefix(recentDesc, "at coordinates") {
				parts = append(parts, fmt.Sprintf("Deforestation pressure has shifted %sward, "+
					"from areas %s toward %s.", strings.ToLower(direction), earlyDesc, recentDesc))
			} else {
				sector := s.describeParkSector(parkID, recentLat, recentLon)
				if sector != "" {
					parts = append(parts, fmt.Sprintf("Deforestation activity has shifted %sward toward the %s over the observation period.",
						strings.ToLower(direction), sector))
				}
			}
		}
	}

	return strings.Join(parts, " ")
}

// describeParkSector returns a description of which part of the park a coordinate is in
func (s *Server) describeParkSector(parkID string, lat, lon float64) string {
	// Get park center by calculating from geometry bounding box
	var centerLat, centerLon float64
	var found bool

	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.ID == parkID || area.WDPAID == parkID {
				// Calculate center from geometry bounding box
				latMin, latMax, lonMin, lonMax := area.GetBoundingBox()
				centerLat = (latMin + latMax) / 2
				centerLon = (lonMin + lonMax) / 2
				found = true
				break
			}
		}
	}

	if !found {
		return ""
	}

	// Determine which sector based on position relative to center
	latDiff := lat - centerLat
	lonDiff := lon - centerLon

	// Compound sectors read as "south-western", not "southernwestern", so keep
	// the bare stem and only add the "-ern" suffix on the last word.
	var ns, ew string
	if latDiff > 0.05 {
		ns = "north"
	} else if latDiff < -0.05 {
		ns = "south"
	}

	if lonDiff > 0.05 {
		ew = "east"
	} else if lonDiff < -0.05 {
		ew = "west"
	}

	switch {
	case ns == "" && ew == "":
		return "central sector"
	case ns == "":
		return ew + "ern sector"
	case ew == "":
		return ns + "ern sector"
	}
	return ns + "-" + ew + "ern sector"
}

// reverseCardinal returns the opposite cardinal direction
func reverseCardinal(dir string) string {
	reverse := map[string]string{
		"N": "S", "S": "N", "E": "W", "W": "E",
		"NE": "SW", "NW": "SE", "SE": "NW", "SW": "NE",
		"NNE": "SSW", "ENE": "WSW", "ESE": "WNW", "SSE": "NNW",
		"SSW": "NNE", "WSW": "ENE", "WNW": "ESE", "NNW": "SSE",
	}
	if r, ok := reverse[dir]; ok {
		return r
	}
	return dir
}

// calculateTrend computes the 5-year rolling average trend
func calculateTrend(yearlyAreas []struct {
	year int
	area float64
}) (direction string, percentChange, earlyAvg, recentAvg float64) {
	if len(yearlyAreas) < 5 {
		return "insufficient_data", 0, 0, 0
	}

	// Calculate early 5-year average (first 5 years)
	earlyYears := 5
	if len(yearlyAreas) < 10 {
		earlyYears = len(yearlyAreas) / 2
	}
	if earlyYears < 2 {
		earlyYears = 2
	}

	var earlySum float64
	for i := 0; i < earlyYears; i++ {
		earlySum += yearlyAreas[i].area
	}
	earlyAvg = earlySum / float64(earlyYears)

	// Calculate recent 5-year average (last 5 years)
	recentYears := 5
	if len(yearlyAreas) < 10 {
		recentYears = len(yearlyAreas) - earlyYears
	}
	if recentYears < 2 {
		recentYears = 2
	}

	var recentSum float64
	for i := len(yearlyAreas) - recentYears; i < len(yearlyAreas); i++ {
		recentSum += yearlyAreas[i].area
	}
	recentAvg = recentSum / float64(recentYears)

	// Calculate percent change
	if earlyAvg > 0 {
		percentChange = ((recentAvg - earlyAvg) / earlyAvg) * 100
	}

	// Determine trend direction (10% threshold for "stable")
	if percentChange > 10 {
		direction = "worsening"
	} else if percentChange < -10 {
		direction = "improving"
	} else {
		direction = "stable"
	}

	return direction, percentChange, earlyAvg, recentAvg
}

// describeTrend generates human-readable trend description
func describeTrend(direction string, percentChange float64) string {
	switch direction {
	case "worsening":
		return fmt.Sprintf("⚠️ TREND ALERT: Deforestation has increased by %.0f%% comparing recent years to earlier periods.", percentChange)
	case "improving":
		return fmt.Sprintf("✅ POSITIVE TREND: Deforestation has decreased by %.0f%% comparing recent years to earlier periods.", -percentChange)
	case "stable":
		return "Deforestation rates have remained relatively stable over the monitoring period."
	default:
		return "Insufficient data to determine long-term trend."
	}
}

// fetchHotspots retrieves the worst deforestation clusters for a park
func (s *Server) fetchHotspots(parkID string, limit int) []DeforestationHotspot {
	var hotspots []DeforestationHotspot

	rows, err := s.DB.Query(`
		SELECT year, cluster_id, area_km2, lat, lon, COALESCE(pattern_type, 'unknown'), COALESCE(description, '')
		FROM deforestation_clusters
		WHERE park_id = ?
		ORDER BY area_km2 DESC
		LIMIT ?
	`, parkID, limit)
	if err != nil {
		return hotspots
	}
	defer rows.Close()

	for rows.Next() {
		var h DeforestationHotspot
		if err := rows.Scan(&h.Year, &h.ClusterID, &h.AreaKm2, &h.Lat, &h.Lon, &h.PatternType, &h.Description); err != nil {
			continue
		}

		// Generate description if empty
		if h.Description == "" {
			locationDesc := s.describeLocation(parkID, h.Lat, h.Lon)
			h.Description = fmt.Sprintf("%.2f km² lost in %d %s", h.AreaKm2, h.Year, locationDesc)
		}

		hotspots = append(hotspots, h)
	}

	return hotspots
}

// determinePatternType analyzes cluster data to determine actual pattern type
func (s *Server) determinePatternType(parkID string, year int, defaultPattern string) string {
	// Query clusters for this park/year to analyze distribution
	var clusterCount int
	var totalArea float64
	var latMin, latMax, lonMin, lonMax sql.NullFloat64

	err := s.DB.QueryRow(`
		SELECT COUNT(*), COALESCE(SUM(area_km2), 0),
		       MIN(lat), MAX(lat), MIN(lon), MAX(lon)
		FROM deforestation_clusters
		WHERE park_id = ? AND year = ?
	`, parkID, year).Scan(&clusterCount, &totalArea, &latMin, &latMax, &lonMin, &lonMax)

	if err != nil || clusterCount == 0 {
		return defaultPattern
	}

	// Calculate geographic spread
	latSpread := 0.0
	lonSpread := 0.0
	if latMin.Valid && latMax.Valid {
		latSpread = latMax.Float64 - latMin.Float64
	}
	if lonMin.Valid && lonMax.Valid {
		lonSpread = lonMax.Float64 - lonMin.Float64
	}

	// Determine pattern based on cluster analysis
	if clusterCount == 1 {
		return "cluster" // Single concentrated area
	}

	// Check for linear (strip) pattern - one dimension much larger than other
	aspectRatio := 0.0
	if latSpread > 0 && lonSpread > 0 {
		if latSpread > lonSpread {
			aspectRatio = latSpread / lonSpread
		} else {
			aspectRatio = lonSpread / latSpread
		}
	}

	if aspectRatio > 3.0 {
		return "strip" // Linear pattern
	}

	// Check for cluster vs scattered based on density
	spreadArea := latSpread * lonSpread * 111 * 111 // Rough km² conversion
	if spreadArea > 0 {
		density := float64(clusterCount) / spreadArea
		if density > 0.5 { // High density of clusters
			return "cluster"
		}
	}

	// If many small clusters spread out
	if clusterCount > 5 {
		return "scattered"
	}

	// Check if clusters are from database with explicit pattern
	var clusterPattern sql.NullString
	s.DB.QueryRow(`
		SELECT pattern_type FROM deforestation_clusters
		WHERE park_id = ? AND year = ? AND pattern_type IS NOT NULL
		GROUP BY pattern_type
		ORDER BY COUNT(*) DESC
		LIMIT 1
	`, parkID, year).Scan(&clusterPattern)

	if clusterPattern.Valid && clusterPattern.String != "" {
		return clusterPattern.String
	}

	return defaultPattern
}

// Helper function to get unique strings from a slice
func uniqueStrings(input []string) []string {
	seen := make(map[string]bool)
	var result []string
	for _, s := range input {
		if !seen[s] {
			seen[s] = true
			result = append(result, s)
		}
	}
	return result
}

// analyzeFireHotspots identifies geographic concentrations of fire activity
func (s *Server) analyzeFireHotspots(parkID string, year int, totalFires int) []FireHotspot {
	var hotspots []FireHotspot

	// Query fire clusters by 0.1 degree grid cells
	rows, err := s.DB.Query(`
		SELECT 
			ROUND(latitude, 1) as lat_bucket,
			ROUND(longitude, 1) as lon_bucket,
			AVG(latitude) as avg_lat,
			AVG(longitude) as avg_lon,
			COUNT(*) as fire_count
		FROM fire_detections 
		WHERE protected_area_id = ?`+fireInsideSQL+` AND strftime('%Y', acq_date) = ?
		GROUP BY lat_bucket, lon_bucket
		HAVING fire_count >= 10
		ORDER BY fire_count DESC
		LIMIT 10
	`, parkID, fmt.Sprintf("%d", year))
	if err != nil {
		return hotspots
	}
	// Buffer rows before per-row enrichment queries: nested queries while
	// rows are open hold a pool conn AND request another → pool deadlock.
	type hsRow struct {
		avgLat, avgLon float64
		fireCount      int
	}
	var hsRows []hsRow
	for rows.Next() {
		var latBucket, lonBucket, avgLat, avgLon float64
		var fireCount int
		if err := rows.Scan(&latBucket, &lonBucket, &avgLat, &avgLon, &fireCount); err != nil {
			continue
		}
		hsRows = append(hsRows, hsRow{avgLat, avgLon, fireCount})
	}
	rows.Close()

	for _, hr := range hsRows {
		avgLat, avgLon, fireCount := hr.avgLat, hr.avgLon, hr.fireCount
		hs := FireHotspot{
			Lat:       avgLat,
			Lon:       avgLon,
			FireCount: fireCount,
		}
		if totalFires > 0 {
			hs.Percentage = float64(fireCount) / float64(totalFires) * 100
		}

		// Find nearby places for context
		settlements, _ := s.findNearestPlaces(parkID, avgLat, avgLon, 2, []string{"village", "hamlet", "town", "city"})
		rivers, _ := s.findNearestPlaces(parkID, avgLat, avgLon, 1, []string{"river", "stream"})

		var nearbyNames []string
		for _, p := range settlements {
			if p.Distance < 30 {
				nearbyNames = append(nearbyNames, fmt.Sprintf("%s (%.0fkm)", p.Name, p.Distance))
			}
		}
		for _, p := range rivers {
			if p.Distance < 20 {
				nearbyNames = append(nearbyNames, fmt.Sprintf("%s River (%.0fkm)", p.Name, p.Distance))
			}
		}
		hs.NearbyPlaces = nearbyNames

		// Build description
		locationDesc := s.describeLocation(parkID, avgLat, avgLon)
		hs.Description = fmt.Sprintf("Fire hotspot %s with %d detections (%.1f%% of park total). ",
			locationDesc, fireCount, hs.Percentage)
		if len(nearbyNames) > 0 {
			hs.Description += fmt.Sprintf("Nearby: %s.", strings.Join(nearbyNames, ", "))
		}

		hotspots = append(hotspots, hs)
	}

	return hotspots
}

// analyzeFireTrend provides multi-year trend analysis
func (s *Server) analyzeFireTrend(parkID string, currentYear int) *FireTrendAnalysis {
	trend := &FireTrendAnalysis{}

	// Get all years of data
	rows, err := s.DB.Query(`
		SELECT 
			pgi.year,
			pgi.total_groups,
			pgi.groups_stopped_inside,
			pgi.groups_transited,
			pgi.avg_days_burning,
			COALESCE(fd.fire_count, 0) as total_fires
		FROM park_group_infractions pgi
		LEFT JOIN (
			SELECT 
				protected_area_id,
				CAST(strftime('%Y', acq_date) AS INTEGER) as year,
				COUNT(*) as fire_count
			FROM fire_detections
			WHERE in_protected_area = 1
			GROUP BY protected_area_id, strftime('%Y', acq_date)
		) fd ON pgi.park_id = fd.protected_area_id AND pgi.year = fd.year
		WHERE pgi.park_id = ?
		ORDER BY pgi.year
	`, parkID)
	if err != nil {
		return nil
	}
	defer rows.Close()

	var totalResponseRate float64
	var yearCount int
	var worstGroups int
	var bestRate float64 = -1

	for rows.Next() {
		var ys FireYearSummary
		if err := rows.Scan(&ys.Year, &ys.TotalGroups, &ys.StoppedInside, &ys.Transited, &ys.AvgDaysBurning, &ys.TotalFires); err != nil {
			continue
		}
		if ys.TotalGroups > 0 {
			ys.ResponseRate = float64(ys.StoppedInside) / float64(ys.TotalGroups) * 100
			totalResponseRate += ys.ResponseRate
			yearCount++

			if ys.TotalGroups > worstGroups {
				worstGroups = ys.TotalGroups
				trend.WorstYear = ys.Year
				trend.WorstYearGroups = ys.TotalGroups
			}
			if bestRate < 0 || ys.ResponseRate > bestRate {
				bestRate = ys.ResponseRate
				trend.BestYear = ys.Year
				trend.BestYearRate = ys.ResponseRate
			}
		}
		trend.Years = append(trend.Years, ys)
	}

	if yearCount > 0 {
		trend.AvgResponseRate = totalResponseRate / float64(yearCount)
	}

	// Determine trend direction
	if len(trend.Years) >= 3 {
		recentAvg := 0.0
		earlyAvg := 0.0
		mid := len(trend.Years) / 2
		for i, y := range trend.Years {
			if i < mid {
				earlyAvg += float64(y.TotalGroups)
			} else {
				recentAvg += float64(y.TotalGroups)
			}
		}
		earlyAvg /= float64(mid)
		recentAvg /= float64(len(trend.Years) - mid)

		if recentAvg > earlyAvg*1.2 {
			trend.TrendDirection = "increasing"
		} else if recentAvg < earlyAvg*0.8 {
			trend.TrendDirection = "decreasing"
		} else {
			trend.TrendDirection = "stable"
		}
	}

	// Build trend narrative
	if len(trend.Years) > 1 {
		var narr strings.Builder
		narr.WriteString(fmt.Sprintf("Analysis of %d years of fire data (%d-%d). ",
			len(trend.Years), trend.Years[0].Year, trend.Years[len(trend.Years)-1].Year))

		switch trend.TrendDirection {
		case "increasing":
			narr.WriteString("⚠️ Fire pressure is INCREASING - enhanced monitoring recommended. ")
		case "decreasing":
			narr.WriteString("✓ Fire pressure is DECREASING - conservation efforts may be working. ")
		case "stable":
			narr.WriteString("Fire pressure remains relatively stable over the analysis period. ")
		}

		narr.WriteString(fmt.Sprintf("Average response rate: %.0f%%. ", trend.AvgResponseRate))
		if trend.WorstYear > 0 {
			narr.WriteString(fmt.Sprintf("Worst year: %d with %d fire groups. ", trend.WorstYear, trend.WorstYearGroups))
		}
		if trend.BestYear > 0 {
			narr.WriteString(fmt.Sprintf("Best response rate: %.0f%% in %d.", trend.BestYearRate, trend.BestYear))
		}
		trend.Narrative = narr.String()
	}

	// Add monthly data and groups_per_km2
	s.enrichTrendWithMonthlyData(parkID, trend)

	return trend
}

// addWeeklyDataToTrend computes weekly fire counts from feature_geometries
func (s *Server) addWeeklyDataToTrend(parkID string, trend *FireTrendAnalysis) {
	if trend == nil {
		return
	}

	// Get park area for groups_per_km2
	var areaKm2 float64
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.ID == parkID {
				areaKm2 = area.AreaKm2
				break
			}
		}
	}

	// Query weekly data (all available data from 2020-06-01)
	weekRows, err := s.DB.Query(`
		SELECT strftime('%Y-W%W', start_date) as week, 
		       COUNT(*) as groups
		FROM feature_geometries 
		WHERE park_id = ? AND feature_type = 'fire_trajectory' 
		  AND start_date >= '2020-06-01'
		GROUP BY week 
		ORDER BY week
	`, parkID)
	if err != nil {
		return
	}
	defer weekRows.Close()

	for weekRows.Next() {
		var week string
		var groups int
		if err := weekRows.Scan(&week, &groups); err == nil && week != "" {
			ws := FireWeekSummary{Week: week, Groups: groups}
			if areaKm2 > 0 {
				ws.GroupsPerKm2 = float64(groups) / areaKm2
			}
			trend.Weeks = append(trend.Weeks, ws)
		}
	}
}

// enrichTrendWithMonthlyData adds monthly breakdown and groups_per_km2 calculations
func (s *Server) enrichTrendWithMonthlyData(parkID string, trend *FireTrendAnalysis) {
	if trend == nil {
		return
	}

	// Get park area
	var areaKm2 float64 = 1.0
	var parkLat float64 = 0
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.ID == parkID {
				if area.AreaKm2 > 0 {
					areaKm2 = area.AreaKm2
				}
				lat, _ := area.CenterLatLon()
				parkLat = lat
				break
			}
		}
	}

	// Calculate groups_per_km2 for each year
	var totalGroupsPerKm2 float64
	for i := range trend.Years {
		if areaKm2 > 0 {
			trend.Years[i].GroupsPerKm2 = float64(trend.Years[i].TotalGroups) / areaKm2
			totalGroupsPerKm2 += trend.Years[i].GroupsPerKm2
		}
	}
	if len(trend.Years) > 0 {
		trend.AvgGroupsPerKm2 = totalGroupsPerKm2 / float64(len(trend.Years))
	}

	// Query monthly data
	monthRows, err := s.DB.Query(`
		SELECT strftime('%Y-%m', start_date) as month, COUNT(*) as groups
		FROM feature_geometries 
		WHERE park_id = ? AND feature_type = 'fire_trajectory' AND start_date IS NOT NULL
		GROUP BY month 
		ORDER BY month
	`, parkID)
	if err == nil {
		defer monthRows.Close()
		monthCounts := make(map[int]int) // month number (1-12) -> total groups
		for monthRows.Next() {
			var month string
			var groups int
			if err := monthRows.Scan(&month, &groups); err == nil {
				ms := FireMonthSummary{Month: month, Groups: groups}
				if areaKm2 > 0 {
					ms.GroupsPerKm2 = float64(groups) / areaKm2
				}
				trend.Months = append(trend.Months, ms)

				// Track by calendar month for seasonality
				if len(month) >= 7 {
					var m int
					fmt.Sscanf(month[5:7], "%d", &m)
					monthCounts[m] += groups
				}
			}
		}

		// Determine peak months and seasonality
		var maxGroups int
		for _, g := range monthCounts {
			if g > maxGroups {
				maxGroups = g
			}
		}
		threshold := maxGroups * 7 / 10 // 70% of peak
		monthNames := []string{"", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"}
		for m := 1; m <= 12; m++ {
			if monthCounts[m] >= threshold {
				trend.PeakMonths = append(trend.PeakMonths, monthNames[m])
			}
		}

		// Generate seasonality description
		if len(trend.PeakMonths) > 0 {
			if len(trend.PeakMonths) <= 3 {
				trend.Seasonality = "Peak activity in " + strings.Join(trend.PeakMonths, ", ")
			} else {
				trend.Seasonality = fmt.Sprintf("Peak activity %s-%s", trend.PeakMonths[0], trend.PeakMonths[len(trend.PeakMonths)-1])
			}
		}
	}

	// Query weekly data (last 2 years for detail)
	weekRows, err := s.DB.Query(`
		SELECT strftime('%Y-W%W', start_date) as week, COUNT(*) as groups
		FROM feature_geometries 
		WHERE park_id = ? AND feature_type = 'fire_trajectory' 
		  AND start_date IS NOT NULL AND start_date >= date('now', '-2 years')
		GROUP BY week 
		ORDER BY week
	`, parkID)
	if err == nil {
		defer weekRows.Close()
		for weekRows.Next() {
			var week string
			var groups int
			if err := weekRows.Scan(&week, &groups); err == nil {
				ws := FireWeekSummary{Week: week, Groups: groups}
				if areaKm2 > 0 {
					ws.GroupsPerKm2 = float64(groups) / areaKm2
				}
				trend.Weeks = append(trend.Weeks, ws)
			}
		}
	}

	// Add latitude comparison if we have the data
	if parkLat != 0 && s.AreaStore != nil && trend.AvgGroupsPerKm2 > 0 {
		trend.LatitudeComparison = s.computeLatitudeComparison(parkID, parkLat, trend.AvgGroupsPerKm2)
	}
}

// ============================================================================
// Stats-only Response Types for Enhanced API
// ============================================================================

// FireNarrativeStats represents the stats-only format for fire narratives
type FireNarrativeStats struct {
	ParkID   string        `json:"park_id"`
	ParkName string        `json:"park_name"`
	Stats    FireStatsData `json:"stats"`
	Features []FireFeature `json:"features"`
}

// FireStatsData contains aggregate fire statistics
type FireStatsData struct {
	TotalTrajectories int            `json:"total_trajectories"`
	TotalFires        int            `json:"total_fires"`
	StoppedInside     int            `json:"stopped_inside"`
	Transited         int            `json:"transited"`
	ResponseRate      float64        `json:"response_rate"`
	AvgDaysBurning    float64        `json:"avg_days_burning"`
	PeakMonth         string         `json:"peak_month,omitempty"`
	YearRange         string         `json:"year_range"`
	ByOutcome         map[string]int `json:"by_outcome"`
	ByYear            map[int]int    `json:"by_year,omitempty"`
}

// FireFeature represents a single fire trajectory with geojson reference
type FireFeature struct {
	ID          string  `json:"id"`
	GeoJSONID   int64   `json:"geojson_id"`
	Year        int     `json:"year"`
	GroupNum    int     `json:"group_num"`
	Outcome     string  `json:"outcome"`
	FiresInside int     `json:"fires_inside"`
	DaysInside  int     `json:"days_inside"`
	EntryDate   string  `json:"entry_date"`
	LastInside  string  `json:"last_inside"`
	OriginLat   float64 `json:"origin_lat"`
	OriginLon   float64 `json:"origin_lon"`
	DestLat     float64 `json:"dest_lat"`
	DestLon     float64 `json:"dest_lon"`
}

// SettlementNarrativeStats represents the stats-only format for settlements
type SettlementNarrativeStats struct {
	ParkID   string              `json:"park_id"`
	ParkName string              `json:"park_name"`
	Stats    SettlementStatsData `json:"stats"`
	Features []SettlementFeature `json:"features"`
}

// SettlementStatsData contains aggregate settlement statistics
type SettlementStatsData struct {
	SettlementCount  int            `json:"settlement_count"`
	TotalAreaKm2     float64        `json:"total_area_km2"`
	Population2030   int64          `json:"population_2030"`
	PopulationEst    int64          `json:"population_est"`
	ByClassification map[string]int `json:"by_classification"`
	AvgAreaM2        float64        `json:"avg_area_m2,omitempty"`
	AvgDistToRoadM   float64        `json:"avg_distance_to_road_m,omitempty"`
}

// SettlementFeature represents a single settlement with geojson reference
type SettlementFeature struct {
	ID             string  `json:"id"`
	GeoJSONID      int64   `json:"geojson_id"`
	Classification string  `json:"classification"`
	AreaM2         float64 `json:"area_m2"`
	PopulationEst  int64   `json:"population_est"`
	Population2030 int64   `json:"population_2030"`
	Lat            float64 `json:"lat"`
	Lon            float64 `json:"lon"`
	NearestPlace   string  `json:"nearest_place,omitempty"`
	DistToRoadM    float64 `json:"distance_to_road_m,omitempty"`
}

// DeforestationNarrativeStats represents the stats-only format for deforestation
type DeforestationNarrativeStats struct {
	ParkID   string                 `json:"park_id"`
	ParkName string                 `json:"park_name"`
	Stats    DeforestationStatsData `json:"stats"`
	Features []DeforestationFeature `json:"features"`
}

// DeforestationStatsData contains aggregate deforestation statistics
type DeforestationStatsData struct {
	TotalEvents      int             `json:"total_events"`
	TotalAreaKm2     float64         `json:"total_area_km2"`
	YearRange        string          `json:"year_range"`
	WorstYear        int             `json:"worst_year,omitempty"`
	WorstYearAreaKm2 float64         `json:"worst_year_area_km2,omitempty"`
	TrendDirection   string          `json:"trend_direction"`
	ByClassification map[string]int  `json:"by_classification"`
	ByYear           map[int]float64 `json:"by_year,omitempty"`
}

// DeforestationFeature represents a single deforestation event with geojson reference
type DeforestationFeature struct {
	ID                string  `json:"id"`
	GeoJSONID         int64   `json:"geojson_id"`
	Year              int     `json:"year"`
	Classification    string  `json:"classification"`
	AreaKm2           float64 `json:"area_km2"`
	Lat               float64 `json:"lat"`
	Lon               float64 `json:"lon"`
	PatternType       string  `json:"pattern_type,omitempty"`
	DistanceToRoadM   float64 `json:"distance_to_road_m,omitempty"`
	DistanceToSettleM float64 `json:"distance_to_settlement_m,omitempty"`
}

// handleFireNarrativeStats returns stats-only format for fire narrative
func (s *Server) handleFireNarrativeStats(w http.ResponseWriter, parkID, parkName string, startDate, endDate string) {
	// Parse year range from dates
	fromYear := 2000
	toYear := time.Now().Year()

	if startDate != "" {
		if t, err := time.Parse("2006-01-02", startDate); err == nil {
			fromYear = t.Year()
		} else if y, err := strconv.Atoi(startDate); err == nil {
			fromYear = y
		}
	}
	if endDate != "" {
		if t, err := time.Parse("2006-01-02", endDate); err == nil {
			toYear = t.Year()
		} else if y, err := strconv.Atoi(endDate); err == nil {
			toYear = y
		}
	}

	response := FireNarrativeStats{
		ParkID:   parkID,
		ParkName: parkName,
		Stats: FireStatsData{
			ByOutcome: make(map[string]int),
			ByYear:    make(map[int]int),
		},
		Features: []FireFeature{},
	}

	// Set year range string
	if fromYear == toYear {
		response.Stats.YearRange = fmt.Sprintf("%d", fromYear)
	} else {
		response.Stats.YearRange = fmt.Sprintf("%d-%d", fromYear, toYear)
	}

	// Get aggregate stats from park_group_infractions
	var totalGroups, stoppedInside, transited int
	var avgDays float64
	err := s.DB.QueryRow(`
		SELECT 
			COALESCE(SUM(total_groups), 0),
			COALESCE(SUM(groups_stopped_inside), 0),
			COALESCE(SUM(groups_transited), 0),
			COALESCE(AVG(avg_days_burning), 0)
		FROM park_group_infractions 
		WHERE park_id = ? AND year >= ? AND year <= ?
	`, parkID, fromYear, toYear).Scan(&totalGroups, &stoppedInside, &transited, &avgDays)

	if err != nil && err != sql.ErrNoRows {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	response.Stats.TotalTrajectories = totalGroups
	response.Stats.StoppedInside = stoppedInside
	response.Stats.Transited = transited
	response.Stats.AvgDaysBurning = avgDays
	response.Stats.ByOutcome["STOPPED_INSIDE"] = stoppedInside
	response.Stats.ByOutcome["TRANSITED"] = transited

	if totalGroups > 0 {
		response.Stats.ResponseRate = float64(stoppedInside) / float64(totalGroups) * 100
	}

	// Get total fire detections (inside the boundary only — srv/fire_containment.go)
	s.DB.QueryRow(`
		SELECT COUNT(*) FROM fire_detections 
		WHERE protected_area_id = ?`+fireInsideSQL+`
		AND acq_date >= ? AND acq_date <= ?
	`, parkID, fmt.Sprintf("%d-01-01", fromYear), fmt.Sprintf("%d-12-31", toYear)).Scan(&response.Stats.TotalFires)

	// Get peak month
	var peakMonth string
	s.DB.QueryRow(`
		SELECT strftime('%m', acq_date) as month
		FROM fire_detections 
		WHERE protected_area_id = ?`+fireInsideSQL+`
		AND acq_date >= ? AND acq_date <= ?
		GROUP BY month ORDER BY COUNT(*) DESC LIMIT 1
	`, parkID, fmt.Sprintf("%d-01-01", fromYear), fmt.Sprintf("%d-12-31", toYear)).Scan(&peakMonth)
	monthNames := map[string]string{
		"01": "January", "02": "February", "03": "March", "04": "April",
		"05": "May", "06": "June", "07": "July", "08": "August",
		"09": "September", "10": "October", "11": "November", "12": "December",
	}
	response.Stats.PeakMonth = monthNames[peakMonth]

	// Get trajectories by year
	rows, err := s.DB.Query(`
		SELECT year, total_groups FROM park_group_infractions 
		WHERE park_id = ? AND year >= ? AND year <= ?
	`, parkID, fromYear, toYear)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var year, groups int
			if rows.Scan(&year, &groups) == nil {
				response.Stats.ByYear[year] = groups
			}
		}
	}

	// Get fire trajectory features with geojson_id
	startDateStr := fmt.Sprintf("%d-01-01", fromYear)
	endDateStr := fmt.Sprintf("%d-12-31", toYear)

	featureRows, err := s.DB.Query(`
		SELECT fg.id, fg.feature_id, fg.start_date, fg.end_date, fg.properties_json,
		       fg.bbox_minx, fg.bbox_miny, fg.bbox_maxx, fg.bbox_maxy
		FROM feature_geometries fg
		WHERE fg.park_id = ? 
		  AND fg.feature_type = 'fire_trajectory'
		  AND fg.start_date >= ? AND fg.end_date <= ?
		ORDER BY fg.start_date DESC
		LIMIT 500
	`, parkID, startDateStr, endDateStr)

	if err == nil {
		defer featureRows.Close()
		for featureRows.Next() {
			var geojsonID int64
			var featureID, startD, endD string
			var propsJSON sql.NullString
			var minX, minY, maxX, maxY float64

			if featureRows.Scan(&geojsonID, &featureID, &startD, &endD, &propsJSON, &minX, &minY, &maxX, &maxY) != nil {
				continue
			}

			feature := FireFeature{
				ID:        featureID,
				GeoJSONID: geojsonID,
				OriginLat: maxY, // Approximate from bbox
				OriginLon: minX,
				DestLat:   minY,
				DestLon:   maxX,
			}

			// Parse properties JSON
			if propsJSON.Valid {
				var props map[string]interface{}
				if json.Unmarshal([]byte(propsJSON.String), &props) == nil {
					if v, ok := props["year"].(float64); ok {
						feature.Year = int(v)
					}
					if v, ok := props["group_num"].(float64); ok {
						feature.GroupNum = int(v)
					}
					if v, ok := props["outcome"].(string); ok {
						feature.Outcome = v
					}
					if v, ok := props["fires_inside"].(float64); ok {
						feature.FiresInside = int(v)
					}
					if v, ok := props["days_inside"].(float64); ok {
						feature.DaysInside = int(v)
					}
					if v, ok := props["entry_date"].(string); ok {
						feature.EntryDate = v
					}
					if v, ok := props["last_inside"].(string); ok {
						feature.LastInside = v
					}
				}
			}

			response.Features = append(response.Features, feature)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// handleSettlementNarrativeStats returns stats-only format for settlement narrative
func (s *Server) handleSettlementNarrativeStats(w http.ResponseWriter, parkID, parkName string) {
	response := SettlementNarrativeStats{
		ParkID:   parkID,
		ParkName: parkName,
		Stats: SettlementStatsData{
			ByClassification: make(map[string]int),
		},
		Features: []SettlementFeature{},
	}

	// Get aggregate stats
	var count int
	var totalArea, avgArea sql.NullFloat64
	var popEst, pop2030 sql.NullInt64
	var avgDistRoad sql.NullFloat64

	err := s.DB.QueryRow(`
		SELECT 
			COUNT(*),
			COALESCE(SUM(`+settlementExtentSQL("")+`), 0) / 1000000.0,
			COALESCE(AVG(`+settlementExtentSQL("")+`), 0),
			COALESCE(SUM(`+settlementPopulationSQL("")+`), 0),
			COALESCE(SUM(population_2030), 0),
			AVG(distance_to_road_m)
		FROM park_settlements
		WHERE park_id = ?`+settlementFilterSQL("narrative", "polygon_ids")+`
	`, parkID).Scan(&count, &totalArea, &avgArea, &popEst, &pop2030, &avgDistRoad)

	if err != nil && err != sql.ErrNoRows {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	response.Stats.SettlementCount = count
	if totalArea.Valid {
		response.Stats.TotalAreaKm2 = totalArea.Float64
	}
	if avgArea.Valid {
		response.Stats.AvgAreaM2 = avgArea.Float64
	}
	if popEst.Valid {
		response.Stats.PopulationEst = popEst.Int64
	}
	if pop2030.Valid {
		response.Stats.Population2030 = pop2030.Int64
	}
	if avgDistRoad.Valid {
		response.Stats.AvgDistToRoadM = avgDistRoad.Float64
	}

	// Get classification breakdown
	classRows, err := s.DB.Query(`
		SELECT COALESCE(classification, 'unclassified'), COUNT(*)
		FROM park_settlements
		WHERE park_id = ?`+settlementFilterSQL("narrative", "polygon_ids")+`
		GROUP BY classification
	`, parkID)
	if err == nil {
		defer classRows.Close()
		for classRows.Next() {
			var class string
			var cnt int
			if classRows.Scan(&class, &cnt) == nil {
				// mining is retired (§10); fold into unclassified
				response.Stats.ByClassification[publicSettlementClass(class)] += cnt
			}
		}
	}

	// Get settlement features with geojson_id
	featureRows, err := s.DB.Query(`
		SELECT 
			s.id, fg.id as geojson_id,
			COALESCE(s.classification, 'unclassified'),
			COALESCE(`+settlementExtentSQL("s")+`, 0),
			COALESCE(`+settlementPopulationSQL("s")+`, 0),
			COALESCE(s.population_2030, 0),
			s.lat, s.lon,
			s.nearest_place,
			s.distance_to_road_m
		FROM park_settlements s
		LEFT JOIN feature_geometries fg ON fg.feature_id = 'settlement_' || s.id AND fg.feature_type = 'settlement'
		WHERE s.park_id = ?`+settlementFilterSQL("s.narrative", "s.polygon_ids")+`
		ORDER BY `+settlementExtentSQL("s")+` DESC
		LIMIT 500
	`, parkID)

	if err == nil {
		defer featureRows.Close()
		for featureRows.Next() {
			var id int64
			var geojsonID sql.NullInt64
			var class string
			var area float64
			var popEst, pop2030 int64
			var lat, lon float64
			var nearestPlace sql.NullString
			var distRoad sql.NullFloat64

			if featureRows.Scan(&id, &geojsonID, &class, &area, &popEst, &pop2030, &lat, &lon, &nearestPlace, &distRoad) != nil {
				continue
			}

			feature := SettlementFeature{
				ID:             fmt.Sprintf("settlement_%d", id),
				Classification: publicSettlementClass(class),
				AreaM2:         area,
				PopulationEst:  popEst,
				Population2030: pop2030,
				Lat:            lat,
				Lon:            lon,
			}

			if geojsonID.Valid {
				feature.GeoJSONID = geojsonID.Int64
			}
			if nearestPlace.Valid {
				feature.NearestPlace = nearestPlace.String
			}
			if distRoad.Valid {
				feature.DistToRoadM = distRoad.Float64
			}

			response.Features = append(response.Features, feature)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// handleDeforestationNarrativeStats returns stats-only format for deforestation narrative
func (s *Server) handleDeforestationNarrativeStats(w http.ResponseWriter, parkID, parkName string, startYear, endYear int) {
	response := DeforestationNarrativeStats{
		ParkID:   parkID,
		ParkName: parkName,
		Stats: DeforestationStatsData{
			ByClassification: make(map[string]int),
			ByYear:           make(map[int]float64),
		},
		Features: []DeforestationFeature{},
	}

	// Set year range string
	if startYear == endYear {
		response.Stats.YearRange = fmt.Sprintf("%d", startYear)
	} else {
		response.Stats.YearRange = fmt.Sprintf("%d-%d", startYear, endYear)
	}

	// Get aggregate stats from deforestation_events
	var totalEvents int
	var totalArea float64
	var worstYear sql.NullInt64
	var worstYearArea sql.NullFloat64

	err := s.DB.QueryRow(`
		SELECT 
			COUNT(*),
			COALESCE(SUM(area_km2), 0)
		FROM deforestation_events
		WHERE park_id = ? AND year >= ? AND year <= ?
	`, parkID, startYear, endYear).Scan(&totalEvents, &totalArea)

	if err != nil && err != sql.ErrNoRows {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	response.Stats.TotalEvents = totalEvents
	response.Stats.TotalAreaKm2 = totalArea

	// Get worst year
	s.DB.QueryRow(`
		SELECT year, area_km2 FROM deforestation_events
		WHERE park_id = ? AND year >= ? AND year <= ?
		ORDER BY area_km2 DESC LIMIT 1
	`, parkID, startYear, endYear).Scan(&worstYear, &worstYearArea)

	if worstYear.Valid {
		response.Stats.WorstYear = int(worstYear.Int64)
	}
	if worstYearArea.Valid {
		response.Stats.WorstYearAreaKm2 = worstYearArea.Float64
	}

	// Calculate trend (compare first half vs second half)
	midYear := (startYear + endYear) / 2
	var firstHalfArea, secondHalfArea float64
	s.DB.QueryRow(`SELECT COALESCE(SUM(area_km2), 0) FROM deforestation_events 
		WHERE park_id = ? AND year >= ? AND year <= ?`, parkID, startYear, midYear).Scan(&firstHalfArea)
	s.DB.QueryRow(`SELECT COALESCE(SUM(area_km2), 0) FROM deforestation_events 
		WHERE park_id = ? AND year > ? AND year <= ?`, parkID, midYear, endYear).Scan(&secondHalfArea)

	if firstHalfArea > 0 {
		change := (secondHalfArea - firstHalfArea) / firstHalfArea * 100
		if change > 10 {
			response.Stats.TrendDirection = "worsening"
		} else if change < -10 {
			response.Stats.TrendDirection = "improving"
		} else {
			response.Stats.TrendDirection = "stable"
		}
	} else {
		response.Stats.TrendDirection = "insufficient_data"
	}

	// Get area by year
	yearRows, err := s.DB.Query(`
		SELECT year, area_km2 FROM deforestation_events
		WHERE park_id = ? AND year >= ? AND year <= ?
		ORDER BY year
	`, parkID, startYear, endYear)
	if err == nil {
		defer yearRows.Close()
		for yearRows.Next() {
			var year int
			var area float64
			if yearRows.Scan(&year, &area) == nil {
				response.Stats.ByYear[year] = area
			}
		}
	}

	// Get classification breakdown from clusters
	classRows, err := s.DB.Query(`
		SELECT COALESCE(classification, 'unclassified'), COUNT(*)
		FROM deforestation_clusters
		WHERE park_id = ? AND year >= ? AND year <= ?
		GROUP BY classification
	`, parkID, startYear, endYear)
	if err == nil {
		defer classRows.Close()
		for classRows.Next() {
			var class string
			var cnt int
			if classRows.Scan(&class, &cnt) == nil {
				response.Stats.ByClassification[class] = cnt
			}
		}
	}

	// Get deforestation features with geojson_id - query from deforestation_events for proper lat/lon
	featureRows, err := s.DB.Query(`
		SELECT DISTINCT
			fg.id, fg.feature_id, de.year, de.area_km2, de.pattern_type,
			de.lat, de.lon,
			COALESCE(de.pattern_type, 'unclassified'),
			0.0,
			0.0
		FROM deforestation_events de
		JOIN feature_geometries fg ON fg.feature_id = 'deforestation_' || de.id
		WHERE de.park_id = ? 
		  AND de.year >= ? AND de.year <= ?
		ORDER BY de.year DESC
		LIMIT 500
	`, parkID, startYear, endYear)

	if err == nil {
		defer featureRows.Close()
		for featureRows.Next() {
			var geojsonID int64
			var featureID string
			var year int
			var areaKm2, lat, lon float64
			var patternType sql.NullString
			var class string
			var distRoad, distSettle sql.NullFloat64

			if featureRows.Scan(&geojsonID, &featureID, &year, &areaKm2, &patternType, &lat, &lon, &class, &distRoad, &distSettle) != nil {
				continue
			}

			feature := DeforestationFeature{
				ID:             featureID,
				GeoJSONID:      geojsonID,
				Year:           year,
				AreaKm2:        areaKm2,
				Lat:            lat,
				Lon:            lon,
				Classification: class,
			}

			if patternType.Valid {
				feature.PatternType = patternType.String
			}
			if distRoad.Valid {
				feature.DistanceToRoadM = distRoad.Float64
			}
			if distSettle.Valid {
				feature.DistanceToSettleM = distSettle.Float64
			}

			response.Features = append(response.Features, feature)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// HydroRiver represents a river from the HydroRIVERS dataset
type HydroRiver struct {
	Name         string  `json:"name"`
	LengthKm     float64 `json:"length_km"`
	DischargeCMS float64 `json:"discharge_cms"`
	StreamOrder  int     `json:"stream_order"`
	DistanceKm   float64 `json:"distance_km"`
	CentroidLat  float64 `json:"centroid_lat"`
	CentroidLon  float64 `json:"centroid_lon"`
}

// findNearestHydroRivers finds the nearest rivers from HydroRIVERS dataset
func (s *Server) findNearestHydroRivers(parkID string, lat, lon float64, limit int) ([]HydroRiver, error) {
	// park_rivers_hydro is the canonical rivers table (the old park_rivers/
	// rivers tables no longer exist). No discharge column; stream order is
	// the best size proxy.
	rows, err := s.DB.Query(`
		SELECT COALESCE(name,''), COALESCE(length_km,0), 0, COALESCE(stream_order,0),
		       0, COALESCE(lat,0), COALESCE(lon,0)
		FROM park_rivers_hydro
		WHERE park_id = ? AND name != '' AND name IS NOT NULL
		ORDER BY stream_order DESC, length_km DESC
		LIMIT ?
	`, parkID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var rivers []HydroRiver
	for rows.Next() {
		var r HydroRiver
		var distKm sql.NullFloat64
		err := rows.Scan(&r.Name, &r.LengthKm, &r.DischargeCMS, &r.StreamOrder,
			&distKm, &r.CentroidLat, &r.CentroidLon)
		if err != nil {
			continue
		}
		if distKm.Valid {
			r.DistanceKm = distKm.Float64
		}
		rivers = append(rivers, r)
	}

	return rivers, nil
}

// findNearestRiverToPoint finds rivers closest to a specific point
func (s *Server) findNearestRiverToPoint(parkID string, lat, lon float64, limit int) ([]HydroRiver, error) {
	// Get named park rivers (park_rivers_hydro; segment lat/lon = midpoint)
	// and calculate distance to point. Pre-filter by bbox in SQL so parks
	// with thousands of reach segments stay cheap.
	rows, err := s.DB.Query(`
		SELECT COALESCE(name,''), COALESCE(length_km,0), 0, COALESCE(stream_order,0),
		       COALESCE(lat,0), COALESCE(lon,0)
		FROM park_rivers_hydro
		WHERE park_id = ? AND name != '' AND name IS NOT NULL
		  AND lat BETWEEN ? - 1.0 AND ? + 1.0
		  AND lon BETWEEN ? - 1.0 AND ? + 1.0
		ORDER BY stream_order DESC
		LIMIT 200
	`, parkID, lat, lat, lon, lon)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	type riverWithDist struct {
		river HydroRiver
		dist  float64
	}
	var candidates []riverWithDist

	for rows.Next() {
		var r HydroRiver
		err := rows.Scan(&r.Name, &r.LengthKm, &r.DischargeCMS, &r.StreamOrder,
			&r.CentroidLat, &r.CentroidLon)
		if err != nil {
			continue
		}
		// Calculate approximate distance
		dist := haversineDistance(lat, lon, r.CentroidLat, r.CentroidLon)
		r.DistanceKm = dist
		candidates = append(candidates, riverWithDist{r, dist})
	}

	// Sort by distance
	sort.Slice(candidates, func(i, j int) bool {
		return candidates[i].dist < candidates[j].dist
	})

	// Return top N
	var result []HydroRiver
	for i := 0; i < limit && i < len(candidates); i++ {
		result = append(result, candidates[i].river)
	}

	return result, nil
}

// getRiverDescription returns a human-readable description of a river
func getRiverDescription(r HydroRiver) string {
	var desc string
	if r.DischargeCMS > 1000 {
		desc = "major river"
	} else if r.DischargeCMS > 100 {
		desc = "significant river"
	} else if r.StreamOrder >= 5 {
		desc = "river"
	} else {
		desc = "stream"
	}
	return desc
}

// countNearbyRoads counts roads within a buffer of a point
func (s *Server) countNearbyRoads(parkID string, lat, lon float64, bufferKm float64) int {
	bufferDeg := bufferKm / 111.0 // Approximate degrees

	var count int
	s.DB.QueryRow(`
		SELECT COUNT(*) FROM feature_geometries
		WHERE park_id = ? AND feature_type = 'road_heigit'
		AND json_extract(geojson, '$.coordinates[0][0]') BETWEEN ? AND ?
		AND json_extract(geojson, '$.coordinates[0][1]') BETWEEN ? AND ?
	`, parkID, lon-bufferDeg, lon+bufferDeg, lat-bufferDeg, lat+bufferDeg).Scan(&count)

	return count
}

// isNearRoad checks if a point is near any road
func (s *Server) isNearRoad(parkID string, lat, lon float64, maxDistKm float64) bool {
	return s.countNearbyRoads(parkID, lat, lon, maxDistKm) > 0
}
