#!/usr/bin/env python3
"""Per-zone statistics for the proposed Pongo-Wau-Numatinna complex.

Takes the KML zone files the plan's authors drew (proposed park, wilderness,
Southern NP, pastoral corridor, the two ecological-corridor markers) and
reports, for each, what the XSA study-area database already holds:

  settlements   park_settlements clusters inside the zone: counts by
                classification and persistence, GHSL population, measured
                built surface 2000/2015/today, cropland fraction around them
  deforestation deforestation_events inside: events, km2 by year, the
                needs_review split, and cropland conversion of cleared pixels
  cropland      GLAD 30 m cropland extent (2003, 2019) zonal over the WHOLE
                zone - the landscape number, not the settlement-ring one
  fire          detections from fire_grid_month (0.1 deg cells whose centre
                falls in the zone) by month, and v5 fire trajectories: which
                fronts touch the zone, their type, and an ORIGIN -> DESTINATION
                matrix over the zones themselves (a front's first and last
                trajectory point, labelled by the zone it falls in, else by
                compass sector outside the zone set).

Everything is measured, nothing is assumed. A zone with no coverage in a
source prints "unmeasured", never 0 (root invariant 1).

    python3 scripts/plan_zone_stats.py                     # all KMLs in the default dir
    python3 scripts/plan_zone_stats.py --kml-dir DIR --json out.json
"""
import argparse
import json
import math
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pyproj
from shapely.geometry import Polygon, Point, shape
from shapely.ops import transform, unary_union
from shapely.prepared import prep
from shapely import vectorized

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db.sqlite3"
AOI = "XSA_Study_Area"
GROUPS = ROOT / "data/fire_groups_v5" / f"{AOI}.json"
CLIPS = ROOT / "data/cropland/clips"
EQA = pyproj.Transformer.from_crs(4326, "+proj=cea", always_xy=True).transform

# Point-only KMLs are markers, not areas: they get a buffer and say so.
MARKER_BUFFER_KM = 15.0


def km2(geom):
    return transform(EQA, geom).area / 1e6


def read_kml(path):
    """Yield (zone_name, geometry, kind, label_km2) per Placemark.

    kind is 'polygon' or 'marker' (a Placemark holding only a Point gets a
    MARKER_BUFFER_KM disc and says so). label_km2 is the area the AUTHOR wrote
    into the name, parsed if present - so the file's own claim and our
    measurement can be compared instead of one silently replacing the other.
    """
    txt = path.read_text(encoding="utf-8", errors="replace")
    fwd = pyproj.Transformer.from_crs(4326, "+proj=cea", always_xy=True).transform
    inv = pyproj.Transformer.from_crs("+proj=cea", 4326, always_xy=True).transform
    out = []
    for pm in re.findall(r"<Placemark>(.*?)</Placemark>", txt, re.S):
        nm = re.search(r"<name>(.*?)</name>", pm, re.S)
        nm = (nm.group(1) if nm else path.stem).replace("&apos;", "'").strip()
        polys, pts = [], []
        for m in re.finditer(r"<coordinates>(.*?)</coordinates>", pm, re.S):
            coords = [tuple(map(float, c.split(",")[:2])) for c in m.group(1).split()]
            if len(coords) >= 4:
                polys.append(Polygon(coords).buffer(0))
            elif len(coords) == 1:
                pts.append(Point(coords[0]))
        if polys:
            geom, kind = unary_union(polys), "polygon"
        elif pts:
            geom = transform(inv, transform(fwd, unary_union(pts)).buffer(MARKER_BUFFER_KM * 1000))
            kind = "marker"
        else:
            continue
        lab = None
        m = re.search(r"([\d',\.]+)\s*(ha|km2|sqkm)", nm, re.I)
        if m:
            v = float(m.group(1).replace(",", "").replace("'", "").rstrip("."))
            lab = round(v / 100.0, 0) if m.group(2).lower() == "ha" else round(v, 0)
        out.append((nm, geom, kind, lab))
    if not out:
        raise ValueError(f"no geometry in {path}")
    return out


