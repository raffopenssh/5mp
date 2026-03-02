package srv

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"math/rand"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

// FAOLEXDocument represents a legal document from FAOLEX
type FAOLEXDocument struct {
	ID           string    `json:"id"`
	Title        string    `json:"title"`
	TitleEnglish string    `json:"title_en"`
	Country      string    `json:"country"`
	CountryISO   string    `json:"country_iso"`
	Year         int       `json:"year"`
	Type         string    `json:"type"` // Law, Regulation, Policy, etc.
	Subject      []string  `json:"subject"`
	Abstract     string    `json:"abstract"`
	URL          string    `json:"url"`
	Keywords     []string  `json:"keywords"`
	ScrapedAt    time.Time `json:"scraped_at"`
}

// FAOLEXSearchParams for querying FAOLEX
type FAOLEXSearchParams struct {
	Country       string   // ISO3 country code
	Keywords      []string // Search keywords
	Subjects      []string // Subject categories
	YearFrom      int
	YearTo        int
	DocumentTypes []string
}

// FAOLEXSubjects relevant to conservation
var FAOLEXSubjects = []string{
	"Protected area",
	"Wildlife conservation",
	"National parks",
	"Forest conservation",
	"Biodiversity",
	"Natural resources",
	"Environment",
	"Hunting",
	"Fisheries",
}

// FAOLEXKeywords for searching legal documents
var FAOLEXKeywords = []string{
	"protected area", "national park", "game reserve", "wildlife",
	"conservation", "fauna", "flora", "forest", "natural resources",
	"boundary", "coordinates", "demarcation", "gazette",
}

// ISO3ToFAOLEXCountry maps ISO3 codes to FAOLEX country names
var ISO3ToFAOLEXCountry = map[string]string{
	"AGO": "Angola",
	"BEN": "Benin",
	"BWA": "Botswana",
	"BFA": "Burkina Faso",
	"BDI": "Burundi",
	"CMR": "Cameroon",
	"CAF": "Central African Republic",
	"TCD": "Chad",
	"COD": "Democratic Republic of the Congo",
	"COG": "Congo",
	"CIV": "Côte d'Ivoire",
	"DJI": "Djibouti",
	"EGY": "Egypt",
	"GNQ": "Equatorial Guinea",
	"ERI": "Eritrea",
	"SWZ": "Eswatini",
	"ETH": "Ethiopia",
	"GAB": "Gabon",
	"GMB": "Gambia",
	"GHA": "Ghana",
	"GIN": "Guinea",
	"GNB": "Guinea-Bissau",
	"KEN": "Kenya",
	"LSO": "Lesotho",
	"LBR": "Liberia",
	"LBY": "Libya",
	"MDG": "Madagascar",
	"MWI": "Malawi",
	"MLI": "Mali",
	"MRT": "Mauritania",
	"MUS": "Mauritius",
	"MAR": "Morocco",
	"MOZ": "Mozambique",
	"NAM": "Namibia",
	"NER": "Niger",
	"NGA": "Nigeria",
	"RWA": "Rwanda",
	"STP": "Sao Tome and Principe",
	"SEN": "Senegal",
	"SYC": "Seychelles",
	"SLE": "Sierra Leone",
	"SOM": "Somalia",
	"ZAF": "South Africa",
	"SSD": "South Sudan",
	"SDN": "Sudan",
	"TZA": "Tanzania",
	"TGO": "Togo",
	"TUN": "Tunisia",
	"UGA": "Uganda",
	"ZMB": "Zambia",
	"ZWE": "Zimbabwe",
}

// FAOLEXScraper handles scraping of FAOLEX database
type FAOLEXScraper struct {
	client    *http.Client
	baseURL   string
	rateLimit time.Duration
}

// Proxy sources for fetching fresh proxies (ordered by reliability)
var proxyGitHubSources = []string{
	// ProxyScrape API (reliable, updated frequently)
	"https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
	// GitHub sources (community maintained)
	"https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",
	"https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt",
	"https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
	"https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
	"https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
}

// fetchProxies gets proxies from GitHub sources
func fetchProxies() []string {
	var all []string
	client := &http.Client{Timeout: 20 * time.Second}
	
	for _, source := range proxyGitHubSources {
		resp, err := client.Get(source)
		if err != nil {
			continue
		}
		body, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil {
			continue
		}
		
		lines := strings.Split(string(body), "\n")
		for _, line := range lines {
			line = strings.TrimSpace(line)
			if line != "" && !strings.HasPrefix(line, "#") && strings.Contains(line, ":") {
				all = append(all, line)
			}
		}
	}
	
	return all
}

