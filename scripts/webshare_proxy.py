"""
Webshare proxy integration for reliable proxy access.

Supports multiple API tokens with automatic fallback.
Tokens are read from .secrets/webshare_tokens (one per line),
falling back to .secrets/webshare_token (single legacy token).
"""
import os
import json
import requests
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
TOKENS_FILE = BASE_DIR / ".secrets" / "webshare_tokens"  # multi-token (preferred)
TOKEN_FILE = BASE_DIR / ".secrets" / "webshare_token"    # single legacy token
CACHE_FILE = BASE_DIR / "data" / "proxy_cache" / "webshare_proxies.json"


def _load_tokens():
    """Load all Webshare API tokens, newest file first."""
    tokens = []
    # Prefer multi-token file
    if TOKENS_FILE.exists():
        with open(TOKENS_FILE) as f:
            for line in f:
                t = line.strip()
                if t and not t.startswith('#'):
                    tokens.append(t)
    # Fallback to legacy single-token file
    if not tokens and TOKEN_FILE.exists():
        with open(TOKEN_FILE) as f:
            t = f.read().strip()
            if t:
                tokens.append(t)
    return tokens


def _fetch_proxies_with_token(token):
    """Fetch proxy list from Webshare API using a specific token.
    
    Returns (proxies_list, is_usable) where is_usable indicates the token
    is valid and returned results (vs rate-limited or expired).
    """
    try:
        headers = {"Authorization": f"Token {token}"}
        resp = requests.get(
            "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=10",
            headers=headers,
            timeout=30
        )
        if resp.status_code == 401:
            print(f"  Webshare token ...{token[-6:]}: INVALID (401 Unauthorized)")
            return [], False
        if resp.status_code == 429:
            print(f"  Webshare token ...{token[-6:]}: RATE LIMITED (429)")
            return [], False
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

        if not proxies:
            print(f"  Webshare token ...{token[-6:]}: 0 valid proxies (quota exhausted?)")
            return [], False

        print(f"  Webshare token ...{token[-6:]}: {len(proxies)} proxies OK")
        return proxies, True
    except Exception as e:
        print(f"  Webshare token ...{token[-6:]}: error - {e}")
        return [], False


def _test_proxy_bandwidth(proxy, test_url="https://firms.modaps.eosdis.nasa.gov", timeout=15):
    """Test that a proxy can actually route traffic (not just return credentials).

    Webshare still returns valid proxy credentials when bandwidth is exhausted,
    but the proxies will fail/hang when you try to use them.
    """
    try:
        proxy_dict = get_proxy_dict(proxy)
        resp = requests.head(test_url, proxies=proxy_dict, timeout=timeout,
                             headers={'User-Agent': 'Mozilla/5.0'})
        return resp.status_code < 400
    except Exception:
        return False


def get_webshare_proxies(verify_bandwidth=True):
    """Fetch proxies from Webshare API, trying all tokens with fallback.

    When verify_bandwidth=True (default), does a live connectivity test through
    the first proxy to confirm the account still has bandwidth remaining.
    This catches the case where Webshare returns credentials but the proxies
    are non-functional due to exhausted monthly bandwidth.
    """
    tokens = _load_tokens()
    if not tokens:
        return []

    # Check cache first (valid for any token)
    if CACHE_FILE.exists():
        import time
        age = time.time() - os.path.getmtime(CACHE_FILE)
        if age < 3600:  # 1 hour cache
            with open(CACHE_FILE) as f:
                cached = json.load(f)
                if cached:
                    if not verify_bandwidth:
                        return cached
                    # Verify cached proxies still have bandwidth
                    if _test_proxy_bandwidth(cached[0]):
                        return cached
                    print("  Cached proxies failed bandwidth test, re-fetching...")
                    # Remove stale cache
                    try:
                        os.remove(CACHE_FILE)
                    except OSError:
                        pass

    # Try each token in order until one works
    for token in tokens:
        proxies, usable = _fetch_proxies_with_token(token)
        if usable and proxies:
            if verify_bandwidth:
                # Test actual connectivity through the first proxy
                p = proxies[0]
                print(f"  Testing bandwidth via {p['host']}:{p['port']}...")
                if not _test_proxy_bandwidth(p):
                    suffix = token[-6:]
                    print(f"  Webshare token ...{suffix}: proxies returned but BANDWIDTH EXHAUSTED")
                    continue  # Try next token
                print("  Bandwidth OK")
            # Cache results
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, 'w') as f:
                json.dump(proxies, f)
            return proxies

    print("WARNING: All Webshare tokens exhausted or failed")
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
    print("Fetching Webshare proxies (multi-token fallback)...")
    tokens = _load_tokens()
    print(f"Found {len(tokens)} token(s)")
    proxies = get_webshare_proxies()
    print(f"\nGot {len(proxies)} proxies:")
    for p in proxies:
        print(f"  {p['host']}:{p['port']} ({p['city']}, {p['country']})")
