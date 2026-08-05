#!/usr/bin/env python3
"""Assemble data/mining_truth/negatives.json — the confuser set for
scripts/eval_mining_detector.py (docs/MINING_FINDINGS_2026-08.md §Plan step 4).

Why negatives at all: the old scanner's failure was not low recall, it was that
its top-scored "mining sites" were a sandbank and a rice paddy. An AUC against
mine-vs-random-background cannot see that, because random background is easy.
Precision has to be measured against the things that actually look like a pit:
bare, bright, ferruginous, near water, and not a mine.

Classes, and how honest each one is:

  documented_fp   the two adjudicated false positives named in the findings doc.
                  Highest confidence: someone looked.
  adjudicated     candidates from a scan that a human checked in Esri imagery
                  (analysis/chip_grid.py) and found no pit morphology at. Added
                  by hand via --adjudicated, with the scan file recorded.
  village         OSM place=village/hamlet/town centroids. Bare compacted earth,
                  roofs, ferruginous tracks - the classic confuser. Certain to
                  be *a village*; a village can still have pits beside it, so we
                  keep only points >1 km from any IPIS visited mine.
  burn_scar       cells with VIIRS fire detections in the current dry season.
                  Fresh burns are dark-bare and score high on BSI/-NDVI. Same
                  IPIS exclusion.
  water           HydroRIVERS/basin-river vertices on >=4th-order reaches:
                  sandbanks and turbid channels, the documented top-FP class.

Village/burn/water are *generated*, so they are weaker evidence than the
adjudicated ones: a point of class `village` is a place we are confident is a
village, not a place we are confident has no pit. The evaluator reports
precision per class for exactly this reason - a detector that fires on
`village` is differently broken from one that fires on `bare_savanna`.

Usage:
  python3 scripts/build_mining_negatives.py --park CAF_Chinko
  python3 scripts/build_mining_negatives.py --park CAF_Chinko \
      --adjudicated data/mining_candidates/CAF_Chinko.json --ranks 0-11 \
      --class bare_savanna --note "Esri z16: dry savanna, no pit morphology"
"""
import argparse, csv, json, math, os, sqlite3, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "db.sqlite3")
OUT = os.path.join(BASE, "data", "mining_truth", "negatives.json")
IPIS_EXCL_KM = 1.0     # do not call anything a negative this close to a visited
                       # mine - IPIS positional accuracy is village-level
THIN_KM = 2.0          # spatial thinning within a generated class

DOCUMENTED = [
    {"lon": 24.23213, "lat": 6.42818, "class": "sandbank",
     "park_id": "CAF_Chinko", "confidence": "high",
     "note": "sandbank in a forested meander; old scanner scored it 0.95 "
             "(docs/MINING_FINDINGS_2026-08.md)"},
    {"lon": 15.03891, "lat": 11.42193, "class": "irrigated_field",
     "park_id": "CMR_Waza", "confidence": "high",
     "note": "irrigated rice paddies; old scanner top hit "
             "(docs/MINING_FINDINGS_2026-08.md)"},
]


def km(a, b):
    return math.hypot((a[0] - b[0]) * 111 * math.cos(math.radians(a[1])),
                      (a[1] - b[1]) * 111)


def ipis_points():
    pts = []
    for f in ("caf", "cod"):
        p = os.path.join(BASE, "data", "ipis", f"{f}_mines_ipis.csv")
        if not os.path.exists(p):
            continue
        for r in csv.DictReader(open(p)):
            try:
                pts.append((float(r["longitude"]), float(r["latitude"])))
            except Exception:
                continue
    return pts


def thin(items, min_km=THIN_KM):
    out = []
    for it in items:
        p = (it["lon"], it["lat"])
        if any(km(p, (o["lon"], o["lat"])) < min_km for o in out):
            continue
        out.append(it)
    return out


def near(p, pts, limit_km):
    return any(km(p, q) < limit_km for q in pts)


def gen_villages(con, park, ipis, limit):
    rows = con.execute(
        "SELECT lon, lat, name, place_type FROM osm_places WHERE park_id=? AND "
        "place_type IN ('village','hamlet','town','city')", (park,)).fetchall()
    out = [{"lon": lo, "lat": la, "class": "village", "park_id": park,
            "confidence": "generated", "note": f"OSM {pt}: {nm}"}
           for lo, la, nm, pt in rows
           if not near((lo, la), ipis, IPIS_EXCL_KM)]
    return thin(out)[:limit]


