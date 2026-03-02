# Proxy Implementation for Fire NRT and FAOLEX

## Summary

Added proxy support to fix network connectivity issues with NASA FIRMS API and FAOLEX scraper.

**Problem:**
- Fire NRT downloads failing since Feb 28: "Network unreachable" (Errno 101)
- FAOLEX scraper returning HTTP 503 (likely IP blocked)
- Both were trying direct connections without proxies

**Solution:**
- Fetch fresh proxies from GitHub sources
- Test proxies before use
- Fall back to direct connection if no proxy works

---

## Changes Made

### 1. Created `scripts/proxy_manager.py`

Comprehensive proxy management module:

```python
class ProxyManager:
    def fetch_fresh_proxies()      # Get proxies from GitHub
    def test_proxy()               # Test if proxy works
    def get_working_proxies()      # Get N working proxies
    def get_working_proxy()        # Get one working proxy
```

**Features:**
- Fetches from multiple GitHub sources
- Caches tested proxies (6-hour expiry)
- Tests against target URL before use
- Tracks success/fail counts

**Proxy sources:**
- https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt
- https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt
- https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt

### 2. Updated `scripts/daily_fire_update.py`

Added proxy support for NASA FIRMS downloads:

```python
def fetch_proxies(max_per_source=50):
    """Fetch proxies from GitHub sources."""

def test_proxy(proxy, test_url, timeout=10):
    """Test if proxy works for given URL."""

def get_working_proxy(test_url, max_test=30):
    """Get a working proxy for the target URL."""

class DailyFireUpdater:
    def download_nrt_fires(self):
        # Try with proxy first
        proxy = get_working_proxy(test_url="https://firms.modaps.eosdis.nasa.gov")
        
        if proxy:
            proxy_dict = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
            response = requests.get(url, proxies=proxy_dict, timeout=300)
        else:
            # Fallback to direct connection
            response = requests.get(url, timeout=300)
```

**Flow:**
1. Fetch proxy lists from GitHub (3 sources)
2. Test up to 30 proxies against FIRMS API
3. Use first working proxy
4. Fall back to direct connection if none work

### 3. Updated `srv/faolex_scraper.go`

Added proxy support for FAOLEX scraping:

```go
// New functions
func fetchProxies() []string
func testProxy(proxyAddr string, testURL string) bool
func getWorkingProxy(testURL string) string
func NewFAOLEXScraperWithProxy(proxyAddr string) *FAOLEXScraper

// Updated RunFAOLEXSync to use proxy
func (s *Server) RunFAOLEXSync(ctx context.Context) {
    proxy := getWorkingProxy("https://www.fao.org/faolex/en/")
    var scraper *FAOLEXScraper
    if proxy != "" {
        scraper = NewFAOLEXScraperWithProxy(proxy)
    } else {
        scraper = NewFAOLEXScraper()  // Fallback to direct
    }
    // ...
}
```

**Flow:**
1. Fetch proxies from GitHub
2. Shuffle and test up to 30
3. Use first working proxy
4. Create HTTP client with proxy transport
5. Fall back to direct if none work

---

## Testing

### Test Fire NRT Download

```bash
# Small test (2 days)
cd /home/exedev/5mp
python3 scripts/daily_fire_update.py --days 2

# Check logs
tail -f logs/daily_fire.log
```

**Expected output:**
```
[timestamp] Step 1: Downloading NRT fires for last 2 days...
[timestamp]   Fetching proxy lists from GitHub...
[timestamp]   Testing 30 proxies...
[timestamp]     Tested 5/30...
[timestamp]   Found working proxy: xxx.xxx.xxx.xxx:yyyy
[timestamp]   Using proxy: xxx.xxx.xxx.xxx:yyyy
[timestamp]   Downloaded NNNN fire detections
```

### Test FAOLEX Sync

```bash
# Trigger FAOLEX sync (runs in background)
curl -X POST "http://localhost:8000/api/admin/trigger-faolex-sync?pwd=test2026"

# Check logs (Sunday runs only)
tail -f logs/faolex_sync_$(date +%Y%m%d).log
journalctl -u 5mp.service -f | grep FAOLEX
```

