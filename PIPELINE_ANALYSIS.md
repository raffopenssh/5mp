# Fire Pipeline Architecture Analysis

## Historical Context (Until Feb 23, 2026)

### Old System (Working with Proxies)

**Script:** `scripts/fire_nrt/download_nrt.py`  
**Cron:** Likely ran via some wrapper script  
**Proxies:** Used from `scripts/fire_nrt/config.py`

**How it worked:**
```python
# From scripts/fire_nrt/config.py
PROXIES = [
    "18.229.170.122:3128",
    "43.161.214.161:1081",
]

# From download_nrt.py
def get_working_proxy(proxies):
    for proxy in proxies[:5]:
        try:
            response = requests.get(
                test_url,
                proxies={"http": f"http://{proxy}", "https": f"http://{proxy}"},
                timeout=10
            )
            if response.ok:
                logger.info(f"Using proxy: {proxy}")
                return proxy
        except:
            continue
    return None
```

**Last successful run:** Feb 23, 2026 03:00 UTC
```
2026-02-23 03:00:04 - INFO - Using proxy: 89.208.85.78:443
2026-02-23 03:00:04 - INFO - Downloading fires for 162 parks with 50.0km buffer
```

**Process flow:**
1. Test proxies from list
2. Use first working proxy
3. Download fires per park (162 individual API calls with 50km buffer)
4. Save to JSON files in `data/fire_nrt/`
5. Run incremental trajectory analysis
6. Load to database
7. Update narratives

---

## New System (Feb 24+, NO PROXIES)

### Current System (Failing)

**Script:** `scripts/daily_fire_update.py`  
**Cron:** `0 3 * * * python3 scripts/daily_fire_update.py`  
**Proxies:** **NONE** - direct connection only

**How it tries to work:**
```python
NASA_API_KEY = "REDACTED_FIRMS_KEY"
FIRMS_NRT_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

def download_nrt_fires(self):
    area = "-20,-35,55,40"  # Africa bbox
    url = f"{FIRMS_NRT_URL}/{NASA_API_KEY}/VIIRS_NOAA20_NRT/{area}/{self.days}"
    
    try:
        response = requests.get(url, timeout=300)  # NO PROXY!
        response.raise_for_status()
        # ...
    except Exception as e:
        log(f"Error downloading NRT fires: {e}")
        return []
```

**Failure mode (Since Feb 28):**
```
Error downloading NRT fires: HTTPSConnectionPool(host='firms.modaps.eosdis.nasa.gov', port=443): 
Max retries exceeded with url: /api/area/csv/.../VIIRS_NOAA20_NRT/-20,-35,55,40/5 
(Caused by NewConnectionError('<...>: Failed to establish a new connection: [Errno 101] Network is unreachable'))
```

**Process flow:**
1. ~~Download NRT fires (SINGLE API call for all Africa)~~ **FAILS - No network**
2. ~~Insert to fire_detections table~~ **SKIPPED - No data**
3. ~~Update raw JSON files~~ **SKIPPED - No data**
4. ~~Rebuild groups for affected parks (incremental)~~ **SKIPPED - No parks**
5. ~~Load to database (incremental)~~ **SKIPPED - No parks**
6. ~~Update narratives (incremental)~~ **SKIPPED - No parks**

---

## Key Architectural Differences

| Aspect | Old System | New System |
|--------|-----------|------------|
| **Proxy support** | Yes, tested multiple | None |
| **API calls** | 162 calls (per park) | 1 call (all Africa) |
| **Buffer zone** | 50km per park | None (bbox only) |
| **Data flow** | JSON files → analysis → DB | DB insert → rebuild → load |
| **Fire storage** | Raw JSON + DB | DB only (fire_detections) |
| **Incremental** | Trajectory rebuild only | Full pipeline |
| **Dependencies** | `fire_nrt/config.py` | None |

---

## Data Flow Understanding

### Historic Data (One-time Build)

**Source:** `fire_archive.zip` + `data/fire_detections_2025_2026/`

