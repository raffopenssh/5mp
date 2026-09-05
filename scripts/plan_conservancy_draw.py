#!/usr/bin/env python3
"""Draw candidate community conservancies bounded by features people can see.

The 2026 Act's conservancies must be explained to a chief, a herder and a
mining desk with one sentence each: "from the Bo River to the Jur, down to the
Marra Hills". So the boundary is NOT a buffer or a Voronoi cell — it is built
only from
    - named rivers (HydroRIVERS, park_rivers_hydro; unnamed reaches take the
      1930s sheet's water label if >= 2 vertices agree, like histmaps `link`),
    - hill ridges: histmap symbols peak/trig_point + terrain labels "J. …"/
      "… HILLS", chained within RIDGE_LINK_KM and named from the labels,
    - the received zone polygons (park, Southern NP) as hard edges.
These lines are polygonized into faces; a conservancy is a connected set of
faces grown from a seed until it holds the seed's settlements and at least
--min-ha (default 2,000 ha). Every conservancy is written with its area, its
settlement list, its population (GHSL lower bound) and a boundary description
that names the feature on each side — and says how many km could NOT be
attributed to a nameable feature (invariant 7/8: a boundary that is 40% "line
on a map" must say so).

    python3 scripts/plan_conservancy_draw.py auto                # seeds from rim clusters
    python3 scripts/plan_conservancy_draw.py seed 27.68 6.20 --name Bandala-Nagero --radius-km 30
    python3 scripts/plan_conservancy_draw.py faces --bbox 27.3,5.9,28.2,6.9   # debug: the face mesh

Outputs to data/plan_zones/conservancies/<name>.{geojson,kml} + SUMMARY.txt.
"""
import argparse, json, math, re, sqlite3, sys
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np, pyproj
from shapely.geometry import (Polygon, MultiPolygon, LineString, MultiLineString,
                              Point, box, shape, mapping)
from shapely.ops import unary_union, polygonize, transform, linemerge
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
DB, HDB = ROOT / "db.sqlite3", ROOT / "data/histmaps/labels.sqlite3"
ZONES, OUT = ROOT / "data/plan_zones", ROOT / "data/plan_zones/conservancies"
AOI = "XSA_Study_Area"
EQA = pyproj.Transformer.from_crs(4326, "+proj=cea", always_xy=True).transform
km2 = lambda g: transform(EQA, g).area / 1e6
kml_ = lambda g: transform(EQA, g).length / 1000
RIDGE_LINK_KM, RIDGE_NAME_KM, RIVER_NAME_KM = 20.0, 6.0, 2.0
HILL_RE = re.compile(r"^(J\.|JEBEL|JABAL)\s|\bHILLS?\b|\bMTS?\b|\bMOUNT", re.I)

def dkm(a, b):
    return math.hypot((a[0]-b[0])*111*math.cos(math.radians((a[1]+b[1])/2)), (a[1]-b[1])*111)

# ------------------------------------------------------------------ inputs
def read_kml(path):
    t = path.read_text(encoding="utf-8", errors="replace")
    out = {}
    for pm in re.findall(r"<Placemark>(.*?)</Placemark>", t, re.S):
        nm = re.search(r"<name>(.*?)</name>", pm, re.S)
        nm = (nm.group(1) if nm else path.stem).replace("&apos;", "'").strip()
        polys = [Polygon([tuple(map(float, c.split(",")[:2])) for c in m.group(1).split()]).buffer(0)
                 for m in re.finditer(r"<coordinates>(.*?)</coordinates>", pm, re.S) if len(m.group(1).split()) >= 4]
        if polys: out[nm] = unary_union(polys)
    return out

def zones():
    z = {}
    for f in ZONES.glob("*.kml"): z.update(read_kml(f))
    return z

def rivers(con, hcon, bb, min_order):
    """Named/major river reaches inside bb as (geom, name, kind)."""
    rows = con.execute("""SELECT hyriv_id, COALESCE(name,''), stream_order, geojson FROM park_rivers_hydro
        WHERE park_id=? AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ? AND (stream_order>=? OR name!='')""",
        (AOI, bb[0]-.3, bb[2]+.3, bb[1]-.3, bb[3]+.3, min_order)).fetchall()
    wl = hcon.execute("SELECT text, lon, lat FROM labels_dedup WHERE category='water' AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?",
                      (bb[0]-.3, bb[2]+.3, bb[1]-.3, bb[3]+.3)).fetchall()
    wl = [(re.sub(r"\s+", " ", t).strip(), lo, la) for t, lo, la in wl if len(t) > 2 and "?" not in t and "=" not in t]
    wpts = [Point(lo, la) for _, lo, la in wl]
    tree = STRtree(wpts) if wpts else None
    by_name = defaultdict(list)
    for hid, name, order, gj in rows:
        g = shape(json.loads(gj))
        if not name and tree is not None:
            votes = Counter()
            for x, y in g.coords:
                for i in tree.query(Point(x, y).buffer(RIVER_NAME_KM/111)):
                    if dkm((x, y), (wl[i][1], wl[i][2])) <= RIVER_NAME_KM: votes[wl[i][0]] += 1
            if votes and votes.most_common(1)[0][1] >= 2:
                name = votes.most_common(1)[0][0] + " (1930s sheet name)"
        by_name[name or f"unnamed river (order {order})"].append(g)
    return [(linemerge(unary_union(gs)) if len(gs) > 1 else gs[0], n, "river") for n, gs in by_name.items()]

