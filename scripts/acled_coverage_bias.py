#!/usr/bin/env python3
"""Measure whether our mining reference's COVERAGE is biased by conflict.

THE QUESTION. scripts/geomaps/eval_affinity.py scores the lithology-based
mining-affinity model against occurrence lists. A score is only about geology if
the list could have found a mine anywhere the model grades. If the places a
surveyor could not reach are systematically the places with fighting, then part
of any score is "the model agrees with where it was safe to walk".

So, per first-level admin unit: does mine-reference coverage correlate with
conflict intensity? Reported per country AND per source, because the sources
have opposite blind spots -- a field survey is stopped by fighting, a satellite
footprint is not, and that contrast is the cleanest evidence available that the
bias is about REACH rather than about geology.

Statistics, so the answer is not eyeballed (AGENTS.md invariant 12):
  * Spearman rho over ADM1 (rank, because both distributions are heavy-tailed),
    with an exact permutation p-value -- no scipy on this box.
  * Mann-Whitney U comparing conflict in surveyed vs unsurveyed units.
  * Cliff's delta as the effect size, which is the readable number.
  * n is printed with every statistic, and n < 8 prints UNMEASURED rather than a
    number: with 10 ADM1 units a rho of 0.6 is noise, and a number without its n
    reads as a finding.

Conflict is used ONLY as a property of our own coverage; it is never evidence
that a place is mined. Counts come from ACLED (see data/eval/acled/), any
inference here is ours.

Input:  data/eval/mining_reference.json   (scripts/mining_reference.py)
Output: data/eval/mining_reference_bias.json + a table on stdout

Usage: python3 scripts/acled_coverage_bias.py [--source ipis_caf]
"""
import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "eval" / "mining_reference.json"
OUT = ROOT / "data" / "eval" / "mining_reference_bias.json"
MIN_N = 8          # below this we report UNMEASURED, not a number
PERMUTATIONS = 20000


