#!/usr/bin/env python3
"""
Build the persistent-hotspot mask (fire_persistent_cells).

WHY
---
Some ~375m cells are detected as "fire" in essentially every month of the
record. Those are not wildfires: gas flares, charcoal kilns, industrial heat,
or a standing sensor artefact. Verified over 2020+ (79 months of data), 15 cells
burn in 75-78 distinct months, e.g.:

    DZA_Djurdjura   8 cells   75-78 months   350-796 detections each
    ZWE_Hwange      1 cell    78 months      597
    CAF_Dzanga_Park 2 cells   77-78 months   308 / 360
    GAB_Ivindo      1 cell    77 months      316
    CMR_Boumba_Bek  1 cell    76 months      404
    ZAF_Sederberg   1 cell    76 months      759

Left unmasked they seed an immortal trajectory group per cell that never ends,
inflating fragmentation metrics and producing permanent bogus fire alerts.

WHAT THE MASK MEANS
-------------------
The builder does NOT delete these detections. It refuses to let them SEED a
cluster/track; they may still be absorbed by a real fire front that sweeps over
the cell (see persistent_mask handling in rebuild_fire_trajectories_v5.py).
So a genuine wildfire crossing a flare site is still tracked in full.

CRITERIA
--------
A cell (0.0034deg grid, ~375m, matching VIIRS pixel size) is persistent if it
has detections in >= MIN_MONTHS distinct calendar months AND spans >=
MIN_SPAN_MONTHS calendar months. The second test stops a single very intense
multi-month season from being masked.

USAGE
-----
    python3 scripts/build_persistent_hotspots.py                # rebuild table
    python3 scripts/build_persistent_hotspots.py --min-months 24
    python3 scripts/build_persistent_hotspots.py --report       # read-only

Rerun monthly (cheap: one grouped scan, ~30s). After changing the mask, rebuild
trajectories and re-run scripts/eval_fire_trajectories.py - the golden set for
this feature is DZA_Djurdjura, ZWE_Hwange, CAF_Dzanga_Park.
"""

import argparse
import sqlite3
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"

# ~375m: one VIIRS pixel. Same expression must be used by any reader.
CELL_DEG = 0.0034
MIN_DATE = '2020-01-01'
# 30 distinct months out of 79 = burning in >1 month in 3 for six years.
# Real fire regimes are seasonal: a park's worst wildfire cell still only
# lights up 1-2 months a year (<= ~14 over the record).
MIN_MONTHS = 30
MIN_SPAN_MONTHS = 24
MIN_DETECTIONS = 50

DDL = """
CREATE TABLE IF NOT EXISTS fire_persistent_cells (
    park_id      TEXT NOT NULL,
    cell_x       INTEGER NOT NULL,     -- CAST(longitude/0.0034 AS INT)
    cell_y       INTEGER NOT NULL,     -- CAST(latitude/0.0034 AS INT)
    months       INTEGER NOT NULL,     -- distinct YYYY-MM with detections
    span_months  INTEGER NOT NULL,     -- first..last inclusive
    detections   INTEGER NOT NULL,
    mean_frp     REAL,
    first_seen   TEXT,
    last_seen    TEXT,
    computed_at  TEXT NOT NULL,
    PRIMARY KEY (park_id, cell_x, cell_y)
) WITHOUT ROWID;
"""

QUERY = f"""
SELECT protected_area_id,
       CAST(longitude / {CELL_DEG} AS INT) AS cx,
       CAST(latitude  / {CELL_DEG} AS INT) AS cy,
       COUNT(DISTINCT substr(acq_date, 1, 7)) AS months,
       COUNT(*)      AS n,
       AVG(frp)      AS mean_frp,
       MIN(acq_date) AS first_seen,
       MAX(acq_date) AS last_seen
FROM fire_detections
WHERE protected_area_id IS NOT NULL AND acq_date >= ?
GROUP BY 1, 2, 3
HAVING months >= ? AND n >= ?
"""


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def month_span(first, last):
    fy, fm = int(first[:4]), int(first[5:7])
    ly, lm = int(last[:4]), int(last[5:7])
    return (ly - fy) * 12 + (lm - fm) + 1


def load_mask(conn, park_id):
    """Set of (cell_x, cell_y) for a park. Used by the trajectory builder."""
    try:
        return {(r[0], r[1]) for r in conn.execute(
            "SELECT cell_x, cell_y FROM fire_persistent_cells WHERE park_id = ?",
            (park_id,))}
    except sqlite3.OperationalError:
        return set()   # table not built yet: mask simply inactive


def cell_of(lon, lat):
    return int(lon / CELL_DEG), int(lat / CELL_DEG)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--min-months', type=int, default=MIN_MONTHS)
    ap.add_argument('--min-span-months', type=int, default=MIN_SPAN_MONTHS)
    ap.add_argument('--min-detections', type=int, default=MIN_DETECTIONS)
    ap.add_argument('--since', default=MIN_DATE)
    ap.add_argument('--report', action='store_true', help='Do not write')
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    # The nightly ingest/backfill may hold the write lock for a while.
    conn.execute("PRAGMA busy_timeout=120000")
    log(f"Scanning fire_detections since {args.since} "
        f"(cell {CELL_DEG}deg, >= {args.min_months} distinct months)...")
    rows = conn.execute(QUERY, (args.since, args.min_months,
                               args.min_detections)).fetchall()
    log(f"  {len(rows)} candidate cells")

    kept = []
    for park, cx, cy, months, n, mean_frp, first, last in rows:
        span = month_span(first, last)
        if span < args.min_span_months:
            continue
        kept.append((park, cx, cy, months, span, n, round(mean_frp or 0, 2),
                     first, last))

    by_park = {}
    for r in kept:
        by_park.setdefault(r[0], []).append(r)
    log(f"  {len(kept)} persistent cells in {len(by_park)} parks")
    for park in sorted(by_park, key=lambda p: -len(by_park[p]))[:15]:
        cells = by_park[park]
        log(f"    {park}: {len(cells)} cell(s), "
            f"months {min(c[3] for c in cells)}-{max(c[3] for c in cells)}, "
            f"{sum(c[5] for c in cells):,} detections")

    if args.report:
        log("Report only, nothing written.")
        return 0

    conn.executescript(DDL)
    conn.execute("DELETE FROM fire_persistent_cells")
    now = datetime.now().isoformat()
    conn.executemany(
        "INSERT INTO fire_persistent_cells (park_id, cell_x, cell_y, months, "
        "span_months, detections, mean_frp, first_seen, last_seen, computed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [r + (now,) for r in kept])
    conn.commit()
    log(f"Wrote {len(kept)} rows to fire_persistent_cells")
    log("Next: rebuild affected parks and re-run scripts/eval_fire_trajectories.py")
    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
