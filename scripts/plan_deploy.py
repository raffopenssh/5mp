#!/usr/bin/env python3
"""Deployment footprint on the SOLVED zoning: where the ECHO, TANGO and focal-point staff go in year 1 and year 2, and why.

    python3 -W ignore scripts/plan_deploy.py [--tag ""] [--staff-y1 30 --staff-y2 80] [--echo-y2 5 --tango-y2 5 --fp-y2 3] [--narrate]

Runs AFTER plan_solver.py solve/rank (reads zones<tag>.geojson, solve<tag>_lab.npy, movement.json, movement_bundle_ud.npy).
The solver decides the CLASSES (zoning is a land question and its objective is fixed, docs/agents/zoning.md 'Solver');
this step decides WHERE A SMALL STAFF STANDS FIRST, which is a triage question and is answered with measured numbers only:

  ECHO  (community conservancy zones): urgency = the gold-rush exposure of the zone — the XSA mining model's top-5 % target cells
        (skill printed in DEPLOY.txt from the prediction.json MINING_MODEL selects - scripts/mining_model.py), its candidates and the abandoned-
        village-on-gold watchlist — multiplied by people × conversion threat P10 (the fitted 2015→today model, AUC 0.86) and
        by the zone's shield of core/corridor (RANK.txt). A zone with no gold target and no people scores 0 and is not staffed.
        The team SITE is a GHSL settlement inside the zone (a settlement is water: people live there) that lies within
        ROAD_KM of a trunk…tertiary road (the teams are on motorbikes), largest first; water is re-checked against
        HydroRIVERS / 1930s wells / JRC and printed, never assumed silently.
  TANGO (corridor zones): urgency = herd utilisation the band carries × people within 25 km (herders meet residents) × gold
        targets in the band (a rush on a herd route is where the two conflicts meet); site = settlement on a road inside or
        within SITE_REACH_KM of the band, at the band's densest passage; active from the bundle's onset month.
  FP    (focal points): towns ≥ fp_min_pop on a road, ranked by the staffed ECHO/TANGO sites within FP_REACH_KM.

Year 1 = the first --staff-y1 people worth of sites in urgency order (a team is 5: 4 scouts + 1 leader, per the budget's
unit line; an FP is 1); year 2 = --staff-y2. Team counts are bounded by --echo-y2/--tango-y2/--fp-y2 (the operational
span one coordinator can hold, 4–6 / 4–6 / 2–4). The FOOTPRINT is the union of the zones a team serves plus a
SITE_REACH_KM disc round its site; everything else on the map is drawn without an outline.

Outputs in data/plan_zones/solver/: deploy<tag>.json, DEPLOY<tag>.txt, deploy<tag>_teams.geojson (points), deploy<tag>_footprint.geojson
(one polygon per team, year attribute), and with --narrate a muse-glimmer brief per team built ONLY from the served zones'
stored descriptions (boundary, rationale, panel story where present) — the LLM combines text, it never classifies or ranks."""
import argparse, json, math, os, re, sqlite3, sys, time
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np
from shapely.geometry import shape, mapping, Point, MultiPolygon, LineString
from shapely.ops import unary_union, transform
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "scripts"))
import plan_conservancy_units as P
import plan_solver as S
import __main__; __main__.Grid = P.Grid
OUT = S.OUT; log = P.log
TEAM = 5            # 4 scouts + 1 leader (budget 'PER UNIT' line); FP = 1
ROAD_KM = 5.0       # a site with a trunk…tertiary road within this distance is a full-score base (motorbike teams)
ROAD_FAR_KM = 25.0  # beyond this no mapped road is reported; the OSRM car share then decides whether tracks exist
SITE_REACH_KM = 25.0
FP_REACH_KM = 120.0
MIN_SITE_POP = 150  # a hamlet of fewer people is not a base

def load(tag):
    st = S.load_state(); G = st["G"]
    zs = json.load(open(OUT / f"zones{tag}.geojson"))["features"]
    for f in zs:
        p = f["properties"]
        for k in ("towns", "boundary", "rationale", "herd_bundles", "overlaps", "geology"):
            if isinstance(p.get(k), str) and p[k][:1] in "[{":
                try: p[k] = json.loads(p[k])
                except Exception: pass
    lab = np.load(OUT / f"solve{tag}_lab.npy")
    p10 = np.load(OUT / "threat_p10.npy") if (OUT / "threat_p10.npy").exists() else np.zeros(lab.shape, np.float32)
    bud = np.load(OUT / "movement_bundle_ud.npy").astype(np.float32) if (OUT / "movement_bundle_ud.npy").exists() else None
    mv = json.load(open(OUT / "movement.json")) if (OUT / "movement.json").exists() else None
    rk = json.load(open(OUT / f"rank_conservancies{tag}.json")) if (OUT / f"rank_conservancies{tag}.json").exists() else None
    return st, G, zs, lab, p10, bud, mv, rk

def site_index(con, G):
    """Settlements (GHSL clusters; people = water), roads, water sources, town names — everything a site is tested against."""
    C = P.cells(con, G); S_ = G.settlements
    roads = [g for g, *_ in P.roads(con)]; rtree = STRtree(roads)          # roads_heigit (XSA rows cover CAR + SSD since 2026-09-06)
    hcon = sqlite3.connect(P.HDB); W = []
    for n, gj in con.execute("SELECT COALESCE(name,''), geojson FROM park_rivers_hydro WHERE park_id=? AND (stream_order>=4 OR name!='')", (P.AOI,)): W.append((shape(json.loads(gj)), (n or "river") + " (HydroRIVERS)"))
    for gj, in con.execute("SELECT geojson FROM park_waterbodies WHERE park_id=? AND waterbody_type LIKE '%perennial%'", (P.AOI,)): W.append((shape(json.loads(gj)), "perennial surface water (JRC)"))
    for p_, n in P.hist_waters(hcon, G.aoi): W.append((Point(p_), n))
    wtree = STRtree([g for g, _ in W])
    OT = P.osm_towns(con, hcon); ottree = STRtree([Point(lo, la) for _, lo, la, _ in OT])
    from shapely.ops import nearest_points
    def line_dist(pt, g):
        q = nearest_points(pt, g)[1]; return P.dkm((pt.x, pt.y), (q.x, q.y))
    def road_km(lon, lat, km=ROAD_KM):
        pt = Point(lon, lat); best = None
        for j in rtree.query(pt.buffer(km / 111 * 1.2)):
            d = line_dist(pt, roads[j]); best = d if best is None or d < best else best
        return best
    def water_at(lon, lat, km=P.WATER_KM):
        pt = Point(lon, lat); best = None
        for j in wtree.query(pt.buffer(km / 111 * 1.2)):
            d = line_dist(pt, W[j][0])
            if d <= km and (best is None or d < best[0]): best = (round(d, 1), W[j][1])
        return best
    def name_at(lon, lat, fallback):
        best = None
        for j in ottree.query(Point(lon, lat).buffer(0.08)):
            d = P.dkm((lon, lat), OT[j][1:3]); t = OT[j][3]
            if d <= P.TOWN_REACH_KM.get(t, 8) and (best is None or d + P.TOWN_PENALTY_KM.get(t, 0) < best[0]): best = (d + P.TOWN_PENALTY_KM.get(t, 0), OT[j][0])
        return best[1] if best else (fallback + "* (nearest_place, unverified)" if fallback else "unnamed settlement")
    towns = [(n, lo, la) for n, lo, la, t in OT if t in ("city", "town", "verified_town")]
    return dict(C=C, S=S_, road_km=road_km, water_at=water_at, name_at=name_at, towns=towns)

class Access:
    """OSRM travel-time matrix (data/eval/xsa_mining/osrm_times.npz: 2,337 settlement stations x 15,788 0.05-deg cells, minutes,
    best of car/foot/bush-walk; docs/agents/mining.md 'Travel times'). A base is judged by what it can REACH: the share of a
    zone's targets (gold cells, people, area) within REACH_MIN by the best mode, and the car share of those trips (motorbikes
    ride where cars do). Where the matrix is absent (non-XSA), reach falls back to straight-line at 25 km/h on a road, 4 km/h off."""
    def __init__(self, G):
        f = ROOT / "data/eval/xsa_mining/osrm_times.npz"; self.ok = f.exists()
        if not self.ok: return
        z = np.load(f); self.t = z["t_min"]; self.mode = z["mode"]; self.st = z["stations"]; self.cells = z["cells"]
        self.sttree = STRtree([Point(*xy) for xy in self.st]); self.celltree = STRtree([Point(*xy) for xy in self.cells])
    def station(self, lon, lat, km=2.5):
        if not self.ok: return None
        j = self.sttree.nearest(Point(lon, lat)); d = P.dkm((lon, lat), tuple(self.st[j]))
        return int(j) if d <= km else None
    def cell_ids(self, mask, G):
        """OSRM cell ids whose centre falls in the 2 km-grid mask"""
        rr, cc = G.rc_arr(self.cells[:, 0], self.cells[:, 1]); ok = G.inside(rr, cc); out = np.zeros(len(self.cells), bool)
        out[ok] = mask[rr[ok], cc[ok]]; return np.flatnonzero(out)
    def reach(self, j, cids, w, reach_min):
        """(weighted share of cids within reach_min, median minutes to the weighted targets, car share of the trips within reach)"""
        if j is None or not len(cids): return None
        t = self.t[j, cids].astype(float); m = self.mode[j, cids]; w = np.asarray(w, float); w = w / max(w.sum(), 1e-9)
        within = t <= reach_min
        med = float(np.interp(0.5, np.cumsum(w[np.argsort(t)]), np.sort(t))) if w.sum() > 0 else float("nan")
        return dict(share=round(float(w[within].sum()), 3), median_min=int(round(med)), car_share=round(float((m[within] == 0).sum() / max(within.sum(), 1)), 2), n_cells=int(len(cids)))

REACH_MIN = 240     # a day's out-and-back from the base: 4 h each way by the best mode

