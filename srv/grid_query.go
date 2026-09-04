package srv

import (
	"context"
	"database/sql"
	"fmt"
	"math"
	"strings"
	"time"
)

// GridQueryParams holds all filter parameters for grid data queries.
type GridQueryParams struct {
	FromYear      int64
	ToYear        int64
	FromMonth     int64       // 0 = no month filter on start
	ToMonth       int64       // 0 = no month filter on end
	FromDay       int64       // 0 = no day filter on start
	ToDay         int64       // 0 = no day filter on end
	Month         *int64      // Optional: filter by specific month
	MovementTypes []string    // Optional: filter by movement types (foot, vehicle, boat, fixed_wing, rotor_wing)
	BBox          *[4]float64 // Optional: [minLng, minLat, maxLng, maxLat]
	Envs          string      // JSON array of patrol envs (PatrolEnvsJSON); empty = no filter
}

// GridRow represents a row from the grid query result.
type GridRow struct {
	GridCellID         string
	LatCenter          float64
	LonCenter          float64
	TotalDistanceKm    float64
	TotalPoints        int64
	UniqueUploads      int64
	CoveragePercent    *float64
	DryMonths          int64
	RainyMonths        int64
	VisitDays          int64    // distinct days with effort within the window
	LastVisitDay       int64    // YYYYMMDD code of most recent effort day (0 if unknown)
	SubcellCount       int64    // distinct subcells visited (from subcell_visits, 0-100)
	FootKm             float64  // distance by movement type
	VehicleKm          float64  // ground vehicles (excludes boat)
	AircraftKm         float64  // all aircraft (sum of fixed+rotor+unclassified)
	BoatKm             float64  // boat subtype of vehicle
	FixedWingKm        float64  // fixed-wing subtype of aircraft
	RotorWingKm        float64  // rotor-wing (helicopter) subtype of aircraft
	AvgSpeedKmh        *float64 // distance-weighted avg speed (all types combined)
	AvgAltitudeM       *float64 // distance-weighted avg altitude (aircraft cells)
	FootSpeedKmh       *float64 // per-type distance-weighted avg speed
	VehicleSpeedKmh    *float64
	AircraftSpeedKmh   *float64
	BoatSpeedKmh       *float64
	FixedWingSpeedKmh  *float64
	RotorWingSpeedKmh  *float64
	FootAltitudeM      *float64 // per-type distance-weighted avg altitude
	VehicleAltitudeM   *float64
	AircraftAltitudeM  *float64
	BoatAltitudeM      *float64
	FixedWingAltitudeM *float64
	RotorWingAltitudeM *float64
}

