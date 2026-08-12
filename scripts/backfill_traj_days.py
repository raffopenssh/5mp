#!/usr/bin/env python3
"""Backfill feature_geometries.traj_days from data/fire_groups_v5/*.json.

One-off (2026-08-12) companion to migration 051. Every later write comes from
load_fire_groups_to_db.py, which now fills the column at ingest time.

A row whose group is missing from the JSON keeps traj_days NULL; the server
treats NULL as "evenly spaced across start_date..end_date", which is a
degradation of the animation's timing, never a loss of the trajectory.
"""
import json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from load_fire_groups_to_db import day_offsets, DB_PATH, INPUT_DIR

def main():
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    files = sorted(INPUT_DIR.glob("*.json"))
    total = 0
    for i, f in enumerate(files, 1):
        park = f.stem
        try:
            groups = json.load(open(f))
        except Exception as e:
            print(f"  ! {park}: {e}")
            continue
        rows = []
        for g in groups:
            traj = g.get('trajectory') or []
            fid = g.get('feature_id')
            if not fid or not traj:
                continue
            rows.append((json.dumps(day_offsets(traj, g.get('start_date', '')),
                                    separators=(',', ':')), fid))
        for start in range(0, len(rows), 500):
            conn.executemany(
                "UPDATE feature_geometries SET traj_days = ? "
                "WHERE feature_type = 'fire_trajectory' AND feature_id = ?",
                rows[start:start + 500])
            conn.commit()
        total += len(rows)
        print(f"[{i}/{len(files)}] {park}: {len(rows)}", flush=True)
    conn.close()
    print("updated", total)

if __name__ == '__main__':
    main()
