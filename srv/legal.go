package srv

import (
	"encoding/json"
	"net/http"
	"os"
	"strings"
)

// LegalFrameworks holds the complete legal data structure.
type LegalFrameworks struct {
	Countries  map[string]CountryLegal `json:"countries"`
	PASpecific map[string]PALegal      `json:"pa_specific"`
}

// CountryLegal holds legal information for a country.
type CountryLegal struct {
	Name        string       `json:"name"`
	Legislation []Legislation `json:"legislation"`
}

// Legislation represents a single piece of legislation.
type Legislation struct {
	Name string `json:"name"`
	Year int    `json:"year"`
	URL  string `json:"url"`
}

// PALegal holds legal information specific to a protected area.
type PALegal struct {
	Name                     string   `json:"name"`
	LegalDesignation         string   `json:"legal_designation"`
	EstablishmentYear        int      `json:"establishment_year"`
	GoverningBody            string   `json:"governing_body"`
	SpecificRegulations      []string `json:"specific_regulations"`
	InternationalDesignations []string `json:"international_designations,omitempty"`
}

// LegalStore holds loaded legal frameworks data.
type LegalStore struct {
	Frameworks *LegalFrameworks
}

// LoadLegalFrameworks loads legal frameworks from JSON file.
func LoadLegalFrameworks(path string) (*LegalStore, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}

	var frameworks LegalFrameworks
	if err := json.Unmarshal(data, &frameworks); err != nil {
		return nil, err
	}

	return &LegalStore{Frameworks: &frameworks}, nil
}

// HandleAPILegalByCountry returns legal information for a country.
// GET /api/legal/:country_code
func (s *Server) HandleAPILegalByCountry(w http.ResponseWriter, r *http.Request) {
	if s.LegalStore == nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"error": "legal data not configured"})
		return
	}

	// Extract country code from path
	path := r.URL.Path
	parts := strings.Split(path, "/")
	if len(parts) < 4 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "country code required"})
		return
	}
	countryCode := strings.ToUpper(parts[3])

	country, ok := s.LegalStore.Frameworks.Countries[countryCode]
	if !ok {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{"error": "country not found"})
		return
	}

	// Also find any PA-specific entries for this country
	paEntries := make(map[string]PALegal)
	for paID, paLegal := range s.LegalStore.Frameworks.PASpecific {
		if strings.HasPrefix(paID, countryCode+"_") {
			paEntries[paID] = paLegal
		}
	}

	response := map[string]interface{}{
		"country_code":       countryCode,
		"country":            country,
		"protected_areas":    paEntries,
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	json.NewEncoder(w).Encode(response)
}

// HandleAPILegalByPA returns legal information for a specific protected area.
// GET /api/legal/pa/:pa_id
func (s *Server) HandleAPILegalByPA(w http.ResponseWriter, r *http.Request) {
	if s.LegalStore == nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"error": "legal data not configured"})
		return
	}

	// Extract PA ID from path
	path := r.URL.Path
	parts := strings.Split(path, "/")
	if len(parts) < 5 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "PA ID required"})
		return
	}
	paID := parts[4]

	paLegal, ok := s.LegalStore.Frameworks.PASpecific[paID]
	if !ok {
		// Try to find country-level info based on PA ID prefix
		countryCode := ""
		if idx := strings.Index(paID, "_"); idx > 0 {
			countryCode = paID[:idx]
		}

		if countryCode != "" {
			if country, ok := s.LegalStore.Frameworks.Countries[countryCode]; ok {
				response := map[string]interface{}{
					"pa_id":        paID,
					"pa_specific":  nil,
					"country_code": countryCode,
					"country":      country,
				}
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("Cache-Control", "public, max-age=3600")
				json.NewEncoder(w).Encode(response)
				return
			}
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]string{"error": "no legal info found for PA"})
		return
	}

	// Get country info from PA ID prefix
	countryCode := ""
	if idx := strings.Index(paID, "_"); idx > 0 {
		countryCode = paID[:idx]
	}

	var countryInfo *CountryLegal
	if countryCode != "" {
		if c, ok := s.LegalStore.Frameworks.Countries[countryCode]; ok {
			countryInfo = &c
		}
	}

	response := map[string]interface{}{
		"pa_id":        paID,
		"pa_specific":  paLegal,
		"country_code": countryCode,
		"country":      countryInfo,
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	json.NewEncoder(w).Encode(response)
}