// QueryGridData executes a flexible query for grid data with optional filters.
func (s *Server) QueryGridData(ctx context.Context, params GridQueryParams) ([]GridRow, error) {
	var args []interface{}
	var conditions []string

	// Base query — aggregate across all day-level records.
	// Day-level effort_data records have day IS NOT NULL;
	// legacy month-level records have day IS NULL.
	// We accept both so old data still works.
	query := `
		SELECT 
			g.id as grid_cell_id,
			g.lat_center,
			g.lon_center,
			COALESCE(SUM(e.total_distance_km), 0) as total_distance_km,
			COALESCE(SUM(e.total_points), 0) as total_points,
			COALESCE(MAX(e.unique_uploads), 0) as unique_uploads,
			MAX(e.coverage_percent) as coverage_percent,
			COUNT(DISTINCT CASE WHEN e.month IN (11, 12, 1, 2, 3, 4) THEN e.year * 100 + e.month END) as dry_months,
			COUNT(DISTINCT CASE WHEN e.month IN (5, 6, 7, 8, 9, 10) THEN e.year * 100 + e.month END) as rainy_months,
			COUNT(DISTINCT CASE WHEN e.day IS NOT NULL THEN e.year * 10000 + e.month * 100 + e.day END) as visit_days,
			COALESCE(MAX(CASE WHEN e.day IS NOT NULL THEN e.year * 10000 + e.month * 100 + e.day END), 0) as last_visit_day,
			CASE WHEN SUM(CASE WHEN e.avg_speed_kmh IS NOT NULL THEN e.total_distance_km ELSE 0 END) > 0
				THEN SUM(CASE WHEN e.avg_speed_kmh IS NOT NULL THEN e.avg_speed_kmh * e.total_distance_km ELSE 0 END)
				   / SUM(CASE WHEN e.avg_speed_kmh IS NOT NULL THEN e.total_distance_km ELSE 0 END)
				ELSE NULL END as avg_speed_kmh,
			CASE WHEN SUM(CASE WHEN e.avg_altitude_m IS NOT NULL THEN e.total_distance_km ELSE 0 END) > 0
				THEN SUM(CASE WHEN e.avg_altitude_m IS NOT NULL THEN e.avg_altitude_m * e.total_distance_km ELSE 0 END)
				   / SUM(CASE WHEN e.avg_altitude_m IS NOT NULL THEN e.total_distance_km ELSE 0 END)
				ELSE NULL END as avg_altitude_m
		FROM grid_cells g
		JOIN effort_data e ON e.grid_cell_id = g.id
		WHERE 1=1
	`

	// Date range filter with day precision when available
	if params.FromDay > 0 && params.ToDay > 0 && params.FromMonth > 0 && params.ToMonth > 0 {
		// Day-level records match by exact day range.
		// Legacy month-level records (day IS NULL) match if any part of
		// their month overlaps the requested range.
		conditions = append(conditions,
			"((e.day IS NOT NULL AND (e.year * 10000 + e.month * 100 + e.day) BETWEEN ? AND ?) OR "+
				" (e.day IS NULL AND (e.year * 100 + e.month) BETWEEN ? AND ?))")
		args = append(args,
			params.FromYear*10000+params.FromMonth*100+params.FromDay,
			params.ToYear*10000+params.ToMonth*100+params.ToDay,
			params.FromYear*100+params.FromMonth,
			params.ToYear*100+params.ToMonth)
	} else if params.FromMonth > 0 && params.ToMonth > 0 {
		// Month-level filtering
		conditions = append(conditions, "(e.year * 100 + e.month) BETWEEN ? AND ?")
		args = append(args, params.FromYear*100+params.FromMonth, params.ToYear*100+params.ToMonth)
	} else {
		conditions = append(conditions, "e.year BETWEEN ? AND ?")
		args = append(args, params.FromYear, params.ToYear)
	}

	// Month filter (single month override)
	if params.Month != nil {
		conditions = append(conditions, "e.month = ?")
		args = append(args, *params.Month)
	}

	// Movement type filter
	if len(params.MovementTypes) > 0 && len(params.MovementTypes) < 5 {
		// Filter by specific movement types (DB-native subtypes).
		// Expand "aircraft" to fixed_wing + rotor_wing for backward compat.
		expanded := make(map[string]bool)
		for _, t := range params.MovementTypes {
			switch t {
			case "aircraft":
				expanded["fixed_wing"] = true
				expanded["rotor_wing"] = true
			default:
				expanded[t] = true
			}
		}
		placeholders := make([]string, 0, len(expanded))
		for t := range expanded {
			placeholders = append(placeholders, "?")
			args = append(args, t)
		}
		conditions = append(conditions, fmt.Sprintf("e.movement_type IN (%s)", strings.Join(placeholders, ",")))
	} else {
		// Use the pre-aggregated 'all' type for efficiency
		conditions = append(conditions, "e.movement_type = 'all'")
	}

	// Env (tenant) filter
	if params.Envs != "" {
		conditions = append(conditions, PatrolEnvsSQL("e.env"))
		args = append(args, params.Envs)
	}

	// Bounding box filter
	if params.BBox != nil {
		conditions = append(conditions, "g.lat_center >= ? AND g.lat_center <= ?")
		args = append(args, params.BBox[1], params.BBox[3]) // minLat, maxLat
		conditions = append(conditions, "g.lon_center >= ? AND g.lon_center <= ?")
		args = append(args, params.BBox[0], params.BBox[2]) // minLng, maxLng
	}

	// Build final query
	if len(conditions) > 0 {
		query += " AND " + strings.Join(conditions, " AND ")
	}
	query += " GROUP BY g.id, g.lat_center, g.lon_center"

	// Execute query
	rows, err := s.DB.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("query grid data: %w", err)
	}
	defer rows.Close()

	var results []GridRow
	for rows.Next() {
		var row GridRow
		var coveragePercent sql.NullFloat64

		var avgSpeed, avgAlt sql.NullFloat64
		if err := rows.Scan(
			&row.GridCellID,
			&row.LatCenter,
			&row.LonCenter,
			&row.TotalDistanceKm,
			&row.TotalPoints,
			&row.UniqueUploads,
			&coveragePercent,
			&row.DryMonths,
			&row.RainyMonths,
			&row.VisitDays,
			&row.LastVisitDay,
			&avgSpeed,
			&avgAlt,
		); err != nil {
			return nil, fmt.Errorf("scan grid row: %w", err)
		}

		if coveragePercent.Valid {
			row.CoveragePercent = &coveragePercent.Float64
		}
		if avgSpeed.Valid {
			row.AvgSpeedKmh = &avgSpeed.Float64
		}
		if avgAlt.Valid {
			row.AvgAltitudeM = &avgAlt.Float64
		}

		results = append(results, row)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate grid rows: %w", err)
	}

	// Enrich with subcell coverage from subcell_visits table.
	// We batch this as a second query to avoid complicating the main JOIN.
	if len(results) > 0 {
		if err := s.enrichSubcellCoverage(ctx, params, results); err != nil {
			// Non-fatal: subcell data is supplementary
			for i := range results {
				results[i].SubcellCount = 0
			}
		}
	}

	// Enrich with per-movement-type distance breakdown.
	if len(results) > 0 {
		if err := s.enrichMovementTypes(ctx, params, results); err != nil {
			// Non-fatal
		}
	}

	return results, nil
}

