#!/usr/bin/env python3
"""Imagery reading for the planner — the sense the rasters lack.

    python3 -W ignore scripts/plan_imagery.py units   [--zoom 11 --workers 4 --limit N]   one ~40 km landscape chip per chip-square over the AOI (~300 chips)
    python3 -W ignore scripts/plan_imagery.py sites   [--zoom 14]                          team sites + zone centroids → what is visibly there
    python3 -W ignore scripts/plan_imagery.py spot    [--zoom 15 --limit 60]               sporadic high-res checks ONLY where a decision hinges on the ground (team sites, clearing/cluster inside a core, chip/raster conflicts)
    python3 -W ignore scripts/plan_imagery.py calibrate                                     imagery vs GHSL / cropland / OSM: agreement, so a reading has a known error
    python3 -W ignore scripts/plan_imagery.py raster                                        per-cell imagery layers for the solver (cultivation, huts, wetland, forest, tracks)

Source: the satellite basemap the AOI owner configured (tile_sources, proxied by our own server, owner-only, no disk
cache — the tiles are viewed, not archived; only the model's structured READING is stored, in
data/plan_zones/solver/imagery.sqlite3). Model: fireworks/muse-glimmer-30b (vision), many parallel workers, the same
gateway the histmap OCR uses. Every reading is a JSON with fractions that sum to 1 and yes/no/count fields, plus the
model's own confidence; `calibrate` measures it against what we already know (GHSL clusters, cropland 2019, OSM
villages) and prints precision/recall — an unmeasured reading is not evidence (root invariant 12).

Scope (decided 2026-09-06): LANDSCAPE, not settlements. GHSL, the fire tracker and OSM already know the people; the
imagery is read at ~40 km per chip for what no raster of ours holds — toich/wetland extent, gallery forest, plateau
and hills, drainage pattern, burn extent, cultivated mosaic. Token budget: ~1,000 tokens a chip, ~300 chips an AOI.
"""
import argparse, base64, io, json, math, os, sqlite3, sys, time, pickle, re
from collections import Counter, defaultdict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import urllib.request, urllib.parse
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import plan_conservancy_units as P
import __main__; __main__.Grid = P.Grid
from shapely.geometry import shape, Point, box
from shapely.ops import transform

OUT = ROOT / "data/plan_zones" / ("solver" if P.AOI == "XSA_Study_Area" else f"solver_{P.AOI}"); OUT.mkdir(parents=True, exist_ok=True)
DBI = OUT / "imagery.sqlite3"
API = os.environ.get("HISTMAP_API", "http://localhost:8000")
LLM_URL = os.environ.get("HISTMAP_LLM_URL", "https://llm.int.exe.xyz/v1/chat/completions")
MODEL = os.environ.get("HISTMAP_OCR_MODEL", "fireworks/muse-glimmer-30b")
log = P.log

def owner_pwd():
    for l in open(ROOT / "secrets.env"):
        if l.startswith("AOI_OWNER_PWD="): return l.split("=", 1)[1].strip()
    sys.exit("AOI_OWNER_PWD missing from secrets.env")

def source_id(pwd, prefer=("World Imagery", "Google Satellite", "Bing Satellite")):
    d = json.load(urllib.request.urlopen(f"{API}/api/tile-sources?pwd={urllib.parse.quote(pwd)}", timeout=30))
    srcs = d.get("sources", d if isinstance(d, list) else [])
    for p_ in prefer:
        for s in srcs:
            if s.get("name") == p_: return s["id"], s["name"]
    if srcs: return srcs[0]["id"], srcs[0]["name"]
    sys.exit("the AOI owner has no imagery source configured")

def tile_xy(lon, lat, z):
    n = 2 ** z; x = int((lon + 180) / 360 * n); y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n); return x, y

def tile_bounds(x, y, z):
    n = 2 ** z; lon0 = x / n * 360 - 180; lon1 = (x + 1) / n * 360 - 180
    lat0 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n)))); lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon0, lat0, lon1, lat1