// testProxy checks if a proxy works
func testProxy(proxyAddr string, testURL string) bool {
	// If proxyAddr already has a scheme, use it as-is (e.g., Webshare URLs)
	var proxyURL *url.URL
	var err error
	if strings.HasPrefix(proxyAddr, "http://") || strings.HasPrefix(proxyAddr, "https://") {
		proxyURL, err = url.Parse(proxyAddr)
	} else {
		proxyURL, err = url.Parse("http://" + proxyAddr)
	}
	if err != nil {
		return false
	}
	
	client := &http.Client{
		Transport: &http.Transport{
			Proxy: http.ProxyURL(proxyURL),
		},
		Timeout: 10 * time.Second,
	}
	
	resp, err := client.Get(testURL)
	if err != nil {
		return false
	}
	resp.Body.Close()
	
	return resp.StatusCode < 400
}

// getWorkingProxy finds a working proxy from GitHub sources
func getWorkingProxy(testURL string) string {
	slog.Info("Fetching proxy lists from GitHub...")
	proxies := fetchProxies()
	
	if len(proxies) == 0 {
		slog.Warn("No proxies fetched from sources")
		return ""
	}
	
	// Shuffle proxies
	rand := rand.New(rand.NewSource(time.Now().UnixNano()))
	rand.Shuffle(len(proxies), func(i, j int) { proxies[i], proxies[j] = proxies[j], proxies[i] })
	
	slog.Info("Testing proxies", "count", len(proxies), "max_test", 30)
	
	// Test up to 30 proxies
	maxTest := 30
	if len(proxies) < maxTest {
		maxTest = len(proxies)
	}
	
	for i := 0; i < maxTest; i++ {
		if testProxy(proxies[i], testURL) {
			slog.Info("Found working proxy", "proxy", proxies[i])
			return proxies[i]
		}
		if (i+1)%5 == 0 {
			slog.Debug("Testing proxies", "tested", i+1, "max", maxTest)
		}
	}
	
	slog.Warn("No working proxy found")
	return ""
}

// NewFAOLEXScraper creates a new FAOLEX scraper
func NewFAOLEXScraper() *FAOLEXScraper {
	return NewFAOLEXScraperWithProxy("")
}

// NewFAOLEXScraperWithProxy creates a new FAOLEX scraper with optional proxy
func NewFAOLEXScraperWithProxy(proxyAddr string) *FAOLEXScraper {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{InsecureSkipVerify: false},
	}
	
	// If proxy provided, use it
	if proxyAddr != "" {
		if !strings.HasPrefix(proxyAddr, "http://") && !strings.HasPrefix(proxyAddr, "https://") {
			proxyAddr = "http://" + proxyAddr
		}
		proxyURL, err := url.Parse(proxyAddr)
		if err == nil {
			transport.Proxy = http.ProxyURL(proxyURL)
			slog.Info("Using proxy for FAOLEX", "proxy", proxyAddr)
		}
	}
	
	return &FAOLEXScraper{
		client: &http.Client{
			Transport: transport,
			Timeout:   60 * time.Second,
		},
		baseURL:   "https://www.fao.org/faolex",
		rateLimit: 2 * time.Second, // Be respectful
	}
}

// SearchFAOLEX searches for legal documents
func (f *FAOLEXScraper) SearchFAOLEX(ctx context.Context, params FAOLEXSearchParams) ([]FAOLEXDocument, error) {
	// FAOLEX uses a different search API
	// The main search endpoint
	searchURL := "https://www.fao.org/faolex/results/en/"

	// Build query parameters
	queryParams := url.Values{}

	// Country filter
	if params.Country != "" {
		if countryName, ok := ISO3ToFAOLEXCountry[params.Country]; ok {
			queryParams.Set("country", countryName)
		}
	}

	// Keywords
	if len(params.Keywords) > 0 {
		queryParams.Set("q", strings.Join(params.Keywords, " OR "))
	}

	// Subject categories
	for _, subj := range params.Subjects {
		queryParams.Add("subject", subj)
	}

	// Year range
	if params.YearFrom > 0 {
		queryParams.Set("yearFrom", fmt.Sprintf("%d", params.YearFrom))
	}
	if params.YearTo > 0 {
		queryParams.Set("yearTo", fmt.Sprintf("%d", params.YearTo))
	}

	fullURL := searchURL + "?" + queryParams.Encode()

	req, err := http.NewRequestWithContext(ctx, "GET", fullURL, nil)
	if err != nil {
		return nil, err
	}

	req.Header.Set("User-Agent", "5MP-Conservation-Monitor/1.0 (research@5mp.globe)")
	req.Header.Set("Accept", "text/html,application/xhtml+xml")

	resp, err := f.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("FAOLEX returned status %d", resp.StatusCode)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	// Parse the HTML response
	return f.parseSearchResults(string(body), params.Country)
}