// enrichSubcellCoverage looks up distinct subcell counts for each grid cell
// within the requested date range.
func (s *Server) enrichSubcellCoverage(ctx context.Context, params GridQueryParams, rows []GridRow) error {
	// Build cell ID list
	idSet := make(map[string]int, len(rows)) // cellID -> index in rows
	for i, r := range rows {
		idSet[r.GridCellID] = i
	}

	// Build date conditions for subcell_visits.
	// visit_date may be stored as "YYYY-MM-DD" or Go's "YYYY-MM-DD 00:00:00 +0000 UTC".
	// Use >= fromDate AND < dayAfterTo to handle both formats correctly.
	var dateCondition string
	var dateArgs []interface{}
	if params.FromDay > 0 && params.ToDay > 0 && params.FromMonth > 0 && params.ToMonth > 0 {
		fromDate := fmt.Sprintf("%04d-%02d-%02d", params.FromYear, params.FromMonth, params.FromDay)
		toTime := time.Date(int(params.ToYear), time.Month(params.ToMonth), int(params.ToDay), 0, 0, 0, 0, time.UTC)
		dayAfterTo := toTime.AddDate(0, 0, 1).Format("2006-01-02")
		dateCondition = "AND sv.visit_date >= ? AND sv.visit_date < ?"
		dateArgs = append(dateArgs, fromDate, dayAfterTo)
	}
	if params.Envs != "" {
		dateCondition += " AND " + PatrolEnvsSQL("sv.env")
		dateArgs = append(dateArgs, params.Envs)
	}

	// Query in batches of ~500 to avoid huge IN clauses
	cellIDs := make([]string, 0, len(idSet))
	for id := range idSet {
		cellIDs = append(cellIDs, id)
	}

	const batchSize = 500
	for start := 0; start < len(cellIDs); start += batchSize {
		end := start + batchSize
		if end > len(cellIDs) {
			end = len(cellIDs)
		}
		batch := cellIDs[start:end]

		placeholders := make([]string, len(batch))
		qargs := make([]interface{}, 0, len(batch)+len(dateArgs))
		for i, id := range batch {
			placeholders[i] = "?"
			qargs = append(qargs, id)
		}
		qargs = append(qargs, dateArgs...)

		q := fmt.Sprintf(`
			SELECT grid_cell_id, COUNT(DISTINCT subcell_id)
			FROM subcell_visits sv
			WHERE sv.grid_cell_id IN (%s) %s
			GROUP BY sv.grid_cell_id
		`, strings.Join(placeholders, ","), dateCondition)

		srows, err := s.DB.QueryContext(ctx, q, qargs...)
		if err != nil {
			return fmt.Errorf("query subcell coverage: %w", err)
		}
		for srows.Next() {
			var cellID string
			var cnt int64
			if err := srows.Scan(&cellID, &cnt); err != nil {
				srows.Close()
				return err
			}
			if idx, ok := idSet[cellID]; ok {
				rows[idx].SubcellCount = cnt
			}
		}
		srows.Close()
	}

	return nil
}

