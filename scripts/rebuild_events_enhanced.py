#!/usr/bin/env python3
"""
Rebuild deforestation_events and park_settlements from polygon data.
Enhanced version with road proximity detection for linear clearing patterns.

Uses:
- HydroRIVERS (park_rivers_hydro) for river context
- HydroLAKES (park_lakes_hydro) for lake context  
- OSM places (osm_places) for settlement context
- HeiGIT roads (roads_heigit) for road proximity / linear patterns
- Climate data for seasonality
"""

import json
import sqlite3
import math
from pathlib import Path
from collections import defaultdict
from datetime import datetime

DB_PATH = Path('db.sqlite3')
CLIMATE_FILE = Path('data/climate/park_climate.json')
SETTLEMENT_DIR = Path('data/settlement_events')
DEFOREST_DIR = Path('data/deforestation_events')

# Clustering parameters
SETTLEMENT_CLUSTER_KM = 2.0
DEFORESTATION_CLUSTER_KM = 5.0

# Maximum bbox diagonal of one cluster, in km. Single linkage has no diameter
# bound: at park scale nothing chained, at AOI scale 52,454 built-up polygons
# chained into ONE "town" spanning 270 x 275 km (docs/AOI_STRUCTURAL_FIXES.md
# F3). A settlement larger than this is not a settlement, so a cluster that
# exceeds it is split on a grid until every part fits. 15 km comfortably holds
# the largest real town in these landscapes and is an order of magnitude below
# the runaway.
MAX_CLUSTER_DIAMETER_KM = 15.0

# Distance beyond which "Near <river>" is not said at all. A river 700 km away
# is not context (F5).
RIVER_CONTEXT_MAX_KM = 10.0

def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance in km between two points"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def point_to_line_distance(px, py, x1, y1, x2, y2):
    """Distance from point (px,py) to line segment (x1,y1)-(x2,y2) in degrees"""
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.sqrt((px - x1)**2 + (py - y1)**2)
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx*dx + dy*dy)))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.sqrt((px - proj_x)**2 + (py - proj_y)**2)

