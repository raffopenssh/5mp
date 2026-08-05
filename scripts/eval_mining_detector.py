#!/usr/bin/env python3
"""Golden-set evaluator for the mining-pit ranker (Plan step 4).

Answers the only question that decides whether any of this reaches the UI:
**of the top N things the detector points at, how many are mines?** The old
scanner was never asked this; when it finally was, agreement with visited-mine
truth was 0.1% and the top hits were a sandbank and a rice paddy.

Two modes, mirroring scripts/eval_fire_trajectories.py:

  scan mode (--candidate [--baseline])
      Score a scan's ranked output against the truth sets. Reports
      precision@N, recall@radius, and - the point of the exercise - precision
      broken down by which class of negative got hit.

  pixel mode (--pixel-auc)
      The upstream question: do the features separate mine pixels from confuser
      pixels at all? analysis/eval_ipis_auc.py already measures mine-vs-random-
      background (AUC ~0.8). Random background is easy. This measures
      mine-vs-CONFUSER, which is the number that matters and is expected to be
      much worse.

Truth sets
  positives   data/ipis/{caf,cod}_mines_ipis.csv, filtered to visit_date >= 2015
              and gold-bearing (same filter as analysis/eval_ipis_auc.py), plus
              data/mining_truth/chinko_headwaters_manual.json (8 manual pits).
              IPIS positions are village/pit-cluster level, hence MATCH_KM=1.5.
              IPIS covers western CAR + eastern DRC only - NOT Chinko, NOT Boma.
  negatives   data/mining_truth/negatives.json (scripts/build_mining_negatives.py)

Reading the output
  A candidate that matches no truth point of either sign is `unknown`, not a
  false positive - outside IPIS's footprint almost everything is unknown, so
  precision here is a LOWER bound and `unknown_pct` says how blind the measure
  is. `neg@N` (share of the top N that hit a known confuser) is the honest
  failure signal and needs no coverage assumption.

Usage:
  python3 scripts/eval_mining_detector.py --candidate data/mining_candidates/CAF_Chinko.json
  python3 scripts/eval_mining_detector.py --candidate NEW.json --baseline data/mining_pits/CAF_Chinko.json
  python3 scripts/eval_mining_detector.py --snapshot data/eval/mining_baseline
  python3 scripts/eval_mining_detector.py --pixel-auc --n 30
"""
import argparse, csv, glob, json, math, os, shutil, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEG_FILE = os.path.join(BASE, "data", "mining_truth", "negatives.json")
MANUAL = os.path.join(BASE, "data", "mining_truth",
                      "chinko_headwaters_manual.json")
MATCH_KM = 1.5          # IPIS positional accuracy is village/cluster level
MANUAL_MATCH_KM = 0.4   # hand-digitised from imagery: much tighter
NEG_MATCH_KM = 0.5
AT_N = (10, 25, 50, 100, 200)


def km(a, b):
    return math.hypot((a[0] - b[0]) * 111 * math.cos(math.radians(a[1])),
                      (a[1] - b[1]) * 111)


# ------------------------------------------------------------------ truth sets
def load_positives(gold_only=True, since="2015"):
    pos = []
    for cc in ("caf", "cod"):
        p = os.path.join(BASE, "data", "ipis", f"{cc}_mines_ipis.csv")
        if not os.path.exists(p):
            continue
        for r in csv.DictReader(open(p)):
            try:
                lo, la = float(r["longitude"]), float(r["latitude"])
            except Exception:
                continue
            if since and (r.get("visit_date") or "") < since:
                continue
            mins = r.get("minerals") or ""
            if gold_only and "Or" not in mins:
                continue
            pos.append({"lon": lo, "lat": la, "src": f"ipis_{cc}",
                        "name": r.get("name"), "match_km": MATCH_KM})
    if os.path.exists(MANUAL):
        for s in json.load(open(MANUAL))["sites"]:
            pos.append({"lon": s["lon"], "lat": s["lat"], "src": "manual",
                        "name": s.get("id"), "match_km": MANUAL_MATCH_KM})
    return pos


