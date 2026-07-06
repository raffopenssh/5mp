"""Per-park river turbidity scanner — detects sediment plumes from artisanal mining.

Samples Sentinel-2 L2A red reflectance + SCL along OSM waterways (rivers and
long streams) and flags "turbidity onset" points: locations where water turns
abruptly turbid relative to the upstream rolling median. Point-source onsets on
otherwise-clean rivers are the signature of alluvial gold mining (confirmed at
Chinko headwaters, 7.446N 24.030E). OSM waterway ways are drawn in flow
direction, so way ordering = upstream->downstream.

Data sources (all free): earth-search.aws.element84.com STAC -> sentinel-cogs
COGs via rasterio HTTP; OSM PBFs in data/osm_raw/ (extracted per park with
osmium, cached in data/osm_raw/waterways/).

Usage:
  python3 analysis/river_turbidity.py --park CAF_Chinko
  python3 analysis/river_turbidity.py --rotate            # most-stale park w/ PBF coverage (cron)
  python3 analysis/river_turbidity.py --park CAF_Chinko --datetime 2025-01-01/2025-03-01  # historic

Output: data/turbidity/{park_id}.json   (alerts + scan metadata)
State:  data/turbidity/state.json
"""
import argparse, datetime, json, math, os, subprocess, sys, urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
from cron_notify import notify_status  # noqa: E402

STAC = "https://earth-search.aws.element84.com/v1/search"
OUT_DIR = "data/turbidity"
STATE_FILE = f"{OUT_DIR}/state.json"
WATERWAY_CACHE = "data/osm_raw/waterways"

# country ISO3 prefix -> geofabrik africa/ region name. PBFs are downloaded
# on demand to /tmp, waterways extracted for ALL parks of the country in one
# pass (small geojson caches in data/osm_raw/waterways/), then deleted.
GEOFABRIK = {
    "AGO": "angola", "BEN": "benin", "BWA": "botswana",
    "CAF": "central-african-republic", "CIV": "ivory-coast",
    "CMR": "cameroon", "COD": "congo-democratic-republic",
    "COG": "congo-brazzaville", "DZA": "algeria", "ETH": "ethiopia",
    "GAB": "gabon", "GHA": "ghana", "GNQ": "equatorial-guinea",
    "KEN": "kenya", "LBR": "liberia", "LSO": "lesotho", "MLI": "mali",
    "MOZ": "mozambique", "MWI": "malawi", "NAM": "namibia",
    "NER": "niger", "NGA": "nigeria", "RWA": "rwanda", "SDN": "sudan",
    "SEN": "senegal-and-gambia", "SSD": "south-sudan", "TCD": "chad",
    "TGO": "togo", "TZA": "tanzania", "UGA": "uganda",
    "ZAF": "south-africa", "ZMB": "zambia", "ZWE": "zimbabwe",
}

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_CACHEMAX", "512")
os.environ.setdefault("CPL_VSIL_CURL_CACHE_SIZE", "200000000")


def km(a, b):
    return math.hypot((a[1]-b[1])*111, (a[0]-b[0])*111*math.cos(math.radians(a[1])))


def park_bbox(park, buffer_km):
    lons, lats = [], []
    def walk(c):
        if isinstance(c[0], (int, float)):
            lons.append(c[0]); lats.append(c[1])
        else:
            for x in c:
                walk(x)
    walk(park["geometry"]["coordinates"])
    d = buffer_km / 111.0
    return min(lons)-d, min(lats)-d, max(lons)+d, max(lats)+d


def _osmium_extract(pbf_path, park_id, bbox, out):
    """bbox-extract park area from country PBF; derive waterway geojson.
    Returns path to the bbox-extracted park PBF (caller may reuse for
    infra enrichment, and must delete it)."""
    w, s, e, n = bbox
    tmp = f"/tmp/{park_id}_ww.osm.pbf"
    tmp2 = f"/tmp/{park_id}_ww2.osm.pbf"
    subprocess.run(["osmium", "extract", "-b", f"{w},{s},{e},{n}",
                    pbf_path, "-o", tmp, "--overwrite"], check=True)
    subprocess.run(["osmium", "tags-filter", tmp, "w/waterway=river,stream",
                    "-o", tmp2, "--overwrite"], check=True)
    subprocess.run(["osmium", "export", tmp2, "-o", out, "--overwrite",
                    "--geometry-types=linestring"], check=True)
    try: os.remove(tmp2)
    except OSError: pass
    return tmp


