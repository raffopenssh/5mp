"""
Webshare proxy integration for reliable proxy access.
"""
import os
import json
import requests
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
TOKEN_FILE = BASE_DIR / ".secrets" / "webshare_token"
CACHE_FILE = BASE_DIR / "data" / "proxy_cache" / "webshare_proxies.json"

def get_webshare_proxies():
    """Fetch proxies from Webshare API."""
    if not TOKEN_FILE.exists():
        return []
    
    with open(TOKEN_FILE) as f:
        token = f.read().strip()
    
    # Check cache first
    if CACHE_FILE.exists():
        import time
        if time.time() - os.path.getmtime(CACHE_FILE) < 3600:  # 1 hour cache
            with open(CACHE_FILE) as f:
                return json.load(f)
    
    # Fetch from API
    try:
        headers = {"Authorization": f"Token {token}"}
        resp = requests.get(
            "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=10",
            headers=headers,
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        
        proxies = []
        for p in data.get("results", []):
            if p.get("valid"):
                proxies.append({
                    "host": p["proxy_address"],
                    "port": p["port"],
                    "username": p["username"],
                    "password": p["password"],
                    "country": p.get("country_code", ""),
                    "city": p.get("city_name", "")
                })
        
        # Cache results
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, 'w') as f:
            json.dump(proxies, f)
        
        return proxies
    except Exception as e:
        print(f"Warning: Could not fetch Webshare proxies: {e}")
        return []

def get_proxy_dict(proxy):
    """Convert proxy dict to requests-compatible format."""
    if proxy.get("username"):
        proxy_url = f"http://{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}"
    else:
        proxy_url = f"http://{proxy['host']}:{proxy['port']}"
    
    return {
        "http": proxy_url,
        "https": proxy_url
    }

def get_working_proxy(test_url="https://firms.modaps.eosdis.nasa.gov"):
    """Get a working Webshare proxy."""
    proxies = get_webshare_proxies()
    
    for proxy in proxies:
        try:
            proxy_dict = get_proxy_dict(proxy)
            resp = requests.get(test_url, proxies=proxy_dict, timeout=10)
            if resp.status_code < 400:
                return proxy_dict
        except:
            continue
    
    return None

if __name__ == "__main__":
    print("Fetching Webshare proxies...")
    proxies = get_webshare_proxies()
    print(f"\nFound {len(proxies)} proxies:")
    for p in proxies:
        print(f"  {p['host']}:{p['port']} ({p['city']}, {p['country']})")
