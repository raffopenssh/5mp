"""Load secrets from secrets.env (gitignored; see secrets.env.example).

Usage:
    from secrets_config import secret
    key = secret('NASA_FIRMS_KEY')
    pwd = secret('ACCESS_PASSWORDS').split(',')[0]
"""
import os
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
_cache = None


def _load():
    global _cache
    if _cache is None:
        _cache = {}
        f = _BASE / 'secrets.env'
        if f.exists():
            for line in f.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    _cache[k.strip()] = v.strip()
    return _cache


def secret(key, default=''):
    """Return secret from env var, then secrets.env, then default."""
    return os.environ.get(key) or _load().get(key, default)


def app_password():
    """First app access password (for local API calls)."""
    return secret('ACCESS_PASSWORDS', 'test2026').split(',')[0]