def gen_burns(con, park, ipis, limit, bbox=None):
    """Burn scars inside the park's basin bbox.

    fire_detections has no park_id (only protected_area_id, set for detections
    inside a keystone boundary) - and we specifically want burns in the basin,
    including outside the park, since that is where the scan looks. So query by
    bbox and clip to the basin geometry.
    """
    if bbox is None:
        return []
    w, s, e, n = bbox
    rows = con.execute(
        # latitude first: idx_fire_location is (latitude, longitude), and with
        # longitude leading SQLite scans all 6M rows (>30 s).
        "SELECT round(longitude,2) lo, round(latitude,2) la, COUNT(*) c, "
        "MAX(acq_date) last FROM fire_detections "
        "WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? "
        "AND acq_date >= date('now','-2 years') "
        "GROUP BY 1,2 HAVING c >= 3 ORDER BY c DESC LIMIT 4000",
        (s, n, w, e)).fetchall()
    out = [{"lon": lo, "lat": la, "class": "burn_scar", "park_id": park,
            "confidence": "generated",
            "note": f"{c} VIIRS detections, last {last}"}
           for lo, la, c, last in rows
           if not near((lo, la), ipis, IPIS_EXCL_KM)]
    return thin(out)[:limit]


def gen_water(con, park, ipis, limit):
    rows = con.execute(
        "SELECT geojson FROM park_basin_rivers WHERE park_id=? AND "
        "stream_order >= 4", (park,)).fetchall()
    out = []
    for (gj,) in rows:
        cs = json.loads(gj)["coordinates"]
        lo, la = cs[len(cs) // 2][:2]
        if near((lo, la), ipis, IPIS_EXCL_KM):
            continue
        out.append({"lon": round(lo, 5), "lat": round(la, 5), "class": "water",
                    "park_id": park, "confidence": "generated",
                    "note": "basin river reach, Strahler >= 4 (sandbank/turbid)"})
    return thin(out)[:limit]


def parse_ranks(spec):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--park", action="append", default=[])
    ap.add_argument("--limit-per-class", type=int, default=60)
    ap.add_argument("--adjudicated", help="scan json whose ranked sites a human "
                                         "checked and rejected")
    ap.add_argument("--ranks", default="", help="e.g. 0-11,15")
    ap.add_argument("--class", dest="cls", default="bare_savanna")
    ap.add_argument("--note", default="")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    existing = {"sites": []}
    if os.path.exists(a.out):
        existing = json.load(open(a.out))
    bykey = {(round(s["lon"], 5), round(s["lat"], 5)): s
             for s in existing.get("sites", [])}
    for s in DOCUMENTED:
        bykey.setdefault((round(s["lon"], 5), round(s["lat"], 5)), s)

    ipis = ipis_points()
    con = sqlite3.connect(DB)
    sys.path.insert(0, os.path.join(BASE, "analysis"))
    import flow_corridor as fc
    added = {}
    for park in a.park:
        geom = fc.scan_geom(park)
        bbox = geom.bounds if geom is not None else None
        for fn in (gen_villages, gen_burns, gen_water):
            got = (fn(con, park, ipis, a.limit_per_class, bbox)
                   if fn is gen_burns else
                   fn(con, park, ipis, a.limit_per_class))
            if fn is gen_burns and geom is not None:
                from shapely.geometry import Point
                got = [s for s in got if geom.contains(Point(s["lon"], s["lat"]))]
            for s in got:
                k = (round(s["lon"], 5), round(s["lat"], 5))
                if k not in bykey:
                    bykey[k] = s
                    added[s["class"]] = added.get(s["class"], 0) + 1
    con.close()

    if a.adjudicated:
        d = json.load(open(a.adjudicated))
        ranks = parse_ranks(a.ranks) if a.ranks else range(len(d["sites"]))
        for i in ranks:
            if i >= len(d["sites"]):
                continue
            s = d["sites"][i]
            k = (round(s["lon"], 5), round(s["lat"], 5))
            bykey[k] = {"lon": s["lon"], "lat": s["lat"], "class": a.cls,
                        "park_id": d["park_id"], "confidence": "adjudicated",
                        "note": a.note or "human-checked in Esri imagery, "
                                          "no pit morphology",
                        "from_scan": os.path.basename(a.adjudicated),
                        "from_rank": i}
            added[a.cls] = added.get(a.cls, 0) + 1

    sites = sorted(bykey.values(), key=lambda s: (s["class"], s["lon"]))
    counts = {}
    for s in sites:
        counts[s["class"]] = counts.get(s["class"], 0) + 1
    out = {
        "purpose": "negative (confuser) set for scripts/eval_mining_detector.py",
        "built_by": "scripts/build_mining_negatives.py",
        "confidence_semantics": {
            "high": "documented, adjudicated in the findings doc",
            "adjudicated": "a human checked this exact point in Esri imagery",
            "generated": "class identity is certain (it IS a village/burn/river) "
                         "but absence of mining is only probable",
        },
        "counts": counts, "sites": sites,
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w"), indent=1)
    print(f"{len(sites)} negatives -> {a.out}")
    for k, v in sorted(counts.items()):
        print(f"  {k:16} {v:4}" + (f"  (+{added[k]})" if k in added else ""))


if __name__ == "__main__":
    main()
