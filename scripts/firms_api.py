#!/usr/bin/env python3
"""Shared FIRMS area-API client.

Why this exists: the FIRMS area endpoint accepts **at most a 5-day window**
per request ("Invalid day range. Expects [1..5]"). `daily_fire_update.py`
asked for 10 days in one shot and every sensor 400'd silently for as long as
that limit has been in force -- 0 detections ingested, pipeline "degraded",
nobody paged. Any caller that wants a longer window must chunk it, so the
chunking lives here once and both the nightly cron and the overlay
`fire_gap` backfill (docs/PLAN_STUDY_AREA_ACL.md 4b) use the same code.

Two request shapes, same endpoint:
    .../csv/KEY/SOURCE/AREA/DAYS                 -> DAYS ending today
    .../csv/KEY/SOURCE/AREA/DAYS/YYYY-MM-DD      -> DAYS starting that date

Source suffix matters: NRT covers roughly the last two months, SP is the
reprocessed archive. Asking NRT for old dates returns an empty CSV (HTTP 200,
header only) -- a silent zero, not an error, which is exactly how a backfill
ends up looking "successful" while inserting nothing. pick_source() decides
by age so callers cannot get that wrong.

Quota: the map key reports transaction_limit / current_transactions per
10-minute interval (5000/10min today). budget_remaining() reads it so a
batched backfill can stop before it is throttled.
"""

import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from secrets_config import secret  # noqa: E402

BASE = "https://firms.modaps.eosdis.nasa.gov"
AREA_CSV = BASE + "/api/area/csv"
STATUS_URL = BASE + "/mapserver/mapkey_status/"

# Hard API limit; do not raise it hoping for the best -- the server 400s.
MAX_WINDOW_DAYS = 5

# Fallback only. The real NRT/SP cutover is not a fixed age and is not the same
# for the three sensors -- see availability() below. Used when the availability
# endpoint is unreachable.
NRT_MAX_AGE_DAYS = 45

SENSORS = ["VIIRS_NOAA20", "VIIRS_SNPP", "VIIRS_NOAA21"]

AFRICA_BBOX = "-20,-35,55,40"


def api_key():
    return os.environ.get("NASA_FIRMS_KEY") or secret("NASA_FIRMS_KEY")


def _as_date(d):
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


_AVAIL_CACHE = {}


def availability(force=False):
    """{'VIIRS_NOAA20_SP': (min_date, max_date), ...} from FIRMS itself.

    The NRT/SP cutover is NOT a fixed age and NOT the same across sensors.
    Measured 2026-08-06:

        VIIRS_NOAA20   SP .. 2026-05-31   NRT 2026-06-01 ..   (~2 mo of NRT)
        VIIRS_SNPP     SP .. 2026-04-27   NRT 2026-04-28 ..   (~3.5 mo)
        VIIRS_NOAA21   SP does not exist  NRT 2024-01-17 ..   (NRT only)

    Guessing this by age fails two ways, both silent-ish:
      * asking for a nonexistent product ('VIIRS_NOAA21_SP') returns HTTP 400
        'Invalid source.' -- which is at least loud;
      * asking the wrong side of a real cutover returns HTTP 200 with a
        header-only CSV, i.e. zero detections that look like "no fires".
    An AOI backfill over 2024 hit both: every NOAA21 window 400'd, and the
    SNPP/NOAA20 windows between the guessed 45-day line and the true cutover
    came back empty.

    Cached for the life of the process; the boundaries move by a day at a time.
    """
    if _AVAIL_CACHE and not force:
        return _AVAIL_CACHE
    try:
        r = requests.get(f"{BASE}/api/data_availability/csv/{api_key()}/ALL",
                         timeout=60)
        r.raise_for_status()
        for line in r.text.strip().splitlines()[1:]:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                _AVAIL_CACHE[parts[0]] = (parts[1], parts[2])
    except Exception:
        pass
    return _AVAIL_CACHE