def fetch_tile(sid, pwd, z, x, y):
    u = f"{API}/api/tile-sources/{sid}/tile/{z}/{x}/{y}?pwd={urllib.parse.quote(pwd)}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(u, timeout=60) as r:
                b = r.read()
                if r.headers.get("Content-Type", "").startswith("image/") and len(b) > 500: return b
        except Exception: time.sleep(1 + attempt)
    return None

def mosaic(sid, pwd, lon, lat, z, n=2):
    """n×n tiles centred on (lon, lat) → PNG bytes + geographic bounds. n=2 at z13 ≈ 9.8 km square at the equator."""
    from PIL import Image
    x, y = tile_xy(lon, lat, z); x0, y0 = x - n // 2 + (1 if n % 2 == 0 and (lon + 180) / 360 * 2 ** z - x > 0.5 else 0), y - n // 2 + (1 if n % 2 == 0 else 0)
    x0 = x - (n - 1) // 2; y0 = y - (n - 1) // 2
    im = Image.new("RGB", (256 * n, 256 * n)); ok = 0
    for i in range(n):
        for j in range(n):
            b = fetch_tile(sid, pwd, z, x0 + i, y0 + j)
            if b: im.paste(Image.open(io.BytesIO(b)).convert("RGB"), (256 * i, 256 * j)); ok += 1
    if ok < n * n: return None, None
    b0 = tile_bounds(x0, y0, z); b1 = tile_bounds(x0 + n - 1, y0 + n - 1, z)
    buf = io.BytesIO(); im.save(buf, "PNG"); return buf.getvalue(), (b0[0], b1[1], b1[2], b0[3])

SYS = """You are a physical geographer reading one true-colour satellite chip of savanna country on the South Sudan – Central African Republic – DR Congo border. The chip is about {km:.0f} km across, so you see LANDSCAPE, not buildings: wooded grassland, gallery forest as dark strips along drainage, seasonal grass-swamps (toich) as pale smooth flats along rivers, ironstone plateau as uniform reddish-brown ground, inselbergs/hills as shadowed rock, dry-season burn scars as dark irregular patches, cultivation as a fine patchwork of light rectangles around roads. Do not count houses — settlements are known from other data; describe the land.
Answer ONLY a JSON object:
 cover: {{"closed_forest":f, "wooded_savanna":f, "open_grassland":f, "toich_or_wetland":f, "cultivated_mosaic":f, "bare_rock_or_plateau":f, "water":f, "burnt":f}}  fractions of the chip, summing to 1.0
 drainage: "none" | "few_lines" | "dense_dendritic" | "braided_or_swampy"
 gallery_forest: "none" | "thin" | "broad"
 relief: "flat" | "gently_rolling" | "plateau_edges" | "hills"
 wetland_extent: "none" | "along_main_river" | "widespread_floodplain"
 human_footprint: "none_visible" | "tracks_only" | "scattered_fields" | "farmed_landscape"
 burn: "none" | "patchy" | "extensive"
 cloud_or_haze: 0-1 fraction obscured
 notable: one short sentence a planner should know about this ground (e.g. 'broad toich along the river through the middle; forest only on the east edge')
 confidence: 0-1
No prose outside the JSON."""

def read_chip(png, km, max_tokens=2500):
    # muse-glimmer thinks in `reasoning_content` before it answers; give it room and read the JSON from either field
    body = json.dumps({"model": MODEL, "max_tokens": max_tokens, "messages": [
        {"role": "system", "content": SYS.format(km=km)},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
                                     {"type": "text", "text": "Read this chip. Think briefly, then output the JSON object."}]}]}).encode()
    for attempt in range(3):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(LLM_URL, data=body, headers={"Content-Type": "application/json"}), timeout=300))
            msg = r["choices"][0]["message"]; txt = (msg.get("content") or "") + "\n" + (msg.get("reasoning_content") or "")
            m = max(re.finditer(r"\{(?:[^{}]|\{[^{}]*\})*\}", txt, re.S), key=lambda m_: len(m_.group(0)), default=None)
            if m:
                d = json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
                cov = d.get("cover") or {}; s = sum(float(v) for v in cov.values()) or 1.0
                d["cover"] = {k: round(float(cov.get(k, 0)) / s, 3) for k in ("closed_forest", "wooded_savanna", "open_grassland", "toich_or_wetland", "cultivated_mosaic", "bare_rock_or_plateau", "water", "burnt")}
                return d
        except Exception as e: err = str(e)[:120]; time.sleep(2 + 2 * attempt)
    return {"error": err if 'err' in dir() else "no JSON"}

