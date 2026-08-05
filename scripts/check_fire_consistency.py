#!/usr/bin/env python3
"""
Fire pipeline consistency checker.

Three artefacts must agree or the UI breaks in ways that are invisible in logs:

  data/fire_groups_v5/*.json   <- builder output (source of truth)
  feature_geometries           <- what the map/pin API can resolve
  fire_narrative_cache         <- what the popup narrative links point at

Every narrative entry must have a resolvable feature, or clicking a fire in the
popup yields "Feature not found". Every JSON group must exist in the DB, and the
DB must not keep groups the builder no longer produces (stale rows from a
rebuild that was never followed by a load).

Usage:
    python3 scripts/check_fire_consistency.py            # summary
    python3 scripts/check_fire_consistency.py --verbose  # per-park detail
    python3 scripts/check_fire_consistency.py --json     # machine readable

Exit code 0 = consistent, 1 = drift found. Safe/read-only.
Fix drift with: python3 scripts/fix_fire_consistency.py
"""

import json
import glob
import sqlite3
import argparse
import collections
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "db.sqlite3"
GROUPS_DIR = BASE_DIR / "data" / "fire_groups_v5"


def load_json_groups():
    """park_id -> list of feature_ids (with duplicates preserved)."""
    out = {}
    for f in sorted(glob.glob(str(GROUPS_DIR / "*.json"))):
        park = Path(f).stem
        try:
            out[park] = [g['feature_id'] for g in json.load(open(f))]
        except Exception as e:
            out[park] = []
            print(f"  WARN: unreadable {park}.json: {e}")
    return out


def narrative_feature_ids(conn):
    """park_id -> set of feature_ids referenced by the cached narratives."""
    out = collections.defaultdict(set)
    for park_id, blob in conn.execute(
            "SELECT park_id, narrative_json FROM fire_narrative_cache "
            "WHERE narrative_json IS NOT NULL"):
        try:
            data = json.loads(blob)
        except Exception:
            continue
        items = data.get('narratives') if isinstance(data, dict) else data
        for n in (items or []):
            if isinstance(n, dict) and n.get('feature_id'):
                out[park_id].add(n['feature_id'])
    return out


def check():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    js = load_json_groups()
    db = collections.defaultdict(set)
    for park, fid in conn.execute(
            "SELECT park_id, feature_id FROM feature_geometries "
            "WHERE feature_type = 'fire_trajectory'"):
        db[park].add(fid)
    narr = narrative_feature_ids(conn)

    report = {'parks': {}, 'totals': collections.Counter()}
    for park in sorted(set(js) | set(db) | set(narr)):
        ids = js.get(park, [])
        uniq = set(ids)
        counts = collections.Counter(ids)
        dup = sum(v - 1 for v in counts.values() if v > 1)
        dbids = db.get(park, set())
        missing = uniq - dbids          # builder produced it, DB can't resolve it
        stale = dbids - uniq            # DB keeps a group the builder dropped
        nids = narr.get(park, set())
        n_dangling = nids - dbids       # narrative link that 404s
        n_dup = nids & {k for k, v in counts.items() if v > 1}
        entry = {
            'json_groups': len(ids), 'json_unique': len(uniq),
            'duplicate_ids': dup,
            'db_groups': len(dbids),
            'missing_in_db': len(missing), 'stale_in_db': len(stale),
            'narrative_refs': len(nids),
            'narrative_dangling': len(n_dangling),
            'narrative_ambiguous': len(n_dup),
            'no_json_file': park not in js,
            'examples': {
                'missing_in_db': sorted(missing)[:3],
                'stale_in_db': sorted(stale)[:3],
                'narrative_dangling': sorted(n_dangling)[:3],
            },
        }
        report['parks'][park] = entry
        for k, v in entry.items():
            if isinstance(v, int):
                report['totals'][k] += v
            elif k == 'no_json_file' and v:
                report['totals']['parks_without_json'] += 1
    report['totals'] = dict(report['totals'])
    conn.close()
    return report


BAD_KEYS = ('duplicate_ids', 'missing_in_db', 'stale_in_db',
            'narrative_dangling', 'narrative_ambiguous')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    rep = check()
    t = rep['totals']
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print("Fire pipeline consistency")
        print(f"  parks:                 {len(rep['parks'])}")
        print(f"  JSON groups:           {t.get('json_groups', 0):,} "
              f"({t.get('json_unique', 0):,} unique)")
        print(f"  DB fire_trajectory:    {t.get('db_groups', 0):,}")
        print(f"  narrative references:  {t.get('narrative_refs', 0):,}")
        print("  --- drift ---")
        for k in BAD_KEYS:
            print(f"  {k + ':':<24} {t.get(k, 0):,}")
        if args.verbose:
            print("\nPer-park (only parks with drift):")
            for park, e in rep['parks'].items():
                if any(e.get(k) for k in BAD_KEYS) or e['no_json_file']:
                    print(f"  {park}: " + ", ".join(
                        f"{k}={e[k]}" for k in BAD_KEYS if e.get(k)))

    bad = sum(t.get(k, 0) for k in BAD_KEYS)
    if bad:
        print(f"\nINCONSISTENT ({bad} issues). "
              f"Fix: python3 scripts/fix_fire_consistency.py")
        return 1
    print("\nConsistent.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
