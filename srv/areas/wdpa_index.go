package areas

import (
	"encoding/json"
	"os"
	"strings"
)

// WDPAIndexEntry represents a protected area from the WDPA index.
type WDPAIndexEntry struct {
	WDPAID      int     `json:"wdpa_id"`
	Name        string  `json:"name"`
	Country     string  `json:"country"`
	CountryCode string  `json:"country_code"`
	Designation string  `json:"designation,omitempty"`
	IUCNCat     string  `json:"iucn_category,omitempty"`
	AreaKm2     float64 `json:"area_km2,omitempty"`
}

// WDPAIndex holds the index of all WDPA protected areas for search.
type WDPAIndex struct {
	Entries []WDPAIndexEntry
	// Map of WDPA ID to entry for fast lookup
	ByID map[int]*WDPAIndexEntry
}

// LoadWDPAIndex loads the WDPA index from a JSON file.
func LoadWDPAIndex(path string) (*WDPAIndex, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}

	var entries []WDPAIndexEntry
	if err := json.Unmarshal(data, &entries); err != nil {
		return nil, err
	}

	index := &WDPAIndex{
		Entries: entries,
		ByID:    make(map[int]*WDPAIndexEntry, len(entries)),
	}

	for i := range entries {
		index.ByID[entries[i].WDPAID] = &entries[i]
	}

	return index, nil
}

// Search searches the WDPA index for entries matching the query.
// Returns up to maxResults entries.
func (idx *WDPAIndex) Search(query string, maxResults int) []WDPAIndexEntry {
	if idx == nil || query == "" {
		return nil
	}

	queryLower := strings.ToLower(query)
	results := make([]WDPAIndexEntry, 0, maxResults)

	for _, entry := range idx.Entries {
		if strings.Contains(strings.ToLower(entry.Name), queryLower) {
			results = append(results, entry)
			if len(results) >= maxResults {
				break
			}
		}
	}

	return results
}

// GetByID returns a WDPA entry by its ID, or nil if not found.
func (idx *WDPAIndex) GetByID(wdpaID int) *WDPAIndexEntry {
	if idx == nil {
		return nil
	}
	return idx.ByID[wdpaID]
}
