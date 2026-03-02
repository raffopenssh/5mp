package srv

import (
	"context"
	"fmt"
	"log/slog"
	"regexp"
	"strings"
	"time"

	"github.com/chromedp/chromedp"
)

// FAOLEXScraperChrome uses headless Chrome to scrape FAOLEX
type FAOLEXScraperChrome struct {
	ctx       context.Context
	cancel    context.CancelFunc
	rateLimit time.Duration
}

// NewFAOLEXScraperChrome creates a new Chrome-based scraper
func NewFAOLEXScraperChrome() *FAOLEXScraperChrome {
	// Create chrome context
	opts := append(chromedp.DefaultExecAllocatorOptions[:],
		chromedp.Flag("headless", true),
		chromedp.Flag("disable-gpu", true),
		chromedp.Flag("no-sandbox", true),
		chromedp.Flag("disable-dev-shm-usage", true),
		chromedp.UserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
	)

	allocCtx, cancel := chromedp.NewExecAllocator(context.Background(), opts...)
	ctx, _ := chromedp.NewContext(allocCtx)

	return &FAOLEXScraperChrome{
		ctx:       ctx,
		cancel:    cancel,
		rateLimit: 2 * time.Second,
	}
}

// Close closes the Chrome instance
func (f *FAOLEXScraperChrome) Close() {
	if f.cancel != nil {
		f.cancel()
	}
}

// SearchFAOLEX searches FAOLEX using headless Chrome
func (f *FAOLEXScraperChrome) SearchFAOLEX(ctx context.Context, params FAOLEXSearchParams) ([]FAOLEXDocument, error) {
	// Strategy: Fetch country profile, then filter by keywords/subjects
	var searchURL string
	if params.Country != "" {
		if _, ok := ISO3ToFAOLEXCountry[params.Country]; ok {
			// Use country profile which lists all documents
			searchURL = fmt.Sprintf("https://www.fao.org/faolex/country-profiles/general-profile/en/?iso3=%s", params.Country)
		} else {
			// Fallback to simple search
			searchURL = fmt.Sprintf("https://www.fao.org/faolex/results/en/?query=%s", params.Country)
		}
	} else {
		// Generic search
		keywords := append(params.Keywords, params.Subjects...)
		query := strings.Join(keywords, " ")
		if query == "" {
			query = "conservation"
		}
		searchURL = fmt.Sprintf("https://www.fao.org/faolex/results/en/?query=%s", strings.ReplaceAll(query, " ", "+"))
	}

	slog.Debug("FAOLEX Chrome search", "url", searchURL)

	// Navigate and wait for results to load
	var htmlContent string
	var pageText string
	err := chromedp.Run(f.ctx,
		chromedp.Navigate(searchURL),
		chromedp.Sleep(6*time.Second), // Wait for JavaScript and content to load
		chromedp.Text("body", &pageText, chromedp.ByQuery),
		chromedp.OuterHTML("html", &htmlContent),
	)

	if err != nil {
		return nil, fmt.Errorf("chrome navigation failed: %w", err)
	}

	slog.Debug("FAOLEX page info", "html_bytes", len(htmlContent), "text_bytes", len(pageText))

	// Check if we got a "no results" page
	if strings.Contains(pageText, "No records found") || strings.Contains(pageText, "0 records") {
		slog.Info("FAOLEX: no results for this query")
		return []FAOLEXDocument{}, nil
	}

	// Parse results from the rendered HTML
	docs, err := f.parseSearchResults(htmlContent, params.Country)
	if err != nil {
		return nil, err
	}

	// Filter results by keywords/subjects if specified
	docs = f.filterDocuments(docs, params)

	return docs, nil
}