def _export_filtered(park_pbf, park_id, filt, geom_types):
    """osmium tags-filter + export -> parsed geojson features (or [])."""
    tmpf = f"/tmp/{park_id}_enr.osm.pbf"
    tmpj = f"/tmp/{park_id}_enr.geojson"
    try:
        subprocess.run(["osmium", "tags-filter", park_pbf, *filt,
                        "-o", tmpf, "--overwrite"], check=True)
        subprocess.run(["osmium", "export", tmpf, "-o", tmpj, "--overwrite",
                        f"--geometry-types={geom_types}", "-u", "type_id"],
                       check=True)
        return json.load(open(tmpj)).get("features", [])
    except Exception as ex:
        print(f"  enrich export failed ({filt}): {ex}", file=sys.stderr)
        return []
    finally:
        for t in (tmpf, tmpj):
            try: os.remove(t)
            except OSError: pass


def _line_centroid_len(coords):
    lons = [c[0] for c in coords]; lats = [c[1] for c in coords]
    length = sum(km(a, b) for a, b in zip(coords, coords[1:]))
    return sum(lons)/len(lons), sum(lats)/len(lats), length


_PAVED = {"asphalt", "concrete", "paved", "paving_stones", "chipseal",
          "concrete:plates", "sett", "cobblestone"}
_UNPAVED = {"unpaved", "compacted", "ground", "gravel", "dirt", "sand",
            "earth", "fine_gravel", "mud", "grass", "pebblestone", "rock"}


def _road_class(props):
    """Map OSM tags -> (dl_class, passability) matching HeiGIT conventions
    used elsewhere in roads_heigit (paved/unpaved/unknown, PAV_/UNP_ codes)."""
    surface = (props.get("surface") or "").split(";")[0]
    hw = props.get("highway", "")
    if surface in _PAVED:
        dl = "paved"
    elif surface in _UNPAVED or hw in ("track", "path"):
        dl = "unpaved"
    elif hw in ("motorway", "trunk", "primary"):
        dl = "paved"  # near-universal for these classes in the region
    else:
        dl = "unknown"
    if dl == "unknown":
        return dl, None
    prefix = "PAV" if dl == "paved" else "UNP"
    lanes = props.get("lanes")
    try:
        dual = props.get("oneway") == "yes" or (lanes and int(lanes) >= 4)
    except ValueError:
        dual = False
    if hw in ("track", "path", "service"):
        kind = "LIGHT"
    else:
        kind = "DUAL" if dual else "SINGLE"
    return dl, f"{prefix}_{kind}"


