"""Pull fresh GFW integrated deforestation alerts (GLAD-S2/RADD/GLAD-L, ~10m) for parks
with a buffer (default 100km) — catches headwaters activity OUTSIDE park boundaries
that the park-clipped Hansen pipeline misses.

Designed for GFW API rate limits: scan ONE park per day (--rotate mode, for cron).
CAR + SSD parks are prioritized; each park gets rescanned every N days.

Usage:
  python3 analysis/gfw_alerts.py --park CAF_Chinko            # scan one park
  python3 analysis/gfw_alerts.py --rotate                     # scan most-stale park (cron)
  python3 analysis/gfw_alerts.py --rotate --priority CAF_,SSD_

Output: data/gfw_alerts/{park_id}.json — 0.01-deg cell clusters (compact, git-friendly).
State:  data/gfw_alerts/state.json — last scan time + request count per park.
"""
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'scripts'))
from secrets_config import secret
import json, math, sys, time, subprocess, argparse, os, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
from cron_notify import notify_status  # noqa: E402

API_KEY = os.environ.get("GFW_API_KEY") or secret('GFW_API_KEY')
BASE = "https://data-api.globalforestwatch.org"
STATE_FILE = "data/gfw_alerts/state.json"
# Heal queue: park ids whose existing scan was assembled from silently
# truncated tiles (see TRUNCATION_FLOOR). --rotate drains this list first,
# one park per day, before the normal staleness rotation -- so a bad scan is
# repaired within days without blowing the daily API budget in one run.
# Written by scripts/gfw_find_truncated.py; entries removed after a
# successful re-scan.
HEAL_FILE = "data/gfw_alerts/heal_queue.json"

# Per-tile cache. Park scans used to write only data/gfw_alerts/{park}.json, so
# the same 0.5-deg tile was re-fetched for every park (and every AOI) that
# touched it. Caching by TILE instead of by consumer is the AOI plan's rule 2
# made concrete (docs/PLAN_AOI_OVERLAY.md §8): the AOI's ~240-tile scan is
# geography, so the overlapping parks get it for free, and a second AOI over
# the same ground costs nothing.
TILE_CACHE_DIR = "data/gfw_tiles"
TILE_TTL_DAYS = 14


def _tile_key(w, s, since):
    """Cache key: tile origin at 0.01-deg resolution + the 'since' cutoff.
    'since' is part of the key because a tile fetched for an older cutoff is a
    superset but a newer one is not, and quietly serving a short window as a
    long one would silently under-report alerts."""
    return f"{w:.2f}_{s:.2f}_{since}"


def _tile_cached(key):
    path = os.path.join(TILE_CACHE_DIR, key + ".json")
    if not os.path.exists(path):
        return None
    age_days = (time.time() - os.path.getmtime(path)) / 86400
    if age_days > TILE_TTL_DAYS:
        return None
    try:
        d = json.load(open(path))
        rows = d["rows"]
    except Exception:
        return None
    if len(rows) >= TRUNCATION_FLOOR and not d.get("complete"):
        # Stored before truncation detection existed: the API silently capped
        # one of this tile's answers. A big tile assembled BY subdivision is
        # fine and carries complete=true; an unmarked big tile is suspect.
        # Treat as a miss so it is refetched with subdivision.
        return None
    return rows


def _tile_store(key, rows):
    os.makedirs(TILE_CACHE_DIR, exist_ok=True)
    path = os.path.join(TILE_CACHE_DIR, key + ".json")
    tmp = path + ".tmp"
    # complete=True: this answer passed truncation detection (any sub-query
    # at or above TRUNCATION_FLOOR was subdivided), so a large row count here
    # is genuine, not a cap.
    json.dump({"fetched_at": datetime.datetime.now(datetime.timezone.utc)
               .strftime("%Y-%m-%dT%H:%M:%SZ"), "complete": True,
               "rows": rows}, open(tmp, "w"))
    os.replace(tmp, path)
_version = None
_nreq = 0

def _curl(url, body=None):
    """The GFW API's WAF blocks python urllib; shell out to curl."""
    global _nreq
    _nreq += 1
    cmd = ["curl", "-sL", "--max-time", "180",
           "-H", f"x-api-key: {API_KEY}",
           "-H", "Content-Type: application/json"]
    if body is not None:
        cmd += ["-X", "POST", "-d", json.dumps(body)]
    cmd.append(url)
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=200)
    return json.loads(out.stdout)

def dataset_version():
    global _version
    if _version is None:
        d = _curl(f"{BASE}/dataset/gfw_integrated_alerts/latest")
        _version = d["data"]["version"]
        print(f"gfw_integrated_alerts version: {_version}", file=sys.stderr)
    return _version