**Expected output:**
```
INFO Starting FAOLEX legal document sync with GADM regions
INFO Fetching proxy lists from GitHub...
INFO Testing proxies count=XXX max_test=30
INFO Found working proxy proxy=xxx.xxx.xxx.xxx:yyyy
INFO Using proxy for FAOLEX proxy=http://xxx.xxx.xxx.xxx:yyyy
```

### Test Proxy Manager Standalone

```bash
python3 scripts/proxy_manager.py
```

**Expected:**
- Fetches proxies from 3 GitHub sources
- Tests them against NASA FIRMS, FAOLEX, Google
- Shows 5 working proxies

---

## How It Works

### Proxy Lifecycle

```
1. Fetch fresh proxies from GitHub
   ↓
2. Parse and deduplicate
   ↓
3. Shuffle list (distribute load)
   ↓
4. Test each proxy against target URL
   - Make HTTP request through proxy
   - Check if status code < 400
   - Timeout after 8-10 seconds
   ↓
5. Return first working proxy
   ↓
6. Use for actual API call
   ↓
7. Fallback to direct if proxy fails mid-request
```

### Proxy Testing

**Python (daily_fire_update.py):**
```python
def test_proxy(proxy, test_url, timeout=10):
    try:
        proxy_dict = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
        resp = requests.get(
            test_url, 
            proxies=proxy_dict, 
            timeout=timeout,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        return resp.status_code < 400
    except:
        return False
```

**Go (faolex_scraper.go):**
```go
func testProxy(proxyAddr string, testURL string) bool {
    proxyURL, err := url.Parse("http://" + proxyAddr)
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
```

---

## Fire Pipeline Architecture

### Historic Data (One-Time)

```
fire_archive.zip (2018-2024)
        ↓
build_unified_fire_dataset.py
        ↓
data/raw-fire-viirs-*/park.json  (Raw points)
        ↓
rebuild_fire_trajectories_v5.py
        ↓
data/fire_groups_v5/park.json  (Clustered groups)
        ↓
load_fire_groups_to_db.py --force
        ↓
feature_geometries table  (179k trajectories)
        ↓
precompute_narratives_v5.py
        ↓
fire_narrative_cache table
```

### Incremental Updates (Daily)

```
daily_fire_update.py (3am UTC cron)
        ↓
1. Download NRT fires (last 5 days) [NOW WITH PROXY]
        ↓
2. Insert to fire_detections table
        ↓
3. Update raw JSON files
        ↓
4. rebuild_fire_trajectories_v5.py --incremental --days 14
   (Re-cluster recent fires, groups can grow)
        ↓
5. load_fire_groups_to_db.py --incremental --days 14
   (Update feature_geometries)
        ↓
6. precompute_narratives_v5.py --incremental --days 14
   (Update narrative cache)
```

**Key points:**
- Groups identified by stable ID: `{park}_{year}_grp_{hash}`
- Active fires can CONTINUE to grow (trajectories extend)
- 14-day window allows linking with 3-day max gaps
- No deletions - append/upsert only

---

## Monitoring

### Daily Fire Cron

```bash
# Watch live
tail -f logs/daily_fire.log

# Check recent runs
ls -lht logs/daily_fire.log logs/fire_nrt_daily_*.log | head

# Search for proxy usage
grep -i "proxy" logs/daily_fire.log
```

**Success indicators:**
- "Found working proxy: xxx.xxx.xxx.xxx:yyyy"
- "Downloaded NNNN fire detections"
- "Inserted NNN new fire records"
- "Affected parks: NNN"

**Failure indicators:**
- "No working proxy found"
- "Error downloading NRT fires"
- "Network is unreachable"

### FAOLEX Sync (Sundays)

```bash
# Watch live (if running)
journalctl -u 5mp.service -f | grep -i faolex

# Check logs
ls -lht logs/faolex_sync_*.log | head
cat logs/faolex_sync_$(date +%Y%m%d).log
```

**Success indicators:**
- "Found working proxy"
- "Using proxy for FAOLEX"
- "FAOLEX sync completed"
- New entries in `legal_documents` table

### Database Checks

```bash
sqlite3 db.sqlite3 << 'EOF'
-- Check latest fire trajectories
SELECT MAX(end_date) as latest_fire 
FROM feature_geometries 
WHERE feature_type = 'fire_trajectory';

-- Count legal documents
SELECT COUNT(*) as legal_docs FROM legal_documents;

-- Check recent notifications
SELECT notification_type, COUNT(*), MAX(created_at)
FROM notifications
GROUP BY notification_type;
EOF
```

