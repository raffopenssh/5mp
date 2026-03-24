package srv

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
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
	MovementTypes []string    // Optional: filter by movement types (foot, vehicle, aircraft)
	BBox          *[4]float64 // Optional: [minLng, minLat, maxLng, maxLat]
}

// GridRow represents a row from the grid query result.
type GridRow struct {
	GridCellID      string
	LatCenter       float64
	LonCenter       float64
	TotalDistanceKm float64
	TotalPoints     int64
	UniqueUploads   int64
	CoveragePercent *float64
	DryMonths       int64
	RainyMonths     int64
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
			COUNT(DISTINCT CASE WHEN e.month IN (5, 6, 7, 8, 9, 10) THEN e.year * 100 + e.month END) as rainy_months
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
	if len(params.MovementTypes) > 0 && len(params.MovementTypes) < 3 {
		// Filter by specific movement types (aggregate them)
		placeholders := make([]string, len(params.MovementTypes))
		for i, t := range params.MovementTypes {
			placeholders[i] = "?"
			args = append(args, t)
		}
		conditions = append(conditions, fmt.Sprintf("e.movement_type IN (%s)", strings.Join(placeholders, ",")))
	} else {
		// Use the pre-aggregated 'all' type for efficiency
		conditions = append(conditions, "e.movement_type = 'all'")
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
		); err != nil {
			return nil, fmt.Errorf("scan grid row: %w", err)
		}

		if coveragePercent.Valid {
			row.CoveragePercent = &coveragePercent.Float64
		}

		results = append(results, row)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate grid rows: %w", err)
	}

	return results, nil
}