// parseSearchResults extracts documents from FAOLEX search results HTML
func (f *FAOLEXScraper) parseSearchResults(html string, countryISO string) ([]FAOLEXDocument, error) {
	var docs []FAOLEXDocument

	// FAOLEX search results pattern (simplified - real implementation would use a proper HTML parser)
	// Look for document links and metadata

	// Pattern for document IDs and titles
	docPattern := regexp.MustCompile(`href="/faolex/results/details/en/c/LEX-FAOC(\d+)"[^>]*>([^<]+)</a>`)
	matches := docPattern.FindAllStringSubmatch(html, -1)

	for _, match := range matches {
		if len(match) >= 3 {
			docID := "LEX-FAOC" + match[1]
			title := strings.TrimSpace(match[2])

			doc := FAOLEXDocument{
				ID:         docID,
				Title:      title,
				CountryISO: countryISO,
				URL:        fmt.Sprintf("https://www.fao.org/faolex/results/details/en/c/%s", docID),
				ScrapedAt:  time.Now(),
			}

			// Try to extract year from title
			yearPattern := regexp.MustCompile(`\b(19\d{2}|20\d{2})\b`)
			if yearMatch := yearPattern.FindString(title); yearMatch != "" {
				fmt.Sscanf(yearMatch, "%d", &doc.Year)
			}

			docs = append(docs, doc)
		}
	}

	return docs, nil
}

// GetDocumentDetails fetches full details for a document
func (f *FAOLEXScraper) GetDocumentDetails(ctx context.Context, docID string) (*FAOLEXDocument, error) {
	detailURL := fmt.Sprintf("https://www.fao.org/faolex/results/details/en/c/%s", docID)

	req, err := http.NewRequestWithContext(ctx, "GET", detailURL, nil)
	if err != nil {
		return nil, err
	}

	req.Header.Set("User-Agent", "5MP-Conservation-Monitor/1.0 (research@5mp.globe)")

	resp, err := f.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	return f.parseDocumentDetails(string(body), docID)
}

// parseDocumentDetails extracts metadata from a document detail page
func (f *FAOLEXScraper) parseDocumentDetails(html, docID string) (*FAOLEXDocument, error) {
	doc := &FAOLEXDocument{
		ID:        docID,
		URL:       fmt.Sprintf("https://www.fao.org/faolex/results/details/en/c/%s", docID),
		ScrapedAt: time.Now(),
	}

	// Extract title
	titlePattern := regexp.MustCompile(`<h1[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</h1>`)
	if match := titlePattern.FindStringSubmatch(html); len(match) > 1 {
		doc.Title = strings.TrimSpace(match[1])
	}

	// Extract country
	countryPattern := regexp.MustCompile(`<span[^>]*class="[^"]*country[^"]*"[^>]*>([^<]+)</span>`)
	if match := countryPattern.FindStringSubmatch(html); len(match) > 1 {
		doc.Country = strings.TrimSpace(match[1])
	}

	// Extract year
	yearPattern := regexp.MustCompile(`<span[^>]*class="[^"]*year[^"]*"[^>]*>(\d{4})</span>`)
	if match := yearPattern.FindStringSubmatch(html); len(match) > 1 {
		fmt.Sscanf(match[1], "%d", &doc.Year)
	}

	// Extract abstract/description
	abstractPattern := regexp.MustCompile(`<div[^>]*class="[^"]*abstract[^"]*"[^>]*>(.*?)</div>`)
	if match := abstractPattern.FindStringSubmatch(html); len(match) > 1 {
		doc.Abstract = stripHTMLTags(match[1])
	}

	// Extract document type
	typePattern := regexp.MustCompile(`<span[^>]*class="[^"]*type[^"]*"[^>]*>([^<]+)</span>`)
	if match := typePattern.FindStringSubmatch(html); len(match) > 1 {
		doc.Type = strings.TrimSpace(match[1])
	}

	// Extract subjects/keywords
	subjectPattern := regexp.MustCompile(`<a[^>]*class="[^"]*subject[^"]*"[^>]*>([^<]+)</a>`)
	subjectMatches := subjectPattern.FindAllStringSubmatch(html, -1)
	for _, match := range subjectMatches {
		if len(match) > 1 {
			doc.Subject = append(doc.Subject, strings.TrimSpace(match[1]))
		}
	}

	return doc, nil
}