def db():
    c = sqlite3.connect(DBI); c.execute("""CREATE TABLE IF NOT EXISTS readings (kind TEXT, key TEXT, z INT, lon REAL, lat REAL, km REAL, bounds TEXT, source TEXT, model TEXT, read_at TEXT, json TEXT, PRIMARY KEY (kind, key, z))"""); return c

def units_mode(a):
    """LANDSCAPE sampling: one 2×2-tile mosaic at zoom `a.zoom` (default 11 → ~40 km chip) per chip-sized square over the
    AOI, so the whole area is read once with no overlap — ~300 chips for XSA, ~1,000 tokens each. A chip is then
    joined to every fine unit it covers (area-weighted) in `unit_table`. Buildings are not the target: GHSL and the
    fire tracker know the people; the chip reads what no raster of ours holds — toich, gallery forest, plateau,
    drainage pattern, burn extent."""
    st = pickle.load(open(P.OUT / "state.pkl", "rb")); G = st["G"]
    pwd = owner_pwd(); sid, sname = source_id(pwd); log(f"imagery source: {sname} ({sid}); zoom {a.zoom}")
    con = db(); have = {k for (k,) in con.execute("SELECT key FROM readings WHERE kind='chip' AND z=?", (a.zoom,))}
    km = 2 * 40075 / 2 ** a.zoom * math.cos(math.radians(G.aoi.centroid.y)); step = km * 1000
    from shapely.prepared import prep
    aoi_m = prep(transform(P.FWD, G.aoi)); jobs = []
    x = G.x0 + step / 2
    while x < G.x0 + G.w * G.res:
        y = G.y1 - step / 2
        while y > G.y1 - G.h * G.res:
            if aoi_m.intersects(Point(x, y).buffer(step / 2)):
                lon, lat = P.INV(x, y); key = f"{lon:.3f},{lat:.3f}"
                if key not in have: jobs.append((key, lon, lat))
            y -= step
        x += step
    if a.limit: jobs = jobs[:a.limit]
    log(f"{len(jobs)} landscape chips of ~{km:.0f} km to read ({len(have)} already done)")
    def one(job):
        key, lon, lat = job; png, b = mosaic(sid, pwd, lon, lat, a.zoom)
        return key, lon, lat, b, (read_chip(png, km) if png else {"error": "tile fetch failed"})
    n_ok = 0; t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, (key, lon, lat, b, d) in enumerate(ex.map(one, jobs), 1):
            con.execute("INSERT OR REPLACE INTO readings VALUES ('chip',?,?,?,?,?,?,?,?,datetime('now'),?)", (key, a.zoom, lon, lat, km, json.dumps(b), sname, MODEL, json.dumps(d, ensure_ascii=False)))
            n_ok += "error" not in d
            if i % 25 == 0: con.commit(); log(f"  {i}/{len(jobs)} read, {n_ok} ok, {(time.time()-t0)/i:.1f} s/chip")
    con.commit(); log(f"done: {n_ok}/{len(jobs)} readings ok")