// enrichMovementTypes looks up per-movement-type distance for each grid cell.
func (s *Server) enrichMovementTypes(ctx context.Context, params GridQueryParams, rows []GridRow) error {
	idSet := make(map[string]int, len(rows))
	for i, r := range rows {
		idSet[r.GridCellID] = i
	}

	cellIDs := make([]string, 0, len(idSet))
	for id := range idSet {
		cellIDs = append(cellIDs, id)
	}

	// Build date condition
	var dateCondition string
	var dateArgs []interface{}
	if params.FromDay > 0 && params.ToDay > 0 && params.FromMonth > 0 && params.ToMonth > 0 {
		dateCondition = "AND ((e.day IS NOT NULL AND (e.year * 10000 + e.month * 100 + e.day) BETWEEN ? AND ?) OR (e.day IS NULL AND (e.year * 100 + e.month) BETWEEN ? AND ?))"
		dateArgs = append(dateArgs,
			params.FromYear*10000+params.FromMonth*100+params.FromDay,
			params.ToYear*10000+params.ToMonth*100+params.ToDay,
			params.FromYear*100+params.FromMonth,
			params.ToYear*100+params.ToMonth)
	} else if params.FromMonth > 0 && params.ToMonth > 0 {
		dateCondition = "AND (e.year * 100 + e.month) BETWEEN ? AND ?"
		dateArgs = append(dateArgs, params.FromYear*100+params.FromMonth, params.ToYear*100+params.ToMonth)
	} else {
		dateCondition = "AND e.year BETWEEN ? AND ?"
		dateArgs = append(dateArgs, params.FromYear, params.ToYear)
	}
	if params.Envs != "" {
		dateCondition += " AND " + PatrolEnvsSQL("e.env")
		dateArgs = append(dateArgs, params.Envs)
	}

	const batchSize = 500
	for start := 0; start < len(cellIDs); start += batchSize {
		end := start + batchSize
		if end > len(cellIDs) {
			end = len(cellIDs)
		}
		batch := cellIDs[start:end]

		placeholders := make([]string, len(batch))
		qargs := make([]interface{}, 0, len(batch)+len(dateArgs))
		for i, id := range batch {
			placeholders[i] = "?"
			qargs = append(qargs, id)
		}
		qargs = append(qargs, dateArgs...)

		q := fmt.Sprintf(`
			SELECT e.grid_cell_id, e.movement_type,
			       COALESCE(SUM(e.total_distance_km), 0),
			       CASE WHEN SUM(CASE WHEN e.avg_speed_kmh IS NOT NULL THEN e.total_distance_km ELSE 0 END) > 0
			            THEN SUM(CASE WHEN e.avg_speed_kmh IS NOT NULL THEN e.avg_speed_kmh * e.total_distance_km ELSE 0 END)
			               / SUM(CASE WHEN e.avg_speed_kmh IS NOT NULL THEN e.total_distance_km ELSE 0 END)
			            ELSE NULL END,
			       CASE WHEN SUM(CASE WHEN e.avg_altitude_m IS NOT NULL THEN e.total_distance_km ELSE 0 END) > 0
			            THEN SUM(CASE WHEN e.avg_altitude_m IS NOT NULL THEN e.avg_altitude_m * e.total_distance_km ELSE 0 END)
			               / SUM(CASE WHEN e.avg_altitude_m IS NOT NULL THEN e.total_distance_km ELSE 0 END)
			            ELSE NULL END
			FROM effort_data e
			WHERE e.grid_cell_id IN (%s)
			  AND e.movement_type IN ('foot','vehicle','aircraft','boat','fixed_wing','rotor_wing')
			  %s
			GROUP BY e.grid_cell_id, e.movement_type
		`, strings.Join(placeholders, ","), dateCondition)

		srows, err := s.DB.QueryContext(ctx, q, qargs...)
		if err != nil {
			return fmt.Errorf("query movement types: %w", err)
		}
		for srows.Next() {
			var cellID, mtype string
			var km float64
			var avgSpeed sql.NullFloat64
			var avgAlt sql.NullFloat64
			if err := srows.Scan(&cellID, &mtype, &km, &avgSpeed, &avgAlt); err != nil {
				srows.Close()
				return err
			}
			if idx, ok := idSet[cellID]; ok {
				switch mtype {
				case "foot":
					rows[idx].FootKm += km
					if avgSpeed.Valid {
						rows[idx].FootSpeedKmh = &avgSpeed.Float64
					}
					if avgAlt.Valid {
						rows[idx].FootAltitudeM = &avgAlt.Float64
					}
				case "vehicle":
					rows[idx].VehicleKm += km
					if avgSpeed.Valid {
						rows[idx].VehicleSpeedKmh = &avgSpeed.Float64
					}
					if avgAlt.Valid {
						rows[idx].VehicleAltitudeM = &avgAlt.Float64
					}
				case "aircraft":
					rows[idx].AircraftKm += km
					if avgSpeed.Valid {
						rows[idx].AircraftSpeedKmh = &avgSpeed.Float64
					}
					if avgAlt.Valid {
						rows[idx].AircraftAltitudeM = &avgAlt.Float64
					}
				case "boat":
					rows[idx].BoatKm += km
					rows[idx].VehicleKm += km // boat counts toward vehicle total
					if avgSpeed.Valid {
						rows[idx].BoatSpeedKmh = &avgSpeed.Float64
					}
					if avgAlt.Valid {
						rows[idx].BoatAltitudeM = &avgAlt.Float64
					}
				case "fixed_wing":
					rows[idx].FixedWingKm += km
					rows[idx].AircraftKm += km
					if avgSpeed.Valid {
						rows[idx].FixedWingSpeedKmh = &avgSpeed.Float64
						rows[idx].AircraftSpeedKmh = &avgSpeed.Float64
					}
					if avgAlt.Valid {
						rows[idx].FixedWingAltitudeM = &avgAlt.Float64
						rows[idx].AircraftAltitudeM = &avgAlt.Float64
					}
				case "rotor_wing":
					rows[idx].RotorWingKm += km
					rows[idx].AircraftKm += km
					if avgSpeed.Valid {
						rows[idx].RotorWingSpeedKmh = &avgSpeed.Float64
						rows[idx].AircraftSpeedKmh = &avgSpeed.Float64
					}
					if avgAlt.Valid {
						rows[idx].RotorWingAltitudeM = &avgAlt.Float64
						rows[idx].AircraftAltitudeM = &avgAlt.Float64
					}
				}
			}
		}
		srows.Close()
	}

	return nil
}

