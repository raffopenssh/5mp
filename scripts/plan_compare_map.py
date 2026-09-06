#!/usr/bin/env python3
"""Side-by-side map: the authors' drawn plan (KML + gazetted WDPA) vs the solver's zones, same frame, same class colours.

    python3 -W ignore scripts/plan_compare_map.py [--tag ""] [--out reports/ZONING_COMPARE_<date>.png]

Left: what the authors drew (park → core, wilderness/headwaters → wilderness, pâturage → corridor) and existing WDPA
designations (NP/faunal reserve/conservation area → core, game reserve/hunting area → wilderness); undrawn ground white.
Right: solve<tag>_lab.npy from plan_solver.py. Both panels: trunk rivers, towns ≥ 5,000, borders, the AOI frame, and
hatched where COMPARE's disagreement blocks lie (numbered, largest first). A third strip lists the blocks.
A judging aid, not a report figure: no legend prose, no provenance footer — build_map.py is the report map."""
import argparse, json, pickle, re, sqlite3, sys, datetime as dt
from pathlib import Path
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MPoly, Patch
from shapely.geometry import shape, mapping
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "scripts"))
import plan_conservancy_units as P
import __main__; __main__.Grid = P.Grid
SOLVER = ROOT / "data/plan_zones" / ("solver" if P.AOI == "XSA_Study_Area" else f"solver_{P.AOI}")
CLASSES = ["core", "wilderness", "community", "corridor"]
COL = {"core": "#1b5e20", "wilderness": "#7fae8b", "community": "#b48ad0", "corridor": "#d9534f"}
PLAN_CLASS = [("National-Park", "core"), ("Southern NP", "core"), ("Wilderness", "wilderness"), ("headwaters", "wilderness"), ("pâturage", "corridor"), ("Corridor", "corridor")]

def authors_label(G, refs):
    zl = np.zeros((G.h, G.w), np.int8)
    for k, g in refs.items():
        if g.geom_type == "Point": continue
        if k.startswith("PLAN"): c = next((c_ for pat, c_ in PLAN_CLASS if pat in k), None)
        else: c = "core" if re.search(r"National Park|Faunal Reserve|Conservation Area", k) else ("wilderness" if re.search(r"Game Reserve|Hunting Area", k) else None)
        if c is None: continue
        m = G.rasterize([(transform(P.FWD, g), 1)]).astype(bool); zl[m] = np.where(zl[m] == 0, CLASSES.index(c) + 1, zl[m])
    return zl