// stripHTMLTags removes HTML tags from a string
func stripHTMLTags(s string) string {
	tagPattern := regexp.MustCompile(`<[^>]*>`)
	return strings.TrimSpace(tagPattern.ReplaceAllString(s, ""))
}

// extractCountryCode extracts ISO3 country code from park ID (e.g., "TCD_Zakouma" -> "TCD")
func extractCountryCode(parkID string) string {
	parts := strings.Split(parkID, "_")
	if len(parts) > 0 {
		return parts[0]
	}
	return ""
}

// uniqueStringsFaolex returns unique strings from a slice
func uniqueStringsFaolex(strs []string) []string {
	seen := make(map[string]bool)
	result := make([]string, 0, len(strs))
	for _, s := range strs {
		if s != "" && !seen[s] {
			seen[s] = true
			result = append(result, s)
		}
	}
	return result
}

// minInt returns the minimum of two integers
func minIntFaolex(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// RunFAOLEXSync syncs legal documents for all parks
func (s *Server) RunFAOLEXSync(ctx context.Context) {
	if s.AreaStore == nil {
		return
	}

	slog.Info("Starting FAOLEX legal document sync with GADM regions")

	// Ensure GADM regions are loaded
	LoadParkRegions()

	// Try to get a working proxy for FAOLEX (Webshare first, then free proxies)
	proxy := GetWorkingWebshareProxy("https://www.fao.org/faolex/en/")
	if proxy == "" {
		slog.Info("Webshare proxies unavailable, trying free proxies")
		proxy = getWorkingProxy("https://www.fao.org/faolex/en/")
	}
	
	var scraper *FAOLEXScraper
	if proxy != "" {
		scraper = NewFAOLEXScraperWithProxy(proxy)
	} else {
		slog.Warn("No proxy found for FAOLEX, trying direct connection")
		scraper = NewFAOLEXScraper()
	}

	// Get unique countries and regions from parks
	countries := make(map[string]bool)
	parksByCountry := make(map[string][]string)
	regionsByCountry := make(map[string][]string) // GADM region names for legal search

	for _, area := range s.AreaStore.Areas {
		countryCode := extractCountryCode(area.ID)
		if countryCode != "" {
			countries[countryCode] = true
			parksByCountry[countryCode] = append(parksByCountry[countryCode], area.Name)
			
			// Add GADM region names for this park
			regionNames := GetAllRegionNames(area.ID)
			regionsByCountry[countryCode] = append(regionsByCountry[countryCode], regionNames...)
		}
	}

	for countryCode := range countries {
		select {
		case <-ctx.Done():
			return
		default:
		}

		// Search for general conservation laws
		params := FAOLEXSearchParams{
			Country:  countryCode,
			Subjects: FAOLEXSubjects[:5], // First 5 subjects
			YearFrom: 1990,
			YearTo:   time.Now().Year(),
		}

		docs, err := scraper.SearchFAOLEX(ctx, params)
		if err != nil {
			slog.Error("FAOLEX search failed", "country", countryCode, "error", err)
			time.Sleep(scraper.rateLimit)
			continue
		}

		// Also search for specific park names
		parkNames := parksByCountry[countryCode]
		for _, parkName := range parkNames {
			parkParams := FAOLEXSearchParams{
				Country:  countryCode,
				Keywords: []string{parkName, "protected area", "boundary"},
			}

			parkDocs, err := scraper.SearchFAOLEX(ctx, parkParams)
			if err == nil {
				docs = append(docs, parkDocs...)
			}
			time.Sleep(scraper.rateLimit)
		}

		// Search by GADM region names (provinces/districts) - important for governor-signed laws
		regionNames := uniqueStringsFaolex(regionsByCountry[countryCode])
		for _, regionName := range regionNames[:minIntFaolex(10, len(regionNames))] { // Limit to avoid too many requests
			regionParams := FAOLEXSearchParams{
				Country:  countryCode,
				Keywords: []string{regionName, "protected area"},
			}

			regionDocs, err := scraper.SearchFAOLEX(ctx, regionParams)
			if err == nil {
				docs = append(docs, regionDocs...)
			}
			time.Sleep(scraper.rateLimit)
		}

		// Store documents
		for _, doc := range docs {
			s.storeLegalDocument(ctx, countryCode, doc)
		}

		slog.Debug("FAOLEX sync completed for country", "country", countryCode, "found", len(docs))
		time.Sleep(scraper.rateLimit)
	}

	slog.Info("FAOLEX sync completed")
}

// storeLegalDocument stores a legal document in the database
func (s *Server) storeLegalDocument(ctx context.Context, countryCode string, doc FAOLEXDocument) {
	// Create table if not exists
	s.DB.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS legal_documents (
			id TEXT PRIMARY KEY,
			country_iso TEXT NOT NULL,
			title TEXT NOT NULL,
			title_en TEXT,
			year INTEGER,
			doc_type TEXT,
			subjects TEXT,
			abstract TEXT,
			url TEXT,
			keywords TEXT,
			scraped_at DATETIME,
			created_at DATETIME DEFAULT CURRENT_TIMESTAMP
		)
	`)

	// Check if document exists
	var exists int
	err := s.DB.QueryRowContext(ctx, "SELECT 1 FROM legal_documents WHERE id = ?", doc.ID).Scan(&exists)
	if err == nil {
		return // Already exists
	}

	// Insert new document
	subjectsJSON, _ := json.Marshal(doc.Subject)
	keywordsJSON, _ := json.Marshal(doc.Keywords)

	_, err = s.DB.ExecContext(ctx, `
		INSERT INTO legal_documents (id, country_iso, title, title_en, year, doc_type, subjects, abstract, url, keywords, scraped_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`, doc.ID, countryCode, doc.Title, doc.TitleEnglish, doc.Year, doc.Type,
		string(subjectsJSON), doc.Abstract, doc.URL, string(keywordsJSON), doc.ScrapedAt)

	if err != nil {
		slog.Error("Failed to store legal document", "id", doc.ID, "error", err)
		return
	}

	// Create notification for new legal document about protected areas
	if containsKeyword(doc.Title, []string{"protected area", "national park", "game reserve", "wildlife"}) {
		s.createLegalDocumentNotification(ctx, countryCode, doc)
	}
}

// containsKeyword checks if text contains any of the keywords
func containsKeyword(text string, keywords []string) bool {
	textLower := strings.ToLower(text)
	for _, kw := range keywords {
		if strings.Contains(textLower, strings.ToLower(kw)) {
			return true
		}
	}
	return false
}

// createLegalDocumentNotification creates a notification for relevant legal documents
func (s *Server) createLegalDocumentNotification(ctx context.Context, countryCode string, doc FAOLEXDocument) {
	title := fmt.Sprintf("New Legal Document: %s", ISO3ToFAOLEXCountry[countryCode])
	message := doc.Title
	if len(message) > 200 {
		message = message[:197] + "..."
	}

	_, err := s.DB.ExecContext(ctx, `
		INSERT INTO notifications (park_id, notification_type, title, message, link, created_at)
		VALUES (?, 'legal_document', ?, ?, ?, datetime('now'))
	`, countryCode, title, message, doc.URL)

	if err != nil {
		slog.Error("Failed to create legal document notification", "error", err)
	}
}

// HandleAPILegalDocuments returns legal documents for a park's country
func (s *Server) HandleAPILegalDocuments(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}

	countryCode := extractCountryCode(parkID)
	if countryCode == "" {
		http.Error(w, "Invalid park ID", http.StatusBadRequest)
		return
	}

	// Query legal documents
	rows, err := s.DB.Query(`
		SELECT id, title, year, doc_type, abstract, url, subjects
		FROM legal_documents
		WHERE country_iso = ?
		ORDER BY year DESC
		LIMIT 50
	`, countryCode)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	var docs []map[string]interface{}
	for rows.Next() {
		var id, title, docType, abstract, url, subjects string
		var year int
		if err := rows.Scan(&id, &title, &year, &docType, &abstract, &url, &subjects); err != nil {
			continue
		}

		var subjectsList []string
		json.Unmarshal([]byte(subjects), &subjectsList)

		docs = append(docs, map[string]interface{}{
			"id":       id,
			"title":    title,
			"year":     year,
			"type":     docType,
			"abstract": abstract,
			"url":      url,
			"subjects": subjectsList,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"country":   countryCode,
		"documents": docs,
		"count":     len(docs),
	})
}