def pick_source(sensor, start_day):
    """Full FIRMS source name for a sensor and the start of a window.

    Returns None when NEITHER product covers the date -- callers must treat
    that as "skip", not as a failure to retry: for NOAA21 before 2024-01-17 no
    amount of retrying will produce rows.
    """
    day = _as_date(start_day).isoformat()
    avail = availability()
    if avail:
        hit = [suf for suf in ("SP", "NRT")
               if sensor + "_" + suf in avail
               and avail[sensor + "_" + suf][0] <= day <= avail[sensor + "_" + suf][1]]
        if hit:
            # Prefer SP where both cover the date: it is the reprocessed
            # archive and the one our history is built from.
            return f"{sensor}_{hit[0]}"
        return None
    age = (date.today() - _as_date(start_day)).days
    return f"{sensor}_{'NRT' if age <= NRT_MAX_AGE_DAYS else 'SP'}"


def day_windows(start, end=None, size=MAX_WINDOW_DAYS):
    """Split [start, end] (inclusive) into (start_date, ndays) chunks <= size."""
    start, end = _as_date(start), _as_date(end or date.today())
    size = max(1, min(size, MAX_WINDOW_DAYS))
    out = []
    cur = start
    while cur <= end:
        n = min(size, (end - cur).days + 1)
        out.append((cur, n))
        cur += timedelta(days=n)
    return out


def parse_csv(text):
    """FIRMS CSV -> list of dicts. Tolerates the extra SP 'type' column."""
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return []
    header = lines[0].split(",")
    if "latitude" not in header:  # an error body, not a CSV
        raise RuntimeError(lines[0][:200])
    rows = []
    for line in lines[1:]:
        v = line.split(",")
        if len(v) >= len(header):
            rows.append(dict(zip(header, v)))
    return rows


_proxies = None


def _webshare():
    global _proxies
    if _proxies is None:
        try:
            from webshare_proxy import get_webshare_proxies, get_proxy_dict
            _proxies = [(p, get_proxy_dict(p)) for p in (get_webshare_proxies() or [])]
        except Exception:
            _proxies = []
    return _proxies


def fetch(source, area, start_day=None, days=1, timeout=120, log=None):
    """One area request. Returns list of dicts, or None if every path failed.

    An empty list is a legitimate answer (no detections in that window).
    """
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    url = f"{AREA_CSV}/{api_key()}/{source}/{area}/{days}"
    if start_day is not None:
        url += "/" + _as_date(start_day).isoformat()
    last = None
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return parse_csv(r.text)
    except Exception as e:
        last = e
    for p, pd in _webshare():
        try:
            r = requests.get(url, proxies=pd, timeout=timeout)
            r.raise_for_status()
            rows = parse_csv(r.text)
            if log:
                log(f"    {source}: via Webshare {p['host']}:{p['port']}")
            return rows
        except Exception as e:
            last = e
    if log:
        log(f"    {source} {start_day or 'last'}+{days}d: all paths failed: {str(last)[:100]}")
    return None


def fetch_range(sensor, area, start, end=None, log=None, sleep=0.0,
                on_window=None):
    """Fetch a sensor over an arbitrary date range, chunked to 5-day windows.

    Returns (rows, n_failed_windows). on_window(start_day, days, rows) is
    called after each successful window so callers can commit incrementally
    instead of buffering a multi-year backfill in RAM.
    """
    rows, failed = [], 0
    for start_day, n in day_windows(start, end):
        src = pick_source(sensor, start_day)
        if src is None:
            continue        # no product covers this date for this sensor
        got = fetch(src, area, start_day, n, log=log)
        if got is None:
            failed += 1
            continue
        if on_window:
            on_window(start_day, n, got)
        else:
            rows.extend(got)
        if sleep:
            time.sleep(sleep)
    return rows, failed


def key_status():
    """{'transaction_limit': 5000, 'current_transactions': N, ...} or {}."""
    try:
        r = requests.get(STATUS_URL, params={"MAP_KEY": api_key()}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def budget_remaining(reserve=200):
    """Requests we may still make this 10-minute interval, minus a reserve
    kept for the time-critical nightly cron. None if unknown."""
    s = key_status()
    try:
        return max(0, int(s["transaction_limit"]) - int(s["current_transactions"]) - reserve)
    except Exception:
        return None


if __name__ == "__main__":
    print("key status:", key_status())
    print("windows:", day_windows("2026-07-28", "2026-08-06"))
    rows = fetch(pick_source("VIIRS_NOAA20", date.today()), AFRICA_BBOX, days=1)
    print("today NOAA20:", None if rows is None else len(rows))
