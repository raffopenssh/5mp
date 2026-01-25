// Command fetchpas fetches protected area boundaries from the Protected Planet API.
// It reads a list of keystone protected areas and fetches their WDPA data including geometry.
//
// Usage: go run ./cmd/fetchpas
package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"time"

	"srv.exe.dev/srv/protectedplanet"
)

const (
	inputFile       = "/tmp/keystones_list.json"
	outputFile      = "data/keystones.json"
	requestDelaySec = 1
)

// KeystoneInput represents an entry in the input keystones list.
type KeystoneInput struct {
	Country string `json:"country"`
	Name    string `json:"name"`
}

// KeystoneOutput represents a protected area with its geometry for output.
type KeystoneOutput struct {
	WDPAID       int             `json:"wdpa_id"`
	Name         string          `json:"name"`
	Country      string          `json:"country"`
	AreaKm2      float64         `json:"area_km2"`
	IUCNCategory string          `json:"iucn_category"`
	Geometry     json.RawMessage `json:"geometry"`
}

func main() {
	// Read input file
	data, err := os.ReadFile(inputFile)
	if err != nil {
		log.Fatalf("Failed to read input file %s: %v", inputFile, err)
	}

	var keystones []KeystoneInput
	if err := json.Unmarshal(data, &keystones); err != nil {
		log.Fatalf("Failed to parse input JSON: %v", err)
	}

	fmt.Printf("Loaded %d protected areas from %s\n", len(keystones), inputFile)

	// Create Protected Planet client
	client := protectedplanet.NewClient()

	// Process each keystone
	var results []KeystoneOutput
	var notFound []KeystoneInput

	for i, ks := range keystones {
		fmt.Printf("[%d/%d] Searching for %s in %s... ", i+1, len(keystones), ks.Name, ks.Country)

		// Search by name within the country
		matches, err := client.SearchByName(ks.Name, ks.Country)
		if err != nil {
			fmt.Printf("ERROR: %v\n", err)
			notFound = append(notFound, ks)
			time.Sleep(requestDelaySec * time.Second)
			continue
		}

		if len(matches) == 0 {
			fmt.Printf("NOT FOUND\n")
			notFound = append(notFound, ks)
			time.Sleep(requestDelaySec * time.Second)
			continue
		}

		// Use the first match
		match := matches[0]
		fmt.Printf("found WDPA ID %d, fetching geometry... ", match.WDPAID)

		// Rate limit before fetching geometry
		time.Sleep(requestDelaySec * time.Second)

		// Fetch full details with geometry
		pa, err := client.GetByWDPAID(match.WDPAID)
		if err != nil {
			fmt.Printf("ERROR: %v\n", err)
			notFound = append(notFound, ks)
			continue
		}

		// Extract geometry
		var geomJSON json.RawMessage
		if pa.Geometry != nil && pa.Geometry.Geometry != nil {
			// Serialize just the inner geometry (not the full feature)
			geomJSON, err = json.Marshal(pa.Geometry.Geometry)
			if err != nil {
				fmt.Printf("ERROR marshaling geometry: %v\n", err)
				notFound = append(notFound, ks)
				continue
			}
		} else {
			fmt.Printf("NO GEOMETRY\n")
			notFound = append(notFound, ks)
			continue
		}

		result := KeystoneOutput{
			WDPAID:       pa.WDPAID,
			Name:         pa.Name,
			Country:      ks.Country, // Keep the ISO3 code from input
			AreaKm2:      pa.AreaKm2,
			IUCNCategory: pa.IUCNCategory,
			Geometry:     geomJSON,
		}
		results = append(results, result)
		fmt.Printf("OK (%.0f km², %s)\n", pa.AreaKm2, pa.IUCNCategory)

		// Rate limit between requests
		if i < len(keystones)-1 {
			time.Sleep(requestDelaySec * time.Second)
		}
	}

	// Write results
	output, err := json.MarshalIndent(results, "", "  ")
	if err != nil {
		log.Fatalf("Failed to marshal results: %v", err)
	}

	if err := os.WriteFile(outputFile, output, 0644); err != nil {
		log.Fatalf("Failed to write output file %s: %v", outputFile, err)
	}

	fmt.Printf("\n=== Summary ===\n")
	fmt.Printf("Successfully fetched: %d\n", len(results))
	fmt.Printf("Not found: %d\n", len(notFound))
	fmt.Printf("Results saved to: %s\n", outputFile)

	if len(notFound) > 0 {
		fmt.Printf("\nProtected areas not found:\n")
		for _, nf := range notFound {
			fmt.Printf("  - %s (%s)\n", nf.Name, nf.Country)
		}
	}
}
