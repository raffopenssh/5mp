#!/usr/bin/env python3
"""
Calibrate the fire-link evidence model: P(link feature | real) / P(link feature | noise).

Runs the tracker with evidence scoring OFF on real detections and on
day-shuffled detections (same field, no continuity - see eval_fire_null.py),
records for every accepted link the nearest detection-to-detection distance
between the two slice-clusters (per day of gap), and bins the ratio of the two
histograms. That ratio is the likelihood ratio the builder then attaches to
each link as log2 evidence.

Why this feature: a fire front advances from its own edge and a herder's
ignition chain is a string of adjacent burns, so consecutive-day clusters of
one real fire ABUT (nearest pixels <1 km). Two unrelated fires that happen to
sit within the 13 km gate do not. Speed is deliberately not a feature:
herders walk 800 km one way and hunters cover 80 km in a day.

Measured 2026-09-14 (XSA box, CAF_Chinko, TZA_Serengeti): LR ~6 at <0.5 km,
~4 at <1 km, ~2.5 at <2 km, ~1.3 at <8 km, <1 beyond ~10 km - the same shape
in all three regimes, which is why one table serves all parks.

    python3 scripts/calibrate_fire_link_lr.py            # writes data/fire_link_lr.json
    python3 scripts/calibrate_fire_link_lr.py --dry-run  # print only
"""
import argparse
import hashlib
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import rebuild_fire_trajectories_v5 as B  # noqa: E402
import eval_fire_null as E  # noqa: E402

OUT = Path(__file__).parent.parent / "data" / "fire_link_lr.json"

# Calibration set: one dense AOI box, one transhumance park, one grassland park.
CALIB = [
    dict(aoi="XSA_Study_Area", park=None, date_from="2024-01-01", date_to="2024-03-31", bbox="24,6,26,8"),
    dict(aoi=None, park="CAF_Chinko", date_from="2023-11-01", date_to="2024-04-30", bbox=None),
    dict(aoi=None, park="TZA_Serengeti", date_from="2024-06-01", date_to="2024-10-31", bbox=None),
]
BINS_KM = [0, 0.25, 0.5, 1, 2, 3, 4, 6, 8, 12, 16, float("inf")]


def collect_links(fires, pid, geom):
    rec = []
    orig = B.Track.extend

    def ext(self, dc, margin=1.0, llr=0.0):
        gap = max(dc["t"] - self.last_t, B.GATE_MIN_GAP_DAYS)
        rec.append(B._min_pair_km(self.last_dc, dc) / gap)
        orig(self, dc, margin, llr)
    B.Track.extend = ext
    try:
        B.build_tracks(B.daily_clusters(fires))
    finally:
        B.Track.extend = orig
    return np.array(rec)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seeds", type=int, default=2)
    args = ap.parse_args()

    # Score nothing while calibrating: the LR must describe the *gated* links
    # of the tracker as it selects them, not links already filtered by an
    # earlier LR table.
    B.LINK_LR = None
    B.LLR_COST_BITS_KM = 0.0
    B.SPRT_CUT_BITS = 0.0

    real_all, null_all, sets = [], [], []
    for c in CALIB:
        a = argparse.Namespace(**c)
        pid, geom, fires = E._load(a)
        r = collect_links(fires, pid, geom)
        ns = [collect_links(E.shuffle_days(fires, 1 + s), pid, geom) for s in range(args.seeds)]
        n = np.concatenate(ns)
        real_all.append(r)
        null_all.append(n)
        sets.append(dict(c, detections=len(fires), real_links=int(len(r)), null_links=int(len(n))))
        print(f"{pid}: {len(fires):,} detections, {len(r):,} real links, {len(n):,} null links")

    r = np.concatenate(real_all)
    n = np.concatenate(null_all)
    hr, _ = np.histogram(r, BINS_KM)
    hn, _ = np.histogram(n, BINS_KM)
    pr = (hr + 0.5) / (hr.sum() + 0.5 * len(hr))
    pn = (hn + 0.5) / (hn.sum() + 0.5 * len(hn))
    lr = pr / pn
    print(f"\n{'min_pair_km/day':>18}{'real%':>8}{'null%':>8}{'LR':>7}{'log2':>7}")
    for i in range(len(lr)):
        lo, hi = BINS_KM[i], BINS_KM[i + 1]
        print(f"{f'[{lo}, {hi})':>18}{100*pr[i]:8.1f}{100*pn[i]:8.1f}{lr[i]:7.2f}{np.log2(lr[i]):7.2f}")

    doc = {
        "feature": "min_pair_km_per_day",
        "description": "nearest detection-to-detection distance between consecutive linked "
                       "slice-clusters, divided by the gap in days; LR = P(real)/P(day-shuffled)",
        "bins_km": [b if b != float("inf") else None for b in BINS_KM],
        "log2_lr": [round(float(v), 3) for v in np.log2(lr)],
        "real_hist": hr.tolist(), "null_hist": hn.tolist(),
        "calibration": sets,
        "builder_sha256": hashlib.sha256(Path(B.__file__).read_bytes()).hexdigest()[:16],
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if args.dry_run:
        return
    OUT.write_text(json.dumps(doc, indent=1))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