def sites_mode(a):
    """Team sites and solved-zone centroids at high zoom (z15 ≈ 1.2 km chip): is the village there, is there water, are
    there fields — the question a planner asks before sending anyone."""
    pwd = owner_pwd(); sid, sname = source_id(pwd); con = db()
    pts = []
    tp = P.OUT / "teams.geojson"
    if tp.exists():
        for f in json.load(open(tp))["features"]:
            p = f["properties"]; pts.append((f"team:{p['kind']}:{p['place']}", *f["geometry"]["coordinates"][:2]))
    zp = OUT / "zones.geojson"
    if zp.exists():
        for f in json.load(open(zp))["features"]:
            c = shape(f["geometry"]).representative_point(); pts.append((f"zone:{f['properties']['uid']}", c.x, c.y))
    km = 2 * 40075 / 2 ** a.zoom * math.cos(math.radians(7.5))
    def one(t):
        key, lon, lat = t; png, b = mosaic(sid, pwd, lon, lat, a.zoom)
        return key, lon, lat, b, (read_chip(png, km) if png else {"error": "tile fetch failed"})
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for key, lon, lat, b, d in ex.map(one, pts):
            con.execute("INSERT OR REPLACE INTO readings VALUES ('site',?,?,?,?,?,?,?,?,datetime('now'),?)", (key, a.zoom, lon, lat, km, json.dumps(b), sname, MODEL, json.dumps(d, ensure_ascii=False)))
    con.commit(); log(f"sites: {len(pts)} read")
    L = ["SITES — what the imagery shows at each team site / zone centre (chip ≈ %.1f km)" % km, ""]
    for key, lon, lat, js in con.execute("SELECT key, lon, lat, json FROM readings WHERE kind='site' ORDER BY key"):
        d = json.loads(js)
        if "error" in d: L.append(f"{key}: {d['error']}"); continue
        L.append(f"{key} ({lon:.3f},{lat:.3f}): compounds {d.get('compounds_visible')}, fields {d.get('field_freshness')}, tracks {d.get('tracks_visible')}, water {d.get('water_visible')}, cover {d.get('cover')}; {d.get('notable')} (conf {d.get('confidence')})")
    (OUT / "IMAGERY_SITES.txt").write_text("\n".join(L) + "\n")

SPOT_SYS = """You are checking one high-resolution satellite chip (about {km:.1f} km across) the way a field planner does in Google Earth before deciding about this exact spot, in savanna country on the South Sudan – CAR – DRC border. Look for: round-hut compounds and their cleared yards, fields (fresh light rectangles or old fallow patchwork), cattle camps (bare trampled circles, often with a thorn fence), tracks and roads, open water or a river bed with water, wet green swamp grass (toich), gallery forest, rock/hills, fresh tree clearing (raw bare ground with felled stems), mining pits (pale spoil heaps, turbid pools).
Answer ONLY a JSON object:
 compounds: integer count of homestead compounds visible (0 if none)
 fields: "none" | "old_fallow" | "active" | "fresh_clearing"
 cattle_camp: true/false
 tracks: "none" | "footpaths" | "cattle_tracks" | "vehicle_track" | "road"
 water: "none" | "dry_bed" | "river_with_water" | "pools" | "swamp"
 forest: "none" | "gallery_strip" | "closed_forest"
 mining_signs: true/false
 rock_or_hill: true/false
 verdict: one sentence answering the question asked
 confidence: 0-1
No prose outside the JSON."""