**Pipeline:**
```
1. build_unified_fire_dataset.py
   └─> data/raw-fire-viirs-YYYYMMDD-YYYYMMDD/{park_id}.json
       (Raw fire points with lat/lon/date/time/frp)

2. rebuild_fire_trajectories_v5.py
   └─> data/fire_groups_v5/{park_id}.json
       (Clustered groups with trajectories)

3. load_fire_groups_to_db.py --force
   └─> feature_geometries table
       (Fire trajectory LineStrings/Points)

4. precompute_narratives_v5.py
   └─> fire_narrative_cache table
       └─> data/export/fire_narratives/{park_id}.json
```

**Result:** 179,552 fire trajectories (2020-2026) in database

### Incremental Updates (Daily)

**What SHOULD happen:**

```
1. Download NRT fires (last 5 days) from FIRMS
   └─> Insert to fire_detections table (upsert)
       └─> Update data/raw-fire-viirs-*/{park_id}.json

2. rebuild_fire_trajectories_v5.py --incremental --days 14
   └─> Read from data/raw-fire-viirs-*/{park_id}.json
   └─> Cluster recent fires (last 14 days)
   └─> Update data/fire_groups_v5/{park_id}.json
       (Existing groups MAY grow, new groups added)

3. load_fire_groups_to_db.py --incremental --days 14
   └─> Read from data/fire_groups_v5/{park_id}.json
   └─> Update feature_geometries table
       (Upsert by feature_id)

4. precompute_narratives_v5.py --incremental --days 14
   └─> Read from feature_geometries table
   └─> Update fire_narrative_cache table
```

**Key points:**
- Groups identified by STABLE ID: `{park}_{year}_grp_{hash}`
- Active fires can CONTINUE to grow (trajectory extends)
- No deletions - append/update only
- 14-day window allows for:
  - 5 days of new data from FIRMS
  - 9 days of potential group linking
  - 3-day max gap between observations

### What's Actually Happening

**Since Feb 28:**
```
1. Download NRT fires
   └─> FAILS (network unreachable)
   └─> Returns empty list

2-4. All subsequent steps SKIPPED
   └─> "No parks affected"
```

**But the system still works because:**
- Fire trajectories in database (179k records) are still valid
- Last data: Feb 27, 2026
- Frontend loads from feature_geometries table
- Narratives load from fire_narrative_cache table
- No new fires detected, but existing data fully functional

---

## Why FAOLEX Also Fails

**Script:** `srv/faolex_scraper.go`

**How it works:**
```go
func (f *FAOLEXScraper) searchLegalDocuments(params SearchParams) ([]LegalDocument, error) {
    searchURL := fmt.Sprintf(
        "https://www.fao.org/faolex/results/en/?%s",
        queryParams)
    
    resp, err := http.Get(searchURL)  // NO PROXY!
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()
    
    if resp.StatusCode != http.StatusOK {
        return nil, fmt.Errorf("FAOLEX returned status %d", resp.StatusCode)
    }
    // ...
}
```

**Error:**
```
ERROR FAOLEX search failed country=CIV error="FAOLEX returned status 503"
```

**Two possibilities:**
1. FAOLEX service genuinely down (503 Service Unavailable)
2. exe.dev VM IP blocked/rate-limited (returns 503)

**Most likely:** IP blocking - FAO servers detecting automated scraping

---

## Solution Required

### 1. Add Proxy Support to daily_fire_update.py

**Fetch fresh proxies from GitHub:**
- https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt
- https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt
- https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt

**Test proxies before use:**
```python
def get_working_proxy(proxies):
    for proxy in proxies:
        try:
            test_url = "https://firms.modaps.eosdis.nasa.gov"
            response = requests.get(
                test_url,
                proxies={
                    "http": f"http://{proxy}",
                    "https": f"http://{proxy}"
                },
                timeout=10
            )
            if response.status_code < 400:
                return proxy
        except:
            continue
    return None
```