// computeWindowSpanDays returns the number of days in the selected time window.
// Returns 365 when no day-level filtering is applied (full-year view).
func computeWindowSpanDays(params GridQueryParams) int {
	if params.FromDay > 0 && params.ToDay > 0 && params.FromMonth > 0 && params.ToMonth > 0 {
		from := time.Date(int(params.FromYear), time.Month(params.FromMonth), int(params.FromDay), 0, 0, 0, 0, time.UTC)
		to := time.Date(int(params.ToYear), time.Month(params.ToMonth), int(params.ToDay), 0, 0, 0, 0, time.UTC)
		days := int(to.Sub(from).Hours()/24) + 1
		if days < 1 {
			days = 1
		}
		return days
	}
	// Full-year or multi-year
	years := int(params.ToYear-params.FromYear) + 1
	if years < 1 {
		years = 1
	}
	return years * 365
}

// computeRecency returns a 0-1 value: 1.0 = last visit is at the end of the window,
// 0.0 = last visit is at the start. Decays as a square root curve so recent visits
// stay bright longer.
func computeRecency(lastVisitDay int64, params GridQueryParams) float64 {
	if lastVisitDay == 0 {
		return 0
	}
	// Parse last visit date
	lvYear := lastVisitDay / 10000
	lvMonth := (lastVisitDay % 10000) / 100
	lvDay := lastVisitDay % 100
	lastDate := time.Date(int(lvYear), time.Month(lvMonth), int(lvDay), 0, 0, 0, 0, time.UTC)

	// Window end date
	var endDate time.Time
	if params.ToDay > 0 && params.ToMonth > 0 {
		endDate = time.Date(int(params.ToYear), time.Month(params.ToMonth), int(params.ToDay), 0, 0, 0, 0, time.UTC)
	} else {
		endDate = time.Now().UTC().Truncate(24 * time.Hour)
	}

	windowDays := float64(computeWindowSpanDays(params))
	if windowDays < 1 {
		windowDays = 1
	}

	// Cap the effective decay window at 120 days so recency always
	// produces meaningful visual differentiation. Without this cap,
	// a multi-year window (e.g. 1100 days) makes all recent visits
	// look identical (recency ~0.97-1.0).
	const maxDecayDays = 120.0
	if windowDays > maxDecayDays {
		windowDays = maxDecayDays
	}

	daysSinceLast := endDate.Sub(lastDate).Hours() / 24
	if daysSinceLast < 0 {
		daysSinceLast = 0
	}

	// Fraction of window remaining since last visit
	frac := 1.0 - (daysSinceLast / windowDays)
	if frac < 0 {
		frac = 0
	}
	// Square-root curve: recent visits stay bright, old ones fade
	return math.Sqrt(frac)
}
