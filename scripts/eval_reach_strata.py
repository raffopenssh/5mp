#!/usr/bin/env python3
"""Does the affinity score change when the truth set's REACH changes?

THE MEASUREMENT THAT ACTUALLY MATTERS HERE. scripts/acled_coverage_bias.py asks
whether mine-reference coverage tracks conflict at ADM1 level and finds nothing
significant in any of the four countries -- but ADM1 is a 50,000 km2 bucket, and
those tests could only have caught |delta| >= 0.58-0.95. So the ADM1 null is
"no strong effect at province scale", not "no effect".

This script asks the same question where the resolution is site-level and the
observation is mining-specific: IPIS's CAR surveyors recorded, at each mine they
stood in, whether an armed actor was present (28% of 914 sites). That is a
property of the SITE, not of a province, and it splits the truth set into two
strata that differ in exactly the way reach-bias would predict.

If the affinity model scores the same on both strata, the score is about rocks.
If it scores differently, part of the score is about who could reach the ground
-- and every published lift needs to say which stratum it came from.

The score is the existing one: scripts/geomaps/eval_affinity.py's own
`score_sheet`, imported rather than reimplemented, so a stratum cannot be scored
by a slightly different rule than the headline number.

Usage: python3 scripts/eval_reach_strata.py
Output: data/eval/reach_strata.json
"""
import csv
import io
import json
import random
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "geomaps"))

from shapely.geometry import Point  # noqa: E402

import eval_affinity as EA  # noqa: E402

IPIS = ROOT / "data" / "ipis" / "caf_mines_ipis.csv"
OUT = ROOT / "data" / "eval" / "reach_strata.json"
MIN_N = 8


def strata():
    """IPIS CAR sites split by armed-actor presence recorded ON SITE."""
    armed, calm = {"gold": [], "diamond": []}, {"gold": [], "diamond": []}
    n_armed = n_calm = 0
    for r in csv.DictReader(IPIS.open()):
        if not r.get("longitude"):
            continue
        p = Point(float(r["longitude"]), float(r["latitude"]))
        actor = (r.get("actor_type") or "").strip()
        bucket = armed if actor not in ("", "0") else calm
        if bucket is armed:
            n_armed += 1
        else:
            n_calm += 1
        if (r.get("minerals_or") or "0").strip() in ("1", "1.0"):
            bucket["gold"].append(p)
        if (r.get("minerals_diamant") or "0").strip() in ("1", "1.0"):
            bucket["diamond"].append(p)
    return (armed, n_armed), (calm, n_calm)


def score(sites, tid, label, note):
    """Score one stratum with eval_affinity's own scorer.

    hull_baseline=True: a stratum's sites are a subset of a survey's reach, so
    random ground must mean random ground in the stratum's own hull. Comparing a
    subset's capture against the whole sheet's area would make the smaller
    stratum look better for being smaller.
    """
    t = EA.Truth("car", tid, label, note, sites, "visits", hull_baseline=True)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rec = EA.score_sheet("car", t, min_sites=MIN_N)
    return rec, buf.getvalue()


def lift(rec, com, mw=1):
    u = (rec.get("units") or {}).get(com) or {}
    if not isinstance(u, dict) or "verdict" in u:
        return None
    got = u.get(mw) or u.get(str(mw))
    return got.get("lift") if got else None


def jlift(rec, com, mw=1):
    j = (rec.get("junctions") or {}).get(com) or {}
    if not isinstance(j, dict) or "verdict" in j:
        return None
    got = j.get(mw) or j.get(str(mw))
    return got.get("lift") if got else None