def octant(dy, dx):
    b = math.degrees(math.atan2(dx, dy)) % 360
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int(((b + 22.5) % 360) // 45)]


RIM_KM = 25.0          # rim = ring this wide OUTSIDE the zone boundary


def rim_of(geom):
    fwd = pyproj.Transformer.from_crs(4326, "+proj=cea", always_xy=True).transform
    inv = pyproj.Transformer.from_crs("+proj=cea", 4326, always_xy=True).transform
    m = transform(fwd, geom)
    return transform(inv, m.buffer(RIM_KM * 1000).difference(m))


# ---------------------------------------------------------------- settlements
def settlements(con, zones):
    rows = con.execute(
        """SELECT lat, lon, population_est, area_m2, extent_m2, classification,
                  persistence, surface_e2000_m2, surface_e2015_m2,
                  cropland_frac_2019, cropland_frac_2003, nearest_place,
                  population_source, area_source
           FROM park_settlements WHERE park_id = ?""", (AOI,)).fetchall()
    lat = np.array([r[0] for r in rows])
    lon = np.array([r[1] for r in rows])
    out = {}
    for name, z in zones.items():
        mask = vectorized.contains(z["geom"], lon, lat)
        sel = [r for r, m in zip(rows, mask) if m]
        cls = Counter(r[5] or "unclassified" for r in sel)
        per = Counter(r[6] or "unknown" for r in sel)
        pop = [r[2] for r in sel if r[2]]
        crop = [r[9] for r in sel if r[9] is not None]
        crop03 = [r[10] for r in sel if r[10] is not None]
        big = sorted(sel, key=lambda r: -(r[2] or 0))[:6]
        out[name] = dict(
            clusters=len(sel),
            population_est=int(sum(pop)),
            population_measured_rows=len(pop),
            built_surface_km2=round(sum((r[3] or 0) for r in sel) / 1e6, 2),
            built_surface_2000_km2=round(sum((r[7] or 0) for r in sel) / 1e6, 2),
            built_surface_2015_km2=round(sum((r[8] or 0) for r in sel) / 1e6, 2),
            by_classification=dict(cls.most_common()),
            by_persistence=dict(per.most_common()),
            cropland_frac_2019_mean=(round(float(np.mean(crop)), 4) if crop else None),
            cropland_frac_2003_mean=(round(float(np.mean(crop03)), 4) if crop03 else None),
            cropland_measured_rows=len(crop),
            largest=[dict(place=r[11], lat=round(r[0], 4), lon=round(r[1], 4),
                          pop=r[2], classification=r[5],
                          cropland_frac_2019=r[9]) for r in big],
        )
    return out


# ------------------------------------------------------------- deforestation
def deforestation(con, zones):
    rows = con.execute(
        """SELECT lat, lon, year, area_km2, classification, needs_review,
                  cropland_conversion_frac, cropland_event_frac_2019, fires_same_year
           FROM deforestation_events WHERE park_id = ?""", (AOI,)).fetchall()
    lat = np.array([r[0] for r in rows])
    lon = np.array([r[1] for r in rows])
    out = {}
    for name, z in zones.items():
        mask = vectorized.contains(z["geom"], lon, lat)
        sel = [r for r, m in zip(rows, mask) if m]
        ok = [r for r in sel if not r[5]]
        by_year = defaultdict(float)
        for r in ok:
            by_year[r[2]] += r[3] or 0
        conv = [(r[3] or 0) * r[6] for r in ok if r[6] is not None]
        conv_meas = [r for r in ok if r[6] is not None]
        out[name] = dict(
            events=len(sel),
            events_verified=len(ok),
            events_needs_review=len(sel) - len(ok),
            km2_verified=round(sum(r[3] or 0 for r in ok), 2),
            km2_needs_review=round(sum(r[3] or 0 for r in sel if r[5]), 2),
            by_year_km2={y: round(v, 2) for y, v in sorted(by_year.items())},
            by_classification=dict(Counter(r[4] or "unclassified" for r in ok).most_common()),
            cropland_conversion_km2=(round(sum(conv), 2) if conv else None),
            cropland_conversion_measured_events=len(conv_meas),
            with_fire_same_year=sum(1 for r in ok if (r[8] or 0) > 0),
        )
    return out


# ------------------------------------------------------------------ cropland
def cropland_zonal(zones):
    """Zone-wide GLAD cropland fraction from the XSA clips (2003, 2019)."""
    try:
        import rasterio
        from rasterio.features import geometry_mask
    except ImportError:
        return {n: dict(note="unmeasured: rasterio missing") for n in zones}
    out = {n: {} for n in zones}
    for epoch in (2003, 2019):
        p = CLIPS / f"{AOI}_{epoch}.tif"
        if not p.exists():
            for n in zones:
                out[n][f"frac_{epoch}"] = None
                out[n][f"source_{epoch}"] = "unmeasured: clip missing"
            continue
        with rasterio.open(p) as src:
            for name, z in zones.items():
                g = z["geom"]
                win = src.window(*g.bounds)
                # A zone can be the whole study area (27,600 x 34,800 px);
                # decimate so a baseline read stays a read, not a gigabyte.
                h, wdt = int(win.height), int(win.width)
                dec = max(1, int(max(h, wdt) / 8000) + (1 if max(h, wdt) > 8000 else 0))
                shp = (max(h // dec, 1), max(wdt // dec, 1))
                arr = src.read(1, window=win, out_shape=shp)
                if arr.size == 0:
                    out[name][f"frac_{epoch}"] = None
                    out[name][f"source_{epoch}"] = "unmeasured: zone outside clip"
                    continue
                tr = src.window_transform(win) * rasterio.Affine.scale(
                    wdt / shp[1], h / shp[0])
                m = geometry_mask([g], out_shape=arr.shape, transform=tr, invert=True)
                vals = arr[m]
                frac = float((vals > 0).mean())
                out[name][f"frac_{epoch}"] = round(frac, 6)
                out[name][f"km2_{epoch}"] = round(frac * km2(g), 2)
                out[name][f"source_{epoch}"] = "glad_cropland_30m"
                out[name][f"px_{epoch}"] = int(vals.size)
                out[name][f"cropland_px_{epoch}"] = int((vals > 0).sum())
                out[name][f"decimation_{epoch}"] = dec
    return out


# ---------------------------------------------------------------------- fire
def fire_grid(con, zones, since="2018-04-01"):
    """Detections per zone from the 0.1 deg monthly grid.

    THE FLEET TRIPLES ON 2024-01-01 (Suomi-NPP alone before, +NOAA-20/21
    after), so counts either side of that date are not comparable and the
    per-area rate is quoted for FULL YEARS 2024-2025 ONLY - one fleet, two
    complete seasons. The earlier years ship as a separate block, labelled.
    """
    rows = con.execute(
        "SELECT d, xi, yi, n, frp FROM fire_grid_month WHERE d >= ?", (since,)).fetchall()
    xi = np.array([r[1] for r in rows]) * 0.1
    yi = np.array([r[2] for r in rows]) * 0.1
    out = {}
    for name, z in zones.items():
        mask = vectorized.contains(z["geom"], xi, yi)
        sel = [r for r, m in zip(rows, mask) if m]
        by_month = defaultdict(int)
        by_cal = defaultdict(int)
        by_year = defaultdict(int)
        for d, _, _, n, _ in sel:
            by_month[d] += n
            by_cal[d[5:7]] += n
            by_year[d[:4]] += n
        cal_mod = defaultdict(int)
        for d, _, _, n, _ in sel:
            if "2024" <= d[:4] <= "2025":
                cal_mod[d[5:7]] += n
        tot_mod = sum(cal_mod.values())
        area = km2(z["geom"])
        out[name] = dict(
            detections_2024_2025=tot_mod,
            detections_per_1000km2_per_year=(round(tot_mod / 2 / area * 1000, 1)
                                             if area else None),
            rate_basis="full years 2024-2025, VIIRS 3-satellite fleet",
            nov_feb_share=(round(sum(cal_mod[m] for m in ("11", "12", "01", "02"))
                                 / tot_mod, 3) if tot_mod else None),
            by_calendar_month_2024_2025={m: cal_mod[m] for m in sorted(cal_mod)},
            by_year_all={y: by_year[y] for y in sorted(by_year)},
            by_year_caption="2018-2023 is Suomi-NPP only; 2024+ adds NOAA-20 "
                            "and NOAA-21 - do not compare across 2024-01-01",
            since=since,
        )
    return out


def fire_trajectories(zones):
    groups = json.load(open(GROUPS))
    names = list(zones)
    preps = {n: prep(zones[n]["geom"]) for n in names}
    cents = {n: zones[n]["geom"].centroid for n in names}
    shared = Counter()      # unordered pair -> fronts touching both zones
    flow = Counter()        # (start zone, end zone) -> fronts

    def label(lon, lat, ref):
        for n in names:
            if preps[n].contains(Point(lon, lat)):
                return n
        c = cents[ref]
        return "outside " + octant(lat - c.y, (lon - c.x) * math.cos(math.radians(lat)))

    res = {n: dict(fronts=0, by_type=Counter(), origins=Counter(), destinations=Counter(),
                   starts_inside=0, ends_inside=0, months=Counter(), dists=[],
                   in_zone_km=[], through_direction=Counter()) for n in names}
    for g in groups:
        t = g.get("trajectory") or []
        if len(t) < 2:
            continue
        lons = np.array([p[0] for p in t])
        lats = np.array([p[1] for p in t])
        touched = []
        for n in names:
            m = vectorized.contains(zones[n]["geom"], lons, lats)
            if not m.any():
                continue
            touched.append(n)
            r = res[n]
            r["fronts"] += 1
            r["by_type"][g.get("group_type") or "unknown"] += 1
            r["months"][g["start_date"][5:7]] += 1
            r["dists"].append(float(g.get("distance_km") or 0))
            first, last = t[0], t[-1]
            r["origins"][label(first[0], first[1], n)] += 1
            r["destinations"][label(last[0], last[1], n)] += 1
            r["starts_inside"] += bool(m[0])
            r["ends_inside"] += bool(m[-1])
            idx = np.flatnonzero(m)
            i0, i1 = max(0, idx[0] - 1), min(len(t) - 1, idx[-1] + 1)
            p1, p2 = t[i0], t[i1]
            d = math.hypot((p2[1] - p1[1]) * 111,
                           (p2[0] - p1[0]) * 111 * math.cos(math.radians(p1[1])))
            r["in_zone_km"].append(d)
            if d >= 3:
                r["through_direction"][octant(p2[1] - p1[1],
                                              (p2[0] - p1[0]) * math.cos(math.radians(p1[1])))] += 1
        for i, n in enumerate(touched):
            for m2 in touched[i + 1:]:
                shared[(n, m2)] += 1
        s0 = next((n for n in names if preps[n].contains(Point(t[0][0], t[0][1]))), None)
        e0 = next((n for n in names if preps[n].contains(Point(t[-1][0], t[-1][1]))), None)
        if s0 and e0 and s0 != e0:
            flow[(s0, e0)] += 1
    out = {}
    for n, r in res.items():
        dz = sorted(r["in_zone_km"])
        out[n] = dict(
            fronts_touching=r["fronts"],
            by_type=dict(r["by_type"].most_common()),
            starts_inside=r["starts_inside"],
            ends_inside=r["ends_inside"],
            origins=dict(r["origins"].most_common(10)),
            destinations=dict(r["destinations"].most_common(10)),
            through_direction=dict(r["through_direction"].most_common()),
            start_months={m: r["months"][m] for m in sorted(r["months"])},
            median_front_length_km=(round(sorted(r["dists"])[len(r["dists"]) // 2], 1)
                                    if r["dists"] else None),
            median_in_zone_km=(round(dz[len(dz) // 2], 1) if dz else None),
        )
    return out, dict(
        shared_fronts={f"{a} ↔ {b}": c for (a, b), c in shared.most_common()},
        zone_to_zone={f"{a} → {b}": c for (a, b), c in flow.most_common(40)},
        note="shared_fronts: one fire front whose path touches both zones. "
             "zone_to_zone: front STARTS in the first zone and ENDS in the "
             "second (start/end of the detected front, not of the herd).",
    )


# ------------------------------------------------------------- named places
def places(con, zones):
    rows = con.execute(
        """SELECT name, lat, lon, place_type FROM osm_places
           WHERE park_id = ?""", (AOI,)).fetchall()
    if not rows:
        return {n: dict(note="unmeasured: no osm_places rows for " + AOI) for n in zones}
    lat = np.array([r[1] for r in rows])
    lon = np.array([r[2] for r in rows])
    out = {}
    for name, z in zones.items():
        mask = vectorized.contains(z["geom"], lon, lat)
        sel = [r for r, m in zip(rows, mask) if m]
        rank = {"city": 0, "town": 1, "village": 2, "hamlet": 3}
        sel.sort(key=lambda r: (rank.get(r[3], 9), r[0]))
        out[name] = dict(
            osm_places=len(sel),
            by_type=dict(Counter(r[3] for r in sel).most_common()),
            names=[f"{r[0]} ({r[3]})" for r in sel[:12]],
        )
    return out


# ------------------------------------------------- 1930s survey-sheet labels
def hist_labels(zones, base, pwd):
    """What the Sudan Survey 1:250,000 sheets call this ground, per zone.

    Read through the running app's own API (/api/histmap/sudan250k/labels), so
    the report and the map agree by construction. The endpoint reports
    `complete`; if the OCR run were still in flight an empty answer would mean
    "not extracted yet", not "the map is silent here" - so that flag is carried
    into the output rather than swallowed (invariant 1).
    """
    import urllib.parse
    import urllib.request

    def fetch(w, s, e, n):
        """Labels in one bbox; splits recursively rather than returning a
        truncated answer (a cut list is indistinguishable from a short one)."""
        qs = urllib.parse.urlencode(dict(bbox=f"{w},{s},{e},{n}", limit=5000, pwd=pwd))
        with urllib.request.urlopen(f"{base}/api/histmap/sudan250k/labels?{qs}",
                                    timeout=180) as r:
            j = json.loads(r.read())
        if not j.get("available"):
            raise RuntimeError("histmap labels unavailable")
        if not j.get("truncated"):
            return j.get("labels", []), bool(j.get("complete"))
        mx, my = (w + e) / 2, (s + n) / 2
        labs, comp = [], True
        for bb in ((w, s, mx, my), (mx, s, e, my), (w, my, mx, n), (mx, my, e, n)):
            l, c = fetch(*bb)
            labs += l
            comp = comp and c
        return labs, comp

    out = {}
    for name, z in zones.items():
        w, s, e, n = z["geom"].bounds
        try:
            raw, complete = fetch(w, s, e, n)
        except Exception as ex:                                   # noqa: BLE001
            out[name] = dict(note=f"unmeasured: {type(ex).__name__} {ex}")
            continue
        seen, labs = set(), []
        for l in raw:
            k = (l["text"], round(l["lon"], 5), round(l["lat"], 5))
            if k not in seen:
                seen.add(k)
                labs.append(l)
        pz = prep(z["geom"])
        labs = [l for l in labs if pz.contains(Point(l["lon"], l["lat"]))]
        keep = [l for l in labs if l.get("category") not in ("junk", "collar", None)]
        by_cat = Counter(l["category"] for l in keep)
        pick = lambda c: [l["text"] for l in keep if l["category"] == c]  # noqa: E731
        out[name] = dict(
            labels=len(keep),
            by_category=dict(by_cat.most_common()),
            places=pick("place")[:20],
            water=sorted(set(pick("water")))[:20],
            terrain=sorted(set(pick("terrain")))[:12],
            route=sorted(set(pick("route")))[:12],
            notes=[t for t in dict.fromkeys(pick("note")) if len(t) > 6][:10],
            mine_notes=[l["text"] for l in labs
                        if re.search(r"mine|copper|gold|working|quarry|iron forge",
                                     l["text"], re.I)][:10],
            ocr_complete=complete,
            caveat="machine transcription of 1908-1976 letterpress; "
                   "spellings are claims, verify against the sheet",
        )
    return out


# ------------------------------------------- the plan's own proposed sites
# Coordinates as printed in section 5 of the EASY assessment (three are
# uncertain, marked there with ~; kept here with the same caveat).
PLAN_SITES = [
    ("Boro-Medina (ECHO, ~)", 8.63, 25.58),
    ("Raga (ECHO)", 8.46, 25.68),
    ("Deim Zubeir (ECHO)", 7.72, 26.20),
    ("Ali Golo (ECHO, ~)", 7.73, 27.47),
    ("Faraj Allah (ECHO, ~)", 7.56, 27.76),
    ("Raffili Mission (ECHO)", 6.87, 27.99),
    ("Nagero (ECHO)", 6.39, 27.77),
    ("M'Bittima (ECHO)", 6.00, 27.07),
    ("Wau (Focal Point)", 7.71, 27.98),
    ("Tambura (Focal Point)", 5.61, 27.47),
]


def plan_sites(zones):
    """For each proposed site: the zone containing it, else km to each zone."""
    out = {}
    for label, lat, lon in PLAN_SITES:
        p = Point(lon, lat)
        inside = [n for n, z in zones.items() if z["geom"].contains(p)]
        d = {n: round(km_between(p, z["geom"]), 1) for n, z in zones.items()
             if n not in inside}
        near = dict(sorted(d.items(), key=lambda kv: kv[1])[:3])
        out[label] = dict(lat=lat, lon=lon, inside=inside, km_to_nearest_zones=near)
    return out


def km_between(pt, geom):
    fwd = pyproj.Transformer.from_crs(4326, "+proj=cea", always_xy=True).transform
    return transform(fwd, pt).distance(transform(fwd, geom)) / 1000.0


def nearest_towns(con, zones, min_pop=1000, k=6):
    """The nearest measured-population places OUTSIDE each zone, with distance.

    A rim ring answers "who is adjacent"; this answers "where is the weight",
    which for these zones sits 40-90 km out (Wau, Maridi, Raga) and would be
    invisible in any fixed ring.
    """
    rows = con.execute(
        """SELECT lat, lon, population_est, nearest_place, classification
           FROM park_settlements WHERE park_id = ? AND population_est >= ?""",
        (AOI, min_pop)).fetchall()
    fwd = pyproj.Transformer.from_crs(4326, "+proj=cea", always_xy=True).transform
    pts = [(transform(fwd, Point(r[1], r[0])), r) for r in rows]
    out = {}
    for name, z in zones.items():
        m = transform(fwd, z["geom"])
        d = sorted(((p.distance(m) / 1000.0, r) for p, r in pts), key=lambda x: x[0])
        out[name] = [dict(place=r[3], lat=round(r[0], 4), lon=round(r[1], 4),
                          pop=r[2], classification=r[4], km=round(km, 1))
                     for km, r in d[:k]]
    return out


# -------------------------------------------------------------------- mining
def mining(zones):
    """Reported mine sites, model candidates and watchlist villages per zone.

    Candidates and the watchlist are IMAGERY TARGETS with a measured but modest
    skill (prediction.json composite_skill) - never evidence of a mine.
    """
    pj = ROOT / "data/eval/xsa_mining/prediction.json"
    gj = ROOT / "data/eval/xsa_mining/prediction.geojson"
    if not (pj.exists() and gj.exists()):
        return {n: dict(note="unmeasured: prediction outputs missing") for n in zones}
    pred = json.load(open(pj))
    feats = json.load(open(gj))["features"]
    anchors = [(a["lon"], a["lat"], a.get("source"), a.get("resource"))
               for a in pred.get("anchors", []) if a.get("lat") is not None]
    cand = [(c["lon"], c["lat"], c) for c in pred.get("candidates", [])]
    watch = [(w["lon"], w["lat"], w)
             for w in pred.get("abandoned_village_gold_watchlist", {}).get("places", [])]
    top5 = [(f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1])
            for f in feats if f["properties"].get("tier") == "top05"]
    out = {}
    for name, z in zones.items():
        pz = prep(z["geom"])
        def inside(items):
            return [it for it in items if pz.contains(Point(it[0], it[1]))]
        ca, wa, an = inside(cand), inside(watch), inside(anchors)
        out[name] = dict(
            reported_mine_sites=len(an),
            reported_sources=dict(Counter(a[2] for a in an).most_common()),
            model_candidates=len(ca),
            candidate_characters=dict(Counter(c[2].get("character") for c in ca).most_common()),
            candidate_points=[dict(lat=round(c[1], 4), lon=round(c[0], 4),
                                   character=c[2].get("character"),
                                   basin=c[2].get("basin")) for c in ca],
            watchlist_villages=len(wa),
            watchlist_names=[w[2].get("name") for w in wa][:8],
            top05_cells=len(inside([(x, y, None) for x, y in top5])),
            caveat="candidates and watchlist are imagery targets, not mines "
                   "(composite_skill in prediction.json)",
        )
    return out


def render_report(res):
    """Plain-text block for the EASY assessment (section 3b).

    Every number here is read from the JSON just written - none is typed
    (root invariant 2), so re-running after a boundary edit reprints the
    section rather than inviting a hand-patch.
    """
    L = []
    z, st, sr = res["zones"], res["settlements"], res["settlements_rim"]
    de, dr = res["deforestation"], res["deforestation_rim"]
    cr, crr = res["cropland"], res["cropland_rim"]
    fd, ft, mi, mr = (res["fire_detections"], res["fire_trajectories"],
                      res["mining"], res["mining_rim"])
    hl = res["hist_labels"]
    rim = res["rim_km"]
    order = [n for n in z if z[n]["kind"] == "polygon"] + \
            [n for n in z if z[n]["kind"] == "marker"] + \
            [n for n in z if z[n]["kind"] == "baseline"]

    L.append("ZONE                                 AREA   PEOPLE  CLUSTERS  FIRE/1000km2/yr")
    L.append("-" * 80)
    for n in order:
        s, f = st[n], fd[n]
        L.append(f"{n[:36]:36s} {z[n]['area_km2']:7,.0f} {s['population_est']:8,d} "
                 f"{s['clusters']:9,d}  {f['detections_per_1000km2_per_year']:8,.0f}")
    L.append("")
    for n in order:
        s, f, t, d, c, m, h = st[n], fd[n], ft.get(n), de[n], cr[n], mi[n], hl.get(n, {})
        rk = f"{n} \u2014 {rim:g} km rim"
        L.append("=" * 80)
        lab = z[n]["label_km2"]
        labtxt = f" (file says {lab:,.0f})" if lab else ""
        L.append(f"{n}  \u2014 {z[n]['area_km2']:,.0f} km2 measured{labtxt}")
        L.append(f"  INSIDE  {s['clusters']} settlement clusters, "
                 f"{s['population_est']:,} people (GHSL); "
                 f"types {s['by_classification'] or '{}'}; "
                 f"age {s['by_persistence'] or '{}'}")
        if rk in sr:
            r = sr[rk]
            L.append(f"  RIM({rim:g}km) {r['clusters']} clusters, {r['population_est']:,} people; "
                     f"types {r['by_classification'] or '{}'}; "
                     f"recent(post-2015) {r['by_persistence'].get('recent', 0)}")
            L.append("           biggest: " + ", ".join(
                f"{x['place']} {x['pop']:,}" for x in r["largest"][:4]))
        nt = res["nearest_towns"].get(n, [])
        if nt:
            L.append("  NEAREST WEIGHT (>=1,000 people, any distance): " + ", ".join(
                f"{x['place']} {x['pop']:,} @ {x['km']} km" for x in nt[:4]))
        L.append(f"  CROPLAND inside {c['km2_2019']:.2f} km2 "
                 f"({c['frac_2019'] * 100:.4f}% of the zone; 2003: {c['km2_2003']:.2f} km2)"
                 + (f"; rim {crr[rk]['km2_2019']:.2f} km2" if rk in crr else ""))
        L.append(f"  CLEARING inside {d['events_verified']} verified events, "
                 f"{d['km2_verified']:.2f} km2 since 2001 "
                 f"({d['by_classification'] or '{}'})"
                 + (f"; rim {dr[rk]['km2_verified']:.2f} km2 in "
                    f"{dr[rk]['events_verified']} events" if rk in dr else ""))
        L.append(f"  FIRE     {f['detections_2024_2025']:,} detections in 2024-2025 = "
                 f"{f['detections_per_1000km2_per_year']:,.0f} per 1,000 km2 per year; "
                 f"Nov-Feb {f['nov_feb_share']:.0%}")
        if t:
            L.append(f"  FRONTS   {t['fronts_touching']:,} fire fronts touch it "
                     f"({t['by_type']}); {t['starts_inside']} start inside, "
                     f"{t['ends_inside']} die inside "
                     f"({t['ends_inside'] / t['fronts_touching']:.0%}); "
                     f"median {t['median_in_zone_km']} km of front inside")
            ext_o = {k[8:]: v for k, v in t["origins"].items() if k.startswith("outside")}
            ext_d = {k[8:]: v for k, v in t["destinations"].items() if k.startswith("outside")}
            zo = {k: v for k, v in t["origins"].items() if not k.startswith("outside")}
            zd = {k: v for k, v in t["destinations"].items() if not k.startswith("outside")}
            L.append(f"    from outside: {ext_o}")
            L.append(f"    to outside:   {ext_d}")
            L.append(f"    from zones:   { {k[:28]: v for k, v in zo.items()} }")
            L.append(f"    to zones:     { {k[:28]: v for k, v in zd.items()} }")
        L.append(f"  MINING   inside: {m['reported_mine_sites']} reported sites, "
                 f"{m['model_candidates']} model candidates, "
                 f"{m['watchlist_villages']} watchlist villages, "
                 f"{m['top05_cells']} top-5% cells"
                 + (f"; rim: {mr[rk]['reported_mine_sites']}/"
                    f"{mr[rk]['model_candidates']}/{mr[rk]['watchlist_villages']}"
                    if rk in mr else ""))
        if h.get("labels"):
            L.append(f"  1930s    {h['labels']} sheet labels {h['by_category']}")
            if h.get("places"):
                L.append("    named places: " + ", ".join(h["places"][:8]))
            if h.get("mine_notes"):
                L.append("    surveyor mine notes: " + "; ".join(h["mine_notes"]))
    L.append("=" * 80)
    L.append("SHARED FIRE FRONTS (one front whose path touches both zones)")
    for k, v in list(res["fire_connectivity"]["shared_fronts"].items())[:12]:
        L.append(f"  {v:6,d}  {k}")
    L.append("FRONT STARTS IN -> DIES IN")
    for k, v in list(res["fire_connectivity"]["zone_to_zone"].items())[:12]:
        L.append(f"  {v:6,d}  {k}")
    L.append("PLAN SITES vs ZONES")
    for k, v in res["plan_sites"].items():
        where = ", ".join(v["inside"]) if v["inside"] else "outside every zone; " + \
            ", ".join(f"{a[:30]} {b} km" for a, b in v["km_to_nearest_zones"].items())
        L.append(f"  {k:24s} {where}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kml-dir", default="/tmp/shelley-uploads")
    ap.add_argument("--json", default=str(ROOT / "data/eval/zone_stats.json"))
    ap.add_argument("--base", default="http://localhost:8000",
                    help="running 5mp instance, for the histmap label API")
    ap.add_argument("--pwd", default=os.environ.get("MAP_PWD", "test2026"))
    ap.add_argument("--report", metavar="FILE",
                    help="also write the plain-text per-zone block here")
    a = ap.parse_args()

    zones = {}
    for p in sorted(Path(a.kml_dir).glob("*.kml")):
        for nm, geom, kind, lab in read_kml(p):
            key = nm if nm not in zones else f"{nm} [{p.stem}]"
            zones[key] = dict(geom=geom, kind=kind, file=p.name,
                              area_km2=round(km2(geom), 0),
                              label_km2=lab,
                              bounds=[round(v, 3) for v in geom.bounds])
            if kind == "marker":
                zones[key]["note"] = (f"point placemark; statistics are for a "
                                      f"{MARKER_BUFFER_KM:g} km disc around it")
    if not zones:
        sys.exit("no KML zones found")

    xsa = shape(json.load(open(ROOT / "data/study_areas" / f"{AOI}.geojson")))
    for n, z in zones.items():
        inside = km2(z["geom"].intersection(xsa)) / max(km2(z["geom"]), 1e-9)
        z["share_inside_XSA"] = round(inside, 4)

    # The whole study area rides along as a BASELINE for the zonal measures
    # (every per-area number is meaningless without its background). It is
    # NOT part of the zone set used for trajectory labelling or overlaps: it
    # contains all the others, so it would swallow every origin label.
    base_zone = {"XSA study area (baseline)": dict(
        geom=xsa, kind="baseline", file="data/study_areas/XSA_Study_Area.geojson",
        area_km2=round(km2(xsa), 0), label_km2=None,
        bounds=[round(v, 3) for v in xsa.bounds], share_inside_XSA=1.0)}
    allz = {**zones, **base_zone}

    traj, conn = fire_trajectories(zones)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rims = {f"{n} — {RIM_KM:g} km rim": dict(geom=rim_of(z["geom"]), kind="rim")
            for n, z in zones.items() if z["kind"] == "polygon"}
    result = dict(
        rim_km=RIM_KM,
        zones={n: {k: v for k, v in z.items() if k != "geom"} for n, z in allz.items()},
        overlaps={},
        settlements=settlements(con, allz),
        places=places(con, allz),
        hist_labels=hist_labels(zones, a.base.rstrip("/"), a.pwd),
        settlements_rim=settlements(con, rims),
        nearest_towns=nearest_towns(con, zones),
        deforestation=deforestation(con, allz),
        deforestation_rim=deforestation(con, rims),
        cropland=cropland_zonal(allz),
        cropland_rim=cropland_zonal(rims),
        fire_detections=fire_grid(con, allz),
        fire_trajectories=traj,
        fire_connectivity=conn,
        mining=mining(allz),
        mining_rim=mining(rims),
        plan_sites=plan_sites(zones),
    )
    names = list(zones)
    for i, n in enumerate(names):
        for m in names[i + 1:]:
            inter = km2(zones[n]["geom"].intersection(zones[m]["geom"]))
            if inter > 1:
                result["overlaps"][f"{n} ∩ {m}"] = round(inter, 0)

    os.makedirs(Path(a.json).parent, exist_ok=True)
    json.dump(result, open(a.json, "w"), indent=1, ensure_ascii=False)
    text = render_report(result)
    if a.report:
        Path(a.report).write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nwrote {a.json}" + (f" and {a.report}" if a.report else ""), file=sys.stderr)


if __name__ == "__main__":
    main()
