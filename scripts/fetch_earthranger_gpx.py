#!/usr/bin/env python3
"""Fetch GPS tracks from EarthRanger (PAMDAS) and upload as anonymised GPX.

Designed to be called daily by the Go backend.  READ-ONLY access to the
EarthRanger API — no writes, no deletes.

Usage:
    python3 fetch_earthranger_gpx.py \
        --url https://nyerere.pamdas.org \
        --user MananeCR \
        --upload-url http://localhost:8000/api/upload/async?pwd=test2026 \
        [--days 1] [--dry-run]

    Password is read from EARTHRANGER_PASSWORD env var (never passed as arg).

What it does:
  1. Authenticates via OAuth2 (read-only session)
  2. Lists all subjects (persons, vehicles, aircraft)
  3. Skips any subject_type == 'wildlife' (animal collars)
  4. For each allowed subject, fetches GPS tracks for the last N days
  5. Builds ONE anonymised GPX file with all tracks
     - Subject IDs are kept as opaque track names (no real names)
     - Timestamps are preserved (needed for speed inference)
  6. Uploads the GPX via the app's async upload endpoint
  7. Prints a JSON summary to stdout for the Go caller
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

# ── EarthRanger client (read-only) ────────────────────────────────────────────

ALLOWED_SUBJECT_TYPES = {'person', 'vehicle', 'aircraft'}
BLOCKED_SUBJECT_TYPES = {'wildlife', 'animal', 'collar'}  # animal collars
CLIENT_IDS = ['das_web_client', 'er_mobile_tracker']  # tried in order


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
    """Return list of (lon, lat, time) tuples for a subject+source."""
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
            t = times[i] if i < len(times) else None
            if t:  # only include points with timestamps
                points.append((lon, lat, t))
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

def build_gpx(tracks: dict) -> tuple:
    """Build a GPX 1.1 XML string from {track_id: [(lon, lat, time), ...]}.

    Returns (xml_string, total_points).
    """
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
        trkseg = ET.SubElement(trk, 'trkseg')

        for lon, lat, t in points:
            trkpt = ET.SubElement(trkseg, 'trkpt', {'lat': f'{lat:.6f}', 'lon': f'{lon:.6f}'})
            ET.SubElement(trkpt, 'time').text = normalise_timestamp(t)
            total_pts += 1

    ET.indent(gpx)
    return ET.tostring(gpx, encoding='unicode', xml_declaration=True), total_pts


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='Fetch EarthRanger GPS tracks → anonymised GPX')
    ap.add_argument('--url', required=True, help='PAMDAS server URL')
    ap.add_argument('--user', required=True, help='Username')
    ap.add_argument('--upload-url', required=True, help='App async upload URL')
    ap.add_argument('--days', type=int, default=1, help='Days of history (default: 1)')
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
    since = (now - timedelta(days=args.days)).isoformat()
    until = now.isoformat()

    # 2. Get subjects (excluding wildlife/animal collars)
    subjects = fetch_subjects(session, api)

    # 3. Fetch tracks per subject
    tracks = {}  # subject_id -> [(lon, lat, time), ...]
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

    # 4. Build GPX
    if not tracks:
        print(json.dumps({'ok': True, 'subjects': 0, 'points': 0, 'uploaded': False,
                          'message': 'No GPS data found for the period'}))
        sys.exit(0)

    gpx_xml, total_pts = build_gpx(tracks)

    # 5. Upload
    if args.dry_run:
        # Write to stdout for inspection
        sys.stderr.write(gpx_xml[:2000] + '\n...\n')
        print(json.dumps({'ok': True, 'subjects': subject_count, 'points': total_pts,
                          'uploaded': False, 'dry_run': True}))
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
                          'subjects': subject_count, 'points': total_pts}))
        sys.exit(1)

    print(json.dumps({
        'ok': True,
        'subjects': subject_count,
        'points': total_pts,
        'uploaded': True,
        'queue_id': upload_result.get('queue_id'),
    }))


if __name__ == '__main__':
    main()
