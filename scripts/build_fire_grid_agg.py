#!/usr/bin/env python3
"""Build/refresh pre-aggregated fire grids for the time animator.

Tables (WITHOUT ROWID, PK (d, xi, yi), base res 0.1 deg):
  fire_grid_day, fire_grid_week, fire_grid_month

Served by GET /api/fire-frames (srv/fire_frames.go).

Usage:
  python3 scripts/build_fire_grid_agg.py            # full rebuild (~2 min)
  python3 scripts/build_fire_grid_agg.py --since 2026-06-25   # incremental

Incremental mode deletes and re-aggregates only buckets covering dates >= since
(week/month buckets are refreshed from their bucket start).
"""
import argparse
import sqlite3
import sys
import time
from datetime import datetime, timedelta

DB = 'db.sqlite3'
RES = 0.1

DDL = """
CREATE TABLE IF NOT EXISTS {t} (
  d TEXT NOT NULL, xi INTEGER NOT NULL, yi INTEGER NOT NULL,
  n INTEGER NOT NULL, frp REAL NOT NULL,
  PRIMARY KEY (d, xi, yi)
) WITHOUT ROWID;
"""

SEL_DAY = f"""
SELECT acq_date, CAST(round(longitude/{RES}) AS INTEGER), CAST(round(latitude/{RES}) AS INTEGER),
       COUNT(*), COALESCE(SUM(frp),0)
FROM fire_detections {{where}}
GROUP BY 1,2,3
"""


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', help='Incremental: refresh buckets covering dates >= YYYY-MM-DD')
    ap.add_argument('--db', default=DB)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=60)
    conn.execute('PRAGMA journal_mode=WAL')
    for t in ('fire_grid_day', 'fire_grid_week', 'fire_grid_month'):
        conn.execute(DDL.format(t=t))

    t0 = time.time()
    if args.since:
        since = datetime.strptime(args.since, '%Y-%m-%d').date()
        week_start = (since - timedelta(days=(since.weekday()))).isoformat()
        month_start = since.replace(day=1).isoformat()
        log(f"Incremental refresh since {since} (week from {week_start}, month from {month_start})")

        conn.execute("DELETE FROM fire_grid_day WHERE d >= ?", (since.isoformat(),))
        conn.execute(
            "INSERT INTO fire_grid_day " + SEL_DAY.format(where="WHERE acq_date >= ?"),
            (since.isoformat(),))

        conn.execute("DELETE FROM fire_grid_week WHERE d >= ?", (week_start,))
        conn.execute("""
            INSERT INTO fire_grid_week
            SELECT date(d,'weekday 0','-6 days') AS wd, xi, yi, SUM(n), SUM(frp)
            FROM fire_grid_day WHERE d >= ? GROUP BY 1,2,3""", (week_start,))

        conn.execute("DELETE FROM fire_grid_month WHERE d >= ?", (month_start,))
        conn.execute("""
            INSERT INTO fire_grid_month
            SELECT strftime('%Y-%m-01',d), xi, yi, SUM(n), SUM(frp)
            FROM fire_grid_day WHERE d >= ? GROUP BY 1,2,3""", (month_start,))
    else:
        log("Full rebuild of fire_grid_day/week/month")
        conn.execute("DELETE FROM fire_grid_day")
        conn.execute("INSERT INTO fire_grid_day " + SEL_DAY.format(where=""))
        conn.execute("DELETE FROM fire_grid_week")
        conn.execute("""
            INSERT INTO fire_grid_week
            SELECT date(d,'weekday 0','-6 days'), xi, yi, SUM(n), SUM(frp)
            FROM fire_grid_day GROUP BY 1,2,3""")
        conn.execute("DELETE FROM fire_grid_month")
        conn.execute("""
            INSERT INTO fire_grid_month
            SELECT strftime('%Y-%m-01',d), xi, yi, SUM(n), SUM(frp)
            FROM fire_grid_day GROUP BY 1,2,3""")

    conn.commit()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ('fire_grid_day', 'fire_grid_week', 'fire_grid_month')}
    log(f"Done in {time.time()-t0:.1f}s: {counts}")
    conn.close()


if __name__ == '__main__':
    sys.exit(main())