def draw_lab(ax, G, lab, extent, alpha=0.55, outlines=True, inten=None):
    """inten (0–1 per pixel, solver only) becomes OPACITY: a pale class is one the evidence barely supports — a community unit with
    nobody in it reads as unzoned white, a corridor reads as strong as the herd utilisation under it."""
    rgba = np.zeros((G.h, G.w, 4))
    for i, c in enumerate(CLASSES, 1):
        m = lab == i; col = np.array(matplotlib.colors.to_rgba(COL[c], alpha)); rgba[m] = col
        if inten is not None: rgba[m, 3] = 0.08 + 0.82 * np.clip(inten[m], 0, 1)
    ax.imshow(rgba, extent=extent, origin="upper", interpolation="nearest", zorder=2)
    # outline every zone (connected component of one class) so a conservancy's shape can be judged
    from scipy import ndimage
    from skimage import measure
    if not outlines: return
    for i, c in enumerate(CLASSES, 1):
        cc, n = ndimage.label(lab == i)
        for z in range(1, n + 1):
            m = np.pad(cc == z, 1)
            for ring in measure.find_contours(m.astype(float), 0.5):
                xs = extent[0] + (ring[:, 1] - 0.5) * G.res; ys = extent[3] - (ring[:, 0] - 0.5) * G.res
                ax.plot(xs, ys, color=matplotlib.colors.to_rgba(COL[c], 1.0) if c != "core" else "#0b3d12", lw=1.4 if c == "corridor" else 0.7, zorder=3.5)

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default=""); ap.add_argument("--out", default=""); ap.add_argument("--dpi", type=int, default=110)
    a = ap.parse_args()
    st = pickle.load(open(P.OUT / "state.pkl", "rb")); G = st["G"]
    lab_s = np.load(SOLVER / f"solve{a.tag}_lab.npy"); refs = P.references(); lab_a = authors_label(G, refs)
    lab_s = np.where(G.mask, lab_s, 0); lab_a = np.where(G.mask, lab_a, 0)
    inten = np.load(SOLVER / f"solve{a.tag}_intensity.npy") if (SOLVER / f"solve{a.tag}_intensity.npy").exists() else None
    cmp_ = json.load(open(SOLVER / f"compare{a.tag}.json")) if (SOLVER / f"compare{a.tag}.json").exists() else None
    # projected extent (CEA metres) → draw everything in CEA so the raster is axis-aligned
    extent = (G.x0, G.x0 + G.w * G.res, G.y1 - G.h * G.res, G.y1)
    con = sqlite3.connect(f"file:{P.DB}?mode=ro", uri=True)
    rivers = []
    for (gj,) in con.execute("SELECT geojson FROM park_rivers_hydro WHERE park_id=? AND stream_order>=6", (P.AOI,)):
        try: g = transform(P.FWD, shape(json.loads(gj)))
        except Exception: continue
        rivers += ([g] if g.geom_type == "LineString" else list(g.geoms))
    towns = [(n, *P.FWD(lo, la)) for n, lo, la, t in P.osm_towns(con) if t in ("city", "town", "verified_town")]
    ctry = P.countries()
    fig, axs = plt.subplots(1, 2, figsize=(22, 11.5), dpi=a.dpi); fig.subplots_adjust(left=0.02, right=0.98, top=0.93, bottom=0.16, wspace=0.03)
    titles = ["AUTHORS — drawn KML zones + gazetted WDPA (white = undrawn)", f"SOLVER — solve{a.tag} (every fine unit one class; ILP). Opacity = evidence: emptiness for core/wilderness, people for community, herd utilisation for corridor"]
    zones_gj = json.load(open(SOLVER / f"zones{a.tag}.geojson"))["features"] if (SOLVER / f"zones{a.tag}.geojson").exists() else None
    for ax, lab, t in zip(axs, (lab_a, lab_s), titles):
        ax.set_facecolor("white"); draw_lab(ax, G, lab, extent, outlines=not (lab is lab_s and zones_gj), inten=inten if lab is lab_s else None)
        if lab is lab_s and zones_gj:                                   # the exported zones (community split into committee-sized pieces): their outlines
            for f in zones_gj:
                c = f["properties"]["solver_class"]; gg = transform(P.FWD, shape(f["geometry"]))
                for part in (gg.geoms if hasattr(gg, "geoms") else [gg]): ax.plot(*part.exterior.xy, color={"core": "#0b3d12", "wilderness": "#3d6b4a", "community": "#4a1a66", "corridor": "#8b1a14"}[c], lw=1.3 if c == "corridor" else 0.6, zorder=3.5)
        for g in rivers: ax.plot(*g.xy, color="#3a6fa8", lw=0.5, zorder=3)
        for k, g in ctry.items():
            gg = transform(P.FWD, g)
            for part in (gg.geoms if hasattr(gg, "geoms") else [gg]): ax.plot(*part.exterior.xy, color="#222", lw=0.6, ls=(0, (4, 3)), zorder=4)
        aoi = transform(P.FWD, G.aoi)
        for part in (aoi.geoms if hasattr(aoi, "geoms") else [aoi]): ax.plot(*part.exterior.xy, color="#000", lw=1.0, zorder=5)
        for k, g in refs.items():                                       # every drawn/WDPA outline in grey on BOTH panels so the eye can compare
            if g.geom_type == "Point": continue
            gg = transform(P.FWD, g)
            for part in (gg.geoms if hasattr(gg, "geoms") else [gg]): ax.plot(*part.exterior.xy, color="#444" if k.startswith("PLAN") else "#777", lw=0.8 if k.startswith("PLAN") else 0.5, ls="-" if k.startswith("PLAN") else ":", zorder=6)
        for n, x, y in towns:
            if extent[0] < x < extent[1] and extent[2] < y < extent[3]: ax.plot(x, y, "o", ms=3, color="k", zorder=7); ax.annotate(n, (x, y), xytext=(3, 3), textcoords="offset points", fontsize=6.5, zorder=7)
        if lab is lab_s and (SOLVER / f"rank_conservancies{a.tag}.json").exists():
            rk = json.load(open(SOLVER / f"rank_conservancies{a.tag}.json")); zs = {f["properties"]["uid"]: f for f in json.load(open(SOLVER / f"zones{a.tag}.geojson"))["features"]}
            for i, r in enumerate(rk["rows"], 1):
                if not r.get("in_budget"): continue
                c = shape(zs[r["uid"]]["geometry"]).representative_point(); x, y = P.FWD(c.x, c.y)
                ax.annotate(f"C{i}", (x, y), ha="center", va="center", fontsize=7.5, fontweight="bold", color="white", bbox=dict(boxstyle="round,pad=0.2", fc="#6a2c8f", ec="white", lw=0.5), zorder=9)
        if cmp_:
            for i, b in enumerate(cmp_["disagreement_blocks"][:15], 1):
                x, y = P.FWD(b["lon"], b["lat"]); ax.annotate(str(i), (x, y), ha="center", va="center", fontsize=8, fontweight="bold", color="#000", bbox=dict(boxstyle="circle,pad=0.15", fc="#ffeb3b", ec="k", lw=0.6), zorder=9)
        ax.set_xlim(extent[0], extent[1]); ax.set_ylim(extent[2], extent[3]); ax.set_xticks([]); ax.set_yticks([]); ax.set_title(t, fontsize=12, loc="left")
        km2 = {c: float((lab == i).sum()) * G.cell_km2() for i, c in enumerate(CLASSES, 1)}
        ax.text(0.01, 0.01, "  ".join(f"{c} {km2[c]:,.0f} km²" for c in CLASSES), transform=ax.transAxes, fontsize=8.5, va="bottom", bbox=dict(fc="white", ec="none", alpha=0.85))
    handles = [Patch(fc=COL[c], ec="none", label=c) for c in CLASSES] + [Patch(fc="#6a2c8f", ec="white", label="C1..n = conservancy in budget order (RANK.txt)")] + [plt.Line2D([], [], color="#444", lw=0.8, label="authors' drawn line"), plt.Line2D([], [], color="#777", lw=0.5, ls=":", label="WDPA boundary"), plt.Line2D([], [], color="#3a6fa8", lw=0.5, label="river (order ≥6)")]
    fig.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.02, 0.10), ncol=8, fontsize=8.5, frameon=False)
    lines = []
    if cmp_:
        lines.append(f"drawn/designated {cmp_['drawn_share_of_aoi']:.0%} of AOI, agreement on it {cmp_['agreement_share_of_drawn']:.0%}; objective authors {cmp_['authors']['objective']:,} vs solver {cmp_['solver']['objective']:,}; "
                     f"people in corridor {cmp_['authors']['ledger']['people_in_corridor']:,} vs {cmp_['solver']['ledger']['people_in_corridor']:,}; herd-months outside corridor {cmp_['authors']['ledger']['herd_months_outside_corridor']:,} vs {cmp_['solver']['ledger']['herd_months_outside_corridor']:,}; open-bush boundary {cmp_['authors']['ledger']['boundary_open_bush_km']:,} vs {cmp_['solver']['ledger']['boundary_open_bush_km']:,} km")
        for i, b in enumerate(cmp_["disagreement_blocks"][:15], 1):
            lines.append(f"{i:>2}. {b['km2']:,} km² {('near ' + b['near']) if b.get('near') else ''}: authors {b['authors']} → solver {b['solver']} — {b['people']:,} people, cropland {b['cropland_pct']}%, herd UD {b['herd_ud_share']}, in band {b['in_herd_band']:.0%}, core-eligible {b.get('core_eligible', 0):.0%}")
    ncol = 2; half = (len(lines) - 1 + ncol - 1) // ncol
    fig.text(0.02, 0.085, lines[0] if lines else "", fontsize=7.5, va="top")
    for c_ in range(ncol): fig.text(0.02 + c_ * 0.49, 0.068, "\n".join(lines[1 + c_ * half: 1 + (c_ + 1) * half]), fontsize=6.8, va="top", family="monospace")
    fig.suptitle(f"Zoning plan comparison — {P.AOI} — {dt.date.today()}", fontsize=13, x=0.02, ha="left")
    out = Path(a.out) if a.out else ROOT / "reports" / f"ZONING_COMPARE_{dt.date.today():%Y-%m}{a.tag}.png"; out.parent.mkdir(exist_ok=True)
    fig.savefig(out); print(out)

if __name__ == "__main__": main()
