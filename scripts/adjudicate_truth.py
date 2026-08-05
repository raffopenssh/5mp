#!/usr/bin/env python3
"""Adjudicate the mining truth sets in imagery (handover action 2).

Why this is a blocker, not a nicety
-----------------------------------
Every number in docs/MINING_FINDINGS_2026-08.md rests on two truth sets that
have never been checked against imagery:

  * **IPIS visited sites** (`data/ipis/*.csv`, 8,077) are *field* visits. Their
    coordinates are village / pit-cluster level, which is why the evaluator uses
    MATCH_KM=1.5. Some fraction of them have no visible pit at all at the given
    point - and an at-chance AUC measured against invisible positives says
    nothing about the detector.
  * **The 8 Chinko headwater pits** are invisible even at ~50 cm Esri resolution
    (§8.3). They came from a KML, not from imagery interpretation. §6's
    93.7-99.5 flow-accumulation "validation" uses the same points and inherits
    the doubt.

So: before trusting *any* evaluator - ours or the AMW CNN's - decide which truth
points show a pit. This script produces the contact sheets to look at and a
verdict file to record what you saw.

Workflow
  1. python3 scripts/adjudicate_truth.py --sheets --set ipis --n 48
     -> analysis/out/adjudicate/ipis_000.png ... (12 per sheet, crosshaired,
        each cell labelled with its verdict key)
  2. Look at them. For each key, decide: pit | maybe | no_pit | unclear_imagery
  3. python3 scripts/adjudicate_truth.py --verdict ipis_caf_0031=pit \
         --verdict ipis_caf_0032=no_pit --note "z17, spoil heaps obvious"
     (or edit data/mining_truth/adjudications.json directly - it is plain JSON)
  4. python3 scripts/adjudicate_truth.py --status
     -> how many of each verdict, and the *visible* subset written to
        data/mining_truth/visible_positives.json for evaluators to use.

Design choices
  * Verdicts live in one append-only-ish JSON keyed by a stable site key
    (`ipis_<cc>_<row>`, `manual_<id>`) so re-running the sheets never renumbers
    anything. Renumbering is what makes visual adjudication unreproducible.
  * `unclear_imagery` is a distinct verdict from `no_pit`. Conflating them was
    exactly the §8.3 mistake: "I cannot see it" is not "it is not there", and
    only `no_pit` should ever remove a point from the positive set.
  * Two zoom levels are rendered per site by default (z16 ~ 1 km context, z18 ~
    250 m detail): a hand-dug shaft field is only recognisable at the second, and
    a rice paddy or sandbank is only recognisable at the first.
  * This script never edits the source truth files. It writes a *derived*
    `visible_positives.json`; the originals stay canonical.
"""
import argparse
import csv
import json
import os
import random
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "analysis"))

TRUTH_DIR = os.path.join(BASE, "data", "mining_truth")
ADJ = os.path.join(TRUTH_DIR, "adjudications.json")
VISIBLE = os.path.join(TRUTH_DIR, "visible_positives.json")
MANUAL = os.path.join(TRUTH_DIR, "chinko_headwaters_manual.json")
OUTDIR = os.path.join(BASE, "analysis", "out", "adjudicate")
VERDICTS = ("pit", "maybe", "no_pit", "unclear_imagery")
PER_SHEET = 12


# ------------------------------------------------------------------ truth I/O
def ipis_sites(since="2015", gold_only=True):
    """IPIS rows with a stable key. Key = file + row index, so the key survives
    re-filtering (a different `--since` must not renumber earlier verdicts)."""
    out = []
    for cc in ("caf", "cod"):
        p = os.path.join(BASE, "data", "ipis", f"{cc}_mines_ipis.csv")
        if not os.path.exists(p):
            continue
        for i, r in enumerate(csv.DictReader(open(p))):
            try:
                lon, lat = float(r["longitude"]), float(r["latitude"])
            except Exception:
                continue
            if since and (r.get("visit_date") or "") < since:
                continue
            if gold_only and "Or" not in (r.get("minerals") or ""):
                continue
            out.append({"key": f"ipis_{cc}_{i:05d}", "lon": lon, "lat": lat,
                        "set": "ipis", "name": r.get("name"),
                        "visit_date": r.get("visit_date"),
                        "minerals": r.get("minerals")})
    return out


def manual_sites():
    if not os.path.exists(MANUAL):
        return []
    return [{"key": f"manual_{s['id']}", "lon": s["lon"], "lat": s["lat"],
             "set": "manual", "name": s.get("id")}
            for s in json.load(open(MANUAL))["sites"]]


def all_sites(which):
    s = []
    if which in ("ipis", "all"):
        s += ipis_sites()
    if which in ("manual", "all"):
        s += manual_sites()
    return s


def load_adj():
    if os.path.exists(ADJ):
        return json.load(open(ADJ))
    return {"note": "Visual adjudication of mining truth points "
                    "(scripts/adjudicate_truth.py). verdict: pit | maybe | "
                    "no_pit | unclear_imagery. 'unclear_imagery' is NOT "
                    "'no_pit' - only no_pit removes a positive.",
            "verdicts": {}}


def save_adj(d):
    os.makedirs(TRUTH_DIR, exist_ok=True)
    json.dump(d, open(ADJ, "w"), indent=1, sort_keys=True)


