"""Which mining-context surface the plan chain reads: ONE switch, read everywhere.

    MINING_MODEL=heldout   (default) data/eval/xsa_mining_heldout/prediction.json
                                    the regional model (scripts/regional_mining/nested_cv.py) trained
                                    on CAF+SSD+SDN outside XSA, applied once to XSA. Its skill is a
                                    HELD-OUT measurement (nested_cv.json xsa_claim).
    MINING_MODEL=insample            data/eval/xsa_mining/prediction.json
                                    the 2026-08-16 equal-factor vote whose signals were selected on the
                                    same 43 XSA clusters it is scored on (lift 2.01, p 0.057 reach-null).

Every consumer (predict_mining_xsa.py, plan_conservancy_units.py cells(), plan_zone_stats.py,
plan_deploy.py, easypip/{pip_facts,build_map,build_gpkg}.py) calls prediction_dir() so the whole
chain - unit table -> solver -> deploy -> facts -> PIP text/map - is fitted from ONE surface, and
`MINING_MODEL=insample <re-run chain>` reverts it. Both prediction.json files carry
`model_variant` and `skill_basis`, so a stale mix is detectable: `check_consistent()` fails loudly
if an artefact on disk names the other variant.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = {"insample": ROOT / "data/eval/xsa_mining",
            "heldout": ROOT / "data/eval/xsa_mining_heldout"}
NESTED_CV = ROOT / "data/eval/regional_mining/nested_cv.json"
NESTED_CV_SCORES = ROOT / "data/eval/regional_mining/nested_cv.xsa_scores.npz"


def variant():
    v = os.environ.get("MINING_MODEL", "heldout").strip().lower()
    if v not in VARIANTS:
        raise SystemExit(f"MINING_MODEL={v!r}: expected one of {sorted(VARIANTS)}")
    return v


def prediction_dir():
    return VARIANTS[variant()]


def prediction_json():
    return prediction_dir() / "prediction.json"


def prediction_geojson():
    return prediction_dir() / "prediction.geojson"


def skill_top(pred, frac):
    """The composite_skill row for a cumulative top-`frac` cut, in either variant's prediction.json."""
    return next((s for s in pred.get("composite_skill") or [] if abs(s["top_frac"] - frac) < 1e-9), None)


def describe(pred):
    """One sentence a report can print beside any number derived from this surface."""
    return pred.get("skill_basis") or "unmeasured: prediction.json carries no skill_basis"


def check_consistent(*json_paths):
    """Raise if any artefact carries a `mining_model` field naming the other variant."""
    want = variant()
    bad = []
    for p in json_paths:
        p = Path(p)
        if not p.exists():
            continue
        try:
            d = json.load(open(p))
        except Exception:
            continue
        got = d.get("mining_model") if isinstance(d, dict) else None
        if got and got != want:
            bad.append(f"{p}: mining_model={got}")
    if bad:
        raise SystemExit(f"MINING_MODEL={want} but stale artefacts on disk:\n  " + "\n  ".join(bad)
                         + "\nre-run the chain (docs/agents/mining.md 'Switching the model') or set MINING_MODEL")
    return want