def enrich_park_infra(park_pbf, park_id):
    """Fill osm_places (placenames + named rivers/streams as points) and
    roads_heigit for parks that have no rows yet. Runs opportunistically
    while the country PBF is on disk; no-op when data already present."""
    import sqlite3
    db = sqlite3.connect("db.sqlite3")
    try:
        n_places = db.execute("SELECT COUNT(*) FROM osm_places WHERE park_id=?",
                              (park_id,)).fetchone()[0]
        n_roads = db.execute("SELECT COUNT(*) FROM roads_heigit WHERE park_id=?",
                             (park_id,)).fetchone()[0]

        if n_places == 0:
            rows = []
            for f in _export_filtered(park_pbf, park_id,
                                      ["n/place=city,town,village,hamlet"], "point"):
                p = f["properties"]
                name = p.get("name")
                if not name:
                    continue
                lo, la = f["geometry"]["coordinates"]
                rows.append((park_id, p["place"], name, la, lo,
                             f.get("id", ""), json.dumps(p, ensure_ascii=False)))
            # named rivers/streams: one row per name (longest way wins;
            # OSM splits rivers into many ways). osm_tags keeps attributes:
            # intermittent, width, tidal, boat...
            best = {}
            for f in _export_filtered(park_pbf, park_id,
                                      ["w/waterway=river,stream"], "linestring"):
                p = f["properties"]
                name = p.get("name")
                if not name or f["geometry"]["type"] != "LineString":
                    continue
                lo, la, length = _line_centroid_len(f["geometry"]["coordinates"])
                key = (name, p["waterway"])
                if key not in best or length > best[key][0]:
                    best[key] = (length, (park_id, p["waterway"], name, la, lo,
                                          f.get("id", ""),
                                          json.dumps(p, ensure_ascii=False)))
            rows += [r for _, r in best.values()]
            for f in _export_filtered(park_pbf, park_id,
                                      ["n/natural=peak,hill"], "point"):
                p = f["properties"]
                name = p.get("name")
                if not name:
                    continue
                lo, la = f["geometry"]["coordinates"]
                ptype = "mountain" if p.get("natural") == "peak" else "hill"
                rows.append((park_id, ptype, name, la, lo,
                             f.get("id", ""), json.dumps(p, ensure_ascii=False)))
            if rows:
                db.executemany("""INSERT INTO osm_places
                    (park_id, place_type, name, lat, lon, osm_id, osm_tags)
                    VALUES (?,?,?,?,?,?,?)""", rows)
                db.commit()
                print(f"  enriched osm_places: {park_id} +{len(rows)}", file=sys.stderr)

        if n_roads == 0:
            rows = []
            for f in _export_filtered(park_pbf, park_id,
                    ["w/highway=motorway,trunk,primary,secondary,tertiary,"
                     "unclassified,residential,track,path,service"], "linestring"):
                if f["geometry"]["type"] != "LineString":
                    continue
                p = f["properties"]
                _, _, length = _line_centroid_len(f["geometry"]["coordinates"])
                dl_class, passability = _road_class(p)
                rows.append((park_id, f.get("id", ""), p.get("name"),
                             p.get("highway"), (p.get("surface") or "").split(";")[0],
                             round(length, 2), json.dumps(f["geometry"]),
                             dl_class, passability))
            if rows:
                db.executemany("""INSERT INTO roads_heigit
                    (park_id, osm_id, name, highway_type, surface, length_km,
                     geojson, dl_class_2024, passability)
                    VALUES (?,?,?,?,?,?,?,?,?)""", rows)
                db.commit()
                print(f"  enriched roads_heigit: {park_id} +{len(rows)}", file=sys.stderr)
    finally:
        db.close()


def ensure_waterways(park_id, bbox, parks, buffer_km):
    """Return cached waterway geojson for park. On cache miss, download the
    country PBF from geofabrik to /tmp, extract waterways for ALL parks of
    that country in one pass (so the big PBF is only ever downloaded once),
    then delete the PBF. Keeps disk usage to the small geojson caches."""
    os.makedirs(WATERWAY_CACHE, exist_ok=True)
    out = f"{WATERWAY_CACHE}/{park_id}.geojson"
    if os.path.exists(out):
        return out
    iso = park_id.split("_")[0]
    region = GEOFABRIK.get(iso)
    if not region:
        raise SystemExit(f"no geofabrik mapping for {iso}")
    # legacy local PBFs (pre-rollout) — use if present
    pbf = f"/tmp/{region}-latest.osm.pbf"
    legacy = f"data/osm_raw/{region}-latest.osm.pbf"
    if os.path.exists(legacy):
        pbf = legacy
    if not os.path.exists(pbf):
        url = f"https://download.geofabrik.de/africa/{region}-latest.osm.pbf"
        print(f"downloading {url}", file=sys.stderr)
        subprocess.run(["curl", "-sfL", "--retry", "3", url, "-o", pbf], check=True)
    try:
        for pid, p in parks.items():
            if pid.split("_")[0] != iso:
                continue
            po = f"{WATERWAY_CACHE}/{pid}.geojson"
            if os.path.exists(po):
                continue
            print(f"  extracting waterways: {pid}", file=sys.stderr)
            park_pbf = _osmium_extract(pbf, pid, park_bbox(p, buffer_km), po)
            # while we have the OSM data anyway: backfill missing park infra
            # (placenames, named rivers, roads incl. surface/passability)
            try:
                enrich_park_infra(park_pbf, pid)
            except Exception as ex:
                print(f"  enrich {pid} failed: {ex}", file=sys.stderr)
            try: os.remove(park_pbf)
            except OSError: pass
    finally:
        if pbf.startswith("/tmp/"):
            try: os.remove(pbf)
            except OSError: pass
    return out


