package areas

import (
	"testing"
)

func TestLoadWDPAIndex(t *testing.T) {
	idx, err := LoadWDPAIndex("../../data/wdpa_index.json")
	if err != nil {
		t.Fatalf("LoadWDPAIndex failed: %v", err)
	}

	if len(idx.Entries) == 0 {
		t.Fatal("Expected entries in WDPA index")
	}

	t.Logf("Loaded %d WDPA entries", len(idx.Entries))

	// Test search
	results := idx.Search("Serengeti", 10)
	if len(results) == 0 {
		t.Error("Expected to find Serengeti")
	}

	for _, r := range results {
		t.Logf("Found: %s (%s) - WDPA ID: %d", r.Name, r.Country, r.WDPAID)
	}

	// Test GetByID - Serengeti's WDPA ID is 916
	entry := idx.GetByID(916)
	if entry == nil {
		t.Log("WDPA ID 916 (Serengeti) not found in index - may not be in African countries")
	} else {
		t.Logf("Found by ID: %s (%s)", entry.Name, entry.Country)
	}
}

func TestWDPAIndexSearch(t *testing.T) {
	idx, err := LoadWDPAIndex("../../data/wdpa_index.json")
	if err != nil {
		t.Fatalf("LoadWDPAIndex failed: %v", err)
	}

	tests := []struct {
		query      string
		expectMin  int
		expectName string // Expected to contain this in results
	}{
		{"Kruger", 1, "Kruger"},
		{"national park", 10, ""},
		{"xyz123nonexistent", 0, ""},
	}

	for _, tt := range tests {
		t.Run(tt.query, func(t *testing.T) {
			results := idx.Search(tt.query, 20)
			if len(results) < tt.expectMin {
				t.Errorf("Search(%q) got %d results, expected at least %d", tt.query, len(results), tt.expectMin)
			}

			if tt.expectName != "" {
				found := false
				for _, r := range results {
					if contains(r.Name, tt.expectName) {
						found = true
						break
					}
				}
				if !found {
					t.Errorf("Expected to find %q in results", tt.expectName)
				}
			}
		})
	}
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(substr) == 0 ||
		(len(s) > 0 && len(substr) > 0 && findSubstring(s, substr)))
}

func findSubstring(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
