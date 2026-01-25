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

// OpenAlexWork represents a work from the OpenAlex API.
type OpenAlexWork struct {
	ID           string `json:"id"`
	Title        string `json:"title"`
	PublicationYear int `json:"publication_year"`
	DOI          string `json:"doi"`
	CitedByCount int    `json:"cited_by_count"`
	Authorships  []struct {
		Author struct {
			DisplayName string `json:"display_name"`
		} `json:"author"`
	} `json:"authorships"`
	PrimaryLocation struct {
		LandingPageURL string `json:"landing_page_url"`
	} `json:"primary_location"`
	AbstractInvertedIndex map[string][]int `json:"abstract_inverted_index"`
}

// OpenAlexResponse is the API response wrapper.
type OpenAlexResponse struct {
	Results []OpenAlexWork `json:"results"`
	Meta    struct {
		Count int `json:"count"`
	} `json:"meta"`
}

// StartResearchWorker starts the background job for fetching publications.
func (s *Server) StartResearchWorker(ctx context.Context) {
	ticker := time.NewTicker(24 * time.Hour)
	defer ticker.Stop()

	// Run immediately on startup, then every 24 hours
	s.runResearchSync(ctx)

	for {
		select {
		case <-ctx.Done():
			slog.Info("research worker shutting down")
			return
		case <-ticker.C:
			s.runResearchSync(ctx)
		}
	}
}

// runResearchSync processes a batch of PAs.
func (s *Server) runResearchSync(ctx context.Context) {
	if s.AreaStore == nil {
		return
	}

	q := dbgen.New(s.DB)

	// Get PAs that haven't been synced yet
	syncedPAs, _ := q.GetAllSyncedPAIDs(ctx)
	syncedSet := make(map[string]bool)
	for _, id := range syncedPAs {
		syncedSet[id] = true
	}

	// Find unsycned PAs first, then stale ones
	var toSync []string
	for _, area := range s.AreaStore.Areas {
		paID := area.WDPAID
		if paID == "" {
			paID = area.ID
		}
		if !syncedSet[paID] {
			toSync = append(toSync, paID+":"+area.Name)
			if len(toSync) >= 3 { // Process 3 new PAs per run
				break
			}
		}
	}

	// If no new PAs, check for stale ones
	if len(toSync) == 0 {
		stale, _ := q.GetPAsNeedingPublicationSync(ctx, 3)
		for _, id := range stale {
			// Find name for this PA
			for _, area := range s.AreaStore.Areas {
				paID := area.WDPAID
				if paID == "" {
					paID = area.ID
				}
				if paID == id {
					toSync = append(toSync, paID+":"+area.Name)
					break
				}
			}
		}
	}

	for _, entry := range toSync {
		parts := strings.SplitN(entry, ":", 2)
		if len(parts) != 2 {
			continue
		}
		paID, name := parts[0], parts[1]

		count, err := s.fetchPublicationsForPA(ctx, paID, name)
		if err != nil {
			slog.Error("failed to fetch publications", "pa_id", paID, "name", name, "error", err)
			continue
		}
		slog.Info("fetched publications", "pa_id", paID, "name", name, "count", count)

		// Rate limit: wait between requests
		time.Sleep(2 * time.Second)
	}
}

// fetchPublicationsForPA fetches research papers for a protected area.
func (s *Server) fetchPublicationsForPA(ctx context.Context, paID, name string) (int, error) {
	// Build search query - use park name
	searchQuery := url.QueryEscape(name + " protected area conservation")
	apiURL := fmt.Sprintf(
		"https://api.openalex.org/works?search=%s&filter=type:article&per_page=25&sort=cited_by_count:desc",
		searchQuery,
	)

	req, err := http.NewRequestWithContext(ctx, "GET", apiURL, nil)
	if err != nil {
		return 0, err
	}
	req.Header.Set("User-Agent", "5mp-conservation-app/1.0 (mailto:admin@example.org)")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return 0, fmt.Errorf("OpenAlex API returned status %d", resp.StatusCode)
	}

	var data OpenAlexResponse
	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return 0, err
	}

	q := dbgen.New(s.DB)
	count := 0

	for _, work := range data.Results {
		// Extract authors
		authors := make([]string, 0, len(work.Authorships))
		for _, a := range work.Authorships {
			if a.Author.DisplayName != "" {
				authors = append(authors, a.Author.DisplayName)
			}
		}
		authorsJSON, _ := json.Marshal(authors)

		// Reconstruct abstract from inverted index
		abstract := reconstructAbstract(work.AbstractInvertedIndex)

		// Get URL
		workURL := work.PrimaryLocation.LandingPageURL
		if workURL == "" && work.DOI != "" {
			workURL = work.DOI
		}

		// Extract OpenAlex ID (just the ID part)
		openalexID := work.ID
		if idx := strings.LastIndex(work.ID, "/"); idx >= 0 {
			openalexID = work.ID[idx+1:]
		}

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
			count++
		}
	}

	// Update sync status
	q.UpsertPAPublicationSync(ctx, dbgen.UpsertPAPublicationSyncParams{
		PaID:        paID,
		ResultCount: int64(len(data.Results)),
	})

	return count, nil
}

// reconstructAbstract rebuilds abstract from OpenAlex inverted index format.
func reconstructAbstract(inverted map[string][]int) string {
	if len(inverted) == 0 {
		return ""
	}

	// Find max position
	maxPos := 0
	for _, positions := range inverted {
		for _, pos := range positions {
			if pos > maxPos {
				maxPos = pos
			}
		}
	}

	// Build word array
	words := make([]string, maxPos+1)
	for word, positions := range inverted {
		for _, pos := range positions {
			words[pos] = word
		}
	}

	// Join and truncate
	abstract := strings.Join(words, " ")
	if len(abstract) > 1000 {
		abstract = abstract[:1000] + "..."
	}
	return abstract
}

func ptr[T any](v T) *T {
	return &v
}

func ptrIfNotEmpty(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}
