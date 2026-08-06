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
# The Geofabrik/osmium machinery and the opportunistic park-infra backfill used
# to live in this file. They were lifted to scripts/osm_pbf.py on 2026-08-06 so
# they survive the mining/turbidity retirement — this file's cron is disabled
# and was their only caller (docs/PLAN_AOI_OVERLAY.md §6). Re-exported here so
# the scanner below is unchanged if the mining flag is ever flipped back.
from osm_pbf import (  # noqa: E402,F401
    GEOFABRIK, _export_filtered, _line_centroid_len, _osmium_extract,
    _road_class, enrich_park_infra, km, park_bbox,
)

STAC = "https://earth-search.aws.element84.com/v1/search"
OUT_DIR = "data/turbidity"
STATE_FILE = f"{OUT_DIR}/state.json"
WATERWAY_CACHE = "data/osm_raw/waterways"

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_CACHEMAX", "512")
os.environ.setdefault("CPL_VSIL_CURL_CACHE_SIZE", "200000000")


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
