#!/usr/bin/env python3
"""Find GFW tile-cache entries that were silently truncated by the API and
queue their consumers for healing.

The GFW query endpoint caps large answers (observed at exactly 40,000 and
45,000 rows) while still returning status=success, so a scan assembled from
such a tile under-reports alerts in a sharp rectangular hole (AGENTS.md
invariant 8: a truncated answer is indistinguishable from a complete one).
analysis/gfw_alerts.py now subdivides at TRUNCATION_FLOOR and marks healthy
cache entries complete=true; this script deals with the *legacy* damage:

- park scans built from a suspect tile  -> data/gfw_alerts/heal_queue.json,
  drained by the nightly `gfw_alerts.py --rotate` one park per day (API cap).
- AOI scans built from a suspect tile   -> aoi_datasets gfw+deforestation
  reset to pending; the daily aoi_runner re-walks the tiles (cache hits are
  free, suspect tiles refetch with subdivision) under its own budget.

Idempotent and cheap (no API calls) -- safe to run any time:
    python3 scripts/gfw_find_truncated.py [--dry-run]
"""
import argparse, glob, json, os, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
sys.path.insert(0, os.path.join(BASE, "analysis"))
sys.path.insert(0, os.path.join(BASE, "scripts"))
from gfw_alerts import TRUNCATION_FLOOR, HEAL_FILE  # noqa: E402


def suspect_tiles():
    """[(w, s, since)] cache tiles at/over the cap and not marked complete."""
    out = []
    for p in glob.glob("data/gfw_tiles/*.json"):
        try:
            d = json.load(open(p))
            rows = d["rows"]
        except Exception:
            continue
        if len(rows) >= TRUNCATION_FLOOR and not d.get("complete"):
            w, s, since = os.path.basename(p)[:-5].split("_", 2)
            out.append((float(w), float(s), since))
    return out


def affected_scans(tiles):
    """{park_or_aoi_id: {n_tiles, is_aoi}} for scans overlapping a suspect
    tile fetched with the same `since` cutoff."""
    hit = {}
    for p in glob.glob("data/gfw_alerts/*.json"):
        if os.path.basename(p) in ("state.json", "heal_queue.json"):
            continue
        try:
            d = json.load(open(p))
        except Exception:
            continue
        if "bbox" not in d:
            continue
        w0, s0, e0, n0 = d["bbox"]
        n = sum(1 for (w, s, since) in tiles
                if since == d.get("since")
                and w < e0 and w + 0.5 > w0 and s < n0 and s + 0.5 > s0)
        if n:
            hit[d["park_id"]] = {"n_tiles": n, "is_aoi": bool(d.get("is_aoi"))}
    return hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tiles = suspect_tiles()
    hit = affected_scans(tiles)
    print(f"{len(tiles)} suspect tile(s); {len(hit)} affected scan(s)")
    for pid, v in sorted(hit.items()):
        print(f"  {pid}: {v['n_tiles']} tile(s)" + (" [AOI]" if v["is_aoi"] else ""))
    if args.dry_run:
        return

    # Parks -> heal queue for the nightly rotation (one per day).
    parks = sorted(pid for pid, v in hit.items() if not v["is_aoi"])
    try:
        q = json.load(open(HEAL_FILE))
    except Exception:
        q = []
    q += [pid for pid in parks if pid not in q]
    os.makedirs(os.path.dirname(HEAL_FILE), exist_ok=True)
    json.dump(q, open(HEAL_FILE, "w"), indent=1)
    print(f"heal queue: {q}")

    # AOIs -> reset gfw (+ dependent deforestation) to pending; the aoi_runner
    # cron re-walks the tiles under its own budget. cursor=NULL restarts the
    # tile walk; completed tiles are cache hits, suspect ones refetch
    # subdivided.
    aois = sorted(pid for pid, v in hit.items() if v["is_aoi"])
    if aois:
        import aoi_lib
        conn = aoi_lib.connect()
        for aid in aois:
            conn.execute(
                "UPDATE aoi_datasets SET state='pending', cursor=NULL, "
                "units_done=0, detail='re-queued: truncated GFW tiles' "
                "WHERE aoi_id=? AND dataset IN ('gfw','deforestation') "
                "AND state='done'", (aid,))
            print(f"re-queued AOI {aid}: gfw + deforestation")
        conn.commit()


if __name__ == "__main__":
    main()