def spot_mode(a):
    """SPORADIC HIGH-RESOLUTION CHECKS, like a human dropping into Google Earth — only where a decision hinges on it:
      * every team site (TEAMS: is there a village and water where the planner put people?)
      * core zones with a reviewed clearing event since 2020 or a GHSL cluster inside (is the core really empty there?)
      * chips reading 'farmed_landscape' where GHSL counts < 50 people in the fine unit (who is farming?)
      * corridor band cells flagged WATER UNVERIFIED in TEAMS.txt
    Capped at --limit (default 60) chips per run, questions attached, answers written to IMAGERY_SPOT.txt and the
    readings table (kind='spot'). Not a survey: a spot check whose result changes one decision."""
    st = pickle.load(open(P.OUT / "state.pkl", "rb")); G, labf = st["G"], st["labf"]; con = db()
    pwd = owner_pwd(); sid, sname = source_id(pwd); z = a.zoom if a.zoom else 15; km = 2 * 40075 / 2 ** z * math.cos(math.radians(G.aoi.centroid.y))
    C = P.cells(sqlite3.connect(str(P.DB)), G); Q = []
    tp = P.OUT / "teams.geojson"
    if tp.exists():
        for f in json.load(open(tp))["features"]:
            p = f["properties"]; lon, lat = f["geometry"]["coordinates"][:2]
            Q.append((f"team:{p['kind']}:{p['place']}", lon, lat, f"Is there a village with people and any water (river, pools, swamp) here, where a {p['kind']} team of {p['team_size']} is proposed? Planner says water: {str(p.get('water'))[:80]}"))
    zp = OUT / "zones.geojson"
    if zp.exists():
        clear = np.nan_to_num(C["clear20"]) > 0; clus = np.nan_to_num(C["clusters"]) > 0
        for f in json.load(open(zp))["features"]:
            p = f["properties"]
            if p.get("solver_class") != "core": continue
            m = G.rasterize([(transform(P.FWD, shape(f["geometry"])), 1)]).astype(bool)
            for flag, why in ((clear & m, "a reviewed clearing event since 2020 inside a proposed core"), (clus & m, "a GHSL settlement cluster inside a proposed core")):
                rr, cc = np.nonzero(flag)
                for i in np.linspace(0, len(rr) - 1, min(3, len(rr))).astype(int) if len(rr) else []:
                    lon, lat = P.INV(G.x0 + (cc[i] + 0.5) * G.res, G.y1 - (rr[i] + 0.5) * G.res)
                    Q.append((f"core:{p['uid']}:{lon:.3f},{lat:.3f}", lon, lat, f"This is {why} ({p.get('seed')}). Is there active use here — compounds, fields, fresh clearing, a cattle camp — or is it empty bush?"))
    T = unit_table(con, 11, st) if con.execute("SELECT 1 FROM readings WHERE kind='chip' LIMIT 1").fetchone() else {}
    ML = labf.max() + 1; pop_u = np.bincount(labf.ravel(), weights=np.nan_to_num(C["pop"]).ravel(), minlength=ML)
    for u, t in T.items():
        if t.get("img_human_footprint") == "farmed_landscape" and pop_u[u] < 50:
            rr, cc = np.nonzero(labf == u); i = len(rr) // 2; lon, lat = P.INV(G.x0 + (cc[i] + 0.5) * G.res, G.y1 - (rr[i] + 0.5) * G.res)
            Q.append((f"farmed_but_empty:{u}", lon, lat, "The 40 km chip read this as a farmed landscape but GHSL counts almost nobody in the unit. Are there fields and compounds here?"))
    have = {k for (k,) in con.execute("SELECT key FROM readings WHERE kind='spot'")}; Q = [q for q in Q if q[0] not in have][: a.limit or 60]
    log(f"spot: {len(Q)} high-resolution checks at zoom {z} (~{km:.1f} km chips)")
    def one(q):
        key, lon, lat, question = q; png, b = mosaic(sid, pwd, lon, lat, z)
        if png is None: return key, lon, lat, None, question, {"error": "tile fetch failed"}
        body = json.dumps({"model": MODEL, "max_tokens": 1800, "messages": [{"role": "system", "content": SPOT_SYS.format(km=km)},
                 {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}, {"type": "text", "text": "QUESTION: " + question + "\nThink briefly, then output the JSON object."}]}]}).encode()
        d = {"error": "no JSON"}
        for attempt in range(3):
            try:
                r = json.load(urllib.request.urlopen(urllib.request.Request(LLM_URL, data=body, headers={"Content-Type": "application/json"}), timeout=300)); msg = r["choices"][0]["message"]
                txt = (msg.get("content") or "") + "\n" + (msg.get("reasoning_content") or ""); m = max(re.finditer(r"\{(?:[^{}]|\{[^{}]*\})*\}", txt, re.S), key=lambda m_: len(m_.group(0)), default=None)
                if m: d = json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0))); break
            except Exception as e: d = {"error": str(e)[:100]}; time.sleep(2)
        d["question"] = question; return key, lon, lat, b, question, d
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for key, lon, lat, b, question, d in ex.map(one, Q):
            con.execute("INSERT OR REPLACE INTO readings VALUES ('spot',?,?,?,?,?,?,?,?,datetime('now'),?)", (key, z, lon, lat, km, json.dumps(b), sname, MODEL, json.dumps(d, ensure_ascii=False)))
    con.commit()
    L = [f"SPOT CHECKS — high-resolution (~{km:.1f} km) looks where a decision hinges on the ground, {sname}, zoom {z}", ""]
    for key, lon, lat, js in con.execute("SELECT key, lon, lat, json FROM readings WHERE kind='spot' ORDER BY key"):
        d = json.loads(js)
        if "error" in d: L.append(f"{key}: {d['error']}"); continue
        L.append(f"{key} ({lon:.3f},{lat:.3f})\n    Q: {d.get('question')}\n    A: {d.get('verdict')} — compounds {d.get('compounds')}, fields {d.get('fields')}, cattle camp {d.get('cattle_camp')}, tracks {d.get('tracks')}, water {d.get('water')}, forest {d.get('forest')}, mining {d.get('mining_signs')} (conf {d.get('confidence')})")
    (OUT / "IMAGERY_SPOT.txt").write_text("\n".join(L) + "\n"); print("\n".join(L[:14]))