**Use in downloads:**
```python
proxy = get_working_proxy(proxies)
if proxy:
    proxies_dict = {
        "http": f"http://{proxy}",
        "https": f"http://{proxy}"
    }
    response = requests.get(url, proxies=proxies_dict, timeout=300)
else:
    # Fallback to direct connection
    response = requests.get(url, timeout=300)
```

### 2. Add Proxy Support to faolex_scraper.go

**Similar approach in Go:**
```go
import (
    "net/http"
    "net/url"
)

func getWorkingProxy(proxies []string) *url.URL {
    for _, proxyStr := range proxies {
        proxyURL, err := url.Parse("http://" + proxyStr)
        if err != nil {
            continue
        }
        
        // Test proxy
        client := &http.Client{
            Transport: &http.Transport{Proxy: http.ProxyURL(proxyURL)},
            Timeout: 10 * time.Second,
        }
        
        resp, err := client.Get("https://www.fao.org/faolex/en/")
        if err == nil && resp.StatusCode < 400 {
            resp.Body.Close()
            return proxyURL
        }
    }
    return nil
}
```

### 3. Proxy List Management

**Create shared proxy configuration:**
```python
# scripts/proxy_config.py
import requests
import random

PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
]

def fetch_fresh_proxies():
    """Fetch and test proxies from GitHub."""
    all_proxies = []
    for source in PROXY_SOURCES:
        try:
            resp = requests.get(source, timeout=30)
            proxies = [p.strip() for p in resp.text.split('\n') if p.strip()]
            all_proxies.extend(proxies)
        except:
            pass
    return list(set(all_proxies))  # Deduplicate

def test_proxy(proxy, test_url="https://firms.modaps.eosdis.nasa.gov"):
    """Test if proxy works."""
    try:
        response = requests.get(
            test_url,
            proxies={"http": f"http://{proxy}", "https": f"http://{proxy}"},
            timeout=10
        )
        return response.status_code < 400
    except:
        return False

def get_working_proxies(count=10):
    """Get N working proxies."""
    fresh_proxies = fetch_fresh_proxies()
    random.shuffle(fresh_proxies)
    
    working = []
    for proxy in fresh_proxies[:50]:  # Test first 50
        if test_proxy(proxy):
            working.append(proxy)
            if len(working) >= count:
                break
    return working
```

---

## Testing Plan

### 1. Test Proxy Fetching
```bash
python3 -c "from scripts.proxy_config import get_working_proxies; print(get_working_proxies(5))"
```

### 2. Test Fire Download
```bash
python3 scripts/daily_fire_update.py --days 2  # Small test
```

### 3. Test FAOLEX
```bash
curl -X POST "http://localhost:8000/api/admin/trigger-faolex-sync?pwd=test2026"
# Check logs/faolex_sync_YYYYMMDD.log
```

### 4. Monitor Logs
```bash
tail -f logs/daily_fire.log
tail -f logs/faolex_sync_*.log
journalctl -u 5mp.service -f
```

---

## Summary

**What worked until Feb 23:**
- Old fire_nrt system with proxy support
- Proxies: `89.208.85.78:443`, `18.229.170.122:3128`, etc.
- Downloaded fires per park with 50km buffer
- Used v2/v3/v4 trajectory analysis

**What changed on Feb 24:**
- Switched to new `daily_fire_update.py` script
- NO proxy support implemented
- Single API call for all Africa
- Uses v5 trajectory analysis
- Proxies from old config.py NOT used

**What's failing since Feb 28:**
- Fire NRT downloads: Network unreachable (no proxies)
- FAOLEX scraper: HTTP 503 (likely IP blocked, no proxies)

**Why system still works:**
- Fire data current through Feb 27 (sufficient)
- Uses pipeline-based architecture (pre-built trajectories)
- Publications syncing successfully (OpenAlex API works)
- All core features functional with existing data

**Fix needed:**
1. Add proxy support to `daily_fire_update.py`
2. Add proxy support to `faolex_scraper.go`
3. Fetch fresh proxies from GitHub sources
4. Test proxies before use
5. Rotate/fallback if proxy fails

**Priority:** Medium - system functional without fresh data, but should fix for completeness
