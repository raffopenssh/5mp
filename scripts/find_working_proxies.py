#!/usr/bin/env python3
"""
Aggressively find working proxies for FIRMS API using parallel testing.
"""
import requests
import concurrent.futures
import time
from datetime import datetime

# Proxy sources
SOURCES = [
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",
    "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
]

TEST_URL = "https://firms.modaps.eosdis.nasa.gov"

def fetch_all_proxies():
    """Fetch proxies from all sources."""
    all_proxies = []
    for source in SOURCES:
        try:
            print(f"Fetching from {source.split('/')[2]}...")
            resp = requests.get(source, timeout=20)
            proxies = [p.strip() for p in resp.text.split('\n') if p.strip() and ':' in p and not p.startswith('#')]
            all_proxies.extend(proxies)
            print(f"  Got {len(proxies)} proxies")
        except Exception as e:
            print(f"  Failed: {e}")
    
    # Deduplicate
    unique = list(set(all_proxies))
    print(f"\nTotal unique proxies: {len(unique)}")
    return unique

def test_proxy(proxy):
    """Test if proxy works for FIRMS API."""
    try:
        proxy_dict = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
        resp = requests.get(TEST_URL, proxies=proxy_dict, timeout=8)
        if resp.status_code < 400:
            return proxy
    except:
        pass
    return None

def find_working_proxies(max_workers=50):
    """Test proxies in parallel and find working ones."""
    print(f"\n{'='*60}")
    print("TESTING PROXIES IN PARALLEL")
    print(f"{'='*60}\n")
    
    proxies = fetch_all_proxies()
    working = []
    
    print(f"\nTesting {len(proxies)} proxies with {max_workers} parallel workers...")
    print("This will take ~30-60 seconds...\n")
    
    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(test_proxy, p): p for p in proxies}
        
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            result = future.result()
            if result:
                working.append(result)
                print(f"✓ WORKING: {result} ({len(working)} found)")
            
            if (i + 1) % 100 == 0:
                print(f"  Tested {i + 1}/{len(proxies)}... ({len(working)} working so far)")
    
    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"RESULTS: {len(working)} working proxies found in {elapsed:.1f}s")
    print(f"{'='*60}\n")
    
    return working

if __name__ == "__main__":
    working = find_working_proxies()
    
    if working:
        print("Working proxies:")
        for p in working:
            print(f"  {p}")
        
        # Save to file
        cache_file = "/home/exedev/5mp/data/proxy_cache/working_proxies.txt"
        import os
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        
        with open(cache_file, 'w') as f:
            f.write(f"# Working proxies tested at {datetime.now()}\n")
            f.write(f"# Test URL: {TEST_URL}\n")
            for p in working:
                f.write(f"{p}\n")
        
        print(f"\n✓ Saved to {cache_file}")
    else:
        print("✗ No working proxies found!")
