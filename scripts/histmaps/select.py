#!/usr/bin/env python3
"""Choose ONE scan per 1:250k sheet cell: the most detailed, preferring 1924-1936.

Why this exists: the LOC item holds 759 placeable scans covering only 195 distinct
sheet cells -- most cells have 3-5 editions (1908-1976) and some have two scans of
the *same* edition. Georeferencing all of them costs days and yields a stack
of near-duplicates. This picks one per cell.

How "most detailed" is decided, in order:

1. **Edition window first, quality second.** Any edition inside --from..--to beats
   every edition outside it, regardless of score. The window is an editorial
   choice about which survey to represent; detail only breaks ties *within* it.
   A cell with no in-window edition falls back to scoring all its editions, and
   is reported so the fallback is visible rather than silent.

2. **Interior ink coverage, measured, not guessed.** Score = fraction of the
   flattened+hysteresis ink mask on a ~6% thumbnail, with the outer 14% of each
   edge cropped away. The crop matters: title blocks, legends and margin notes
   are dense ink that is not map content, and scoring the full sheet ranks a
   fat legend above a surveyed interior. Newer is NOT used as a proxy for
   detail -- measured on 65-J the 1936 edition really does carry ~2x the ink of
   1916, but on 65-I the 1909 sheet outscores both 1916 editions, so the year
   would have picked wrong.

   When two scans are the same edition, this correctly prefers the sharper scan
   (65-L: cs000249 at 0.190 vs cs000014 at 0.141 -- identical content, better
   capture), which is the tie-break we want.

Reads the same catalogue as sudan250k.py, writes a JSON plan + an --ids line.
"""
import argparse, json, os, subprocess, sys, collections, math
import cv2, numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import ink
from sudan250k import catalogue, IIIF

THUMB_PCT = 6      # ~750 px wide; enough for an ink-density ratio, 40 KB a sheet
MARGIN    = 0.14   # fraction of each edge treated as collar and excluded


def thumb(cid, pct=THUMB_PCT, cache=None):
    p = os.path.join(cache or "/tmp", f"th_{cid}.jpg")
    if not os.path.exists(p) or os.path.getsize(p) < 5000:
        # LOC's HTTP/2 endpoint resets streams under concurrency; force 1.1+retry.
        subprocess.check_call(["curl", "-sS", "--http1.1", "--retry", "5",
                               "--retry-all-errors", "--retry-delay", "2",
                               "-o", p, f"{IIIF}:{cid}/full/pct:{pct}/0/default.jpg"])
    return p


def detail_score(cid, cache=None, margin=MARGIN):
    """Interior ink coverage of the thumbnail. Higher = denser line work."""
    g = cv2.imread(thumb(cid, cache=cache), cv2.IMREAD_GRAYSCALE)
    if g is None:
        return None
    h, w = g.shape
    g = g[int(h*margin):int(h*(1-margin)), int(w*margin):int(w*(1-margin))]
    if g.size == 0:
        return None
    return float(ink.hysteresis(ink.flatten(g)).mean())


def main():
    a = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--from", dest="y0", type=int, default=1924)
    a.add_argument("--to",   dest="y1", type=int, default=1936)
    a.add_argument("--out",  default=os.path.join(ROOT, "selection.json"))
    a.add_argument("--cache", default="/tmp", help="thumbnail cache dir")
    a.add_argument("--quiet", action="store_true")
    a.add_argument("--priority-bbox", metavar="W,S,E,N",
                   help="emit cells intersecting this bbox first (nearest-centre "
                        "order), so an interrupted run has the sheets that matter")
    a.add_argument("--priority-buffer", type=float, default=0.5,
                   help="degrees of slack around --priority-bbox (default 0.5)")
    ns = a.parse_args()

    cells = collections.defaultdict(list)
    for c in catalogue():
        if c["extent"]:
            cells[c["sheet"]].append(c)

    plan, fallback = [], []
    for cell in sorted(cells):
        eds = cells[cell]
        inw = [e for e in eds if e["year"] and ns.y0 <= e["year"] <= ns.y1]
        pool, used_fallback = (inw, False) if inw else (eds, True)
        for e in pool:
            e["score"] = detail_score(e["id"], ns.cache)
        scored = [e for e in pool if e["score"] is not None] or pool
        best = max(scored, key=lambda e: (e["score"] or 0))
        rec = dict(cell=cell, id=best["id"], year=best["year"], title=best["title"],
                   extent=best["extent"], score=round(best["score"] or 0, 4),
                   in_window=not used_fallback, n_editions=len(eds),
                   rejected=[dict(id=e["id"], year=e["year"],
                                  score=round(e["score"], 4) if e.get("score") else None)
                             for e in pool if e is not best])
        plan.append(rec)
        if used_fallback:
            fallback.append(rec)
        if not ns.quiet:
            flag = "" if not used_fallback else "  [no edition in window]"
            print(f"{cell:7} -> {best['id']} {str(best['year']):5} "
                  f"ink {rec['score']:.4f}  of {len(pool)} cand{flag}", flush=True)

    # Order the plan. A 195-cell run is ~2 days of downloading and warping, and
    # it WILL be interrupted, so the order is part of the product: put the cells
    # covering the area under study first, nearest-centre outwards, and mark
    # them so a partial run can be inspected for "is the AOI covered yet".
    if ns.priority_bbox:
        w, s, e, n = [float(v) for v in ns.priority_bbox.split(",")]
        b = ns.priority_buffer
        cx, cy = (w + e) / 2, (s + n) / 2
        for r in plan:
            x0, y0, x1, y1 = r["extent"]
            r["priority"] = (x0 < e + b and x1 > w - b and y0 < n + b and y1 > s - b)
            r["_d"] = math.hypot((x0 + x1) / 2 - cx, (y0 + y1) / 2 - cy)
        plan.sort(key=lambda r: (not r["priority"], r["_d"]))
        for r in plan:
            r.pop("_d", None)
        npri = sum(1 for r in plan if r["priority"])
        print(f"\npriority: {npri} of {len(plan)} cells intersect "
              f"{ns.priority_bbox} (+{b} deg) and are ordered first")

    json.dump(dict(window=[ns.y0, ns.y1], n_cells=len(plan),
                   priority_bbox=ns.priority_bbox, selected=plan),
              open(ns.out, "w"), indent=1)
    print(f"\n{len(plan)} cells selected -> {ns.out}")
    print(f"{len(plan)-len(fallback)} inside {ns.y0}-{ns.y1}, "
          f"{len(fallback)} fell back to nearest available edition")
    if fallback:
        print("fallback cells (no edition in window):")
        for r in fallback:
            print(f"  {r['cell']:7} {r['id']} {r['year']}  {r['title'][:46]}")
    print("\nids:")
    print(",".join(r["id"] for r in plan))


if __name__ == "__main__":
    main()
