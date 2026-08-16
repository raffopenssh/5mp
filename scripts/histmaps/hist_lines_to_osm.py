#!/usr/bin/env python3
"""Convert 1930s-sheets stitched road/track lines to OSM XML for OSRM.

The historic sheets show a route network the modern OSM extract largely
lacks.  Assumption (stated, not hidden): a 1930s route is at least
bicycle-fast today and slowly drivable -- hist road -> maxspeed=20,
hist track -> maxspeed=15, both highway=unclassified surface=ground so
both car.lua and foot.lua route them.  Tagged source=sudan250k_1915_68.

CONNECTIVITY IS THE POINT, not the geometry.  A first version emitted the
lines as free-floating ways with fresh node ids: OSRM marked every one a
tiny disconnected component, refused to snap table phantoms into them, and
returned a bit-identical 36.9M-pair matrix -- a no-op that read as an
answer.  So this script must:
  1. weld hist vertices to each other (traced lines never share exact
     coordinates) within WELD_M, so the hist net is one fabric, and
  2. stitch the fabric into the modern graph by REUSING the modern highway
     node id when a hist vertex falls within STITCH_M of one.
It prints the connection stats and refuses to write a file whose hist
component count suggests it is still an archipelago of islands.

Needs /tmp/modern_hw_nodes.npz (ids+lonlat of modern highway nodes), built
by osmium tags-filter w/highway + a 5-line pyosmium pass -- see
docs/agents/mining.md "Historic network".

Usage: hist_lines_to_osm.py OUT.osm [minlon minlat maxlon maxlat]
"""
import json
import sqlite3
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data/histmaps/labels.sqlite3"
MODERN_NODES = Path("/tmp/modern_hw_nodes.npz")
BASE_ID = 90_000_000_000        # far above real OSM ids
WELD_M = 60.0                   # hist vertices closer than this = one node
STITCH_M = 150.0                # hist vertex adopts a modern node id within this


def main():
    out = sys.argv[1]
    if len(sys.argv) >= 6:
        w, s, e, n = map(float, sys.argv[2:6])
    else:
        w, s, e, n = 22.3, 4.1, 31.4, 11.2  # XSA OSRM extract bbox

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = c.execute(
        "SELECT id, kind, name, pts FROM lines_stitched "
        "WHERE kind IN ('road','track') AND maxlon>=? AND minlon<=? "
        "AND maxlat>=? AND minlat<=?", (w, e, s, n)).fetchall()
    if len(rows) < 100:
        sys.exit(f"UNFINISHED: only {len(rows)} historic lines in bbox "
                 "- expected ~1,700; refusing to write a near-empty file")

    lines = []
    all_pts = []
    for lid, kind, name, pts in rows:
        coords = json.loads(pts)
        if len(coords) < 2:
            continue
        idx = list(range(len(all_pts), len(all_pts) + len(coords)))
        all_pts.extend(coords)
        lines.append((lid, kind, name, idx))
    P = np.array(all_pts, np.float64)

    lat0 = float(P[:, 1].mean())
    kx = 111.32 * np.cos(np.radians(lat0))
    ky = 111.32
    Pk = P * [kx, ky]                       # km plane

    # --- 1. weld hist vertices (union-find over WELD_M pairs) ---
    tree = cKDTree(Pk)
    parent = np.arange(len(P))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in tree.query_pairs(WELD_M / 1000.0):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    root = np.array([find(i) for i in range(len(P))])
    welds = len(P) - len(set(root.tolist()))

    # --- 2. stitch to modern network by adopting modern node ids ---
    mz = np.load(MODERN_NODES)
    mids, mxy = mz["ids"], mz["xy"]
    mtree = cKDTree(mxy * [kx, ky])
    d, j = mtree.query(Pk)
    node_id = {}                            # root -> emitted node id
    node_ll = {}
    adopted = set()
    next_id = BASE_ID
    for i in range(len(P)):
        r = root[i]
        if r in node_id:
            continue
        if d[i] * 1000 <= STITCH_M:
            node_id[r] = int(mids[j[i]])    # reuse modern node -> shared graph
            adopted.add(r)
        else:
            next_id += 1
            node_id[r] = next_id
            node_ll[node_id[r]] = (P[r][0], P[r][1])

    # --- report connectivity before writing ---
    lines_touching = sum(
        1 for _, _, _, idx in lines if any(root[i] in adopted for i in idx))
    print(f"{len(lines)} lines, {len(P)} vertices -> {welds} welds, "
          f"{len(adopted)} vertices adopt a modern node id "
          f"({lines_touching} lines touch the modern network directly)")
    if lines_touching < len(lines) * 0.05:
        sys.exit("UNFINISHED: almost no hist line reaches the modern "
                 "network - the merge would be disconnected islands again")

    # --- write ---
    wid = BASE_ID
    with open(out, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<osm version="0.6" generator="hist_lines_to_osm">\n')
        for i, (lon, lat) in node_ll.items():
            f.write(f' <node id="{i}" version="1" '
                    f'lat="{lat:.6f}" lon="{lon:.6f}"/>\n')
        for lid, kind, name, idx in lines:
            refs = []
            for i in idx:                   # collapse welded repeats
                r = node_id[root[i]]
                if not refs or refs[-1] != r:
                    refs.append(r)
            if len(refs) < 2:
                continue
            wid += 1
            maxspeed = 20 if kind == "road" else 15
            tags = [("highway", "unclassified"), ("surface", "ground"),
                    ("maxspeed", str(maxspeed)),
                    ("source", "sudan250k_1915_68"),
                    ("hist_kind", kind), ("hist_id", str(lid))]
            if name:
                tags.append(("name", name))
            f.write(f' <way id="{wid}" version="1">\n')
            for r in refs:
                f.write(f'  <nd ref="{r}"/>\n')
            for k, v in tags:
                f.write(f'  <tag k="{k}" v="{escape(v)}"/>\n')
            f.write(' </way>\n')
        f.write('</osm>\n')
    print(f"wrote {out}: {wid - BASE_ID} ways, {len(node_ll)} new nodes, "
          f"{len(adopted)} shared modern nodes")


if __name__ == "__main__":
    main()