// parseSearchResults extracts documents from rendered HTML
func (f *FAOLEXScraperChrome) parseSearchResults(html string, countryISO string) ([]FAOLEXDocument, error) {
	var docs []FAOLEXDocument

	// After JavaScript loads, look for result items
	// Pattern 1: Look for document links in result list
	docPattern1 := regexp.MustCompile(`href="/faolex/results/details/en/c/(LEX-FAOC\d+)"[^>]*>([^<]+)</a>`)
	matches1 := docPattern1.FindAllStringSubmatch(html, -1)

	slog.Debug("FAOLEX parse", "pattern1_matches", len(matches1))

	for _, match := range matches1 {
		if len(match) >= 3 {
			docID := match[1]
			title := strings.TrimSpace(match[2])

			doc := FAOLEXDocument{
				ID:         docID,
				Title:      title,
				CountryISO: countryISO,
				URL:        fmt.Sprintf("https://www.fao.org/faolex/results/details/en/c/%s", docID),
				ScrapedAt:  time.Now(),
			}

			// Extract year from title
			yearPattern := regexp.MustCompile(`\b(19\d{2}|20\d{2})\b`)
			if yearMatch := yearPattern.FindString(title); yearMatch != "" {
				fmt.Sscanf(yearMatch, "%d", &doc.Year)
			}

			docs = append(docs, doc)
		}
	}

	// Pattern 2: Alternative format - <a class="result-title" href="...">
	docPattern2 := regexp.MustCompile(`<a[^>]*class="[^"]*result-title[^"]*"[^>]*href="/faolex/results/details/en/c/(LEX-FAOC\d+)"[^>]*>([^<]+)</a>`)
	matches2 := docPattern2.FindAllStringSubmatch(html, -1)

	slog.Debug("FAOLEX parse", "pattern2_matches", len(matches2))

	for _, match := range matches2 {
		if len(match) >= 3 {
			docID := match[1]
			// Check if already added
			duplicate := false
			for _, existing := range docs {
				if existing.ID == docID {
					duplicate = true
					break
				}
			}
			if !duplicate {
				title := strings.TrimSpace(match[2])
				doc := FAOLEXDocument{
					ID:         docID,
					Title:      title,
					CountryISO: countryISO,
					URL:        fmt.Sprintf("https://www.fao.org/faolex/results/details/en/c/%s", docID),
					ScrapedAt:  time.Now(),
				}
				yearPattern := regexp.MustCompile(`\b(19\d{2}|20\d{2})\b`)
				if yearMatch := yearPattern.FindString(title); yearMatch != "" {
					fmt.Sscanf(yearMatch, "%d", &doc.Year)
				}
				docs = append(docs, doc)
			}
		}
	}

	// Pattern 3: Look for any FAOLEX document IDs in the page
	if len(docs) == 0 {
		docPattern3 := regexp.MustCompile(`(LEX-FAOC\d+)`)
		matches3 := docPattern3.FindAllString(html, -1)
		slog.Debug("FAOLEX parse", "pattern3_matches", len(matches3))

		// Deduplicate
		seen := make(map[string]bool)
		for _, docID := range matches3 {
			if !seen[docID] {
				seen[docID] = true
				docs = append(docs, FAOLEXDocument{
					ID:         docID,
					Title:      fmt.Sprintf("Document %s", docID),
					CountryISO: countryISO,
					URL:        fmt.Sprintf("https://www.fao.org/faolex/results/details/en/c/%s", docID),
					ScrapedAt:  time.Now(),
				})
			}
		}
	}

	return docs, nil
}

// filterDocuments filters documents by keywords and subjects
func (f *FAOLEXScraperChrome) filterDocuments(docs []FAOLEXDocument, params FAOLEXSearchParams) []FAOLEXDocument {
	// If no filters, return all
	if len(params.Keywords) == 0 && len(params.Subjects) == 0 && params.YearFrom == 0 && params.YearTo == 0 {
		return docs
	}

	filtered := []FAOLEXDocument{}
	for _, doc := range docs {
		match := true

		// Year filter
		if params.YearFrom > 0 && doc.Year > 0 && doc.Year < params.YearFrom {
			match = false
		}
		if params.YearTo > 0 && doc.Year > 0 && doc.Year > params.YearTo {
			match = false
		}

		// Keyword filter (must match at least one keyword)
		if len(params.Keywords) > 0 {
			keywordMatch := false
			titleLower := strings.ToLower(doc.Title)
			for _, keyword := range params.Keywords {
				if strings.Contains(titleLower, strings.ToLower(keyword)) {
					keywordMatch = true
					break
				}
			}
			if !keywordMatch {
				match = false
			}
		}

		// Subject filter (check if document relates to any subject)
		if len(params.Subjects) > 0 {
			subjectMatch := false
			titleLower := strings.ToLower(doc.Title)
			for _, subject := range params.Subjects {
				subjectLower := strings.ToLower(subject)
				// Check for related terms
				if strings.Contains(subjectLower, "wildlife") {
					if strings.Contains(titleLower, "wildlife") || strings.Contains(titleLower, "faune") ||
						strings.Contains(titleLower, "animal") || strings.Contains(titleLower, "hunting") ||
						strings.Contains(titleLower, "chasse") {
						subjectMatch = true
						break
					}
				} else if strings.Contains(subjectLower, "protected") || strings.Contains(subjectLower, "conservation") {
					if strings.Contains(titleLower, "protected") || strings.Contains(titleLower, "conservation") ||
						strings.Contains(titleLower, "protégé") || strings.Contains(titleLower, "park") ||
						strings.Contains(titleLower, "parc") || strings.Contains(titleLower, "reserve") ||
						strings.Contains(titleLower, "réserve") {
						subjectMatch = true
						break
					}
				} else if strings.Contains(subjectLower, "forest") {
					if strings.Contains(titleLower, "forest") || strings.Contains(titleLower, "forêt") ||
						strings.Contains(titleLower, "timber") || strings.Contains(titleLower, "bois") {
						subjectMatch = true
						break
					}
				} else if strings.Contains(titleLower, subjectLower) {
					subjectMatch = true
					break
				}
			}
			if !subjectMatch {
				match = false
			}
		}

		if match {
			filtered = append(filtered, doc)
		}
	}

	slog.Info("FAOLEX search complete", "total", len(docs), "filtered", len(filtered))
	return filtered
}
