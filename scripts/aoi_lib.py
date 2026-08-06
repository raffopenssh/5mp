#!/usr/bin/env python3
"""Shared helpers for AOI overlays (docs/PLAN_AOI_OVERLAY.md).

An AOI is a user-drawn polygon with a fixed analysis window and an owner. It is
deliberately NOT a park: it never enters keystones_with_boundaries.json and is
never a fire_detections.protected_area_id, so park_assigner cannot reassign a
single detection because an AOI exists (§3). Fire membership is by polygon,
cached in aoi_fires.
"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import transform

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"

sys.path.insert(0, str(BASE_DIR / "scripts"))


def connect(readonly=False):
    if readonly:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    return conn


def principal_ref(pwd):
    """Must match srv/aoi.go principalRef: sha256 hex, first 16 chars."""
    return hashlib.sha256(pwd.encode()).hexdigest()[:16]


def geodesic_area_km2(geom):
    """Area of a lon/lat geometry via an equal-area (Mollweide) projection."""
    import pyproj

    project = pyproj.Transformer.from_crs(
        "EPSG:4326", "+proj=moll +lon_0=0 +datum=WGS84 +units=m",
        always_xy=True).transform
    return transform(project, geom).area / 1e6


def load_aoi(conn, aoi_id):
    row = conn.execute("SELECT * FROM aois WHERE id = ?", (aoi_id,)).fetchone()
    if not row:
        raise SystemExit(f"no such AOI: {aoi_id}")
    return row


def aoi_geom(row):
    return shape(json.loads(row["geometry"]))


def aoi_bbox(row):
    return (row["bbox_minx"], row["bbox_miny"], row["bbox_maxx"], row["bbox_maxy"])


def firms_area(row, pad=0.0):
    """FIRMS area string 'minx,miny,maxx,maxy' for the AOI bbox.

    Always request the whole bbox, never a tiled concave gap: INSERT OR IGNORE
    on UNIQUE(lat, lon, acq_date, acq_time, satellite) makes overlap with rows
    we already hold free (§1).
    """
    x0, y0, x1, y1 = aoi_bbox(row)
    return f"{x0 - pad:.4f},{y0 - pad:.4f},{x1 + pad:.4f},{y1 + pad:.4f}"


def upsert_aoi(conn, aoi_id, name, geojson_geom, from_date=None, to_date=None,
               owner_pwd=None, visibility="private", notes=None):
    geom = shape(geojson_geom)
    x0, y0, x1, y1 = geom.bounds
    owner_id = None
    if owner_pwd:
        ref = principal_ref(owner_pwd)
        conn.execute(
            "INSERT OR IGNORE INTO principals (kind, ref, label) VALUES ('password', ?, ?)",
            (ref, owner_pwd[:3] + "\u2026"))
        owner_id = conn.execute(
            "SELECT id FROM principals WHERE kind='password' AND ref=?",
            (ref,)).fetchone()[0]
    conn.execute("""
        INSERT INTO aois (id, name, geometry, bbox_minx, bbox_miny, bbox_maxx,
                          bbox_maxy, area_km2, from_date, to_date,
                          owner_principal_id, visibility, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name, geometry=excluded.geometry,
          bbox_minx=excluded.bbox_minx, bbox_miny=excluded.bbox_miny,
          bbox_maxx=excluded.bbox_maxx, bbox_maxy=excluded.bbox_maxy,
          area_km2=excluded.area_km2, from_date=excluded.from_date,
          to_date=excluded.to_date,
          owner_principal_id=COALESCE(excluded.owner_principal_id, aois.owner_principal_id),
          visibility=excluded.visibility, notes=excluded.notes
    """, (aoi_id, name, json.dumps(geojson_geom), x0, y0, x1, y1,
          geodesic_area_km2(geom), from_date, to_date, owner_id, visibility, notes))
    conn.commit()
    return owner_id


# Dataset queue definition. priority = lower runs first; depends_on gates a
# dataset until its dependency is 'done'.
DEFAULT_DATASETS = [
    # dataset,        priority, depends_on
    ("fire_gap",      10, None),
    ("fire_v5",       20, "fire_gap"),
    ("gfw",           30, None),
    ("deforestation", 35, "gfw"),
    ("ghsl",          40, None),
    ("osm",           50, None),
    ("gsw",           60, None),
    ("hydro",         70, None),
    ("basin",         80, None),
]


def seed_datasets(conn, aoi_id, datasets=None):
    for name, prio, dep in (datasets or DEFAULT_DATASETS):
        conn.execute("""
            INSERT OR IGNORE INTO aoi_datasets (aoi_id, dataset, priority, depends_on)
            VALUES (?,?,?,?)""", (aoi_id, name, prio, dep))
    conn.commit()
