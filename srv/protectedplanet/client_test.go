package protectedplanet

import (
	"testing"
)

func TestSearchByName(t *testing.T) {
	client := NewClient()

	// Search for Serengeti in Tanzania (TZA)
	results, err := client.SearchByName("Serengeti", "TZA")
	if err != nil {
		t.Fatalf("SearchByName failed: %v", err)
	}

	if len(results) == 0 {
		t.Fatal("Expected at least one result for Serengeti in Tanzania")
	}

	// Log results
	t.Logf("Found %d protected areas matching 'Serengeti' in Tanzania:", len(results))
	for i, pa := range results {
		t.Logf("  %d. %s (WDPA ID: %d, Country: %s, Area: %.2f km², IUCN: %s)",
			i+1, pa.Name, pa.WDPAID, pa.Country, pa.AreaKm2, pa.IUCNCategory)
	}

	// Verify we found Serengeti National Park
	found := false
	for _, pa := range results {
		if pa.Name == "Serengeti National Park" {
			found = true
			if pa.WDPAID != 916 {
				t.Errorf("Expected Serengeti WDPA ID 916, got %d", pa.WDPAID)
			}
			break
		}
	}

	if !found {
		t.Error("Serengeti National Park not found in results")
	}
}

func TestGetByWDPAID(t *testing.T) {
	client := NewClient()

	// Get Serengeti National Park by WDPA ID
	pa, err := client.GetByWDPAID(916)
	if err != nil {
		t.Fatalf("GetByWDPAID failed: %v", err)
	}

	t.Logf("Got PA: %s (WDPA ID: %d)", pa.Name, pa.WDPAID)
	t.Logf("  Country: %s", pa.Country)
	t.Logf("  Area: %.2f km²", pa.AreaKm2)
	t.Logf("  IUCN Category: %s", pa.IUCNCategory)

	if pa.Name != "Serengeti National Park" {
		t.Errorf("Expected 'Serengeti National Park', got '%s'", pa.Name)
	}

	if pa.Geometry == nil {
		t.Error("Expected geometry to be present")
	} else {
		t.Logf("  Geometry type: %s", pa.Geometry.Type)
		if pa.Geometry.Geometry != nil {
			t.Logf("  Geometry inner type: %s", pa.Geometry.Geometry.Type)
		}
	}
}

func TestGetGeometry(t *testing.T) {
	client := NewClient()

	// Get Serengeti geometry
	geom, err := client.GetGeometry(916)
	if err != nil {
		t.Fatalf("GetGeometry failed: %v", err)
	}

	if geom == nil {
		t.Fatal("Expected geometry to be present")
	}

	t.Logf("Geometry feature type: %s", geom.Type)
	if geom.Geometry != nil {
		t.Logf("Geometry type: %s", geom.Geometry.Type)
		t.Logf("Coordinates sample (first 100 bytes): %s", string(geom.Geometry.Coordinates[:min(100, len(geom.Geometry.Coordinates))]))
	}
}

func TestSearchByName_NoCountry(t *testing.T) {
	client := NewClient()

	_, err := client.SearchByName("Serengeti", "")
	if err == nil {
		t.Error("Expected error for missing country code")
	}
}

func TestGetByWDPAID_Invalid(t *testing.T) {
	client := NewClient()

	_, err := client.GetByWDPAID(-1)
	if err != ErrInvalidWDPAID {
		t.Errorf("Expected ErrInvalidWDPAID, got: %v", err)
	}

	_, err = client.GetByWDPAID(0)
	if err != ErrInvalidWDPAID {
		t.Errorf("Expected ErrInvalidWDPAID, got: %v", err)
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
