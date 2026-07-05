#!/usr/bin/env python3
"""
Canonical single-park assignment for fire detections.

Assigns each point to exactly ONE park: the park whose boundary polygon is
nearest, if within ASSIGN_MAX_DIST_KM (100km). Ties broken by park_id.

Replaces the old bbox+first-match logic (_find_park in daily_fire_update.py,
BUFFER_DEG loops in extract_*_fires scripts) which caused:
- rectangular bbox seams in trajectories
- the same fire ingested into multiple parks where buffers overlap
  -> duplicated, differently-fragmented fire groups

Usage:
    from park_assigner import ParkAssigner
    pa = ParkAssigner()
    park_id, dist_km = pa.assign(lon, lat)   # (None, None) if > 100km from all
    results = pa.assign_many([(lon, lat), ...])
"""

import json
import math
from pathlib import Path

from shapely.geometry import shape, Point
from shapely.strtree import STRtree
from shapely import prepared

BASE_DIR = Path(__file__).parent.parent
KEYSTONES_FILE = BASE_DIR / "data" / "keystones_with_boundaries.json"

ASSIGN_MAX_DIST_KM = 100  # single source of truth for the fire ingest buffer

# Rough deg->km. Distances are computed in degrees then scaled; at African
# latitudes (35S..37N) longitude shrink is <= ~20%, and since we compare
# *relative* distances between nearby parks the ranking error is negligible.
KM_PER_DEG = 111.0


class ParkAssigner:
    def __init__(self, keystones_file=KEYSTONES_FILE, max_dist_km=ASSIGN_MAX_DIST_KM):
        self.max_dist_km = max_dist_km
        self.max_dist_deg = max_dist_km / KM_PER_DEG
        self.park_ids = []
        geoms = []
        with open(keystones_file) as f:
            parks = json.load(f)
        # Sort by id for deterministic tie-breaking
        for p in sorted(parks, key=lambda x: x['id']):
            g = p.get('geometry')
            if not g:
                continue
            try:
                geom = shape(g)
                if not geom.is_valid:
                    geom = geom.buffer(0)
            except Exception:
                continue
            self.park_ids.append(p['id'])
            geoms.append(geom)
        self.geoms = geoms
        self.tree = STRtree(geoms)
        self.prepared = [prepared.prep(g) for g in geoms]

    def assign(self, lon, lat):
        """Return (park_id, dist_km) for the nearest park within max_dist_km,
        or (None, None). dist_km is 0.0 if the point is inside the park."""
        pt = Point(lon, lat)
        # Query candidates whose envelope is within max_dist_deg
        idxs = self.tree.query(pt.buffer(self.max_dist_deg))
        best_id, best_dist = None, None
        lat_scale = max(math.cos(math.radians(lat)), 0.3)
        for i in sorted(idxs, key=lambda i: self.park_ids[i]):
            i = int(i)
            if self.prepared[i].contains(pt):
                return self.park_ids[i], 0.0
            d_deg = self.geoms[i].distance(pt)
            # Correct for longitude shrink (approx): scale by average factor
            d_km = d_deg * KM_PER_DEG * ((1 + lat_scale) / 2)
            if d_km <= self.max_dist_km and (best_dist is None or d_km < best_dist):
                best_id, best_dist = self.park_ids[i], round(d_km, 1)
        return best_id, best_dist

    def assign_many(self, points):
        """points: iterable of (lon, lat). Returns list of (park_id, dist_km)."""
        return [self.assign(lon, lat) for lon, lat in points]

    def dist_to_park_km(self, park_id, lon, lat):
        """Distance from a point to a specific park's boundary (0 if inside)."""
        try:
            i = self.park_ids.index(park_id)
        except ValueError:
            return None
        pt = Point(lon, lat)
        if self.prepared[i].contains(pt):
            return 0.0
        lat_scale = max(math.cos(math.radians(lat)), 0.3)
        return round(self.geoms[i].distance(pt) * KM_PER_DEG * ((1 + lat_scale) / 2), 1)


if __name__ == '__main__':
    # Smoke test
    pa = ParkAssigner()
    print(f"Loaded {len(pa.park_ids)} parks")
    tests = [
        ("inside Chinko", 23.63, 6.49),
        ("~60km SW of Chinko", 22.8, 5.8),
        ("far from any park (mid-Sahara)", 10.0, 25.0),
        ("Virunga/QE border zone", 29.6, -0.3),
    ]
    for name, lon, lat in tests:
        print(f"  {name}: {pa.assign(lon, lat)}")
