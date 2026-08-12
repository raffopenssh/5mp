#!/usr/bin/env python3
"""Derive the CONTACTS of a geology sheet: where two mapped units meet.

    scripts/geomaps/contacts.py sudan
    scripts/geomaps/contacts.py                 # every sheet whose units exist

WHY THIS IS A LAYER AND NOT A PROPERTY OF A POLYGON

legend.py's affinity model answers *which rock* can host a commodity. The next
question a geologist asks is *where two of them meet*: granite against a
greenstone belt is the classic orogenic-gold setting, an intrusive against a
carbonate is the skarn setting, listwaenite gold sits on an ophiolite thrust.
Prospectivity there belongs to the BOUNDARY, not to either polygon, so nothing
keyed on a single unit can state it.

WHY IN THE BUILD, NEVER IN THE BROWSER

Sudan's 46 classes share 567 boundaries across 800k line segments. Recomputing
that on every pan would ship as a hang. It is derived once, here, written to a
file, tiled by tiles.sh and served like any other attribute.

HOW — ONE SEGMENT INDEX, NOT N² BUFFERS

The obvious implementation is a pairwise `a.boundary.buffer(t).intersection(
b.boundary)` over every candidate pair. It is correct and it is far too slow:
one GEOS buffer per pair, 47 s for CAR's *seventeen* polygons and minutes for
Sudan, because each pair re-buffers the whole boundary of both units.

The observation that kills it: a contact is a property of a SEGMENT, so build
the segment index once and let every pair fall out of one pass.

  1. explode every ring of every unit into segments (Sudan: 802k)
  2. densify to the snap distance, so a long segment cannot skip a cell
  3. hash each sub-segment's midpoint into a CELL-sized grid
  4. one pass over the 3x3 neighbourhood: a sub-segment whose neighbourhood
     holds a DIFFERENT unit is a contact segment of that pair

All of it is numpy over int64 keys — no per-pair geometry, no STRtree query
per polygon, one `line_merge` per pair at the end. Sudan drops from minutes to
~30 s, CAR from 47 s to ~6 s, and the cost now scales with the number of
segments rather than with the square of the number of units.

SLIVERS ARE KEPT — ALL OF THEM

An earlier draft cut contacts shorter than 2 km as "digitising noise". That is
exactly backwards for what this layer is for: a short contact is a small
intrusive against a belt, a faulted wedge, a screen between two plutons — the
ground an artisanal gold rush actually works. A minimum length would delete
the most interesting features and (invariant 1) would do it silently. So the
only thing excluded is a touch that is not a LINE at all: two polygons meeting
at a single vertex intersect in a point, which is a topological accident of
digitising, has no length, and cannot be drawn. Those are counted and
reported, never dropped in silence.

WHAT COMES OUT

  data/geomaps/<sheet>_contacts.geojson   one MultiLineString per unit PAIR
                                          (gitignored; the input to tiles.sh)
  data/geomaps/<sheet>_contacts.json      the same pairs without geometry —
                                          small, committed, and what the
                                          server reads to build the catalogue

A pair is UNORDERED and carries both codes, because the UI has to answer
"granite against greenstone" without a second lookup.
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np
import shapely
from shapely.geometry import shape, mapping

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
OUT_DIR = os.path.join(ROOT, "data", "geomaps")
SHEETS = ("sudan", "car", "tanzania")

# How far apart two traced boundaries may be and still be the same line.
#
# These polygons are traced off a raster (or arrive from a survey's own WFS),
# so two units that share an edge do NOT share vertices: a strict intersection
# of their boundaries returns points, not lines, and the honest-looking answer
# is "these units never touch". 0.002 deg ~= 200 m, well under the ~500 m line
# work a 1:1.5M-1:2M sheet resolves, so it cannot fuse genuinely separate
# boundaries.
CELL_DEG = 0.002

# A sub-segment is densified to HALF a cell, not to a whole one.
#
# The midpoint is what gets hashed and what the distance test uses, so a
# sub-segment as long as the cell can have its midpoint 100 m from its ends: a
# real contact of a few hundred metres then falls between two midpoints and is
# never found. Checked against the slow pairwise ground truth on CAR, a whole
# cell missed one pair (a1/bA-bD, 0.5 km) and under-measured the rest by ~5%;
# half a cell recovers all 113 pairs with none invented. That missed pair is
# exactly the kind this layer exists for — a small basic intrusion against
# alluvium — so the resolution is set by what it must not drop, not by speed.
STEP_FRAC = 0.5

# Units per sheet must fit in the low bits of the int64 pair key.

_UNIT_BITS = 1 << 12


def load_units(sheet):
    """(codes, names, x1, y1, x2, y2, cls) — every ring, keyed by CLASS.

    Keyed by class and not by feature, deliberately. Sudan is one feature per
    class, but Tanzania is 596 features over 41 classes, and a feature-keyed
    pass produced 1,026 "contacts" that were really 297 pairs listed several
    times over — plus 36 rows where a unit met ITSELF across a tiling seam,
    which is not a contact, it is the same rock drawn twice. A contact is a
    statement about two rock types, so the class is the identity.
    """
    path = os.path.join(OUT_DIR, "%s_units.geojson" % sheet)
    with open(path) as fh:
        doc = json.load(fh)
    feats = doc.get("features", [])
    if not feats:
        raise SystemExit("%s: no units in %s" % (sheet, path))
    X1, Y1, X2, Y2, U = [], [], [], [], []
    codes, names, index = [], [], {}
    for f in feats:
        p = f.get("properties", {})
        code = p.get("code", "")
        if code not in index:
            index[code] = len(codes)
            codes.append(code)
            names.append(p.get("name", ""))
        k = index[code]
        g = shape(f["geometry"])
        if g.is_empty:
            continue
        for poly in getattr(g, "geoms", [g]):
            if poly.geom_type != "Polygon":
                continue
            for ring in [poly.exterior] + list(poly.interiors):
                c = np.asarray(ring.coords)
                if len(c) < 2:
                    continue
                X1.append(c[:-1, 0]); Y1.append(c[:-1, 1])
                X2.append(c[1:, 0]);  Y2.append(c[1:, 1])
                U.append(np.full(len(c) - 1, k, dtype=np.int64))
    if len(codes) >= _UNIT_BITS:
        raise SystemExit("%s: %d classes exceeds the pair-key packing (%d)"
                         % (sheet, len(codes), _UNIT_BITS))
    return (codes, names, np.concatenate(X1), np.concatenate(Y1),
            np.concatenate(X2), np.concatenate(Y2), np.concatenate(U))


def densify(x1, y1, x2, y2, u, cell):
    """Split every segment so no piece spans more than one grid cell.

    Without this a 40 km segment hashes to ONE cell at its midpoint and its
    whole length is invisible to the index — which shows up as a contact that
    exists along a wiggly boundary and vanishes along a straight one.
    """
    span = np.maximum(np.abs(x2 - x1), np.abs(y2 - y1))
    n = np.maximum(1, np.ceil(span / cell).astype(np.int64))
    idx = np.repeat(np.arange(len(n)), n)
    start = np.zeros(len(n), np.int64)
    start[1:] = np.cumsum(n)[:-1]
    k = np.arange(len(idx)) - start[idx]
    ta, tb = k / n[idx], (k + 1) / n[idx]
    dx, dy = x2[idx] - x1[idx], y2[idx] - y1[idx]
    return (x1[idx] + dx * ta, y1[idx] + dy * ta,
            x1[idx] + dx * tb, y1[idx] + dy * tb, u[idx])


def km_len(geom):
    """Length in km, scaled at the geometry's own latitude.

    Not a projection: these are 1:1.5M sheets and this number orders pairs and
    sizes a hairline. It is never printed as a survey measurement.
    """
    if geom is None or geom.is_empty:
        return 0.0
    total = 0.0
    for ln in getattr(geom, "geoms", [geom]):
        cs = getattr(ln, "coords", None)
        if cs is None:
            continue
        c = np.asarray(cs)
        if len(c) < 2:
            continue
        lat = np.radians((c[:-1, 1] + c[1:, 1]) / 2.0)
        dx = (c[1:, 0] - c[:-1, 0]) * np.cos(lat)
        dy = c[1:, 1] - c[:-1, 1]
        total += float(np.sum(np.hypot(dx, dy)) * 111.32)
    return total


def contacts(sheet, cell=CELL_DEG, verbose=True):
    t0 = time.time()
    codes, names, x1, y1, x2, y2, u = load_units(sheet)
    n_seg = len(u)
    sx1, sy1, sx2, sy2, su = densify(x1, y1, x2, y2, u, cell * STEP_FRAC)
    t_prep = time.time()

    # Grid key of every sub-segment's midpoint, sorted once. The index is over
    # SUB-SEGMENTS, not over (cell, unit) pairs, and that distinction is worth
    # the extra memory: a cell-membership index only answers "some part of unit
    # B is in this cell", which is true across a whole cell diagonal and so
    # pulls in boundaries up to ~300 m away. Measured against the slow pairwise
    # ground truth, that over-reported CAR's contact lengths by ~30% and
    # invented seven pairs that do not touch at all. Keeping the segment is
    # what lets the distance be MEASURED instead of assumed.
    mx = (sx1 + sx2) / 2
    my = (sy1 + sy2) / 2
    ix = np.floor(mx / cell).astype(np.int64) + 200000
    iy = np.floor(my / cell).astype(np.int64) + 200000
    cellkey = ix * 1000000 + iy
    order = np.argsort(cellkey, kind="stable")
    kcell = cellkey[order]
    t_key = time.time()

    # The 3x3 neighbourhood, queried rather than materialised. Dilating the
    # INDEX (writing all nine copies of every cell) costs 9x the memory and
    # measured slower; dilating the QUERY touches each sub-segment nine times
    # over an array that stays put. The cell IS the snap distance, so 3x3
    # covers the whole disc and nothing within it can be missed.
    seg_hits, other_hits = [], []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            q = cellkey + dx * 1000000 + dy
            lo = np.searchsorted(kcell, q, "left")
            hi = np.searchsorted(kcell, q, "right")
            cnt = hi - lo
            tot = int(cnt.sum())
            if not tot:
                continue
            segi = np.repeat(np.arange(len(cellkey)), cnt)
            off = np.arange(tot) - np.repeat(np.cumsum(cnt) - cnt, cnt)
            oj = order[np.repeat(lo, cnt) + off]
            m = su[oj] != su[segi]       # a unit against itself is not a contact
            segi, oj = segi[m], oj[m]
            # The cell is the candidate filter; THIS is the test. Without it a
            # segment in the far corner of an adjacent cell counts as touching.
            near = np.hypot(mx[oj] - mx[segi], my[oj] - my[segi]) <= cell
            seg_hits.append(segi[near])
            other_hits.append(su[oj[near]])
    t_match = time.time()

    if not seg_hits or not sum(len(s) for s in seg_hits):
        segi = np.empty(0, np.int64)
        oth = np.empty(0, np.int64)
    else:
        segi = np.concatenate(seg_hits)
        oth = np.concatenate(other_hits)

    # ONE SIDE OF THE JUNCTION, NOT BOTH.
    #
    # A shared boundary is traced twice — once as unit A's ring, once as unit
    # B's, ~200 m apart — so keeping every matched sub-segment draws the
    # contact as a pair of parallel hairlines and reports TWICE its length.
    # CAR came out at 97,000 km of contact, which is not a measurement, it is
    # a double count. The line is the junction, so keep the side belonging to
    # the lower-numbered unit: deterministic, half the geometry, and a length
    # that means what it says.
    a = np.minimum(su[segi], oth)
    b = np.maximum(su[segi], oth)
    one_side = su[segi] == a
    segi, a, b = segi[one_side], a[one_side], b[one_side]
    pk = a * _UNIT_BITS + b
    # (pair, segment), deduplicated: a sub-segment is found from several cells
    # of the neighbourhood and must contribute its length once.
    comb = np.unique(pk * (n_seg_max := (len(su) + 1)) + segi)
    pks, ss = comb // n_seg_max, comb % n_seg_max
    t_dedupe = time.time()

    rows = []
    point_only = 0
    if len(pks):
        coords = np.empty((len(ss) * 2, 2))
        coords[0::2, 0] = sx1[ss]; coords[0::2, 1] = sy1[ss]
        coords[1::2, 0] = sx2[ss]; coords[1::2, 1] = sy2[ss]
        lines = shapely.linestrings(coords, indices=np.repeat(np.arange(len(ss)), 2))
        bounds = np.flatnonzero(np.r_[True, pks[1:] != pks[:-1]])
        for i, s0 in enumerate(bounds):
            e0 = bounds[i + 1] if i + 1 < len(bounds) else len(pks)
            geom = shapely.line_merge(shapely.multilinestrings(lines[s0:e0]))
            if geom.is_empty:
                point_only += 1
                continue
            ia, ib = int(pks[s0]) // _UNIT_BITS, int(pks[s0]) % _UNIT_BITS
            km = km_len(geom)
            if km <= 0:
                # A touch with no length: a single shared vertex. It cannot be
                # drawn and it is not a contact. Counted, never silent.
                point_only += 1
                continue
            rows.append({"a": codes[ia], "b": codes[ib],
                         "name_a": names[ia], "name_b": names[ib],
                         "km": round(km, 1), "geom": geom})
    rows.sort(key=lambda r: -r["km"])
    took = time.time() - t0

    n_classes = len(codes)
    if verbose:
        print("%s: %d classes, %d segments -> %d contacts, %.0f km "
              "in %.1fs (load+densify %.1f, index %.1f, match %.1f, merge %.1f; "
              "%d point-only touch(es) excluded, no length cut)"
              % (sheet, n_classes, n_seg, len(rows),
                 sum(r["km"] for r in rows), took,
                 t_prep - t0, t_key - t_prep, t_match - t_key, took - (t_dedupe - t0),
                 point_only))
        if rows:
            short = sum(1 for r in rows if r["km"] < 2)
            print("   shortest %.1f km, longest %.0f km, %d under 2 km (KEPT: a "
                  "short contact is a small intrusive against a belt, which is "
                  "the ground a rush works)" % (rows[-1]["km"], rows[0]["km"], short))

    # INVARIANT 1. A geological sheet tiles its area, so its units share
    # boundaries by construction. Zero contacts means the pass did not run —
    # it must report unfinished rather than freeze a wrong result as success.
    if n_classes > 1 and not rows:
        raise SystemExit(
            "%s: UNFINISHED - %d classes and 0 contacts. A mapped sheet's units "
            "share boundaries by construction, so this is a broken pass, not an "
            "empty result." % (sheet, n_classes))

    stats = dict(classes=n_classes, segments=int(n_seg),
                 subsegments=int(len(su)), snap_deg=cell,
                 point_only_excluded=point_only, min_km=None,
                 seconds=round(took, 1))
    return rows, stats


def write(sheet, rows, stats):
    os.makedirs(OUT_DIR, exist_ok=True)
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         # `pair` is the join key the client filters on: one string compare
         # instead of two, because MapLibre evaluates a filter per feature per
         # frame on the render thread.
         "properties": {"sheet": sheet, "code_a": r["a"], "code_b": r["b"],
                        "pair": r["a"] + "|" + r["b"], "km": r["km"]},
         "geometry": mapping(r["geom"])}
        for r in rows]}
    gpath = os.path.join(OUT_DIR, "%s_contacts.geojson" % sheet)
    with open(gpath, "w") as fh:
        json.dump(gj, fh)

    # The committed half: the pairs without their geometry. The server has to
    # know WHICH units meet (to grade a junction and to size the layer) and
    # must not parse a 30 MB geometry file to find out.
    jpath = os.path.join(OUT_DIR, "%s_contacts.json" % sheet)
    with open(jpath, "w") as fh:
        json.dump({"sheet": sheet, "n_contacts": len(rows),
                   "total_km": round(sum(r["km"] for r in rows), 1),
                   "quality": stats,
                   "contacts": [{"a": r["a"], "b": r["b"], "km": r["km"]}
                                for r in rows]}, fh, indent=1)
    return gpath, jpath


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sheet", nargs="*", choices=list(SHEETS), default=None)
    ap.add_argument("--snap-deg", type=float, default=CELL_DEG,
                    help="how far apart two traced boundaries may be and still "
                         "be the same line (default %(default)s deg ~ 200 m)")
    a = ap.parse_args(argv)
    todo = a.sheet or [s for s in SHEETS
                       if os.path.exists(os.path.join(OUT_DIR, "%s_units.geojson" % s))]
    if not todo:
        raise SystemExit("no <sheet>_units.geojson found in %s" % OUT_DIR)
    for s in todo:
        rows, stats = contacts(s, cell=a.snap_deg)
        g, j = write(s, rows, stats)
        print("   -> %s (%.1f MB), %s" % (g, os.path.getsize(g) / 1e6, j))
    return 0


if __name__ == "__main__":
    sys.exit(main())
