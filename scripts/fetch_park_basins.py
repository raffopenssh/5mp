#!/usr/bin/env python3
"""Fetch each park's contributing (upstream) watershed + downstream river trace.

Why: mining pressure on a park is a *watershed* phenomenon. The 8 confirmed
artisanal pits in the Chinko headwaters are 123 km OUTSIDE CAF_Chinko yet drain
straight through it (docs/MINING_FINDINGS_2026-08.md §1). Every scanner in this
repo was park-bbox-scoped, so "upstream mining pressure on park X" was not even
expressible. This script makes it expressible:

  upstream    mghydro Global Watersheds API   (docs/MINING_DATA_SOURCES.md §5.1)
  downstream  global-river-runner pygeoapi    (§5.3)

Outlet selection (the only non-trivial part): a park's contributing basin is the
union of the watersheds above every point where a significant river *leaves* the
park. Most parks have more than one (CAF_Chinko drains via both the Chinko and
the Mbari), so a single outlet systematically under-covers. We therefore:

  1. take HydroRIVERS vertices (park_rivers_hydro; `ord_flow` LOWER = more
     discharge) that sit within EXIT_KM of the park boundary,
  2. sample Copernicus GLO-90 elevation at each and cluster them (CLUSTER_KM),
  3. rank clusters by (ord_flow, elevation) and keep up to --max-outlets,
  4. skip any outlet that already falls *inside* a fetched watershed - it is a
     nested duplicate of a bigger one.

mghydro then snaps each point to its own MERIT-Hydro network.

Courtesy-API etiquette (§5.1, §6.6): mghydro runs on a $25/month shared host and
its author asks for ~5 s between calls and no parallelism. So: strictly serial,
--sleep 5 default, and EVERY response (including errors) is cached in the
http_cache SQLite table. Re-runs are free; use --refresh to bypass the cache.

Usage:
  python3 scripts/fetch_park_basins.py --list-outlets              # dry run, no API calls
  python3 scripts/fetch_park_basins.py --park CAF_Chinko
  python3 scripts/fetch_park_basins.py --all                       # 163 parks, ~30 min
  python3 scripts/fetch_park_basins.py --all --kind upstream
"""
import argparse, json, math, os, sqlite3, sys, time, urllib.error, urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "db.sqlite3")
KEYSTONES = os.path.join(BASE, "data", "keystones_with_boundaries.json")
OUT_DIR = os.path.join(BASE, "data", "park_basins")

# /app/getwshed is the endpoint the Global Watersheds web app itself calls. It
# returns, in ONE request: the watershed polygon, the *upstream river network*
# with Strahler stream order per reach, and the snapped outlet. That is strictly
# better than /app/watershed_api (polygon only) + /app/upstream_rivers_api
# (rivers only): half the requests on a $25/mo shared host, and the stream
# orders are what restricts turbidity analysis to >=3rd-order reaches, where
# Sentinel-2 can actually see the water surface.
# Response is gzip-encoded JSON; note it silently reverts to low precision above
# 50,000 km2 (we record that in meta.precision).
MGHYDRO = "https://mghydro.com/app/getwshed?lat={lat}&lng={lon}&precision=high"
RIVERRUNNER = ("https://merit.internetofwater.app/processes/river-runner/"
               "execution?lat={lat}&lng={lon}")
DEM_TMPL = ("https://copernicus-dem-90m.s3.eu-central-1.amazonaws.com/"
            "Copernicus_DSM_COG_30_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
            "Copernicus_DSM_COG_30_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")
EXIT_KM = 4.0           # river vertex counts as a park exit within this of edge
CLUSTER_KM = 25.0       # merge exit candidates closer than this
MAX_OUTLETS = 3         # API calls per park per kind (courtesy budget)
UA = {"User-Agent": "5mp-conservation-monitor/1.0 (+https://exe.dev) basin fetch",
      "Accept-Encoding": "gzip"}

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")


def km(a, b):
    """a,b = (lon,lat)."""
    return math.hypot((a[0] - b[0]) * 111 * math.cos(math.radians(a[1])),
                      (a[1] - b[1]) * 111)


# ---------------------------------------------------------------- DEM sampling
_tiles = {}


