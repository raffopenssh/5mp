#!/usr/bin/env python3
"""
Backfill feature_geometries.dist_to_park_km for fire_trajectory rows where
it is NULL (old groups whose fire_groups_v5 JSON predates the field).

dist = 0 if pct_inside > 0 or any trajectory point inside the park polygon,
else min distance over trajectory points to the boundary (ParkAssigner
metric). Also writes the value back into properties_json.
"""
import json, sqlite3, sys
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent))
from park_assigner import ParkAssigner

DB = Path(__file__).parent.parent / "db.sqlite3"

def log(m): print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)

pa = ParkAssigner()
log(f"ParkAssigner loaded ({len(pa.park_ids)} parks)")
con = sqlite3.connect(str(DB))
rows = con.execute("""
    SELECT id, park_id, geojson, properties_json
    FROM feature_geometries
    WHERE feature_type='fire_trajectory' AND dist_to_park_km IS NULL
""").fetchall()
log(f"{len(rows):,} rows to backfill")

updates = []
for rid, park_id, gj, pj in rows:
    props = json.loads(pj) if pj else {}
    if (props.get('pct_inside') or 0) > 0:
        d = 0.0
    else:
        try:
            geom = json.loads(gj)
            if geom.get('type') == 'Feature':
                geom = geom.get('geometry', {})
            t = geom.get('type')
            c = geom.get('coordinates', [])
            if t == 'Point':
                coords = [c]
            elif t in ('LineString', 'MultiPoint'):
                coords = c
            elif t == 'MultiLineString':
                coords = [p for line in c for p in line]
            else:
                coords = []
            dists = [pa.dist_to_park_km(park_id, p[0], p[1]) for p in coords]
            dists = [x for x in dists if x is not None]
            d = min(dists) if dists else None
        except Exception:
            d = None
    if d is not None:
        props['dist_to_park_km'] = d
        updates.append((d, json.dumps(props), rid))
    if len(updates) % 20000 == 0 and updates:
        log(f"  computed {len(updates):,}...")

log(f"Writing {len(updates):,} updates...")
con.executemany("UPDATE feature_geometries SET dist_to_park_km=?, properties_json=? WHERE id=?", updates)
con.commit()
n = con.execute("SELECT SUM(dist_to_park_km IS NULL) FROM feature_geometries WHERE feature_type='fire_trajectory'").fetchone()[0]
log(f"DONE. Remaining NULL: {n}")