def shortlist(cands, IX, AC, G, zone_mask, target_w, prefer=None, k=3, need_road=True, exclude=(), exclude_geom=()):
    """cands: settlement rows (lat, lon, pop, cls, ...). Keep settlements >= MIN_SITE_POP that are not camps and have a road
    within ROAD_KM; score each by what it REACHES of the zone (OSRM, weighted by target_w on the zone's cells) x log(people);
    return the top k with their numbers. The deterministic score picks; muse-glimmer may re-order the shortlist (adjudicate)."""
    rows = [x for x in cands if x[3] != "temporary_camp" and (x[2] or 0) >= MIN_SITE_POP]
    rows.sort(key=lambda x: -((prefer(x) if prefer else 1.0) * math.log1p(x[2] or 0)))
    cids = AC.cell_ids(zone_mask, G) if AC.ok else np.zeros(0, int)
    rr, cc = (G.rc_arr(AC.cells[cids, 0], AC.cells[cids, 1]) if len(cids) else (np.zeros(0, int), np.zeros(0, int)))
    w = target_w[rr, cc] if len(cids) else np.zeros(0)
    out = []
    for x in rows[:120]:
        rk = IX["road_km"](x[1], x[0], ROAD_FAR_KM)
        j = AC.station(x[1], x[0]); rc = AC.reach(j, cids, w, REACH_MIN) if j is not None else None
        # road: a mapped trunk-tertiary road within ROAD_KM is best; further out the OSRM car share says whether tracks exist
        # (SSD's mapped road net is sparse — a hard filter dropped the 50,000-people zones); no road AND no car trips = not a base
        road_ok = (rk is not None and rk <= ROAD_KM) or (rc is not None and rc["car_share"] >= 0.5)
        if need_road and not road_ok: continue                      # TANGO walks (hundreds of km, like the herders; helicopter in extremis): no road test
        pref = prefer(x) if prefer else 1.0; road_f = 1.0 if (rk is not None and rk <= ROAD_KM) else 0.7
        score = (rc["share"] if rc else 0.3) * (0.5 + 0.5 * (rc["car_share"] if rc else 0.5)) * pref * road_f * math.log1p(x[2] or 0)
        name = IX["name_at"](x[1], x[0], x[5])
        if exclude and site_excluded(name, exclude): continue          # a settlement set aside by declared constraint (--deprioritise site:NAME)
        if exclude_geom and any(g.contains(Point(x[1], x[0])) for g in exclude_geom): continue   # a settlement on the penalised ground (east of the line / in the polygon)
        out.append(dict(row=x, road_km=(round(rk, 1) if rk is not None else None), water=IX["water_at"](x[1], x[0]), reach=rc, score=round(score, 3), name=name))
        if len(out) >= 40: break
    out.sort(key=lambda o: -o["score"]); return out[:k]

def pct_rank(v):
    from scipy.stats import rankdata
    v = np.asarray(v, float); return (rankdata(v) - 1) / max(len(v) - 1, 1) if len(v) > 1 else np.ones(len(v))

DEPRIO_DEFAULT = 0.25

def parse_deprioritise(specs):
    """--deprioritise KEY[=FACTOR] → {key: factor}; key = zone uid (str of int), ISO3 (upper), or 'site:name' (lower)."""
    out = {}
    for sp in specs:
        k, _, f = sp.partition("="); f = float(f) if f else DEPRIO_DEFAULT
        if not (0 < f <= 1): raise SystemExit(f"--deprioritise {sp}: FACTOR must be in (0, 1]")
        k = k.strip(); k = ("site:" + k[5:].strip().lower()) if k.lower().startswith("site:") else (k.upper() if k.isalpha() else str(int(k)))
        out[k] = f
    return out

def site_excluded(name, exclude):
    n = (name or "").lower()
    return any(re.search(r"(^|[^a-z])" + re.escape(x) + r"([^a-z]|$)", n) for x in exclude)

def penalty_of(c, DP):
    """factor for a candidate zone: the zone's own uid first, else — for ECHO candidates only — its country (ISO3). An ISO3 key names a
    STRAND (the foreign ECHO budget line: COD, SDN, CAF); TANGO corridor zones are always the SSD strand and a corridor that crosses a
    border (251 Kafia-Kingi lies in SDN-administered ground) is penalised only by its own uid. 1.0 = not penalised."""
    if not DP: return 1.0
    if str(c["uid"]) in DP: return DP[str(c["uid"])]
    return DP.get((c.get("country") or "SSD").upper(), 1.0) if "strand" in c else 1.0

def spatial_constraint(a, con, G):
    """The declared ground, as polygons with factors: east of a line (--deprioritise-east-of), inside polygons (--deprioritise-polygon),
    outside one polygon (--prioritise-polygon). The line is a roads_heigit road (its parts merged south→north, gaps bridged) or a GeoJSON
    line; it is extended at both ends along the end bearings far beyond the study area, and 'east' is the side towards +lon."""
    out = []
    def fac(spec):
        k, _, f = spec.partition("="); return k, (float(f) if f else DEPRIO_DEFAULT)
    def load_geom(k):
        if Path(k).exists() and k.lower().endswith(".gpx"):           # a hand-traced line (Locus / any GPX): trkpt/rtept/wpt in file order
            pts = [(float(lo), float(la)) for la, lo in re.findall(r'lat="([-\d.]+)"\s+lon="([-\d.]+)"', open(k).read())]
            if not pts: raise SystemExit(f"{k}: no lat/lon points")
            return LineString(pts)
        if Path(k).exists():
            gj = json.load(open(k)); feats = gj.get("features") or [gj]
            return unary_union([shape(f.get("geometry") or f) for f in feats])
        gs = [shape(json.loads(gj)) for gj, in con.execute("SELECT geojson FROM roads_heigit WHERE park_id=? AND name=?", (P.AOI, k))]
        if not gs: raise SystemExit(f"--deprioritise-east-of {k}: no road of that name in roads_heigit and no such file")
        from shapely.ops import linemerge
        m = linemerge(unary_union(gs)); parts = list(m.geoms) if m.geom_type == "MultiLineString" else [m]
        parts.sort(key=lambda g: g.bounds[1])                              # south → north; each part oriented south → north; gaps bridged straight
        coords = []
        for g in parts:
            c = list(g.coords); c = c if c[0][1] <= c[-1][1] else c[::-1]; coords += c
        return coords
    if a.deprioritise_east_of:
        k, f = fac(a.deprioritise_east_of); coords = load_geom(k)
        if not isinstance(coords, list): coords = list(coords.coords)
        coords.sort(key=lambda c: c[1])                                       # south → north
        if a.line_clip_lat: coords = [c for c in coords if a.line_clip_lat[0] <= c[1] <= a.line_clip_lat[1]]
        if len(coords) < 2: raise SystemExit("--deprioritise-east-of: fewer than 2 vertices left after clipping")
        (x0, y0), (x1, y1) = coords[0], coords[1]; (xa, ya), (xb, yb) = coords[-2], coords[-1]
        ext = 30.0   # degrees: far beyond any study-area edge
        d0 = math.hypot(x1 - x0, y1 - y0); d1 = math.hypot(xb - xa, yb - ya)
        south = (x0 - (x1 - x0) / d0 * ext, y0 - (y1 - y0) / d0 * ext); north = (xb + (xb - xa) / d1 * ext, yb + (yb - ya) / d1 * ext)
        line = [south] + coords + [north]
        from shapely.geometry import Polygon
        east = Polygon(line + [(90.0, north[1]), (90.0, south[1])]).buffer(0)
        desc = describe_line(coords, con, south, north)
        out.append((f"east of the line {Path(k).name if Path(k).exists() else k!r}: {desc}", f, east, line))
    for spec in a.deprioritise_polygon:
        k, f = fac(spec); out.append((f"inside {k}", f, load_geom(k).buffer(0), None))
    if a.prioritise_polygon:
        k, f = fac(a.prioritise_polygon); from shapely.geometry import box
        out.append((f"outside {k}", f, box(-180, -90, 180, 90).difference(load_geom(k).buffer(0)), None))
    return [(n, f, g) for n, f, g, _ in out] if not out or True else out

OSM_WATER = ROOT / "data/osm_raw/waterways_xsa/jur_sue_box.geojson"   # osmium extract of Geofabrik south-sudan.osm.pbf, waterway=river|stream|canal, box 27.4-28.7 E / 4.3-9.0 N (2026-10-16)

def describe_line(coords, con, south, north):
    """The line in the words of the water it follows. Every vertex is matched to the nearest OSM waterway (OSM_WATER; name, or
    'unnamed OSM river/stream' when OSM carries no name) within 5 km, HydroRIVERS (named, order >= 3) as the fallback; consecutive
    vertices on the same water merge into runs 'NAME S–N'. Then the two straight extensions by their bearings. Printed in DEPLOY.txt
    so the constraint reads as geography (a river line), not as a file name."""
    W = []
    if OSM_WATER.exists():
        for f in json.load(open(OSM_WATER))["features"]:
            pr = f["properties"]; W.append((shape(f["geometry"]), pr.get("name") or f"unnamed OSM {pr.get('waterway') or 'waterway'}", "OSM"))
    for n, o, gj in con.execute("SELECT name, stream_order, geojson FROM park_rivers_hydro WHERE park_id=? AND name!='' AND stream_order>=3", (P.AOI,)): W.append((shape(json.loads(gj)), n, "HydroRIVERS"))
    tree = STRtree([g for g, _, _ in W]); runs = []
    for lon, lat in coords:
        pt = Point(lon, lat); cand = [(W[j][0].distance(pt) * 111, W[j][2] != "OSM", W[j][1], W[j][2]) for j in tree.query(pt.buffer(5 / 111))]
        cand = [c for c in cand if c[0] <= 5]; d, _, name, src = min(cand) if cand else (99, 0, "open ground (no mapped waterway within 5 km)", "")
        name = name if "unnamed" in name or not src else f"{name} ({src})"
        if runs and runs[-1][0].split(" (")[0] == name.split(" (")[0]: name = runs[-1][0]     # same river, other source: one run
        if runs and runs[-1][0] == name: runs[-1][2] = lat; runs[-1][3] = max(runs[-1][3], d)
        else: runs.append([name, lat, lat, d])
    def brg(a, b):
        ang = (math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) + 360) % 360
        return ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"][int((ang + 11.25) // 22.5) % 16]
    parts = [f"{n} {s_:.2f}–{e:.2f} N" + (f" (≤ {d:.1f} km off the mapped channel)" if d < 99 else "") for n, s_, e, d in runs]
    return ("; ".join(parts) + f"; extended {brg(coords[1], coords[0])} from {coords[0][1]:.2f} N and {brg(coords[-2], coords[-1])} from {coords[-1][1]:.2f} N on the end bearings to the study-area edge")