def ranks(xs):
    """Average ranks, ties shared."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = sum((a - mx) ** 2 for a in xs) ** 0.5
    dy = sum((b - my) ** 2 for b in ys) ** 0.5
    return num / (dx * dy) if dx and dy else 0.0


def spearman(xs, ys, rng):
    """rho + two-sided permutation p."""
    rho = pearson(ranks(xs), ranks(ys))
    ry = ranks(ys)
    rx = ranks(xs)
    hits = 0
    shuf = list(ry)
    for _ in range(PERMUTATIONS):
        rng.shuffle(shuf)
        if abs(pearson(rx, shuf)) >= abs(rho) - 1e-12:
            hits += 1
    return rho, (hits + 1) / (PERMUTATIONS + 1)


def cliffs_delta(a, b):
    """P(a>b) - P(a<b). Positive = group a tends higher."""
    if not a or not b:
        return None
    gt = lt = 0
    for x in a:
        for y in b:
            if x > y:
                gt += 1
            elif x < y:
                lt += 1
    n = len(a) * len(b)
    return (gt - lt) / n


def mannwhitney_p(a, b, rng):
    """Two-sided permutation p on the difference in mean ranks."""
    if len(a) < 2 or len(b) < 2:
        return None
    pool = list(a) + list(b)
    r = ranks(pool)
    obs = sum(r[:len(a)]) / len(a) - sum(r[len(a):]) / len(b)
    hits = 0
    idx = list(range(len(pool)))
    for _ in range(PERMUTATIONS):
        rng.shuffle(idx)
        ra = sum(r[i] for i in idx[:len(a)]) / len(a)
        rb = sum(r[i] for i in idx[len(a):]) / len(b)
        if abs(ra - rb) >= abs(obs) - 1e-12:
            hits += 1
    return (hits + 1) / (PERMUTATIONS + 1)


def median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def detectable_delta(na, nb, rng, alpha=0.05):
    """Smallest Cliff's delta this split could have called significant.

    A null result is only informative if the test could have found something.
    "No significant association" with n=10 is not a finding, it is a shrug -- so
    every null prints the effect it WOULD have caught (AGENTS.md invariant 1: a
    unit that measured nothing must say so, not pass as an answer). Found by
    walking a clean shift: k of the surveyed units moved below all the others.
    """
    if na < 2 or nb < 2:
        return None
    for k in range(1, na + 1):
        a = list(range(k)) + list(range(100, 100 + na - k))
        b = list(range(100, 100 + nb))
        if mannwhitney_p(a, b, rng) < alpha:
            d = cliffs_delta(a, b)
            return round(abs(d), 3)
    return None


def analyse(units, label, rng, source=None):
    """units: list of dicts with .sites (for `source` if given) and .acled_events"""
    xs = [u["acled_events"] for u in units]
    ys = [(u["by_source"].get(source, 0) if source else u["sites"])
          for u in units]
    n = len(units)
    res = {"scope": label, "source": source or "all", "adm1_units": n,
           "adm1_with_sites": sum(1 for y in ys if y)}
    if n < MIN_N:
        res["verdict"] = "UNMEASURED"
        res["reason"] = f"{n} admin units is too few to rank ({MIN_N} needed)"
        return res
    if not any(ys):
        # A source with no sites in this scope is unmeasured here, not "no bias"
        res["verdict"] = "UNMEASURED"
        res["reason"] = "no sites from this source in this scope"
        return res
    rho, p = spearman(xs, ys, rng)
    surveyed = [u["acled_events"] for u, y in zip(units, ys) if y]
    empty = [u["acled_events"] for u, y in zip(units, ys) if not y]
    res.update({
        "spearman_rho_events_vs_sites": round(rho, 3),
        "spearman_p": round(p, 4),
        "median_events_surveyed": median(surveyed),
        "median_events_unsurveyed": median(empty),
        "cliffs_delta_surveyed_vs_not": (
            round(cliffs_delta(surveyed, empty), 3)
            if surveyed and empty else None),
        "mannwhitney_p": (round(mannwhitney_p(surveyed, empty, rng), 4)
                          if len(surveyed) >= 2 and len(empty) >= 2 else None),
        "n_surveyed": len(surveyed), "n_unsurveyed": len(empty),
    })
    d = res["cliffs_delta_surveyed_vs_not"]
    res["min_detectable_delta"] = detectable_delta(
        len(surveyed), len(empty), rng)
    if d is None or res["mannwhitney_p"] is None:
        res["verdict"] = "UNMEASURED"
        res["reason"] = ("every admin unit is on one side of the split; "
                         "nothing to compare")
    elif res["mannwhitney_p"] < 0.05 and d < 0:
        res["verdict"] = "COVERAGE AVOIDS CONFLICT"
    elif res["mannwhitney_p"] < 0.05 and d > 0:
        res["verdict"] = "COVERAGE FOLLOWS CONFLICT"
    else:
        mdd = res["min_detectable_delta"]
        res["verdict"] = ("no significant association" if mdd is not None
                          else "UNMEASURED")
        if mdd is not None:
            res["null_caveat"] = (
                f"this split could only have detected |delta| >= {mdd}; "
                f"observed {abs(d):.2f}. A null here rules out a strong "
                f"association, not a moderate one.")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="restrict to one reference source")
    ap.add_argument("--seed", type=int, default=20260813)
    a = ap.parse_args()
    if not SRC.exists():
        raise SystemExit("run scripts/mining_reference.py first")
    ref = json.loads(SRC.read_text())
    rng = random.Random(a.seed)

    by_iso = defaultdict(list)
    for u in ref["adm1"]:
        # an ADM1 with no ACLED number cannot enter a correlation
        if u["acled_events"] is None:
            continue
        by_iso[u["iso3"]].append(u)

    sources = ([a.source] if a.source else
               sorted({s["source"] for s in ref["sites"]}))
    results = []
    for iso in sorted(by_iso):
        results.append(analyse(by_iso[iso], iso, rng))
        for src in sources:
            present = sum(u["by_source"].get(src, 0) for u in by_iso[iso])
            if present:
                results.append(analyse(by_iso[iso], iso, rng, source=src))

    hdr = (f"{'scope':6} {'source':13} {'n':>3} {'held':>4} {'rho':>6} "
           f"{'p':>7} {'medS':>6} {'medU':>6} {'delta':>6} {'pMW':>7}  verdict")
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        if r["verdict"] == "UNMEASURED":
            print(f"{r['scope']:6} {r['source']:13} {r['adm1_units']:>3} "
                  f"{r['adm1_with_sites']:>4} {'':>6} {'':>7} {'':>6} {'':>6} "
                  f"{'':>6} {'':>7}  UNMEASURED ({r['reason']})")
            continue
        d = r["cliffs_delta_surveyed_vs_not"]
        print(f"{r['scope']:6} {r['source']:13} {r['adm1_units']:>3} "
              f"{r['adm1_with_sites']:>4} {r['spearman_rho_events_vs_sites']:>6.2f} "
              f"{r['spearman_p']:>7.4f} "
              f"{(r['median_events_surveyed'] or 0):>6.0f} "
              f"{(r['median_events_unsurveyed'] or 0):>6.0f} "
              f"{(d if d is not None else float('nan')):>6.2f} "
              f"{(r['mannwhitney_p'] if r['mannwhitney_p'] is not None else float('nan')):>7.4f}"
              f"  {r['verdict']}"
              + (f" (could only see |d|>={r['min_detectable_delta']})"
                 if r["verdict"] == "no significant association"
                 and r.get("min_detectable_delta") else ""))

    # Field-observed armed presence AT the mine: mining-specific, independent of
    # ACLED, and the one number here that is about mining rather than coverage.
    armed = Counter()
    tot = Counter()
    for s in ref["sites"]:
        if s.get("observed") == "field_visit":
            tot[s["source"]] += 1
            if s.get("site_armed_actor"):
                armed[s["source"]] += 1
    site_level = {src: {"field_visited_sites": tot[src],
                        "with_armed_actor_on_site": armed[src],
                        "share": round(armed[src] / tot[src], 3) if tot[src] else None}
                  for src in tot}
    print("\nArmed actor recorded AT the mine site (IPIS field visits, not ACLED):")
    for src, v in sorted(site_level.items()):
        if v["share"] is None:
            print(f"  {src}: UNMEASURED (no field-visit rows)")
        elif v["with_armed_actor_on_site"] == 0:
            print(f"  {src}: 0 of {v['field_visited_sites']} sites -- the survey "
                  "does not record this field")
        else:
            print(f"  {src}: {v['with_armed_actor_on_site']} of "
                  f"{v['field_visited_sites']} sites ({v['share']:.0%})")

    out = {
        "generated_by": "scripts/acled_coverage_bias.py",
        "question": "does mine-reference coverage per ADM1 correlate with "
                    "conflict intensity? (a property of our truth set, not of "
                    "the ground)",
        "conflict_context": ref["conflict_context"],
        "method": {
            "spearman": f"rank correlation, {PERMUTATIONS} permutations, two-sided",
            "mannwhitney": "permutation test on mean-rank difference",
            "effect_size": "Cliff's delta, surveyed vs unsurveyed ADM1",
            "min_units": MIN_N,
        },
        "results": results,
        "site_level_armed_presence": site_level,
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"\n-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