def dem_tile(lon, lat):
    """Whole 1-deg GLO-90 tile as (array, transform-ish). Cached; a tile is
    1200x1200 float32 = 5.8 MB, far cheaper than thousands of range requests."""
    key = (math.floor(lon), math.floor(lat))
    if key in _tiles:
        return _tiles[key]
    import rasterio
    lo, la = key
    url = DEM_TMPL.format(ns="N" if la >= 0 else "S", lat=abs(la),
                          ew="E" if lo >= 0 else "W", lon=abs(lo))
    try:
        with rasterio.open(url) as d:
            _tiles[key] = (d.read(1), d.transform)
    except Exception as ex:
        print(f"  DEM miss {key}: {str(ex)[:70]}", file=sys.stderr)
        _tiles[key] = None
    return _tiles[key]


def elevation(lon, lat):
    t = dem_tile(lon, lat)
    if not t:
        return None
    arr, tr = t
    col, row = ~tr * (lon, lat)
    r, c = int(row), int(col)
    if not (0 <= r < arr.shape[0] and 0 <= c < arr.shape[1]):
        return None
    v = float(arr[r, c])
    return None if v < -1000 else v


# ------------------------------------------------------------------- geometry
def boundary_rings(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    if geom["type"] == "MultiPolygon":
        return [p[0] for p in geom["coordinates"]]
    return []


def pick_outlets(park, max_outlets=MAX_OUTLETS):
    """Candidate drainage exits, best first.

    A "river exit" = a HydroRIVERS vertex inside the park but within EXIT_KM of
    its boundary. Ranked by discharge (ord_flow ascending = bigger river) then
    elevation, then spatially thinned to CLUSTER_KM so we do not spend three API
    calls on three vertices of the same reach. Falls back to the lowest sampled
    boundary DEM pixel for parks with no river data.
    """
    from shapely.geometry import shape, Point
    from shapely.prepared import prep
    poly = shape(park["geometry"])
    inside = prep(poly)
    edge = poly.boundary
    deg = EXIT_KM / 111.0

    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT lon, lat, ord_flow, name FROM park_rivers_hydro WHERE park_id=?",
        (park["id"],)).fetchall()
    con.close()

    cands = []
    for lo, la, of, nm in rows:
        if lo is None or la is None:
            continue
        p = Point(lo, la)
        if not inside.contains(p) or p.distance(edge) > deg:
            continue
        cands.append({"lon": round(lo, 5), "lat": round(la, 5),
                      "ord_flow": of, "river": nm})
    if not cands:
        pts = []
        for ring in boundary_rings(park["geometry"]):
            step = max(1, len(ring) // 300)
            pts += [tuple(c[:2]) for c in ring[::step]]
        best, best_z = None, None
        for q in pts:
            z = elevation(*q)
            if z is not None and (best_z is None or z < best_z):
                best, best_z = q, z
        if best is None:
            c = park.get("coordinates") or {}
            if c.get("lon") is None:
                return []
            return [{"lon": c["lon"], "lat": c["lat"], "how": "centroid",
                     "elev_m": None}]
        return [{"lon": round(best[0], 5), "lat": round(best[1], 5),
                 "how": "lowest_boundary_pixel", "elev_m": round(best_z, 1)}]

    for c in cands:
        z = elevation(c["lon"], c["lat"])
        c["elev_m"] = round(z, 1) if z is not None else None
        c["how"] = "river_exit"
    cands.sort(key=lambda c: (c["ord_flow"],
                              c["elev_m"] if c["elev_m"] is not None else 9e9))
    out = []
    for c in cands:
        if any(km((c["lon"], c["lat"]), (o["lon"], o["lat"])) < CLUSTER_KM
               for o in out):
            continue
        out.append(c)
        if len(out) >= max_outlets:
            break
    return out


# ------------------------------------------------------------ cached fetching
def _gunzip(body):
    """Bodies are stored decompressed, but be tolerant of rows written before
    that was true (and of any server that ignores our Accept-Encoding)."""
    if body[:2] == b"\x1f\x8b":
        import gzip
        try:
            return gzip.decompress(body)
        except Exception:
            pass
    return body


def cached_get(con, url, refresh=False, sleep=5.0, timeout=180):
    if not refresh:
        r = con.execute("SELECT status, body FROM http_cache WHERE url=?",
                        (url,)).fetchone()
        if r and r[0] == 200:
            return 200, _gunzip(r[1]), True
    time.sleep(sleep)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                    timeout=timeout) as resp:
            status, body = resp.status, resp.read()
            # /app/getwshed always responds gzip (it is what the web app asks
            # for); urllib does not transparently decompress. Store plain JSON
            # in the cache so the cache is inspectable with sqlite3.
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                try:
                    body = gzip.decompress(body)
                except Exception:
                    pass
    except urllib.error.HTTPError as ex:
        status, body = ex.code, ex.read()[:4096]
    except Exception as ex:
        status, body = 0, str(ex)[:500].encode()
    con.execute("INSERT OR REPLACE INTO http_cache(url, status, body, fetched_at) "
                "VALUES (?,?,?,datetime('now'))", (url, status, sqlite3.Binary(body)))
    con.commit()
    return status, _gunzip(body), False


