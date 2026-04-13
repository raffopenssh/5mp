package srv

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// WebshareProxy represents a Webshare proxy credential
type WebshareProxy struct {
	Host     string `json:"host"`
	Port     int    `json:"port"`
	Username string `json:"username"`
	Password string `json:"password"`
	Country  string `json:"country"`
	City     string `json:"city"`
}

// loadWebshareTokens reads all tokens from .secrets/webshare_tokens (multi-line)
// or falls back to .secrets/webshare_token (single legacy token).
func loadWebshareTokens() []string {
	var tokens []string

	// Prefer multi-token file
	if f, err := os.Open(".secrets/webshare_tokens"); err == nil {
		defer f.Close()
		scanner := bufio.NewScanner(f)
		for scanner.Scan() {
			t := strings.TrimSpace(scanner.Text())
			if t != "" && !strings.HasPrefix(t, "#") {
				tokens = append(tokens, t)
			}
		}
	}

	// Fallback to legacy single-token file
	if len(tokens) == 0 {
		if data, err := os.ReadFile(".secrets/webshare_token"); err == nil {
			if t := strings.TrimSpace(string(data)); t != "" {
				tokens = append(tokens, t)
			}
		}
	}

	return tokens
}

// fetchProxiesWithToken tries a single Webshare API token.
func fetchProxiesWithToken(token string) ([]WebshareProxy, bool) {
	suffix := token
	if len(suffix) > 6 {
		suffix = token[len(token)-6:]
	}

	req, err := http.NewRequest("GET", "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=10", nil)
	if err != nil {
		return nil, false
	}
	req.Header.Set("Authorization", "Token "+token)

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		slog.Debug("Webshare API request failed", "token", "..."+suffix, "error", err)
		return nil, false
	}
	defer resp.Body.Close()

	if resp.StatusCode == 401 {
		slog.Warn("Webshare token invalid (401)", "token", "..."+suffix)
		return nil, false
	}
	if resp.StatusCode == 429 {
		slog.Warn("Webshare token rate limited (429)", "token", "..."+suffix)
		return nil, false
	}
	if resp.StatusCode != 200 {
		slog.Debug("Webshare API error", "token", "..."+suffix, "status", resp.StatusCode)
		return nil, false
	}

	var apiResp struct {
		Results []struct {
			ProxyAddress string `json:"proxy_address"`
			Port         int    `json:"port"`
			Username     string `json:"username"`
			Password     string `json:"password"`
			CountryCode  string `json:"country_code"`
			CityName     string `json:"city_name"`
			Valid        bool   `json:"valid"`
		} `json:"results"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&apiResp); err != nil {
		return nil, false
	}

	var proxies []WebshareProxy
	for _, p := range apiResp.Results {
		if p.Valid {
			proxies = append(proxies, WebshareProxy{
				Host:     p.ProxyAddress,
				Port:     p.Port,
				Username: p.Username,
				Password: p.Password,
				Country:  p.CountryCode,
				City:     p.CityName,
			})
		}
	}

	if len(proxies) == 0 {
		slog.Warn("Webshare token returned 0 valid proxies (quota exhausted?)", "token", "..."+suffix)
		return nil, false
	}

	slog.Info("Webshare token OK", "token", "..."+suffix, "proxies", len(proxies))
	return proxies, true
}

// testProxyBandwidth does a HEAD request through a proxy to verify it
// can actually route traffic. Webshare returns valid credentials even when
// the account's monthly bandwidth is exhausted — the proxies just hang.
// Uses HEAD to avoid consuming bandwidth on the check itself.
func testProxyBandwidth(proxy WebshareProxy, testURL string) bool {
	parsed, err := url.Parse(proxy.ToProxyURL())
	if err != nil {
		return false
	}
	client := &http.Client{
		Transport: &http.Transport{Proxy: http.ProxyURL(parsed)},
		Timeout:   10 * time.Second,
	}
	resp, err := client.Head(testURL)
	if err != nil {
		return false
	}
	resp.Body.Close()
	return resp.StatusCode < 400
}

// GetWebshareProxies loads proxies from cache or tries all tokens with fallback.
// Each token's proxies are bandwidth-tested before being accepted.
func GetWebshareProxies() []WebshareProxy {
	cacheFile := "data/proxy_cache/webshare_proxies.json"
	verifyURL := "https://firms.modaps.eosdis.nasa.gov"

	// Check cache
	if info, err := os.Stat(cacheFile); err == nil {
		if time.Since(info.ModTime()) < time.Hour {
			if data, err := os.ReadFile(cacheFile); err == nil {
				var proxies []WebshareProxy
				if err := json.Unmarshal(data, &proxies); err == nil && len(proxies) > 0 {
					// Verify cached proxies still have bandwidth
					if testProxyBandwidth(proxies[0], verifyURL) {
						return proxies
					}
					slog.Warn("Cached Webshare proxies failed bandwidth test, re-fetching")
					os.Remove(cacheFile)
				}
			}
		}
	}

	tokens := loadWebshareTokens()
	if len(tokens) == 0 {
		slog.Debug("No Webshare tokens found")
		return nil
	}

	slog.Info("Trying Webshare tokens", "count", len(tokens))

	// Try each token until one succeeds with working bandwidth
	for _, token := range tokens {
		suffix := token
		if len(suffix) > 6 {
			suffix = token[len(token)-6:]
		}

		proxies, ok := fetchProxiesWithToken(token)
		if ok && len(proxies) > 0 {
			// Verify actual bandwidth by routing a request through the first proxy
			slog.Info("Testing proxy bandwidth", "token", "..."+suffix,
				"proxy", fmt.Sprintf("%s:%d", proxies[0].Host, proxies[0].Port))
			if !testProxyBandwidth(proxies[0], verifyURL) {
				slog.Warn("Webshare token proxies returned but BANDWIDTH EXHAUSTED", "token", "..."+suffix)
				continue // Try next token
			}
			slog.Info("Webshare bandwidth OK", "token", "..."+suffix)

			// Cache results
			os.MkdirAll(filepath.Dir(cacheFile), 0755)
			if data, err := json.MarshalIndent(proxies, "", "  "); err == nil {
				os.WriteFile(cacheFile, data, 0644)
			}
			return proxies
		}
	}

	slog.Warn("All Webshare tokens exhausted or failed")
	return nil
}

// ToProxyURL converts a WebshareProxy to a proxy URL string
func (p WebshareProxy) ToProxyURL() string {
	return fmt.Sprintf("http://%s:%s@%s:%d", p.Username, p.Password, p.Host, p.Port)
}

// GetWorkingWebshareProxy tests Webshare proxies and returns the first working one
func GetWorkingWebshareProxy(testURL string) string {
	proxies := GetWebshareProxies()
	if len(proxies) == 0 {
		return ""
	}

	slog.Info("Testing Webshare proxies", "count", len(proxies))

	for i, proxy := range proxies {
		proxyURL := proxy.ToProxyURL()
		if testProxy(proxyURL, testURL) {
			slog.Info("Found working Webshare proxy", "proxy", fmt.Sprintf("%s:%d", proxy.Host, proxy.Port), "country", proxy.Country)
			return proxyURL
		}
		if i >= 5 {
			break // Don't test all 10, just first 5
		}
	}

	return ""
}
