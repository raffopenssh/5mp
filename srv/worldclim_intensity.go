package srv

import (
	"context"
	"fmt"
)

// QueryGridDataWithWorldClim queries grid data using WorldClim precipitation to classify dry/rainy months
// Falls back to park climate, then to defaults if WorldClim data unavailable
func (s *Server) QueryGridDataWithWorldClim(ctx context.Context, params GridQueryParams, parkID string) (map[string]float64, error) {
	// Query all grid cells in bbox
	query := `
		SELECT DISTINCT g.id, g.lat_center, g.lon_center
		FROM grid_cells g
		JOIN effort_data e ON e.grid_cell_id = g.id
		WHERE e.movement_type = 'all'
		  AND e.year BETWEEN ? AND ?
	`
	args := []interface{}{params.FromYear, params.ToYear}
	if params.Env != "" {
		query += " AND e.env = ?"
		args = append(args, params.Env)
	}
	
	if params.BBox != nil {
		query += ` AND g.lat_center BETWEEN ? AND ? AND g.lon_center BETWEEN ? AND ?`
		args = append(args, params.BBox[1], params.BBox[3], params.BBox[0], params.BBox[2])
	}
	
	rows, err := s.DB.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	
	intensityMap := make(map[string]float64)
	
	for rows.Next() {
		var gridCellID string
		var lat, lon float64
		
		if err := rows.Scan(&gridCellID, &lat, &lon); err != nil {
			continue
		}
		
		// Get dry/rainy months for this specific grid cell using WorldClim
		dryMonths, rainyMonths := s.getGridCellSeasons(gridCellID, parkID)
		
		// Build SQL to count visits in dry/rainy months
		dryMonthsSQL := buildMonthINClause(dryMonths)
		rainyMonthsSQL := buildMonthINClause(rainyMonths)
		
		visitQuery := fmt.Sprintf(`
			SELECT 
				COUNT(DISTINCT CASE WHEN e.month IN %s THEN e.year || '-' || e.month END) as dry_count,
				COUNT(DISTINCT CASE WHEN e.month IN %s THEN e.year || '-' || e.month END) as rainy_count
			FROM effort_data e
			WHERE e.grid_cell_id = ?
			  AND e.movement_type = 'all'
			  AND e.year BETWEEN ? AND ?
			  AND (? = '' OR e.env = ?)
		`, dryMonthsSQL, rainyMonthsSQL)
		
		var dryCount, rainyCount int64
		if err := s.DB.QueryRowContext(ctx, visitQuery, gridCellID, params.FromYear, params.ToYear, params.Env, params.Env).Scan(&dryCount, &rainyCount); err != nil {
			continue
		}
		
		// Calculate intensity using same formula as buildGridFeature
		var intensity float64
		if dryCount > 0 || rainyCount > 0 {
			actualWeight := float64(dryCount) + float64(rainyCount)*0.3
			expectedDryMonths := float64(len(dryMonths))
			if expectedDryMonths == 0 {
				expectedDryMonths = 6.0
			}
			intensity = actualWeight / expectedDryMonths
			if intensity > 1.5 {
				intensity = 1.5
			}
		}
		
		intensityMap[gridCellID] = intensity
	}
	
	return intensityMap, nil
}

// getGridCellSeasons determines dry/rainy months for a grid cell
// Priority: 1) WorldClim data, 2) Park climate, 3) Default
func (s *Server) getGridCellSeasons(gridCellID, parkID string) (dryMonths, rainyMonths []int) {
	// Try WorldClim data first (most accurate - per grid cell)
	if globalGridPrecip != nil {
		dry, rainy := globalGridPrecip.ClassifyDryRainyMonths(gridCellID)
		if len(dry) > 0 || len(rainy) > 0 {
			return dry, rainy
		}
	}
	
	// Fallback to park climate data
	var drySeason, rainySeason string
	if err := s.DB.QueryRow(`SELECT dry_season, rainy_season FROM park_climate WHERE park_id = ?`, parkID).Scan(&drySeason, &rainySeason); err == nil {
		dryMonths = parseSeasonMonths(drySeason)
		rainyMonths = parseSeasonMonths(rainySeason)
		if len(dryMonths) > 0 || len(rainyMonths) > 0 {
			return dryMonths, rainyMonths
		}
	}
	
	// Default fallback
	return []int{11, 12, 1, 2, 3, 4}, []int{5, 6, 7, 8, 9, 10}
}