def ridges(hcon, bb):
    """Hilltops from the traced sheets, chained into ridge lines."""
    pts = [(lo, la, "peak" if c == "peak" else "trig") for lo, la, c in hcon.execute(
        "SELECT lon, lat, category FROM symbols WHERE category IN ('peak','trig_point') AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?",
        (bb[0]-.2, bb[2]+.2, bb[1]-.2, bb[3]+.2))]
    labs = [(re.sub(r"\s+", " ", t).strip(), lo, la) for t, lo, la in hcon.execute(
        "SELECT text, lon, lat FROM labels_dedup WHERE category='terrain' AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?",
        (bb[0]-.2, bb[2]+.2, bb[1]-.2, bb[3]+.2)) if HILL_RE.search(t)]
    pts += [(lo, la, "label") for _, lo, la in labs]
    if not pts: return [], []
    # single-linkage chains
    n = len(pts); parent = list(range(n))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i in range(n):
        for j in range(i+1, n):
            if dkm(pts[i][:2], pts[j][:2]) <= RIDGE_LINK_KM: parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i in range(n): groups[find(i)].append(pts[i])
    lines, singles = [], []
    for g in groups.values():
        names = []
        for t, lo, la in labs:
            if any(dkm((lo, la), p[:2]) <= RIDGE_NAME_KM for p in g) and t not in names: names.append(t)
        name = (" – ".join(names[:3]) if names else "unnamed hills") + " ridge"
        if len(g) < 2:
            singles.append((Point(g[0][:2]), name)); continue
        xy = np.array([p[:2] for p in g]); c = xy.mean(0)
        u, s, vt = np.linalg.svd(xy - c); order = np.argsort((xy - c) @ vt[0])
        lines.append((LineString(xy[order]), name, "ridge"))
    return lines, singles

# ------------------------------------------------------------------ mesh
def settlements(con, bb):
    return con.execute("""SELECT lat, lon, population_est, classification, persistence, nearest_place
        FROM park_settlements WHERE park_id=? AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?""",
        (AOI, bb[0], bb[2], bb[1], bb[3])).fetchall()

def build_mesh(con, hcon, bb, excl, min_order):
    feats = rivers(con, hcon, bb, min_order)
    rl, singles = ridges(hcon, bb)
    feats += rl
    for nm, g in excl.items():
        feats.append((g.boundary, f"{nm} boundary", "zone"))
    frame = box(*bb)
    feats.append((frame.boundary, "working-window edge", "frame"))
    lines = unary_union([f[0] for f in feats])
    faces = [f for f in polygonize(lines) if f.intersects(frame)]
    ex = unary_union(list(excl.values())) if excl else None
    faces = [f for f in faces if ex is None or not ex.buffer(-1e-6).contains(f.representative_point())]
    return feats, faces, singles

def describe_boundary(poly, feats, step_km=1.0):
    ring = poly.exterior
    m = transform(EQA, ring); n = max(4, int(m.length / 1000 / step_km))
    ftree = STRtree([f[0] for f in feats]); c = poly.centroid
    by = Counter(); side = defaultdict(Counter); un = 0.0
    inv = pyproj.Transformer.from_crs("+proj=cea", 4326, always_xy=True).transform
    for i in range(n):
        p = transform(inv, m.interpolate(i / n, normalized=True))
        best, bd = None, 1.5
        for j in ftree.query(p.buffer(0.02)):
            d = dkm((p.x, p.y), feats[j][0].interpolate(feats[j][0].project(p)).coords[0])
            if d < bd: best, bd = j, d
        ang = math.degrees(math.atan2(p.y - c.y, (p.x - c.x) * math.cos(math.radians(c.y)))) % 360
        s = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"][int(((ang + 22.5) % 360) // 45)]
        if best is None: un += step_km; continue
        by[feats[best][1]] += step_km; side[feats[best][1]][s] += step_km
    parts = [f"{name} on the {'/'.join(k for k, _ in side[name].most_common(2))} ({k:.0f} km)" for name, k in by.most_common()]
    tot = m.length / 1000
    return parts, un, tot

