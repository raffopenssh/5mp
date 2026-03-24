#!/usr/bin/env python3
"""Fetch GPS tracks from EarthRanger (PAMDAS) and upload as anonymised GPX.

Designed to be called daily by the Go backend.  READ-ONLY access to the
EarthRanger API — no writes, no deletes.

Usage:
    python3 fetch_earthranger_gpx.py \\
        --url https://nyerere.pamdas.org \\
        --user MananeCR \\
        --upload-url http://localhost:8000/api/upload/async?pwd=test2026 \\
        [--days 1] [--dry-run]

    Password is read from EARTHRANGER_PASSWORD env var (never passed as arg).

What it does:
  1. Authenticates via OAuth2 (read-only session)
  2. Lists all subjects (persons, vehicles, aircraft)
  3. Skips any subject_type == 'wildlife' (animal collars)
  4. Fetches active patrols to identify patrol leaders and their patrol types
  5. For each allowed subject, fetches GPS tracks for the last N days
  6. Builds ONE anonymised GPX file with all tracks
     - Subject IDs are kept as opaque track names (no real names)
     - Timestamps are preserved (needed for speed inference)
     - EarthRanger subject metadata (type, subtype, patrol_type) is embedded
       in GPX <extensions> under the er: namespace so the Go classifier can
       use authoritative type info instead of guessing from speed
  7. Uploads the GPX via the app's async upload endpoint
  8. Prints a JSON summary to stdout for the Go caller

GPX extension namespace:
  xmlns:er="http://5mp.globe/earthranger/1"

  Per-track extensions:
    <er:subject_type>   — person | vehicle | aircraft
    <er:subject_subtype> — er_mobile | ranger | truck | car | plane | helicopter
    <er:patrol_type>    — (optional) e.g. heli_patrol_operations, vehicle_patrol
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    import requests
except ImportError:
    sys.exit("error: 'requests' not installed. Run: pip install requests")

# ── Constants ─────────────────────────────────────────────────────────────────

ALLOWED_SUBJECT_TYPES = {'person', 'vehicle', 'aircraft'}
BLOCKED_SUBJECT_TYPES = {'wildlife', 'animal', 'collar'}  # animal collars
CLIENT_IDS = ['das_web_client', 'er_mobile_tracker']  # tried in order

# EarthRanger extension namespace — used for subject metadata in GPX
ER_NS = 'http://5mp.globe/earthranger/1'
ER_NS_PREFIX = 'er'

# Register the namespace so ElementTree serialises it as "er:" not "ns0:"
ET.register_namespace(ER_NS_PREFIX, ER_NS)

# ── Type classification ───────────────────────────────────────────────────────
#
# For the JSON summary we bucket subjects into three movement categories.
# subject_type → category mapping (subject_subtype doesn't change the bucket).
#
# person/ranger   → foot      (InReach on foot patrols)
# person/er_mobile → foot     (default — overridden if patrol says otherwise)
# vehicle/*       → vehicle
# aircraft/*      → aircraft

TYPE_CATEGORY = {
    'person':   'foot',
    'vehicle':  'vehicle',
    'aircraft': 'aircraft',
}

# Patrol type strings that indicate the leader is airborne or driving.
# If a person/er_mobile is leading one of these, their category changes.
PATROL_TYPE_OVERRIDES = {
    # aircraft patrols
    'plane_patrol_operations':              'aircraft',
    'plane_patrol_law_enforcement':         'aircraft',
    'helicopter_patrol_operations':         'aircraft',
    'helicopter_patrol_law_enforcement':    'aircraft',
    'heli_patrol_operations':               'aircraft',
    'heli_patrol_law_enforcement':          'aircraft',
    # vehicle patrols
    'vehicle_patrol':                       'vehicle',
    'vehicle_patrol_operations':            'vehicle',
    'vehicle_patrol_law_enforcement':       'vehicle',
}


# ── EarthRanger client (read-only) ────────────────────────────────────────────

def er_authenticate(session: requests.Session, url: str, user: str, pw: str) -> str:
    """Obtain an OAuth2 bearer token.  Tries common client_ids."""
    for cid in CLIENT_IDS:
        resp = session.post(
            f"{url.rstrip('/')}/oauth2/token",
            data={'grant_type': 'password', 'username': user, 'password': pw, 'client_id': cid},
            timeout=30,
        )
        if resp.status_code == 200:
            token = resp.json().get('access_token')
            if token:
                return token
    raise RuntimeError(f"Authentication failed for {url} (tried client_ids: {CLIENT_IDS})")


def er_get(session: requests.Session, api: str, path: str, params: Optional[dict] = None):
    """GET helper with error handling."""
    resp = session.get(f"{api}/{path}", params=params or {}, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_subjects(session: requests.Session, api: str) -> list:
    """Return list of non-wildlife subjects."""
    data = er_get(session, api, 'subjects', {'page_size': 500})
    results = data
    # Handle nested response structures
    if isinstance(data, dict):
        if 'data' in data:
            inner = data['data']
            results = inner.get('results', inner) if isinstance(inner, dict) else inner
        elif 'results' in data:
            results = data['results']
    if not isinstance(results, list):
        results = []

    allowed = []
    for s in results:
        stype = (s.get('subject_type') or '').lower()
        if stype in BLOCKED_SUBJECT_TYPES:
            continue
        if stype not in ALLOWED_SUBJECT_TYPES:
            continue  # skip unknown types (safe default)
        allowed.append(s)
    return allowed


def fetch_patrols(session: requests.Session, api: str,
                  since: str, until: str) -> dict:
    """Fetch active patrols and return a map of subject_id → patrol_type.

    Queries /api/v1.0/activity/patrols for the given date range.  For each
    patrol segment, if there is a leader with a subject id, we record the
    patrol_type so we can embed it in the GPX track extensions.

    Returns: {subject_id: patrol_type_string}
    """
    subject_patrol = {}  # subject_id → patrol_type
    try:
        data = er_get(session, api, 'activity/patrols', {
            'filter': json.dumps({
                'date_range': {'lower': since, 'upper': until},
                'status': ['open', 'done'],
            }),
            'page_size': 500,
        })
    except Exception:
        # Patrol API may not be accessible — non-fatal
        return subject_patrol

    # Unwrap response (may be {data: {results: [...]}} or just [...])
    results = data
    if isinstance(data, dict):
        if 'data' in data:
            inner = data['data']
            results = inner.get('results', inner) if isinstance(inner, dict) else inner
        elif 'results' in data:
            results = data['results']
    if not isinstance(results, list):
        return subject_patrol

    for patrol in results:
        # patrol_type can live at the patrol level OR the segment level.
        # In many ER installations, patrol-level type is null and only
        # segments carry the type string.
        patrol_type = ''
        pt = patrol.get('patrol_type')
        if isinstance(pt, str) and pt:
            patrol_type = pt
        elif isinstance(pt, dict):
            patrol_type = pt.get('value', pt.get('id', ''))

        # Each patrol has one or more segments, each with a leader
        segments = patrol.get('patrol_segments') or []
        for seg in segments:
            # Segment-level patrol_type takes precedence over patrol-level
            seg_pt = seg.get('patrol_type')
            if isinstance(seg_pt, str) and seg_pt:
                effective_type = seg_pt
            elif isinstance(seg_pt, dict):
                effective_type = seg_pt.get('value', seg_pt.get('id', patrol_type))
            else:
                effective_type = patrol_type

            if not effective_type:
                continue  # Neither patrol nor segment has a type — skip

            leader = seg.get('leader')
            if not leader:
                continue
            # leader may be a dict with 'id' or a bare UUID string
            leader_id = leader.get('id') if isinstance(leader, dict) else str(leader)
            if leader_id:
                # Keep the most specific patrol type (last wins if multiple)
                subject_patrol[leader_id] = effective_type

    return subject_patrol


def fetch_sources(session: requests.Session, api: str, subject_id: str) -> list:
    """Return source IDs linked to a subject."""
    data = er_get(session, api, f'subject/{subject_id}/subjectsources')
    items = data.get('data', data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [s.get('source') for s in items if isinstance(s, dict) and s.get('source')]


def fetch_tracks(session: requests.Session, api: str,
                 subject_id: str, source_id: str,
                 since: str, until: str) -> list:
    """Return list of (lon, lat, time, alt) tuples for a subject+source."""
    data = er_get(session, api, f'subject/{subject_id}/source/{source_id}/tracks',
                  {'since': since, 'until': until})
    inner = data.get('data', data) if isinstance(data, dict) else data
    features = inner.get('features', []) if isinstance(inner, dict) else []

    points = []
    for feat in features:
        geom = feat.get('geometry')
        if not geom or not geom.get('coordinates'):
            continue
        coords = geom['coordinates']
        times = []
        cp = feat.get('properties', {}).get('coordinateProperties', {})
        if isinstance(cp, dict) and 'times' in cp:
            times = cp['times']

        for i, coord in enumerate(coords):
            lon, lat = coord[0], coord[1]
            alt = coord[2] if len(coord) > 2 else None
            t = times[i] if i < len(times) else None
            if t:  # only include points with timestamps
                points.append((lon, lat, t, alt))
    return points


# ── Timestamp normalisation ───────────────────────────────────────────────────

def normalise_timestamp(t: str) -> str:
    """Convert any ISO-8601 timestamp to valid RFC3339 ending in 'Z'.

    EarthRanger returns e.g. '2026-03-22T19:15:06+00:00'.
    GPX 1.1 requires UTC timestamps.  We strip any offset and append 'Z'.
    """
    # Remove trailing Z if present (we'll re-add it)
    t = t.rstrip('Z')
    # Remove UTC offset (+00:00, +0000, -05:00, etc.)
    t = re.sub(r'[+-]\d{2}:?\d{2}$', '', t)
    return t + 'Z'


# ── GPX builder ───────────────────────────────────────────────────────────────

def build_gpx(tracks: dict, subject_meta: dict) -> tuple:
    """Build a GPX 1.1 XML string with EarthRanger subject extensions.

    Args:
        tracks:       {subject_id: [(lon, lat, time, alt), ...]}
        subject_meta: {subject_id: {'subject_type': ..., 'subject_subtype': ...,
                                     'patrol_type': ... (optional)}}

    Returns:
        (xml_string, total_points)

    Each <trk> gets an <extensions> block like:
        <extensions>
            <er:subject_type>vehicle</er:subject_type>
            <er:subject_subtype>truck</er:subject_subtype>
            <er:patrol_type>vehicle_patrol</er:patrol_type>   <!-- if known -->
        </extensions>
    """
    # Note: ET.register_namespace('er', ER_NS) at module level ensures the
    # namespace serialises with the 'er:' prefix.  ElementTree auto-adds the
    # xmlns:er declaration on the root element when it encounters child
    # elements in that namespace.  We do NOT add xmlns:er as an explicit
    # attribute — that would cause a duplicate declaration.
    gpx = ET.Element('gpx', {
        'version': '1.1',
        'creator': '5mp-autofetch',
        'xmlns': 'http://www.topografix.com/GPX/1/1',
    })

    metadata = ET.SubElement(gpx, 'metadata')
    ET.SubElement(metadata, 'name').text = f'autofetch-{datetime.now(timezone.utc).strftime("%Y%m%d")}'
    ET.SubElement(metadata, 'time').text = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    total_pts = 0
    for track_id, points in tracks.items():
        if not points:
            continue
        # Sort by time
        points.sort(key=lambda p: p[2])

        trk = ET.SubElement(gpx, 'trk')
        ET.SubElement(trk, 'name').text = track_id  # opaque subject ID

        # ── Subject metadata extensions ───────────────────────────────
        meta = subject_meta.get(track_id, {})
        stype = meta.get('subject_type', '')
        ssubtype = meta.get('subject_subtype', '')
        patrol_type = meta.get('patrol_type', '')

        if stype or ssubtype or patrol_type:
            ext = ET.SubElement(trk, 'extensions')
            if stype:
                ET.SubElement(ext, f'{{{ER_NS}}}subject_type').text = stype
            if ssubtype:
                ET.SubElement(ext, f'{{{ER_NS}}}subject_subtype').text = ssubtype
            if patrol_type:
                ET.SubElement(ext, f'{{{ER_NS}}}patrol_type').text = patrol_type

        trkseg = ET.SubElement(trk, 'trkseg')

        for pt in points:
            lon, lat, t = pt[0], pt[1], pt[2]
            alt = pt[3] if len(pt) > 3 else None
            trkpt = ET.SubElement(trkseg, 'trkpt', {'lat': f'{lat:.6f}', 'lon': f'{lon:.6f}'})
            if alt is not None:
                ET.SubElement(trkpt, 'ele').text = f'{alt:.1f}'
            ET.SubElement(trkpt, 'time').text = normalise_timestamp(t)
            total_pts += 1

    ET.indent(gpx)
    return ET.tostring(gpx, encoding='unicode', xml_declaration=True), total_pts


def classify_subject(meta: dict) -> str:
    """Return the movement category for a subject: 'foot', 'vehicle', or 'aircraft'.

    Uses patrol_type to override the default category when a person/er_mobile
    is leading a vehicle or aircraft patrol.
    """
    stype = meta.get('subject_type', '')
    patrol_type = meta.get('patrol_type', '')

    # Check if patrol type overrides the default (e.g. er_mobile in helicopter)
    if patrol_type:
        override = PATROL_TYPE_OVERRIDES.get(patrol_type)
        if override:
            return override

    return TYPE_CATEGORY.get(stype, 'foot')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='Fetch EarthRanger GPS tracks → anonymised GPX')
    ap.add_argument('--url', required=True, help='PAMDAS server URL')
    ap.add_argument('--user', required=True, help='Username')
    ap.add_argument('--upload-url', required=True, help='App async upload URL')
    ap.add_argument('--days', type=int, default=1, help='Days of history (default: 1, used if --since not set)')
    ap.add_argument('--since', type=str, default=None,
                    help='ISO-8601 timestamp: only fetch data after this time (overrides --days)')
    ap.add_argument('--dry-run', action='store_true', help='Build GPX but do not upload')
    args = ap.parse_args()

    # Password from environment variable — never from command line
    password = os.environ.get('EARTHRANGER_PASSWORD', '')
    if not password:
        print(json.dumps({'ok': False, 'error': 'EARTHRANGER_PASSWORD env var not set'}))
        sys.exit(1)

    session = requests.Session()
    session.headers['User-Agent'] = '5mp-autofetch/1.0'

    # 1. Authenticate
    try:
        token = er_authenticate(session, args.url, args.user, password)
    except RuntimeError as e:
        print(json.dumps({'ok': False, 'error': str(e)}))
        sys.exit(1)

    session.headers['Authorization'] = f'Bearer {token}'
    api = f"{args.url.rstrip('/')}/api/v1.0"

    now = datetime.now(timezone.utc)
    if args.since:
        since = args.since
    else:
        since = (now - timedelta(days=args.days)).isoformat()
    until = now.isoformat()

    # 2. Get subjects (excluding wildlife/animal collars)
    subjects = fetch_subjects(session, api)

    # 3. Fetch patrol context — maps subject_id → patrol_type string
    #    This tells us when a person/er_mobile is actually in a vehicle or aircraft.
    patrol_map = fetch_patrols(session, api, since, until)

    # 4. Build subject metadata lookup (subject_id → {type, subtype, patrol_type})
    subject_meta = {}  # subject_id → {subject_type, subject_subtype, patrol_type}
    for s in subjects:
        sid = s['id']
        stype = (s.get('subject_type') or '').lower()
        ssubtype = (s.get('subject_subtype') or '').lower()
        meta = {
            'subject_type': stype,
            'subject_subtype': ssubtype,
        }
        # Attach patrol type if this subject is a patrol leader
        if sid in patrol_map:
            meta['patrol_type'] = patrol_map[sid]
        subject_meta[sid] = meta

    # 5. Fetch tracks per subject
    tracks = {}  # subject_id → [(lon, lat, time, alt), ...]
    subject_count = 0
    skipped_no_source = 0

    for s in subjects:
        sid = s['id']
        sources = fetch_sources(session, api, sid)
        if not sources:
            skipped_no_source += 1
            continue

        all_points = []
        for src_id in sources:
            try:
                pts = fetch_tracks(session, api, sid, src_id, since, until)
                all_points.extend(pts)
            except Exception:
                pass  # skip individual source errors

        if all_points:
            tracks[sid] = all_points
            subject_count += 1

    # 6. Build GPX
    if not tracks:
        print(json.dumps({'ok': True, 'subjects': 0, 'points': 0, 'uploaded': False,
                          'types': {'foot': 0, 'vehicle': 0, 'aircraft': 0},
                          'message': 'No GPS data found for the period'}))
        sys.exit(0)

    gpx_xml, total_pts = build_gpx(tracks, subject_meta)

    # 7. Compute type breakdown for the summary
    type_counts = {'foot': 0, 'vehicle': 0, 'aircraft': 0}
    for sid in tracks:
        meta = subject_meta.get(sid, {})
        category = classify_subject(meta)
        type_counts[category] = type_counts.get(category, 0) + 1

    # 8. Upload
    if args.dry_run:
        # Write to stdout for inspection
        sys.stderr.write(gpx_xml[:2000] + '\n...\n')
        print(json.dumps({'ok': True, 'subjects': subject_count, 'points': total_pts,
                          'types': type_counts, 'uploaded': False, 'dry_run': True}))
        sys.exit(0)

    try:
        resp = requests.post(
            args.upload_url,
            files={'gpx': (f'autofetch-{now.strftime("%Y%m%d")}.gpx',
                           gpx_xml.encode('utf-8'), 'application/gpx+xml')},
            timeout=120,
        )
        resp.raise_for_status()
        upload_result = resp.json()
    except Exception as e:
        print(json.dumps({'ok': False, 'error': f'Upload failed: {e}',
                          'subjects': subject_count, 'points': total_pts,
                          'types': type_counts}))
        sys.exit(1)

    print(json.dumps({
        'ok': True,
        'subjects': subject_count,
        'points': total_pts,
        'types': type_counts,
        'uploaded': True,
        'queue_id': upload_result.get('queue_id'),
    }))


if __name__ == '__main__':
    main()
