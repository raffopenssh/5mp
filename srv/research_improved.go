package srv

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// MultilingualCountryNames maps ISO3 codes to country names in multiple languages
// Based on WOS query patterns: de, ar, fr, en, es, ru, zh
var MultilingualCountryNames = map[string][]string{
	"AGO": {"Angola", "Republica de Angola", "République d'Angola"},
	"BEN": {"Benin", "Bénin", "République du Bénin"},
	"BWA": {"Botswana", "Republic of Botswana"},
	"BFA": {"Burkina Faso", "Burkina"},
	"BDI": {"Burundi", "République du Burundi"},
	"CMR": {"Cameroon", "Cameroun", "République du Cameroun", "Kamerun"},
	"CAF": {"Central African Republic", "République centrafricaine", "Centrafrique"},
	"TCD": {"Chad", "Tchad", "République du Tchad", "Tschad", "تشاد"},
	"COD": {"Democratic Republic of the Congo", "DRC", "Congo-Kinshasa", "Zaire", "République démocratique du Congo", "Demokratische Republik Kongo", "جمهورية الكونغو الديمقراطي"},
	"COG": {"Republic of the Congo", "Congo-Brazzaville", "Congo", "République du Congo"},
	"CIV": {"Côte d'Ivoire", "Ivory Coast", "Cote d'Ivoire"},
	"DJI": {"Djibouti", "République de Djibouti"},
	"EGY": {"Egypt", "Égypte", "مصر", "Ägypten"},
	"GNQ": {"Equatorial Guinea", "Guinée équatoriale", "Guinea Ecuatorial"},
	"ERI": {"Eritrea", "État d'Érythrée"},
	"SWZ": {"Eswatini", "Swaziland"},
	"ETH": {"Ethiopia", "Éthiopie", "إثيوبيا"},
	"GAB": {"Gabon", "République gabonaise"},
	"GMB": {"Gambia", "The Gambia"},
	"GHA": {"Ghana", "Republic of Ghana"},
	"GIN": {"Guinea", "Guinée", "République de Guinée"},
	"GNB": {"Guinea-Bissau", "Guinée-Bissau"},
	"KEN": {"Kenya", "Republic of Kenya"},
	"LSO": {"Lesotho", "Kingdom of Lesotho"},
	"LBR": {"Liberia", "Republic of Liberia"},
	"LBY": {"Libya", "Libye", "ليبيا"},
	"MDG": {"Madagascar", "République de Madagascar"},
	"MWI": {"Malawi", "Republic of Malawi"},
	"MLI": {"Mali", "République du Mali"},
	"MRT": {"Mauritania", "Mauritanie", "موريتانيا"},
	"MUS": {"Mauritius"},
	"MAR": {"Morocco", "Maroc", "المغرب"},
	"MOZ": {"Mozambique", "Moçambique"},
	"NAM": {"Namibia", "Republic of Namibia"},
	"NER": {"Niger", "République du Niger"},
	"NGA": {"Nigeria", "Federal Republic of Nigeria"},
	"RWA": {"Rwanda", "République du Rwanda"},
	"STP": {"São Tomé and Príncipe", "Sao Tome and Principe"},
	"SEN": {"Senegal", "Sénégal", "République du Sénégal"},
	"SYC": {"Seychelles"},
	"SLE": {"Sierra Leone"},
	"SOM": {"Somalia", "Somalie", "الصومال"},
	"ZAF": {"South Africa", "Afrique du Sud", "Südafrika"},
	"SSD": {"South Sudan", "Soudan du Sud"},
	"SDN": {"Sudan", "Soudan", "السودان"},
	"TZA": {"Tanzania", "Tanzanie", "United Republic of Tanzania"},
	"TGO": {"Togo", "République togolaise"},
	"TUN": {"Tunisia", "Tunisie", "تونس"},
	"UGA": {"Uganda", "République d'Ouganda"},
	"ZMB": {"Zambia", "Republic of Zambia"},
	"ZWE": {"Zimbabwe", "Republic of Zimbabwe"},
}

// ConservationCategories from WOS: ecology, environment, evolution, biology, geography, zoology, ornithology, plant sciences, biodiversity conservation
var ConservationCategories = []string{
	"ecology", "environmental science", "biology", "evolution",
	"zoology", "ornithology", "botany", "mammalogy", "geography",
	"conservation biology", "biodiversity", "wildlife",
}

// ImprovedPublicationSearch searches for publications using multiple strategies
type ImprovedPublicationSearch struct {
	ParkID       string
	ParkName     string
	Country      string
	CountryISO   string
	CountryNames []string // Multilingual country names
	RegionNames  []string // GADM level 1 (province) names
	Species      []string // Latin binomial names
	KeySpecies   []string // High-profile species (CR/EN status)
}

