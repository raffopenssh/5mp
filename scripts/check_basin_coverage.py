#!/usr/bin/env python3
"""QA the park_basins table produced by scripts/fetch_park_basins.py.

Read-only. Answers the two questions that decide whether the basin rescope
(docs/MINING_FINDINGS_2026-08.md §Plan step 1) is usable per park:

  coverage  = fraction of the PARK that the contributing basin contains.
              Low is not automatically a bug: a park on a drainage divide
              (RWA_Volcans, TZA_Kilimanjaro) or in an endorheic pan
              (NAM_Etosha) has almost no contributing area above its outlet.
              It IS the reason analysis/flow_corridor.py scans basin U park.
  size      = basin area. >200,000 km2 means the outlet snapped onto a
              continental trunk (Zambezi, Nile) and "upstream pressure" is no
              longer attributable to the park; those need reporting by
              distance/travel time, not membership.

Usage:
  python3 scripts/check_basin_coverage.py                 # summary + outliers
  python3 scripts/check_basin_coverage.py --all           # every park
  python3 scripts/check_basin_coverage.py --json out.json
"""
import argparse, json, os, sqlite3, statistics, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "db.sqlite3")
KEYSTONES = os.path.join(BASE, "data", "keystones_with_boundaries.json")
TRUNK_KM2 = 200_000     # above this the basin is a continental trunk


def rows():
    from shapely.geometry import shape
    parks = {p["id"]: p for p in json.load(open(KEYSTONES))}
    con = sqlite3.connect(DB)
    down = dict(con.execute(
        "SELECT park_id, length_km FROM park_basins WHERE kind='downstream'"))
    # How many SEPARATE watersheds each area drains by (migration 044). `outlets`
    # below counts outlets we ASKED about; this counts the ones that produced a
    # distinct watershed. 0 = fetched before the parts table existed; re-run
    # fetch_park_basins.py to backfill it from http_cache for free.
    nparts = dict(con.execute(
        "SELECT park_id, COUNT(*) FROM park_basin_parts WHERE kind='upstream'"
        " GROUP BY park_id"))
    out = []
    for pid, area, gj, meta in con.execute(
            "SELECT park_id, area_km2, geojson, meta FROM park_basins "
            "WHERE kind='upstream'"):
        p = parks.get(pid)
        if not p or not p.get("geometry"):
            continue
        pg = shape(p["geometry"])
        if not pg.is_valid:
            pg = pg.buffer(0)
        bg = shape(json.loads(gj))
        if not bg.is_valid:
            bg = bg.buffer(0)
        cov = (pg.intersection(bg).area / pg.area) if pg.area else 0.0
        m = json.loads(meta or "{}")
        o = (m.get("outlets") or [{}])[0]
        out.append({"park_id": pid, "basin_km2": area or 0.0,
                    "park_km2": p.get("area_km2"), "coverage": round(cov, 3),
                    "downstream_km": down.get(pid),
                    "outlets": len(m.get("outlets") or []),
                    "how": o.get("how", "?"), "river": o.get("river"),
                    "parts": nparts.get(pid, 0),
                    "precision": m.get("precision", "?"),
                    "n_reaches": m.get("n_reaches")})
    con.close()
    out.sort(key=lambda r: r["coverage"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--limit", type=int, default=25)
    a = ap.parse_args()
    rs = rows()
    if not rs:
        print("park_basins is empty - run scripts/fetch_park_basins.py --all",
              file=sys.stderr)
        return 1
    show = rs if a.all else rs[:a.limit]
    print(f"{'park':34}{'basin_km2':>11}{'cov':>6}{'down_km':>9}{'out':>5}"
          f"{'wsh':>5}{'reach':>7}  how/precision")
    for r in show:
        print(f"{r['park_id']:34}{r['basin_km2']:>11.0f}{r['coverage']:>6.2f}"
              f"{(r['downstream_km'] or 0):>9.0f}{r['outlets']:>5}"
              f"{r['parts']:>5}{(r['n_reaches'] or 0):>7}  "
              f"{r['how']} {r['precision']}")
    covs = [r["coverage"] for r in rs]
    print(f"\nn={len(rs)}  coverage median {statistics.median(covs):.2f}  "
          f"<0.5: {sum(c < 0.5 for c in covs)}  <0.2: {sum(c < 0.2 for c in covs)}")
    print(f"trunk basins (>{TRUNK_KM2:,} km2): "
          f"{sum(r['basin_km2'] > TRUNK_KM2 for r in rs)}  "
          f"low-precision polygons: {sum(r['precision'] == 'low' for r in rs)}  "
          f"no upstream rivers: {sum(not r['n_reaches'] for r in rs)}")
    multi = sum(1 for r in rs if r["parts"] > 1)
    noparts = sum(1 for r in rs if not r["parts"])
    print(f"areas draining by >1 watershed: {multi}  "
          f"(mean {statistics.mean([r['parts'] for r in rs]):.1f} each)"
          + (f"  -- {noparts} not yet split, re-run fetch_park_basins.py"
             if noparts else ""))
    miss = sum(1 for r in rs if r["basin_km2"] <= 0)
    if miss:
        print(f"WARNING {miss} basins with no area")
    if a.json:
        json.dump(rs, open(a.json, "w"), indent=1)
        print(f"-> {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