# -------------------------------------------------------------------- sheets
def make_sheets(sites, n, zooms, skip, seed=13):
    """Contact sheets for the next `n` un-adjudicated sites, one per zoom."""
    import chip_grid
    adj = load_adj()["verdicts"]
    todo = [s for s in sites if s["key"] not in adj]
    random.Random(seed).shuffle(todo)      # unbiased sample, reproducible
    todo = todo[skip:skip + n]
    if not todo:
        print("nothing left to adjudicate in this set")
        return []
    os.makedirs(OUTDIR, exist_ok=True)
    made = []
    for z in zooms:
        half = 0.0045 if z <= 16 else 0.0012
        for b in range(0, len(todo), PER_SHEET):
            batch = todo[b:b + PER_SHEET]
            out = os.path.join(OUTDIR,
                               f"{batch[0]['set']}_z{z}_{skip + b:04d}.png")
            from PIL import Image
            cols = min(4, len(batch))
            rows_n = (len(batch) + cols - 1) // cols
            C = chip_grid.CELL
            sheet = Image.new("RGB", (cols * C, rows_n * C), (20, 20, 20))
            for i, s in enumerate(batch):
                lbl = f"{s['key']} {s['lat']:.4f},{s['lon']:.4f}"
                try:
                    im = chip_grid.chip(s["lon"], s["lat"], z, half, lbl)
                except Exception as ex:
                    print(f"  chip fail {s['key']}: {str(ex)[:60]}",
                          file=sys.stderr)
                    continue
                sheet.paste(im, ((i % cols) * C, (i // cols) * C))
            sheet.save(out)
            print(out)
            made.append(out)
    print(f"\n{len(todo)} sites pending. Record verdicts with:")
    print(f"  python3 scripts/adjudicate_truth.py --verdict "
          f"{todo[0]['key']}=pit --verdict {todo[min(1, len(todo)-1)]['key']}"
          f"=no_pit")
    return made


# -------------------------------------------------------------------- status
def write_visible(sites):
    """Derived positive set: everything except adjudicated `no_pit`.

    `maybe` and `unclear_imagery` are KEPT - excluding them would quietly turn
    "we could not tell" into "not a mine", which is the failure mode §8.3 warns
    about. The file records each point's verdict so an evaluator can restrict
    further (e.g. verdict=='pit' only) explicitly rather than by accident.
    """
    adj = load_adj()["verdicts"]
    keep = []
    for s in sites:
        v = (adj.get(s["key"]) or {}).get("verdict")
        if v == "no_pit":
            continue
        q = dict(s)
        q["verdict"] = v
        keep.append(q)
    json.dump({"note": "Positives minus imagery-adjudicated no_pit. "
                       "verdict=null means not yet looked at; 'maybe' and "
                       "'unclear_imagery' are retained on purpose "
                       "(see scripts/adjudicate_truth.py docstring).",
               "n": len(keep), "sites": keep},
              open(VISIBLE, "w"), indent=1)
    return keep


def status(sites):
    adj = load_adj()["verdicts"]
    counts = {v: 0 for v in VERDICTS}
    counts["(unseen)"] = 0
    per_set = {}
    for s in sites:
        v = (adj.get(s["key"]) or {}).get("verdict") or "(unseen)"
        counts[v] = counts.get(v, 0) + 1
        per_set.setdefault(s["set"], {}).setdefault(v, 0)
        per_set[s["set"]][v] += 1
    print(f"truth points: {len(sites)}")
    for v, c in counts.items():
        print(f"  {v:16} {c:>5}")
    for st, d in per_set.items():
        print(f"  [{st}] " + "  ".join(f"{k}={v}" for k, v in sorted(d.items())))
    seen = len(sites) - counts["(unseen)"]
    if seen:
        pit = counts.get("pit", 0) + counts.get("maybe", 0)
        print(f"\nof {seen} looked at, {pit} show a pit or maybe "
              f"({100.0 * pit / seen:.0f}%) - this is the number that decides "
              f"how much any AUC against these positives is worth")
    keep = write_visible(sites)
    print(f"-> {VISIBLE} ({len(keep)} positives retained)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="all", choices=("ipis", "manual", "all"))
    ap.add_argument("--sheets", action="store_true",
                    help="render contact sheets for un-adjudicated sites")
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--zoom", default="16,18",
                    help="comma-separated Esri zooms (default context+detail)")
    ap.add_argument("--verdict", action="append", default=[],
                    metavar="KEY=VERDICT")
    ap.add_argument("--note", default="", help="attached to --verdict entries")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    sites = all_sites(a.set)
    if not sites:
        print("no truth points found", file=sys.stderr)
        return 1

    if a.verdict:
        d = load_adj()
        known = {s["key"] for s in all_sites("all")}
        for kv in a.verdict:
            if "=" not in kv:
                ap.error(f"--verdict wants KEY=VERDICT, got {kv!r}")
            k, v = kv.split("=", 1)
            if v not in VERDICTS:
                ap.error(f"verdict must be one of {VERDICTS}, got {v!r}")
            if k not in known:
                ap.error(f"unknown site key {k!r}")
            d["verdicts"][k] = {"verdict": v, "note": a.note}
        save_adj(d)
        print(f"recorded {len(a.verdict)} verdict(s) -> {ADJ}")

    if a.sheets:
        make_sheets(sites, a.n, [int(z) for z in a.zoom.split(",")], a.skip)

    if a.status or not (a.sheets or a.verdict):
        status(sites)
    return 0


if __name__ == "__main__":
    sys.exit(main())