// buildSearchQueries creates multiple search queries for OpenAlex
// Based on rwosconsindex keywords: ecology, environment, biodiversity conservation
func (s *Server) buildSearchQueries(search ImprovedPublicationSearch) []string {
	queries := []string{}

	parkNameClean := cleanSearchTerm(search.ParkName)

	// 1. Park name exact match (most specific)
	if parkNameClean != "" {
		queries = append(queries, fmt.Sprintf(`"%s"`, parkNameClean))
	}

	// 2. Park name + conservation keywords
	conservationKeywords := []string{"conservation", "biodiversity", "wildlife", "protected area"}
	if parkNameClean != "" {
		for _, kw := range conservationKeywords {
			queries = append(queries, fmt.Sprintf(`"%s" %s`, parkNameClean, kw))
		}
	}

	// 3. Park name + all country name variants
	if parkNameClean != "" {
		for _, countryName := range search.CountryNames {
			queries = append(queries, fmt.Sprintf(`"%s" %s`, parkNameClean, countryName))
		}
	}

	// 4. Park name + GADM region names (provinces)
	if parkNameClean != "" {
		for _, regionName := range search.RegionNames {
			queries = append(queries, fmt.Sprintf(`"%s" %s`, parkNameClean, regionName))
		}
	}

	// 5. Key species (CR/EN) + country - very specific
	for _, species := range search.KeySpecies {
		if len(search.CountryNames) > 0 {
			queries = append(queries, fmt.Sprintf(`"%s" %s`, species, search.CountryNames[0]))
		}
	}

	// 6. Key species + region names
	for _, species := range search.KeySpecies[:minInt(3, len(search.KeySpecies))] {
		for _, region := range search.RegionNames[:minInt(3, len(search.RegionNames))] {
			queries = append(queries, fmt.Sprintf(`"%s" %s`, species, region))
		}
	}

	// 7. All species with park name
	for _, species := range search.Species[:minInt(5, len(search.Species))] {
		queries = append(queries, fmt.Sprintf(`"%s" "%s"`, species, parkNameClean))
	}

	return queries
}

// minInt returns the minimum of two ints
func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// cleanSearchTerm prepares a term for search
func cleanSearchTerm(s string) string {
	s = strings.TrimSpace(s)
	s = strings.ReplaceAll(s, "_", " ")
	return s
}

