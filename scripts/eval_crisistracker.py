#!/usr/bin/env python3
"""What the Crisis Tracker mine list can and cannot say about the CAR sheet.

WHY THIS IS A SEPARATE SCRIPT AND NOT A ROW IN eval_affinity.py
---------------------------------------------------------------
eval_affinity scores PER COMMODITY: a lift is "sites of X land in units graded
for X". Crisis Tracker names a commodity for only a handful of sites -- a
community report says "a mining site was attacked", not "an alluvial diamond
working". Handing those to the commodity scorer would either invent a commodity
or report `too_few` and stop, and both would waste the one thing this list
actually has: 41 CAR mines in prefectures NO other list we hold has ever
reached.

So this script asks the question the list can answer:

  1. REACH. Where are these mines, relative to the truth sets we already score?
     IPIS's 914 CAR sites span longitudes 14.6-17.9 (the west). Crisis Tracker's
     are in Haute-Kotto and Mbomou (the east). If the nearest IPIS mine to a
     Crisis Tracker mine is hundreds of km away, then every CAR lift we publish
     was measured on one half of the country, and this list is the first
     out-of-sample ground -- not a bigger sample of the same ground.

  2. UNLABELLED OCCURRENCE. Ignore commodity; ask whether a mine lands on
     ground the model grades for ANYTHING (gold or diamond, the two commodities
     the CAR sheet models). That is a weaker claim than a commodity lift and it
     is stated as one, but it is testable, and it is the only prospectivity
     statement an unlabelled occurrence supports.

  3. The commodity subset is reported with its n, and DECLINED below MIN_N,
     with the same rule eval_affinity uses. Five gold sites cannot tell 1.0
     from 3.0.

The baseline for (2) is random ground in the list's OWN convex hull, clipped to
mapped units -- same discipline as every reach-limited list in eval_affinity: a
community reporting network covers the communities it connects, so scoring its
capture against the whole sheet would credit the model for where the network
is. Significance is a two-sided binomial test of capture against the hull's
area share; a lift with no test beside it is what invariant 12 forbids.

Approximate coordinates (Crisis Tracker's `exact_location_of_incident_unknown`,
or a cluster whose incidents disagree by more than a few km) are scored
separately as a sensitivity check rather than dropped: dropping them would bias
the list toward the places that are easy to pin, which is the bias this whole
line of work exists to measure.

Input:  data/eval/crisistracker/mine_sites.json  (scripts/crisistracker_mines.py)
        data/geomaps/car_units.geojson
        data/ipis/caf_mines_ipis.csv
Output: data/eval/crisistracker/reach_car.json

Usage: python3 scripts/eval_crisistracker.py
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
SITES = ROOT / "data" / "eval" / "crisistracker" / "mine_sites.json"
UNITS = ROOT / "data" / "geomaps" / "car_units.geojson"
IPIS = ROOT / "data" / "ipis" / "caf_mines_ipis.csv"
OUT = ROOT / "data" / "eval" / "crisistracker" / "reach_car.json"

SHEET_COMMODITIES = ("gold", "diamond")   # what the CAR sheet models
MIN_N = 8                                  # same floor as eval_affinity
N_RANDOM = 20000
SEED = 7


def km(a, b):
    dy = (b[1] - a[1]) * 111.32
    dx = (b[0] - a[0]) * 111.32 * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot(dx, dy)


def binom_two_sided(k, n, p):
    """P(|X - np| >= |k - np|) for X ~ Bin(n, p), exact.

    n is 41; anything asymptotic here would be a normal approximation quoted at
    a sample size that does not support one.
    """
    if n == 0:
        return None
    from math import comb
    obs = abs(k - n * p)
    return sum(comb(n, i) * p ** i * (1 - p) ** (n - i)
               for i in range(n + 1) if abs(i - n * p) >= obs - 1e-12)


def min_detectable_lift(n, p, alpha=0.05):
    """Smallest lift this n could have distinguished from 1.0, both directions.

    A null without its power is a shrug wearing a result's clothes. With n=38
    and a baseline that already covers most of the hull, only a large effect
    could have cleared alpha -- and the reader must be told which.
    """
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
        raise SystemExit(f"missing {SITES} -- run scripts/crisistracker_mines.py")
    doc = json.loads(SITES.read_text())
    sites = [s for s in doc["sites"] if s["iso3"] == "CAF"]
    if not sites:
        raise SystemExit("no CAR sites in the Crisis Tracker list -- the join "
                         "on community_country failed; this is not 'no mines'")

    units = json.loads(UNITS.read_text())["features"]
    geoms = [shape(f["geometry"]) for f in units]
    props = [f["properties"] for f in units]
    tree = STRtree(geoms)
    land = unary_union(geoms)

    def graded_for(p, coms):
        """Which of `coms` the unit under p is graded for, or None if off-sheet.

        None is UNMEASURED, not zero: a point outside the mapped units is a
        point this 1964 sheet never described.
        """
        for i in tree.query(p):
            if geoms[i].contains(p):
                return {a["commodity"] for a in (props[i].get("affinity") or [])
                        if a["weight"] >= 1 and a["commodity"] in coms}
        return None

    # ---- 1. reach: how far is this list from the list we already score ----
    ipis = [(float(r["longitude"]), float(r["latitude"]))
            for r in csv.DictReader(IPIS.open()) if r.get("longitude")]
    nearest = [min(km((s["lon"], s["lat"]), q) for q in ipis) for s in sites]
    reach = {
        "ipis_sites": len(ipis),
        "ipis_lon_range": [round(min(x for x, _ in ipis), 3),
                           round(max(x for x, _ in ipis), 3)],
        "ct_lon_range": [round(min(s["lon"] for s in sites), 3),
                         round(max(s["lon"] for s in sites), 3)],
        "nearest_ipis_km": {
            "median": round(statistics.median(nearest), 1),
            "min": round(min(nearest), 1),
            "max": round(max(nearest), 1),
        },
        "within_25km_of_an_ipis_mine": sum(1 for d in nearest if d <= 25),
        "adm1_reported": dict(Counter(s["adm1_reported"] for s in sites)),
    }

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
        "all": block(on_sheet, "all Crisis Tracker CAR mine sites"),
        "well_located_only": block(exact, "sites with a reported coordinate"),
        "approximate_only": block(approx, "sites whose coordinate is "
                                          "approximate (sensitivity check)"),
    }

    # ---- 3. the commodity subset, declined honestly if small --------------
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
                       "p_two_sided": round(binom_two_sided(hits, len(sub), area), 5)}

    sheet_rand = []
    sminx, sminy, smaxx, smaxy = land.bounds
    guard = 0
    while len(sheet_rand) < 4000:
        guard += 1
        if guard > 4000 * 400:
            raise SystemExit("could not sample the sheet")
        p = Point(rng.uniform(sminx, smaxx), rng.uniform(sminy, smaxy))
        if land.contains(p):
            sheet_rand.append(p)
    sheet_share = sum(1 for p in sheet_rand
                      if (graded_for(p, SHEET_COMMODITIES) or set())) / len(sheet_rand)

    out = {
        "generated_by": "scripts/eval_crisistracker.py",
        "sheet": "car",
        "source": doc["source"],
        "notice": doc["notice"],
        "unit": "site (mine working), clustered from incident reports",
        "claim_scored": "UNLABELLED OCCURRENCE: does a reported mine sit on "
                        "ground the sheet grades for gold or diamond? This is "
                        "a weaker claim than a per-commodity lift and must not "
                        "be quoted as one.",
        "baseline": "random ground inside the convex hull of the sites, "
                    f"clipped to mapped units ({100 * hull.area / land.area:.1f}%"
                    " of the sheet)",
        # Context, never a denominator: the hull is not a random part of the
        # sheet, and a capture measured in the hull divided by an area measured
        # over the sheet would be a ratio of two different questions. Printed
        # because "the network's footprint is more graded than the country" is
        # itself a fact about this list's reach.
        "graded_area_share_whole_sheet": round(sheet_share, 4),
        "sites_total": len(sites),
        "sites_off_mapped_sheet": off,
        "reach": reach,
        "unlabelled_occurrence": unlabelled,
        "by_commodity": by_com,
        "caveat": "A community early-warning network reports where its "
                  "communities are. These mines are where somebody was "
                  "attacked and somebody could report it -- not a survey. The "
                  "hull baseline controls for the network's footprint at map "
                  "scale, not for which mines inside it get raided.",
    }
    OUT.write_text(json.dumps(out, indent=1))

    print(f"CAR: {len(sites)} Crisis Tracker mine sites "
          f"({off} off the mapped sheet)")
    print(f"  reach: nearest IPIS mine is {reach['nearest_ipis_km']['median']} "
          f"km away (median); {reach['within_25km_of_an_ipis_mine']} within 25 km")
    print(f"         IPIS spans lon {reach['ipis_lon_range']}, "
          f"Crisis Tracker {reach['ct_lon_range']}")
    for k, v in unlabelled.items():
        if "verdict" in v:
            print(f"  {k:20} n={v['sites']}: {v['verdict']}")
        else:
            print(f"  {k:20} n={v['sites']} capture {v['capture']:.1%} "
                  f"area {v['area_share']:.1%} lift {v['lift']} "
                  f"p={v['p_two_sided']} "
                  f"(could only have shown lift >={v['min_detectable_lift']['above']} "
                  f"or <={v['min_detectable_lift']['below']})")
    print(f"  graded ground: {unlabelled['all']['area_share']:.1%} of the "
          f"list's hull vs {sheet_share:.1%} of the whole sheet")
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
