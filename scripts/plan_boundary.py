#!/usr/bin/env python3
"""Metes-and-bounds description of every solved zone — the legal/geographic style a drafter or a
villager can follow: start at a corner, walk the outline clockwise, one leg per feature.

    python3 -W ignore scripts/plan_boundary.py            # all zones in data/plan_zones/solver/zones.geojson
    python3 -W ignore scripts/plan_boundary.py E1 T2     # only the zones these team codes serve
    python3 -W ignore scripts/plan_boundary.py --narrate  # + muse-glimmer 'in words' (36 workers; names/numbers only from the legs)

Writes data/plan_zones/solver/boundaries.json (machine: legs with coords) and BOUNDARIES.txt (human).
facts.json (easyplan.py) copies each team's `legal` text and `legs` so a question like "describe E1's
boundary" is answered from ONE file.

Method (no LLM, no typed numbers): the zone outline is sampled every STEP km; each sample takes the
nearest *walkable* linear feature within SNAP km (river / khor / ridge / swamp edge / road / border /
1930s district line — the same `feats` the mesh was cut on, from state.pkl). Consecutive samples on
the same feature form a LEG; gaps shorter than MIN_LEG km are absorbed into their neighbour. A leg on
no feature is "open bush" and names the point landmarks (1930s village sites, wells, pools, lone
hills; `G.beacons`) within BEACON km of it, in walking order. A geological contact is reported as its
own kind ("geological contact — not visible, beacon it"), never as a named feature. Each leg gives
its bearing (8-point), length, start and end coordinates (dd.dddd°), and the junction it ends at
("junction of Nahr al Jur and Busseri River"). The whole is also rendered as one legal paragraph.
"""
import json, math, pickle, re, sys, time, __main__
from collections import Counter
from pathlib import Path

import numpy as np
from shapely.geometry import shape, Point, LineString
from shapely.ops import transform, nearest_points
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import plan_conservancy_units as P                         # FWD/INV projections, INVISIBLE_KINDS, Grid (for the pickle)
__main__.Grid = P.Grid
SOLVER = ROOT / "data/plan_zones/solver"