def query(sql, geom, retries=3):
    url = f"{BASE}/dataset/gfw_integrated_alerts/{dataset_version()}/query/json"
    for i in range(retries):
        try:
            d = _curl(url, {"sql": sql, "geometry": geom})
            if d.get("status") == "success":
                return d["data"]
            raise RuntimeError(str(d.get("message") or d.get("errorType") or d)[:200])
        except Exception:
            if i < retries-1:
                time.sleep(5); continue
            raise
    raise RuntimeError("query failed")

def fetch_tile(w, s, e, n, since, depth=0, use_cache=True):
    """Fetch alerts for a tile, subdividing on oversize responses.

    Only whole (depth-0) tiles are cached; subdivisions are an internal detail
    of one tile's fetch.
    """
    if use_cache and depth == 0:
        key = _tile_key(w, s, since)
        hit = _tile_cached(key)
        if hit is not None:
            return hit
        rows = _fetch_tile_uncached(w, s, e, n, since, 0)
        _tile_store(key, rows)
        return rows
    return _fetch_tile_uncached(w, s, e, n, since, depth)


# The GFW query endpoint silently truncates large answers while still
# returning status=success (observed: exactly 40,000 rows for tiles whose
# quarters sum to >100,000; older caches show a 45,000 cap). A truncated
# answer is indistinguishable from a complete one (AGENTS.md invariant 8),
# so any response at or above this floor is treated as truncated and the
# tile is subdivided, exactly like an error would be.
TRUNCATION_FLOOR = 40000


def _fetch_tile_uncached(w, s, e, n, since, depth=0):
    geom = {"type": "Polygon", "coordinates": [[
        [w, s], [e, s], [e, n], [w, n], [w, s]]]}
    sql = ("SELECT longitude, latitude, gfw_integrated_alerts__date, "
           "gfw_integrated_alerts__confidence FROM results "
           f"WHERE gfw_integrated_alerts__date >= '{since}'")
    try:
        rows = query(sql, geom)
        if len(rows) < TRUNCATION_FLOOR:
            return rows
        if depth >= 6:
            # Genuinely possible (~49 km2 of loss in a ~0.008-deg cell is
            # not) -- but if it ever happens, say so instead of freezing a
            # truncated tile as complete.
            raise RuntimeError(
                f"tile {w:.4f},{s:.4f} still returns {len(rows)} rows "
                f"(>= truncation floor {TRUNCATION_FLOOR}) at depth {depth}")
        # fall through to subdivision below
        ex = None
    except Exception as e2:
        if depth >= 6:
            raise
        if depth >= 3 and "truncation floor" not in str(e2):
            print(f"  tile {w:.2f},{s:.2f} FAILED: {e2}", file=sys.stderr)
            return []
        ex = e2
    # subdivide into 4 (oversize error, or a silently truncated success)
    mx, my = (w+e)/2, (s+n)/2
    rows = []
    for (a, b, c, d) in [(w, s, mx, my), (mx, s, e, my),
                         (w, my, mx, n), (mx, my, e, n)]:
        rows += _fetch_tile_uncached(a, b, c, d, since, depth+1)
    return rows

def bbox_of(geom):
    lons, lats = [], []
    def walk(c):
        if isinstance(c[0], (int, float)): lons.append(c[0]); lats.append(c[1])
        else:
            for x in c: walk(x)
    walk(geom["coordinates"])
    return min(lons), min(lats), max(lons), max(lats)

def cluster(alerts):
    cells = {}
    for a in alerts:
        k = (round(a["latitude"], 2), round(a["longitude"], 2))
        c = cells.setdefault(k, {"lat": k[0], "lon": k[1], "n": 0,
                                 "first": a["gfw_integrated_alerts__date"],
                                 "last": a["gfw_integrated_alerts__date"],
                                 "high_conf": 0})
        c["n"] += 1
        d = a["gfw_integrated_alerts__date"]
        if d < c["first"]: c["first"] = d
        if d > c["last"]: c["last"] = d
        if a["gfw_integrated_alerts__confidence"] in ("high", "highest"):
            c["high_conf"] += 1
    return sorted(cells.values(), key=lambda c: -c["n"])

def tiles_for_bbox(w, s, e, n, tile_deg):
    """[(w, s, e, n)] tiles covering a bbox, snapped to a global tile_deg grid
    so different consumers produce identical (and therefore shareable) keys."""
    out = []
    lat = math.floor(s / tile_deg) * tile_deg
    while lat < n:
        lon = math.floor(w / tile_deg) * tile_deg
        while lon < e:
            out.append((round(lon, 4), round(lat, 4),
                        round(lon + tile_deg, 4), round(lat + tile_deg, 4)))
            lon += tile_deg
        lat += tile_deg
    return out


