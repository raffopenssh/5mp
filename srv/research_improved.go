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

	"srv.exe.dev/db/dbgen"
)

// ImprovedPublicationSearch searches for publications using multiple strategies
type ImprovedPublicationSearch struct {
	ParkID      string
	ParkName    string
	Country     string
	Species     []string // Latin binomial names
	KeySpecies  []string // High-profile species (CR/EN status)
}

// buildSearchQueries creates multiple search queries for OpenAlex
func (s *Server) buildSearchQueries(search ImprovedPublicationSearch) []string {
	queries := []string{}

	// 1. Park name exact match (most specific)
	parkNameClean := cleanSearchTerm(search.ParkName)
	if parkNameClean != "" {
		queries = append(queries, fmt.Sprintf(`"%s"`, parkNameClean))
	}

	// 2. Park name + country
	if parkNameClean != "" && search.Country != "" {
		countryClean := cleanSearchTerm(search.Country)
		queries = append(queries, fmt.Sprintf(`"%s" %s`, parkNameClean, countryClean))
	}

	// 3. Key species + park name (for flagship species)
	for _, species := range search.KeySpecies {
		if species != "" {
			queries = append(queries, fmt.Sprintf(`"%s" "%s"`, species, parkNameClean))
		}
	}

	// 4. Key species + country (broader search for important species)
	for _, species := range search.KeySpecies {
		if species != "" && search.Country != "" {
			queries = append(queries, fmt.Sprintf(`"%s" %s conservation`, species, search.Country))
		}
	}

	return queries
}

