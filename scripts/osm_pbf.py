#!/usr/bin/env python3
"""Geofabrik PBF download + osmium extraction + park infra enrichment.

Lifted verbatim out of analysis/river_turbidity.py on 2026-08-06. That file is
the mining/turbidity scanner, retired the same day
(docs/MINING_FINDINGS_2026-08.md §10), and its nightly rotation was the ONLY
caller of enrich_park_infra() — the opportunistic backfill that fills
osm_places and roads_heigit for parks that have none. Good code with a dead
caller is a slow-motion regression, so it moves here and gains a CLI of its
own; river_turbidity.py now imports from this module.

Why Geofabrik + osmium and not Overpass: the AOI gap alone is ~223,000 km²
across 5 countries. Overpass would need ~20 oversized bbox queries that time
out, is rate-limited, and offers no resumability. One deterministic country
download plus offline extraction wins on every axis, and the PBF is deleted
afterwards (~750 MB transient).

Usage:
  python3 scripts/osm_pbf.py --enrich-missing            # every park with no rows
  python3 scripts/osm_pbf.py --enrich-missing --iso COG  # one country
  python3 scripts/osm_pbf.py --enrich-missing --dry-run
"""

import argparse
import json
import math
import os
import subprocess
import sys

DB_PATH = os.environ.get("MP5_DB", "db.sqlite3")

# country ISO3 prefix -> geofabrik africa/ region name. PBFs are downloaded
# on demand to /tmp, used for ALL parks of the country in one pass (so the big
# file is only ever fetched once), then deleted.
# NOTE: DR Congo is 'congo-democratic-republic'; the plausible-looking
# 'democratic-republic-of-the-congo' 404s.
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

# Local PBFs already staged by earlier work; preferred over a fresh download.
LOCAL_PBF_DIRS = ["data/osm_geofabrik", "data/osm_raw"]


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


