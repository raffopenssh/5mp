// Command fetchboundaries fetches actual boundaries for keystone protected areas.
// It reads keystones_basic.json, fetches geometries from Protected Planet API,
// and saves results to keystones_with_boundaries.json.
//
// Usage: go run ./cmd/fetchboundaries
package main

import (
	"encoding/json"
	"log"
	"os"
	"strconv"
	"time"

	"srv.exe.dev/srv/protectedplanet"
)

const (
	inputFile       = "data/keystones_basic.json"
	outputFile      = "data/keystones_with_boundaries.json"
	progressFile    = "data/keystones_with_boundaries_progress.json"
	requestDelaySec = 1
)

// KeystonePA matches the structure in keystones_basic.json.
type KeystonePA struct {
	ID          string  `json:"id"`
	CountryCode string  `json:"country_code"`
	Country     string  `json:"country"`
	Name        string  `json:"name"`
	Partner     *string `json:"partner"`
	Staff       *int    `json:"staff"`
	Budget      *int    `json:"budget"`
	Donor       *string `json:"donor"`
	Performance *string `json:"performance"`
	WDPAID      string  `json:"wdpa_id"`
	AreaKm2     *int    `json:"area_km2"`
	Coordinates struct {
		Lat float64 `json:"lat"`
		Lon float64 `json:"lon"`
	} `json:"coordinates"`
}

// GeoJSONGeometry represents a GeoJSON geometry object.
type GeoJSONGeometry struct {
	Type        string          `json:"type"`
	Coordinates json.RawMessage `json:"coordinates"`
}

// KeystoneWithBoundary includes the actual geometry.
type KeystoneWithBoundary struct {
	KeystonePA
	Geometry *GeoJSONGeometry `json:"geometry,omitempty"`
}

func main() {
	log.SetFlags(log.Ltime)

	// Read input file
	data, err := os.ReadFile(inputFile)
	if err != nil {
		log.Fatalf("Failed to read input file %s: %v", inputFile, err)
	}

	var keystones []KeystonePA
	if err := json.Unmarshal(data, &keystones); err != nil {
		log.Fatalf("Failed to parse input JSON: %v", err)
	}

	log.Printf("Loaded %d keystones from %s", len(keystones), inputFile)

	// Try to load progress file to resume
	results := make([]KeystoneWithBoundary, 0, len(keystones))
	processed := make(map[string]bool)

	if progressData, err := os.ReadFile(progressFile); err == nil {
		var progress []KeystoneWithBoundary
		if err := json.Unmarshal(progressData, &progress); err == nil {
			results = progress
			for _, r := range progress {
				processed[r.ID] = true
			}
			log.Printf("Resuming from progress file, %d already processed", len(processed))
		}
	}

	// Create Protected Planet client
	client := protectedplanet.NewClient()

	// Stats
	var fetched, noWDPA, noGeom, errors int

	for i, ks := range keystones {
		// Skip if already processed
		if processed[ks.ID] {
			continue
		}

		log.Printf("[%d/%d] Processing %s (%s)...", i+1, len(keystones), ks.Name, ks.CountryCode)

		result := KeystoneWithBoundary{KeystonePA: ks}

		// Check if we have a WDPA ID
		if ks.WDPAID == "" {
			log.Printf("  -> No WDPA ID, skipping geometry fetch")
			noWDPA++
			results = append(results, result)
			continue
		}

		// Parse WDPA ID
		wdpaID, err := strconv.Atoi(ks.WDPAID)
		if err != nil {
			log.Printf("  -> Invalid WDPA ID '%s': %v", ks.WDPAID, err)
			errors++
			results = append(results, result)
			continue
		}

		// Fetch from API
		pa, err := client.GetByWDPAID(wdpaID)
		if err != nil {
			log.Printf("  -> API error: %v", err)
			errors++
			results = append(results, result)
			time.Sleep(requestDelaySec * time.Second)
			continue
		}

		// Extract geometry
		if pa.Geometry != nil && pa.Geometry.Geometry != nil {
			geom := &GeoJSONGeometry{
				Type:        pa.Geometry.Geometry.Type,
				Coordinates: pa.Geometry.Geometry.Coordinates,
			}
			result.Geometry = geom
			log.Printf("  -> Got geometry (%s), area: %.0f km²", geom.Type, pa.AreaKm2)
			fetched++
		} else {
			log.Printf("  -> No geometry in response")
			noGeom++
		}

		results = append(results, result)

		// Save progress periodically (every 10 items)
		if len(results)%10 == 0 {
			saveProgress(results)
		}

		// Rate limit
		time.Sleep(requestDelaySec * time.Second)
	}

	// Final save
	output, err := json.MarshalIndent(results, "", "  ")
	if err != nil {
		log.Fatalf("Failed to marshal results: %v", err)
	}

	if err := os.WriteFile(outputFile, output, 0644); err != nil {
		log.Fatalf("Failed to write output file %s: %v", outputFile, err)
	}

	// Clean up progress file
	os.Remove(progressFile)

	log.Printf("")
	log.Printf("=== Summary ===")
	log.Printf("Total keystones: %d", len(keystones))
	log.Printf("Geometries fetched: %d", fetched)
	log.Printf("No WDPA ID: %d", noWDPA)
	log.Printf("No geometry available: %d", noGeom)
	log.Printf("Errors: %d", errors)
	log.Printf("Results saved to: %s", outputFile)
}

func saveProgress(results []KeystoneWithBoundary) {
	output, err := json.MarshalIndent(results, "", "  ")
	if err != nil {
		log.Printf("Warning: failed to save progress: %v", err)
		return
	}
	if err := os.WriteFile(progressFile, output, 0644); err != nil {
		log.Printf("Warning: failed to write progress file: %v", err)
	}
}
