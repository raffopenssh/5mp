"""Re-derive fire_detections.in_protected_area from today's boundaries (F10).

WHAT THE FLAG WAS. `in_protected_area` is written at ingest as `dist_km == 0.0`
— a stored answer from whichever rule ran that night. That makes it a
PROVENANCE LABEL naming an instrument, and like every such label it drifts from
the thing it describes: 469,692 flagged rows (5.83%) are not inside the polygon
the app draws today, 433,632 of them from one batch (2026-02-26 -> 2026-07-03)
written by the bbox+0.5deg `_find_park` that ParkAssigner replaced in 858eb69.
CMR_Nki — the park the test list calls pristine — kept 117 of them after the
queries adopted `AND +in_protected_area = 1`, and 117 fires in a rainforest
park still reads as fire.

WHAT IT IS AFTER THIS RUNS. A point-in-polygon answer against
data/keystones_with_boundaries.json, recomputed. That is a DERIVED value, so it
inherits the derived-value rule: it must be re-derivable and it must say which
input it came from. data/fire_containment_state.json records the boundary
file's SHA-256 and the counts; when the boundary file changes the state no
longer matches and this script is due again (the same mechanism as
ghsl_tiles.PIPELINE_VERSION). scripts/audit_fire_containment.py is the
independent instrument and is NOT this code — it re-measures rather than
trusting the stamp.

    python3 scripts/rederive_fire_containment.py --dry-run     # counts only
    python3 scripts/rederive_fire_containment.py --park CMR_Nki
    python3 scripts/rederive_fire_containment.py               # all parks

Both directions are corrected: a flagged row outside the polygon is cleared,
and an unflagged row inside it is set. Only correcting one direction would move
every count in the app in one direction and look like a trend.

Writes are batched and committed per park so SQLite's single writer stays
available to the app (invariant 16). A park whose rows cannot be read is left
ALONE and reported UNFINISHED rather than cleared — an empty read must never
be written as "nothing is inside" (invariant 1).
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import shape

BASE = Path(__file__).parent.parent
DB_PATH = BASE / "db.sqlite3"
KEYSTONES = BASE / "data" / "keystones_with_boundaries.json"
STATE = BASE / "data" / "fire_containment_state.json"
BATCH = 20000


def boundary_digest():
    h = hashlib.sha256()
    with open(KEYSTONES, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_boundaries():
    with open(KEYSTONES) as f:
        return {p["id"]: shape(p["geometry"])
                for p in json.load(f) if p.get("geometry")}


def rederive_park(conn, park, geom, dry):
    rows = conn.execute(
        "SELECT id, longitude, latitude, COALESCE(in_protected_area,0) "
        "FROM fire_detections WHERE protected_area_id = ?", (park,)).fetchall()
    if not rows:
        return None
    a = np.asarray(rows, dtype=float)
    inside = shapely.contains_xy(geom, a[:, 1], a[:, 2])
    flagged = a[:, 3] > 0
    to_clear = a[flagged & ~inside, 0].astype(np.int64)
    to_set = a[~flagged & inside, 0].astype(np.int64)
    if not dry:
        for ids, val in ((to_clear, 0), (to_set, 1)):
            for i in range(0, len(ids), BATCH):
                chunk = ids[i:i + BATCH].tolist()
                conn.execute(
                    "UPDATE fire_detections SET in_protected_area = ? WHERE id IN (%s)"
                    % ",".join("?" * len(chunk)), [val] + chunk)
                conn.commit()   # yield the writer between batches
    return {"park": park, "rows": len(rows), "inside": int(inside.sum()),
            "was_flagged": int(flagged.sum()),
            "cleared": len(to_clear), "set": len(to_set)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--park", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    geoms = load_boundaries()
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    parks = args.park or [r[0] for r in conn.execute(
        "SELECT DISTINCT protected_area_id FROM fire_detections "
        "WHERE protected_area_id IS NOT NULL AND protected_area_id != ''")]

    out, unmeasured, t0 = [], [], time.time()
    for i, p in enumerate(sorted(parks), 1):
        g = geoms.get(p)
        if g is None:
            # No polygon means containment is UNMEASURED, which is not the same
            # as "nothing is inside": leave every flag exactly as it was.
            unmeasured.append(p)
            print(f"[{i}/{len(parks)}] {p:34} no boundary — left untouched", flush=True)
            continue
        r = rederive_park(conn, p, g, args.dry_run)
        if r is None:
            continue
        out.append(r)
        print(f"[{i}/{len(parks)}] {p:34} rows {r['rows']:9,} inside {r['inside']:8,} "
              f"cleared {r['cleared']:7,} set {r['set']:6,}", flush=True)

    if not out:
        print("UNFINISHED: no parks with rows and a boundary; nothing written",
              file=sys.stderr)
        return 1

    cleared = sum(r["cleared"] for r in out)
    was = sum(r["was_flagged"] for r in out)
    print(f"\n{'(dry run) ' if args.dry_run else ''}{len(out)} park(s) in "
          f"{time.time()-t0:.0f}s · flagged before {was:,} · "
          f"cleared {cleared:,} · set {sum(r['set'] for r in out):,} · "
          f"inside now {sum(r['inside'] for r in out):,}")
    if unmeasured:
        print(f"{len(unmeasured)} park(s) had no boundary and were NOT touched: "
              + ", ".join(sorted(unmeasured)[:8]) + ("…" if len(unmeasured) > 8 else ""))

    if not args.dry_run:
        STATE.write_text(json.dumps({
            "boundary_sha256": boundary_digest(),
            "boundary_file": str(KEYSTONES.relative_to(BASE)),
            "parks_rederived": len(out),
            "parks_without_boundary": sorted(unmeasured),
            "flagged_after": sum(r["inside"] for r in out),
            "rederived_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, indent=2) + "\n")
        print(f"wrote {STATE.relative_to(BASE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