// searchOpenAlex performs a single OpenAlex API search
func (s *Server) searchOpenAlex(ctx context.Context, query string) ([]OpenAlexWork, error) {
	baseURL := "https://api.openalex.org/works"
	params := url.Values{}
	params.Set("search", query)
	params.Set("filter", "is_oa:true,type:article")
	params.Set("sort", "publication_date:desc")
	params.Set("per-page", "25")
	params.Set("mailto", "research@5mp.globe")

	reqURL := baseURL + "?" + params.Encode()

	req, err := http.NewRequestWithContext(ctx, "GET", reqURL, nil)
	if err != nil {
		return nil, err
	}

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("OpenAlex API returned status %d", resp.StatusCode)
	}

	var result struct {
		Results []OpenAlexWork `json:"results"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}

	return result.Results, nil
}

// getParkRegionNames returns all GADM region names (level 1 + level 2) for a park
// This includes province names, district names, and variant names for comprehensive search
func (s *Server) getParkRegionNames(parkID string) []string {
	return GetAllRegionNames(parkID)
}

// getCountryNames returns multilingual country names for an ISO3 code
func getCountryNames(iso3 string) []string {
	if names, ok := MultilingualCountryNames[iso3]; ok {
		return names
	}
	return []string{iso3}
}

// RunImprovedPublicationSync runs the enhanced publication search for all parks
func (s *Server) RunImprovedPublicationSync(ctx context.Context) {
	if s.AreaStore == nil {
		return
	}

	slog.Info("Starting improved publication sync with GADM regions (level 1+2) and multilingual names")

	// Ensure GADM regions are loaded
	LoadParkRegions()

	for _, area := range s.AreaStore.Areas {
		select {
		case <-ctx.Done():
			return
		default:
		}

		// Extract country code from park ID (e.g., "TCD_Zakouma" -> "TCD")
		parts := strings.Split(area.ID, "_")
		if len(parts) == 0 {
			continue
		}
		countryISO := parts[0]

		// Get species for this park
		species, keySpecies := s.getSpeciesForPark(area.ID)

		// Build search config with GADM regions (level 1 + 2) and multilingual names
		search := ImprovedPublicationSearch{
			ParkID:       area.ID,
			ParkName:     area.Name,
			Country:      area.Country,
			CountryISO:   countryISO,
			CountryNames: getCountryNames(countryISO),
			RegionNames:  s.getParkRegionNames(area.ID), // Now includes level 1+2 regions
			Species:      species,
			KeySpecies:   keySpecies,
		}

		// Run all queries
		allWorks := make(map[string]OpenAlexWork) // dedupe by ID
		queries := s.buildSearchQueries(search)

		for _, query := range queries {
			works, err := s.searchOpenAlex(ctx, query)
			if err != nil {
				slog.Debug("OpenAlex search failed", "query", query, "error", err)
				continue
			}

			for _, work := range works {
				if _, exists := allWorks[work.ID]; !exists {
					// Filter for relevance to conservation
					if s.isRelevantToConservation(work, search) {
						allWorks[work.ID] = work
					}
				}
			}

			// Rate limiting - be nice to OpenAlex
			time.Sleep(100 * time.Millisecond)
		}

		// Store results and create notifications for new ones
		for _, work := range allWorks {
			isNew, err := s.storePublicationIfNew(ctx, area.ID, work)
			if err != nil {
				slog.Error("Failed to store publication", "error", err)
				continue
			}

			if isNew {
				s.createPublicationNotificationV2(ctx, area.ID, area.Name, work)
			}
		}

		slog.Debug("Publication sync completed for park", "park", area.ID, "found", len(allWorks))
	}

	slog.Info("Improved publication sync completed")
}

// getSpeciesForPark returns species and key species (CR/EN) for a park
func (s *Server) getSpeciesForPark(parkID string) ([]string, []string) {
	var species []string
	var keySpecies []string

	rows, err := s.DB.Query(`
		SELECT binomial, status FROM park_species 
		WHERE park_id = ?
		LIMIT 50
	`, parkID)
	if err != nil {
		return species, keySpecies
	}
	defer rows.Close()

	for rows.Next() {
		var binomial, status string
		if err := rows.Scan(&binomial, &status); err == nil {
			species = append(species, binomial)
			if status == "CR" || status == "EN" {
				keySpecies = append(keySpecies, binomial)
			}
		}
	}

	return species, keySpecies
}

// isRelevantToConservation checks if a work is relevant to conservation
func (s *Server) isRelevantToConservation(work OpenAlexWork, search ImprovedPublicationSearch) bool {
	titleLower := strings.ToLower(work.Title)

	// Check for park name in title
	parkNameLower := strings.ToLower(search.ParkName)
	if strings.Contains(titleLower, parkNameLower) {
		return true
	}

	// Check for conservation keywords
	conservationKeywords := []string{
		"conservation", "biodiversity", "wildlife", "protected area",
		"ecology", "ecosystem", "habitat", "endangered", "species",
		"national park", "game reserve", "forest", "savanna",
	}
	for _, kw := range conservationKeywords {
		if strings.Contains(titleLower, kw) {
			return true
		}
	}

	// Check for species names in title
	for _, sp := range search.Species {
		if strings.Contains(titleLower, strings.ToLower(sp)) {
			return true
		}
	}

	// Check for country names
	for _, country := range search.CountryNames {
		if strings.Contains(titleLower, strings.ToLower(country)) {
			return true
		}
	}

	return false
}

// storePublicationIfNew stores a publication and returns true if it's new
func (s *Server) storePublicationIfNew(ctx context.Context, parkID string, work OpenAlexWork) (bool, error) {
	// Check if exists by OpenAlex ID
	var exists int
	err := s.DB.QueryRowContext(ctx, "SELECT 1 FROM pa_publications WHERE openalex_id = ?", work.ID).Scan(&exists)
	if err == nil {
		return false, nil // Already exists
	}

	// Extract year from publication date
	year := 0
	if work.PublicationYear > 0 {
		year = work.PublicationYear
	}

	// Store new publication
	authors := ""
	for i, auth := range work.Authorships {
		if i > 0 {
			authors += ", "
		}
		authors += auth.Author.DisplayName
		if i >= 4 {
			authors += " et al."
			break
		}
	}

	_, err = s.DB.ExecContext(ctx, `
		INSERT INTO pa_publications (pa_id, openalex_id, title, authors, year, doi, url, abstract, cited_by_count, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
	`, parkID, work.ID, work.Title, authors, year, work.DOI, work.ID, "", work.CitedByCount)

	return err == nil, err
}

// createPublicationNotificationV2 creates a notification for a new publication
func (s *Server) createPublicationNotificationV2(ctx context.Context, parkID, parkName string, work OpenAlexWork) {
	year := work.PublicationYear
	if year == 0 {
		year = time.Now().Year()
	}

	title := fmt.Sprintf("New Research: %s", parkName)
	message := fmt.Sprintf("New publication (%d): %s", year, work.Title)
	if len(message) > 200 {
		message = message[:197] + "..."
	}

	_, err := s.DB.ExecContext(ctx, `
		INSERT INTO notifications (park_id, notification_type, title, message, link, created_at)
		VALUES (?, 'new_publication', ?, ?, ?, datetime('now'))
	`, parkID, title, message, work.ID)

	if err != nil {
		slog.Error("Failed to create publication notification", "error", err)
	}
}