// cleanSearchTerm prepares a term for search
func cleanSearchTerm(s string) string {
	s = strings.TrimSpace(s)
	// Remove problematic characters but keep hyphens and apostrophes
	s = strings.ReplaceAll(s, `"`, "")
	s = strings.ReplaceAll(s, `\`, "")
	return s
}

// fetchPublicationsImproved uses multiple search strategies
func (s *Server) fetchPublicationsImproved(ctx context.Context, paID, name, country string) (int, error) {
	// Get key species (CR and EN) for this park
	keySpecies := []string{}
	rows, err := s.DB.QueryContext(ctx, `
		SELECT binomial FROM park_species 
		WHERE park_id = ? AND status IN ('CR', 'EN')
		ORDER BY CASE status WHEN 'CR' THEN 1 WHEN 'EN' THEN 2 END
		LIMIT 10
	`, paID)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var binomial string
			if rows.Scan(&binomial) == nil && binomial != "" {
				keySpecies = append(keySpecies, binomial)
			}
		}
	}

	search := ImprovedPublicationSearch{
		ParkID:     paID,
		ParkName:   name,
		Country:    country,
		KeySpecies: keySpecies,
	}

	queries := s.buildSearchQueries(search)
	
	q := dbgen.New(s.DB)
	totalCount := 0
	seenIDs := make(map[string]bool)

	// Get existing publication IDs to detect new ones
	existingIDs := make(map[string]bool)
	existingRows, _ := s.DB.QueryContext(ctx, `SELECT openalex_id FROM pa_publications WHERE pa_id = ?`, paID)
	if existingRows != nil {
		defer existingRows.Close()
		for existingRows.Next() {
			var id string
			if existingRows.Scan(&id) == nil {
				existingIDs[id] = true
			}
		}
	}

	newPublications := []struct {
		Title  string
		Year   int
		DOI    string
	}{}

	for _, query := range queries {
		if ctx.Err() != nil {
			break
		}

		works, err := s.searchOpenAlex(ctx, query, name)
		if err != nil {
			slog.Warn("OpenAlex query failed", "query", query, "error", err)
			continue
		}

		for _, work := range works {
			// Extract OpenAlex ID
			openalexID := work.ID
			if idx := strings.LastIndex(work.ID, "/"); idx >= 0 {
				openalexID = work.ID[idx+1:]
			}

			// Skip duplicates
			if seenIDs[openalexID] {
				continue
			}
			seenIDs[openalexID] = true

			// Check if this is a new publication
			isNew := !existingIDs[openalexID]

			// Extract authors
			authors := make([]string, 0, len(work.Authorships))
			for _, a := range work.Authorships {
				if a.Author.DisplayName != "" {
					authors = append(authors, a.Author.DisplayName)
				}
			}
			authorsJSON, _ := json.Marshal(authors)

			// Get URL
			workURL := work.PrimaryLocation.LandingPageURL
			if workURL == "" && work.DOI != "" {
				workURL = work.DOI
			}

			// Reconstruct abstract
			abstract := reconstructAbstract(work.AbstractInvertedIndex)

			err := q.InsertPublication(ctx, dbgen.InsertPublicationParams{
				PaID:         paID,
				OpenalexID:   openalexID,
				Title:        work.Title,
				Authors:      ptr(string(authorsJSON)),
				Year:         ptr(int64(work.PublicationYear)),
				Doi:          ptrIfNotEmpty(work.DOI),
				Url:          ptrIfNotEmpty(workURL),
				Abstract:     ptrIfNotEmpty(abstract),
				CitedByCount: ptr(int64(work.CitedByCount)),
			})
			if err == nil {
				totalCount++
				if isNew && work.PublicationYear >= time.Now().Year()-1 {
					newPublications = append(newPublications, struct {
						Title string
						Year  int
						DOI   string
					}{work.Title, work.PublicationYear, work.DOI})
				}
			}
		}

		// Rate limit between queries
		time.Sleep(500 * time.Millisecond)
	}

	// Create notifications for new recent publications
	for _, pub := range newPublications {
		s.createPublicationNotification(ctx, paID, name, pub.Title, pub.Year, pub.DOI)
	}

	// Update sync status
	q.UpsertPAPublicationSync(ctx, dbgen.UpsertPAPublicationSyncParams{
		PaID:        paID,
		ResultCount: int64(totalCount),
	})

	return totalCount, nil
}

// searchOpenAlex performs a single OpenAlex search and filters results
func (s *Server) searchOpenAlex(ctx context.Context, query, parkName string) ([]OpenAlexWork, error) {
	// Build API URL with conservation/ecology filter
	apiURL := fmt.Sprintf(
		"https://api.openalex.org/works?search=%s&filter=type:article&per_page=50&sort=publication_year:desc",
		url.QueryEscape(query),
	)

	req, err := http.NewRequestWithContext(ctx, "GET", apiURL, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "5mp-conservation-monitoring/1.0 (https://five-megapixel-conservation.exe.xyz)")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("OpenAlex API returned status %d", resp.StatusCode)
	}

	var data OpenAlexResponse
	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return nil, err
	}

	// Filter results - park name or species must appear in title/abstract
	filtered := []OpenAlexWork{}
	parkNameLower := strings.ToLower(parkName)
	parkNameShort := strings.TrimSuffix(parkNameLower, " national park")
	parkNameShort = strings.TrimSuffix(parkNameShort, " game reserve")
	parkNameShort = strings.TrimSuffix(parkNameShort, " reserve")

	for _, work := range data.Results {
		titleLower := strings.ToLower(work.Title)
		abstract := reconstructAbstract(work.AbstractInvertedIndex)
		abstractLower := strings.ToLower(abstract)

		// Check if park name appears
		if strings.Contains(titleLower, parkNameLower) ||
			strings.Contains(titleLower, parkNameShort) ||
			strings.Contains(abstractLower, parkNameLower) ||
			strings.Contains(abstractLower, parkNameShort) {
			filtered = append(filtered, work)
		}
	}

	return filtered, nil
}

// createPublicationNotification creates a notification for a new publication
func (s *Server) createPublicationNotification(ctx context.Context, paID, parkName, title string, year int, doi string) {
	// Truncate title if too long
	if len(title) > 200 {
		title = title[:197] + "..."
	}

	message := fmt.Sprintf("New publication (%d): %s", year, title)
	notifTitle := fmt.Sprintf("New Research: %s", parkName)
	refURL := ""
	if doi != "" {
		refURL = doi
	}
	
	_, err := s.DB.ExecContext(ctx, `
		INSERT INTO notifications (park_id, notification_type, title, message, reference_url, created_at)
		VALUES (?, 'new_publication', ?, ?, ?, CURRENT_TIMESTAMP)
	`, paID, notifTitle, message, refURL)
	
	if err != nil {
		slog.Warn("Failed to create publication notification", "park_id", paID, "error", err)
	}
}

// RunImprovedResearchSync runs the improved publication sync
func (s *Server) RunImprovedResearchSync(ctx context.Context) {
	if s.AreaStore == nil {
		return
	}

	q := dbgen.New(s.DB)

	// Get PAs that need syncing (never synced or stale > 7 days)
	type paInfo struct {
		ID      string
		Name    string
		Country string
	}

	var toSync []paInfo

	// First: never synced
	syncedPAs, _ := q.GetAllSyncedPAIDs(ctx)
	syncedSet := make(map[string]bool)
	for _, id := range syncedPAs {
		syncedSet[id] = true
	}

	for _, area := range s.AreaStore.Areas {
		paID := area.WDPAID
		if paID == "" {
			paID = area.ID
		}
		if !syncedSet[paID] {
			toSync = append(toSync, paInfo{ID: paID, Name: area.Name, Country: area.Country})
			if len(toSync) >= 5 {
				break
			}
		}
	}

	// If no new PAs, check for stale ones
	if len(toSync) == 0 {
		stale, _ := q.GetPAsNeedingPublicationSync(ctx, 5)
		for _, id := range stale {
			for _, area := range s.AreaStore.Areas {
				paID := area.WDPAID
				if paID == "" {
					paID = area.ID
				}
				if paID == id {
					toSync = append(toSync, paInfo{ID: paID, Name: area.Name, Country: area.Country})
					break
				}
			}
		}
	}

	for _, pa := range toSync {
		if ctx.Err() != nil {
			break
		}

		count, err := s.fetchPublicationsImproved(ctx, pa.ID, pa.Name, pa.Country)
		if err != nil {
			slog.Error("failed to fetch publications", "pa_id", pa.ID, "name", pa.Name, "error", err)
			continue
		}
		slog.Info("fetched publications (improved)", "pa_id", pa.ID, "name", pa.Name, "count", count)

		// Rate limit between parks
		time.Sleep(2 * time.Second)
	}
}