def multipoly(feats):
    """Merge GeoJSON polygon features into one (Multi)Polygon + rough area."""
    polys = []
    for f in feats:
        g = f["geometry"]
        if g["type"] == "Polygon":
            polys.append(g["coordinates"])
        elif g["type"] == "MultiPolygon":
            polys += g["coordinates"]
    if not polys:
        return None
    if len(polys) == 1:
        return {"type": "Polygon", "coordinates": polys[0]}
    return {"type": "MultiPolygon", "coordinates": polys}


def shoelace_km2(geom):
    def ring_area(ring):
        # Spherical shoelace: A = R^2/2 * sum (lon1-lon0)(sin lat0 + sin lat1).
        # (An earlier version used sin(mean lat), which is ~(sin a + sin b)/2 and
        # so under-reported every area by exactly 2x - checked against the API's
        # own 50,200 km2 for the Chinko outlet.)
        s = 0.0
        for i in range(len(ring) - 1):
            x0, y0 = ring[i][:2]
            x1, y1 = ring[i + 1][:2]
            s += (math.radians(x1 - x0)
                  * (math.sin(math.radians(y0)) + math.sin(math.radians(y1))))
        return abs(s) * 6371.0 ** 2 / 2
    tot = 0.0
    polys = ([geom["coordinates"]] if geom["type"] == "Polygon"
             else geom["coordinates"])
    for p in polys:
        tot += ring_area(p[0]) - sum(ring_area(h) for h in p[1:])
    return tot


def _num(v):
    """mghydro returns human-formatted numbers like '50,200'."""
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


def fetch_upstream(con, park, outlet, **kw):
    url = MGHYDRO.format(lat=outlet["lat"], lon=outlet["lon"])
    st, body, hit = cached_get(con, url, **kw)
    if st != 200:
        return None, f"HTTP {st}: {body[:120]!r}"
    try:
        d = json.loads(body)
    except Exception as ex:
        return None, f"bad json: {ex}"
    g = d.get("watershed")
    if not g or not g.get("coordinates"):
        return None, d.get("message") or "no polygon"
    g = {"type": g["type"], "coordinates": g["coordinates"]}

    msg = d.get("message") or ""
    area = _num(msg.split("watershed of ")[1].split(" km")[0]) \
        if "watershed of " in msg else None
    if area is None:
        area = round(shoelace_km2(g), 1)

    # upstream river reaches with Strahler order (only /app/getwshed has these)
    rivers = []
    for f in (d.get("rivers") or {}).get("features", []):
        gg = f.get("geometry") or {}
        if gg.get("type") != "LineString":
            continue
        pr = f.get("properties") or {}
        rivers.append({"comid": pr.get("comid"), "sorder": pr.get("sorder"),
                       "coords": [c[:2] for c in gg["coordinates"]]})

    snapped = None
    for f in (d.get("outlet") or {}).get("features", []):
        if (f.get("properties") or {}).get("point_type") == "snapped":
            snapped = f["geometry"]["coordinates"][:2]
    return {"kind": "upstream", "geojson": g, "area_km2": area,
            "source": "mghydro", "cache_hit": hit, "rivers": rivers,
            "meta": {"outlet": outlet, "snapped_outlet": snapped,
                     "wid": d.get("wid"), "message": msg.strip(),
                     # the API downgrades precision above 50,000 km2
                     "precision": "low" if "too large" in msg else "high",
                     "n_reaches": len(rivers), "url": url}}, None