def grow(faces, sets, seed, radius_km, min_ha, excl_union):
    ftree = STRtree(faces)
    fpop = [0]*len(faces); fset = [[] for _ in faces]
    for s in sets:
        p = Point(s[1], s[0])
        for i in ftree.query(p):
            if faces[i].contains(p): fpop[i] += s[2] or 0; fset[i].append(s); break
    sp = Point(*seed)
    start = next((i for i in ftree.query(sp) if faces[i].contains(sp)), None)
    if start is None: raise SystemExit("seed falls in an excluded zone or outside the window")
    # target faces: hold a settlement within radius of the seed
    want = {i for i in range(len(faces)) if fset[i] and any(dkm(seed, (s[1], s[0])) <= radius_km for s in fset[i])}
    want.add(start)
    # adjacency
    adj = defaultdict(set)
    for i, f in enumerate(faces):
        for j in ftree.query(f):
            if j != i and f.touches(faces[j]) and f.intersection(faces[j]).length > 1e-6: adj[i].add(j)
    # BFS from start, taking wanted faces and the cheapest connectors (smallest faces) between them
    sel = {start}; frontier = set(adj[start])
    while want - sel:
        cand = [(0 if i in want else 1, km2(faces[i]), i) for i in frontier if
                dkm(seed, faces[i].representative_point().coords[0]) <= radius_km*1.6]
        if not cand: break
        _, _, i = min(cand); sel.add(i); frontier |= adj[i]; frontier -= sel
    while km2(unary_union([faces[i] for i in sel]))*100 < min_ha:
        cand = [(-fpop[i], km2(faces[i]), i) for i in frontier if
                dkm(seed, faces[i].representative_point().coords[0]) <= radius_km*1.6]
        if not cand: break
        _, _, i = min(cand); sel.add(i); frontier |= adj[i]; frontier -= sel
    poly = unary_union([faces[i] for i in sel])
    if isinstance(poly, MultiPolygon): poly = max(poly.geoms, key=lambda g: g.area)
    poly = Polygon(poly.exterior)  # fill holes
    if excl_union is not None: poly = poly.difference(excl_union)
    if isinstance(poly, MultiPolygon): poly = max(poly.geoms, key=lambda g: g.area)
    inside = [s for s in sets if poly.contains(Point(s[1], s[0]))]
    return poly, inside, want - sel

def write(name, poly, inside, parts, un, tot, seed, faces_used, note=""):
    OUT.mkdir(parents=True, exist_ok=True)
    pop = sum(s[2] or 0 for s in inside)
    area = km2(poly)
    top = sorted(inside, key=lambda s: -(s[2] or 0))[:6]
    ages = Counter(s[4] or "unknown" for s in inside)
    txt = [f"{name}", f"  area          {area*100:,.0f} ha ({area:,.0f} km2)   seed {seed[0]:.3f}E {seed[1]:.3f}N",
           f"  settlements   {len(inside)} clusters, {pop:,} people (GHSL lower bound); age {dict(ages)}",
           "  largest       " + ", ".join(f"{s[5]} {s[2]:,}" for s in top),
           "  boundary      " + "; ".join(parts),
           f"  unattributed  {un:.0f} of {tot:.0f} km ({100*un/tot:.0f}%) follows no nameable feature" + ("  <-- WEAK: redraw or survey" if un/tot > .25 else "")]
    if note: txt.append("  note          " + note)
    props = dict(name=name, area_ha=round(area*100), clusters=len(inside), population_est=pop,
                 boundary="; ".join(parts), unattributed_km=round(un), perimeter_km=round(tot))
    (OUT / f"{name}.geojson").write_text(json.dumps({"type": "Feature", "properties": props, "geometry": mapping(poly)}))
    coords = " ".join(f"{x:.5f},{y:.5f},0" for x, y in poly.exterior.coords)
    (OUT / f"{name}.kml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>{name} ({area*100:,.0f} ha)</name><description>{props['boundary']}</description><Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>""")
    return "\n".join(txt)