def load_negatives():
    if not os.path.exists(NEG_FILE):
        print(f"no {NEG_FILE}: run scripts/build_mining_negatives.py",
              file=sys.stderr)
        return []
    return json.load(open(NEG_FILE))["sites"]


def in_bbox(p, bbox, pad_km=5.0):
    if not bbox:
        return True
    pad = pad_km / 111.0
    return (bbox[0] - pad <= p["lon"] <= bbox[2] + pad
            and bbox[1] - pad <= p["lat"] <= bbox[3] + pad)


# --------------------------------------------------------------- scan scoring
def sites_of(scan):
    """Ranked candidate list, tolerant of both output schemas."""
    ss = scan.get("sites", [])
    key = ("rank_score" if ss and "rank_score" in ss[0] else
           "score" if ss and "score" in ss[0] else None)
    if key:
        ss = sorted(ss, key=lambda s: -s.get(key, 0))
    return ss


def scan_bbox(sites, pad=0.05):
    if not sites:
        return None
    lo = [s["lon"] for s in sites]
    la = [s["lat"] for s in sites]
    return (min(lo) - pad, min(la) - pad, max(lo) + pad, max(la) + pad)


def evaluate(scan, pos, neg, verbose=False, self_scan=None):
    """Label each ranked candidate pos/neg/unknown and summarise.

    `self_scan` = basename of the file these candidates came from. Negatives
    carrying `from_scan == self_scan` were adjudicated FROM this very output, so
    counting them as errors is circular - the scan is being graded on the
    homework it set. They are reported separately as `self_adjudicated`, which
    is still useful ("a human rejected the top 12") but must not be compared
    against a baseline that never got the same treatment.
    """
    sites = sites_of(scan)
    bbox = scan_bbox(sites)
    # Only truth inside the scanned area can be found or missed. Scoring a
    # Chinko scan against 7,000 DRC mines it never looked at would report a
    # meaningless recall of ~0.
    pos_in = [p for p in pos if in_bbox(p, bbox)]
    neg_in = [n for n in neg if in_bbox(n, bbox)]

    labels = []
    for s in sites:
        q = (s["lon"], s["lat"])
        bp = min(((km(q, (p["lon"], p["lat"])), p) for p in pos_in),
                 default=(9e9, None), key=lambda t: t[0])
        bn = min(((km(q, (n["lon"], n["lat"])), n) for n in neg_in),
                 default=(9e9, None), key=lambda t: t[0])
        if bp[1] is not None and bp[0] <= bp[1]["match_km"]:
            labels.append(("pos", bp[0], bp[1]))
        elif bn[1] is not None and bn[0] <= NEG_MATCH_KM:
            labels.append(("neg", bn[0], bn[1]))
        else:
            labels.append(("unknown", bp[0], None))

    res = {"park_id": scan.get("park_id"), "version": scan.get("version"),
           "n_sites": len(sites), "pos_in_scope": len(pos_in),
           "neg_in_scope": len(neg_in), "at": {}, "neg_classes": {}}
    for n in AT_N:
        if n > len(labels):
            continue
        top = labels[:n]
        npos = sum(1 for t in top if t[0] == "pos")
        nneg = sum(1 for t in top if t[0] == "neg" and not (
            self_scan and t[2].get("from_scan") == self_scan))
        nself = sum(1 for t in top if t[0] == "neg" and (
            self_scan and t[2].get("from_scan") == self_scan))
        res["at"][n] = {
            "precision": round(npos / n, 3),
            "neg_rate": round(nneg / n, 3),
            "self_adj_rate": round(nself / n, 3),
            "unknown_pct": round(100.0 * (n - npos - nneg - nself) / n, 1),
        }
    res["self_adjudicated"] = 0
    for t in labels:
        if t[0] != "neg":
            continue
        if self_scan and t[2].get("from_scan") == self_scan:
            res["self_adjudicated"] += 1
            continue
        c = t[2]["class"]
        d = res["neg_classes"].setdefault(
            c, {"hits": 0, "confidence": t[2].get("confidence")})
        d["hits"] += 1
    # recall: a positive is found if ANY candidate is within its match radius
    found = 0
    misses = []
    for p in pos_in:
        d = min((km((p["lon"], p["lat"]), (s["lon"], s["lat"])) for s in sites),
                default=9e9)
        if d <= p["match_km"]:
            found += 1
        else:
            misses.append((round(d, 2), p.get("src"), p.get("name")))
    res["recall"] = round(found / len(pos_in), 3) if pos_in else None
    res["found"] = found
    res["misses"] = sorted(misses)[:10]
    # rank of the best true positive: how deep a ranger must read
    ranks = [i for i, t in enumerate(labels) if t[0] == "pos"]
    res["first_pos_rank"] = ranks[0] if ranks else None
    return res


