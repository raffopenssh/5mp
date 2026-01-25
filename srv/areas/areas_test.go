package areas

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// Helper to create a simple rectangular polygon geometry
func makeRectPolygon(latMin, latMax, lonMin, lonMax float64) GeoJSONGeometry {
	rings := [][][]float64{{
		{lonMin, latMin}, // SW
		{lonMax, latMin}, // SE
		{lonMax, latMax}, // NE
		{lonMin, latMax}, // NW
		{lonMin, latMin}, // SW (close ring)
	}}
	coords, _ := json.Marshal(rings)
	return GeoJSONGeometry{
		Type:        "Polygon",
		Coordinates: coords,
		parsedRings: rings,
	}
}

func TestPointInPolygon(t *testing.T) {
	serengeti := ProtectedArea{
		ID:       "serengeti",
		Name:     "Serengeti National Park",
		Geometry: makeRectPolygon(-3.0, -1.5, 34.0, 35.5),
		BufferKm: 5.0,
	}
	serengeti.computeBoundingBox()

	tests := []struct {
		name     string
		lat, lon float64
		want     bool
	}{
		{"center of park", -2.25, 34.75, true},
		{"edge of park", -3.0, 34.0, true},
		{"within buffer", -3.04, 34.0, true}, // ~4.4km outside, within 5km buffer
		{"outside buffer", -3.1, 34.0, false}, // ~11km outside, beyond 5km buffer
		{"far outside", -10.0, 40.0, false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := serengeti.ContainsPoint(tt.lat, tt.lon)
			if got != tt.want {
				t.Errorf("ContainsPoint(%v, %v) = %v, want %v", tt.lat, tt.lon, got, tt.want)
			}
		})
	}
}

func TestPointInIrregularPolygon(t *testing.T) {
	// Test with a triangle-shaped polygon
	rings := [][][]float64{{
		{0.0, 0.0},   // bottom left
		{2.0, 0.0},   // bottom right
		{1.0, 2.0},   // top center
		{0.0, 0.0},   // close
	}}
	coords, _ := json.Marshal(rings)
	triangle := ProtectedArea{
		ID:   "triangle",
		Name: "Triangle Area",
		Geometry: GeoJSONGeometry{
			Type:        "Polygon",
			Coordinates: coords,
			parsedRings: rings,
		},
		BufferKm: 0.0,
	}
	triangle.computeBoundingBox()

	tests := []struct {
		name     string
		lat, lon float64
		want     bool
	}{
		{"center", 0.5, 1.0, true},
		{"inside near bottom", 0.1, 1.0, true},
		{"outside left", 1.0, -0.5, false},
		{"outside right", 1.0, 2.5, false},
		{"outside top", 2.5, 1.0, false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := triangle.ContainsPoint(tt.lat, tt.lon)
			if got != tt.want {
				t.Errorf("ContainsPoint(%v, %v) = %v, want %v", tt.lat, tt.lon, got, tt.want)
			}
		})
	}
}

func TestLoadAreas(t *testing.T) {
	// Create temp file with test data using polygon geometry
	tmpDir := t.TempDir()
	tmpFile := filepath.Join(tmpDir, "areas.json")

	testData := `[
		{"id": "test1", "name": "Test Area 1", "country": "Test", "geometry": {"type": "Polygon", "coordinates": [[[34.0, -2.0], [35.0, -2.0], [35.0, -1.0], [34.0, -1.0], [34.0, -2.0]]]}, "buffer_km": 2.0},
		{"id": "test2", "name": "Test Area 2", "country": "Test", "geometry": {"type": "Polygon", "coordinates": [[[36.0, -5.0], [37.0, -5.0], [37.0, -4.0], [36.0, -4.0], [36.0, -5.0]]]}, "buffer_km": 1.0}
	]`

	if err := os.WriteFile(tmpFile, []byte(testData), 0644); err != nil {
		t.Fatalf("failed to write test file: %v", err)
	}

	store, err := LoadAreas(tmpFile)
	if err != nil {
		t.Fatalf("LoadAreas failed: %v", err)
	}

	if len(store.Areas) != 2 {
		t.Errorf("expected 2 areas, got %d", len(store.Areas))
	}

	if store.Areas[0].ID != "test1" {
		t.Errorf("expected first area ID 'test1', got '%s'", store.Areas[0].ID)
	}

	// Verify bounding boxes were computed
	if store.Areas[0].bbox == nil {
		t.Error("expected bounding box to be computed for area 1")
	}
}

