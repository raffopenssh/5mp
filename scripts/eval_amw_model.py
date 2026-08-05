#!/usr/bin/env python3
"""Evaluate the Amazon Mining Watch CNN ensemble on our data (handover action 1).

Background: every hand-picked spectral index we built is at chance against
confusers (docs/MINING_FINDINGS_2026-08.md §8.1). A learned model trained on this
exact phenomenon is the last remaining candidate, so
`docs/MINING_REBUILD_HANDOVER.md` next-action #1 is to test
`earthrise-media/mining-detector` before declaring 10 m optical ASM detection
closed. `analysis/amw_model.py` runs the model on our own Sentinel-2 stacks; this
script decides what the scores mean.

Three modes, in the order you should run them:

  --sanity N     Score N mine / N non-mine patches from the **model's own**
                 held-out labels (val + test2 + Venezuela geo-holdout, shipped as
                 data/mining_truth/amw_labels_holdout.json). This does not test
                 the model - upstream already reported specificity 0.994 /
                 sensitivity 0.895 there - it tests OUR reproduction of its input
                 pipeline (band order, harmonisation offset, cloud mask,
                 composite). If these numbers are far from upstream's, every
                 Africa number below is noise and must be discarded.

  --africa N     The measurement that matters: N IPIS field-visited gold mines vs
                 N confusers (village / burn scar / river water / adjudicated
                 bare savanna) from data/mining_truth/negatives.json - the same
                 two sets on which our indices scored AUC 0.45-0.56. Reports AUC
                 plus sensitivity/specificity at the published t=0.43.

  --manual       The 8 Chinko headwater pits (data/mining_truth/*_manual.json).
                 Note handover action #2: these points are of doubtful identity
                 (invisible at 50 cm), so read this as a probe, not a verdict.

Design notes
  * Points are scored concurrently (`--workers`); each point is ~40 HTTP range
    reads, so the wall time is network-bound, not CPU-bound. Composites are
    cached on disk by analysis/amw_model.py, so a re-run with different
    thresholds is nearly free.
  * `--jitter` scores 4 quarter-patch offsets as well as the centred patch and
    keeps the max, standing in for upstream's half-patch inference stride. A mine
    off-centre in a 480 m patch is otherwise diluted by surrounding forest.
  * Africa positives use each site's own dry-season windows (2 years), because
    bare ground is only separable in the dry season and IPIS visit dates are not
    imagery dates. Sanity positives use the label's own start/end window, which
    is what the model saw in training.
  * AUC is computed by rank sum (identical formula to
    scripts/eval_mining_detector.py --pixel-auc) so the two are comparable.

Usage
  python3 scripts/eval_amw_model.py --sanity 40 --workers 6 --json out.json
  python3 scripts/eval_amw_model.py --africa 25 --jitter --workers 6
  python3 scripts/eval_amw_model.py --manual --jitter
"""
import argparse
import json
import os
import random
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

warnings.filterwarnings("ignore")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "analysis"))
sys.path.insert(0, os.path.join(BASE, "scripts"))

import amw_model as A                                    # noqa: E402
from eval_mining_detector import load_positives, load_negatives  # noqa: E402

HOLDOUT = os.path.join(BASE, "data", "mining_truth", "amw_labels_holdout.json")
CONFUSER_CLASSES = ("village", "burn_scar", "water", "bare_savanna",
                    "sandbank", "irrigated_field")


def auc(pos, neg):
    """Rank-sum AUC. Same formula as eval_mining_detector.pixel_auc."""
    a, b = np.asarray(pos, float), np.asarray(neg, float)
    if len(a) < 3 or len(b) < 3:
        return None
    allv = np.concatenate([a, b])
    r = allv.argsort().argsort().astype(float) + 1
    return float((r[:len(a)].sum() - len(a) * (len(a) + 1) / 2) / (len(a) * len(b)))


def score_many(points, workers, jitter, verbose, tag=""):
    """Score `points` = [{lon, lat, windows, ...}]; returns them with `score`.

    The Keras model is loaded once up front: six CNNs deserialised per thread
    would be both slow and memory-hungry on a 7 GB box, and predict() is
    thread-safe for inference.
    """
    A.load_model()
    out, t0 = [], time.time()

    def work(p):
        r = A.score_point(p["lon"], p["lat"], p["windows"], jitter=jitter)
        if r is None:
            return None
        q = dict(p)
        q.pop("windows", None)
        q.update(r)
        return q

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work, p): p for p in points}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                r = f.result()
            except Exception as e:
                print(f"  {tag} error: {str(e)[:90]}", file=sys.stderr)
                continue
            if r is None:
                continue
            out.append(r)
            if verbose:
                print(f"  {tag} {i}/{len(points)} score={r['score']:.3f} "
                      f"({r.get('name') or r.get('class') or ''}) "
                      f"[{time.time() - t0:.0f}s]", file=sys.stderr)
    return out


def rates(pos, neg, thr):
    """Sensitivity / specificity at `thr`, upstream's reporting convention."""
    sens = float(np.mean([s >= thr for s in pos])) if pos else None
    spec = float(np.mean([s < thr for s in neg])) if neg else None
    return sens, spec