def build_polylines(geojson_path, bbox, min_stream_km):
    """Return list of {name, waterway, pts} polylines, chained by river name.
    OSM waterway ways point downstream, so ordering within a way is flow order."""
    w, s, e, n = bbox
    d = json.load(open(geojson_path))
    by_name = {}
    singles = []
    for f in d["features"]:
        if f["geometry"]["type"] != "LineString":
            continue
        cs = f["geometry"]["coordinates"]
        if not any(w <= lo <= e and s <= la <= n for lo, la in cs[::5]):
            continue
        props = f["properties"]
        ww = props.get("waterway")
        if ww not in ("river", "stream"):
            continue
        name = props.get("name") or ""
        if name:
            by_name.setdefault((name, ww), []).append(list(cs))
        else:
            singles.append((name, ww, list(cs)))
    polylines = []
    # chain named rivers: greedily join segments end-to-start (flow order kept)
    for (name, ww), segs in by_name.items():
        remaining = list(segs)
        while remaining:
            chain = remaining.pop(0)
            grew = True
            while grew:
                grew = False
                for sgs in remaining:
                    if km(chain[-1], sgs[0]) < 0.5:      # downstream continuation
                        chain = chain + sgs
                        remaining.remove(sgs); grew = True; break
                    if km(sgs[-1], chain[0]) < 0.5:      # upstream extension
                        chain = sgs + chain
                        remaining.remove(sgs); grew = True; break
            polylines.append({"name": name, "waterway": ww, "pts": chain})
    for name, ww, cs in singles:
        polylines.append({"name": name, "waterway": ww, "pts": cs})

    # length filter + sampling
    out = []
    for pl in polylines:
        pts, ww = pl["pts"], pl["waterway"]
        length = sum(km(a, b) for a, b in zip(pts, pts[1:]))
        if ww == "stream" and length < min_stream_km:
            continue
        if ww == "river" and length < 3:
            continue
        spacing = 0.25 if ww == "river" else 0.5
        sampled = [pts[0]]; dist = [0.0]
        for p in pts[1:]:
            dd = km(sampled[-1], p)
            if dd >= spacing:
                sampled.append(p); dist.append(dist[-1] + dd)
        pl["samples"] = sampled
        pl["dist"] = dist
        pl["length_km"] = round(length, 1)
        del pl["pts"]
        out.append(pl)
    return out


def stac_scenes(bbox, dt_range, max_cloud):
    body = json.dumps({
        "collections": ["sentinel-2-l2a"], "bbox": list(bbox),
        "datetime": dt_range, "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "limit": 200,
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
    }).encode()
    req = urllib.request.Request(STAC, data=body,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))["features"]


def sample_scenes(polylines, scenes):
    """Fill red/scl values per sample point from scenes (newest, least-cloudy first
    within same coverage). Returns parallel arrays on each polyline."""
    import rasterio
    from rasterio.warp import transform as rio_transform
    from shapely.geometry import shape, Point

    flat = []   # (pl_idx, sample_idx, lon, lat)
    for i, pl in enumerate(polylines):
        for j, p in enumerate(pl["samples"]):
            flat.append((i, j, p[0], p[1]))
    values = [None] * len(flat)

    # prefer newest scenes; among same date prefer lower cloud
    scenes = sorted(scenes, key=lambda sc: (sc["properties"]["datetime"][:10],
                                            -sc["properties"]["eo:cloud_cover"]),
                    reverse=True)
    for sc in scenes:
        geom = shape(sc["geometry"])
        todo = [k for k, v in enumerate(values)
                if v is None and geom.contains(Point(flat[k][2], flat[k][3]))]
        if not todo:
            continue
        print(f"  {sc['id']} cc={sc['properties']['eo:cloud_cover']:.0f}% pts={len(todo)}",
              file=sys.stderr)
        try:
            with rasterio.open(sc["assets"]["red"]["href"]) as R, \
                 rasterio.open(sc["assets"]["scl"]["href"]) as S:
                xs = [flat[k][2] for k in todo]; ys = [flat[k][3] for k in todo]
                tx, ty = rio_transform("EPSG:4326", R.crs, xs, ys)
                reds = list(R.sample(zip(tx, ty)))
                scls = list(S.sample(zip(tx, ty)))
                date = sc["properties"]["datetime"][:10]
                for m, k in enumerate(todo):
                    scl = int(scls[m][0])
                    # keep only clear water; cloud/shadow/etc -> leave None for older scene
                    if scl in (8, 9, 10, 3, 0):
                        continue
                    values[k] = {"red": int(reds[m][0]), "scl": scl,
                                 "scene": sc["id"], "date": date}
        except Exception as ex:
            print(f"  ERR {sc['id']}: {ex}", file=sys.stderr)
        if all(v is not None for v in values):
            break

    for i, pl in enumerate(polylines):
        pl["vals"] = [None] * len(pl["samples"])
    for k, v in enumerate(values):
        i, j, _, _ = flat[k]
        polylines[i]["vals"][j] = v


