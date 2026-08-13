#!/usr/bin/env python3
"""Nightlight (VNP46A3) radiance at reported mine sites — the WP3 skill gate.

docs/PLAN_NEW_DATA_LAYERS.md WP3: site-anchored scalars, not a raster layer.
For every reported mine site (data/eval/mining_reference.json +
data/eval/crisistracker/mine_sites.json) inside our parks/AOIs, extract a
monthly radiance series and compare against a control set of random points in
the same area hull. The measured lift (and its permutation p) IS the
deliverable; whether any UI layer ever ships is gated on it (R4). If mine
sites are dark — likely, pits have no generators — that finding is committed
as the result and no layer ships.

Units: radiance is nW/(cm^2 sr) (named in every field, invariant 7). A month
with no cloud-free observation is None, never 0 — a dark month and an
unobserved month are different states.

Schedule (mirrors backfill_settlement_surface.py): the unit of work is an
AREA (one VIIRS tile download run serves every site in it). --rotate N does
the N stalest pending areas; XSA_Study_Area is seeded first (aoi first, per
the user). State: data/nightlight_state.json — scheduling bookkeeping, a file
not a table, survives a DB restore. An area whose fetch failed months stays
pending (R1: unfinished, not zero) and exits non-zero. Each run appends only
missing months, so the monthly cron picks up the newest composite for free.

Output: data/eval/nightlights_sites.json (committed, small, R7 attribution).
Cron notifications: nightlights_success / nightlights_failed via cron_notify
(the bell resolves *_success/*_failed by shape; no frontend change needed).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

from cron_notify import notify_status  # noqa: E402
import fetch_nightlights as fnl  # noqa: E402

DB_PATH = BASE_DIR / "db.sqlite3"
KEYSTONES = BASE_DIR / "data" / "keystones_with_boundaries.json"
STATE_FILE = BASE_DIR / "data" / "nightlight_state.json"
OUT_FILE = BASE_DIR / "data" / "eval" / "nightlights_sites.json"
MINING_REF = BASE_DIR / "data" / "eval" / "mining_reference.json"
CT_SITES = BASE_DIR / "data" / "eval" / "crisistracker" / "mine_sites.json"

N_CONTROLS_PER_SITE = 5   # random hull points per mine site (the denominator)
CONTROL_SEED = 20260813   # fixed: controls must not move between runs
LIT_THRESHOLD = 1.0       # nW/(cm^2 sr); rural dark ground is ~0.2-0.5
PIPELINE_VERSION = "2026-08-13a"  # bump to re-queue every area


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(STATE_FILE)


def reference_sites():
    """[(site_id, lon, lat, source)] from both reference lists."""
    out = []
    d = json.load(open(MINING_REF))
    for i, s in enumerate(d["sites"]):
        out.append((f"mr_{i:04d}", s["lon"], s["lat"], s["source"]))
    ct = json.load(open(CT_SITES))
    for s in ct["sites"]:
        out.append((s["site_id"], s["lon"], s["lat"], "crisistracker"))
    return out


def areas_with_sites():
    """{area_id: {'geom': shapely, 'is_aoi': bool, 'sites': [(id,lon,lat,src)]}}
    for every park/AOI containing at least one reference site."""
    import sqlite3
    from shapely.geometry import shape, Point
    from shapely.prepared import prep
    areas = []
    for p in json.load(open(KEYSTONES)):
        if p.get("geometry"):
            areas.append((p["id"], shape(p["geometry"]), False))
    conn = sqlite3.connect(DB_PATH)
    for r in conn.execute("SELECT id, geometry FROM aois WHERE geometry IS NOT NULL"):
        areas.append((r[0], shape(json.loads(r[1])), True))
    conn.close()
    sites = reference_sites()
    out = {}
    for aid, geom, is_aoi in areas:
        pg = prep(geom)
        mine = [s for s in sites if pg.contains(Point(s[1], s[2]))]
        if mine:
            out[aid] = {"geom": geom, "is_aoi": is_aoi, "sites": mine}
    return out


def control_points(geom, n, seed):
    """n random points inside the area hull — rejection sampling, fixed seed.
    A site series without a random-ground series beside it is a number with
    no denominator (acled.md discipline)."""
    from shapely.geometry import Point
    from shapely.prepared import prep
    rng = np.random.default_rng(seed)
    pg = prep(geom)
    minx, miny, maxx, maxy = geom.bounds
    pts = []
    tries = 0
    while len(pts) < n and tries < n * 1000:
        x = rng.uniform(minx, maxx)
        y = rng.uniform(miny, maxy)
        tries += 1
        if pg.contains(Point(x, y)):
            pts.append((x, y))
    if len(pts) < n:
        raise RuntimeError(f"control sampling starved: {len(pts)}/{n}")
    return pts


def area_site_list(aid, info):
    """All points to extract for an area: mines + controls, grouped by tile.
    Returns {tile: [(point_id, lon, lat)]}, plus metadata dicts."""
    mines = {}
    for sid, lon, lat, src in info["sites"]:
        mines[sid] = {"lon": lon, "lat": lat, "source": src}
    n_ctl = len(mines) * N_CONTROLS_PER_SITE
    ctl = {}
    for i, (x, y) in enumerate(control_points(info["geom"], n_ctl, CONTROL_SEED)):
        ctl[f"ctl_{aid}_{i:04d}"] = {"lon": x, "lat": y}
    by_tile = {}
    for pid, p in {**mines, **ctl}.items():
        t = fnl.tile_for(p["lon"], p["lat"])
        by_tile.setdefault(t, []).append((pid, p["lon"], p["lat"]))
    return by_tile, mines, ctl


def run_area(aid, info, months=None):
    """Fetch/refresh extracts for one area. Returns (n_months_done, failed)."""
    by_tile, mines, ctl = area_site_list(aid, info)
    log(f"{aid}: {len(mines)} mine sites, {len(ctl)} controls, "
        f"tiles {sorted(by_tile)}")
    done_total, failed_total = 0, []
    for tile, pts in sorted(by_tile.items()):
        done, failed = fnl.fetch_months(tile, pts, months=months, log=log)
        done_total += len(done)
        failed_total += [f"{tile}:{m}" for m in failed]
    return done_total, failed_total


def series_stats(rows):
    """Summary of one point's monthly series.
    observed = months with a radiance; lit = observed months over threshold."""
    vals = [r[1] for r in rows if r[1] is not None]
    if not vals:
        return {"months_observed": 0, "months_unobserved": len(rows),
                "median_radiance_nw_cm2_sr": None, "max_radiance_nw_cm2_sr": None,
                "lit_months": 0}
    return {
        "months_observed": len(vals),
        "months_unobserved": len(rows) - len(vals),
        "median_radiance_nw_cm2_sr": round(float(np.median(vals)), 3),
        "max_radiance_nw_cm2_sr": round(float(np.max(vals)), 3),
        "lit_months": int(sum(v >= LIT_THRESHOLD for v in vals)),
    }


def evaluate(area_ids=None):
    """Build nightlights_sites.json from whatever extracts exist.

    Skill: is the median radiance at mine sites higher than at hull controls?
    Lift = mean(site medians) / mean(control medians); permutation p over the
    pooled labels. A null is printed WITH its power ceiling (how big a lift
    the control count could even resolve) — a null without its power is a
    shrug wearing a result's clothes (acled.md).
    """
    areas = areas_with_sites()
    if area_ids:
        areas = {k: v for k, v in areas.items() if k in area_ids}
    rng = np.random.default_rng(7)
    out_areas = {}
    for aid, info in sorted(areas.items()):
        by_tile, mines, ctl = area_site_list(aid, info)
        tile_of = {pid: t for t, pts in by_tile.items() for pid, _, _ in pts}
        site_rows, mine_meds, ctl_meds = {}, [], []
        n_unextracted = 0
        for pid in list(mines) + list(ctl):
            rows = fnl.site_series(tile_of[pid], pid)
            if not rows:
                n_unextracted += 1
                continue
            st = series_stats(rows)
            med = st["median_radiance_nw_cm2_sr"]
            if pid in mines:
                site_rows[pid] = {**mines[pid], **st,
                                  "series": [[m, v] for m, v, _ in rows]}
                if med is not None:
                    mine_meds.append(med)
            elif med is not None:
                ctl_meds.append(med)
        if not site_rows:
            out_areas[aid] = {"status": "unextracted",
                              "n_sites": len(mines)}
            continue
        skill = {"n_mine": len(mine_meds), "n_control": len(ctl_meds),
                 "lit_sites": sum(1 for r in site_rows.values()
                                  if r["lit_months"] > 0)}
        if mine_meds and ctl_meds:
            mm, cm = np.array(mine_meds), np.array(ctl_meds)
            base = float(cm.mean())
            lift = float(mm.mean()) / base if base > 0 else None
            pooled = np.concatenate([mm, cm])
            k, obs = len(mm), float(mm.mean())
            perm = [rng.permutation(pooled)[:k].mean() for _ in range(2000)]
            p = float((np.sum(np.array(perm) >= obs) + 1) / 2001)
            skill.update({
                "mean_mine_median_nw_cm2_sr": round(float(mm.mean()), 3),
                "mean_control_median_nw_cm2_sr": round(base, 3),
                "lift": round(lift, 3) if lift is not None else None,
                "permutation_p": round(p, 4),
                "verdict": ("lit" if lift and lift > 1 and p < 0.05
                            else "dark"),
                "power_note": (
                    f"{len(cm)} controls; a true lift smaller than the "
                    f"control spread (sd {float(cm.std()):.2f} "
                    f"nW/(cm^2 sr)) is not resolvable here"),
            })
        else:
            skill["verdict"] = "unmeasured"
            skill["power_note"] = "no observed months on one side; unmeasured"
        out_areas[aid] = {"status": "measured",
                          "unextracted_points": n_unextracted,
                          "skill": skill, "sites": site_rows}
    out = {
        "generated_by": "scripts/nightlight_sites.py",
        "pipeline_version": PIPELINE_VERSION,
        "generated": datetime.now(timezone.utc).isoformat(),
        "source": "NASA VNP46A3 Black Marble monthly composites, Collection 2",
        "accessed": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "citation": ("Román, M.O. et al. (2018). NASA's Black Marble "
                     "nighttime lights product suite. Remote Sensing of "
                     "Environment 210, 113-143. "
                     "https://doi.org/10.1016/j.rse.2018.03.017"),
        "terms": "NASA data — no restrictions; cite the product",
        "notice": ("Radiance medians over 3x3 pixel windows (~1.5 km) of "
                   "AllAngle_Composite_Snow_Free. Site coordinates and mine "
                   "labels are from the mining reference lists, not NASA. A "
                   "None radiance is an unobserved month (cloud), not "
                   "darkness. Lift compares reported mine sites to random "
                   "points in the same area hull."),
        "unit": "nW/(cm^2 sr)",
        "lit_threshold_nw_cm2_sr": LIT_THRESHOLD,
        "controls_per_site": N_CONTROLS_PER_SITE,
        "areas": out_areas,
    }
    OUT_FILE.write_text(json.dumps(out, indent=1, sort_keys=True))
    return out


def pending_areas(state):
    """Areas still owed work, stalest first. AOIs first on a tie — the
    plan's target sites are in XSA_Study_Area."""
    areas = areas_with_sites()
    pend = []
    for aid, info in areas.items():
        st = state.get(aid) or {}
        if st.get("pipeline") != PIPELINE_VERSION or st.get("failed_months"):
            pend.append((st.get("when", ""), not info["is_aoi"], aid))
    pend.sort()
    return [aid for _, _, aid in pend], areas


