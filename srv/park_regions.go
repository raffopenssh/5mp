package srv

import (
	"encoding/json"
	"log/slog"
	"os"
	"sync"
)

// ParkGADMRegions stores GADM region mappings for a park
type ParkGADMRegions struct {
	CountryISO     string   `json:"country_iso"`
	Level1Regions  []string `json:"level1_regions"`
	Level1IDs      []string `json:"level1_ids"`
	Level2Regions  []string `json:"level2_regions"`
	Level2IDs      []string `json:"level2_ids"`
	Level2Varnames []string `json:"level2_varnames"`
}

// ParkRegionsStore caches park-region mappings
type ParkRegionsStore struct {
	regions map[string]*ParkGADMRegions
	mu      sync.RWMutex
	loaded  bool
}

var parkRegionsStore = &ParkRegionsStore{
	regions: make(map[string]*ParkGADMRegions),
}

// LoadParkRegions loads park-GADM region mappings from JSON
func LoadParkRegions() error {
	parkRegionsStore.mu.Lock()
	defer parkRegionsStore.mu.Unlock()

	if parkRegionsStore.loaded {
		return nil
	}

	data, err := os.ReadFile("data/park_gadm_regions.json")
	if err != nil {
		slog.Warn("Could not load park GADM regions", "error", err)
		return err
	}

	if err := json.Unmarshal(data, &parkRegionsStore.regions); err != nil {
		slog.Error("Failed to parse park GADM regions", "error", err)
		return err
	}

	parkRegionsStore.loaded = true
	slog.Info("Loaded park GADM regions", "parks", len(parkRegionsStore.regions))
	return nil
}

// GetParkRegions returns GADM regions for a park
func GetParkRegions(parkID string) *ParkGADMRegions {
	// Ensure loaded
	LoadParkRegions()

	parkRegionsStore.mu.RLock()
	defer parkRegionsStore.mu.RUnlock()

	return parkRegionsStore.regions[parkID]
}

// GetAllRegionNames returns all region names (level 1 + level 2 + varnames) for a park
func GetAllRegionNames(parkID string) []string {
	regions := GetParkRegions(parkID)
	if regions == nil {
		return nil
	}

	// Combine all region names for comprehensive search
	names := make([]string, 0, len(regions.Level1Regions)+len(regions.Level2Regions)+len(regions.Level2Varnames))
	names = append(names, regions.Level1Regions...)
	names = append(names, regions.Level2Regions...)
	names = append(names, regions.Level2Varnames...)

	return names
}