def capture_permutation(rng, permutations=20000):
    """Is the armed/unarmed difference in CAPTURE more than label noise?

    A lift ratio of 1.55 with no test beside it is exactly what invariant 12
    forbids: tuning or concluding by eye. The full lift cannot be permuted --
    score_sheet takes 16 s, so 20k re-scores is 90 hours -- but the part that
    carries the difference can be. `capture` is "what fraction of this stratum's
    sites sit in a unit the model grades for gold", and that is a per-site
    property: compute each site's graded/not once (the expensive geometry), then
    shuffle the armed/unarmed labels over the SAME sites.

    This tests the numerator only. The area denominator differs between strata
    because each is scored in its own hull, so the reported p is about capture,
    and the lift ratio is the effect size it qualifies. Stated, not hidden.
    """
    rows = [r for r in csv.DictReader(IPIS.open()) if r.get("longitude")]
    gold = [(Point(float(r["longitude"]), float(r["latitude"])),
             (r.get("actor_type") or "").strip() not in ("", "0"),
             (r.get("prefecture") or "").strip())
            for r in rows
            if (r.get("minerals_or") or "0").strip() in ("1", "1.0")]

    # per-site graded/not, once
    cat = EA.catalogue("car")
    units = json.load(open(ROOT / "data/geomaps/car_units.geojson"))["features"]
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    geoms = [shape(f["geometry"]) for f in units]
    props = [f["properties"] for f in units]
    tree = STRtree(geoms)

    def graded(pt):
        for i in tree.query(pt):
            if geoms[i].contains(pt):
                for a in props[i].get("affinity") or []:
                    if a["commodity"] == "gold" and a["weight"] >= 1:
                        return True
                return False
        return None      # off the mapped sheet: not a zero, an absence

    obs = [(graded(p), armed, pref) for p, armed, pref in gold]
    obs = [(g, a, pr) for g, a, pr in obs if g is not None]
    if not obs:
        return None
    flags = [g for g, _, _ in obs]
    labels = [a for _, a, _ in obs]
    prefs = [pr for _, _, pr in obs]
    na = sum(labels)
    if na < MIN_N or len(labels) - na < MIN_N:
        return None

    def cap(sel):
        n = sum(1 for i in sel if flags[i])
        return n / len(sel) if sel else 0.0

    idx_a = [i for i, a in enumerate(labels) if a]
    idx_u = [i for i, a in enumerate(labels) if not a]
    observed = cap(idx_a) - cap(idx_u)
    # the same difference, computed only over prefectures that hold both kinds
    # of site (elsewhere the within-prefecture contrast does not exist)
    inform = [i for i, pr in enumerate(prefs)
              if 0 < sum(1 for j, q in enumerate(prefs)
                         if q == pr and labels[j]) < sum(1 for q in prefs if q == pr)]
    ia = [i for i in inform if labels[i]]
    iu = [i for i in inform if not labels[i]]
    observed_within = (cap(ia) - cap(iu)) if ia and iu else 0.0
    order = list(range(len(labels)))
    hits = 0
    for _ in range(permutations):
        rng.shuffle(order)
        d = cap(order[:na]) - cap(order[na:])
        if abs(d) >= abs(observed) - 1e-12:
            hits += 1
    # THE CONFOUND THIS HAS TO SURVIVE: armed sites are not spread evenly across
    # the prefectures, and neither is the geology. A shuffle over the whole
    # country would credit the model with "armed sites happen to sit in the
    # prefecture with the gold-bearing rocks". So repeat the permutation
    # STRATIFIED: labels are shuffled only WITHIN each prefecture, which holds
    # geography fixed and asks whether the armed/calm split still matters.
    by_pref = {}
    for i, pr in enumerate(prefs):
        by_pref.setdefault(pr, []).append(i)
    strat_hits = 0
    for _ in range(permutations):
        d_a = d_u = n_a = n_u = 0
        for idxs in by_pref.values():
            k = sum(1 for i in idxs if labels[i])
            if not k or k == len(idxs):
                continue
            pool = list(idxs)
            rng.shuffle(pool)
            for i in pool[:k]:
                d_a += flags[i]
                n_a += 1
            for i in pool[k:]:
                d_u += flags[i]
                n_u += 1
        if not n_a or not n_u:
            continue
        if abs(d_a / n_a - d_u / n_u) >= abs(observed_within) - 1e-12:
            strat_hits += 1

    return {
        "commodity": "gold", "metric": "capture (fraction of sites in a unit "
                                       "graded gold, weight>=1)",
        "sites_on_sheet": len(obs),
        "capture_armed": round(cap(idx_a), 4),
        "capture_unarmed": round(cap(idx_u), 4),
        "difference": round(observed, 4),
        "permutations": permutations,
        "p": round((hits + 1) / (permutations + 1), 5),
        "within_prefecture": {
            "difference": round(observed_within, 4),
            "p": round((strat_hits + 1) / (permutations + 1), 5),
            "prefectures_informative": sum(
                1 for idxs in by_pref.values()
                if 0 < sum(1 for i in idxs if labels[i]) < len(idxs)),
            "note": "labels shuffled only within prefecture, so geology and "
                    "prefecture-level access are held fixed",
        },
        "note": "tests the capture numerator only; each stratum's area "
                "denominator is its own hull, so this p qualifies the "
                "difference in capture, not the lift ratio itself",
    }


