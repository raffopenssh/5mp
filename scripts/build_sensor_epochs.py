#!/usr/bin/env python3
"""Measure the satellite fleet behind the fire archive, per month (F11).

Every raw fire chart has a step at 2024-01-01 that is instrument, not
landscape: one VIIRS sensor before, three after. The app must be able to SAY
so on the chart, and to say it without anybody typing "three" — the fleet is a
property of the ingest history, which grows nightly (AGENTS.md invariant 2).

This is the writer for `fire_sensor_epochs` (db/migrations/056). It is a full
scan of fire_detections (~90 s over 42.9M rows, there is no index on
`satellite` and one would cost more than this script does), so it runs from
cron monthly and after any bulk import, not per request.

    python3 scripts/build_sensor_epochs.py            # rebuild all months
    python3 scripts/build_sensor_epochs.py --since 2026-01   # only recent
    python3 scripts/build_sensor_epochs.py --dry-run

A month whose detections all name one sensor is reported with its detection
count so a reader can tell a real single-sensor month from a thin one.
Invariant 1: a scan that yields no months where months existed exits non-zero
as UNFINISHED and writes nothing, so the caller retries rather than freezing
an empty fleet as the answer.
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "db.sqlite3"


def scan(conn, since):
    sql = ("SELECT substr(acq_date,1,7) m, satellite, COUNT(*) "
           "FROM fire_detections WHERE acq_date IS NOT NULL AND acq_date != ''")
    params = []
    if since:
        sql += " AND acq_date >= ?"
        params.append(since + "-01")
    sql += " GROUP BY m, satellite"
    months = {}
    for m, sat, n in conn.execute(sql, params):
        if not m or len(m) != 7:
            continue
        rec = months.setdefault(m, {"sensors": set(), "detections": 0})
        rec["detections"] += n
        if sat:
            rec["sensors"].add(sat)
    return months


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="YYYY-MM; only rebuild months >= this")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    prior = conn.execute("SELECT COUNT(*) FROM fire_sensor_epochs").fetchone()[0]
    months = scan(conn, args.since)

    if not months:
        # invariant 1: nothing matched is not an answer.
        print("UNFINISHED: no months matched; nothing written", file=sys.stderr)
        return 1
    if prior and not args.since and len(months) < prior:
        print(f"UNFINISHED: scan produced {len(months)} months where "
              f"{prior} existed; nothing written", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [(m, ",".join(sorted(r["sensors"])), len(r["sensors"]),
             r["detections"], now) for m, r in sorted(months.items())]

    prev = None
    for m, sensors, n, det, _ in rows:
        mark = "  <-- fleet changes here" if prev is not None and sensors != prev else ""
        if mark or args.dry_run:
            print(f"{m}  {n} sensor(s) [{sensors:12}] {det:10,} detections{mark}")
        prev = sensors

    if args.dry_run:
        print(f"\n(dry run) {len(rows)} month(s) would be written")
        return 0

    conn.executemany(
        "INSERT INTO fire_sensor_epochs (month, sensors, sensor_count, detections, computed_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(month) DO UPDATE SET "
        "sensors=excluded.sensors, sensor_count=excluded.sensor_count, "
        "detections=excluded.detections, computed_at=excluded.computed_at", rows)
    conn.commit()
    print(f"\nwrote {len(rows)} month(s) to fire_sensor_epochs "
          f"({rows[0][0]} .. {rows[-1][0]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
