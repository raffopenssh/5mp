#!/usr/bin/env python3
"""
Proxy Manager - Fetch and test working proxies from GitHub sources.

Provides a shared proxy management system for scripts that need to bypass
IP blocking or rate limiting.

Usage:
    from scripts.proxy_manager import ProxyManager
    
    pm = ProxyManager()
    proxy = pm.get_working_proxy(test_url="https://firms.modaps.eosdis.nasa.gov")
    
    if proxy:
        response = requests.get(url, proxies=proxy.as_dict(), timeout=30)
"""

import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# GitHub proxy list sources (updated frequently)
PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
]

# Cache file for tested proxies
CACHE_DIR = Path(__file__).parent.parent / "data" / "proxy_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = CACHE_DIR / "working_proxies.json"

# Cache expiry (refresh after 6 hours)
CACHE_EXPIRY_HOURS = 6


@dataclass
class Proxy:
    """Proxy with host:port and optional authentication."""
    host: str
    port: int
    protocol: str = "http"
    username: Optional[str] = None
    password: Optional[str] = None
    last_tested: Optional[datetime] = None
    success_count: int = 0
    fail_count: int = 0
    
    @classmethod
    def from_string(cls, proxy_str: str) -> Optional['Proxy']:
        """Parse proxy from string (host:port or protocol://host:port)."""
        try:
            # Remove protocol if present
            if '://' in proxy_str:
                protocol, rest = proxy_str.split('://', 1)
                if protocol not in ['http', 'https', 'socks5', 'socks4']:
                    return None
            else:
                protocol = 'http'
                rest = proxy_str
            
            # Parse host:port
            if ':' in rest:
                host, port = rest.rsplit(':', 1)
                port = int(port)
            else:
                return None
            
            return cls(host=host, port=port, protocol=protocol)
        except (ValueError, IndexError):
            return None
    
    def as_dict(self) -> Dict[str, str]:
        """Convert to requests-compatible proxy dict."""
        proxy_url = f"{self.protocol}://{self.host}:{self.port}"
        return {
            "http": proxy_url,
            "https": proxy_url
        }
    
    def __str__(self) -> str:
        return f"{self.host}:{self.port}"
    
    def to_json(self) -> dict:
        """Serialize for caching."""
        return {
            "host": self.host,
            "port": self.port,
            "protocol": self.protocol,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "last_tested": self.last_tested.isoformat() if self.last_tested else None
        }
    
    @classmethod
    def from_json(cls, data: dict) -> 'Proxy':
        """Deserialize from cache."""
        last_tested = None
        if data.get('last_tested'):
            try:
                last_tested = datetime.fromisoformat(data['last_tested'])
            except:
                pass
        
        return cls(
            host=data['host'],
            port=data['port'],
            protocol=data.get('protocol', 'http'),
            success_count=data.get('success_count', 0),
            fail_count=data.get('fail_count', 0),
            last_tested=last_tested
        )