def current_month_targets():
    """The last few months (the newest composite lags ~2 months) for --append."""
    from datetime import date, timedelta
    t = date.today().replace(day=1)
    months = []
    for _ in range(4):
        months.append(f"{t.year}-{t.month:02d}")
        t = (t - timedelta(days=1)).replace(day=1)
    return sorted(months)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--area", help="one park/AOI id")
    ap.add_argument("--rotate", type=int, metavar="N",
                    help="process the N stalest pending areas (cron)")
    ap.add_argument("--append", action="store_true",
                    help="only fetch the last few months (monthly refresh)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--eval-only", action="store_true",
                    help="rebuild nightlights_sites.json from extracts")
    args = ap.parse_args()

    state = load_state()
    pend, areas = pending_areas(state)

    if args.list:
        for aid in areas:
            mark = "pending" if aid in pend else "done"
            print(f"{aid:28s} {len(areas[aid]['sites']):4d} sites  {mark}")
        return 0

    if args.eval_only:
        out = evaluate()
        for aid, a in out["areas"].items():
            if a.get("skill"):
                s = a["skill"]
                print(f"{aid}: lift={s.get('lift')} p={s.get('permutation_p')} "
                      f"verdict={s.get('verdict')}")
        return 0

    try:
        fnl.token()
    except fnl.MissingToken as ex:
        log(str(ex))
        notify_status("nightlights_failed", "Nightlights: no token", str(ex))
        return 1

    todo = [args.area] if args.area else pend[:args.rotate or 1]
    if not todo:
        log("queue empty — nothing pending")
        if args.append:
            # monthly refresh: append newest months to every DONE area
            todo = list(areas)
        else:
            return 0

    months = current_month_targets() if args.append else None
    any_failed = False
    for aid in todo:
        if aid not in areas:
            log(f"{aid}: no reference mine sites here — nothing to measure")
            continue
        try:
            n_done, failed = run_area(aid, areas[aid], months=months)
        except fnl.MissingToken as ex:
            notify_status("nightlights_failed", "Nightlights: no token", str(ex))
            return 1
        except Exception as ex:  # noqa: BLE001
            log(f"{aid}: FAILED: {ex}")
            notify_status("nightlights_failed", f"Nightlights failed: {aid}",
                          str(ex)[:400], park_id=aid)
            any_failed = True
            continue
        st = {"when": datetime.now(timezone.utc).isoformat(),
              "months_extracted": n_done, "failed_months": failed,
              "n_sites": len(areas[aid]["sites"])}
        if not failed:
            st["pipeline"] = PIPELINE_VERSION  # only a clean run counts (R1)
        else:
            any_failed = True
        state[aid] = st
        save_state(state)
        # Re-evaluate after each area so the JSON is never stale w.r.t. state
        out = evaluate()
        a = out["areas"].get(aid, {})
        s = a.get("skill", {})
        msg = (f"{aid}: {n_done} months, {st['n_sites']} sites"
               + (f", {len(failed)} months FAILED" if failed else "")
               + (f"; lift={s.get('lift')} p={s.get('permutation_p')} "
                  f"verdict={s.get('verdict')}" if s else ""))
        log(msg)
        notify_status(
            "nightlights_failed" if failed else "nightlights_success",
            f"Nightlights: {aid}", msg, park_id=aid)
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
