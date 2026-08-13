#!/usr/bin/env python3
"""What the UCDP GED mine list can and cannot say about the CAR sheet.

Same discipline as scripts/eval_crisistracker.py, and read that header first:
a mine list recovered from conflict reports covers conflict, not mining, so
its capture is scored against random ground in the list's OWN convex hull,
never the whole sheet; a lift ships with an exact two-sided binomial p and
the minimum lift its n could have detected; commodity subsets below MIN_N are
declined with the same floor eval_affinity uses (invariant 12).

WHY ONLY THE CAR SHEET. The 96 GED sites split CAF 27 / COD 61 / SDN 7 /
TZA 1. COD has no geology sheet in this project; SDN and TZA are below MIN_N.
So the CAR block is the measurement and the rest is reach description.

WHAT THIS LIST ADDS OVER CRISIS TRACKER. Crisis Tracker's 41 CAR mines are
LRA-era east (Haute-Kotto, Mbomou). GED's reach is whatever the world's news
wires georeferenced since 1989 -- Ndassima, Bria, Koki -- so the two
conflict-derived lists cover different attacks in overlapping ground, and the
reach block below says how much of GED is new relative to IPIS *and* Crisis
Tracker rather than a re-report of either.

Input:  data/eval/ucdp/mine_sites.json      (scripts/ucdp_mines.py)
        data/geomaps/car_units.geojson
        data/ipis/caf_mines_ipis.csv
        data/eval/crisistracker/mine_sites.json (optional, for overlap)
Output: data/eval/ucdp/reach_car.json

Usage: python3 scripts/eval_ucdp.py
"""
import csv
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
SITES = ROOT / "data" / "eval" / "ucdp" / "mine_sites.json"
UNITS = ROOT / "data" / "geomaps" / "car_units.geojson"
IPIS = ROOT / "data" / "ipis" / "caf_mines_ipis.csv"
CT = ROOT / "data" / "eval" / "crisistracker" / "mine_sites.json"
OUT = ROOT / "data" / "eval" / "ucdp" / "reach_car.json"

SHEET_COMMODITIES = ("gold", "diamond")
MIN_N = 8
N_RANDOM = 20000
SEED = 7


def km(a, b):
    dy = (b[1] - a[1]) * 111.32
    dx = (b[0] - a[0]) * 111.32 * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot(dx, dy)


def binom_two_sided(k, n, p):
    if n == 0:
        return None
    from math import comb
    obs = abs(k - n * p)
    return sum(comb(n, i) * p ** i * (1 - p) ** (n - i)
               for i in range(n + 1) if abs(i - n * p) >= obs - 1e-12)


def min_detectable_lift(n, p, alpha=0.05):
    if n == 0 or p <= 0 or p >= 1:
        return None
    hi = lo = None
    for k in range(int(round(n * p)), n + 1):
        if binom_two_sided(k, n, p) < alpha:
            hi = round((k / n) / p, 3)
            break
    for k in range(int(round(n * p)), -1, -1):
        if binom_two_sided(k, n, p) < alpha:
            lo = round((k / n) / p, 3)
            break
    return {"above": hi, "below": lo, "alpha": alpha}


