// Command buildwdpaindex fetches protected areas from WDPA API for African countries
// and creates an index file for search functionality.
package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"
)

const (
	apiURL = "https://api.protectedplanet.net/v3/protected_areas/search"
	apiKey = "dea58ea0389007e386776c4f583f4425"
	perPage = 50 // API max is 50
)

// African countries ISO3 codes
var africanCountries = []string{
	"DZA", "AGO", "BEN", "BWA", "BFA", "BDI", "CMR", "CPV", "CAF", "TCD",
	"COM", "COD", "COG", "DJI", "EGY", "GNQ", "ERI", "SWZ", "ETH", "GAB",
	"GMB", "GHA", "GIN", "GNB", "CIV", "KEN", "LSO", "LBR", "LBY", "MDG",
	"MWI", "MLI", "MRT", "MUS", "MAR", "MOZ", "NAM", "NER", "NGA", "RWA",
	"STP", "SEN", "SYC", "SLE", "SOM", "ZAF", "SSD", "SDN", "TZA", "TGO",
	"TUN", "UGA", "ZMB", "ZWE",
}

type APIResponse struct {
	ProtectedAreas []APIPA `json:"protected_areas"`
}

type APIPA struct {
	WDPAID       int    `json:"wdpa_id"`
	Name         string `json:"name"`
	ReportedArea string `json:"reported_area"`
	Countries    []struct {
		Name string `json:"name"`
		ISO3 string `json:"iso_3"`
	} `json:"countries"`
	Designation *struct {
		Name string `json:"name"`
	} `json:"designation"`
	IUCNCategory *struct {
		Name string `json:"name"`
	} `json:"iucn_category"`
}

type WDPAIndexEntry struct {
	WDPAID      int     `json:"wdpa_id"`
	Name        string  `json:"name"`
	Country     string  `json:"country"`
	CountryCode string  `json:"country_code"`
	Designation string  `json:"designation,omitempty"`
	IUCNCat     string  `json:"iucn_category,omitempty"`
	AreaKm2     float64 `json:"area_km2,omitempty"`
}

func fetchCountry(client *http.Client, country string, seen map[int]bool) ([]WDPAIndexEntry, error) {
	var entries []WDPAIndexEntry
	page := 1

	for {
		url := fmt.Sprintf("%s?token=%s&country=%s&per_page=%d&page=%d",
			apiURL, apiKey, country, perPage, page)

		resp, err := client.Get(url)
		if err != nil {
			return entries, fmt.Errorf("fetch error: %w", err)
		}

		if resp.StatusCode != 200 {
			resp.Body.Close()
			return entries, fmt.Errorf("HTTP %d", resp.StatusCode)
		}

		var result APIResponse
		if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
			resp.Body.Close()
			return entries, fmt.Errorf("decode error: %w", err)
		}
		resp.Body.Close()

		if len(result.ProtectedAreas) == 0 {
			break
		}

		for _, pa := range result.ProtectedAreas {
			if seen[pa.WDPAID] {
				continue
			}
			seen[pa.WDPAID] = true

			entry := WDPAIndexEntry{
				WDPAID: pa.WDPAID,
				Name:   pa.Name,
			}

			if len(pa.Countries) > 0 {
				entry.Country = pa.Countries[0].Name
				entry.CountryCode = pa.Countries[0].ISO3
			}

			if pa.Designation != nil {
				entry.Designation = pa.Designation.Name
			}

			if pa.IUCNCategory != nil {
				entry.IUCNCat = pa.IUCNCategory.Name
			}

			if pa.ReportedArea != "" {
				fmt.Sscanf(pa.ReportedArea, "%f", &entry.AreaKm2)
			}

			entries = append(entries, entry)
		}

		// If we got fewer than perPage, we're on the last page
		if len(result.ProtectedAreas) < perPage {
			break
		}

		page++
		// Rate limiting
		time.Sleep(300 * time.Millisecond)
	}

	return entries, nil
}

func main() {
	client := &http.Client{Timeout: 60 * time.Second}

	var allPAs []WDPAIndexEntry
	seen := make(map[int]bool)

	for _, country := range africanCountries {
		log.Printf("Fetching PAs for %s...", country)

		entries, err := fetchCountry(client, country, seen)
		if err != nil {
			log.Printf("  Error: %v", err)
			continue
		}

		allPAs = append(allPAs, entries...)
		log.Printf("  Found %d PAs for %s (total: %d)", len(entries), country, len(allPAs))

		// Rate limiting between countries
		time.Sleep(500 * time.Millisecond)
	}

	log.Printf("Total unique PAs: %d", len(allPAs))

	// Write to file
	output, err := json.MarshalIndent(allPAs, "", "  ")
	if err != nil {
		log.Fatalf("Error marshaling: %v", err)
	}

	if err := os.WriteFile("data/wdpa_index.json", output, 0644); err != nil {
		log.Fatalf("Error writing file: %v", err)
	}

	log.Println("Done! Written to data/wdpa_index.json")
}