def penalise(cands, DP, SP=(), zs=()):
    """cands sorted by PUBLISHED urgency. Records rank_pub/urgency_pub on every candidate, multiplies the penalised ones' urgency by
    their factor and re-sorts: the published numbers stay in the record, the working order is the constrained one. A penalised
    zone is deprioritised, not deleted: with a factor of 1 it is where it was; with 0.25 it is behind every unpenalised zone
    whose urgency exceeds a quarter of its own."""
    for i, c in enumerate(cands, 1): c["rank_pub"] = i; c["urgency_pub"] = c["urgency"]
    geom = {f["properties"]["uid"]: shape(f["geometry"]).buffer(0) for f in zs} if SP else {}
    for c in cands:
        f = penalty_of(c, DP)
        for name, F, g in SP:   # area-weighted: the share of the zone on the penalised ground carries the factor, the rest keeps its weight
            z = geom[c["uid"]]; share = z.intersection(g).area / max(z.area, 1e-12) if z.intersects(g) else 0.0
            c["share_penalised"] = round(max(c.get("share_penalised", 0.0), share), 3)
            if str(c["uid"]) not in DP: f = min(f, 1.0 - (1.0 - F) * share)   # a factor declared BY NAME overrides the line (e.g. --deprioritise 126=1 keeps Yambio whole)
        f = round(f, 4)
        if f < 1.0: c["deprioritised"] = f; c["urgency"] = round(c["urgency"] * f, 3)
    cands.sort(key=lambda c: (-c["urgency"], c["rank_pub"]))
    for i, c in enumerate(cands, 1): c["rank_pen"] = i

# Budget STRANDS. The South Sudan request (SSD: ECHO + TANGO + FP under the staff caps) is the plan; a community zone in
# another country is funded on that country's own line — capped, id-prefixed and legally framed separately — so a foreign
# conservancy never displaces a South Sudanese one (user decisions 2026-09-07: CAR, then COD and SDN). strand code →
# (ISO3 of the zone, team-id prefix, cap flag, legend/budget name, legal instrument).
STRANDS = {
    "CAR": dict(iso="CAF", prefix="K", flag="echo_car_y2", name="CAR conservancies", law="Code de protection de la faune (CAR)", dashed=True),
    "COD": dict(iso="COD", prefix="D", flag="echo_cod_y2", name="DRC conservancies", law="Loi n° 14/003 relative à la conservation de la nature (DRC)", dashed=True),
    "SDN": dict(iso="SDN", prefix="S", flag="echo_sdn_y2", name="Sudan conservancies", law="Wildlife Conservation and National Parks Act 1986 (Sudan)", dashed=False),
}   # dashed: drawn dashed + lighter on the deployment sheet (user 2026-09-07: CAR and DRC dashed, Sudan like South Sudan)
def strand_of(country): return next((k for k, v in STRANDS.items() if v["iso"] == country), "SSD")

def focal_points_of(chosen, n_fp, town_pop, seed=()):
    """greedy by MARGINAL coverage: each focal point scores only the team sites no earlier focal point already holds within
    FP_REACH_KM, so three FPs do not pile onto one cluster of teams while another region's teams report to nobody.
    `seed` = focal points already standing (year 1's, when staging year 2): they stay, and count against n_fp.
    town_pop: [{name, lon, lat, pop, road_km}] — towns >= fp_min_pop (saved in deploy.json as fp_towns so plan_deploy_frontier.py grows the same FPs)."""
    teams_ = [s for s in chosen if s["kind"] != "FP"]; covered = set(); out = []
    for f0 in seed:
        served = [s for s in teams_ if P.dkm((f0["lon"], f0["lat"]), (s["lon"], s["lat"])) <= FP_REACH_KM]
        covered.update(id(s) for s in served)
        out.append(dict(f0, zone_uids=sorted({u for s in served for u in s["zone_uids"]}) or f0["zone_uids"]))
    while len(out) < n_fp:
        best = None
        for t in town_pop:
            n, lo, la, tp = t["name"], t["lon"], t["lat"], t["pop"]
            if any(P.dkm((lo, la), (o["lon"], o["lat"])) < 60 for o in out): continue
            served = [s for s in teams_ if P.dkm((lo, la), (s["lon"], s["lat"])) <= FP_REACH_KM]
            new = [s for s in served if id(s) not in covered]
            if not new: continue
            tot = sum(s["urgency"] * (2.0 if P.dkm((lo, la), (s["lon"], s["lat"])) <= 10 else 1.0) for s in new) + 0.001 * math.log1p(tp)
            if best is None or tot > best[0]: best = (tot, t, served, new)
        if best is None: break
        tot, t, served, new = best; n, lo, la, tp = t["name"], t["lon"], t["lat"], t["pop"]; covered.update(id(s) for s in new)
        out.append(dict(kind="FP", staff=1, zone_uids=sorted({u for s in served for u in s["zone_uids"]}), urgency=round(tot / max(len(teams_), 1), 3), place=n, lon=round(lo, 4), lat=round(la, 4), site_people=int(tp), road_km=t["road_km"],
                        water="town (supplies itself)", season="year-round", why=f"town of {tp:,} people; {len(served)} of this year's team sites within {FP_REACH_KM:g} km ({', '.join(s['place'] for s in served)}), {len(new)} of them not held by an earlier focal point; county and traditional authorities are seated here", towns=[n], serves=[s["place"] for s in served]))
    return out

def pm_fp_unit(town, town_pop):
    """the Project Manager as a focal point: the named town (fp_towns entry), staff 0, pm=True; zone_uids/serves are filled by focal_points_of from the teams it reaches"""
    t = next((t for t in town_pop if t["name"] == town), None)
    if t is None: raise SystemExit(f"--pm-fp-town {town!r}: not an FP town (>= fp_min_pop people on a road); towns: {', '.join(x['name'] for x in town_pop)}")
    return dict(kind="FP", staff=0, pm=True, zone_uids=[], urgency=0.0, place=t["name"], lon=round(t["lon"], 4), lat=round(t["lat"], 4), site_people=int(t["pop"]), road_km=t["road_km"],
                water="town (supplies itself)", season="year-round", why=f"DECISION (AP-RCA, Oct 2026): the Project Manager, based here, is the wave-1 focal point — no focal-point salary or desk is budgeted for {t['name']} in the first year; paid focal points join in wave 2 where the teams need one", towns=[t["name"]], serves=[])

