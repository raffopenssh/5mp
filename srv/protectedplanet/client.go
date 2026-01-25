// Package protectedplanet provides a client for the Protected Planet API.
// Protected Planet is the world's largest database of protected areas.
package protectedplanet

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"time"
)

const (
	baseURL    = "https://api.protectedplanet.net/v3"
	apiKey     = "dea58ea0389007e386776c4f583f4425"
	timeoutSec = 30
)

// Common errors
var (
	ErrNotFound      = errors.New("protected area not found")
	ErrRateLimited   = errors.New("rate limit exceeded")
	ErrUnauthorized  = errors.New("invalid API key")
	ErrServerError   = errors.New("server error")
	ErrInvalidWDPAID = errors.New("invalid WDPA ID")
)

// GeoJSON represents a GeoJSON geometry object.
type GeoJSON struct {
	Type        string          `json:"type"`
	Coordinates json.RawMessage `json:"coordinates"`
}

// GeoJSONFeature represents a GeoJSON feature with properties and geometry.
type GeoJSONFeature struct {
	Type       string          `json:"type"`
	Properties json.RawMessage `json:"properties,omitempty"`
	Geometry   *GeoJSON        `json:"geometry"`
}

// PA represents a Protected Area.
type PA struct {
	WDPAID       int            `json:"wdpa_id"`
	Name         string         `json:"name"`
	Country      string         `json:"country"`
	Geometry     *GeoJSONFeature `json:"geometry,omitempty"`
	AreaKm2      float64        `json:"area_km2"`
	IUCNCategory string         `json:"iucn_category"`
}

// Client is a Protected Planet API client.
type Client struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
}

// NewClient creates a new Protected Planet API client.
func NewClient() *Client {
	return &Client{
		baseURL: baseURL,
		apiKey:  apiKey,
		httpClient: &http.Client{
			Timeout: timeoutSec * time.Second,
		},
	}
}

// NewClientWithKey creates a new client with a custom API key.
func NewClientWithKey(apiKey string) *Client {
	c := NewClient()
	c.apiKey = apiKey
	return c
}

// apiResponse wraps the common API response structure.
type searchResponse struct {
	ProtectedAreas []apiPA `json:"protected_areas"`
}

type singleResponse struct {
	ProtectedArea apiPA `json:"protected_area"`
}

// apiPA is the API's protected area structure.
type apiPA struct {
	ID           int     `json:"id"`
	WDPAID       int     `json:"wdpa_id"`
	Name         string  `json:"name"`
	OriginalName string  `json:"original_name"`
	MarineArea   float64 `json:"marine_area"`
	ReportedArea string  `json:"reported_area"` // API returns string
	GISArea      string  `json:"gis_area"`      // API returns string
	Countries    []struct {
		Name    string `json:"name"`
		ISOCode string `json:"iso_3"`
	} `json:"countries"`
	IUCNCategory *struct {
		ID   int    `json:"id"`
		Name string `json:"name"`
	} `json:"iucn_category"`
	GeoJSON *GeoJSONFeature `json:"geojson,omitempty"`
}

// toPA converts an API PA to our PA struct.
func (a *apiPA) toPA() *PA {
	pa := &PA{
		WDPAID:   a.WDPAID,
		Name:     a.Name,
		Geometry: a.GeoJSON,
	}

	// Parse area from string
	if a.ReportedArea != "" {
		fmt.Sscanf(a.ReportedArea, "%f", &pa.AreaKm2)
	}

	if len(a.Countries) > 0 {
		pa.Country = a.Countries[0].Name
	}

	if a.IUCNCategory != nil {
		pa.IUCNCategory = a.IUCNCategory.Name
	}

	return pa
}