def report(name, r):
    print(f"\n=== {name}  ({r['park_id']} / {r.get('version') or 'legacy'})")
    print(f"  candidates {r['n_sites']}   truth in scope: "
          f"{r['pos_in_scope']} pos / {r['neg_in_scope']} neg")
    if not r["pos_in_scope"]:
        print("  NO POSITIVE TRUTH IN SCOPE - precision/recall are undefined "
              "here; only neg_rate is meaningful.")
    print(f"  {'N':>5} {'prec':>7} {'neg':>7} {'selfadj':>8} {'unknown%':>9}")
    for n, d in r["at"].items():
        print(f"  {n:>5} {d['precision']:>7.3f} {d['neg_rate']:>7.3f} "
              f"{d['self_adj_rate']:>8.3f} {d['unknown_pct']:>9.1f}")
    if r.get("self_adjudicated"):
        print(f"  {r['self_adjudicated']} candidates were themselves the source "
              f"of adjudicated negatives (circular; excluded from neg_rate)")
    print(f"  recall {r['recall']}  ({r['found']}/{r['pos_in_scope']})"
          f"   first true positive at rank {r['first_pos_rank']}")
    if r["neg_classes"]:
        print("  negatives hit by class:")
        for c, d in sorted(r["neg_classes"].items(), key=lambda kv: -kv[1]["hits"]):
            print(f"    {c:16} {d['hits']:>4}   [{d['confidence']}]")
    if r["misses"]:
        print("  nearest miss (km, src, name):")
        for m in r["misses"][:5]:
            print(f"    {m[0]:>7} {m[1]} {m[2]}")


def load_scan(path):
    if os.path.isdir(path):
        out = []
        for f in sorted(glob.glob(os.path.join(path, "*.json"))):
            if os.path.basename(f) == "state.json":
                continue
            d = json.load(open(f))
            d["_path"] = f
            out.append(d)
        return out
    d = json.load(open(path))
    d["_path"] = path
    return [d]