def detect_onsets(pl, min_ratio=1.8, min_red=1200, max_clean=1000,
                  window=15, confirm=4, lookahead=6):
    """Scan downstream; alert where red jumps vs upstream rolling median of
    water (SCL==6) samples and stays turbid."""
    water = [(j, v) for j, v in enumerate(pl["vals"])
             if v is not None and v["scl"] == 6 and v["red"] > 0]
    if len(water) < window + lookahead:
        return [], 0.0
    alerts = []
    reds = [v["red"] for _, v in water]
    spacing = 0.25 if pl["waterway"] == "river" else 0.5
    turbid_n = sum(1 for r in reds if r >= min_red)
    turbid_km = turbid_n * spacing

    # Case A: river already turbid at its uppermost observable water sample
    # (source further upstream in a channel too narrow for SCL water pixels;
    # this is exactly the confirmed Chinko headwaters mining signature).
    head = reds[:min(confirm * 2, len(reds))]
    if len(head) >= confirm and sorted(head)[len(head)//2] >= min_red \
            and turbid_km >= 5:
        j, v = water[0]
        p = pl["samples"][j]
        down_turbid = sum(1 for x in reds if x >= min_red) * spacing
        alerts.append({
            "lat": round(p[1], 5), "lon": round(p[0], 5),
            "river": pl["name"] or f"unnamed {pl['waterway']}",
            "waterway": pl["waterway"], "type": "turbid_headwater",
            "red": reds[0], "upstream_median": None, "ratio": None,
            "dist_km": round(pl["dist"][j], 1),
            "downstream_turbid_km": round(down_turbid, 1),
            "scene": v["scene"], "date": v["date"],
        })

    # Case B: point-source onset along an otherwise clean reach
    for i in range(window, len(water) - 1):
        up = sorted(reds[max(0, i-window):i])[len(reds[max(0, i-window):i])//2]
        if up >= max_clean:
            continue
        r = reds[i]
        if r < min_red or r < up * min_ratio:
            continue
        ahead = reds[i:i+lookahead]
        if sum(1 for x in ahead if x >= min_red and x >= up * min_ratio) < confirm:
            continue
        j, v = water[i]
        p = pl["samples"][j]
        # downstream turbid extent from this onset
        down_turbid = sum(1 for x in reds[i:] if x >= min_red) * spacing
        alerts.append({
            "lat": round(p[1], 5), "lon": round(p[0], 5),
            "river": pl["name"] or f"unnamed {pl['waterway']}",
            "waterway": pl["waterway"], "type": "turbidity_onset",
            "red": r, "upstream_median": up,
            "ratio": round(r / max(up, 1), 2),
            "dist_km": round(pl["dist"][j], 1),
            "downstream_turbid_km": round(down_turbid, 1),
            "scene": v["scene"], "date": v["date"],
        })
    # dedupe: keep first onset of each turbid stretch (>=5km apart)
    dedup = []
    for a in alerts:
        if dedup and a["dist_km"] - dedup[-1]["dist_km"] < 5:
            continue
        dedup.append(a)
    return dedup, turbid_km


def scan_park(park, args, parks):
    bbox = park_bbox(park, args.buffer_km)
    print(f"{park['id']} bbox {['%.2f' % x for x in bbox]}", file=sys.stderr)
    ww_path = ensure_waterways(park["id"], bbox, parks, args.buffer_km)
    polylines = build_polylines(ww_path, bbox, args.min_stream_km)
    npts = sum(len(pl["samples"]) for pl in polylines)
    print(f"{len(polylines)} waterways, {npts} sample pts", file=sys.stderr)

    if args.datetime:
        dt_range = args.datetime.replace("/", "T00:00:00Z/") + "T00:00:00Z"
    else:
        end = datetime.date.today()
        start = end - datetime.timedelta(days=args.days)
        dt_range = f"{start}T00:00:00Z/{end}T23:59:59Z"
    scenes = stac_scenes(bbox, dt_range, args.max_cloud)
    print(f"{len(scenes)} scenes in {dt_range}", file=sys.stderr)
    if not scenes:
        return None
    sample_scenes(polylines, scenes)

    all_alerts, rivers_scanned, turbid_pts = [], [], []
    for pl in polylines:
        alerts, turbid_km = detect_onsets(pl)
        nwater = sum(1 for v in pl["vals"] if v and v["scl"] == 6)
        rivers_scanned.append({"name": pl["name"] or "(unnamed)",
                               "waterway": pl["waterway"],
                               "length_km": pl["length_km"],
                               "water_samples": nwater,
                               "turbid_km": round(turbid_km, 1)})
        all_alerts += alerts
        # turbid water sample points (red >= 1200 on SCL water) — lets the UI
        # draw the sediment plume network, timeslider-filterable by date
        for j, v in enumerate(pl["vals"]):
            if v and v["scl"] == 6 and v["red"] >= 1200:
                p = pl["samples"][j]
                turbid_pts.append({"lat": round(p[1], 5), "lon": round(p[0], 5),
                                   "red": v["red"], "date": v["date"],
                                   "river": pl["name"] or f"unnamed {pl['waterway']}"})
    all_alerts.sort(key=lambda a: -a["downstream_turbid_km"])

    os.makedirs(OUT_DIR, exist_ok=True)
    out = {
        "park_id": park["id"],
        "scanned_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "datetime_range": dt_range, "buffer_km": args.buffer_km,
        "n_waterways": len(polylines), "n_sample_pts": npts,
        "alerts": all_alerts,
        "rivers": sorted([r for r in rivers_scanned if r["water_samples"] > 0],
                         key=lambda r: -r["turbid_km"])[:50],
        "turbid_points": turbid_pts[:5000],
    }
    path = f"{OUT_DIR}/{park['id']}.json"
    json.dump(out, open(path, "w"), indent=1)
    print(f"{len(all_alerts)} turbidity onset alerts -> {path}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--park")
    ap.add_argument("--rotate", action="store_true",
                    help="scan most-stale park with PBF coverage (cron)")
    ap.add_argument("--buffer-km", type=float, default=50)
    ap.add_argument("--days", type=int, default=45,
                    help="lookback window for scenes")
    ap.add_argument("--datetime", help="explicit range: 2025-01-01/2025-03-01")
    ap.add_argument("--max-cloud", type=float, default=40)
    ap.add_argument("--min-stream-km", type=float, default=8)
    args = ap.parse_args()

    parks = {p["id"]: p for p in json.load(open("data/keystones_with_boundaries.json"))
             if p.get("geometry")}
    if args.park:
        targets = [parks[args.park]]
    elif args.rotate:
        state = {}
        try: state = json.load(open(STATE_FILE))
        except Exception: pass
        cand = [p for pid, p in parks.items() if pid.split("_")[0] in GEOFABRIK]
        cand.sort(key=lambda p: state.get(p["id"], {}).get("scanned_at", ""))
        targets = cand[:1]
    else:
        ap.error("need --park or --rotate")

    for p in targets:
        try:
            res = scan_park(p, args, parks)
        except Exception as ex:  # noqa: BLE001
            notify_status("turbidity_scan_failed", "Turbidity Scan Failed",
                          f"{p['id']}: {str(ex)[:200]}")
            raise
        state = {}
        try: state = json.load(open(STATE_FILE))
        except Exception: pass
        state[p["id"]] = {"scanned_at": datetime.datetime.now(datetime.timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "n_alerts": len(res["alerts"]) if res else 0}
        os.makedirs(OUT_DIR, exist_ok=True)
        json.dump(state, open(STATE_FILE, "w"), indent=1)
        if res:
            notify_status("turbidity_scan_success", "Turbidity Scan Complete",
                          f"{p['id']}: {len(res['alerts'])} alerts, "
                          f"{len(res.get('rivers', []))} rivers, "
                          f"{res.get('n_sample_pts', 0):,} sample points "
                          f"({res.get('datetime_range', '')})")
        else:
            notify_status("turbidity_scan_success", "Turbidity Scan Complete",
                          f"{p['id']}: no usable Sentinel-2 scenes in window "
                          f"(clouds) — will retry on next rotation")


if __name__ == "__main__":
    main()
