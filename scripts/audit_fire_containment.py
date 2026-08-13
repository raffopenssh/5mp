#!/usr/bin/env python3
"""Measure how far `protected_area_id` and `in_protected_area` are from
"this fire burned inside this park" (F10, docs/AOI_STRUCTURAL_FIXES.md).

Three quantities exist and only one of them is containment:

    tagged   COUNT(*) WHERE protected_area_id = X
             — the nearest park boundary is X and it is within
             park_assigner.ASSIGN_MAX_DIST_KM (100 km). A catchment, not a park.
    flagged  ... AND in_protected_area = 1
             — what the ingest recorded as dist_km == 0.0 AT INGEST TIME.
    inside   point-in-polygon against data/keystones_with_boundaries.json
             — containment, measured now, by this script.

`tagged` is the number every user-facing "fires in park X" count in srv/ is
built from, and it is a median 9.8x overstatement. `flagged` is close but not
free: it is a stored answer from whatever rule was current when the row was
written, so it drifts from the boundary file the app draws (invariant: a
provenance label names the instrument, not the code that read it). This script
is the instrument; it recomputes `inside` from the same polygons the map uses,
so a boundary edit shows up here as a delta rather than as silence.

WHY IT DOES NOT WRITE. The fix for F10 is in the *queries* (add
`AND in_protected_area = 1`, or select by polygon), not in the data. Re-deriving
the flag is a separate, larger change with its own risk — see
docs/agents/fire.md "F10". This script exists so that change can be measured
before and after, and so a boundary update cannot silently move published
counts without anyone noticing.

Usage:
    python3 scripts/audit_fire_containment.py                 # all parks
    python3 scripts/audit_fire_containment.py --park CAF_Chinko ...
    python3 scripts/audit_fire_containment.py --csv out.csv
    python3 scripts/audit_fire_containment.py --sample 20000  # fast estimate

Runtime: ~12 min for all 163 parks / 42.9M rows (full), ~1 min sampled.
"""

import argparse
import csv
import json
import sqlite3
import statistics
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import shape

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES_FILE = BASE_DIR / "data" / "keystones_with_boundaries.json"


def load_boundaries():
    with open(KEYSTONES_FILE) as f:
        return {p["id"]: shape(p["geometry"])
                for p in json.load(f) if p.get("geometry")}


def audit_park(conn, park_id, geom, sample=0):
    sql = ("SELECT longitude, latitude, COALESCE(in_protected_area, 0) "
           "FROM fire_detections WHERE protected_area_id = ?")
    params = [park_id]
    if sample:
        sql += " LIMIT ?"
        params.append(sample)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return None
    a = np.asarray(rows, dtype=float)
    if geom is None:
        # No polygon => containment is UNMEASURED, which is not the same as
        # zero (invariant 12: a measurement's absence and its refusal differ).
        return {"park": park_id, "tagged": len(rows),
                "flagged": int(a[:, 2].sum()), "inside": None,
                "flagged_outside": None}
    inside = shapely.contains_xy(geom, a[:, 0], a[:, 1])
    flagged = a[:, 2] > 0
    return {
        "park": park_id,
        "tagged": len(rows),
        "flagged": int(flagged.sum()),
        "inside": int(inside.sum()),
        # rows the ingest called "inside" that today's polygon does not contain
        "flagged_outside": int((flagged & ~inside).sum()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--park", nargs="*", help="limit to these park ids")
    ap.add_argument("--csv", help="write per-park rows here")
    ap.add_argument("--sample", type=int, default=0,
                    help="rows per park (0 = all). A sample is an ESTIMATE and "
                         "is labelled as one.")
    args = ap.parse_args()

    geoms = load_boundaries()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    parks = args.park or [r[0] for r in conn.execute(
        "SELECT DISTINCT protected_area_id FROM fire_detections "
        "WHERE protected_area_id IS NOT NULL AND protected_area_id != ''")]

    out = []
    for i, p in enumerate(sorted(parks), 1):
        r = audit_park(conn, p, geoms.get(p), args.sample)
        if r is None:
            continue
        out.append(r)
        print(f"[{i}/{len(parks)}] {p:34} tagged {r['tagged']:9,} "
              f"flagged {r['flagged']:8,} inside "
              + ("unmeasured" if r["inside"] is None else f"{r['inside']:8,}"),
              flush=True)

    if not out:
        # invariant 1: nothing matched is not an answer.
        print("UNFINISHED: no parks matched", file=sys.stderr)
        return 1

    measured = [r for r in out if r["inside"] is not None]
    tagged = sum(r["tagged"] for r in out)
    flagged = sum(r["flagged"] for r in out)
    inside = sum(r["inside"] for r in measured)
    fo = sum(r["flagged_outside"] for r in measured)

    label = f"SAMPLED {args.sample}/park (ESTIMATE)" if args.sample else "FULL"
    unmeasured = len(out) - len(measured)
    print(f"\n=== {label} — {len(out)} park(s), "
          f"{unmeasured} without a polygon (unmeasured) ===")

    def ratio(smaller):
        # A ratio against zero is not a large ratio, it is no ratio at all.
        if smaller == 0:
            return "none at all"
        return f"{tagged / smaller:.1f}x smaller"

    print(f"tagged  (protected_area_id = X)          {tagged:12,}")
    print(f"flagged (+ in_protected_area = 1)        {flagged:12,}  {ratio(flagged)}")
    print(f"inside  (point-in-polygon, measured now) {inside:12,}  {ratio(inside)}")
    if flagged:
        print(f"flagged but NOT inside today's polygon   {fo:12,}  "
              f"({100 * fo / flagged:.2f}% of flagged)")
    ratios = [r["tagged"] / r["inside"] for r in measured if r["inside"]]
    if ratios:
        print(f"per-park tagged/inside: median {statistics.median(ratios):.1f}x, "
              f"max {max(ratios):,.0f}x")
    zero = [r["park"] for r in measured if r["inside"] == 0 and r["tagged"]]
    if zero:
        print(f"{len(zero)} park(s) with detections tagged but NONE inside: "
              + ", ".join(sorted(zero)))

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0]))
            w.writeheader()
            w.writerows(out)
        print(f"wrote {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