def main():
    if not SITES.exists():
        raise SystemExit(f"missing {SITES} -- run scripts/ucdp_mines.py")
    doc = json.loads(SITES.read_text())
    all_sites = doc["sites"]
    sites = [s for s in all_sites if s["iso3"] == "CAF"]
    if not sites:
        raise SystemExit("no CAR sites in the GED list -- the country filter "
                         "failed; this is not 'no mines'")

    units = json.loads(UNITS.read_text())["features"]
    geoms = [shape(f["geometry"]) for f in units]
    props = [f["properties"] for f in units]
    tree = STRtree(geoms)
    land = unary_union(geoms)

    def graded_for(p, coms):
        for i in tree.query(p):
            if geoms[i].contains(p):
                return {a["commodity"] for a in (props[i].get("affinity") or [])
                        if a["weight"] >= 1 and a["commodity"] in coms}
        return None

    # ---- 1. reach vs the lists we already hold ----------------------------
    ipis = [(float(r["longitude"]), float(r["latitude"]))
            for r in csv.DictReader(IPIS.open()) if r.get("longitude")]
    nearest_ipis = [min(km((s["lon"], s["lat"]), q) for q in ipis)
                    for s in sites]
    reach = {
        "by_country_all_sites": dict(Counter(s["iso3"] for s in all_sites)),
        "ipis_sites": len(ipis),
        "nearest_ipis_km": {
            "median": round(statistics.median(nearest_ipis), 1),
            "min": round(min(nearest_ipis), 1),
            "max": round(max(nearest_ipis), 1),
        },
        "within_25km_of_an_ipis_mine": sum(1 for d in nearest_ipis if d <= 25),
        "adm1_reported": dict(Counter(s["adm1_reported"] for s in sites)),
    }
    if CT.exists():
        ct = [(s["lon"], s["lat"])
              for s in json.loads(CT.read_text())["sites"]
              if s["iso3"] == "CAF"]
        nearest_ct = [min(km((s["lon"], s["lat"]), q) for q in ct)
                      for s in sites]
        reach["crisistracker_car_sites"] = len(ct)
        reach["nearest_crisistracker_km"] = {
            "median": round(statistics.median(nearest_ct), 1),
            "min": round(min(nearest_ct), 1),
            "max": round(max(nearest_ct), 1),
        }
        reach["within_25km_of_a_crisistracker_mine"] = sum(
            1 for d in nearest_ct if d <= 25)
        reach["new_ground"] = sum(
            1 for di, dc in zip(nearest_ipis, nearest_ct)
            if di > 25 and dc > 25)

    # ---- 2. unlabelled occurrence vs graded ground ------------------------
    pts = [Point(s["lon"], s["lat"]) for s in sites]
    on_sheet = [(s, p) for s, p in zip(sites, pts) if land.contains(p)]
    off = len(sites) - len(on_sheet)

    hull = unary_union([p for _, p in on_sheet]).convex_hull.intersection(land)
    if hull.is_empty or hull.area <= 0:
        raise SystemExit("the sites' hull does not meet the mapped sheet")

    import numpy as np
    rng = np.random.default_rng(SEED)
    minx, miny, maxx, maxy = hull.bounds
    rand, guard = [], 0
    while len(rand) < N_RANDOM:
        guard += 1
        if guard > N_RANDOM * 400:
            raise SystemExit("could not sample the hull; region degenerate")
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if hull.contains(p):
            rand.append(p)

    def block(subset, label):
        n = len(subset)
        if n < MIN_N:
            return {"label": label, "sites": n,
                    "verdict": f"n<{MIN_N}: no lift computed"}
        hits = sum(1 for _, p in subset
                   if (graded_for(p, SHEET_COMMODITIES) or set()))
        rhits = sum(1 for p in rand
                    if (graded_for(p, SHEET_COMMODITIES) or set()))
        area = rhits / len(rand)
        cap = hits / n
        return {
            "label": label, "sites": n,
            "capture": round(cap, 4),
            "area_share": round(area, 4),
            "lift": round(cap / area, 3) if area else None,
            "p_two_sided": (round(binom_two_sided(hits, n, area), 5)
                            if area else None),
            "min_detectable_lift": min_detectable_lift(n, area),
            "note": "capture = share of mines on ground the sheet grades for "
                    "gold or diamond; area_share = the same share of random "
                    "ground inside the list's own hull",
        }

    exact = [(s, p) for s, p in on_sheet if s["precision"] == "reported"]
    approx = [(s, p) for s, p in on_sheet if s["precision"] != "reported"]

    unlabelled = {
        "all": block(on_sheet, "all GED CAR mine sites"),
        "well_located_only": block(exact, "sites with where_prec 1 and a "
                                          "tight cluster"),
        "approximate_only": block(approx, "sites whose coordinate is "
                                          "approximate (sensitivity check)"),
    }

    # ---- 3. commodity subsets, declined honestly if small -----------------
    by_com = {}
    for com in SHEET_COMMODITIES:
        sub = [(s, p) for s, p in on_sheet if com in s["commodities"]]
        if len(sub) < MIN_N:
            by_com[com] = {"sites": len(sub),
                           "verdict": f"n<{MIN_N}: no lift computed",
                           "named_at": [s["name"] for s, _ in sub if s["name"]]}
            continue
        hits = sum(1 for _, p in sub if com in (graded_for(p, (com,)) or set()))
        rhits = sum(1 for p in rand if com in (graded_for(p, (com,)) or set()))
        area = rhits / len(rand)
        cap = hits / len(sub)
        by_com[com] = {"sites": len(sub), "capture": round(cap, 4),
                       "area_share": round(area, 4),
                       "lift": round(cap / area, 3) if area else None,
                       "p_two_sided": round(binom_two_sided(hits, len(sub),
                                                            area), 5),
                       "min_detectable_lift": min_detectable_lift(len(sub),
                                                                  area)}

    out = {
        "generated_by": "scripts/eval_ucdp.py",
        "sheet": "car",
        "source": doc["source"],
        "accessed": doc["accessed"],
        "citation": doc["citation"],
        "terms": doc["terms"],
        "notice": doc["notice"],
        "unit": doc["unit"],
        "claim_scored": "UNLABELLED OCCURRENCE: does a reported mine sit on "
                        "ground the sheet grades for gold or diamond? This is "
                        "a weaker claim than a per-commodity lift and must "
                        "not be quoted as one.",
        "baseline": "random ground inside the convex hull of the sites, "
                    f"clipped to mapped units ({100 * hull.area / land.area:.1f}%"
                    " of the sheet)",
        "sites_total_car": len(sites),
        "sites_off_mapped_sheet": off,
        "reach": reach,
        "unlabelled_occurrence": unlabelled,
        "by_commodity": by_com,
        "caveat": "A news-wire-derived event list reports where journalists "
                  "and monitors looked. These mines are where organised "
                  "violence was recorded -- not a survey. The hull baseline "
                  "controls for the list's footprint at map scale, not for "
                  "which mines inside it get fought over.",
    }
    OUT.write_text(json.dumps(out, indent=1))

    print(f"CAR: {len(sites)} GED mine sites ({off} off the mapped sheet)")
    print(f"  reach: nearest IPIS mine {reach['nearest_ipis_km']['median']} km "
          f"(median); {reach['within_25km_of_an_ipis_mine']} within 25 km")
    if "nearest_crisistracker_km" in reach:
        print(f"         nearest Crisis Tracker mine "
              f"{reach['nearest_crisistracker_km']['median']} km (median); "
              f"{reach['new_ground']} sites >25 km from BOTH lists")
    for k, v in unlabelled.items():
        if "verdict" in v:
            print(f"  {k:20} n={v['sites']}: {v['verdict']}")
        else:
            print(f"  {k:20} n={v['sites']} capture {v['capture']:.1%} "
                  f"area {v['area_share']:.1%} lift {v['lift']} "
                  f"p={v['p_two_sided']} "
                  f"(could only have shown lift >={v['min_detectable_lift']['above']} "
                  f"or <={v['min_detectable_lift']['below']})")
    for com, v in by_com.items():
        if "verdict" in v:
            print(f"  {com:20} n={v['sites']}: {v['verdict']}")
        else:
            print(f"  {com:20} n={v['sites']} lift {v['lift']} "
                  f"p={v['p_two_sided']}")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