def summarise(name, pos, neg, thr):
    ps = [p["score"] for p in pos]
    ns = [n["score"] for n in neg]
    a = auc(ps, ns)
    sens, spec = rates(ps, ns, thr)
    print(f"\n=== {name}")
    print(f"  n_pos={len(ps)}  n_neg={len(ns)}   threshold {thr}")
    if a is not None:
        print(f"  AUC {a:.3f}")
    if ps:
        print(f"  mine     median {np.median(ps):.4f}  "
              f"p90 {np.percentile(ps, 90):.4f}  >=thr {sens:.3f}")
    if ns:
        print(f"  non-mine median {np.median(ns):.4f}  "
              f"p90 {np.percentile(ns, 90):.4f}  <thr {spec:.3f}")
    return {"n_pos": len(ps), "n_neg": len(ns), "auc": a, "threshold": thr,
            "sensitivity": sens, "specificity": spec,
            "pos_median": float(np.median(ps)) if ps else None,
            "neg_median": float(np.median(ns)) if ns else None,
            "pos": sorted(ps, reverse=True), "neg": sorted(ns, reverse=True)}


# ---------------------------------------------------------------- sanity mode
def mode_sanity(n, workers, jitter, verbose):
    if not os.path.exists(HOLDOUT):
        print(f"missing {HOLDOUT}", file=sys.stderr)
        return None
    d = json.load(open(HOLDOUT))
    rnd = random.Random(7)
    sites = list(d["sites"])
    rnd.shuffle(sites)
    pos = [s for s in sites if s["label"] == 1][:n]
    neg = [s for s in sites if s["label"] == 0][:n]
    for s in pos + neg:
        # honour each label's own imagery window: that is the composite the
        # model was trained/validated on for this point
        s["windows"] = A.window_from_dates(s["start"], s["end"])
        s["name"] = s["split"]
    P = score_many(pos, workers, jitter, verbose, "sanity+")
    N = score_many(neg, workers, jitter, verbose, "sanity-")
    r = summarise("SANITY: upstream held-out labels (Amazon), our pipeline",
                  P, N, A.THRESHOLD)
    print("  upstream reports specificity 0.994 / sensitivity 0.895 on the "
          "Venezuela holdout at this threshold (README, July 2026).")
    print("  If our numbers are far below that, the Africa result below is "
          "about OUR pipeline, not about the model.")
    r["detail"] = {"pos": P, "neg": N}
    return r


# ---------------------------------------------------------------- africa mode
def mode_africa(n, workers, jitter, verbose, years):
    rnd = random.Random(11)
    pos = [p for p in load_positives() if p["src"] != "manual"]
    rnd.shuffle(pos)
    pos = pos[:n]
    neg = [x for x in load_negatives() if x["class"] in CONFUSER_CLASSES]
    rnd.shuffle(neg)
    neg = neg[:n]
    for s in pos + neg:
        s["windows"] = A.dry_season_windows(s["lat"], n_years=years)
    P = score_many(pos, workers, jitter, verbose, "africa+")
    N = score_many(neg, workers, jitter, verbose, "africa-")
    r = summarise("AFRICA: IPIS visited gold mines vs confusers",
                  P, N, A.THRESHOLD)
    print("  compare: our spectral indices on these same two sets scored "
          "AUC 0.450-0.555 (docs/MINING_FINDINGS_2026-08.md §8.1)")
    byc = {}
    for x in N:
        byc.setdefault(x["class"], []).append(x["score"])
    if byc:
        print("  confuser scores by class:")
        for c, v in sorted(byc.items(), key=lambda kv: -np.median(kv[1])):
            print(f"    {c:16} n={len(v):>3}  median {np.median(v):.4f}  "
                  f"max {max(v):.4f}")
    r["neg_by_class"] = {c: {"n": len(v), "median": float(np.median(v)),
                            "max": float(max(v))} for c, v in byc.items()}
    r["detail"] = {"pos": P, "neg": N}
    return r


# ---------------------------------------------------------------- manual mode
def mode_manual(workers, jitter, verbose, years):
    pts = [p for p in load_positives() if p["src"] == "manual"]
    for s in pts:
        s["windows"] = A.dry_season_windows(s["lat"], n_years=years)
    P = score_many(pts, workers, jitter, verbose, "manual")
    print("\n=== MANUAL: 8 Chinko headwater pits")
    for p in sorted(P, key=lambda x: -x["score"]):
        print(f"  {p.get('name'):>12}  {p['score']:.4f}  "
              f"(center {p['score_center']:.4f}, {len(p['dates'])} dates)")
    print("  handover action #2: these 8 points are of uncertain identity - "
          "invisible even at 50 cm (§8.3) - so a low score here is ambiguous "
          "between 'model blind' and 'no pit there'.")
    return {"n": len(P), "scores": {p.get("name"): p["score"] for p in P},
            "detail": P}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sanity", type=int, metavar="N",
                    help="N mines + N non-mines from upstream's held-out labels")
    ap.add_argument("--africa", type=int, metavar="N",
                    help="N IPIS mines + N confusers")
    ap.add_argument("--manual", action="store_true",
                    help="the 8 Chinko headwater pits")
    ap.add_argument("--jitter", action="store_true",
                    help="also score 4 quarter-patch offsets, keep the max")
    ap.add_argument("--years", type=int, default=2,
                    help="dry seasons to composite for Africa modes")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--json", help="write results here")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    if not (a.sanity or a.africa or a.manual):
        ap.error("need --sanity, --africa or --manual")

    out = {"model": os.path.basename(A.MODEL), "threshold": A.THRESHOLD,
           "jitter": bool(a.jitter), "years": a.years,
           "run_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if a.sanity:
        out["sanity"] = mode_sanity(a.sanity, a.workers, a.jitter, a.verbose)
    if a.africa:
        out["africa"] = mode_africa(a.africa, a.workers, a.jitter, a.verbose,
                                    a.years)
    if a.manual:
        out["manual"] = mode_manual(a.workers, a.jitter, a.verbose, a.years)
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
        print(f"\n-> {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