def fetch_downstream(con, park, outlet, **kw):
    url = RIVERRUNNER.format(lat=outlet["lat"], lon=outlet["lon"])
    st, body, hit = cached_get(con, url, **kw)
    if st != 200:
        return None, f"HTTP {st}: {body[:120]!r}"
    try:
        d = json.loads(body)
    except Exception as ex:
        return None, f"bad json: {ex}"
    fc = d.get("value") or d
    feats = fc.get("features") or []
    lines, names, length = [], [], 0.0
    for f in feats:
        g = f.get("geometry") or {}
        segs = ([g["coordinates"]] if g.get("type") == "LineString"
                else g.get("coordinates") or [])
        for s in segs:
            cs = [c[:2] for c in s]
            if len(cs) < 2:
                continue
            lines.append(cs)
            length += sum(km(cs[i], cs[i + 1]) for i in range(len(cs) - 1))
        nm = (f.get("properties") or {}).get("river_name") or \
             (f.get("properties") or {}).get("name")
        if nm and nm not in names:
            names.append(nm)
    if not lines:
        return None, "no reaches"
    return {"kind": "downstream", "source": "river-runner", "cache_hit": hit,
            "geojson": {"type": "MultiLineString", "coordinates": lines},
            "length_km": round(length, 1),
            "meta": {"outlet": outlet, "reaches": len(feats),
                     "river_names": names[:25], "url": url}}, None


def merge_upstream(parts):
    """Union of several outlet watersheds (shapely if available, else stacked)."""
    geoms = [p["geojson"] for p in parts]
    try:
        from shapely.geometry import shape, mapping
        from shapely.ops import unary_union
        u = unary_union([shape(g).buffer(0) for g in geoms])
        g = json.loads(json.dumps(mapping(u)))     # tuples -> lists
        area = round(shoelace_km2(g), 1)
    except Exception:
        g = multipoly([{"geometry": x} for x in geoms])
        area = round(sum(p.get("area_km2") or 0 for p in parts), 1)
    rivers, seen = [], set()
    for p in parts:
        for r in p.get("rivers", []):
            if r["comid"] in seen:
                continue
            seen.add(r["comid"])
            rivers.append(r)
    return {"kind": "upstream", "source": "mghydro", "geojson": g,
            "area_km2": area, "rivers": rivers,
            "cache_hit": all(p["cache_hit"] for p in parts),
            "meta": {"outlet": parts[0]["meta"]["outlet"],
                     "outlets": [p["meta"]["outlet"] for p in parts],
                     "snapped_outlets": [p["meta"].get("snapped_outlet")
                                         for p in parts],
                     "per_outlet_area_km2": [p.get("area_km2") for p in parts],
                     "precision": ("low" if any(p["meta"].get("precision") == "low"
                                                for p in parts) else "high"),
                     "n_reaches": len(rivers),
                     "urls": [p["meta"]["url"] for p in parts]}}


def merge_downstream(parts):
    lines, names = [], []
    for p in parts:
        lines += p["geojson"]["coordinates"]
        for n in p["meta"].get("river_names", []):
            if n not in names:
                names.append(n)
    return {"kind": "downstream", "source": "river-runner",
            "geojson": {"type": "MultiLineString", "coordinates": lines},
            "length_km": round(sum(p["length_km"] for p in parts), 1),
            "cache_hit": all(p["cache_hit"] for p in parts),
            "meta": {"outlet": parts[0]["meta"]["outlet"],
                     "outlets": [p["meta"]["outlet"] for p in parts],
                     "reaches": sum(p["meta"]["reaches"] for p in parts),
                     "river_names": names[:25],
                     "urls": [p["meta"]["url"] for p in parts]}}


def contains_point(geom, lon, lat):
    try:
        from shapely.geometry import shape, Point
        return shape(geom).contains(Point(lon, lat))
    except Exception:
        return False


