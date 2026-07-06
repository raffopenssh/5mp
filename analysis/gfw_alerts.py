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
import json, math, sys, time, subprocess, argparse, os, datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
from cron_notify import notify_status  # noqa: E402

API_KEY = os.environ.get("GFW_API_KEY", "REDACTED_GFW_KEY")
BASE = "https://data-api.globalforestwatch.org"
STATE_FILE = "data/gfw_alerts/state.json"
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

def fetch_tile(w, s, e, n, since, depth=0):
    """Fetch alerts for a tile, subdividing on oversize responses."""
    geom = {"type": "Polygon", "coordinates": [[
        [w, s], [e, s], [e, n], [w, n], [w, s]]]}
    sql = ("SELECT longitude, latitude, gfw_integrated_alerts__date, "
           "gfw_integrated_alerts__confidence FROM results "
           f"WHERE gfw_integrated_alerts__date >= '{since}'")
    try:
        return query(sql, geom)
    except Exception as ex:
        if depth >= 3:
            print(f"  tile {w:.2f},{s:.2f} FAILED: {ex}", file=sys.stderr)
            return []
        # subdivide into 4
        mx, my = (w+e)/2, (s+n)/2
        rows = []
        for (a, b, c, d) in [(w, s, mx, my), (mx, s, e, my),
                             (w, my, mx, n), (mx, my, e, n)]:
            rows += fetch_tile(a, b, c, d, since, depth+1)
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

def scan_park(park, buffer_km, since, tile_deg):
    global _nreq
    _nreq = 0
    w, s, e, n = bbox_of(park["geometry"])
    dbuf = buffer_km / 111.0
    w, s, e, n = w-dbuf, s-dbuf, e+dbuf, n+dbuf
    alerts = []
    lat = s
    while lat < n:
        lon = w
        while lon < e:
            alerts += fetch_tile(lon, lat, min(lon+tile_deg, e),
                                 min(lat+tile_deg, n), since)
            lon += tile_deg
        lat += tile_deg
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
    args = ap.parse_args()

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
        pri = [p for pid, p in parks.items() if pid.startswith(prefixes)]
        # least-recently-scanned first (never-scanned = highest priority)
        pri.sort(key=lambda p: state.get(p["id"], {}).get("scanned_at", ""))
        # if all priority parks scanned within 7 days, fall back to other parks
        cutoff = (datetime.datetime.now(datetime.timezone.utc) -
                  datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cand = pri[0] if pri and state.get(pri[0]["id"], {}) \
            .get("scanned_at", "") < cutoff else None
        if cand is None:
            rest = [p for pid, p in parks.items() if not pid.startswith(prefixes)]
            rest.sort(key=lambda p: state.get(p["id"], {}).get("scanned_at", ""))
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
        state = load_state()
        state[p["id"]] = {"scanned_at": datetime.datetime.now(datetime.timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "n_alerts": n, "n_requests": _nreq}
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        json.dump(state, open(STATE_FILE, "w"), indent=1)

if __name__ == "__main__":
    main()