class ProxyManager:
    """Manager for fetching, testing, and caching proxies."""
    
    def __init__(self, cache_file: Path = CACHE_FILE):
        self.cache_file = cache_file
        self.proxies: List[Proxy] = []
        self._load_cache()
    
    def _load_cache(self):
        """Load cached working proxies."""
        if not self.cache_file.exists():
            return
        
        try:
            with open(self.cache_file) as f:
                data = json.load(f)
            
            cache_time = datetime.fromisoformat(data.get('cached_at', ''))
            if datetime.now() - cache_time > timedelta(hours=CACHE_EXPIRY_HOURS):
                logger.info(f"Proxy cache expired (older than {CACHE_EXPIRY_HOURS}h)")
                return
            
            self.proxies = [Proxy.from_json(p) for p in data.get('proxies', [])]
            logger.info(f"Loaded {len(self.proxies)} proxies from cache")
            
        except Exception as e:
            logger.warning(f"Failed to load proxy cache: {e}")
    
    def _save_cache(self):
        """Save working proxies to cache."""
        try:
            data = {
                'cached_at': datetime.now().isoformat(),
                'proxies': [p.to_json() for p in self.proxies if p.success_count > 0]
            }
            with open(self.cache_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved {len(data['proxies'])} working proxies to cache")
        except Exception as e:
            logger.warning(f"Failed to save proxy cache: {e}")
    
    def fetch_fresh_proxies(self, max_per_source: int = 100) -> List[Proxy]:
        """Fetch fresh proxy lists from GitHub sources."""
        all_proxies = []
        
        for source in PROXY_SOURCES:
            try:
                logger.info(f"Fetching proxies from {source.split('/')[-2]}...")
                resp = requests.get(source, timeout=30)
                resp.raise_for_status()
                
                lines = resp.text.strip().split('\n')
                proxies = []
                for line in lines[:max_per_source]:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    proxy = Proxy.from_string(line)
                    if proxy:
                        proxies.append(proxy)
                
                all_proxies.extend(proxies)
                logger.info(f"  Fetched {len(proxies)} proxies")
                time.sleep(0.5)  # Be nice to GitHub
                
            except Exception as e:
                logger.warning(f"Failed to fetch from {source}: {e}")
        
        # Deduplicate by host:port
        seen = set()
        unique_proxies = []
        for proxy in all_proxies:
            key = f"{proxy.host}:{proxy.port}"
            if key not in seen:
                seen.add(key)
                unique_proxies.append(proxy)
        
        logger.info(f"Fetched {len(unique_proxies)} unique proxies from {len(PROXY_SOURCES)} sources")
        return unique_proxies
    
    def test_proxy(self, proxy: Proxy, test_url: str = "https://www.google.com", 
                   timeout: int = 10) -> bool:
        """Test if a proxy works."""
        try:
            response = requests.get(
                test_url,
                proxies=proxy.as_dict(),
                timeout=timeout,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            )
            proxy.last_tested = datetime.now()
            if response.status_code < 400:
                proxy.success_count += 1
                return True
            else:
                proxy.fail_count += 1
                return False
        except Exception as e:
            proxy.fail_count += 1
            logger.debug(f"Proxy {proxy} failed: {e}")
            return False
    
    def get_working_proxies(self, count: int = 10, test_url: str = "https://www.google.com",
                           force_refresh: bool = False) -> List[Proxy]:
        """Get N working proxies, testing them first."""
        
        # Use cache if available and not forcing refresh
        if not force_refresh and self.proxies:
            # Re-test cached proxies to ensure they still work
            working = []
            for proxy in self.proxies[:count * 2]:  # Test extra in case some fail
                if self.test_proxy(proxy, test_url):
                    working.append(proxy)
                    if len(working) >= count:
                        break
            
            if working:
                logger.info(f"Using {len(working)} cached working proxies")
                self._save_cache()  # Update success counts
                return working
        
        # Fetch fresh proxies
        logger.info("Fetching fresh proxies from GitHub...")
        fresh_proxies = self.fetch_fresh_proxies()
        
        if not fresh_proxies:
            logger.error("No proxies fetched from sources")
            return []
        
        # Shuffle and test
        random.shuffle(fresh_proxies)
        working = []
        
        logger.info(f"Testing proxies (need {count} working)...")
        for i, proxy in enumerate(fresh_proxies):
            if len(working) >= count:
                break
            
            if (i + 1) % 10 == 0:
                logger.info(f"  Tested {i + 1}/{len(fresh_proxies)}, found {len(working)} working")
            
            if self.test_proxy(proxy, test_url, timeout=8):
                working.append(proxy)
                logger.info(f"  ✓ Working: {proxy}")
            
            # Stop after testing 100 if we have enough
            if i > 100 and len(working) >= count:
                break
        
        self.proxies = working
        self._save_cache()
        
        logger.info(f"Found {len(working)} working proxies")
        return working
    
    def get_working_proxy(self, test_url: str = "https://www.google.com",
                         force_refresh: bool = False) -> Optional[Proxy]:
        """Get a single working proxy."""
        proxies = self.get_working_proxies(count=1, test_url=test_url, force_refresh=force_refresh)
        return proxies[0] if proxies else None
    
    def get_random_working_proxy(self, test_url: str = "https://www.google.com") -> Optional[Proxy]:
        """Get a random working proxy from cache or fetch new."""
        if not self.proxies:
            self.get_working_proxies(count=5, test_url=test_url)
        
        if self.proxies:
            return random.choice(self.proxies)
        return None


def main():
    """Test proxy manager."""
    import sys
    
    pm = ProxyManager()
    
    # Test for FIRMS API
    print("\n=== Testing proxies for NASA FIRMS ===")
    proxy = pm.get_working_proxy(test_url="https://firms.modaps.eosdis.nasa.gov", force_refresh=True)
    if proxy:
        print(f"✓ Working proxy: {proxy}")
        print(f"  Success: {proxy.success_count}, Fail: {proxy.fail_count}")
    else:
        print("✗ No working proxy found")
    
    # Test for FAOLEX
    print("\n=== Testing proxies for FAOLEX ===")
    proxy = pm.get_working_proxy(test_url="https://www.fao.org/faolex/en/", force_refresh=False)
    if proxy:
        print(f"✓ Working proxy: {proxy}")
    else:
        print("✗ No working proxy found")
    
    # Get multiple working proxies
    print("\n=== Getting 5 working proxies ===")
    proxies = pm.get_working_proxies(count=5, test_url="https://www.google.com")
    for i, p in enumerate(proxies, 1):
        print(f"  {i}. {p} (success: {p.success_count})")


if __name__ == "__main__":
    main()
