package areas

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPointInArea(t *testing.T) {
	serengeti := ProtectedArea{
		ID:       "serengeti",
		Name:     "Serengeti National Park",
		LatMin:   -3.0,
		LatMax:   -1.5,
		LonMin:   34.0,
		LonMax:   35.5,
		BufferKm: 5.0,
	}

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
			got := PointInArea(tt.lat, tt.lon, serengeti)
			if got != tt.want {
				t.Errorf("PointInArea(%v, %v) = %v, want %v", tt.lat, tt.lon, got, tt.want)
			}
		})
	}
}

func TestLoadAreas(t *testing.T) {
	// Create temp file with test data
	tmpDir := t.TempDir()
	tmpFile := filepath.Join(tmpDir, "areas.json")

	testData := `[
		{"id": "test1", "name": "Test Area 1", "lat_min": -2.0, "lat_max": -1.0, "lon_min": 34.0, "lon_max": 35.0, "buffer_km": 2.0},
		{"id": "test2", "name": "Test Area 2", "lat_min": -5.0, "lat_max": -4.0, "lon_min": 36.0, "lon_max": 37.0, "buffer_km": 1.0}
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
}

func TestFindArea(t *testing.T) {
	tmpDir := t.TempDir()
	tmpFile := filepath.Join(tmpDir, "areas.json")

	testData := `[
		{"id": "area1", "name": "Area 1", "lat_min": -2.0, "lat_max": -1.0, "lon_min": 34.0, "lon_max": 35.0, "buffer_km": 0.0},
		{"id": "area2", "name": "Area 2", "lat_min": -5.0, "lat_max": -4.0, "lon_min": 36.0, "lon_max": 37.0, "buffer_km": 0.0}
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
		{"id": "park1", "name": "Park 1", "lat_min": -2.0, "lat_max": -1.0, "lon_min": 34.0, "lon_max": 35.0, "buffer_km": 0.0},
		{"id": "park2", "name": "Park 2", "lat_min": -5.0, "lat_max": -4.0, "lon_min": 36.0, "lon_max": 37.0, "buffer_km": 0.0}
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