def run_seed(con, hcon, z, seed, name, radius, min_ha, min_order, excl_names):
    bb = (seed[0]-radius/111*1.8, seed[1]-radius/111*1.8, seed[0]+radius/111*1.8, seed[1]+radius/111*1.8)
    excl = {k: v for k, v in z.items() if any(e.lower() in k.lower() for e in excl_names)}
    feats, faces, singles = build_mesh(con, hcon, bb, excl, min_order)
    sets = settlements(con, bb)
    poly, inside, missed = grow(faces, sets, seed, radius, min_ha, unary_union(list(excl.values())) if excl else None)
    parts, un, tot = describe_boundary(poly, feats)
    note = f"{len(missed)} target face(s) could not be reached inside {radius*1.6:.0f} km" if missed else ""
    if singles: note += (" · " if note else "") + "isolated hilltops usable as beacons: " + ", ".join(f"{n} {p.x:.3f}E {p.y:.3f}N" for p, n in singles[:5])
    return write(name, poly, inside, parts, un, tot, seed, len(faces), note)

def auto_seeds(con, z, excl_names, rim_km=25, link_km=12, min_pop=500):
    """Seeds = population-weighted centres of settlement clusters on the rims."""
    ex = unary_union([v for k, v in z.items() if any(e.lower() in k.lower() for e in excl_names)])
    exm = transform(EQA, ex); rim = transform(pyproj.Transformer.from_crs("+proj=cea", 4326, always_xy=True).transform,
                                              exm.buffer(rim_km*1000).difference(exm))
    rows = [r for r in settlements(con, rim.bounds) if (r[2] or 0) > 0 and rim.contains(Point(r[1], r[0]))]
    n = len(rows); parent = list(range(n))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i in range(n):
        for j in range(i+1, n):
            if dkm((rows[i][1], rows[i][0]), (rows[j][1], rows[j][0])) <= link_km: parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i in range(n): groups[find(i)].append(rows[i])
    out = []
    for g in groups.values():
        pop = sum(r[2] for r in g)
        if pop < min_pop: continue
        lon = sum(r[1]*r[2] for r in g)/pop; lat = sum(r[0]*r[2] for r in g)/pop
        big = max(g, key=lambda r: r[2])[5]
        spread = max(dkm((lon, lat), (r[1], r[0])) for r in g)
        out.append((pop, (lon, lat), re.sub(r"\W+", "_", big).strip("_"), max(15.0, spread+5)))
    return sorted(out, reverse=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["auto", "seed", "faces"])
    ap.add_argument("lon", nargs="?", type=float); ap.add_argument("lat", nargs="?", type=float)
    ap.add_argument("--name"); ap.add_argument("--radius-km", type=float, default=25)
    ap.add_argument("--min-ha", type=float, default=2000); ap.add_argument("--min-order", type=int, default=4)
    ap.add_argument("--exclude", default="Pongo-Wau-Numatinna,Southern NP", help="zone names (substring) that are hard edges")
    ap.add_argument("--bbox"); ap.add_argument("--top", type=int, default=8)
    a = ap.parse_args()
    con, hcon = sqlite3.connect(DB), sqlite3.connect(HDB)
    z = zones(); excl_names = [e.strip() for e in a.exclude.split(",")]
    if a.mode == "faces":
        bb = tuple(map(float, a.bbox.split(",")))
        excl = {k: v for k, v in z.items() if any(e.lower() in k.lower() for e in excl_names)}
        feats, faces, singles = build_mesh(con, hcon, bb, excl, a.min_order)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "faces_debug.geojson").write_text(json.dumps({"type": "FeatureCollection", "features":
            [{"type": "Feature", "properties": {"kind": "face", "km2": round(km2(f))}, "geometry": mapping(f)} for f in faces] +
            [{"type": "Feature", "properties": {"kind": k, "name": n}, "geometry": mapping(g)} for g, n, k in feats]}))
        print(f"{len(faces)} faces from {len(feats)} features; {sum(1 for f in feats if f[2]=='ridge')} ridges, {len(singles)} lone hilltops -> {OUT/'faces_debug.geojson'}")
        for g, n, k in feats: print(f"  {k:6s} {n}")
        return
    blocks = []
    if a.mode == "seed":
        blocks.append(run_seed(con, hcon, z, (a.lon, a.lat), a.name or f"C_{a.lon:.2f}_{a.lat:.2f}", a.radius_km, a.min_ha, a.min_order, excl_names))
    else:
        for pop, seed, nm, rad in auto_seeds(con, z, excl_names)[:a.top]:
            try: blocks.append(run_seed(con, hcon, z, seed, nm, rad, a.min_ha, a.min_order, excl_names))
            except SystemExit as e: blocks.append(f"{nm}: {e}")
    s = "\n\n".join(blocks); print(s)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "SUMMARY.txt").write_text(s + "\n\nPopulations are GHSL satellite estimates and lower bounds. Boundary names are 1930s sheet or HydroRIVERS names; verify on the ground.\n")

if __name__ == "__main__": main()