def unit_table(con, z, st=None):
    """per fine unit: chip readings weighted by the share of the unit each chip covers"""
    st = st or pickle.load(open(P.OUT / "state.pkl", "rb")); G, labf = st["G"], st["labf"]; ML = labf.max() + 1
    acc = defaultdict(lambda: defaultdict(float)); wsum = defaultdict(float); cats = defaultdict(lambda: defaultdict(Counter)); notes = defaultdict(list)
    for key, bnd, js in con.execute("SELECT key, bounds, json FROM readings WHERE kind='chip' AND z=?", (z,)):
        d = json.loads(js)
        if "error" in d or not d.get("cover") or not bnd: continue
        lon0, lat0, lon1, lat1 = json.loads(bnd); m = G.rasterize([(transform(P.FWD, box(lon0, lat0, lon1, lat1)), 1)]).astype(bool)
        us, n = np.unique(labf[m & (labf > 0)], return_counts=True)
        for u, k in zip(us, n):
            w = float(k) * max(float(d.get("confidence") or 0.5), 0.05); wsum[u] += w
            for c, v in d["cover"].items(): acc[u][c] += w * float(v)
            for c in ("drainage", "gallery_forest", "relief", "wetland_extent", "human_footprint", "burn"): cats[u][c][d.get(c) or "unknown"] += w
            if d.get("notable"): notes[u].append(str(d["notable"])[:140])
    T = {}
    for u in wsum:
        T[u] = dict(chips=len(notes[u]), **{f"img_{c}": round(v / wsum[u], 3) for c, v in acc[u].items()},
                    **{f"img_{c}": cats[u][c].most_common(1)[0][0] for c in cats[u]}, img_notes=" | ".join(notes[u][:3]))
    return T