// doRequest performs an HTTP request and handles common errors.
func (c *Client) doRequest(endpoint string) ([]byte, error) {
	resp, err := c.httpClient.Get(endpoint)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	// Handle HTTP errors
	switch resp.StatusCode {
	case http.StatusOK:
		// Continue to parse body
	case http.StatusNotFound:
		return nil, ErrNotFound
	case http.StatusUnauthorized:
		return nil, ErrUnauthorized
	case http.StatusTooManyRequests:
		return nil, ErrRateLimited
	case http.StatusBadRequest:
		return nil, fmt.Errorf("bad request: check parameters")
	case http.StatusInternalServerError, http.StatusBadGateway, http.StatusServiceUnavailable:
		return nil, ErrServerError
	default:
		return nil, fmt.Errorf("unexpected status code: %d", resp.StatusCode)
	}

	var body []byte
	body = make([]byte, 0, 1024*1024) // Pre-allocate 1MB
	buf := make([]byte, 32*1024)
	for {
		n, err := resp.Body.Read(buf)
		if n > 0 {
			body = append(body, buf[:n]...)
		}
		if err != nil {
			break
		}
	}

	return body, nil
}

// SearchByName searches for protected areas by name within a country.
// The Protected Planet API requires at least one filter (country is most common).
// countryISO3 is the 3-letter ISO country code (e.g., "TZA" for Tanzania).
// If countryISO3 is empty, it returns an error.
func (c *Client) SearchByName(name string, countryISO3 string) ([]PA, error) {
	if countryISO3 == "" {
		return nil, errors.New("country ISO3 code is required for search")
	}

	endpoint := fmt.Sprintf("%s/protected_areas/search?token=%s&country=%s",
		c.baseURL, c.apiKey, url.QueryEscape(countryISO3))

	body, err := c.doRequest(endpoint)
	if err != nil {
		return nil, err
	}

	var resp searchResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	// Filter by name if provided (case-insensitive contains)
	results := make([]PA, 0)
	for _, apiPA := range resp.ProtectedAreas {
		if name == "" || containsIgnoreCase(apiPA.Name, name) {
			results = append(results, *apiPA.toPA())
		}
	}

	return results, nil
}

// SearchByCountry returns all protected areas in a country.
func (c *Client) SearchByCountry(countryISO3 string) ([]PA, error) {
	return c.SearchByName("", countryISO3)
}

// GetByWDPAID retrieves a protected area by its WDPA ID.
// The geometry is included in the response.
func (c *Client) GetByWDPAID(wdpaID int) (*PA, error) {
	if wdpaID <= 0 {
		return nil, ErrInvalidWDPAID
	}

	endpoint := fmt.Sprintf("%s/protected_areas/%d?token=%s",
		c.baseURL, wdpaID, c.apiKey)

	body, err := c.doRequest(endpoint)
	if err != nil {
		return nil, err
	}

	var resp singleResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	return resp.ProtectedArea.toPA(), nil
}

// GetGeometry retrieves just the geometry for a protected area.
// This is a convenience method that calls GetByWDPAID and extracts the geometry.
func (c *Client) GetGeometry(wdpaID int) (*GeoJSONFeature, error) {
	pa, err := c.GetByWDPAID(wdpaID)
	if err != nil {
		return nil, err
	}

	if pa.Geometry == nil {
		return nil, ErrNotFound
	}

	return pa.Geometry, nil
}

// containsIgnoreCase checks if s contains substr (case-insensitive).
func containsIgnoreCase(s, substr string) bool {
	if len(substr) == 0 {
		return true
	}
	if len(s) < len(substr) {
		return false
	}
	// Simple case-insensitive contains
	for i := 0; i <= len(s)-len(substr); i++ {
		match := true
		for j := 0; j < len(substr); j++ {
			sc := s[i+j]
			pc := substr[j]
			// Convert to lowercase
			if sc >= 'A' && sc <= 'Z' {
				sc += 32
			}
			if pc >= 'A' && pc <= 'Z' {
				pc += 32
			}
			if sc != pc {
				match = false
				break
			}
		}
		if match {
			return true
		}
	}
	return false
}