class _RoadIndex:
    """Grid-bucketed road segments, for nearest-road queries.

    A park has ~1,400 roads and a linear scan is fine; an AOI has 12,956 and it
    is not, because the caller runs one query per deforestation polygon. This is
    the same grid trick `_cluster_polygons` uses, for the same reason: it was
    added when fixing the AOI's OSM key made the road count jump 92x
    (AGENTS.md "Areas of interest") and turned this into the new bottleneck.

    A segment is registered in every cell its bbox touches, or a long way would
    be missing from the cells it passes through. `nearest()` searches outward in
    shells and only stops once the searched box provably extends past the best
    hit, so it is exact, not approximate.
    """

    CELL = 0.05     # ~5.5 km
    MAX_RING = 5    # search out to ~27 km, then give up

    def __init__(self, roads):
        self.roads = roads
        self.cells = defaultdict(list)
        for road in roads:
            coords = road.get('coords', [])
            for i in range(len(coords) - 1):
                seg = (coords[i], coords[i + 1], road)
                x1, y1 = coords[i]
                x2, y2 = coords[i + 1]
                for cy in range(int(min(y1, y2) // self.CELL),
                                int(max(y1, y2) // self.CELL) + 1):
                    for cx in range(int(min(x1, x2) // self.CELL),
                                    int(max(x1, x2) // self.CELL) + 1):
                        self.cells[(cy, cx)].append(seg)

    def __len__(self):
        return len(self.roads)

    def nearest(self, lat, lon):
        """(km, road) to the nearest road, or (None, None) beyond MAX_RING.

        Exact within the searched radius: it keeps widening until the searched
        box provably extends past the best hit found. Beyond ~27 km it returns
        None, which the one consumer (_check_linear_pattern, threshold 0.5 km)
        already treats as "no road near this polygon" — so the cap is invisible
        to behaviour and is what keeps a remote polygon in a roadless park from
        scanning the whole grid.
        """
        cy, cx = int(lat // self.CELL), int(lon // self.CELL)
        best, best_road = float('inf'), None
        for ring in range(0, self.MAX_RING + 1):
            # Only the shell added by this ring, not the filled box.
            if ring == 0:
                shell = [(0, 0)]
            else:
                shell = [(dy, dx)
                         for dy in range(-ring, ring + 1)
                         for dx in range(-ring, ring + 1)
                         if max(abs(dy), abs(dx)) == ring]
            for dy, dx in shell:
                for (p1, p2, road) in self.cells.get((cy + dy, cx + dx), ()):
                    d = point_to_line_distance(lon, lat, p1[0], p1[1],
                                               p2[0], p2[1])
                    if d < best:
                        best, best_road = d, road
            if best_road is not None and best <= ring * self.CELL:
                break
        if best_road is None:
            return None, None
        return best * 111.0, best_road


def _area_method_for(id_prefix, year):
    """Which quantity a deforestation row's `area_km2` holds.

    `deforestation_events.area_km2` carries mapped canopy loss for Hansen rows
    (2001-2023) and `alerts x KM2_PER_ALERT` for GFW rows (2024+). Drawn as one
    series that showed 313.6 km² in 2023 -> 0.7 km² in 2024, which reads as a
    99.8% collapse and is purely a unit change
    (docs/AOI_STRUCTURAL_FIXES.md F8, AGENTS.md invariant 7).

    The writer's id prefix is the fact; the year is the fallback for the
    original 2026-02 run, whose rows carry the bare `deforest_` prefix and are
    Hansen by definition (the cutover is Hansen <=2023 / alerts >=2024).
    """
    if id_prefix and 'gfw' in id_prefix:
        return 'gfw_alert_count'
    if id_prefix and 'hansen' in id_prefix:
        return 'hansen_canopy_loss'
    return 'gfw_alert_count' if year and year >= 2024 else 'hansen_canopy_loss'


def _cluster_diameter_km(cluster):
    """Diagonal of a cluster's bounding box, in km."""
    if len(cluster) < 2:
        return 0.0
    lats = [p['lat'] for p in cluster]
    lons = [p['lon'] for p in cluster]
    return haversine(min(lats), min(lons), max(lats), max(lons))


def _split_oversized(cluster, max_km):
    """Split a cluster whose bbox diagonal exceeds max_km into grid tiles.

    Single linkage has no diameter bound, so with a 2 km link distance a chain
    of villages 2 km apart becomes one 270 km "town"
    (docs/AOI_STRUCTURAL_FIXES.md F3). A cluster bigger than the largest real
    settlement in the region is a bug the pipeline can see for itself, and this
    is it seeing it: bucket the members onto a max_km/sqrt(2) grid so every
    part provably fits inside max_km, and recurse only until it does.

    Deliberately a *grid*, not another linkage pass: the members are already
    known to be mutually reachable, so any re-linkage would reproduce the same
    chain. The grid is the only thing that bounds the extent, and it is
    deterministic, so a re-run reproduces the same split.
    """
    if len(cluster) < 2 or _cluster_diameter_km(cluster) <= max_km:
        return [cluster]
    # A cell of side s has diagonal s*sqrt(2); pick s so that is <= max_km.
    side_deg = (max_km / 1.4143) / 111.0
    buckets = defaultdict(list)
    for p in cluster:
        lat_cell = int(p['lat'] // side_deg)
        # Longitude degrees shrink with latitude; widen the cell so a cell is
        # square on the ground rather than square in degrees.
        lon_deg = side_deg / max(math.cos(math.radians(p['lat'])), 0.1)
        buckets[(lat_cell, int(p['lon'] // lon_deg))].append(p)
    if len(buckets) == 1:
        # Everything landed in one cell yet the bbox is too big: only possible
        # from a degenerate coordinate. Return as-is rather than recursing
        # forever -- an infinite loop is a worse answer than a wide cluster.
        return [cluster]
    return [part
            for b in buckets.values()
            for part in _split_oversized(b, max_km)]


class _PointIndex:
    """Grid-bucketed points, for exact nearest-point queries.

    `_load_park_places` used to carry `LIMIT 100` after an ORDER BY on place
    *type*, so "nearest place" was really "nearest of the 100 biggest places".
    For a park that is most of them; for XSA_Study_Area it was 100 of 971, all
    cities and towns, and the stored distance was overstated by a median of
    67 km (docs/AOI_STRUCTURAL_FIXES.md F4). Dropping the LIMIT makes the query
    honest and makes a linear scan per event the new cost, hence this index --
    the same shape as _RoadIndex, and exact for the same reason: it widens
    until the searched box provably extends past the best hit.
    """

    CELL = 0.1      # ~11 km
    MAX_RING = 30   # ~330 km; beyond that a "nearest place" is not context

    def __init__(self, points):
        self.points = points
        self.cells = defaultdict(list)
        for p in points:
            self.cells[(int(p['lat'] // self.CELL),
                        int(p['lon'] // self.CELL))].append(p)

    def __len__(self):
        return len(self.points)

    def __bool__(self):
        return bool(self.points)

    def __iter__(self):
        return iter(self.points)

    def nearest(self, lat, lon):
        """(point, km) or (None, None)."""
        cy, cx = int(lat // self.CELL), int(lon // self.CELL)
        best, best_p = float('inf'), None
        for ring in range(0, self.MAX_RING + 1):
            if ring == 0:
                shell = [(0, 0)]
            else:
                shell = [(dy, dx)
                         for dy in range(-ring, ring + 1)
                         for dx in range(-ring, ring + 1)
                         if max(abs(dy), abs(dx)) == ring]
            for dy, dx in shell:
                for p in self.cells.get((cy + dy, cx + dx), ()):
                    d = haversine(lat, lon, p['lat'], p['lon'])
                    if d < best:
                        best, best_p = d, p
            # A ring's inner boundary is `ring * CELL` degrees away; convert
            # with the tighter of the two axes (lat, 111 km/deg) so the bound
            # is conservative.
            if best_p is not None and best <= ring * self.CELL * 111.0:
                break
        if best_p is None:
            return None, None
        return best_p, best


class EventRebuilder:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = sqlite3.Row
        self.climate = self._load_climate()
        self.rivers_cache = {}
        self.lakes_cache = {}
        self.places_cache = {}
        self.roads_cache = {}
        
    def _load_climate(self):
        if CLIMATE_FILE.exists():
            with open(CLIMATE_FILE) as f:
                return json.load(f)
        return {}
    
    def _load_park_rivers(self, park_id):
        """Named rivers from park_rivers_hydro, as a segment index.

        No LIMIT. It used to take the 20 longest and `_get_nearest_river`
        returned `rivers[0]` without looking at the point, so 1,552 of 1,552
        XSA settlements and 7,814 of 7,815 deforestation events said "Near
        Mbomou river" -- including events 700 km from it
        (docs/AOI_STRUCTURAL_FIXES.md F5). The nearest river is now a real
        nearest-segment query, which needs every river, which needs an index.
        """
        if park_id in self.rivers_cache:
            return self.rivers_cache[park_id]

        segs = []
        cursor = self.conn.execute("""
            SELECT name, stream_order, length_km, geojson
            FROM park_rivers_hydro
            WHERE park_id = ? AND name IS NOT NULL AND name != ''
              AND geojson IS NOT NULL
            ORDER BY stream_order DESC, length_km DESC
        """, (park_id,))
        for row in cursor:
            try:
                gj = json.loads(row['geojson'])
            except Exception:
                continue
            if not gj:
                continue
            if gj.get('type') == 'LineString':
                parts = [gj.get('coordinates', [])]
            elif gj.get('type') == 'MultiLineString':
                parts = gj.get('coordinates', [])
            else:
                continue
            river = {'name': row['name'], 'order': row['stream_order'],
                     'length_km': row['length_km']}
            for coords in parts:
                if len(coords) >= 2:
                    segs.append({**river, 'coords': coords})
        index = _RoadIndex(segs)
        self.rivers_cache[park_id] = index
        return index
    
    def _load_park_lakes(self, park_id):
        """Load lakes from park_lakes_hydro"""
        if park_id in self.lakes_cache:
            return self.lakes_cache[park_id]
        
        lakes = []
        cursor = self.conn.execute("""
            SELECT name, area_km2, centroid_lat, centroid_lon
            FROM park_lakes_hydro
            WHERE park_id = ? AND name IS NOT NULL AND name != ''
            ORDER BY area_km2 DESC
            LIMIT 10
        """, (park_id,))
        for row in cursor:
            lakes.append({
                'name': row['name'],
                'area_km2': row['area_km2'],
                'lat': row['centroid_lat'],
                'lon': row['centroid_lon']
            })
        self.lakes_cache[park_id] = lakes
        return lakes
    
    def _load_park_places(self, park_id):
        """Every named OSM place, as a spatial index (no LIMIT -- see
        _PointIndex and docs/AOI_STRUCTURAL_FIXES.md F4)."""
        if park_id in self.places_cache:
            return self.places_cache[park_id]

        places = []
        cursor = self.conn.execute("""
            SELECT name, place_type, lat, lon
            FROM osm_places
            WHERE park_id = ? AND name IS NOT NULL AND name != ''
              AND lat IS NOT NULL AND lon IS NOT NULL
        """, (park_id,))
        for row in cursor:
            places.append({
                'name': row['name'],
                'type': row['place_type'],
                'lat': row['lat'],
                'lon': row['lon']
            })
        index = _PointIndex(places)
        self.places_cache[park_id] = index
        return index
    
    def _load_park_roads(self, park_id):
        """Load road geometries for road proximity detection, with a spatial index.

        The index is not an optimisation, it is what makes the AOI case finish.
        `_get_nearest_road_distance` is called once per polygon and used to scan
        every road's every segment; that was tolerable when a park had ~1,400
        roads and fatal at 12,956 (which is what an AOI has once its OSM ingest
        is keyed where the readers look — AGENTS.md "Areas of interest"). Bucketing
        segments into ~0.05° cells confines the search to the 3x3 block around
        the query point, the same trick `_cluster_polygons` uses and for the same
        reason.
        """
        if park_id in self.roads_cache:
            return self.roads_cache[park_id]
        
        roads = []
        cursor = self.conn.execute("""
            SELECT osm_id, name, highway_type, geojson
            FROM roads_heigit
            WHERE park_id = ? AND geojson IS NOT NULL
        """, (park_id,))
        for row in cursor:
            geojson = json.loads(row['geojson']) if row['geojson'] else None
            if geojson and geojson.get('type') == 'LineString':
                roads.append({
                    'osm_id': row['osm_id'],
                    'name': row['name'],
                    'type': row['highway_type'],
                    'coords': geojson.get('coordinates', [])
                })
        roads = _RoadIndex(roads)
        self.roads_cache[park_id] = roads
        return roads
    
    def _get_nearest_road_distance(self, lat, lon, roads):
        """Get distance to nearest road in km."""
        if not roads:
            return None, None
        if isinstance(roads, _RoadIndex):
            return roads.nearest(lat, lon)

        min_dist_deg = float('inf')
        nearest_road = None
        
        for road in roads:
            coords = road.get('coords', [])
            for i in range(len(coords) - 1):
                x1, y1 = coords[i]
                x2, y2 = coords[i + 1]
                dist = point_to_line_distance(lon, lat, x1, y1, x2, y2)
                if dist < min_dist_deg:
                    min_dist_deg = dist
                    nearest_road = road
        
        # Convert degrees to km (approximate)
        dist_km = min_dist_deg * 111.0 if min_dist_deg < float('inf') else None
        return dist_km, nearest_road
    
    def _check_linear_pattern(self, polygons, roads):
        """Check if deforestation follows a linear pattern along roads"""
        if len(polygons) < 3 or not roads:
            return False, 0, None
        
        # Count how many polygons are within 500m of a road
        near_road_count = 0
        total_near_road_dist = 0
        nearest_road = None
        
        for p in polygons:
            dist, road = self._get_nearest_road_distance(p['lat'], p['lon'], roads)
            if dist is not None and dist < 0.5:  # 500m
                near_road_count += 1
                total_near_road_dist += dist
                if nearest_road is None:
                    nearest_road = road
        
        fraction_near_road = near_road_count / len(polygons)
        avg_dist = total_near_road_dist / near_road_count if near_road_count > 0 else None
        
        # Linear if >60% of polygons within 500m of road
        is_linear = fraction_near_road > 0.6
        
        return is_linear, fraction_near_road, nearest_road
    
    def _get_fire_density(self, park_id, year, lat, lon, radius_km=5):
        """Get fire count near a location for a given year.

        ⚠️ The bounds MUST stay as `latitude BETWEEN ? AND ?`, never
        `ABS(latitude - ?) < ?`. Wrapping the indexed column in a function makes
        the term non-sargable, so SQLite abandons idx_fire_location and does a
        covering scan of all 42.9M rows: **5.0 s per call vs 0.04 s**, measured
        2026-08-07. This is called once per deforestation cluster, which is what
        made rebuild_deforestation_for_park hold SQLite's single writer for 2.5+
        hours on the XSA AOI's 76,903 Hansen polygons and turned every
        user-initiated write on the deployment into a 500 (the archive "blocker"
        of AGENTS.md "Areas of interest" was this query, not the handler).
        """
        radius_deg = radius_km / 111.0

        cursor = self.conn.execute("""
            SELECT COUNT(*) as cnt
            FROM fire_detections
            WHERE latitude BETWEEN ? AND ?
              AND longitude BETWEEN ? AND ?
              AND acq_date LIKE ?
        """, (lat - radius_deg, lat + radius_deg,
              lon - radius_deg, lon + radius_deg, f"{year}%"))

        row = cursor.fetchone()
        return row['cnt'] if row else 0
    
    def _get_nearest_place(self, lat, lon, places):
        """(place, km) or (None, None)."""
        if not places:
            return None, None
        if isinstance(places, _PointIndex):
            return places.nearest(lat, lon)

        nearest = None
        min_dist = float('inf')
        for p in places:
            dist = haversine(lat, lon, p['lat'], p['lon'])
            if dist < min_dist:
                min_dist = dist
                nearest = p
        return nearest, min_dist

    def _get_nearest_river(self, lat, lon, rivers,
                           max_km=RIVER_CONTEXT_MAX_KM):
        """The nearest named river WITHIN max_km, or None.

        It used to be `return rivers[0]` -- the longest river anywhere in the
        area, regardless of the point it was asked about
        (docs/AOI_STRUCTURAL_FIXES.md F5). Returning None beyond the threshold
        is the point: the caller then omits the sentence instead of asserting a
        river that is 700 km away.
        """
        if not rivers:
            return None
        if isinstance(rivers, _RoadIndex):
            dist_km, river = rivers.nearest(lat, lon)
            if river is None or dist_km is None or dist_km > max_km:
                return None
            return {**river, 'distance_km': dist_km}
        # A plain list (legacy callers): nearest by centroid, same threshold.
        best, best_r = float('inf'), None
        for r in rivers:
            if r.get('lat') is None:
                continue
            d = haversine(lat, lon, r['lat'], r['lon'])
            if d < best:
                best, best_r = d, r
        return best_r if best_r is not None and best <= max_km else None
    
    def _cluster_polygons(self, polygons, max_dist_km):
        """Single-linkage clustering: polygons within max_dist_km chain together.

        Grid-accelerated. The original was a rescan of every remaining polygon
        against every cluster member, which is fine for a park (a few thousand
        polygons) and hopeless for an AOI (XSA yields ~170k GHSL polygons, and
        the quadratic version does not finish). The partition produced by
        single linkage is unique, so this returns exactly the same clusters --
        only the order of members within a cluster differs.
        """
        if not polygons:
            return []

        # Bucket into cells of the linkage distance, so a polygon's possible
        # neighbours are confined to the 3x3 block around its own cell.
        deg = max_dist_km / 111.0
        cells = defaultdict(list)
        for i, p in enumerate(polygons):
            cells[(int(p['lat'] // deg), int(p['lon'] // deg))].append(i)

        seen = [False] * len(polygons)
        clusters = []
        for start in range(len(polygons)):
            if seen[start]:
                continue
            seen[start] = True
            queue = [start]
            cluster = []
            while queue:
                i = queue.pop()
                p = polygons[i]
                cluster.append(p)
                cy, cx = int(p['lat'] // deg), int(p['lon'] // deg)
                # A degree of longitude is shorter than a degree of latitude,
                # so the linkage radius can reach further than one lon cell.
                span = int(1.0 / max(math.cos(math.radians(p['lat'])), 0.1)) + 1
                for dy in (-1, 0, 1):
                    for dx in range(-span, span + 1):
                        for j in cells.get((cy + dy, cx + dx), ()):
                            if seen[j]:
                                continue
                            q = polygons[j]
                            if haversine(p['lat'], p['lon'], q['lat'], q['lon']) <= max_dist_km:
                                seen[j] = True
                                queue.append(j)
            clusters.append(cluster)

        return [part
                for c in clusters
                for part in _split_oversized(c, MAX_CLUSTER_DIAMETER_KM)]
    
    def _classify_deforestation(self, polygons, park_id, year, fires_near, roads):
        """Classify deforestation with enhanced road detection"""
        
        total_area = sum(p['area_km2'] for p in polygons)
        num_polygons = len(polygons)
        
        # Calculate spatial spread
        if num_polygons > 1:
            lats = [p['lat'] for p in polygons]
            lons = [p['lon'] for p in polygons]
            spread_km = haversine(min(lats), min(lons), max(lats), max(lons))
        else:
            spread_km = 0
        
        avg_size = total_area / num_polygons if num_polygons > 0 else 0
        fire_ratio = fires_near / max(total_area, 0.01)
        
        # Check for linear road pattern
        is_linear, road_fraction, nearest_road = self._check_linear_pattern(polygons, roads)
        
        # Classification logic
        classification = 'unknown'
        confidence = 0.5
        pattern = 'scattered'
        
        if fire_ratio > 50:
            classification = 'slash_burn'
            confidence = 0.85
            pattern = 'fire_associated'
        elif is_linear and road_fraction > 0.7:
            classification = 'logging'
            confidence = 0.8
            pattern = 'linear_road'
        elif is_linear:
            classification = 'logging'
            confidence = 0.7
            pattern = 'linear'
        elif spread_km > 5 and avg_size < 0.1 and num_polygons > 5:
            classification = 'logging'
            confidence = 0.65
            pattern = 'linear'
        elif avg_size > 0.5 and num_polygons < 3:
            classification = 'encroachment'
            confidence = 0.6
            pattern = 'concentrated'
        elif spread_km < 2 and num_polygons > 3:
            classification = 'encroachment'
            confidence = 0.65
            pattern = 'clustered'
        elif fires_near == 0 and total_area < 0.5:
            classification = 'natural'
            confidence = 0.5
            pattern = 'scattered'
        else:
            classification = 'encroachment'
            confidence = 0.4
            pattern = 'scattered'
        
        return {
            'classification': classification,
            'confidence': confidence,
            'pattern': pattern,
            'total_area_km2': total_area,
            'num_polygons': num_polygons,
            'spread_km': spread_km,
            'avg_polygon_size': avg_size,
            'fires_nearby': fires_near,
            'fire_ratio': fire_ratio,
            'is_linear': is_linear,
            'road_fraction': road_fraction,
            'nearest_road': nearest_road.get('name') if nearest_road else None
        }
    
    def _generate_deforestation_narrative(self, park_name, year, classification, 
                                           nearest_place, nearest_river, climate_data):
        """Generate rich narrative for deforestation event"""
        
        parts = []
        cls = classification['classification']
        pattern = classification['pattern']
        total_area = classification['total_area_km2']
        num_poly = classification['num_polygons']
        
        # Main description
        cls_desc = {
            'slash_burn': 'Slash-and-burn clearing',
            'logging': 'Logging activity',
            'encroachment': 'Forest encroachment',
            'natural': 'Natural forest loss',
            'unknown': 'Forest loss'
        }
        parts.append(f"{cls_desc.get(cls, 'Forest loss')} detected in {year}.")
        parts.append(f"Affected {total_area:.2f} km² across {num_poly} {'patch' if num_poly == 1 else 'patches'}.")
        
        # Pattern description
        if pattern == 'linear_road':
            road_name = classification.get('nearest_road')
            if road_name:
                parts.append(f"Linear pattern following {road_name} road suggests logging access route.")
            else:
                parts.append("Linear pattern along road suggests logging access route.")
        elif pattern == 'linear':
            parts.append("Linear clearing pattern indicates organized logging activity.")
        elif pattern == 'fire_associated':
            parts.append("Strong fire correlation indicates agricultural burning.")
        elif pattern == 'clustered':
            parts.append("Clustered pattern near settlement suggests encroachment.")
        elif pattern == 'concentrated':
            parts.append("Concentrated clearing suggests single development event.")
        
        # Location context
        if nearest_place:
            place, dist = nearest_place
            parts.append(f"Located {dist:.1f}km from {place['name']}.")
        
        if nearest_river:
            # Distance included, and the sentence omitted entirely beyond
            # RIVER_CONTEXT_MAX_KM by _get_nearest_river -- it used to name the
            # longest river in the whole area for every event, including ones
            # 700 km away (docs/AOI_STRUCTURAL_FIXES.md F5).
            parts.append(f"Near {nearest_river['name']} river "
                         f"({nearest_river.get('distance_km', 0):.1f}km).")
        
        return ' '.join(parts)
    
    def load_deforestation_polygons(self, park_id=None, id_prefix=None):
        """{(park_id, year): [polygon dicts]} from feature_geometries.

        id_prefix scopes the read to one writer's rows. feature_geometries
        deforestation for a single park_id can come from several writers with
        disjoint id prefixes (`deforest_` the original park run,
        `deforest_hansen_` <=2023, `deforest_gfw_` >=2024), and a per-writer
        rebuild must see only its own or it would re-cluster and duplicate
        somebody else's events.
        """
        sql = """
            SELECT park_id, feature_id,
                   json_extract(properties_json, '$.year') as year,
                   json_extract(properties_json, '$.area_km2') as area_km2,
                   json_extract(properties_json, '$.lat') as lat,
                   json_extract(properties_json, '$.lon') as lon
            FROM feature_geometries
            WHERE feature_type = 'deforestation'"""
        args = []
        if park_id:
            sql += " AND park_id = ?"
            args.append(park_id)
        if id_prefix:
            sql += " AND feature_id LIKE ?"
            args.append(id_prefix + '%')
        sql += " ORDER BY park_id, year"

        park_year_polygons = defaultdict(list)
        for row in self.conn.execute(sql, args):
            year = int(row['year']) if row['year'] else 0
            if year == 0:
                continue
            park_year_polygons[(row['park_id'], year)].append({
                'feature_id': row['feature_id'],
                'area_km2': float(row['area_km2']) if row['area_km2'] else 0,
                'lat': float(row['lat']) if row['lat'] else 0,
                'lon': float(row['lon']) if row['lon'] else 0,
            })
        return park_year_polygons

    def rebuild_deforestation_for_park(self, park_id, polygons_by_year=None,
                                       id_prefix=None, delete=True,
                                       on_batch=None, batch=200):
        """Cluster + classify one park's (or AOI's) deforestation polygons.

        The mirror of rebuild_settlements_for_park, and split out for the same
        reason: a park onboarding and an AOI's Hansen unit both need ONE park's
        events rebuilt through the canonical classifier rather than a second
        copy of it (docs/PLAN_AOI_OVERLAY.md §4.3).

        `id_prefix` makes the rebuild belong to a single writer: it both scopes
        which polygons are read and which existing events are deleted, so the
        Hansen unit (<=2023) and the GFW-alert unit (>=2024) can own rows in
        the same table for the same park without either erasing the other.
        `delete=False` for the global rebuild, which has already truncated.

        **It commits every `batch` events and calls `on_batch(count)` between
        batches**, the same way hansen_loss.ingest() flushes per window. One
        transaction around the whole rebuild is what made an AOI-sized input
        (76,903 polygons) hold SQLite's single writer for hours, so nothing
        else on the deployment — not the nightly park refresh, not a user
        flipping a toggle — could get a write slot (AGENTS.md "Areas of interest").
        Committing per batch is safe because a re-run is idempotent: the delete
        above is prefix-scoped and re-derives the same clusters.

        `on_batch` is also the interrupt point: raise from it (the AOI runner
        raises Interrupted) to stop between batches with everything so far
        committed.
        """
        if polygons_by_year is None:
            polygons_by_year = self.load_deforestation_polygons(
                park_id, id_prefix=id_prefix)
        if delete:
            # Before the empty-input early return, not after: a rebuild that
            # now yields nothing must not leave the old rows immortal
            # (AGENTS.md, "a park with zero groups is a real state").
            if id_prefix:
                self.conn.execute(
                    "DELETE FROM deforestation_events WHERE park_id = ? "
                    "AND polygon_ids LIKE ?", (park_id, id_prefix + '%'))
            else:
                self.conn.execute(
                    "DELETE FROM deforestation_events WHERE park_id = ?",
                    (park_id,))
            self.conn.commit()
        if not polygons_by_year:
            return 0

        places = self._load_park_places(park_id)
        rivers = self._load_park_rivers(park_id)
        roads = self._load_park_roads(park_id)
        climate = self.climate.get(park_id, {})
        park_name = ' '.join(park_id.split('_')[1:]).replace('_', ' ')

        count, linear_count = 0, 0
        for (_pid, year), polygons in sorted(polygons_by_year.items()):
            clusters = self._cluster_polygons(polygons, DEFORESTATION_CLUSTER_KM)
            for cluster in clusters:
                avg_lat = sum(p['lat'] for p in cluster) / len(cluster)
                avg_lon = sum(p['lon'] for p in cluster) / len(cluster)

                fires_near = self._get_fire_density(park_id, year, avg_lat,
                                                    avg_lon, radius_km=10)
                classification = self._classify_deforestation(
                    cluster, park_id, year, fires_near, roads)
                if classification['is_linear']:
                    linear_count += 1

                nearest_place = self._get_nearest_place(avg_lat, avg_lon, places)
                nearest_river = self._get_nearest_river(avg_lat, avg_lon, rivers)
                narrative = self._generate_deforestation_narrative(
                    park_name, year, classification,
                    nearest_place, nearest_river, climate)

                self.conn.execute("""
                    INSERT INTO deforestation_events
                    (park_id, year, area_km2, lat, lon, pattern_type, classification,
                     classification_confidence, narrative, fires_same_year, fire_ratio,
                     polygon_ids, pixel_count, classified_at, area_method)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    park_id, year, classification['total_area_km2'],
                    avg_lat, avg_lon, classification['pattern'],
                    classification['classification'], classification['confidence'],
                    narrative, fires_near, classification['fire_ratio'],
                    ','.join(p['feature_id'] for p in cluster),
                    classification['num_polygons'],
                    datetime.now().isoformat(),
                    # F8: Hansen mapped canopy loss and GFW alert counts scaled
                    # by KM2_PER_ALERT are different quantities in one column.
                    # Derived from the writer's own id prefix, never typed.
                    _area_method_for(id_prefix, year)
                ))
                count += 1
                # Release the write lock between batches so cron jobs and user
                # toggles can interleave; on_batch may raise to interrupt.
                if on_batch and count % batch == 0:
                    self.conn.commit()
                    on_batch(count)
        self.conn.commit()
        self.linear_count = linear_count
        return count

    def rebuild_deforestation(self):
        """Rebuild deforestation_events for every park at once."""

        print("=" * 60)
        print("Rebuilding deforestation events (enhanced)")
        print("=" * 60)

        park_year_polygons = self.load_deforestation_polygons()
        print(f"Found {len(park_year_polygons)} park-year combinations")

        by_park = defaultdict(dict)
        for key, polys in park_year_polygons.items():
            by_park[key[0]][key] = polys

        # Clear existing events; the per-park calls then pass delete=False.
        self.conn.execute("DELETE FROM deforestation_events")

        count, linear_count = 0, 0
        for park_id, polys in sorted(by_park.items()):
            print(f"  Processing {park_id}...")
            count += self.rebuild_deforestation_for_park(
                park_id, polys, delete=False)
            linear_count += getattr(self, 'linear_count', 0)

        self.conn.commit()

        print(f"\nCreated {count} deforestation events")
        print(f"  Linear patterns (road-associated): {linear_count}")

        cursor = self.conn.execute("""
            SELECT classification, COUNT(*), SUM(area_km2)
            FROM deforestation_events
            GROUP BY classification
            ORDER BY COUNT(*) DESC
        """)
        print("\nBy classification:")
        for row in cursor:
            print(f"  {row[0]}: {row[1]} events, {row[2]:.1f} km²")

        return count

    def load_settlement_polygons(self, park_id=None):
        """{park_id: [polygon dicts]} from feature_geometries.

        `population_est` is absent from a polygon written after
        docs/AOI_STRUCTURAL_FIXES.md F2 when GHS_POP could not be read, and
        that absence is carried through as **None**, not 0 -- a settlement of
        unknown population is not a settlement of nobody. `extent_m2` is only
        present on polygons written after F1; older ones have the mask area in
        `area_m2`, which is what `area_source` on the cluster then records.
        """
        cursor = self.conn.execute("""
            SELECT park_id, feature_id,
                   json_extract(properties_json, '$.area_m2') as area_m2,
                   json_extract(properties_json, '$.extent_m2') as extent_m2,
                   json_extract(properties_json, '$.population_est') as population_est,
                   json_extract(properties_json, '$.population_source') as population_source,
                   json_extract(properties_json, '$.epoch') as epoch,
                   json_extract(properties_json, '$.lat') as lat,
                   json_extract(properties_json, '$.lon') as lon
            FROM feature_geometries
            WHERE feature_type = 'settlement'"""
            + (" AND park_id = ?" if park_id else "") + " ORDER BY park_id",
            (park_id,) if park_id else ())

        park_polygons = defaultdict(list)
        for row in cursor:
            pop = row['population_est']
            park_polygons[row['park_id']].append({
                'feature_id': row['feature_id'],
                'area_m2': float(row['area_m2']) if row['area_m2'] else 0,
                'extent_m2': (float(row['extent_m2'])
                              if row['extent_m2'] is not None else None),
                'population_est': None if pop is None else int(pop),
                'population_source': row['population_source'],
                'epoch': row['epoch'],
                'lat': float(row['lat']) if row['lat'] else 0,
                'lon': float(row['lon']) if row['lon'] else 0
            })
        return park_polygons

    def rebuild_settlements(self):
        """Rebuild park_settlements with clustering, for every park at once."""
        
        print("\n" + "=" * 60)
        print("Rebuilding settlement events")
        print("=" * 60)
        
        park_polygons = self.load_settlement_polygons()
        print(f"Found polygons for {len(park_polygons)} parks")
        
        # Clear existing settlements -- but only the ones this rebuild can
        # recreate. A row with no polygon_ids came from the retired pit/
        # turbidity detector (3,019 of them, AGENTS.md invariant 5), has no
        # GHSL polygon behind it, and would be destroyed rather than rebuilt.
        # A rebuild is not a purge.
        self.conn.execute("DELETE FROM park_settlements WHERE "
                          "polygon_ids IS NOT NULL AND polygon_ids != ''")
        
        count = 0
        for park_id, polygons in sorted(park_polygons.items()):
            count += self.rebuild_settlements_for_park(park_id, polygons,
                                                       delete=False)
        
        self.conn.commit()
        print(f"Created {count} settlement records")
        
        cursor = self.conn.execute("""
            SELECT classification, COUNT(*)
            FROM park_settlements
            GROUP BY classification
            ORDER BY COUNT(*) DESC
        """)
        print("\nBy classification:")
        for row in cursor:
            print(f"  {row[0]}: {row[1]}")
        
        return count

    def rebuild_settlements_for_park(self, park_id, polygons=None, delete=True,
                                     on_batch=None, batch=200):
        """Cluster + classify one park's (or AOI's) settlement polygons.

        Split out of rebuild_settlements so a single park can be refreshed
        after onboarding, and so an AOI can reach the *canonical* classifier
        rather than growing a second copy of it (docs/PLAN_AOI_OVERLAY.md).
        `delete` scopes the wipe to this park; the global rebuild has already
        truncated the table and passes False.

        Commits every `batch` clusters and calls `on_batch(count)` between
        batches, the mirror of rebuild_deforestation_for_park. One transaction
        around the whole rebuild is what let a single AOI-sized input hold
        SQLite's only writer for its entire run, so nothing else on the
        deployment -- not the nightly refresh, not a user toggle -- could get a
        write slot (AGENTS.md "Areas of interest" this was the
        pattern that section named as still-missing here). Safe because a re-run
        is idempotent: the delete above is park-scoped and re-derives the same
        clusters. `on_batch` is also the interrupt point -- raise from it (the
        AOI runner raises Interrupted) to stop with everything so far committed.
        """
        if polygons is None:
            polygons = self.load_settlement_polygons(park_id).get(park_id, [])
        if delete:
            # Before the empty-input early return, not after: a rebuild that
            # now yields nothing must not leave the old rows immortal.
            # Scoped to rows derived from GHSL polygons: a row with no
            # polygon_ids is retired detector output (invariant 5) that this
            # clusterer cannot recreate, so deleting it destroys data instead
            # of refreshing it. scripts/backfill_settlement_surface.py runs
            # this over all 160 areas, which is how a purge would have gone
            # unnoticed until the rows were gone.
            self.conn.execute("DELETE FROM park_settlements WHERE park_id = ? "
                              "AND polygon_ids IS NOT NULL AND polygon_ids != ''",
                              (park_id,))
            self.conn.commit()
        if not polygons:
            return 0

        print(f"  Processing {park_id} ({len(polygons)} polygons)...")
        park_name = ' '.join(park_id.split('_')[1:]).replace('_', ' ')
        count = 0
        clusters = self._cluster_polygons(polygons, SETTLEMENT_CLUSTER_KM)
        
        places = self._load_park_places(park_id)
        rivers = self._load_park_rivers(park_id)
        climate = self.climate.get(park_id, {})
        
        for cluster in clusters:
            avg_lat = sum(p['lat'] for p in cluster) / len(cluster)
            avg_lon = sum(p['lon'] for p in cluster) / len(cluster)
            total_area = sum(p['area_m2'] for p in cluster)
            # Extent is only present on polygons written after F1; when it is
            # not, area_m2 IS the mask extent and area_source says so, so the
            # two columns still each mean one thing.
            has_extent = any(p.get('extent_m2') is not None for p in cluster)
            total_extent = (sum(p.get('extent_m2') or 0 for p in cluster)
                            if has_extent else total_area)
            area_source = ('ghsl_built_s_surface' if has_extent
                           else 'ghsl_mask_extent')
            # A population is measured or it is absent. One polygon without a
            # GHS_POP reading makes the CLUSTER's total unmeasured, because a
            # partial sum presented as a total is the same lie in a smaller
            # font (AGENTS.md invariant 1).
            pop_sources = {p.get('population_source') for p in cluster}
            measured = (all(p.get('population_est') is not None for p in cluster)
                        and pop_sources - {None} and None not in pop_sources)
            total_pop = (sum(p['population_est'] for p in cluster)
                         if measured else None)
            pop_source = sorted(s for s in pop_sources if s)[0] if measured else None
            epoch = next((p.get('epoch') for p in cluster if p.get('epoch')), None)

            # Classification keys on POPULATION where it exists and on built
            # SURFACE otherwise -- never on the mask extent, which is ~24x the
            # surface and made every cluster look like a town.
            if total_pop is not None and total_pop > 1000:
                classification = 'town'
                confidence = 0.7
            elif total_pop is not None and total_pop > 200:
                classification = 'village'
                confidence = 0.7
            elif total_area > 50000:
                classification = 'agricultural'
                confidence = 0.6
            elif total_area < 5000:
                classification = 'temporary_camp'
                confidence = 0.5
            else:
                classification = 'settlement'
                confidence = 0.5

            nearest_place, place_dist = self._get_nearest_place(avg_lat, avg_lon, places)
            nearest_river = self._get_nearest_river(avg_lat, avg_lon, rivers)

            # Generate narrative
            narrative_parts = [f"{classification.replace('_', ' ').title()} in {park_name}."]
            if total_pop is not None:
                narrative_parts.append(
                    f"Built-up surface: {total_area/10000:.2f} ha, "
                    f"estimated population: {total_pop}.")
            else:
                # "estimated population: 0" for an unmeasured quantity is the
                # `nil` problem of invariant 12: say the word instead.
                narrative_parts.append(
                    f"Built-up surface: {total_area/10000:.2f} ha, "
                    f"population not measured.")
            if nearest_place:
                narrative_parts.append(f"Located {place_dist:.1f}km from {nearest_place['name']}.")
            if nearest_river:
                narrative_parts.append(
                    f"Near {nearest_river['name']} river "
                    f"({nearest_river.get('distance_km', 0):.1f}km).")
            narrative = ' '.join(narrative_parts)

            polygon_ids = ','.join(p['feature_id'] for p in cluster)
            place_name = nearest_place['name'] if nearest_place else ''

            # F12: `temporary` required total_area < 5,000 m², below the
            # MIN_AREA_M2 = 5,000 ingest floor, so it was unreachable BY
            # CONSTRUCTION and every row in a landscape of seasonal pastoral
            # camps said `permanent`. Size cannot answer this question at all
            # -- persistence between epochs can, and is not ingested -- so the
            # column is NULL until something measures it. A column that always
            # says the same word is not a classification.
            sett_type = None

            fires_1km, fires_5km, seasonality, defo_km2 = \
                self._settlement_fire_context(park_id, avg_lat, avg_lon)

            self.conn.execute("""
                INSERT INTO park_settlements
                (park_id, lat, lon, area_m2, extent_m2, area_source,
                 population_est, population_source, epoch, households_est,
                 nearest_place, distance_to_place_km, settlement_type,
                 classification, classification_confidence, narrative, polygon_ids,
                 fires_1km, fires_5km, fire_seasonality, deforest_nearby_km2,
                 fire_context_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?)
            """, (
                park_id, avg_lat, avg_lon, total_area, total_extent,
                area_source, total_pop, pop_source, epoch,
                None if total_pop is None else int(total_pop / 4.5),
                place_name, place_dist if place_dist else 0, sett_type,
                classification, confidence, narrative, polygon_ids,
                fires_1km, fires_5km, seasonality, defo_km2,
                datetime.now().isoformat()
            ))
            count += 1
            # Release the write lock between batches so cron jobs and user
            # toggles can interleave; on_batch may raise to interrupt.
            if on_batch and count % batch == 0:
                self.conn.commit()
                on_batch(count)
    
        self.conn.commit()
        return count
    
    def _settlement_fire_context(self, park_id, lat, lon):
        """(fires_1km, fires_5km, seasonality, deforest_nearby_km2).

        F6: these four columns were populated for parks by the Go classifier
        (/api/refresh-park) and by nothing at all for AOIs, so all 1,552 XSA
        rows carried 0 -- which reads as "no fire near this settlement" for
        settlements whose median is 1,594 detections within 5 km. Computing
        them here means the ONE clusterer fills them for parks and AOIs alike,
        and `fire_context_at` then distinguishes a measured zero from a never
        measured one.

        ⚠️ The bounds MUST stay `BETWEEN`, never `ABS(col - ?) < ?` -- see
        _get_fire_density and AGENTS.md invariant 3.
        """
        row = self.conn.execute("""
            SELECT
              COUNT(CASE WHEN latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ? THEN 1 END),
              COUNT(*),
              COUNT(CASE WHEN CAST(SUBSTR(acq_date, 6, 2) AS INT) IN (12,1,2,3) THEN 1 END)
            FROM fire_detections
            WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
        """, (lat - 0.01, lat + 0.01, lon - 0.01, lon + 0.01,
              lat - 0.05, lat + 0.05, lon - 0.05, lon + 0.05)).fetchone()
        f1, f5, dry = (row[0] or 0), (row[1] or 0), (row[2] or 0)
        wet = f5 - dry
        if dry > wet * 3:
            seasonality = 'dry_season'
        elif wet > dry * 3:
            seasonality = 'wet_season'
        elif f5 > 0:
            seasonality = 'year_round'
        else:
            seasonality = None

        defo = self.conn.execute("""
            SELECT COALESCE(SUM(area_km2), 0) FROM deforestation_events
            WHERE park_id = ? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
        """, (park_id, lat - 0.2, lat + 0.2, lon - 0.2, lon + 0.2)).fetchone()[0]
        return f1, f5, seasonality, float(defo or 0)

    def export_json(self):
        """Export events to JSON files"""
        print("\n" + "=" * 60)
        print("Exporting to JSON files")
        print("=" * 60)
        
        SETTLEMENT_DIR.mkdir(parents=True, exist_ok=True)
        DEFOREST_DIR.mkdir(parents=True, exist_ok=True)
        
        # Export settlements
        cursor = self.conn.execute("""
            SELECT * FROM park_settlements ORDER BY park_id, id
        """)
        
        settlements_by_park = defaultdict(list)
        columns = [desc[0] for desc in cursor.description]
        for row in cursor:
            d = dict(zip(columns, row))
            settlements_by_park[d['park_id']].append(d)
        
        for park_id, settlements in settlements_by_park.items():
            with open(SETTLEMENT_DIR / f'{park_id}.json', 'w') as f:
                json.dump(settlements, f, indent=2)
        print(f"  Exported settlements for {len(settlements_by_park)} parks")
        
        # Export deforestation
        cursor = self.conn.execute("""
            SELECT * FROM deforestation_events ORDER BY park_id, year, id
        """)
        
        deforest_by_park = defaultdict(list)
        columns = [desc[0] for desc in cursor.description]
        for row in cursor:
            d = dict(zip(columns, row))
            deforest_by_park[d['park_id']].append(d)
        
        for park_id, events in deforest_by_park.items():
            with open(DEFOREST_DIR / f'{park_id}.json', 'w') as f:
                json.dump(events, f, indent=2)
        print(f"  Exported deforestation for {len(deforest_by_park)} parks")
    
    def run(self):
        """Run full rebuild"""
        self.rebuild_deforestation()
        self.rebuild_settlements()
        self.export_json()
        self.conn.close()
        print("\n" + "=" * 60)
        print("Complete!")

if __name__ == '__main__':
    rebuilder = EventRebuilder()
    rebuilder.run()