def calibrate(a):
    """The reading against what we already hold, per fine unit: cultivated_mosaic vs cropland 2019 (GLAD), water +
    toich vs JRC surface-water share, human_footprint vs GHSL people, burnt vs 2024-25 fire density. Spearman rank
    correlations — the reading is a landscape description, so agreement in ORDER is what matters. Printed so a
    reader knows what the imagery term in the solver is worth (invariant 12)."""
    st = pickle.load(open(P.OUT / "state.pkl", "rb")); G, labf = st["G"], st["labf"]; con = db(); T = unit_table(con, a.zoom, st)
    C = P.cells(sqlite3.connect(str(P.DB)), G); ML = labf.max() + 1
    S = {k: np.bincount(labf.ravel(), weights=np.nan_to_num(C[k]).ravel(), minlength=ML) for k in ("pop", "crop19", "cropn", "fire")}
    cnt = np.bincount(labf.ravel(), minlength=ML)
    water = np.zeros((G.h, G.w), bool); dbc = sqlite3.connect(str(P.DB))
    for gj, in dbc.execute("SELECT geojson FROM park_waterbodies WHERE park_id=?", (P.AOI,)): water |= G.rasterize([(transform(P.FWD, shape(json.loads(gj))), 1)], all_touched=True).astype(bool)
    for gj, in dbc.execute("SELECT geojson FROM park_lakes_hydro WHERE park_id=?", (P.AOI,)): water |= G.rasterize([(transform(P.FWD, shape(json.loads(gj))), 1)], all_touched=True).astype(bool)
    wsh = np.bincount(labf.ravel(), weights=water.ravel().astype(float), minlength=ML) / np.maximum(cnt, 1)
    us = sorted(T); from scipy.stats import spearmanr
    def rho(x, y): return round(float(spearmanr(x, y).correlation), 3) if len(us) > 8 else None
    FP = {"none_visible": 0, "tracks_only": 1, "scattered_fields": 2, "farmed_landscape": 3, "unknown": 1}; BU = {"none": 0, "patchy": 1, "extensive": 2, "unknown": 1}
    res = dict(units_with_reading=len(us), chips=int(con.execute("SELECT count(*) FROM readings WHERE kind='chip' AND json NOT LIKE '%error%'").fetchone()[0]),
               rho_cultivated_vs_cropland2019=rho([T[u]["img_cultivated_mosaic"] for u in us], [S["crop19"][u] / max(S["cropn"][u], 1) for u in us]),
               rho_water_toich_vs_jrc_share=rho([T[u]["img_toich_or_wetland"] + T[u]["img_water"] for u in us], [wsh[u] for u in us]),
               rho_footprint_vs_ghsl_people_km2=rho([FP.get(T[u]["img_human_footprint"], 1) for u in us], [S["pop"][u] / max(cnt[u], 1) for u in us]),
               rho_burnt_vs_fire_density=rho([BU.get(T[u]["img_burn"], 1) + T[u]["img_burnt"] for u in us], [S["fire"][u] / max(cnt[u], 1) for u in us]))
    json.dump(res, open(OUT / "imagery_calibration.json", "w"), indent=1)
    L = [f"IMAGERY CALIBRATION — {res['chips']} landscape chips (zoom {a.zoom}) → {res['units_with_reading']} fine units; Spearman rank agreement with what we hold:",
         f"  cultivated mosaic  vs GLAD cropland 2019      ρ = {res['rho_cultivated_vs_cropland2019']}", f"  toich + water      vs JRC/HydroLAKES share    ρ = {res['rho_water_toich_vs_jrc_share']}",
         f"  human footprint    vs GHSL people/km²          ρ = {res['rho_footprint_vs_ghsl_people_km2']}", f"  burnt              vs VIIRS fire density 24-25 ρ = {res['rho_burnt_vs_fire_density']}",
         "  Reading: ρ ≥ 0.5 = the model sees what the raster measures; the imagery-only fields (gallery forest, relief, drainage, wetland extent) have no raster to check against and enter the solver at the weight the checkable ones earn."]
    (OUT / "IMAGERY_CALIBRATION.txt").write_text("\n".join(L) + "\n"); print("\n".join(L))

def raster_mode(a):
    """Per-fine-unit landscape attributes → per-cell rasters the solver reads (imagery_*.npy) + a JSON table."""
    st = pickle.load(open(P.OUT / "state.pkl", "rb")); labf = st["labf"]; con = db(); T = unit_table(con, a.zoom, st); ML = labf.max() + 1
    for k in ("closed_forest", "wooded_savanna", "open_grassland", "toich_or_wetland", "cultivated_mosaic", "bare_rock_or_plateau", "water", "burnt"):
        v = np.full(ML, np.nan)
        for u, t in T.items(): v[u] = t.get(f"img_{k}", np.nan)
        np.save(OUT / f"imagery_{k}.npy", v[labf])
    GF = {"none": 0, "thin": 0.5, "broad": 1}; WE = {"none": 0, "along_main_river": 0.5, "widespread_floodplain": 1}
    for k, M in (("gallery_forest", GF), ("wetland_extent", WE)):
        v = np.full(ML, np.nan)
        for u, t in T.items(): v[u] = M.get(t.get(f"img_{k}"), np.nan)
        np.save(OUT / f"imagery_{k}.npy", v[labf])
    json.dump({int(u): t for u, t in T.items()}, open(OUT / "imagery_units.json", "w"), ensure_ascii=False)
    log(f"imagery rasters written for {len(T)} units")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["units", "sites", "spot", "calibrate", "raster"]); ap.add_argument("--zoom", type=int, default=None); ap.add_argument("--workers", type=int, default=4); ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if a.zoom is None: a.zoom = 14 if a.mode == "sites" else 11
    {"units": units_mode, "sites": sites_mode, "spot": spot_mode, "calibrate": calibrate, "raster": raster_mode}[a.mode](a)

if __name__ == "__main__": main()
