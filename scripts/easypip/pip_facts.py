#!/usr/bin/env python3
"""Derive every number the EASY PIP quotes, once, into data/eval/pip_facts.json.

WHY THIS EXISTS. The PIP consolidates four documents written over several
weeks (assessment+plan, road check, budget, and the XSA regional review behind
them all). Each restated the other's numbers in prose, and a boundary KML that
arrived on 2026-08-31 invalidated a whole reading of the park without every
restatement being found. The failure mode is root invariant 2 - a typed count
that describes a variable input - and it had already happened: the park was
"~14,500 km2" in one paragraph and 15,811 in another, and "~1,900 people"
survived into a legal argument three sections after the measurement that
replaced it.

So: the PIP report, the two-pager and the map all read THIS file. Nothing is
typed twice, and re-running after a boundary edit moves every document
together. A fact that cannot be measured is emitted as null with a note
saying why - never as a zero (root invariant 1).

    python3 scripts/easypip/pip_facts.py            # ~40 s
    python3 scripts/easypip/pip_facts.py --check    # recompute, exit 1 on drift

Sources, all local and all re-derivable:
  data/eval/zone_stats.json          the eleven proposed shapes, measured
  db.sqlite3                         settlements, clearing, fire index
  data/fire_groups_v5/XSA_*.json     v5 fire trajectories
  data/eval/xsa_mining/prediction*   the mining model and its measured skill
  data/eval/mining_reference.json    reported mine occurrences (coverage!)
  data/eval/acled/adm1_conflict.json per-state conflict scalars (ours, not ACLED's)
  scripts/easybudget/build_budget.py the live budget model
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pyproj
from pyproj import Geod
from shapely import contains_xy
from shapely.geometry import Point, shape
from shapely.ops import transform, unary_union

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "easybudget"))
from plan_zone_stats import RIM_KM, km2, read_kml  # noqa: E402

DB = ROOT / "db.sqlite3"
AOI = "XSA_Study_Area"
OUT = ROOT / "data/eval/pip_facts.json"
ZONES = ROOT / "data/eval/zone_stats.json"
KML_DIR = ROOT / "data/plan_zones"
GROUPS = ROOT / "data/fire_groups_v5" / f"{AOI}.json"
GEOD = Geod(ellps="WGS84")

EQA = pyproj.Transformer.from_crs(4326, "+proj=cea", always_xy=True).transform
INV = pyproj.Transformer.from_crs("+proj=cea", 4326, always_xy=True).transform

# The park is the subject of the PIP; its key in zone_stats is the Placemark
# name the authors wrote. Matched on substring so a renamed file still lands,
# and asserted present rather than defaulted (an absent park must not read as
# an empty park).
PARK_KEY = "Pongo-Wau-Numatinna"


def pick(d, frag, what):
    hits = [k for k in d if frag.lower() in k.lower()]
    if len(hits) != 1:
        sys.exit(f"{what}: expected exactly one key matching {frag!r}, got {hits}")
    return hits[0]


def load_zone_geoms():
    zones = {}
    for p in sorted(KML_DIR.glob("*.kml")):
        for nm, geom, kind, lab in read_kml(p):
            key = nm if nm not in zones else f"{nm} [{p.stem}]"
            zones[key] = dict(geom=geom, kind=kind, file=p.name, label_km2=lab)
    if not zones:
        sys.exit(f"no KML in {KML_DIR}")
    return zones


# ------------------------------------------------------------------ blocks
def shapes_block(zs, geoms):
    """The eleven shapes: how many, of what kind, how big, how nested.

    Every count here is len() of something read from disk. "Eleven shapes in
    six files" is exactly the sentence that gets hand-typed once and then
    survives a seventh file arriving.
    """
    Z = zs["zones"]
    polys = {n: g["geom"] for n, g in geoms.items() if g["kind"] == "polygon"}
    marks = {n: g["geom"] for n, g in geoms.items() if g["kind"] == "marker"}
    union = unary_union(list(polys.values()))
    union_km2 = km2(union)
    sum_km2 = sum(km2(g) for g in polys.values())

    rows = []
    for n, g in geoms.items():
        lab = g["label_km2"]
        meas = (Z.get(n) or {}).get("area_km2") or round(km2(g["geom"]), 0)
        rows.append(dict(
            name=n, file=g["file"], kind=g["kind"],
            measured_km2=meas, file_says_km2=lab,
            delta_pct=(round(100 * (meas - lab) / lab, 1) if lab else None),
        ))
    worst = max((r for r in rows if r["delta_pct"] is not None),
                key=lambda r: abs(r["delta_pct"]), default=None)
    return dict(
        n_files=len(list(KML_DIR.glob("*.kml"))),
        n_shapes=len(geoms),
        n_polygons=len(polys),
        n_markers=len(marks),
        marker_buffer_km=15.0,
        union_km2=round(union_km2),
        sum_of_areas_km2=round(sum_km2),
        overlap_km2=round(sum_km2 - union_km2),
        rows=sorted(rows, key=lambda r: -(r["measured_km2"] or 0)),
        worst_area_discrepancy=worst,
        note=("areas do not add: these are nested designations over mostly the "
              "same ground"),
    )


def union_people(geoms):
    """Who is inside the union of the proposed polygons, and on its rim.

    Measured on the UNION, not summed per zone - summing would count the
    Numatina grazing zone's people again under the wilderness block it
    overlaps by 91% of itself.
    """
    polys = [g["geom"] for g in geoms.values() if g["kind"] == "polygon"]
    u = unary_union(polys)
    um = transform(EQA, u)
    rim = transform(INV, um.buffer(RIM_KM * 1000).difference(um))

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        """SELECT lat, lon, population_est, persistence, classification, nearest_place
           FROM park_settlements WHERE park_id = ?""", (AOI,)).fetchall()
    con.close()
    lon = np.array([r[1] for r in rows])
    lat = np.array([r[0] for r in rows])
    mi = contains_xy(u, lon, lat)
    mr = contains_xy(rim, lon, lat)

    def summarise(sel):
        return dict(
            clusters=len(sel),
            population_est=int(sum(r[2] or 0 for r in sel)),
            by_persistence=dict(Counter(r[3] or "unknown" for r in sel).most_common()),
            by_classification=dict(Counter(r[4] or "unclassified" for r in sel).most_common()),
            largest=[dict(place=r[5], pop=r[2], lat=round(r[0], 4), lon=round(r[1], 4))
                     for r in sorted(sel, key=lambda r: -(r[2] or 0))[:5]],
        )

    ins = summarise([r for r, m in zip(rows, mi) if m])
    rms = summarise([r for r, m in zip(rows, mr) if m])
    ins["area_km2"] = round(km2(u))
    rms["rim_km"] = RIM_KM
    return dict(inside=ins, rim=rms)


def park_block(zs, geoms):
    Z = zs["zones"]
    key = pick(Z, PARK_KEY, "proposed park")
    rimkey = f"{key} \u2014 {RIM_KM:g} km rim"
    s, d = zs["settlements"][key], zs["deforestation"][key]
    c, f = zs["cropland"][key], zs["fire_detections"][key]
    t, m = zs["fire_trajectories"][key], zs["mining"][key]
    sr = zs["settlements_rim"].get(rimkey, {})
    mr = zs["mining_rim"].get(rimkey, {})

    near = zs["nearest_towns"].get(key, [])
    kilo = next((x for x in near if (x["pop"] or 0) >= 1000), None)
    biggest = max(near, key=lambda x: x["pop"] or 0) if near else None

    trans = t["by_type"].get("transhumance", 0)
    return dict(
        key=key,
        area_km2=Z[key]["area_km2"],
        file_says_km2=Z[key]["label_km2"],
        bounds=Z[key]["bounds"],
        clusters=s["clusters"],
        people=s["population_est"],
        settlement_types=s["by_classification"],
        new_since_2015=s["by_persistence"].get("recent", 0),
        only_settlement=(s["largest"][0] if s["largest"] else None),
        cropland_km2=c["km2_2019"],
        cropland_source=c.get("source_2019"),
        clearing_km2=d["km2_verified"],
        clearing_events=d["events_verified"],
        clearing_classes=d["by_classification"],
        clearing_last_year=(max(int(y) for y in d["by_year_km2"])
                            if d["by_year_km2"] else None),
        fire_detections_2024_2025=f["detections_2024_2025"],
        fire_rate=round(f["detections_per_1000km2_per_year"]),
        fire_rate_basis=f["rate_basis"],
        nov_feb_share=round(100 * f["nov_feb_share"]),
        # the season's own shape, so the map's calendar strip and the
        # documents' "Dec 15 - Feb 15" window read the same twelve numbers
        by_calendar_month=f["by_calendar_month_2024_2025"],
        fronts=t["fronts_touching"],
        fronts_transhumance_pct=round(100 * trans / max(t["fronts_touching"], 1)),
        fronts_start_inside=t["starts_inside"],
        fronts_die_inside=t["ends_inside"],
        interception_pct=round(100 * t["ends_inside"] / max(t["fronts_touching"], 1)),
        rim=dict(clusters=sr.get("clusters"), people=sr.get("population_est"),
                 recent=sr.get("by_persistence", {}).get("recent", 0),
                 largest=sr.get("largest", [])[:3], rim_km=RIM_KM),
        nearest_1000=kilo, biggest_near=biggest,
        mining_inside=dict(reported=m["reported_mine_sites"],
                           candidates=m["model_candidates"],
                           watchlist=m["watchlist_villages"],
                           top05_cells=m["top05_cells"]),
        mining_rim=dict(reported=mr.get("reported_mine_sites"),
                        candidates=mr.get("model_candidates"),
                        watchlist=mr.get("watchlist_villages"),
                        top05_cells=mr.get("top05_cells")),
    )


def gold_block(zs, geoms):
    """Where the gold exposure is, per zone, and what the model is worth.

    The headline the plan needs is a NEGATIVE about the park and a POSITIVE
    about the wilderness blocks - so both are derived, and the model's measured
    skill travels with them (root invariant 12: a grade drawn without its score
    beside it reads as a ranking).
    """
    pred = json.load(open(ROOT / "data/eval/xsa_mining/prediction.json"))
    sk = {s["top_frac"]: s for s in pred["composite_skill"]}
    anchor_src = dict(Counter(a["source"] for a in pred["anchors"]).most_common())
    per = {}
    for name, m in zs["mining"].items():
        rk = f"{name} \u2014 {RIM_KM:g} km rim"
        r = zs["mining_rim"].get(rk, {})
        per[name] = dict(
            inside=dict(reported=m["reported_mine_sites"],
                        candidates=m["model_candidates"],
                        watchlist=m["watchlist_villages"],
                        top05_cells=m["top05_cells"],
                        candidate_points=m["candidate_points"],
                        watchlist_names=m["watchlist_names"]),
            rim=dict(reported=r.get("reported_mine_sites"),
                     candidates=r.get("model_candidates"),
                     watchlist=r.get("watchlist_villages"),
                     top05_cells=r.get("top05_cells")),
        )
    hot = sorted(((v["inside"]["top05_cells"], n) for n, v in per.items()
                  if "baseline" not in n), reverse=True)

    ref = json.load(open(ROOT / "data/eval/mining_reference.json"))
    ssd = [e for e in ref["sites"] if e["iso3"] == "SSD"]
    polys = unary_union([g["geom"] for g in geoms.values() if g["kind"] == "polygon"])
    inside_any = sum(1 for e in ref["sites"] if polys.contains(Point(e["lon"], e["lat"])))
    raga = (25.68, 8.46)
    nearest_km, idx = min(
        (GEOD.inv(raga[0], raga[1], e["lon"], e["lat"])[2] / 1000, i)
        for i, e in enumerate(ref["sites"]))
    ne = ref["sites"][idx]
    return dict(
        per_zone=per,
        hottest=[dict(zone=n, top05_cells=c) for c, n in hot if c],
        skill_top05=sk.get(0.05), skill_top20=sk.get(0.2),
        # The number that decides how hard the plan may lean on this model.
        # Raw lift 3.25 (p=0.006) becomes 2.01 (p=0.057) once the anchor list's
        # own reach is corrected for - i.e. NOT significant. Root invariant 12:
        # a grade drawn without its score beside it reads as a ranking.
        verdict=("suggestive, not significant: after correcting for where the "
                 "anchor list can see, the top 5%% of ground holds %.2fx the "
                 "known workings of average ground (p=%.3f, and p<0.05 is the "
                 "usual bar). Treat the shading as WHERE TO LOOK FIRST, never "
                 "as evidence a pit is there."
                 % (sk[0.05]["lift_reach"], sk[0.05]["p_reach"])),
        n_anchors=pred["n_anchors"], n_clusters=pred["n_clusters"],
        anchor_sources=anchor_src,
        n_candidates=len(pred["candidates"]),
        n_watchlist=pred["abandoned_village_gold_watchlist"]["listed"],
        reference=dict(
            ssd_sites=len(ssd),
            ssd_sources=sorted({e["source"] for e in ssd}),
            ssd_with_commodity=sum(1 for e in ssd if e.get("commodities")),
            inside_proposed_shapes=inside_any,
            nearest_to_raga_km=round(nearest_km),
            nearest_to_raga_where=f"{ne['iso3']} {ne.get('adm1') or ''}".strip(),
            meaning=("an empty map here is the reach of the lists, not the "
                     "absence of pits"),
        ),
    )


def corridor_block(zs, geoms):
    """The two "ecological corridor" files are PINS. Measure what they claim."""
    Z = zs["zones"]
    marks = [n for n, z in Z.items() if z.get("kind") == "marker"]
    pts = []
    for n in marks:
        b = Z[n]["bounds"]
        pts.append(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2))
    apart = (round(GEOD.inv(pts[0][0], pts[0][1], pts[1][0], pts[1][1])[2] / 1000, 1)
             if len(pts) == 2 else None)
    conn = zs["fire_connectivity"]
    park = pick(Z, PARK_KEY, "park")
    snp = pick(Z, "Southern NP", "Southern NP")
    shared = None
    for k, v in conn["shared_fronts"].items():
        if park in k and snp in k:
            shared = v
            break
    a, b = geoms[park]["geom"], geoms[snp]["geom"]
    inter = a.intersection(b)
    return dict(
        pins=[dict(lon=round(p[0], 4), lat=round(p[1], 4)) for p in pts],
        pins_apart_km=apart,
        disc_radius_km=15.0,
        park_snp_shared_fronts=shared,
        park_snp_touch_km2=(round(km2(inter), 2) if not inter.is_empty else 0.0),
        ask="a pin cannot be gazetted - send a polygon",
    )


def fire_block(zs):
    """The fire system, at the scale the plan has to govern it."""
    groups = json.load(open(GROUPS))
    types = Counter(g["group_type"] for g in groups)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    det = con.execute(
        "SELECT COUNT(*) FROM aoi_fires WHERE aoi_id = ?", (AOI,)).fetchone()[0]
    con.close()
    base = zs["fire_detections"]["XSA study area (baseline)"]
    rates = {n: v["detections_per_1000km2_per_year"]
             for n, v in zs["fire_detections"].items() if "baseline" not in n}
    return dict(
        trajectories=len(groups),
        trajectory_window=[min(g["start_date"] for g in groups),
                           max(g["end_date"] for g in groups)],
        detections_indexed=det,
        detections_in_trajectories=sum(g["fire_count"] for g in groups),
        by_type=dict(types.most_common()),
        transhumance_pct=round(100 * types["transhumance"] / len(groups)),
        xsa_rate=round(base["detections_per_1000km2_per_year"]),
        zone_rate_min=round(min(rates.values())),
        zone_rate_max=round(max(rates.values())),
        rate_unit="detections per 1,000 km2 per year, 2024-2025 only",
        fleet_caption=("the VIIRS fleet triples on 2024-01-01 - a rate spanning "
                       "that line is not one series"),
    )


def budget_block():
    import build_budget as B
    rows, t = B.compute()
    a = t["a"]
    drv = t["drv"]
    # the two Y2 one-offs, derived from the line items rather than typed
    oneoff = sum(r["tot"][1] for r in rows
                 if "survey" in r["item"].lower() or "borehole" in r["item"].lower()
                 or "water point" in r["item"].lower())
    return dict(
        y1=round(t["total"][0]), y2=round(t["total"][1]), y3=round(t["total"][2]),
        three_year=round(sum(t["total"])),
        first_6_months=round(t["h1"]["total"]),
        direct=[round(x) for x in t["direct"]],
        load_factor=round(t["load"], 4),
        team_loaded_3yr=round(sum(drv["TEAM"]["loaded"])),
        backbone_loaded_3yr=round(sum(drv["FP"]["loaded"])),
        hq_loaded_3yr=round(sum(drv["HQ"]["loaded"])),
        rates=dict(freight=a["FREIGHT_PCT"], support=a["SUPPORT_PCT"],
                   contingency=a["CONTING_PCT"], bank=a["BANK_PCT"]),
        y2_oneoffs=round(oneoff),
    )


def xsa_block():
    """The regional baseline every zonal number is meaningless without.

    Re-derived from the database rather than copied from the XSA review, so a
    drift between the two is a finding rather than a rounding.
    """
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    def q(s, *a):
        return con.execute(s, a).fetchone()

    clusters, pop = q(
        "SELECT COUNT(*), SUM(population_est) FROM park_settlements WHERE park_id=?", AOI)
    built = q("SELECT ROUND(SUM(area_m2)/1e6,1) FROM park_settlements WHERE park_id=?", AOI)[0]
    b2000, b2015 = q(
        """SELECT ROUND(SUM(surface_e2000_m2)/1e6,1), ROUND(SUM(surface_e2015_m2)/1e6,1)
           FROM park_settlements WHERE park_id=?""", AOI)
    ev_all, km_all = q(
        "SELECT COUNT(*), ROUND(SUM(area_km2),1) FROM deforestation_events WHERE park_id=?", AOI)
    ev_ok, km_ok = q(
        """SELECT COUNT(*), ROUND(SUM(area_km2),1) FROM deforestation_events
           WHERE park_id=? AND needs_review=0""", AOI)
    km_q = q("""SELECT ROUND(SUM(area_km2),1) FROM deforestation_events
                WHERE park_id=? AND needs_review=1""", AOI)[0]
    pers = dict(con.execute(
        """SELECT COALESCE(persistence,'unknown'), COUNT(*) FROM park_settlements
           WHERE park_id=? GROUP BY 1""", (AOI,)).fetchall())
    con.close()
    area = round(km2(shape(json.load(open(ROOT / "data/study_areas" / f"{AOI}.geojson")))))
    return dict(
        area_km2=area,
        clusters=clusters, people=int(pop or 0),
        built_km2=built, built_2000_km2=b2000, built_2015_km2=b2015,
        by_persistence=pers,
        clearing_events_all=ev_all, clearing_km2_all=km_all,
        clearing_events_verified=ev_ok, clearing_km2_verified=km_ok,
        clearing_km2_needs_review=km_q,
        note=("population is a GHSL satellite estimate and a LOWER BOUND, never "
              "a count; 'verified' clearing excludes the events the pipeline "
              "itself flags"),
    )


def sites_block(zs):
    """The plan's own sites, with what is actually within 15 km of each."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        """SELECT lat, lon, population_est, nearest_place FROM park_settlements
           WHERE park_id=?""", (AOI,)).fetchall()
    con.close()
    out = {}
    for key, v in zs["plan_sites"].items():
        d = [(GEOD.inv(v["lon"], v["lat"], r[1], r[0])[2] / 1000, r) for r in rows]
        near = [r for km, r in d if km <= 15.0]
        out[key] = dict(
            lat=v["lat"], lon=v["lon"],
            clusters=len(near),
            people=int(sum(r[2] or 0 for r in near)),
            nearest_settlement_km=round(min(km for km, _ in d), 1),
            inside_zones=v["inside"],
            km_to_nearest_zones=v["km_to_nearest_zones"],
        )
    return out


def conflict_block():
    """Per-state scalars WE derived from an ACLED aggregate. Never ACLED's.

    docs/agents/acled.md: the weekly rows were summed and discarded; every
    inference is ours and must not be attributed. Emitted with that sentence
    attached, so a document cannot quote the number without it.

    The three statements the plan makes - WBeG is quiet by count but 2nd by
    peak exposure, Western Equatoria peaked in 2025, and the gold-violence
    link cannot be tested here - are each DERIVED (a rank is computed, not
    typed), because a rank is exactly the kind of claim that silently goes
    stale when the aggregate is refreshed.
    """
    p = ROOT / "data/eval/acled/adm1_conflict.json"
    if not p.exists():
        return dict(note="unmeasured: data/eval/acled/adm1_conflict.json missing")
    d = json.load(open(p))
    ss = (d.get("countries") or {}).get("South Sudan")
    if not ss or not ss.get("adm1"):
        return dict(note="unmeasured: no South Sudan rows in the conflict aggregate")
    a = ss["adm1"]
    by_ev = sorted(a, key=lambda n: a[n]["events"])
    by_exp = sorted(a, key=lambda n: -a[n]["peak_weekly_population_exposure"])

    def state(name):
        v = a.get(name)
        if not v:
            return None
        yrs = v.get("events_by_year") or {}
        et = v.get("events_by_event_type") or {}
        tot = sum(et.values()) or 1
        peak_year = max(yrs.items(), key=lambda kv: kv[1]) if yrs else (None, None)
        return dict(
            name=name, events=v["events"], fatalities=v["fatalities"],
            peak_weekly_population_exposure=round(v["peak_weekly_population_exposure"]),
            rank_by_events=1 + by_ev.index(name),
            rank_by_peak_exposure=1 + by_exp.index(name),
            record_year=peak_year[0], record_year_events=peak_year[1],
            vac_share_pct=round(100 * et.get("Violence against civilians", 0) / tot),
            events_by_year=yrs,
        )

    return dict(
        n_states=len(a),
        window=[d.get("week_from"), d.get("week_to")],
        wbeg=state("Western Bahr el Ghazal"),
        western_equatoria=state("Western Equatoria"),
        quietest=by_ev[0],
        attribution=("our own per-state totals derived from an ACLED aggregate "
                     "(acleddata.com); the weekly rows were summed and discarded. "
                     "These are not ACLED event data and every inference is ours."),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--check", action="store_true",
                    help="recompute and exit 1 if it differs from the file on disk")
    a = ap.parse_args()

    if not ZONES.exists():
        sys.exit(f"{ZONES} missing - run scripts/plan_zone_stats.py first")
    zs = json.load(open(ZONES))
    geoms = load_zone_geoms()

    facts = dict(
        generated_by="scripts/easypip/pip_facts.py",
        rim_km=RIM_KM,
        xsa=xsa_block(),
        shapes=shapes_block(zs, geoms),
        union=union_people(geoms),
        park=park_block(zs, geoms),
        corridor=corridor_block(zs, geoms),
        fire=fire_block(zs),
        gold=gold_block(zs, geoms),
        sites=sites_block(zs),
        budget=budget_block(),
        conflict=conflict_block(),
    )

    txt = json.dumps(facts, indent=1, sort_keys=True, ensure_ascii=False)
    if a.check:
        old = Path(a.out).read_text() if Path(a.out).exists() else ""
        if old.strip() != txt.strip():
            print("pip_facts.json is STALE - re-run scripts/easypip/pip_facts.py")
            sys.exit(1)
        print("pip_facts.json is current")
        return
    Path(a.out).write_text(txt + "\n")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