def store(con, park_id, res):
    con.execute(
        "INSERT OR REPLACE INTO park_basins"
        "(park_id, kind, outlet_lat, outlet_lon, area_km2, length_km, geojson,"
        " source, meta, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))",
        (park_id, res["kind"], res["meta"]["outlet"]["lat"],
         res["meta"]["outlet"]["lon"], res.get("area_km2"), res.get("length_km"),
         json.dumps(res["geojson"]), res["source"], json.dumps(res["meta"])))
    rivers = res.get("rivers") or []
    if rivers:
        con.execute("DELETE FROM park_basin_rivers WHERE park_id=?", (park_id,))
        con.executemany(
            "INSERT OR REPLACE INTO park_basin_rivers"
            "(park_id, comid, stream_order, length_km, geojson) VALUES (?,?,?,?,?)",
            [(park_id, r["comid"], r["sorder"],
              round(sum(km(r["coords"][i], r["coords"][i + 1])
                        for i in range(len(r["coords"]) - 1)), 3),
              json.dumps({"type": "LineString", "coordinates": r["coords"]}))
             for r in rivers if r.get("comid") is not None])
    con.commit()
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{park_id}_{res['kind']}.json")
    out = {"park_id": park_id, "kind": res["kind"], "source": res["source"],
           "area_km2": res.get("area_km2"), "length_km": res.get("length_km"),
           "meta": res["meta"], "geometry": res["geojson"]}
    if rivers:
        out["rivers"] = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "properties": {"comid": r["comid"], "stream_order": r["sorder"]},
             "geometry": {"type": "LineString", "coordinates": r["coords"]}}
            for r in rivers]}
    json.dump(out, open(path, "w"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--park", action="append", help="repeatable")
    ap.add_argument("--parks", help="comma separated")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--kind", choices=("upstream", "downstream", "both"),
                    default="both")
    ap.add_argument("--sleep", type=float, default=5.0,
                    help="seconds between API calls (courtesy; do not lower)")
    ap.add_argument("--refresh", action="store_true", help="bypass http_cache")
    ap.add_argument("--list-outlets", action="store_true",
                    help="compute outlets only, no API calls")
    ap.add_argument("--max-outlets", type=int, default=MAX_OUTLETS)
    ap.add_argument("--skip-existing", action="store_true", default=True)
    ap.add_argument("--force", dest="skip_existing", action="store_false")
    a = ap.parse_args()

    parks = json.load(open(KEYSTONES))
    want = []
    if a.park:
        want += a.park
    if a.parks:
        want += a.parks.split(",")
    if want:
        parks = [p for p in parks if p["id"] in want]
    elif not a.all and not a.list_outlets:
        ap.error("need --park/--parks/--all")
    parks = [p for p in parks if p.get("geometry")]

    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    kinds = (["upstream", "downstream"] if a.kind == "both" else [a.kind])
    have = {(r[0], r[1]) for r in con.execute(
        "SELECT park_id, kind FROM park_basins")} if a.skip_existing else set()

    for i, p in enumerate(parks, 1):
        pid = p["id"]
        todo = [k for k in kinds if (pid, k) not in have]
        if not todo and not a.list_outlets:
            print(f"[{i}/{len(parks)}] {pid}: cached, skip")
            continue
        outlets = pick_outlets(p, a.max_outlets)
        if not outlets:
            print(f"[{i}/{len(parks)}] {pid}: NO OUTLET", file=sys.stderr)
            continue
        desc = ", ".join(f"{o['lat']},{o['lon']}[{o['how']}"
                         + (f" ord{o['ord_flow']}" if o.get("ord_flow") else "")
                         + (f" {o['river']}" if o.get("river") else "") + "]"
                         for o in outlets)
        print(f"[{i}/{len(parks)}] {pid} outlets: {desc}", flush=True)
        if a.list_outlets:
            continue
        for kind in todo:
            fn = fetch_upstream if kind == "upstream" else fetch_downstream
            parts = []
            for o in outlets:
                # nested-duplicate guard: an outlet already inside a fetched
                # upstream polygon adds nothing but an API call
                if kind == "upstream" and any(
                        contains_point(pt["geojson"], o["lon"], o["lat"])
                        for pt in parts):
                    print(f"    upstream: skip nested outlet "
                          f"{o['lat']},{o['lon']}")
                    continue
                res, err = fn(con, p, o, refresh=a.refresh, sleep=a.sleep)
                if err:
                    print(f"    {kind} @{o['lat']},{o['lon']}: FAIL {err}",
                          file=sys.stderr)
                    continue
                parts.append(res)
            if not parts:
                continue
            merged = (merge_upstream(parts) if kind == "upstream"
                      else merge_downstream(parts))
            store(con, pid, merged)
            extra = (f"{merged['area_km2']} km2 from {len(parts)} outlet(s)"
                     if kind == "upstream" else
                     f"{merged['length_km']} km, "
                     f"{merged['meta']['reaches']} reaches, "
                     f"{len(parts)} outlet(s)")
            print(f"    {kind}: {extra}"
                  f"{' (cache)' if merged['cache_hit'] else ''}", flush=True)
    con.close()


if __name__ == "__main__":
    main()