def find_pbf(region):
    """Path to an already-downloaded country PBF, or None."""
    for d in LOCAL_PBF_DIRS:
        for name in (f"{region}.osm.pbf", f"{region}-latest.osm.pbf"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
    return None


def ensure_pbf(iso, dest_dir="/tmp"):
    """(path, is_temporary) for a country PBF, downloading if needed."""
    region = GEOFABRIK.get(iso)
    if not region:
        raise SystemExit(f"no geofabrik mapping for {iso}")
    local = find_pbf(region)
    if local:
        return local, False
    pbf = os.path.join(dest_dir, f"{region}-latest.osm.pbf")
    if not os.path.exists(pbf):
        url = f"https://download.geofabrik.de/africa/{region}-latest.osm.pbf"
        print(f"downloading {url}", file=sys.stderr)
        subprocess.run(["curl", "-sfL", "--retry", "3", url, "-o", pbf], check=True)
    return pbf, pbf.startswith("/tmp/")


def extract_bbox(pbf_path, key, bbox):
    """bbox-extract an area from a country PBF. Caller must delete the result."""
    w, s, e, n = bbox
    out = f"/tmp/{key}_area.osm.pbf"
    subprocess.run(["osmium", "extract", "-b", f"{w},{s},{e},{n}",
                    pbf_path, "-o", out, "--overwrite"], check=True)
    return out


def _osmium_extract(pbf_path, park_id, bbox, out):
    """bbox-extract park area from country PBF; derive waterway geojson.
    Returns path to the bbox-extracted park PBF (caller may reuse for
    infra enrichment, and must delete it)."""
    tmp = extract_bbox(pbf_path, f"{park_id}_ww", bbox)
    tmp2 = f"/tmp/{park_id}_ww2.osm.pbf"
    subprocess.run(["osmium", "tags-filter", tmp, "w/waterway=river,stream",
                    "-o", tmp2, "--overwrite"], check=True)
    subprocess.run(["osmium", "export", tmp2, "-o", out, "--overwrite",
                    "--geometry-types=linestring"], check=True)
    try: os.remove(tmp2)
    except OSError: pass
    return tmp


def _export_filtered(park_pbf, park_id, filt, geom_types):
    """osmium tags-filter + export -> parsed geojson features (or [])."""
    key = park_id.replace(":", "_").replace("/", "_")
    tmpf = f"/tmp/{key}_enr.osm.pbf"
    tmpj = f"/tmp/{key}_enr.geojson"
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


def enrich_park_infra(park_pbf, park_id, force=False, replace=False,
                      append=False):
    """Fill osm_places (placenames + named rivers/streams as points) and
    roads_heigit for parks that have no rows yet. Runs opportunistically
    while the country PBF is on disk; no-op when data already present.

    park_id is used verbatim as the table key. AOI callers pass their bare AOI
    id (see docs/AOI_HANDOVER.md §1) — not an `aoi:<id>` scope key, which no
    read path resolves.

    Three modes, because "skip if rows exist" is right for a park and wrong for
    an AOI:

    * default — skip if the key already has rows. The park backfill: cheap,
      idempotent, safe to call from any cron that happens to hold a PBF.
    * ``replace=True`` — delete this key's rows first. What an AOI's FIRST
      country does, so the real ingest supersedes the clip preview's copied
      park rows atomically.
    * ``append=True`` — insert without the emptiness check, skipping osm_ids
      the key already has. What an AOI's LATER countries do. Without it a
      multi-country AOI silently ingested only its first country (the default
      mode saw non-empty tables and returned): XSA spans 3 countries and had
      432 placenames for 485,000 km². Geofabrik country extracts overlap at
      borders, hence the osm_id filter rather than a blind insert.
    """
    import sqlite3
    db = sqlite3.connect(DB_PATH, timeout=30)
    try:
        n_places = db.execute("SELECT COUNT(*) FROM osm_places WHERE park_id=?",
                              (park_id,)).fetchone()[0]
        n_roads = db.execute("SELECT COUNT(*) FROM roads_heigit WHERE park_id=?",
                             (park_id,)).fetchone()[0]
        force = force or replace

        if n_places == 0 or force or append:
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
            if force:
                db.execute("DELETE FROM osm_places WHERE park_id=?", (park_id,))
            elif append and rows:
                have = {r[0] for r in db.execute(
                    "SELECT osm_id FROM osm_places WHERE park_id=?", (park_id,))}
                rows = [r for r in rows if r[5] not in have]
            if rows:
                db.executemany("""INSERT INTO osm_places
                    (park_id, place_type, name, lat, lon, osm_id, osm_tags)
                    VALUES (?,?,?,?,?,?,?)""", rows)
                db.commit()
                print(f"  enriched osm_places: {park_id} +{len(rows)}", file=sys.stderr)

        if n_roads == 0 or force or append:
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
            if force:
                db.execute("DELETE FROM roads_heigit WHERE park_id=?", (park_id,))
            elif append and rows:
                have = {r[0] for r in db.execute(
                    "SELECT osm_id FROM roads_heigit WHERE park_id=?", (park_id,))}
                rows = [r for r in rows if r[1] not in have]
            if rows:
                db.executemany("""INSERT INTO roads_heigit
                    (park_id, osm_id, name, highway_type, surface, length_km,
                     geojson, dl_class_2024, passability)
                    VALUES (?,?,?,?,?,?,?,?,?)""", rows)
                db.commit()
                print(f"  enriched roads_heigit: {park_id} +{len(rows)}", file=sys.stderr)
    finally:
        db.close()


# --------------------------------------------------------------------------
# CLI: the standalone backfill the turbidity cron used to do as a side effect.

def parks_missing_infra(iso=None):
    """[(park_id, missing)] for parks with no osm_places and/or no roads."""
    import sqlite3
    parks = [p for p in json.load(open("data/keystones_with_boundaries.json"))
             if p.get("geometry")]
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        out = []
        for p in parks:
            pid = p["id"]
            if iso and not pid.startswith(iso + "_"):
                continue
            if pid.split("_")[0] not in GEOFABRIK:
                continue
            miss = []
            if not db.execute("SELECT 1 FROM osm_places WHERE park_id=? LIMIT 1",
                              (pid,)).fetchone():
                miss.append("places")
            if not db.execute("SELECT 1 FROM roads_heigit WHERE park_id=? LIMIT 1",
                              (pid,)).fetchone():
                miss.append("roads")
            if miss:
                out.append((p, miss))
        return out
    finally:
        db.close()


def enrich_missing(iso=None, buffer_km=50, dry_run=False):
    todo = parks_missing_infra(iso)
    if not todo:
        print("nothing missing")
        return
    for p, miss in todo:
        print(f"{p['id']}: missing {'+'.join(miss)}")
    if dry_run:
        return
    # group by country so each PBF is downloaded once
    by_iso = {}
    for p, _ in todo:
        by_iso.setdefault(p["id"].split("_")[0], []).append(p)
    for country, plist in sorted(by_iso.items()):
        pbf, temporary = ensure_pbf(country)
        try:
            for p in plist:
                print(f"  extracting {p['id']}", file=sys.stderr)
                area = extract_bbox(pbf, p["id"], park_bbox(p, buffer_km))
                try:
                    enrich_park_infra(area, p["id"])
                finally:
                    try: os.remove(area)
                    except OSError: pass
        finally:
            if temporary:
                try: os.remove(pbf)
                except OSError: pass


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--enrich-missing", action="store_true",
                    help="backfill osm_places/roads_heigit for parks with none")
    ap.add_argument("--iso", help="restrict to one ISO3 country prefix")
    ap.add_argument("--buffer-km", type=float, default=50)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.enrich_missing:
        ap.error("need --enrich-missing")
    enrich_missing(args.iso, args.buffer_km, args.dry_run)


if __name__ == "__main__":
    main()
