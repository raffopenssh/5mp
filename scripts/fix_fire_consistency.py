#!/usr/bin/env python3
"""
Repair drift between fire_groups_v5 JSON, feature_geometries and the narrative
cache. Diagnose first with scripts/check_fire_consistency.py.

What it fixes, in order:

1. Duplicate feature_ids inside a park's JSON (the re-ignition collision, see
   dedupe_feature_ids in rebuild_fire_trajectories_v5.py). Rewrites the JSON
   in place after a timestamped backup. This is what makes narrative links
   unambiguous - two groups sharing an id meant a pin resolved arbitrarily.
2. feature_geometries reload per park (load_fire_groups_to_db.py --force),
   which deletes the park's old rows first, so stale groups from rebuilds that
   were never followed by a load disappear and missing ones appear.
3. Narrative cache recompute (precompute_narratives_v5.py), because step 2
   renumbers nothing but does remove the stale rows some cached narratives
   were pointing at.

Usage:
    python3 scripts/fix_fire_consistency.py --dry-run
    python3 scripts/fix_fire_consistency.py                 # all drifted parks
    python3 scripts/fix_fire_consistency.py --park ZMB_Kafue
    python3 scripts/fix_fire_consistency.py --skip-narratives

Idempotent: a second run is a no-op. Takes ~15-25 min for all parks (the
per-park loader does river/road/settlement context enrichment).
"""

import json
import sys
import shutil
import argparse
import subprocess
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).parent.parent
SCRIPTS = BASE_DIR / "scripts"
GROUPS_DIR = BASE_DIR / "data" / "fire_groups_v5"
sys.path.insert(0, str(SCRIPTS))

from check_fire_consistency import check, BAD_KEYS  # noqa: E402


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def dedupe_json(parks, dry_run):
    """Rewrite JSON files whose feature_ids collide. Returns parks changed."""
    # Imported lazily: pulls in sklearn/scipy.
    from rebuild_fire_trajectories_v5 import dedupe_feature_ids
    changed = []
    for park in parks:
        f = GROUPS_DIR / f"{park}.json"
        if not f.exists():
            continue
        groups = json.load(open(f))
        n = dedupe_feature_ids(groups, park)
        if not n:
            continue
        changed.append((park, n))
        if dry_run:
            log(f"  [dry-run] {park}: would disambiguate {n} feature_id(s)")
            continue
        backup = f.with_suffix(f".json.bak-{datetime.now():%Y%m%d%H%M%S}")
        shutil.copy2(f, backup)
        json.dump(groups, open(f, 'w'))
        log(f"  {park}: disambiguated {n} feature_id(s) (backup {backup.name})")
    return changed


def run_loader(parks, dry_run):
    for i, park in enumerate(parks, 1):
        if dry_run:
            log(f"  [dry-run] would reload {park}")
            continue
        r = subprocess.run(
            [sys.executable, 'scripts/load_fire_groups_to_db.py',
             '--park', park, '--force'],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=900)
        tail = [l for l in r.stdout.splitlines() if 'Total:' in l]
        log(f"  [{i}/{len(parks)}] {park}: rc={r.returncode} "
            f"{tail[-1].strip() if tail else r.stderr.strip()[:120]}")


def run_narratives(parks, dry_run):
    """Single writer of fire_narrative_cache - see AGENTS.md."""
    if dry_run:
        log(f"  [dry-run] would recompute narratives for {len(parks)} parks")
        return
    for i, park in enumerate(parks, 1):
        r = subprocess.run(
            [sys.executable, 'scripts/precompute_narratives_v5.py',
             '--park', park],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            log(f"  [{i}/{len(parks)}] {park}: FAILED {r.stderr.strip()[:150]}")
        elif i % 10 == 0 or i == len(parks):
            log(f"  [{i}/{len(parks)}] narratives done through {park}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--park', help='Repair a single park')
    ap.add_argument('--all-parks', action='store_true',
                    help='Reload every park, not just drifted ones')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-narratives', action='store_true')
    args = ap.parse_args()

    log("Checking current state...")
    rep = check()

    if args.park:
        parks = [args.park]
    elif args.all_parks:
        parks = sorted(rep['parks'])
    else:
        parks = sorted(p for p, e in rep['parks'].items()
                       if any(e.get(k) for k in BAD_KEYS) or e['no_json_file'])
    log(f"{len(parks)} park(s) to repair")
    if not parks:
        return 0

    log("Step 1: dedupe duplicate feature_ids in JSON")
    dedupe_json(parks, args.dry_run)

    log("Step 2: reload feature_geometries (deletes stale rows per park)")
    run_loader(parks, args.dry_run)

    if args.skip_narratives:
        log("Step 3: skipped (--skip-narratives) - narrative links may dangle")
    else:
        log("Step 3: recompute fire narratives")
        run_narratives(parks, args.dry_run)

    if args.dry_run:
        return 0

    log("Re-checking...")
    rep2 = check()
    t = rep2['totals']
    for k in BAD_KEYS:
        log(f"  {k}: {t.get(k, 0)}")
    bad = sum(t.get(k, 0) for k in BAD_KEYS)
    log("CONSISTENT" if not bad else f"STILL {bad} issue(s)")
    return 0 if not bad else 1


if __name__ == '__main__':
    raise SystemExit(main())