---

## Troubleshooting

### No Proxies Found

**Symptom:** "No working proxy found" in logs

**Possible causes:**
1. GitHub proxy sources unavailable
2. All proxies dead/blocked
3. Network connectivity to proxy sources
4. Firewall blocking proxy connections

**Solutions:**
```bash
# Test proxy sources manually
curl -I https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt

# Check if any proxies work
python3 << 'EOF'
import requests
proxy = "103.48.71.58:83"  # Example from GitHub list
try:
    r = requests.get(
        "https://www.google.com",
        proxies={"http": f"http://{proxy}", "https": f"http://{proxy}"},
        timeout=10
    )
    print(f"Proxy works: {r.status_code}")
except Exception as e:
    print(f"Proxy failed: {e}")
EOF
```

### Proxy Works But Download Fails

**Symptom:** Proxy tested successfully but actual download fails

**Possible causes:**
1. Proxy unstable (works for test, fails for large request)
2. Timeout too short for large downloads
3. FIRMS API blocking proxy IP mid-request

**Solutions:**
- Increase timeout in download_nrt_fires() (already 300s)
- Try different proxy from list
- Implement retry logic with proxy rotation

### FAOLEX Still Returns 503

**Symptom:** Using proxy but still getting 503

**Possible causes:**
1. FAOLEX service genuinely down
2. Proxy IP also blocked by FAOLEX
3. Rate limiting even with proxy

**Solutions:**
```bash
# Test FAOLEX directly
curl -I https://www.fao.org/faolex/en/

# Test through proxy
curl -x http://PROXY:PORT -I https://www.fao.org/faolex/en/

# Add retry logic with multiple proxies
```

### Cron Not Running

```bash
# Check cron status
systemctl status cron

# Verify crontab
crontab -l

# Check cron logs
grep CRON /var/log/syslog | tail -20

# Test manual run
python3 scripts/daily_fire_update.py --days 2
```

---

## Next Steps

### Immediate (After Deployment)

1. **Wait for next cron run** (3am UTC)
   - Check logs/daily_fire.log
   - Verify proxy usage and fire downloads

2. **Test FAOLEX manually** (don't wait for Sunday)
   ```bash
   curl -X POST "http://localhost:8000/api/admin/trigger-faolex-sync?pwd=test2026"
   ```

3. **Monitor for 1 week**
   - Check daily logs
   - Verify fire_detections table growing
   - Check feature_geometries updates

### Future Improvements

1. **Proxy caching**
   - Cache working proxies to file
   - Reuse cached proxies before fetching fresh
   - Refresh cache every 6 hours
   - **Status:** Implemented in proxy_manager.py, NOT used by daily_fire_update.py yet

2. **Proxy rotation**
   - Get multiple working proxies
   - Rotate on each request
   - Retry with different proxy on failure

3. **Proxy pool service**
   - Run local proxy pool manager
   - Scripts query local service for proxies
   - Centralized testing and rotation

4. **Alternative proxy sources**
   - Add paid proxy services (if budget allows)
   - Use SOCKS5 proxies for better stability
   - Consider residential proxies for less blocking

5. **Monitoring/alerting**
   - Send notification if no proxy found for 24h
   - Alert if fire data > 7 days old
   - Dashboard showing proxy success rates

---

## Configuration

### Proxy Sources (Editable)

**Python (scripts/daily_fire_update.py:35):**
```python
PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
]
```

**Go (srv/faolex_scraper.go:124):**
```go
var proxyGitHubSources = []string{
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
}
```

### Timeouts

- Proxy test timeout: 8-10 seconds
- Fire download timeout: 300 seconds (5 min)
- FAOLEX request timeout: 60 seconds

### Testing Limits

- Max proxies to test: 30 (adjustable)
- Max proxies to fetch per source: 50 (Python), 100 (Go)

---

## References

- **PIPELINE_STATUS.md**: Current system health
- **PIPELINE_ANALYSIS.md**: Detailed architecture analysis
- **docs/SCRIPTS.md**: Fire pipeline scripts reference
- **docs/FIRE_PIPELINE.md**: Fire analysis specification
- **AGENTS.md**: Background workers and cron jobs
