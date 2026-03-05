package srv

import (
	"encoding/json"
	"os"
	"sync"
)

// GridPrecipData holds monthly precipitation (mm) for each grid cell
type GridPrecipData struct {
	mu   sync.RWMutex
	data map[string][]float64 // grid_cell_id -> [12 monthly precip values]
}

var globalGridPrecip *GridPrecipData

// LoadWorldClimData loads grid cell precipitation data from JSON
func LoadWorldClimData(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}

	var precip map[string][]float64
	if err := json.Unmarshal(data, &precip); err != nil {
		return err
	}

	globalGridPrecip = &GridPrecipData{
		data: precip,
	}

	return nil
}

// GetMonthlyPrecip returns monthly precipitation for a grid cell
func (g *GridPrecipData) GetMonthlyPrecip(gridCellID string) []float64 {
	if g == nil {
		return nil
	}
	g.mu.RLock()
	defer g.mu.RUnlock()
	return g.data[gridCellID]
}

// ClassifyDryRainyMonths returns dry and rainy month numbers based on precipitation
// Uses threshold: < 50mm = dry, >= 50mm = rainy
// This accounts for actual local climate, not just latitude
func (g *GridPrecipData) ClassifyDryRainyMonths(gridCellID string) (dryMonths, rainyMonths []int) {
	precip := g.GetMonthlyPrecip(gridCellID)
	if precip == nil || len(precip) != 12 {
		// Fallback to default
		return []int{11, 12, 1, 2, 3, 4}, []int{5, 6, 7, 8, 9, 10}
	}

	// Threshold: 50mm/month
	// Below this, off-road vehicle and foot access is good
	// Above this, flooding/mud reduces accessibility
	threshold := 50.0

	for month := 1; month <= 12; month++ {
		monthlyPrecip := precip[month-1] // 0-indexed array
		if monthlyPrecip < threshold {
			dryMonths = append(dryMonths, month)
		} else {
			rainyMonths = append(rainyMonths, month)
		}
	}

	return dryMonths, rainyMonths
}