# ------------------------------------------------------------------ pixel mode
def pixel_auc(n_pos, n_neg, verbose=False):
    """Mine pixels vs CONFUSER pixels, using the scanner's own feature code."""
    import numpy as np
    sys.path.insert(0, os.path.join(BASE, "analysis"))
    import mining_features as mf

    def sample(pts, tag):
        rows = []
        for i, p in enumerate(pts, 1):
            bb = (p["lon"] - 0.002, p["lat"] - 0.002,
                  p["lon"] + 0.002, p["lat"] + 0.002)
            try:
                comp = mf.median_composite(bb, mf.dry_season_windows(p["lat"]),
                                           verbose=False)
            except Exception as ex:
                print(f"  {tag} {i}: {str(ex)[:50]}", file=sys.stderr)
                continue
            if comp is None:
                continue
            F = mf.features(comp)
            rec = {}
            for k in mf.FEATURES:
                v = F[k]
                v = v[np.isfinite(v)]
                if v.size:
                    # 90th pct within the ~440 m box: IPIS points are cluster
                    # level, so the mine need not be at the exact pixel
                    rec[k] = float(np.percentile(v, 90))
            if rec:
                rows.append(rec)
            if verbose:
                print(f"  {tag} {len(rows)}/{n_pos if tag=='pos' else n_neg} "
                      f"{p.get('name') or p.get('class')}", file=sys.stderr)
            if len(rows) >= (n_pos if tag == "pos" else n_neg):
                break
        return rows

    import random
    rnd = random.Random(11)
    pos = [p for p in load_positives() if p["src"] != "manual"]
    rnd.shuffle(pos)
    pos += [p for p in load_positives() if p["src"] == "manual"]
    neg = [n for n in load_negatives()
           if n["class"] in ("village", "burn_scar", "water", "bare_savanna",
                             "sandbank", "irrigated_field")]
    rnd.shuffle(neg)
    P, N = sample(pos, "pos"), sample(neg, "neg")
    print(f"\n=== pixel AUC, mine vs CONFUSER  (n_pos={len(P)}, n_neg={len(N)})")
    print("  (analysis/eval_ipis_auc.py measures mine vs RANDOM background and "
          "gets ~0.8; random background is the easy comparison)")
    for k in mf.FEATURES:
        a = np.array([r[k] for r in P if k in r])
        b = np.array([r[k] for r in N if k in r])
        if len(a) < 3 or len(b) < 3:
            continue
        allv = np.concatenate([a, b])
        rank = allv.argsort().argsort().astype(float) + 1
        auc = (rank[:len(a)].sum() - len(a) * (len(a) + 1) / 2) / (len(a) * len(b))
        print(f"  {k:10} AUC={auc:.3f}  (pos median {np.median(a):.3f} "
              f"vs neg {np.median(b):.3f})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", help="scan json or directory")
    ap.add_argument("--baseline", help="scan json or directory to compare")
    ap.add_argument("--snapshot", help="copy current data/mining_candidates here")
    ap.add_argument("--pixel-auc", action="store_true")
    ap.add_argument("--n", type=int, default=30, help="pixel-mode sample size")
    ap.add_argument("--json", help="write results here")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    if a.snapshot:
        src = os.path.join(BASE, "data", "mining_candidates")
        os.makedirs(a.snapshot, exist_ok=True)
        n = 0
        for f in glob.glob(os.path.join(src, "*.json")):
            shutil.copy2(f, a.snapshot)
            n += 1
        print(f"snapshotted {n} files -> {a.snapshot}")
        if not (a.candidate or a.pixel_auc):
            return 0

    if a.pixel_auc:
        pixel_auc(a.n, a.n, a.verbose)
        if not a.candidate:
            return 0

    if not a.candidate:
        ap.error("need --candidate, --snapshot or --pixel-auc")

    pos, neg = load_positives(), load_negatives()
    print(f"truth: {len(pos)} positives "
          f"({sum(1 for p in pos if p['src'] == 'manual')} manual), "
          f"{len(neg)} negatives")
    out = {"candidate": {}, "baseline": {}}
    for tag, path in (("baseline", a.baseline), ("candidate", a.candidate)):
        if not path:
            continue
        for scan in load_scan(path):
            r = evaluate(scan, pos, neg, a.verbose,
                         self_scan=os.path.basename(scan.get("_path", "")))
            report(f"{tag}: {scan.get('park_id')}", r)
            out[tag][scan.get("park_id")] = r

    if a.baseline and a.candidate:
        print("\n=== delta (candidate - baseline)")
        for pid, c in out["candidate"].items():
            b = out["baseline"].get(pid)
            if not b:
                continue
            for n in AT_N:
                if n in c["at"] and n in b["at"]:
                    dp = c["at"][n]["precision"] - b["at"][n]["precision"]
                    dn = c["at"][n]["neg_rate"] - b["at"][n]["neg_rate"]
                    print(f"  {pid} @{n}: precision {dp:+.3f}  neg_rate {dn:+.3f}")
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
        print(f"-> {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
