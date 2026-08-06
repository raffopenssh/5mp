#!/usr/bin/env python3
"""
Canonical fire-detection source for the trajectory pipeline.

Historically the v5/v6 trajectory builder read `data/raw-fire-viirs-*/{park}.json`.
Those files were a ROLLING WINDOW (most parks only held ~6 months), while
`fire_detections` in SQLite holds the full
2018-2026 history. The consequence was severe: a full (non-incremental) rebuild
silently discarded years of trajectories, because pre-window history only
survived as frozen group JSON carried forward by each incremental run.

This module makes SQLite the only source (the JSON window and its two nightly
writers were deleted 2026-08-05). It is also faster - the
`idx_fire_pa_date` index answers a full-history park query in ~50ms - and it
removes the nightly rewrite of 176MB of JSON.

Fire dict schema (matches the old JSON shape so callers need no changes):
    latitude, longitude, acq_date, acq_time, frp, confidence, satellite
plus:
    acq_dt   - float days since epoch-date, including time-of-day. This is what
               lets the builder treat separate satellite overpasses as distinct
               temporal samples instead of collapsing a whole calendar day.
"""

import sqlite3
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"

# Confidence classes worth keeping. VIIRS 'l' (low) is disproportionately
# sun glint / small hot surfaces; we keep it but callers may down-weight it
# for cluster SEEDING while still letting it extend an existing track.
LOW_CONFIDENCE = "l"

_EPOCH = datetime(2000, 1, 1)


def _date_num(acq_date):
    return (datetime.strptime(acq_date, "%Y-%m-%d") - _EPOCH).days


_dn_cache = {}


def date_num(acq_date):
    v = _dn_cache.get(acq_date)
    if v is None:
        v = _date_num(acq_date)
        _dn_cache[acq_date] = v
    return v


def parse_acq_time(acq_time):
    """FIRMS acq_time is HHMM but arrives unpadded ('11' = 00:11, '1246' =
    12:46). Returns fractional day in [0,1)."""
    s = str(acq_time or "").strip()
    if not s.isdigit():
        return 0.5  # unknown -> midday, matches legacy '1200' assumption
    s = s.zfill(4)
    try:
        hh, mm = int(s[:2]), int(s[2:])
    except ValueError:
        return 0.5
    if hh > 23 or mm > 59:
        return 0.5
    return (hh * 60 + mm) / 1440.0


def _finalize(f):
    f["acq_dt"] = date_num(f["acq_date"]) + parse_acq_time(f.get("acq_time"))
    return f


def load_park_fires_db(park_id, min_date, max_date=None, conn=None):
    """Full-history park fires from SQLite (canonical source)."""
    own = conn is None
    if own:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        sql = ("SELECT latitude, longitude, acq_date, acq_time, frp, "
               "confidence, satellite FROM fire_detections "
               "WHERE protected_area_id = ? AND acq_date >= ?")
        params = [park_id, min_date]
        if max_date:
            sql += " AND acq_date <= ?"
            params.append(max_date)
        out = []
        for lat, lon, d, t, frp, conf, sat in conn.execute(sql, params):
            if not lat or not lon:  # 0.0 == known data error
                continue
            out.append(_finalize({
                "latitude": lat, "longitude": lon, "acq_date": d,
                "acq_time": t, "frp": frp or 0.0,
                "confidence": conf or "n", "satellite": sat or "N20",
            }))
        return out
    finally:
        if own:
            conn.close()


def load_park_fires(park_id, min_date, max_date=None, conn=None):
    return load_park_fires_db(park_id, min_date, max_date, conn=conn)


def park_fire_count(park_id, min_date, conn=None):
    """Cheap count for coverage metrics."""
    own = conn is None
    if own:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM fire_detections "
            "WHERE protected_area_id = ? AND acq_date >= ?",
            (park_id, min_date)).fetchone()
        return row[0] if row else 0
    finally:
        if own:
            conn.close()


def earliest_fire_date(park_id, conn=None):
    """Earliest available detection date for a park, or None.

    Incremental mode uses this to avoid walking the rebuild cutoff back past
    the point where data exists (which would drop unrebuildable old groups).
    """
    own = conn is None
    if own:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT MIN(acq_date) FROM fire_detections WHERE protected_area_id = ?",
            (park_id,)).fetchone()
        return row[0] if row and row[0] else None
    finally:
        if own:
            conn.close()
