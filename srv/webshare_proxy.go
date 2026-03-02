package srv

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
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

// GetWebshareProxies loads proxies from cached file or API
func GetWebshareProxies() []WebshareProxy {
	cacheFile := "data/proxy_cache/webshare_proxies.json"
	
	// Check cache
	if info, err := os.Stat(cacheFile); err == nil {
		if time.Since(info.ModTime()) < time.Hour {
			if data, err := os.ReadFile(cacheFile); err == nil {
				var proxies []WebshareProxy
				if err := json.Unmarshal(data, &proxies); err == nil {
					return proxies
				}
			}
		}
	}
	
	// Fetch from API
	tokenFile := ".secrets/webshare_token"
	tokenData, err := os.ReadFile(tokenFile)
	if err != nil {
		slog.Debug("No Webshare token found", "file", tokenFile)
		return nil
	}
	
	token := strings.TrimSpace(string(tokenData))
	
	req, err := http.NewRequest("GET", "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=10", nil)
	if err != nil {
		return nil
	}
	req.Header.Set("Authorization", "Token "+token)
	
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		slog.Debug("Failed to fetch Webshare proxies", "error", err)
		return nil
	}
	defer resp.Body.Close()
	
	if resp.StatusCode != 200 {
		slog.Debug("Webshare API error", "status", resp.StatusCode)
		return nil
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
		return nil
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
	
	// Cache results
	if len(proxies) > 0 {
		os.MkdirAll(filepath.Dir(cacheFile), 0755)
		if data, err := json.MarshalIndent(proxies, "", "  "); err == nil {
			os.WriteFile(cacheFile, data, 0644)
		}
	}
	
	slog.Info("Fetched Webshare proxies", "count", len(proxies))
	return proxies
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
