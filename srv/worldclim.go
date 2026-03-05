package srv

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
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
// If not found in preloaded data, queries WorldClim on-demand
func (g *GridPrecipData) GetMonthlyPrecip(gridCellID string) []float64 {
	if g == nil {
		return nil
	}
	
	// Try preloaded data first
	g.mu.RLock()
	precip, exists := g.data[gridCellID]
	g.mu.RUnlock()
	
	if exists {
		return precip
	}
	
	// Not found - try on-demand query
	slog.Info("grid cell not in cache, querying WorldClim on-demand", "cell_id", gridCellID)
	precip = g.queryOnDemand(gridCellID)
	
	if precip != nil {
		// Cache the result
		g.mu.Lock()
		g.data[gridCellID] = precip
		g.mu.Unlock()
		slog.Info("cached WorldClim data on-demand", "cell_id", gridCellID, "precip", precip)
	}
	
	return precip
}

// queryOnDemand queries WorldClim data for a grid cell not in preloaded data
func (g *GridPrecipData) queryOnDemand(gridCellID string) []float64 {
	// Parse grid cell ID (format: "lon_lat")
	var lon, lat float64
	parts := strings.Split(gridCellID, "_")
	if len(parts) != 2 {
		slog.Warn("invalid grid cell ID format", "cell_id", gridCellID)
		return nil
	}
	
	var err error
	if lon, err = strconv.ParseFloat(parts[0], 64); err != nil {
		slog.Warn("invalid lon in grid cell ID", "cell_id", gridCellID, "error", err)
		return nil
	}
	if lat, err = strconv.ParseFloat(parts[1], 64); err != nil {
		slog.Warn("invalid lat in grid cell ID", "cell_id", gridCellID, "error", err)
		return nil
	}
	
	// Call Python script to query WorldClim
	scriptPath := filepath.Join("scripts", "query_worldclim_point.py")
	cmd := exec.Command("python3", scriptPath, fmt.Sprintf("%.2f", lat), fmt.Sprintf("%.2f", lon))
	
	output, err := cmd.Output()
	if err != nil {
		slog.Warn("failed to query WorldClim on-demand", "cell_id", gridCellID, "error", err)
		return nil
	}
	
	// Parse JSON response
	var result struct {
		Precip []float64 `json:"precip"`
		Error  string    `json:"error"`
	}
	
	if err := json.Unmarshal(output, &result); err != nil {
		slog.Warn("failed to parse WorldClim response", "cell_id", gridCellID, "error", err)
		return nil
	}
	
	if result.Error != "" {
		slog.Warn("WorldClim query error", "cell_id", gridCellID, "error", result.Error)
		return nil
	}
	
	if len(result.Precip) != 12 {
		slog.Warn("invalid precipitation data length", "cell_id", gridCellID, "length", len(result.Precip))
		return nil
	}
	
	return result.Precip
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

// GetPrecipForLocation returns monthly precipitation for a lat/lon point
// Rounds to 0.5° grid cell and uses GetMonthlyPrecip (with on-demand query)
func (g *GridPrecipData) GetPrecipForLocation(lat, lon float64) []float64 {
	if g == nil {
		return nil
	}
	
	// Match the grid generation logic:
	// Grid cells centered at: -24.75, -24.25, -23.75, ... (min + res/2 + n*res)
	// This creates cells at .2 and .8 when formatted to 1 decimal
	resolution := 0.5
	minLon, minLat := -25.0, -35.0
	
	// Find nearest grid cell center
	gridLon := minLon + resolution/2 + resolution*math.Round((lon-minLon-resolution/2)/resolution)
	gridLat := minLat + resolution/2 + resolution*math.Round((lat-minLat-resolution/2)/resolution)
	
	// Format grid cell ID (will be like 20.2_0.2)
	gridCellID := fmt.Sprintf("%.1f_%.1f", gridLon, gridLat)
	
	return g.GetMonthlyPrecip(gridCellID)
}

// ClassifyDryRainyForLocation returns dry/rainy months for a specific location
func (g *GridPrecipData) ClassifyDryRainyForLocation(lat, lon float64) (dryMonths, rainyMonths []int) {
	precip := g.GetPrecipForLocation(lat, lon)
	if precip == nil || len(precip) != 12 {
		// Fallback to default
		return []int{11, 12, 1, 2, 3, 4}, []int{5, 6, 7, 8, 9, 10}
	}
	
	threshold := 50.0
	for month := 1; month <= 12; month++ {
		if precip[month-1] < threshold {
			dryMonths = append(dryMonths, month)
		} else {
			rainyMonths = append(rainyMonths, month)
		}
	}
	
	return dryMonths, rainyMonths
}

// HandleWorldClimTest - test endpoint for WorldClim on-demand lookup
func (s *Server) HandleWorldClimTest(w http.ResponseWriter, r *http.Request) {
	latStr := r.URL.Query().Get("lat")
	lonStr := r.URL.Query().Get("lon")
	
	if latStr == "" || lonStr == "" {
		http.Error(w, "Missing lat/lon parameters", http.StatusBadRequest)
		return
	}
	
	var lat, lon float64
	if _, err := fmt.Sscanf(latStr, "%f", &lat); err != nil {
		http.Error(w, "Invalid lat", http.StatusBadRequest)
		return
	}
	if _, err := fmt.Sscanf(lonStr, "%f", &lon); err != nil {
		http.Error(w, "Invalid lon", http.StatusBadRequest)
		return
	}
	
	slog.Info("WorldClim test request", "lat", lat, "lon", lon, "globalGridPrecip", globalGridPrecip != nil)
	
	// Round to grid cell using same logic as GetPrecipForLocation
	resolution := 0.5
	minLon, minLat := -25.0, -35.0
	gridLon := minLon + resolution/2 + resolution*math.Round((lon-minLon-resolution/2)/resolution)
	gridLat := minLat + resolution/2 + resolution*math.Round((lat-minLat-resolution/2)/resolution)
	gridCellID := fmt.Sprintf("%.1f_%.1f", gridLon, gridLat)
	
	// Get precipitation (will query on-demand if needed)
	precip := globalGridPrecip.GetPrecipForLocation(lat, lon)
	
	// Classify seasons
	dryMonths, rainyMonths := globalGridPrecip.ClassifyDryRainyForLocation(lat, lon)
	
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"location": map[string]float64{"lat": lat, "lon": lon},
		"grid_cell": map[string]interface{}{
			"id":  gridCellID,
			"lat": gridLat,
			"lon": gridLon,
		},
		"monthly_precip_mm": precip,
		"dry_months":        dryMonths,
		"rainy_months":      rainyMonths,
	})
}