def scan_park(park, buffer_km, since, tile_deg):
    global _nreq
    _nreq = 0
    w, s, e, n = bbox_of(park["geometry"])
    dbuf = buffer_km / 111.0
    w, s, e, n = w-dbuf, s-dbuf, e+dbuf, n+dbuf
    alerts = []
    for (tw, ts, te, tn) in tiles_for_bbox(w, s, e, n, tile_deg):
        alerts += fetch_tile(tw, ts, te, tn, since)
    clusters = cluster(alerts)
    out = f"data/gfw_alerts/{park['id']}.json"
    json.dump({"park_id": park["id"], "scanned_at": datetime.datetime.now(datetime.timezone.utc)
               .strftime("%Y-%m-%dT%H:%M:%SZ"), "since": since,
               "buffer_km": buffer_km, "bbox": [round(w,3), round(s,3),
               round(e,3), round(n,3)], "n_alerts": len(alerts),
               "n_requests": _nreq, "clusters": clusters},
              open(out, "w"))
    print(f"{park['id']}: {len(alerts)} alerts, {len(clusters)} cells, "
          f"{_nreq} API requests -> {out}", file=sys.stderr)
    return len(alerts)

def load_state():
    try: return json.load(open(STATE_FILE))
    except Exception: return {}

def load_heal_queue():
    try: return json.load(open(HEAL_FILE))
    except Exception: return []

def pop_heal_queue(park_id):
    q = [x for x in load_heal_queue() if x != park_id]
    json.dump(q, open(HEAL_FILE, "w"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--park", help="scan a single park by id")
    ap.add_argument("--rotate", action="store_true",
                    help="scan the most-stale priority park (for daily cron)")
    ap.add_argument("--priority", default="CAF_,SSD_",
                    help="comma-separated park-id prefixes scanned first")
    ap.add_argument("--buffer-km", type=float, default=100)
    ap.add_argument("--since", default=None,
                    help="default: 400 days back")
    ap.add_argument("--tile-deg", type=float, default=0.5)
    ap.add_argument("--no-tile-cache", action="store_true",
                    help="bypass data/gfw_tiles/ (force fresh API calls)")
    args = ap.parse_args()

    if args.no_tile_cache:
        global TILE_TTL_DAYS
        TILE_TTL_DAYS = 0

    since = args.since or (datetime.date.today() -
                           datetime.timedelta(days=400)).isoformat()
    parks = {p["id"]: p for p in
             json.load(open("data/keystones_with_boundaries.json"))
             if p.get("geometry")}

    if args.park:
        targets = [parks[args.park]]
    elif args.rotate:
        prefixes = tuple(args.priority.split(","))
        state = load_state()
        # Heal queue first: a scan known to be built from truncated tiles is
        # worse than a stale one -- it is confidently wrong. One per day.
        heal = [pid for pid in load_heal_queue() if pid in parks]
        if heal:
            print(f"heal queue: re-scanning {heal[0]} ({len(heal)} queued)",
                  file=sys.stderr)
            targets = [parks[heal[0]]]
        else:
            pri = [p for pid, p in parks.items() if pid.startswith(prefixes)]
            # least-recently-scanned first (never-scanned = highest priority)
            pri.sort(key=lambda p: state.get(p["id"], {}).get("scanned_at", ""))
            # if all priority parks scanned within 7 days, fall back to others
            cutoff = (datetime.datetime.now(datetime.timezone.utc) -
                      datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
            cand = pri[0] if pri and state.get(pri[0]["id"], {}) \
                .get("scanned_at", "") < cutoff else None
            if cand is None:
                rest = [p for pid, p in parks.items()
                        if not pid.startswith(prefixes)]
                rest.sort(key=lambda p: state.get(p["id"], {})
                          .get("scanned_at", ""))
                cand = rest[0] if rest else (pri[0] if pri else None)
            if cand is None:
                print("nothing to scan", file=sys.stderr); return
            targets = [cand]
    else:
        ap.error("need --park or --rotate")

    for p in targets:
        try:
            n = scan_park(p, args.buffer_km, since, args.tile_deg)
        except Exception as ex:  # noqa: BLE001
            notify_status("gfw_scan_failed", "GFW Alert Scan Failed",
                          f"{p['id']}: {str(ex)[:200]}")
            raise
        notify_status("gfw_scan_success", "GFW Alert Scan Complete",
                      f"{p['id']}: {n:,} integrated deforestation alerts "
                      f"since {since} ({_nreq} API requests)")
        pop_heal_queue(p["id"])
        state = load_state()
        state[p["id"]] = {"scanned_at": datetime.datetime.now(datetime.timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "n_alerts": n, "n_requests": _nreq}
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        json.dump(state, open(STATE_FILE, "w"), indent=1)

if __name__ == "__main__":
    main()