func TestFindArea(t *testing.T) {
	tmpDir := t.TempDir()
	tmpFile := filepath.Join(tmpDir, "areas.json")

	testData := `[
		{"id": "area1", "name": "Area 1", "country": "Test", "geometry": {"type": "Polygon", "coordinates": [[[34.0, -2.0], [35.0, -2.0], [35.0, -1.0], [34.0, -1.0], [34.0, -2.0]]]}, "buffer_km": 0.0},
		{"id": "area2", "name": "Area 2", "country": "Test", "geometry": {"type": "Polygon", "coordinates": [[[36.0, -5.0], [37.0, -5.0], [37.0, -4.0], [36.0, -4.0], [36.0, -5.0]]]}, "buffer_km": 0.0}
	]`

	if err := os.WriteFile(tmpFile, []byte(testData), 0644); err != nil {
		t.Fatalf("failed to write test file: %v", err)
	}

	store, err := LoadAreas(tmpFile)
	if err != nil {
		t.Fatalf("LoadAreas failed: %v", err)
	}

	tests := []struct {
		name     string
		lat, lon float64
		wantID   string
	}{
		{"in area1", -1.5, 34.5, "area1"},
		{"in area2", -4.5, 36.5, "area2"},
		{"outside all", 0.0, 0.0, ""},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			area := store.FindArea(tt.lat, tt.lon)
			if tt.wantID == "" {
				if area != nil {
					t.Errorf("expected nil, got area %s", area.ID)
				}
			} else {
				if area == nil {
					t.Errorf("expected area %s, got nil", tt.wantID)
				} else if area.ID != tt.wantID {
					t.Errorf("expected area %s, got %s", tt.wantID, area.ID)
				}
			}
		})
	}
}

func TestAssignPointsToAreas(t *testing.T) {
	tmpDir := t.TempDir()
	tmpFile := filepath.Join(tmpDir, "areas.json")

	testData := `[
		{"id": "park1", "name": "Park 1", "country": "Test", "geometry": {"type": "Polygon", "coordinates": [[[34.0, -2.0], [35.0, -2.0], [35.0, -1.0], [34.0, -1.0], [34.0, -2.0]]]}, "buffer_km": 0.0},
		{"id": "park2", "name": "Park 2", "country": "Test", "geometry": {"type": "Polygon", "coordinates": [[[36.0, -5.0], [37.0, -5.0], [37.0, -4.0], [36.0, -4.0], [36.0, -5.0]]]}, "buffer_km": 0.0}
	]`

	if err := os.WriteFile(tmpFile, []byte(testData), 0644); err != nil {
		t.Fatalf("failed to write test file: %v", err)
	}

	store, err := LoadAreas(tmpFile)
	if err != nil {
		t.Fatalf("LoadAreas failed: %v", err)
	}

	points := []Point{
		{Lat: -1.5, Lon: 34.5},  // in park1
		{Lat: -1.8, Lon: 34.8},  // in park1
		{Lat: -4.5, Lon: 36.5},  // in park2
		{Lat: 10.0, Lon: 10.0},  // outside
		{Lat: -10.0, Lon: 40.0}, // outside
	}

	result := store.AssignPointsToAreas(points)

	if len(result["park1"]) != 2 {
		t.Errorf("expected 2 points in park1, got %d", len(result["park1"]))
	}
	if len(result["park2"]) != 1 {
		t.Errorf("expected 1 point in park2, got %d", len(result["park2"]))
	}
	if len(result["outside"]) != 2 {
		t.Errorf("expected 2 points outside, got %d", len(result["outside"]))
	}
}

func TestLoadRealAreasFile(t *testing.T) {
	// Test loading the actual areas.json file
	store, err := LoadAreas("../../data/areas.json")
	if err != nil {
		t.Fatalf("LoadAreas failed: %v", err)
	}

	if len(store.Areas) != 10 {
		t.Errorf("expected 10 areas, got %d", len(store.Areas))
	}

	// Test that Serengeti contains a known point inside it
	area := store.FindArea(-2.5, 35.0) // Should be inside Serengeti-Mara
	if area == nil {
		t.Error("expected to find Serengeti-Mara at -2.5, 35.0")
	} else if area.ID != "serengeti-mara" {
		t.Errorf("expected serengeti-mara, got %s", area.ID)
	}

	// Test that a point far outside returns nil
	area = store.FindArea(50.0, 0.0) // London area - should be outside
	if area != nil {
		t.Errorf("expected nil for London coordinates, got %s", area.ID)
	}
}

func TestLoadKeystones(t *testing.T) {
	// Test loading keystones from data directory
	store, err := LoadKeystones("../../data")
	if err != nil {
		t.Fatalf("LoadKeystones failed: %v", err)
	}

	// Should have 162 keystones
	if len(store.Areas) != 162 {
		t.Errorf("expected 162 keystones, got %d", len(store.Areas))
	}

	// Check first area has expected fields
	if len(store.Areas) > 0 {
		area := store.Areas[0]
		if area.ID == "" {
			t.Error("expected area to have ID")
		}
		if area.Name == "" {
			t.Error("expected area to have Name")
		}
		if area.Country == "" {
			t.Error("expected area to have Country")
		}
		if area.WDPAID == "" {
			t.Error("expected area to have WDPAID")
		}
		if area.bbox == nil {
			t.Error("expected area to have computed bounding box")
		}
	}

	// Test that FindArea works for a known point (Cameia center)
	area := store.FindArea(-11.5, 21.5)
	if area == nil {
		t.Error("expected to find Cameia at -11.5, 21.5")
	} else if area.ID != "AGO_Cameia" {
		t.Errorf("expected AGO_Cameia, got %s", area.ID)
	}
}
