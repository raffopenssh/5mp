#!/usr/bin/env python3
"""Seam disagreement between neighbouring season fronts.

For every pair of the given areas whose grids overlap: |front_A - front_B| in
ABSOLUTE days (each grid's day-of-season + its season start) over the cells
where both hold a value, plus the cells only one holds. The number the
landscape front (docs/agents/fire.md "one surface per landscape") is judged
by: catchment fronts gave Ruaha/Kitulo 2024/25 a median 20 d, landscape 0.

    python3 scripts/eval_front_seams.py TZA_Ruaha,TZA_Kitulo_Plateau,TZA_Uzungwa_Scarp 2024/25
"""
import sqlite3, sys
from datetime import date
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import fire_front as FF  # noqa: E402
from fire_source import DB_PATH  # noqa: E402


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    areas, season = sys.argv[1].split(","), sys.argv[2]
    G = {}
    for a in areas:
        r = conn.execute("SELECT season_start,x0,y0,nx,ny,res,front FROM fire_season_front WHERE area_id=? AND season=?",
                         (a, season)).fetchone()
        if not r:
            print(f"{a}: no {season} front"); continue
        s0, x0, y0, nx, ny, res, fb = r
        f = FF.unpack(fb, ny, nx) + date.fromisoformat(s0).toordinal()
        G[a] = (round(x0 / res), round(y0 / res), nx, ny, f)
    ids = list(G)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ax, ay, anx, any_, af = G[a]; bx, by, bnx, bny, bf = G[b]
            x0, x1 = max(ax, bx), min(ax + anx, bx + bnx); y0, y1 = max(ay, by), min(ay + any_, by + bny)
            if x1 <= x0 or y1 <= y0:
                continue
            A = af[y0 - ay:y1 - ay, x0 - ax:x1 - ax]; B = bf[y0 - by:y1 - by, x0 - bx:x1 - bx]
            m = np.isfinite(A) & np.isfinite(B); d = np.abs(A - B)[m]
            med = f"{np.median(d):.1f}" if d.size else "n/a"; p90 = f"{np.percentile(d, 90):.1f}" if d.size else "n/a"
            print(f"{a} vs {b}: overlap {m.size} cells, both {m.sum()}, only-{a} {int((np.isfinite(A) & ~np.isfinite(B)).sum())}, "
                  f"only-{b} {int((~np.isfinite(A) & np.isfinite(B)).sum())}; |Δ| median {med} d, p90 {p90}")


if __name__ == "__main__":
    main()