def main():
    (armed, n_armed), (calm, n_calm) = strata()
    print(f"IPIS CAR field visits: {n_armed} sites with an armed actor recorded "
          f"on site, {n_calm} without")
    for name, s in (("armed", armed), ("no armed actor", calm)):
        print(f"  {name:15} gold {len(s['gold']):>3}  diamond {len(s['diamond']):>3}")

    out = {
        "generated_by": "scripts/eval_reach_strata.py",
        "question": "does the CAR affinity lift differ between mine sites where "
                    "IPIS recorded an armed actor and sites where it did not? A "
                    "difference means part of the lift is about reach, not rock.",
        "stratifier": "IPIS actor_type recorded at the site (not ACLED)",
        "baseline": "each stratum scored against random ground in its OWN hull",
        "min_sites": MIN_N,
        "strata": {},
    }
    logs = {}
    for key, sites, n in (("armed", armed, n_armed), ("unarmed", calm, n_calm)):
        rec, log = score(
            sites, f"ipis_{key}",
            f"the Central African Republic, IPIS sites "
            f"{'with' if key == 'armed' else 'without'} an armed actor on site",
            "A stratum of one survey's reachable sites, split by what the "
            "surveyor saw at the pit. It measures the survey's reach, not the "
            "prevalence of anything.")
        logs[key] = log
        out["strata"][key] = {
            "sites_total": n,
            "sites_by_commodity": {c: len(v) for c, v in sites.items()},
            "units_lift": {c: lift(rec, c) for c in sites},
            "junctions_lift": {c: jlift(rec, c) for c in sites},
        }

    print(f"\n{'commodity':>10} {'stratum':>10} {'n':>4} {'unit lift':>10} "
          f"{'junction lift':>14}")
    comparison = {}
    for com in ("gold", "diamond"):
        row = {}
        for key in ("armed", "unarmed"):
            s = out["strata"][key]
            n = s["sites_by_commodity"].get(com, 0)
            ul, jl = s["units_lift"].get(com), s["junctions_lift"].get(com)
            row[key] = {"n": n, "units": ul, "junctions": jl}
            f = lambda v: f"{v:.2f}" if isinstance(v, float) else "  --"
            flag = "" if n >= MIN_N else f"  (n<{MIN_N}: UNMEASURED)"
            print(f"{com:>10} {key:>10} {n:>4} {f(ul):>10} {f(jl):>14}{flag}")
        # The comparison only means something if BOTH strata resolved a lift.
        both = all(row[k]["n"] >= MIN_N for k in row)
        for kind in ("units", "junctions"):
            a, u = row["armed"][kind], row["unarmed"][kind]
            if both and isinstance(a, float) and isinstance(u, float) and u:
                comparison[f"{com}_{kind}"] = {
                    "armed": round(a, 3), "unarmed": round(u, 3),
                    "ratio_armed_over_unarmed": round(a / u, 3),
                }
            else:
                comparison[f"{com}_{kind}"] = {
                    "verdict": "UNMEASURED",
                    "reason": ("a stratum has fewer than "
                               f"{MIN_N} sites of this commodity"
                               if not both else "a lift had no denominator"),
                }
    out["comparison"] = comparison
    perm = capture_permutation(random.Random(20260813))
    out["capture_permutation"] = perm or {
        "verdict": "UNMEASURED",
        "reason": "a stratum has too few gold sites on the mapped sheet"}

    print("\ncomparison (armed / unarmed):")
    for k, v in comparison.items():
        if "verdict" in v:
            print(f"  {k:22} UNMEASURED -- {v['reason']}")
        else:
            r = v["ratio_armed_over_unarmed"]
            verdict = ("same within 20%" if 0.8 <= r <= 1.25
                       else "DIFFERS -- the lift depends on the stratum")
            print(f"  {k:22} {v['armed']:>6.2f} / {v['unarmed']:>6.2f} "
                  f"= {r:>5.2f}  {verdict}")

    if perm:
        sig = "SIGNIFICANT" if perm["p"] < 0.05 else "not significant"
        print(f"\ncapture permutation ({perm['permutations']} shuffles, "
              f"{perm['sites_on_sheet']} gold sites on the sheet):")
        print(f"  armed {perm['capture_armed']:.1%} vs unarmed "
              f"{perm['capture_unarmed']:.1%}  diff {perm['difference']:+.1%}  "
              f"p={perm['p']:.4f}  {sig}")
        w = perm["within_prefecture"]
        wsig = "SIGNIFICANT" if w["p"] < 0.05 else "not significant"
        print(f"  within prefecture ({w['prefectures_informative']} informative)"
              f": diff {w['difference']:+.1%}  p={w['p']:.4f}  {wsig}")
    else:
        print("\ncapture permutation: UNMEASURED (a stratum has too few gold "
              "sites on the mapped sheet)")

    OUT.write_text(json.dumps(out, indent=2))
    print(f"\n-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