STEP, SNAP, MIN_LEG, BEACON = 1.0, 3.5, 5.0, 3.0            # km; SNAP > the 2 km raster half-diagonal the outline was vectorised from
DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def bearing(a, b):
    """8-point compass from a to b (lon/lat)."""
    dx = (b[0] - a[0]) * math.cos(math.radians((a[1] + b[1]) / 2)); dy = b[1] - a[1]
    ang = math.degrees(math.atan2(dx, dy)) % 360
    return DIRS[int(((ang + 22.5) % 360) // 45)]


def fmt_pt(p):
    return f"{abs(p[1]):.4f}°{'N' if p[1] >= 0 else 'S'} {abs(p[0]):.4f}°{'E' if p[0] >= 0 else 'W'}"


def canon(name):
    """One name for one feature. 'R. Busseri' and 'Busseri River' are the same water; a '(1930s sheet)' tag is a
    provenance note, not a different river. Returns (display, key)."""
    n = name.replace(" (1930s sheet)", "").strip()
    n = n.replace("seasonal water / toich edge (JRC surface water)", "the toich edge (seasonal water)")
    if n.startswith("unnamed river"): return "an unnamed river", "unnamed"
    k = n.lower()
    k = re.sub(r"^(r\.|river|khor|k\.|nahr|bahr|w\.|wadi|j\.|jebel)\s+", "", k); k = re.sub(r"\s+(river|ridge|road)$", "", k)
    k = re.sub(r"[^a-z0-9]+", "", k)
    return n, k or n

def clean(name): return canon(name)[0]

def compass(a, b):
    """Which side of the zone centroid c a leg mid-point sits on."""
    return bearing(a, b)

def rel_side(c, p):
    return bearing((c.x, c.y), p)

SOURCE = {"river": "HydroRIVERS v1.0 (WWF), named from the 1930s Sudan Survey sheet where the net has no name",
          "hist_water": "1930s Sudan Survey 1:250,000 sheet, traced linework (khor/river)",
          "ridge": "hilltop marks (peak / trig point) on the 1930s Sudan Survey sheets, chained",
          "swamp": "JRC Global Surface Water (seasonal) / HydroLAKES edge", "road": "OpenStreetMap (HeiGIT extract)",
          "border": "GADM 4.1 country boundary", "hist_boundary": "1930s Sudan Survey district / province line",
          "open bush": "no linear feature — landmarks are 1930s sheet village/water symbols", "geological contact": "published geological map (Sudan 2004 / CAR 1964) — not visible on the ground"}
GADM = ROOT / "data/gadm_geom"
COD = ROOT / "data/admin_cod"          # OCHA COD-AB (HDX) — the government-endorsed admin set, with p-codes

def admin_units():
    """Jurisdictions a registration notice must name. South Sudan: OCHA COD-AB v03 (2022-12-19) admin-3 payams, carrying
    county (admin-2) and state (admin-1) with p-codes — data/admin_cod/ssd_admin3.geojson. Other countries: GADM 4.1 level-2
    (prefecture/sub-prefecture), no payam level — data/gadm_geom/gadm41_<ISO>_2.json. Rows: (geom, iso, state, county, payam, pcode, source)."""
    out = []
    ssd = COD / "ssd_admin3.geojson"
    if ssd.exists():
        for ft in json.load(open(ssd))["features"]:
            pr = ft["properties"]
            out.append((shape(ft["geometry"]).buffer(0), "SSD", pr["adm1_name"], pr["adm2_name"], pr["adm3_name"], pr["adm3_pcode"], f"OCHA COD-AB {pr.get('version','')} ({pr.get('valid_on','')})", pr["adm1_pcode"], pr["adm2_pcode"]))
    for f in sorted(GADM.glob("gadm41_*_2.json")):
        if "SSD" in f.name and ssd.exists(): continue
        for ft in json.load(open(f))["features"]:
            pr = ft["properties"]; sp = lambda n: re.sub(r"(?<=[a-z])(?=[A-Z])", " ", n or "")
            out.append((shape(ft["geometry"]).buffer(0), pr["GID_0"], sp(pr["NAME_1"]), sp(pr["NAME_2"]), None, pr["GID_2"], "GADM 4.1", pr["GID_1"], pr["GID_2"]))
    return out

def jurisdiction(poly, admin, atree):
    """Share of the zone's area by country / state / county / payam, largest first (≥1 % at payam level; counties
    aggregate everything, so a county total can exceed the sum of its listed payams)."""
    rows = []; cty = {}
    A = transform(P.FWD, poly).area
    for j in atree.query(poly):
        g, iso, st, co, pa, pc, src, st_pc, co_pc = admin[j]
        if not g.intersects(poly): continue
        a = transform(P.FWD, g.intersection(poly)).area / A * 100
        if a < 0.05: continue
        k = (iso, st, co); c = cty.setdefault(k, dict(country=iso, state=st, state_pcode=st_pc, county=co, county_pcode=co_pc, pct=0.0, payams=[], source=src))
        c["pct"] += a
        if pa and a >= 1: c["payams"].append(dict(payam=pa, pcode=pc, pct=round(a)))
    # two sources (COD-AB for SSD, GADM elsewhere) disagree about the border by a few km, so a border zone can sum past 100 %:
    # rescale to the zone and record the overlap, rather than print 104 %
    tot = sum(c["pct"] for c in cty.values()); f = 100 / tot if tot > 100 else 1
    for c in cty.values():
        c["pct"] = round(c["pct"] * f); c["payams"].sort(key=lambda r: -r["pct"])
        for q in c["payams"]: q["pct"] = max(1, round(q["pct"] * f))
        if tot > 100: c["source_overlap_pct"] = round(tot - 100)
    return sorted([c for c in cty.values() if c["pct"] >= 1], key=lambda r: -r["pct"])

def sheets_for(hcon, pts):
    """1930s sheet ids covering these points (for the schedule's source line)."""
    S = set()
    for lon, lat in pts:
        for cell, title, year in hcon.execute("SELECT cell, title, year FROM sheets WHERE minlon<=? AND maxlon>=? AND minlat<=? AND maxlat>=?", (lon, lon, lat, lat)):
            S.add(f"{cell} ({year})")
    return sorted(S)

def load_state():
    return pickle.load(open(P.OUT / "state.pkl", "rb"))


SIMPLIFY_DEG = 0.012   # ~1.3 km: turns the 2 km raster staircase into straight legs so lengths are walking lengths, not stair lengths

def clockwise_from_north(poly):
    """Exterior ring, clockwise, starting at the northernmost vertex (a corner a drafter can name)."""
    ring = LineString(poly.simplify(SIMPLIFY_DEG, preserve_topology=True).exterior.coords)
    coords = list(ring.coords)[:-1]
    if LineString(coords + [coords[0]]).is_ring and poly.exterior.is_ccw: coords = coords[::-1]
    i = max(range(len(coords)), key=lambda k: coords[k][1])
    coords = coords[i:] + coords[:i]
    return LineString(coords + [coords[0]])


def samples(ring_ll):
    m = transform(P.FWD, ring_ll); tot = m.length / 1000; n = max(8, int(round(tot / STEP)))
    pts = [m.interpolate(k / n, normalized=True) for k in range(n + 1)]
    return [transform(P.INV, p) for p in pts], tot / n


def nearest_feature(pt, feats, tree, kinds_ok):
    best, bd = None, SNAP
    for j in tree.query(pt.buffer(SNAP / 111 * 1.2)):
        if j not in kinds_ok: continue
        g = feats[j][0]
        if g.is_empty: continue
        q = nearest_points(g, pt)[0]; d = P.dkm((pt.x, pt.y), (q.x, q.y))
        if d < bd: best, bd = int(j), d
    return best


def legs_of(poly, feats, tree, beacons):
    ring = clockwise_from_north(poly)
    pts, step = samples(ring)
    walk = [k for k in range(len(feats)) if feats[k][2] not in P.INVISIBLE_KINDS]
    geo = [k for k in range(len(feats)) if feats[k][2] == "geology"]
    walk_ok, geo_ok = set(walk), set(geo)
    lab = []
    for p in pts:
        j = nearest_feature(p, feats, tree, walk_ok)
        if j is None:
            g = nearest_feature(p, feats, tree, geo_ok)
            j = ("geo", int(g)) if g is not None else None
        lab.append(j)
    keyof = lambda l: None if l is None else ("geo" if isinstance(l, tuple) else canon(feats[l][1])[1])
    # run-length by canonical KEY (hydro + sheet copy of one river = one leg)
    runs = []
    for i, l in enumerate(lab):
        k = keyof(l)
        if runs and runs[-1][0] == k: runs[-1][2] = i
        else: runs.append([k, i, i, l])
    def absorb(runs, min_km):
        """(1) A–B–A with B short → A.  (2) any run < min_km → longer neighbour.  Repeat to fixpoint."""
        changed = True
        while changed and len(runs) > 1:
            changed = False
            for i in range(1, len(runs) - 1):
                if runs[i - 1][0] == runs[i + 1][0] and (runs[i][2] - runs[i][1] + 1) * step < 2 * min_km and runs[i - 1][0] is not None:
                    runs[i - 1][2] = runs[i + 1][2]; del runs[i:i + 2]; changed = True; break
            if changed: continue
            for i, r in enumerate(runs):
                if (r[2] - r[1] + 1) * step < min_km:
                    cands = [runs[j] for j in (i - 1, i + 1) if 0 <= j < len(runs)]
                    nb = max(cands, key=lambda q: (q[0] is not None, q[2] - q[1]))     # prefer a NAMED neighbour, then the longer
                    nb[1], nb[2] = min(nb[1], r[1]), max(nb[2], r[2]); runs.pop(i); changed = True; break
            if not changed:
                for i in range(len(runs) - 1):
                    if runs[i][0] == runs[i + 1][0]: runs[i][2] = runs[i + 1][2]; runs.pop(i + 1); changed = True; break
        return runs
    runs = absorb(runs, MIN_LEG)
    if len(runs) > 1 and runs[0][0] == runs[-1][0]:            # ring: first and last legs are one river
        runs[0][1] = runs[-1][1] - len(pts); runs.pop()
    runs[-1][2] = len(pts) - 2 if runs[-1][1] >= 0 else runs[-1][2]   # the last leg closes exactly on the point of commencement
    c = poly.centroid
    legs = []
    for k, a, b, l in runs:
        n = len(pts) - 1
        pa, pb = pts[a % n], pts[(b + 1) % n]; km = (b - a + 1) * step
        if k is None: kind, name = "open bush", None
        elif k == "geo": kind, name = "geological contact", None
        else: kind, name = feats[l][2], clean(feats[l][1])
        mid = pts[((a + b) // 2) % n]
        leg = dict(kind=kind, name=name, km=round(km), bearing=bearing((pa.x, pa.y), (pb.x, pb.y)), side=rel_side(c, (mid.x, mid.y)),
                   start=[round(pa.x, 4), round(pa.y, 4)], end=[round(pb.x, 4), round(pb.y, 4)])
        if kind in ("open bush", "geological contact") and beacons:
            best = {}
            for i in range(a, b + 1):
                p = pts[i % n]
                for j in beacons["tree"].query(p.buffer(BEACON / 111)):
                    d = P.dkm((p.x, p.y), beacons["pts"][j])
                    if d <= BEACON and (j not in best or d < best[j][0]): best[j] = (d, i, bearing((p.x, p.y), beacons["pts"][j]))
            seen, marks = set(), []
            for j, (d, i, sd) in sorted(best.items(), key=lambda t: t[1][1]):     # walking order
                nm = beacons["names"][j]
                if nm in seen or nm.startswith("unnamed") or nm.startswith("landmark"): continue
                seen.add(nm); marks.append(dict(name=nm, km_off=round(d, 1), side=sd, at=[round(beacons["pts"][j][0], 4), round(beacons["pts"][j][1], 4)]))
            leg["landmarks"] = marks[:4]
        legs.append(leg)
    return legs, round(sum(l["km"] for l in legs))


KIND_WORD = {"river": "river", "hist_water": "khor (1930s sheet)", "ridge": "hill chain", "swamp": "swamp edge", "road": "road", "border": "international border", "hist_boundary": "1930s district line", "open bush": "open bush", "geological contact": "geological contact (invisible — beacons)"}

def summarise(legs, perimeter):
    """Sides: for each of N/E/S/W what bounds it (merging 8 → 4 sides), longest first, with km. Ledger: named / bush / geology km.
    Corners: the junctions where the boundary changes feature, with coordinates, only where both legs are ≥ CORNER_KM."""
    four = {"N": "north", "NE": "north-east", "E": "east", "SE": "south-east", "S": "south", "SW": "south-west", "W": "west", "NW": "north-west"}
    by = {}
    for l in legs:
        nm = l["name"] or l["kind"]
        d = by.setdefault(nm, dict(name=nm, kind=l["kind"], km=0, sides=Counter()))
        d["km"] += l["km"]; d["sides"][l["side"]] += l["km"]
    feats = sorted(by.values(), key=lambda d: -d["km"])
    for d in feats: d["sides"] = [four[s] for s, _ in d["sides"].most_common(2)]
    named = sum(d["km"] for d in feats if d["kind"] not in ("open bush", "geological contact"))
    bush = sum(d["km"] for d in feats if d["kind"] == "open bush"); geo = sum(d["km"] for d in feats if d["kind"] == "geological contact")
    corners = []
    for i, l in enumerate(legs):
        nxt = legs[(i + 1) % len(legs)]
        if l["km"] >= CORNER_KM and nxt["km"] >= CORNER_KM and l["name"] and nxt["name"]:
            corners.append(dict(at=l["end"], text=f"{l['name']} meets {nxt['name']}"))
    return dict(features=[dict(name=d["name"], kind=d["kind"], km=d["km"], sides=d["sides"]) for d in feats],
                named_km=named, bush_km=bush, geology_km=geo, corners=corners[:8])

CORNER_KM = 10

def summary_text(zone_name, S, perimeter):
    main = [d for d in S["features"] if d["kind"] not in ("open bush", "geological contact") and d["km"] >= 0.05 * perimeter][:6]
    parts = [f"{d['name']}, {d['km']} km on the {'/'.join(d['sides'])}" for d in main]
    t = f"{perimeter} km round. Bounded mainly by " + "; ".join(parts) + "."
    t += f" {S['named_km']} km ({round(100*S['named_km']/max(perimeter,1))}%) follows a river, khor, hill chain, road, border or swamp edge"
    if S["bush_km"] or S["geology_km"]:
        t += f"; {S['bush_km'] + S['geology_km']} km crosses open ground" + (f" (of which {S['geology_km']} km on a geological contact)" if S["geology_km"] else "") + " and must be beaconed with the community"
    t += "."
    if S["corners"]: t += " Key corners: " + "; ".join(f"{c['text']} at {fmt_pt(c['at'])}" for c in S["corners"][:6]) + "."
    return t


def legal_text(zone_name, legs, perimeter):
    def where(l):
        if l["kind"] == "open bush": return "across open bush"
        if l["kind"] == "geological contact": return "along a geological contact (not visible on the ground — to be beaconed)"
        return f"along {l['name']}" + ("" if l["kind"] in ("river", "hist_water", "road", "border", "swamp") else f" ({l['kind'].replace('hist_boundary', '1930s district line')})")
    S = [f"Commencing at the northernmost point ({fmt_pt(legs[0]['start'])})"]
    for i, l in enumerate(legs):
        nxt = legs[(i + 1) % len(legs)]
        end = fmt_pt(l["end"])
        if i == len(legs) - 1: junction = end
        elif nxt["name"] and l["name"]: junction = f"its junction with {nxt['name']} ({end})"
        elif nxt["name"]: junction = f"{nxt['name']} ({end})"
        else: junction = end
        marks = ""
        if l.get("landmarks"): marks = ", passing " + ", ".join(f"{m['name']} {m['km_off']} km to the {m['side']}" for m in l["landmarks"][:3])
        S.append(f"thence {l['bearing']} {where(l)} for {l['km']} km{marks} to {junction}")
    S[-1] += ", the point of commencement."
    return "; ".join(S)


NARR_SYS = """You receive a summary and a metes-and-bounds description of a proposed community conservancy or corridor boundary in South Sudan / CAR. Write what a county official would put in a notice and a village chief would repeat: at most FOUR plain sentences, clockwise from the north. Name only the 4-8 features that carry most of the line (skip legs under ~8 km unless they are the only thing on that side), give a rounded distance for each, name the corner where one feature meets the next, and say plainly which stretches cross open ground and what landmark marks them. Use ONLY names, distances and landmarks given; never invent or add a name; do not repeat a name that was already said for the same stretch. Return JSON: {"in_words": "..."}"""

def narrate(out, workers):
    from concurrent.futures import ThreadPoolExecutor
    import urllib.error
    def one(k):
        for attempt in range(6):
            try: r = P.llm_json(NARR_SYS, out[k]["summary"] + "\n\n" + out[k]["legal"], max_tokens=4000); break
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 5: raise
                time.sleep(5 * 2 ** attempt)
        out[k]["in_words"] = r.get("in_words") or f"(no narration: {r.get('error')})"
    with ThreadPoolExecutor(max_workers=workers) as ex: list(ex.map(one, list(out)))

def schedule_text(name, p, rec, juris, sheets):
    """The formal schedule a gazette notice or a s.14 application carries: identity, jurisdiction, extent, the metes-and-bounds,
    sources, and what is NOT yet done (walked, beaconed, agreed)."""
    def jt(r):
        pay = (" — payams " + ", ".join(f"{q['payam']} {q['pct']}%" for q in r["payams"])) if r["payams"] else ""
        return f"{r['county']} County, {r['state']} State ({r['country']}) {r['pct']}%{pay}" if r["country"] == "SSD" else f"{r['county']}, {r['state']} ({r['country']}) {r['pct']}%"
    j = ("; ".join(jt(r) for r in juris) or "no admin unit resolved") + ". Source: " + ", ".join(sorted({r["source"] for r in juris}))
    bbox = rec["bbox"]
    L = [f"SCHEDULE — {name}",
         f"1. Extent: {p['area_ha']:,} ha ({p['area_km2']:,.0f} km²); perimeter {rec['perimeter_km']} km; centroid {fmt_pt(rec['centroid'])}; bounding box {fmt_pt([bbox[0], bbox[1]])} to {fmt_pt([bbox[2], bbox[3]])}. Datum WGS 84 (EPSG:4326); areas in an equal-area projection.",
         f"2. Jurisdiction (share of area): {j}.",
         f"3. Overlap with existing or proposed designations: {', '.join(json.loads(p['overlaps'])) if json.loads(p['overlaps']) else 'none'}. Distance to the nearest gazetted or proposed park boundary: {p['km_to_park']} km.",
         f"4. Boundary: {rec['summary']}",
         f"5. Metes and bounds: {rec['legal']}",
         f"6. Sources of the boundary lines: " + "; ".join(f"{k} — {SOURCE[k]}" for k in sorted({l['kind'] for l in rec['legs']})) + (f". 1930s sheets consulted: {', '.join(sheets)}" if sheets else "") + ".",
         f"7. Status: machine-drawn from the sources above; no part of this line has been walked, beaconed or agreed with the resident communities or the county. {rec['bush_km'] + rec['geology_km']} km ({round(100*(rec['bush_km']+rec['geology_km'])/max(rec['perimeter_km'],1))}%) lies on no visible feature and needs beacons. Coordinates are read from a 2 km planning grid (±1 km) and are for identification, not survey.",
         f"8. Population inside (satellite lower bound, GHSL): {p['population_est']:,} in {p['clusters']} clusters; cropland {p['cropland_2019_pct']}% (2019); {p['new_since_2015']} settlements founded since 2015."]
    return "\n".join(L)


def main(codes, do_narrate=False, workers=36):
    st = load_state()
    admin = admin_units(); atree = STRtree([a[0] for a in admin]); import sqlite3; hcon = sqlite3.connect(str(P.HDB)); G, feats = st["G"], st["feats"]
    tree = STRtree([f[0] for f in feats]); beacons = getattr(G, "beacons", None)
    Z = json.load(open(SOLVER / "zones.geojson"))
    deploy = json.load(open(SOLVER / "deploy.json")) if (SOLVER / "deploy.json").exists() else {"teams": []}
    team_of = {}
    for t in deploy["teams"]:
        for u in t["zone_uids"]: team_of.setdefault(u, []).append(t["id"])
    want = None
    if codes: want = {u for t in deploy["teams"] if t["id"] in codes for u in t["zone_uids"]}
    out, L = {}, ["BOUNDARIES — metes-and-bounds of every solved zone, clockwise from the northernmost point.",
                 f"Samples every {STEP:g} km snap to the nearest walkable feature within {SNAP} km; legs < {MIN_LEG:g} km are absorbed; landmarks within {BEACON:g} km of open-bush legs. Coordinates WGS84.", ""]
    for f in Z["features"]:
        p = f["properties"]; uid = p["uid"]
        if want is not None and uid not in want: continue
        poly = shape(f["geometry"])
        if poly.geom_type != "Polygon": poly = max(poly.geoms, key=lambda g: g.area)
        legs, per = legs_of(poly, feats, tree, beacons)
        teams = team_of.get(uid, [])
        name = f"Zone {uid} ({p['solver_class']}" + (f", {'/'.join(teams)}" if teams else "") + ")"
        S = summarise(legs, per)
        juris = jurisdiction(poly, admin, atree); c = poly.centroid
        rec = dict(uid=uid, cls=p["solver_class"], teams=teams, area_ha=p["area_ha"], perimeter_km=per, named_pct=round(100 * S["named_km"] / max(per, 1)),
                   centroid=[round(c.x, 4), round(c.y, 4)], bbox=[round(v, 4) for v in poly.bounds], jurisdiction=juris,
                   summary=summary_text(name, S, per), features=S["features"], corners=S["corners"], bush_km=S["bush_km"], geology_km=S["geology_km"],
                   legs=legs, legal=legal_text(name, legs, per))
        rec["sheets_1930s"] = sheets_for(hcon, [l["start"] for l in legs if l["kind"] in ("hist_water", "hist_boundary", "ridge", "open bush")])
        rec["schedule"] = schedule_text(name, p, rec, juris, rec["sheets_1930s"])
        out[str(uid)] = rec
        L.append(f"#{uid} {p['solver_class']} {'/'.join(teams)}  {p['area_ha']:,} ha  {per} km, {rec['named_pct']}% on named features, {len(legs)} legs")
        L.append("  " + rec["schedule"].replace("\n", "\n  "))
        for l in legs:
            lm = ("  · " + "; ".join(f"{m['name']} {m['km_off']} km {m['side']}" for m in l["landmarks"])) if l.get("landmarks") else ""
            L.append(f"    {l['bearing']:>2} {l['km']:>3} km  {l['kind']:<18} {l['name'] or '—':<45} {fmt_pt(l['start'])} → {fmt_pt(l['end'])}{lm}")
        L.append("")
    # GeoJSON for a GIS reviewer: one LineString per leg (kind/name/km/source), one Point per corner and landmark
    gj = []
    for uid, r in out.items():
        for i, l in enumerate(r["legs"]):
            gj.append({"type": "Feature", "properties": dict(uid=int(uid), teams="/".join(r["teams"]), leg=i + 1, kind=l["kind"], name=l["name"], km=l["km"], bearing=l["bearing"], source=SOURCE[l["kind"]]), "geometry": {"type": "LineString", "coordinates": [l["start"], l["end"]]}})
            for m in l.get("landmarks", []): gj.append({"type": "Feature", "properties": dict(uid=int(uid), leg=i + 1, kind="landmark", name=m["name"], km_off=m["km_off"]), "geometry": {"type": "Point", "coordinates": m["at"]}})
        for cn in r["corners"]: gj.append({"type": "Feature", "properties": dict(uid=int(uid), kind="corner", name=cn["text"]), "geometry": {"type": "Point", "coordinates": cn["at"]}})
    prev = SOLVER / "boundaries.json"
    if prev.exists():                        # keep an existing narration when the legal text it was written from is unchanged
        old = json.load(open(prev))["zones"]
        for k, r in out.items():
            o = old.get(k)
            if o and o.get("in_words") and o["legal"] == r["legal"] and not o["in_words"].startswith("(no"): r["in_words"] = o["in_words"]
    if do_narrate:
        todo = {k: r for k, r in out.items() if not r.get("in_words")}
        narrate(todo, workers)
        for i, l in enumerate(L):
            if l.startswith("#") and out[l.split()[0][1:]].get("in_words"):
                L[i] = l + "\n  In words: " + out[l.split()[0][1:]]["in_words"]
    tag = "" if want is None else "_" + "_".join(codes)
    json.dump(dict(step_km=STEP, snap_km=SNAP, min_leg_km=MIN_LEG, beacon_km=BEACON, zones=out), open(SOLVER / f"boundaries{tag}.json", "w"), indent=1, ensure_ascii=False)
    (SOLVER / f"BOUNDARIES{tag}.txt").write_text("\n".join(L) + "\n")
    json.dump({"type": "FeatureCollection", "features": gj}, open(SOLVER / f"boundaries{tag}.geojson", "w"))
    print(f"{len(out)} zones → {SOLVER / f'boundaries{tag}.json'}, BOUNDARIES{tag}.txt")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("codes", nargs="*", help="team codes (E1 T2 …); default all zones")
    ap.add_argument("--narrate", action="store_true", help="muse-glimmer ≤3-sentence 'in words' per zone (text only; numbers and names come from the legs)")
    ap.add_argument("--workers", type=int, default=36)
    a = ap.parse_args(); main(a.codes, a.narrate, a.workers)