def stage_key(s): return (-(round(s["robust"], 1) if s["kind"] == "ECHO" else s["urgency"]), -s["urgency"])

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default=""); ap.add_argument("--staff-y1", type=int, default=40); ap.add_argument("--staff-y2", type=int, default=80)
    ap.add_argument("--tango-y1", type=int, default=None, help="TANGO teams in the FIRST wave (default: all of --tango-y2 — corridor teams go in before the herds, plan.yaml wave_dates_by_kind; 2026-10-01)")
    ap.add_argument("--echo-y1", type=int, default=None, help="ECHO teams in the first wave (default: half of --echo-y2, at least 2)")
    ap.add_argument("--echo-y2", type=int, default=5); ap.add_argument("--tango-y2", type=int, default=5); ap.add_argument("--fp-y2", type=int, default=3, help="PAID focal points by year 2 (the PM-as-FP town, if any, is on top)")
    ap.add_argument("--pm-fp-town", default=None, help="town whose wave-1 focal point IS the Project Manager (no FP salary/desk; staff 0); default plan.yaml pm_fp_town; '' = none")
    ap.add_argument("--echo-car-y2", type=int, default=2, help="ECHO teams on CAR community zones by year 2 — the CAR strand, a separate budget outside --staff-y1/--staff-y2 (0 = none)")
    ap.add_argument("--echo-cod-y2", type=int, default=1, help="ECHO teams on DRC community zones by year 2 — the COD strand, its own budget (0 = none)")
    ap.add_argument("--echo-sdn-y2", type=int, default=1, help="ECHO teams on Sudan community zones by year 2 — the SDN strand, its own budget (0 = none)")
    ap.add_argument("--robust-draws", type=int, default=500, help="perturbation draws for the selection-robustness check (weights ±, gold/people/threat inputs ±20%%, zone dropout)")
    ap.add_argument("--pin-tango", action="append", default=[], metavar="LAT,LON,LABEL",
                    help="a TANGO team placed by DECISION, not by rank: the qualifying settlement (>= MIN_SITE_POP) nearest the point, on the nearest "
                         "corridor zone, second wave, outside the --tango-y2 cap but inside --staff-y2 (review decision 2026-10-01: Boro-Medina)")
    ap.add_argument("--fp-min-pop", type=int, default=5000); ap.add_argument("--narrate", action="store_true"); ap.add_argument("--no-llm", action="store_true", help="ask no LLM: cached adjudications/briefs (deploy.json.prev, --reuse) are still applied, the rest keeps the machine choice / gets no brief"); ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--deprioritise", action="append", default=[], metavar="KEY[=FACTOR]",
                    help="a DECLARED management constraint (not a measurement): KEY = zone uid, ISO3 country of the zone, or site:NAME (a settlement removed "
                         "from every shortlist, case-insensitive). Urgencies are computed on the FULL pool as published; the penalised candidates' urgency "
                         "and selection frequency are then multiplied by FACTOR (0 < FACTOR <= 1, default %g) before placement, staging and the robustness "
                         "draws (re-run on the penalised pool, so the selection mass moves to the remaining neighbours). Deprioritised, not deleted." % DEPRIO_DEFAULT)
    ap.add_argument("--deprioritise-east-of", default=None, metavar="ROAD[=FACTOR]",
                    help="SPATIAL form of the constraint: a road name in roads_heigit (e.g. 'Tambura-Wau' = the A44) or a GeoJSON line file; the line is extended at both "
                         "ends along its end bearings to the study-area edge and everything EAST of it is deprioritised. A zone's factor is area-weighted, "
                         "f = 1 - (1 - FACTOR) x share of the zone east of the line (a zone the line crosses keeps the weight of its western part — nothing is "
                         "deleted, the lost weight moves to the western neighbours in the draws); a settlement east of the line is dropped from every shortlist")
    ap.add_argument("--line-clip-lat", type=float, nargs=2, default=None, metavar=("S", "N"), help="keep only the vertices of the --deprioritise-east-of line between these latitudes before extending it (a traced line that overshoots)")
    ap.add_argument("--deprioritise-polygon", action="append", default=[], metavar="FILE[=FACTOR]", help="GeoJSON polygon(s): zones inside are deprioritised by area share, sites inside dropped (repeatable)")
    ap.add_argument("--prioritise-polygon", default=None, metavar="FILE[=FACTOR]", help="GeoJSON polygon: everything OUTSIDE it is deprioritised by area share (the complement of --deprioritise-polygon)")
    ap.add_argument("--constraint", default=None, help="the constraint as declared, printed verbatim at the head of DEPLOY.txt and saved in deploy.json")
    ap.add_argument("--reuse", action="append", default=[], metavar="FILE", help="extra source of earlier adjudications and briefs: a deploy.json or a shared ZONING_PLAN_*.gpkg (deploy_teams layer); repeatable, searched after deploy.json.prev")
    a = ap.parse_args(); tag = a.tag
    DP = parse_deprioritise(a.deprioritise); EXCL_SITES = [k[5:] for k in DP if k.startswith("site:")]
    st, G, zs, lab, p10, bud, mv, rk = load(tag)
    con = sqlite3.connect(str(P.DB)); IX = site_index(con, G); C = IX["C"]; pop_r = np.nan_to_num(C["pop"])
    SP = spatial_constraint(a, con, G)            # [(name, factor, polygon)] — the penalised ground; None when none declared
    EXCL_GEOM = [g for _, _, g in SP]
    gold_r = np.nan_to_num(C["mine_top05"]) + 2 * np.nan_to_num(C["mine_cand"]) + 2 * np.nan_to_num(C["mine_watch"]) + 3 * np.nan_to_num(C["mine_rep"])
    rank_rows = {r["uid"]: r for r in (rk or {}).get("rows", [])}
    from scipy import ndimage
    near_people = ndimage.maximum_filter(pop_r, size=int(25000 / G.res) * 2 + 1)
    masks = {}
    for f in zs: masks[f["properties"]["uid"]] = G.rasterize([(transform(P.FWD, shape(f["geometry"])), 1)]).astype(bool)
    # ---------------------------------------------------------------- ECHO candidates: community zones by gold-rush urgency
    echo = []
    for f in zs:
        p = f["properties"]
        if p["solver_class"] != "community" or p["population_est"] < MIN_SITE_POP: continue
        m = masks[p["uid"]]; gold = float(gold_r[m].sum()); pxt = float(p["population_est"] * (p.get("threat_p10_mean") or 0))   # people × mean P10 of the zone's ground (built cells carry P10=1 and would make this = people)
        shield = rank_rows.get(p["uid"], {}).get("shield", 0.0); cen = shape(f["geometry"]).centroid
        echo.append(dict(uid=p["uid"], country=p.get("country"), lon=round(cen.x, 3), lat=round(cen.y, 3), perimeter_km=p.get("perimeter_km"), strand=strand_of(p.get("country")), gold=gold, gold_top05=int(p["mine_top05_cells"]), gold_cand=int(p["mine_candidates"]), gold_watch=int(p.get("mine_watchlist") or 0), gold_rep=int(p["mine_reported"]),
                         people=int(p["population_est"]), people_x_threat=pxt, shield=shield, new15=int(p["new_since_2015"]), area_ha=p["area_ha"], towns=[re.sub(r"\s[\d,]+$", "", t).split(" (")[0].replace("*", "") for t in (p.get("towns") or [])[:3]], rank=p.get("rank")))
    if echo:
        g_, t_, s_ = pct_rank([e["gold"] for e in echo]), pct_rank([e["people_x_threat"] for e in echo]), pct_rank([e["shield"] for e in echo])
        for e, gg, tt, ss in zip(echo, g_, t_, s_):
            e["urgency"] = round(float((0.5 * gg + 0.3 * tt + 0.2 * ss) * (1.0 if e["gold"] > 0 else 0.0)), 3)   # no gold target → 0: ECHO is the gold-rush instrument here
        echo.sort(key=lambda e: -e["urgency"])
        penalise(echo, DP, SP, zs)
    # ---------------------------------------------------------------- TANGO candidates: corridor zones by herd × people × gold
    tango = []
    for f in zs:
        p = f["properties"]
        if p["solver_class"] != "corridor" or p["area_ha"] < 20_000: continue
        m = masks[p["uid"]]; hb = p.get("herd_bundles") or []
        udr = np.zeros(lab.shape, np.float32)
        if bud is not None and hb:
            for h in hb: udr += bud[h["bundle"] - 1]
        elif bud is not None: udr = bud.sum(0)
        ud_in = float(udr[m].sum()); meet = float((udr * (near_people > 0))[m].sum()); gold = float(gold_r[m].sum())
        cen = shape(f["geometry"]).centroid
        tango.append(dict(uid=p["uid"], country=p.get("country"), lon=round(cen.x, 3), lat=round(cen.y, 3), perimeter_km=p.get("perimeter_km"), shield=rank_rows.get(p["uid"], {}).get("shield", 0.0),
                          ud=ud_in, meet=meet, gold=gold, gold_top05=int(p["mine_top05_cells"]), people=int(p["population_est"]), area_ha=p["area_ha"], bundles=[h["bundle"] for h in hb],
                          onset=min((h.get("onset") or "12" for h in hb), default=None), fronts=sum(h.get("fronts_fit") or 0 for h in hb), udr=udr, mask=m))
    if tango:
        u_, m_, g_ = pct_rank([t["ud"] for t in tango]), pct_rank([t["meet"] for t in tango]), pct_rank([t["gold"] for t in tango])
        for t, uu, mm, gg in zip(tango, u_, m_, g_): t["urgency"] = round(float(0.4 * uu + 0.35 * mm + 0.25 * gg) * (1.0 if t["ud"] > 0 else 0.0), 3)
        tango.sort(key=lambda t: -t["urgency"])
        penalise(tango, DP, SP, zs)
    # ---------------------------------------------------------------- sites: shortlist by OSRM reach, adjudicated by muse-glimmer
    AC = Access(G)
    def mk(kind, cand, zone_uids, urgency, season, why, towns):
        x = cand["row"]; w = cand["water"]; rc = cand["reach"]
        reach_txt = (f"reaches {rc['share']:.0%} of the zone's targets within {REACH_MIN//60} h ({rc['car_share']:.0%} of those trips by car/motorbike; median {rc['median_min']} min; OSRM car/foot/bush)" if rc else "reach unmeasured (no OSRM station within 2.5 km)")
        return dict(kind=kind, staff=TEAM, zone_uids=zone_uids, urgency=urgency, place=cand["name"], lon=round(x[1], 4), lat=round(x[0], 4), site_people=int(x[2] or 0), road_km=cand["road_km"], reach=rc,
                    water=(f"{w[1]} {w[0]} km" if w else f"settlement of {int(x[2] or 0):,} people (water implied by residence); no mapped river/well/JRC water within {P.WATER_KM:g} km — verify"),
                    season=season, why=why + f" Site: {int(x[2] or 0):,} people, " + (f"road {cand['road_km']} km" if cand.get("road_km") is not None else f"no mapped road within {ROAD_FAR_KM:g} km") + f", {reach_txt}.", towns=towns, alternatives=[])
    placed = []; adjud = []
    caps_f = {k: getattr(a, v["flag"]) for k, v in STRANDS.items()}
    pools = [e for e in echo if e["strand"] == "SSD"][:a.echo_y2 * 4]
    for k in STRANDS: pools += [e for e in echo if e["strand"] == k][:max(caps_f[k], 1) * 4]
    for e in pools:
        if e["urgency"] <= 0: continue
        f = next(f for f in zs if f["properties"]["uid"] == e["uid"]); gp = shape(f["geometry"]).buffer(0)
        inside = [x for x in IX["S"] if gp.contains(Point(x[1], x[0]))]
        tw = (gold_r + pop_r / max(float(pop_r[masks[e["uid"]]].sum()), 1) * max(float(gold_r[masks[e["uid"]]].sum()), 1)) * masks[e["uid"]]   # targets = gold cells and people, equal total weight
        sl = shortlist(inside, IX, AC, G, masks[e["uid"]], tw, exclude=EXCL_SITES, exclude_geom=EXCL_GEOM)
        if not sl: e["skipped"] = "no settlement ≥%d people on a road within %g km inside the zone" % (MIN_SITE_POP, ROAD_KM); continue
        why = (f"community zone {e['uid']} ranks {e['rank_pub']}/{len(echo)} on gold-rush urgency: {e['gold_top05']} top-5% gold-target cells, {e['gold_cand']} model candidates, {e['gold_watch']} abandoned-village-on-gold watchlist places, {e['gold_rep']} reported workings; "
               f"{e['people']:,} people, people×threat {e['people_x_threat']:,.0f}, {e['new15']} settlements founded since 2015; shields core/corridor on {e['shield']:.0%} of its edge" + (f"; budget rank C{e['rank']}" if e.get("rank") else "") + ".")
        s_ = mk("ECHO", sl[0], [e["uid"]], e["urgency"], "year-round; village outreach in the rains, boundary walks Dec–Feb", why, e["towns"]); s_["strand"] = e["strand"]
        if e.get("deprioritised"): s_["deprioritised"] = e["deprioritised"]; s_["urgency_pub"] = e["urgency_pub"]
        s_["alternatives"] = [dict(name=c["name"], people=int(c["row"][2] or 0), road_km=c["road_km"], reach=c["reach"], score=c["score"], lon=round(c["row"][1], 4), lat=round(c["row"][0], 4)) for c in sl]
        placed.append(s_)
    for t in tango[:a.tango_y2 * 3]:
        if t["urgency"] <= 0: break
        sc = ndimage.gaussian_filter(t["udr"] * t["mask"] * (near_people > 0), 2); near = ndimage.binary_dilation(t["mask"], iterations=int(SITE_REACH_KM * 1000 / G.res))
        cands = []
        for x in IX["S"]:
            r, c = G.rc(x[1], x[0])
            if 0 <= r < G.h and 0 <= c < G.w and near[r, c]: cands.append(x)
        def herd_at(x, sc=sc):
            r, c = G.rc(x[1], x[0]); return 0.05 + float(sc[r, c]) / max(float(sc.max()), 1e-12)
        sl = shortlist(cands, IX, AC, G, t["mask"], t["udr"] * t["mask"], prefer=herd_at, need_road=False, exclude=EXCL_SITES, exclude_geom=EXCL_GEOM)
        if not sl: t["skipped"] = "no settlement on a road within %g km of the band" % SITE_REACH_KM; continue
        onset = t["onset"]
        why = (f"corridor zone {t['uid']} ranks {t['rank_pub']}/{len(tango)} on herd × people × gold: bundles {t['bundles']} ({t['fronts']:,} fitted fronts), herd utilisation in band {t['ud']:.2f} of which {t['meet']/max(t['ud'],1e-9):.0%} within 25 km of residents; "
               f"{t['gold_top05']} gold-target cells in the band; {t['people']:,} people on corridor land.")
        s_ = mk("TANGO", sl[0], [t["uid"]], t["urgency"], (f"from month {onset} (bundle onset) through the dry season" if onset else "dry season"), why, [])
        if t.get("deprioritised"): s_["deprioritised"] = t["deprioritised"]; s_["urgency_pub"] = t["urgency_pub"]
        s_["alternatives"] = [dict(name=c["name"], people=int(c["row"][2] or 0), road_km=c["road_km"], reach=c["reach"], score=c["score"], lon=round(c["row"][1], 4), lat=round(c["row"][0], 4)) for c in sl]
        placed.append(s_)
        if len(t["bundles"]) >= 3 and t["area_ha"] >= 1_000_000:
            # a corridor zone that merges three or more routes over a million hectares is not one team's ground: a second site,
            # chosen among settlements the first cannot reach within REACH_MIN (else the two would stand on the same passage)
            far = [x for x in cands if P.dkm((x[1], x[0]), (s_["lon"], s_["lat"])) > 2 * SITE_REACH_KM]
            sl2 = shortlist(far, IX, AC, G, t["mask"], t["udr"] * t["mask"], prefer=herd_at, need_road=False, exclude=EXCL_SITES, exclude_geom=EXCL_GEOM)
            if sl2:
                s2 = mk("TANGO", sl2[0], [t["uid"]], round(t["urgency"] * 0.85, 3), s_["season"], why + f" Second team: the zone spans {len(t['bundles'])} bundles and {t['area_ha']/1e6:.1f} M ha; this site is > {2*SITE_REACH_KM:g} km from the first.", [])
                s2["alternatives"] = [dict(name=c["name"], people=int(c["row"][2] or 0), road_km=c["road_km"], reach=c["reach"], score=c["score"], lon=round(c["row"][1], 4), lat=round(c["row"][0], 4)) for c in sl2]
                placed.append(s2)
    adjudicate(placed, zs, a.workers, prior_teams(tag, a.reuse), ask=not a.no_llm, exclude=EXCL_SITES, exclude_geom=EXCL_GEOM)
    # two teams of one kind on one passage are one team: after adjudication, a site within 2 x SITE_REACH_KM of a higher-urgency
    # site of the same kind falls back to its farthest shortlisted alternative, or is dropped
    placed.sort(key=lambda s: -s["urgency"]); kept = []; dropped = []
    for s_ in placed:
        clash = lambda lon, lat: any(k["kind"] == s_["kind"] and P.dkm((lon, lat), (k["lon"], k["lat"])) < 2 * SITE_REACH_KM for k in kept)
        if clash(s_["lon"], s_["lat"]):
            alt = next((c for c in s_.get("alternatives", []) if not clash(c["lon"], c["lat"])), None)
            if alt is None: s_["dropped"] = "within %g km of a higher-urgency %s site; no alternative" % (2 * SITE_REACH_KM, s_["kind"]); dropped.append(s_); continue
            s_.update(place=alt["name"], lon=alt["lon"], lat=alt["lat"], site_people=alt["people"], road_km=alt["road_km"], reach=alt["reach"]); s_["adjudication"] = (s_.get("adjudication") or "") + f" → moved to {alt['name']}: the chosen site was within {2*SITE_REACH_KM:g} km of a higher-urgency {s_['kind']} site"
        kept.append(s_)
    placed = kept
    # focal points: towns ≥ fp_min_pop, ranked per stage by the CHOSEN team sites within FP_REACH_KM (an FP follows its teams)
    town_pop = [(n, lo, la, sum(x[2] or 0 for x in IX["S"] if P.dkm((lo, la), (x[1], x[0])) <= 8)) for n, lo, la in IX["towns"]]
    town_pop = [t for t in town_pop if t[3] >= a.fp_min_pop]
    town_pop = [dict(name=n, lon=lo, lat=la, pop=tp, road_km=(round(r, 1) if (r := IX["road_km"](lo, la, ROAD_FAR_KM)) is not None else None)) for n, lo, la, tp in town_pop]
    def focal_points(chosen, n_fp, seed=()): return focal_points_of(chosen, n_fp, town_pop, seed)
    pm_town = a.pm_fp_town if a.pm_fp_town is not None else (__import__("yaml").safe_load(open(P.ROOT / "docs/plan/plan.yaml")).get("pm_fp_town") or "")
    pm_seed = [pm_fp_unit(pm_town, town_pop)] if pm_town else []
    # ---------------------------------------------------------------- staging: year 1, year 2 under the staff caps
    for s in placed: s.setdefault("strand", "SSD")
    # ECHO is staged by SELECTION STABILITY, not by point urgency (user decision 2026-09-07): the share of perturbed
    # re-rankings that pick the zone (selection_robustness). Two zones a few hundredths apart on urgency are a coin toss;
    # the one that survives the jitter is the one to fund. Frequencies are bucketed to 0.1 so 500-draw noise (±0.02) cannot
    # reorder near-equals — urgency breaks the tie inside a bucket. TANGO/FP keep their urgency order.
    rob_freq = selection_frequency(echo, a)
    if DP or SP:   # the published (unpenalised) frequency, for the constraint table
        rob_pub = selection_frequency([dict(x, deprioritised=None) for x in echo], a)
        for e in echo: e["robust_pub"] = rob_pub.get(e["uid"], 0.0); e["robust"] = rob_freq.get(e["uid"], 0.0)
    for s in placed:
        if s["kind"] == "ECHO": s["robust"] = rob_freq.get(s["zone_uids"][0], 0.0)
    def stage(cap_staff, n_echo, n_tango, n_fp, caps_foreign, seed_fp=()):
        """The South Sudan strand (ECHO SSD + TANGO + FP) is staged under the staff caps. A foreign strand (STRANDS: CAR,
        COD, SDN — ECHO teams on community zones across the border) is a SEPARATE BUDGET: capped by caps_foreign[strand]
        and not consuming the South Sudan caps, so a foreign conservancy never displaces one in SSD."""
        chosen = []; staff = 0; cnt = Counter()
        # interleave by urgency across kinds so year 1 is not all ECHO; FP added last (they follow the teams)
        pool = sorted([s for s in placed], key=stage_key)
        for s in pool:
            if s["strand"] != "SSD":
                if cnt[s["strand"]] < caps_foreign.get(s["strand"], 0): chosen.append(s); cnt[s["strand"]] += 1
                continue
            lim = n_echo if s["kind"] == "ECHO" else n_tango
            if cnt[s["kind"]] >= lim or staff + s["staff"] > cap_staff: continue
            chosen.append(s); cnt[s["kind"]] += 1; staff += s["staff"]
        for s in focal_points([c for c in chosen if c["strand"] == "SSD"], n_fp, seed_fp):
            if staff + s["staff"] > cap_staff: break
            s["strand"] = "SSD"; chosen.append(s); staff += s["staff"]
        return chosen, staff
    # year 1 first; year 2 then starts from year 1's focal points (a town office is not moved because the teams' centre moved)
    n_tango_y1 = a.tango_y2 if a.tango_y1 is None else a.tango_y1
    n_echo_y1 = max(2, a.echo_y2 // 2) if a.echo_y1 is None else a.echo_y1
    # PM-as-FP (plan.yaml pm_fp_town, 2026-10-16): the town is seeded as the ONLY wave-1 focal point (the PM, staff 0, no FP pay);
    # every paid FP (--fp-y2) then joins in wave 2 where the teams need one. Without it: first max(1, n//2) paid FPs in wave 1.
    y1, staff1 = stage(a.staff_y1, n_echo_y1, n_tango_y1, (1 if pm_seed else max(1, a.fp_y2 // 2)), {k: (max(1, c // 2) if c else 0) for k, c in caps_f.items()}, seed_fp=pm_seed)
    y2, staff2 = stage(a.staff_y2, a.echo_y2, a.tango_y2, a.fp_y2 + len(pm_seed), caps_f, seed_fp=[s for s in y1 if s["kind"] == "FP"])
    y1_keys = {(s["kind"], s["place"]) for s in y1}
    for s in y2: s["year"] = 1 if (s["kind"], s["place"]) in y1_keys else 2
    # ---------------------------------------------------------------- pinned TANGO teams (user decision; printed as such)
    # A pin is not a ranking result: it is a team the client asked for by name, carried through the same site rules (settlement
    # >= MIN_SITE_POP, reach, water printed) and the same budget. It consumes staff cap, not the ranked TANGO cap, and joins the TANGO
    # wave (first wave when all TANGO go first). `why` says "decision", so the report cannot present it as the model's choice.
    for spec in a.pin_tango:
        la, lo, label = spec.split(",", 2); la, lo = float(la), float(lo)
        if staff2 + TEAM > a.staff_y2: print(f"pin {label}: staff cap {a.staff_y2} reached, not placed"); continue
        kx = math.cos(math.radians(la))
        # nearest corridor zone by polygon distance
        zpoly = {f["properties"]["uid"]: shape(f["geometry"]).buffer(0) for f in zs if f["properties"]["solver_class"] == "corridor"}
        t = min((t for t in tango if t["uid"] in zpoly), key=lambda t: zpoly[t["uid"]].distance(Point(lo, la)))
        dist_km = zpoly[t["uid"]].distance(Point(lo, la)) * 111 * kx
        cands = [x for x in IX["S"] if P.dkm((lo, la), (x[1], x[0])) <= SITE_REACH_KM]
        sl = shortlist(cands, IX, AC, G, t["mask"], t["udr"] * t["mask"], prefer=lambda x: 1.0 / (1.0 + P.dkm((lo, la), (x[1], x[0]))), need_road=False)
        if not sl: print(f"pin {label}: no settlement >= {MIN_SITE_POP} people within {SITE_REACH_KM:g} km of ({la}, {lo}); not placed"); continue
        sl.sort(key=lambda c: P.dkm((lo, la), (c["row"][1], c["row"][0])))     # the pin names a place: the nearest qualifying settlement is the site
        why = (f"DECISION, not rank (review of 1 Oct 2026): a herder team at {label}. Nearest corridor zone {t['uid']} ({dist_km:.0f} km away; bundles {t['bundles']}, "
               f"{t['fronts']:,} fitted fronts, {t['gold_top05']} gold-target cells in the band) already carries {sum(1 for s in y2 if s['kind'] == 'TANGO' and t['uid'] in s['zone_uids'])} TANGO team(s) at its eastern end; "
               f"this site puts the first people on the leg of it that runs west to the CAR border.")
        s_ = mk("TANGO", sl[0], [t["uid"]], round(t["urgency"] * 0.7, 3), (f"from month {t['onset']} (bundle onset) through the dry season" if t["onset"] else "dry season"), why, [label])
        s_["alternatives"] = [dict(name=c["name"], people=int(c["row"][2] or 0), road_km=c["road_km"], reach=c["reach"], score=c["score"], lon=round(c["row"][1], 4), lat=round(c["row"][0], 4)) for c in sl]
        s_.update(year=(1 if n_tango_y1 >= a.tango_y2 else 2), strand="SSD", pinned=label, place=label if P.dkm((lo, la), (s_["lon"], s_["lat"])) <= 3 else f"{s_['place']} (for {label})")
        y2.append(s_); staff2 += TEAM; print(f"pin {label}: TANGO at {s_['place']} ({s_['lon']}, {s_['lat']}), zone {t['uid']}, {sl[0]['row'][2]:,} people")
    for s in y2: s.setdefault("strand", "SSD")
    for i, s in enumerate([s for s in y2 if s["kind"] == "ECHO" and s["strand"] == "SSD"], 1): s["id"] = f"E{i}"
    for k, v in STRANDS.items():   # K = CAR, D = DRC, S = Sudan (C is the planner's conservancy rank, E the SSD ECHO)
        for i, s in enumerate([s for s in y2 if s["kind"] == "ECHO" and s["strand"] == k], 1): s["id"] = f"{v['prefix']}{i}"
    strand_staff = {k: dict(year1=sum(s["staff"] for s in y2 if s["strand"] == k and s["year"] <= 1), year2=sum(s["staff"] for s in y2 if s["strand"] == k), cap_teams=caps_f[k], placed=sum(1 for s in y2 if s["strand"] == k), **v) for k, v in STRANDS.items()}
    robust = selection_robustness(echo, y2, a, freq=rob_freq)
    for i, s in enumerate([s for s in y2 if s["kind"] == "TANGO"], 1): s["id"] = f"T{i}"
    for i, s in enumerate(sorted([s for s in y2 if s["kind"] == "FP"], key=lambda s: (not s.get("pm"), s["year"])), 1): s["id"] = f"F{i}"
    # ---------------------------------------------------------------- footprints
    zg = {f["properties"]["uid"]: shape(f["geometry"]).buffer(0) for f in zs}
    feats = []
    for s in y2:
        if s["kind"] == "FP": g = Point(s["lon"], s["lat"]).buffer(FP_REACH_KM / 111 * 0.5)
        else: g = unary_union([zg[u] for u in s["zone_uids"]] + [Point(s["lon"], s["lat"]).buffer(SITE_REACH_KM / 111)])
        s["footprint_km2"] = int(g.area * 111 * 111 * math.cos(math.radians(s["lat"])))
        feats.append({"type": "Feature", "properties": {k: v for k, v in s.items() if k not in ("lon", "lat")}, "geometry": mapping(g.simplify(0.002))})
    json.dump({"type": "FeatureCollection", "features": feats}, open(OUT / f"deploy{tag}_footprint.geojson", "w"))
    json.dump({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {k: v for k, v in s.items() if k not in ("lon", "lat")}, "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]}} for s in y2]}, open(OUT / f"deploy{tag}_teams.geojson", "w"))
    # ---------------------------------------------------------------- text
    def text_out():
        L = [f"DEPLOY{tag} — ECHO / TANGO / focal-point footprint on the solved zoning (zones{tag}.geojson). Team = {TEAM} (4 scouts + 1 leader), FP = 1.",
             f"  SOUTH SUDAN STRAND: year 1 ≤ {a.staff_y1} staff → {staff1} placed ({dict(Counter(s['kind'] for s in y1 if s['strand'] == 'SSD'))});  year 2 ≤ {a.staff_y2} staff → {staff2} placed ({dict(Counter(s['kind'] for s in y2 if s['strand'] == 'SSD'))}); caps ECHO {a.echo_y2} TANGO {a.tango_y2} FP {a.fp_y2}",
             *[f"  {k} STRAND (separate budget, {v['name']}; ids {v['prefix']}1..; {v['law']}): ECHO on {v['iso']} community zones, cap {v['cap_teams']} by year 2 → {v['placed']} placed, {v['year1']}/{v['year2']} staff in Y1/Y2 outside the caps above" for k, v in strand_staff.items()],
             f"  ECHO urgency = 0.5·gold-target rank + 0.3·(people × mean P10) rank + 0.2·shield rank, zero without a gold target ({G.mining_note}); TANGO urgency = 0.4·herd UD + 0.35·UD within 25 km of residents + 0.25·gold targets in band (rank-percentiles).",
             f"  a site = GHSL settlement ≥ {MIN_SITE_POP} people (residence implies water; mapped water re-checked and printed); ECHO needs a trunk…tertiary road ≤ {ROAD_KM:g} km or OSRM car trips (motorbikes); TANGO walks with the herds (no road test; helicopter in extremis). Shortlist scored by OSRM reach within {REACH_MIN//60} h; muse-glimmer adjudicates among the shortlisted sites only. Footprint = served zones + {SITE_REACH_KM:g} km round the site.", ""]
        L += constraint_lines(a, DP, echo, tango, placed, y2, SP, dropped)
        for yr in (1, 2):
            L.append(f"YEAR {yr}" + (" (adds to year 1)" if yr == 2 else ""))
            for s in sorted([s for s in y2 if s["year"] == yr], key=lambda s: (s["kind"] != "FP", s["kind"], -s["urgency"])):
                L.append(f"  {s['id']:<3} {s['kind']:<5} x{s['staff']}  {s['place']}{' (the Project Manager — no focal-point pay)' if s.get('pm') else ''}  ({s['lon']}, {s['lat']})  urgency {s['urgency']:.2f}" + (f"  picked in {s['robust']:.0%} of perturbed re-rankings" if s.get("robust") is not None else "") + f"  zones {s['zone_uids']}  footprint {s.get('footprint_km2', 0):,} km²")
                L.append(f"        water: {s['water']}; road {(str(s['road_km']) + ' km') if s.get('road_km') is not None else 'no mapped road within %g km (OSRM car trips say tracks exist)' % ROAD_FAR_KM}; when: {s['season']}"); L.append(f"        why: {s['why']}")
                if s.get("adjudication"): L.append(f"        site: {s['adjudication']}; shortlist {[(c['name'], c['people'], (c['reach'] or {}).get('share')) for c in s.get('alternatives', [])]}")
                if s.get("short"): L.append(f"        short: {s['short']}")
                if s.get("brief"): L.append(f"        brief: {s['brief']}")
                if s.get("first_season"): L.append(f"        first season: {s['first_season']}")
            L.append("")
        L += robustness_lines(robust)
        L.append("NOT STAFFED (year 2 caps) — community zones with a gold target, by urgency:")
        staffed_uids = {u for s in y2 if s["kind"] == "ECHO" for u in s["zone_uids"]}
        for e in echo:
            if e["urgency"] <= 0: break
            if e["uid"] in staffed_uids: continue
            L.append(f"  zone {e['uid']:<4} {e['strand']}  urgency {e['urgency']:.2f}  gold t5/cand/watch/rep {e['gold_top05']}/{e['gold_cand']}/{e['gold_watch']}/{e['gold_rep']}  {e['people']:,} ppl  {', '.join(e['towns']) or '—'}" + (f"  [{e['skipped']}]" if e.get("skipped") else ""))
        return L
    L = text_out()
    if (OUT / f"deploy{tag}.json").exists(): (OUT / f"deploy{tag}.json").replace(OUT / f"deploy{tag}.json.prev")   # narration cache source
    pool = [{k: v for k, v in s_.items() if k not in ("alternatives",)} for s_ in sorted(placed, key=stage_key)]   # every placed site in staging order: plan_deploy_frontier.py grows a plan from it
    json.dump(dict(params=vars(a), constraint=a.constraint, deprioritise=DP, mining_model=P.MM.variant(), mining_note=G.mining_note, team_size=TEAM, road_km=ROAD_KM, site_reach_km=SITE_REACH_KM, year1_staff=staff1, year2_staff=staff2, strands=strand_staff,
                   pool=pool, dropped=[{k: v for k, v in s_.items() if k != "alternatives"} for s_ in dropped], fp_towns=town_pop, spatial_constraint=[dict(name=n, factor=f, geometry=mapping(g.simplify(0.01))) for n, f, g in SP], car_staff=dict(year1=strand_staff["CAR"]["year1"], year2=strand_staff["CAR"]["year2"], cap_teams=caps_f["CAR"]), robustness=robust, teams=y2, echo_candidates=[{k: v for k, v in e.items()} for e in echo], tango_candidates=[{k: v for k, v in t.items() if k not in ("udr", "mask")} for t in tango]),
              open(OUT / f"deploy{tag}.json", "w"), indent=1, ensure_ascii=False)
    (OUT / f"DEPLOY{tag}.txt").write_text("\n".join(L) + "\n"); print("\n".join(L))
    if a.narrate or a.no_llm: narrate(tag, y2, zs, a.workers, prior_teams(tag, a.reuse), ask=not a.no_llm); L = text_out(); (OUT / f"DEPLOY{tag}.txt").write_text("\n".join(L) + "\n"); print("\n".join(L[:6]))

def selection_frequency(echo, a, draws=None, seed=0):
    """Per ECHO candidate zone, the share of perturbed re-rankings that pick it (see selection_robustness). Computed once,
    BEFORE staging, and used as the staging order for ECHO — so the check is not a post-hoc comment on a choice made by
    point urgency but the choice itself. Deterministic (seed 0)."""
    draws = draws or a.robust_draws; rng = np.random.default_rng(seed)
    cand = [e for e in echo if e["urgency"] > 0 and not e.get("skipped")]
    if not cand: return {}
    caps = {"SSD": a.echo_y2, **{k: getattr(a, v["flag"]) for k, v in STRANDS.items()}}
    G_ = np.array([e["gold"] for e in cand]); T_ = np.array([e["people_x_threat"] for e in cand]); S_ = np.array([e["shield"] for e in cand]); K_ = np.array([e["strand"] for e in cand])
    F_ = np.array([e.get("deprioritised") or 1.0 for e in cand])   # --deprioritise: the draws run on the PENALISED pool (urgency × factor), so the selection mass a penalised zone loses goes to the zones that remain
    hits = Counter()
    for _ in range(draws):
        w = np.clip(np.array([0.5, 0.3, 0.2]) + rng.normal(0, 0.1, 3), 0.05, None); w /= w.sum()
        keep = rng.random(len(cand)) >= 0.1
        g = G_ * rng.lognormal(0, 0.2, len(cand)); t = T_ * rng.lognormal(0, 0.2, len(cand)); sh = S_ * rng.lognormal(0, 0.2, len(cand))
        idx = np.flatnonzero(keep)
        if not len(idx): continue
        u = (w[0] * pct_rank(g[idx]) + w[1] * pct_rank(t[idx]) + w[2] * pct_rank(sh[idx])) * (g[idx] > 0) * F_[idx]
        cnt = Counter()
        for j in np.argsort(-u):
            i = idx[j]; k = K_[i]
            if u[j] <= 0 or cnt[k] >= caps.get(k, 0): continue
            cnt[k] += 1; hits[cand[i]["uid"]] += 1
    return {e["uid"]: round(hits[e["uid"]] / draws, 3) for e in cand}   # the factor acts ONCE, inside every draw; the frequency is what the penalised pool then selects

def selection_robustness(echo, y2, a, draws=None, seed=0, freq=None):
    """Is the ECHO selection an artefact of the weights or of one noisy number? Re-run the urgency ranking `draws` times
    with (i) the three weights jittered (N(0, 0.1) on 0.5/0.3/0.2, renormalised), (ii) every zone's gold, people×threat and
    shield inputs multiplied by an independent log-normal (σ 0.2: GHSL's own bias band, one gold cell more or less),
    (iii) one candidate zone in ten dropped (a zone the community vetoes, Wildlife Act s.14(4)); then re-select under the
    same per-strand caps among zones that HAVE a site. Reports, per zone, the share of draws in which it is chosen. This
    is the deployment's robustness; the zoning's is plan_solver.py support (SUPPORT.txt) and the two must be read together.
    Solved analytically nowhere: the ranking is rank-percentile based, so the only honest test is to redo it."""
    draws = draws or a.robust_draws
    cand = [e for e in echo if e["urgency"] > 0 and not e.get("skipped")]
    if not cand: return dict(draws=0, note="no ECHO candidates")
    freq = freq if freq is not None else selection_frequency(echo, a, draws, seed)
    chosen0 = {u for s_ in y2 if s_["kind"] == "ECHO" for u in s_["zone_uids"]}
    # the by-urgency pick, for the record: what the point estimate alone would have funded
    caps = {"SSD": a.echo_y2, **{k: getattr(a, v["flag"]) for k, v in STRANDS.items()}}; cnt = Counter(); by_urgency = set()
    for e in cand:
        if cnt[e["strand"]] < caps.get(e["strand"], 0): cnt[e["strand"]] += 1; by_urgency.add(e["uid"])
    chosen = sorted([(e["uid"], e["strand"], freq[e["uid"]], ", ".join(e["towns"]) or "—") for e in cand if e["uid"] in chosen0], key=lambda r: -r[2])
    bench = sorted([(e["uid"], e["strand"], freq[e["uid"]], ", ".join(e["towns"]) or "—") for e in cand if e["uid"] not in chosen0 and freq[e["uid"]] >= 0.10], key=lambda r: -r[2])[:8]
    verdict = ("robust" if all(r[2] >= 0.75 for r in chosen) else "one or more marginal picks" if all(r[2] >= 0.5 for r in chosen) else "fragile")
    swapped = [(u, ", ".join(next(e["towns"] for e in cand if e["uid"] == u)) or "—") for u in sorted(by_urgency - chosen0)]
    swapped_in = [(u, ", ".join(next(e["towns"] for e in cand if e["uid"] == u)) or "—") for u in sorted(chosen0 - by_urgency)]
    return dict(draws=draws, perturbation="weights N(0,0.1) renormalised; gold, people×threat, shield × lognormal(σ 0.2) per zone; 10% zone dropout",
                staged_by="selection frequency (bucketed 0.1), urgency as tie-break", verdict=verdict, chosen=chosen, bench=bench, freq=freq,
                by_urgency_only=sorted(by_urgency), swapped_out=swapped, swapped_in=swapped_in)

def constraint_lines(a, DP, echo, tango, placed, y2, SP=(), dropped=()):
    """DEPLOY.txt head: the declared constraint verbatim, every penalised candidate (published rank/urgency → penalised → where it lands),
    the excluded sites, and zone 218 Boro-Medina's position (asked for by name in the Oct 2026 review)."""
    L = []
    if DP or SP:
        L.append("MANAGEMENT CONSTRAINT (declared, not measured): " + (a.constraint or "--deprioritise " + " ".join(f"{k}={f:g}" for k, f in DP.items())))
        for name, F, g in SP: L.append(f"  spatial rule: {name}, factor {F:g}; a zone's factor = 1 - (1 - {F:g}) x its area share on that ground; settlements on it leave every shortlist")
        L.append(f"  method: urgencies computed on the full pool as published; penalised candidates' urgency × factor (applied once, inside every draw too), then placement, staging and the {a.robust_draws} robustness draws re-run on the penalised pool —")
        L.append("  the selection mass a penalised zone loses is redistributed by the draws to the remaining candidates (deprioritised, not deleted; a factor of 1 restores the published order).")
        L.append("  an ISO3 key names a STRAND (foreign ECHO budget line); TANGO corridor zones are always the SSD strand and are penalised by uid only (251 Kafia-Kingi is SDN-administered ground: flagged, not penalised).")
        L.append("  penalised candidate      published rank / urgency  →  penalised (×factor)  →  where it lands")
        by_uid = {u: s_ for s_ in y2 if s_["kind"] != "FP" for u in s_["zone_uids"]}
        placed_uid = {u for s_ in placed if s_["kind"] != "FP" for u in s_["zone_uids"]}
        dropped_uid = {u: s_["dropped"] for s_ in dropped for u in s_["zone_uids"]}
        for kind, cands in (("ECHO", echo), ("TANGO", tango)):
            rest = [c for c in cands if c.get("deprioritised") and not (c["urgency_pub"] > 0 and (c["rank_pub"] <= 40 or str(c["uid"]) in DP))]
            if rest: L.append(f"    ({kind}: {len(rest)} further penalised candidates below published rank 40 or with zero urgency — none of them was a pick; full list in deploy.json)")
            for c in cands:
                if not c.get("deprioritised") or c in rest: continue
                if c["uid"] in by_uid: land = f"staffed: {by_uid[c['uid']]['id']} year {by_uid[c['uid']]['year']} at {by_uid[c['uid']]['place']}"
                elif c["uid"] in placed_uid: land = "site placed, not staged (outside the caps)"
                elif c["uid"] in dropped_uid: land = "dropped: " + dropped_uid[c["uid"]]
                elif c.get("skipped"): land = "no site (" + c["skipped"] + ")"
                else: land = "not placed (outside the placement pool)"
                rob = f"  robust {c['robust_pub']:.2f} → {c['robust']:.2f}" if "robust" in c else ""
                shr = f" {c['share_penalised']:.0%} of area" if c.get("share_penalised") else ""
                L.append(f"    {kind} zone {c['uid']:<4} {c['country'] or '':<3} {', '.join(c.get('towns') or []) or '—':<24} #{c['rank_pub']:<3} {c['urgency_pub']:.3f}  →  #{c['rank_pen']:<3} {c['urgency']:.3f} (×{c['deprioritised']:g}{shr}){rob}  →  {land}")
        for k, f in DP.items():
            if k.startswith("site:"): L.append(f"    site {k[5:]!r}: removed from every shortlist (case-insensitive)")
    z218 = next((c for c in echo if c["uid"] == 218), None)
    if z218: L.append(f"  zone 218 Boro-Medina: ECHO urgency {z218['urgency_pub']:.3f}, published rank #{z218['rank_pub']}/{len(echo)}" + (f" → penalised #{z218['rank_pen']}" if DP else "") + (f"; conservancy rank C{z218['rank']}" if z218.get("rank") else "") + f"; {z218['gold_top05']} gold-target cells, {z218['people']:,} people" + ("; served as corridor ground by the TANGO team(s) on zone 255" if any(255 in s_["zone_uids"] for s_ in y2 if s_["kind"] == "TANGO") else ""))
    else: L.append("  zone 218 Boro-Medina: not an ECHO candidate (not a community zone with ≥ %d people)" % MIN_SITE_POP)
    for s_ in y2:
        if s_["kind"] != "FP" and s_.get("brief", "").startswith("(brief needed"): L.append(f"  brief needed: {s_['id']} {s_['kind']} at {s_['place']} — no cached brief for this site; nothing invented")
    if L: L.append("")
    return L

def robustness_lines(R):
    if not R or not R.get("draws"): return ["SELECTION ROBUSTNESS: not computed (" + str((R or {}).get("note", "")) + ")", ""]
    L = [f"SELECTION ROBUSTNESS — {R['draws']} re-rankings under perturbation ({R['perturbation']}), same caps per strand: {R['verdict'].upper()}.",
         f"  ECHO zones are STAGED by this share ({R['staged_by']}), not by point urgency; per zone, the share of draws that pick it (≥ 0.75 robust, 0.50–0.75 marginal, below: an artefact of the numbers as they stand):"]
    for uid, k, f, towns in R["chosen"]: L.append(f"    chosen   zone {uid:<4} {k}  {f:.2f}  {towns}")
    for uid, k, f, towns in R["bench"]: L.append(f"    bench    zone {uid:<4} {k}  {f:.2f}  {towns}")
    for uid, k, f, towns in R["bench"]:
        weaker = [c for c in R["chosen"] if c[1] == k and c[2] < f]
        if weaker: L.append(f"  ⚠ bench zone {uid} ({towns}) is picked MORE often than chosen zone {weaker[-1][0]} ({weaker[-1][3]}) in the same strand — the two are a coin toss on today's numbers; the choice between them is a judgement, not a measurement")
    for (uo, to), (ui, ti) in zip(R.get("swapped_out", []), R.get("swapped_in", [])):
        L.append(f"  ↔ point urgency alone would have funded zone {uo} ({to}); stability funds zone {ui} ({ti}) instead")
    L.append("  zoning-level robustness (classes under resampled data) is a different question: plan_solver.py support → SUPPORT.txt."); L.append("")
    return L

ADJ_SYS = """You are a field-operations adjudicator for a conservation team base in the Western Bahr el Ghazal / Western Equatoria / CAR border region.
You receive the machine's SHORTLIST of up to three base sites for ONE team, each with measured numbers: people at the site (GHSL, a lower bound), distance to the nearest trunk-tertiary road, mapped water source, and OSRM reach (share of the zone's targets within 4 h by the best of car/foot/bush, car share of those trips, median minutes). You also receive the zone's stored description.
Choose the base. Rules, in order: (1) a hamlet under 1,000 people is a base only if no larger site is within 0.15 of its reach share; (2) otherwise prefer the higher reach share, then the shorter median minutes; (3) a site with no settlement and no mapped water is not a base; (4) do not invent facts; if the shortlist's first entry satisfies these, keep it.
Answer ONLY a JSON object: {"choice": <1-based index into the shortlist>, "reason": "<=2 sentences using only the numbers given"}"""

def prior_teams(tag, reuse=()):
    """Earlier team records that carry adjudications and briefs: deploy<tag>.json.prev first, then every --reuse file — a deploy.json
    or a shared ZONING_PLAN_*.gpkg (its deploy_teams layer stores the same fields, JSON columns as text). The LLM that wrote them
    (muse-glimmer-30b) is no longer served (2026-10-01), so these are the only source of adjudications and briefs until another is."""
    out = []
    for f in [OUT / f"deploy{tag}.json.prev"] + [Path(x) for x in reuse]:
        if not f.exists(): log(f"prior_teams: {f} missing, skipped"); continue
        if f.suffix == ".gpkg":
            c = sqlite3.connect(str(f)); c.row_factory = sqlite3.Row
            rows = [dict(r) for r in c.execute("SELECT * FROM deploy_teams")]
            for r in rows:
                for k in ("zone_uids", "alternatives", "reach", "towns"):
                    if isinstance(r.get(k), str):
                        try: r[k] = json.loads(r[k])
                        except ValueError: pass
                gb = r.pop("geom", None); r["_src"] = f.name
                if gb and "lon" not in r:   # GeoPackage binary: 'GP' + version + flags + srs_id(4) + envelope (flags bits 1-3) + WKB
                    try:
                        from shapely import wkb
                        env = (gb[3] >> 1) & 7; off = 8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(env, 0); g = wkb.loads(bytes(gb[off:]))
                        r["lon"], r["lat"] = round(g.x, 4), round(g.y, 4)
                    except Exception as e: log(f"prior_teams: {f.name} {r.get('id')}: geometry unreadable ({e})")
            out += rows
        else:
            out += [dict(t, _src=f.name) for t in json.load(open(f)).get("teams", [])]
    return out

def llm_safe(sys_prompt, user, max_tokens):
    """P.llm_json raises on HTTP errors (muse-glimmer-30b: HTTP 400 since 2026-10-01); a dead model must read as 'unavailable', not kill the run."""
    try: return P.llm_json(sys_prompt, user, max_tokens=max_tokens)
    except Exception as e: return {"error": f"LLM unavailable ({type(e).__name__}: {str(e)[:80]})"}

def adjudicate(placed, zs, workers, prior, ask=True, exclude=(), exclude_geom=()):
    """muse-glimmer re-orders each team's shortlist (a judgement call the numbers alone leave open: a bigger village vs a
    better-connected one). The LLM chooses among measured candidates only; it cannot add a site or move one."""
    from concurrent.futures import ThreadPoolExecutor
    byuid = {f["properties"]["uid"]: f["properties"] for f in zs}
    # cache (2026-10-01): a team whose kind, machine reasons and shortlist are unchanged keeps the adjudication already made
    # (deploy.json.prev), so a re-run for one new team does not re-open every site choice — and the model that made the
    # earlier choices (muse-glimmer-30b) need not still be served. Delete the .prev to re-adjudicate everything.
    old = {}
    for t in prior:
        if t.get("adjudication") and t["kind"] != "FP":
            old.setdefault((t["kind"], tuple(t["zone_uids"]), re.sub(r" Site.*$", "", t["why"]), tuple(c["name"] for c in t.get("alternatives", []) or [])), t)
    cached = 0
    for s in placed:
        k = (s["kind"], tuple(s["zone_uids"]), re.sub(r" Site.*$", "", s["why"]), tuple(c["name"] for c in s.get("alternatives", [])))
        if k in old and not s.get("pinned") and "lon" in old[k] and not (exclude and site_excluded(old[k]["place"], exclude)) and not any(g.contains(Point(old[k]["lon"], old[k]["lat"])) for g in exclude_geom):
            t = old[k]; s.update(place=t["place"], lon=t["lon"], lat=t["lat"], site_people=t["site_people"], road_km=t["road_km"], reach=t["reach"], why=t["why"], adjudication=t["adjudication"]); s["_adj_cached"] = True; cached += 1
    todo = [i for i, s in enumerate(placed) if not s.pop("_adj_cached", False)]
    if not ask:
        for i in todo: placed[i]["adjudication"] = "kept machine choice (no LLM asked: --no-llm, nothing cached for this shortlist)"
        print(f"adjudicate: {cached} kept from prior runs ({', '.join(sorted({t['_src'] for t in old.values()})) or 'none'}), {len(todo)} keep the machine choice (--no-llm)"); return
    print(f"adjudicate: {cached} kept from prior runs, {len(todo)} to ask {P.LLM_MODEL}")
    def one(i):
        s = placed[i]; p = byuid.get(s["zone_uids"][0], {})
        z = {k: p.get(k) for k in ("solver_class", "area_ha", "population_est", "towns", "boundary", "mine_top05_cells", "herd_bundles")}
        out = llm_safe(ADJ_SYS, f"TEAM {s['kind']}; machine reasons: {s['why']}\n\nSHORTLIST:\n{json.dumps(s['alternatives'], ensure_ascii=False)}\n\nZONE:\n{json.dumps(z, ensure_ascii=False)[:3000]}", max_tokens=2500)
        return i, out
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, out in ex.map(one, todo):
            s = placed[i]; ch = out.get("choice")
            if not isinstance(ch, int) or not (1 <= ch <= len(s["alternatives"])): s["adjudication"] = f"kept machine choice ({out.get('error', 'no valid choice')})"; continue
            c = s["alternatives"][ch - 1]; s["adjudication"] = f"{P.LLM_MODEL.split('/')[-1]} chose #{ch} ({c['name']}): {out.get('reason', '')}"
            if ch != 1:
                s.update(place=c["name"], lon=c["lon"], lat=c["lat"], site_people=c["people"], road_km=c["road_km"], reach=c["reach"])
                rc = c["reach"]; s["why"] = re.sub(r" Site: .*$", "", s["why"]) + f" Site (adjudicated): {c['people']:,} people, road {c['road_km']} km" + (f", reaches {rc['share']:.0%} of targets within {REACH_MIN//60} h ({rc['car_share']:.0%} by car/motorbike)." if rc else ".")

BRIEF_SYS = """You write the field brief for ONE conservation team (ECHO = community scouts working in a community conservancy; TANGO = scouts travelling with transhumant herders on a corridor; FP = a one-person focal point in a town).
You are given the team's site and the machine's reasons, plus the STORED DESCRIPTIONS of the zones it serves (boundary by named features, measured numbers, rationale, and where present a panel story). COMBINE and CONDENSE these into one brief. Use ONLY names, numbers and features that appear in the input; do not invent places, do not re-classify the zones, do not rank anything.
Style: plain prose for a field coordinator; no coordinates, no field names (write 'gold-target cells', not mine_top05_cells), round numbers, no list longer than three names.
Answer ONLY a JSON object: {"short": "ONE sentence, <=30 words: base, ground, purpose", "brief": "<=5 sentences, <=120 words: where the team is based and why, what ground it covers (two or three named edges), whom it meets and when, what it watches for", "first_season": "<=2 sentences: the first two concrete things to do"}"""

def narrate(tag, teams, zs, workers, prior, ask=True):
    from concurrent.futures import ThreadPoolExecutor
    byuid = {f["properties"]["uid"]: f["properties"] for f in zs}
    # only NEW or changed teams are narrated: a brief written for the same kind, site, zones and machine reasons is kept from
    # the previous deploy.json / --reuse files (the LLM is the expensive step; user 2026-09-07). The key deliberately excludes
    # the id, which shifts when the order changes (E5 → E4), and the year/wave (2026-10-01: TANGO moved to wave 1 — the
    # briefs never mention the wave; the 'Wave:' sentence of `why` is stripped for the same reason).
    nowave = lambda w: re.sub(r" Wave: .*$", "", w or "")
    old = {}
    for t in prior:
        if t.get("brief") and not t["brief"].startswith("(brief failed"): old.setdefault((t["kind"], t["place"], tuple(t["zone_uids"]), nowave(t["why"])), t)
    todo = []
    for s in teams:
        o = old.get((s["kind"], s["place"], tuple(s["zone_uids"]), nowave(s["why"])))
        if o: s["brief"], s["first_season"], s["short"] = o["brief"], o.get("first_season"), o.get("short")
        else: todo.append(s)
    print(f"narrate: {len(teams) - len(todo)} briefs kept from prior runs, {len(todo)} to write" + (" — NOT written (--no-llm)" if ask is False and todo else ""), file=sys.stderr)
    teams_all = teams; teams = todo
    if not ask:
        for s in teams: s["brief"] = "(brief needed: no cached brief for this team/site/zones and no LLM asked (--no-llm); nothing invented)"; s["first_season"] = s["short"] = None; print(f"brief needed: {s['id']} {s['kind']} at {s['place']}", file=sys.stderr)
        teams = []
    def one(s):
        zdesc = []
        for u in s["zone_uids"][:4]:
            p = byuid.get(u, {})
            zdesc.append({k: p.get(k) for k in ("uid", "solver_class", "area_ha", "country", "population_est", "new_since_2015", "towns", "boundary", "unattributed_pct", "mine_top05_cells", "mine_candidates", "mine_watchlist", "mine_reported", "fronts_long", "herd_bundles", "clearing_since_2020_km2", "cropland_2019_pct", "rationale", "panel_name", "panel_story", "panel_boundary_in_words", "panel_herd_calendar")})
        user = "TEAM:\n" + json.dumps({k: s[k] for k in ("id", "kind", "place", "lon", "lat", "site_people", "road_km", "water", "season", "why", "year")}, ensure_ascii=False) + "\n\nZONES SERVED (stored descriptions):\n" + json.dumps(zdesc, ensure_ascii=False)[:9000]
        return s["id"], llm_safe(BRIEF_SYS, user, max_tokens=3000)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for tid, out in ex.map(one, teams):
            s = next(t for t in teams if t["id"] == tid)
            if "error" in out: s["brief"] = f"(brief failed: {out.get('error')})"; continue
            s["brief"] = (out.get("brief") or "").strip(); s["first_season"] = (out.get("first_season") or "").strip(); s["short"] = (out.get("short") or "").strip()
    teams = teams_all
    for fn in (f"deploy{tag}_teams.geojson", f"deploy{tag}_footprint.geojson"):
        fc = json.load(open(OUT / fn))
        for f in fc["features"]:
            s = next((t for t in teams if t["id"] == f["properties"]["id"]), None)
            if s: f["properties"]["brief"] = s.get("brief"); f["properties"]["first_season"] = s.get("first_season"); f["properties"]["short"] = s.get("short")
        json.dump(fc, open(OUT / fn, "w"))
    d = json.load(open(OUT / f"deploy{tag}.json")); d["teams"] = teams; json.dump(d, open(OUT / f"deploy{tag}.json", "w"), indent=1, ensure_ascii=False)

if __name__ == "__main__": main()
