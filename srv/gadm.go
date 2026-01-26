package srv

import (
	"encoding/json"
	"os"
	"strings"
)

// GADMStore holds country and region data for search.
type GADMStore struct {
	Countries []GADMCountry `json:"countries"`
	Regions   []GADMRegion  `json:"regions"`
}

// GADMCountry represents a country entry.
type GADMCountry struct {
	Code   string    `json:"code"`
	Name   string    `json:"name"`
	BBox   []float64 `json:"bbox"`
	Center []float64 `json:"center"`
}

// GADMRegion represents an administrative region.
type GADMRegion struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	CountryCode string    `json:"country_code"`
	Country     string    `json:"country"`
	Type        string    `json:"type"`
	BBox        []float64 `json:"bbox"`
	Center      []float64 `json:"center"`
}

// LoadGADMStore loads GADM data from a JSON file.
func LoadGADMStore(path string) (*GADMStore, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}

	var store GADMStore
	if err := json.Unmarshal(data, &store); err != nil {
		return nil, err
	}

	return &store, nil
}

// SearchCountries searches for countries by name.
func (g *GADMStore) SearchCountries(query string, limit int) []GADMCountry {
	query = strings.ToLower(query)
	var results []GADMCountry

	for _, c := range g.Countries {
		if strings.Contains(strings.ToLower(c.Name), query) {
			results = append(results, c)
			if len(results) >= limit {
				break
			}
		}
	}

	return results
}

// SearchRegions searches for administrative regions by name.
func (g *GADMStore) SearchRegions(query string, limit int) []GADMRegion {
	query = strings.ToLower(query)
	var results []GADMRegion

	for _, r := range g.Regions {
		if strings.Contains(strings.ToLower(r.Name), query) ||
			strings.Contains(strings.ToLower(r.Country), query) {
			results = append(results, r)
			if len(results) >= limit {
				break
			}
		}
	}

	return results
}
