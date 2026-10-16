#!/usr/bin/env python3
"""Budget frontier of the deployment: how many teams and focal points a given cap buys, under the constraint plan_deploy.py last ran with.

    python3 -W ignore scripts/plan_deploy_frontier.py [--caps 0.8 1.0 1.2] [--months 13 8] [--tag ""]

Reads data/plan_zones/solver/deploy<tag>.json — its `pool` (every placed site, in the staging order plan_deploy.py uses:
ECHO by selection frequency bucketed to 0.1 then urgency, TANGO by urgency) and `fp_towns` — and GROWS a South Sudan plan
one unit at a time: teams first, then focal points (an FP follows its teams: focal_points_of, the same greedy marginal-coverage
rule), costing every step with the budget builder's summary() (scripts/easybudget/build_budget.py: HQ, freight, bank, support
and contingency included) on the plan.yaml calendar with months_paid set to the row's contract length. The step that would take
the LOADED South Sudan total over the cap is not taken. Team/FP count is therefore an OUTPUT, never an input. The CAR strand
(K-teams) is costed by the same call and printed beside the row, outside the cap (Chinko's own line).

months_paid M: TANGO M (Nov start, the whole field season), ECHO first wave M-1 (Dec start), ECHO second wave M-2 (Jan start),
FP [5, M]; M=13 reproduces plan.yaml as it stands (13/12/11), M=8 is the Nov–Jun seasonal contract (8/7/6).

Coverage, per row, of the ground the constraint keeps (every 2 km cell NOT on the penalised ground, inside South Sudan):
  gold   share of the mining model's top-5 % target cells inside the chosen teams' footprints (served zones + 25 km round each site)
  herd   share of the herd utilisation (movement_bundle_ud.npy, all bundles) inside those footprints
  shield share of the shielded edge (community-zone perimeter × shield, RANK.txt) held by a staffed ECHO zone
Writes FRONTIER_FOCUS<tag>.txt + .json next to deploy.json.

--scenario NAME [NAME ...] | all   applies named plan.yaml override sets from docs/plan/scenarios.yaml (cuts, adds, combined)
  and prints baseline + each scenario for the caps/months given (default then: --caps 1.0 --months 13 8), writing
  FRONTIER_LEAN<tag>.txt/.json instead. Stage order is unchanged; teams and FPs remain the output. plan.yaml itself is not edited."""
import argparse, copy, json, sys
from pathlib import Path
import numpy as np
import yaml
from shapely.geometry import shape, Point
from shapely.ops import unary_union, transform

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / "scripts")); sys.path.insert(0, str(ROOT / "scripts/easybudget"))
import plan_conservancy_units as P
import plan_solver as S
import plan_deploy as D
import build_budget as B
import __main__; __main__.Grid = P.Grid
OUT = S.OUT

def load_scenarios():
    Y = yaml.safe_load(open(ROOT / "docs/plan/scenarios.yaml"))
    flat = {}
    for grp in ("cuts", "adds"):
        for k, v in (Y.get(grp) or {}).items(): flat[k] = dict(v, group=grp, overrides=dict(v.get("overrides") or {}))
    def expand(name, seen=()):
        if name in flat: return flat[name]
        c = (Y.get("combined") or {})[name]; ov = {}; parts = []
        for m in c["of"]:
            e = expand(m, seen + (name,)); parts.append(m)
            for kk, vv in e["overrides"].items():
                if kk == "assumptions": ov["assumptions"] = {**ov.get("assumptions", {}), **vv}
                else: ov[kk] = vv
        return dict(group="combined", of=parts, overrides=ov, review=c.get("review", ""), removes=f"= {' + '.join(parts)}")
    names = list(flat) + list(Y.get("combined") or {})
    return {n: expand(n) for n in names}

def apply_overrides(P_, ov):
    P_ = copy.deepcopy(P_)
    for k, v in ov.items():
        if k == "assumptions": P_["assumptions"] = {**(P_.get("assumptions") or {}), **v}
        else: P_[k] = v
    return P_

def months_for(P_, M):
    P_ = copy.deepcopy(P_)
    P_["months_paid"] = dict(echo=[0, M - 1], tango=[0, M], fp=[P_["months_paid"]["fp"][0], M])
    P_["months_first_phase"] = dict(echo=[0, M - 2], tango=[0, M], fp=[P_["months_first_phase"]["fp"][0], M - 2])
    return P_

def by_year(teams, strands):
    rows = []
    for y in (1, 2):
        cnt = lambda k, yy, st="SSD": sum(1 for t in teams if t["kind"] == k and t["year"] == yy and (t.get("strand", "SSD") == st or k != "ECHO") and not t.get("pm"))
        cum = lambda k, st="SSD": sum(1 for t in teams if t["kind"] == k and t["year"] <= y and (t.get("strand", "SSD") == st or k != "ECHO") and not t.get("pm"))
        row = dict(echo=cum("ECHO"), tango=cum("TANGO"), fp=cum("FP"), new_echo=cnt("ECHO", y), new_tango=cnt("TANGO", y), new_fp=cnt("FP", y), staff=cum("ECHO") * 5 + cum("TANGO") * 5 + cum("FP"))
        for k in strands: row[f"echo_{k.lower()}"] = cum("ECHO", k); row[f"new_echo_{k.lower()}"] = cnt("ECHO", y, k); row[f"staff_{k.lower()}"] = cum("ECHO", k) * 5
        rows.append(row)
    return rows

def cost(F0, P_, teams):
    """loaded totals for this team list: (South Sudan request, CAR strand, all) — the budget builder's own summary()"""
    F = copy.deepcopy(F0); strands = list((F["deploy"].get("strands") or {}).keys())
    F["deploy"]["by_year"] = by_year(teams, strands)
    F["deploy"]["teams"] = {t["id"]: dict(t, place=t["place"], year=t["year"], strand=t.get("strand", "SSD")) for t in teams}
    F["deploy"]["fp_places"] = [t["place"] + (" (the PM)" if t.get("pm") else "") for t in teams if t["kind"] == "FP"]
    s = B.summary(F, P_)
    return s["ssd_total"], s.get("car_total", 0), s["total"], s

def assign(units, n_echo_y1_rule=lambda n: max(2, n // 2), n_fp_y1_rule=lambda n: max(1, n // 2)):
    """ids and waves as plan_deploy.py stages them: TANGO first wave; ECHO first max(2, n//2) first wave; FP first max(1, n//2) first wave —
    unless a PM-as-FP unit (pm=True, plan.yaml pm_fp_town) is present: it is F1 in wave 1 and every paid FP joins in wave 2"""
    out = []; ne = sum(1 for u in units if u["kind"] == "ECHO"); nf = sum(1 for u in units if u["kind"] == "FP" and not u.get("pm")); ie = it = i_f = 0
    has_pm = any(u.get("pm") for u in units)
    for u in sorted(units, key=lambda u: (u["kind"] == "FP", not u.get("pm"))):
        u = dict(u)
        if u["kind"] == "ECHO": ie += 1; u["id"] = f"E{ie}"; u["year"] = 1 if ie <= n_echo_y1_rule(ne) else 2
        elif u["kind"] == "TANGO": it += 1; u["id"] = f"T{it}"; u["year"] = 1
        else: i_f += 1; u["id"] = f"F{i_f}"; u["year"] = 1 if (u.get("pm") or (not has_pm and i_f <= n_fp_y1_rule(nf))) else 2
        out.append(u)
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default=""); ap.add_argument("--caps", type=float, nargs="+", default=[0.8, 1.0, 1.2], help="M USD, loaded South Sudan total")
    ap.add_argument("--months", type=int, nargs="+", default=[13, 8]); ap.add_argument("--max-fp", type=int, default=3); ap.add_argument("--max-echo", type=int, default=6); ap.add_argument("--max-tango", type=int, default=6)
    ap.add_argument("--pool", default=None, help="deploy json whose `pool` is grown; default deploy_focus_pool<tag>.json if present (the full placed pool), else deploy<tag>.json "
                    "(a CAPPED run's pool holds only the sites it staged, in a different order)")
    ap.add_argument("--scenario", nargs="+", default=None, help="names from docs/plan/scenarios.yaml, or 'all'; baseline is always printed first")
    a = ap.parse_args(); tag = a.tag
    SC = {}
    if a.scenario:
        allsc = load_scenarios(); names = list(allsc) if a.scenario == ["all"] else a.scenario
        SC = {n: allsc[n] for n in names}
        if "--caps" not in sys.argv: a.caps = [1.0]
    pool_path = Path(a.pool) if a.pool else (OUT / f"deploy_focus_pool{tag}.json" if (OUT / f"deploy_focus_pool{tag}.json").exists() else OUT / f"deploy{tag}.json")
    dep = json.load(open(pool_path)); print(f"pool: {pool_path.name}", file=sys.stderr); F0 = json.load(open(OUT / "facts.json")); Pyaml = yaml.safe_load(open(ROOT / "docs/plan/plan.yaml"))
    pool = [s for s in dep["pool"] if s.get("strand", "SSD") == "SSD" and s["kind"] in ("ECHO", "TANGO")]
    car = [dict(t) for t in dep["teams"] if t.get("strand") == "CAR"]
    # ---------------------------------------------------------------- ground kept by the constraint, on the solver grid
    st = S.load_state(); G = st["G"]; zs = json.load(open(OUT / f"zones{tag}.geojson"))["features"]
    con = __import__("sqlite3").connect(str(P.DB)); C = P.cells(con, G); gold = np.nan_to_num(C["mine_top05"]) > 0
    bud = np.load(OUT / "movement_bundle_ud.npy").astype(np.float32).sum(0) if (OUT / "movement_bundle_ud.npy").exists() else np.zeros(gold.shape, np.float32)
    ssd = G.rasterize([(transform(P.FWD, shape(f["geometry"])), 1) for f in zs if f["properties"].get("country") == "SSD"]).astype(bool)
    pen = [shape(x["geometry"]) for x in dep.get("spatial_constraint") or []]
    kept = ssd & ~(G.rasterize([(transform(P.FWD, g), 1) for g in pen]).astype(bool) if pen else np.zeros(gold.shape, bool))
    rank = {r["uid"]: r for r in (json.load(open(OUT / f"rank_conservancies{tag}.json")).get("rows", []) if (OUT / f"rank_conservancies{tag}.json").exists() else [])}
    zg = {f["properties"]["uid"]: shape(f["geometry"]).buffer(0) for f in zs}
    comm_w = {f["properties"]["uid"]: f["properties"]["perimeter_km"] * rank.get(f["properties"]["uid"], {}).get("shield", 0.0) for f in zs
              if f["properties"]["solver_class"] == "community" and f["properties"].get("country") == "SSD" and not any(g.contains(zg[f["properties"]["uid"]].centroid) for g in pen)}
    tot_gold, tot_ud, tot_shield = int((gold & kept).sum()), float(bud[kept].sum()), sum(comm_w.values())
    def coverage(units):
        fps = [unary_union([zg[u] for u in s["zone_uids"]] + [Point(s["lon"], s["lat"]).buffer(D.SITE_REACH_KM / 111)]) for s in units if s["kind"] != "FP"]
        m = G.rasterize([(transform(P.FWD, g), 1) for g in fps]).astype(bool) & kept if fps else np.zeros(gold.shape, bool)
        held = {u for s in units if s["kind"] == "ECHO" for u in s["zone_uids"]}
        return dict(gold=round(int((gold & m).sum()) / max(tot_gold, 1), 3), herd=round(float(bud[m].sum()) / max(tot_ud, 1e-9), 3), shield=round(sum(w for u, w in comm_w.items() if u in held) / max(tot_shield, 1e-9), 3))
    # ---------------------------------------------------------------- grow
    rows = []
    for sc_name, sc in [("baseline", dict(overrides={}))] + list(SC.items()):
      Psc = apply_overrides(Pyaml, sc["overrides"])
      for M in a.months:
        P_ = months_for(Psc, M)
        for cap in a.caps:
            chosen = []; cnt = {"ECHO": 0, "TANGO": 0}; steps = []; refused = None
            def total_of(units):
                return cost(F0, P_, assign(units) + car)
            base = total_of([])
            for s in pool:                                    # teams in stage_key order, skipping a kind at its cap
                lim = a.max_echo if s["kind"] == "ECHO" else a.max_tango
                if cnt[s["kind"]] >= lim: continue
                t = total_of(chosen + [s])
                steps.append((s["kind"], s["place"], s["zone_uids"], t[0]))
                if t[0] > cap * 1e6: refused = steps[-1]; break      # the first team that breaks the cap ends the team phase (order is the ranking)
                chosen.append(s); cnt[s["kind"]] += 1
            seed = [D.pm_fp_unit(P_["pm_fp_town"], dep["fp_towns"])] if P_.get("pm_fp_town") else []     # the PM as the wave-1 FP: free, on top of max_fp
            fps = D.focal_points_of(chosen, len(seed), dep["fp_towns"], seed=seed) if seed else []
            for k in range(a.max_fp):                         # then paid focal points, one at a time, each by marginal coverage of the chosen teams
                nf = D.focal_points_of(chosen, len(seed) + k + 1, dep["fp_towns"], seed=fps)
                if len(nf) <= len(fps): break
                t = total_of(chosen + nf)
                steps.append(("FP", nf[-1]["place"], nf[-1]["zone_uids"], t[0]))
                if t[0] > cap * 1e6: refused = refused or steps[-1]; break
                fps = nf
            units = assign(chosen + fps); ssd_total, car_total, all_total, summ = cost(F0, P_, units + car)
            cov = coverage(units)
            n_paid_fp = sum(1 for f in fps if not f.get("pm"))
            rows.append(dict(scenario=sc_name, cap_musd=cap, months_paid=M, teams=len(chosen), echo=cnt["ECHO"], tango=cnt["TANGO"], fp=n_paid_fp, staff=5 * len(chosen) + n_paid_fp, pm_fp=P_.get("pm_fp_town"),
                             ssd_loaded=ssd_total, car_loaded=car_total, backbone_only=base[0], per_phase=summ["ssd_per_year"], load_factor=summ["load_factor"],
                             units=[dict(id=u["id"], kind=u["kind"], place=u["place"], zone_uids=u["zone_uids"], year=u["year"], urgency=u.get("urgency"), robust=u.get("robust"), pm=bool(u.get("pm"))) for u in units],
                             next_refused=refused, steps=steps, coverage=cov,
                             backbone_split=dict(fp_and_backbone=base[3]["backbone_loaded"], hq_and_fixed=[base[0] - sum(base[3]["backbone_loaded"])])))
    # ---------------------------------------------------------------- write
    if SC: return write_lean(a, tag, rows, SC, tot_gold, tot_ud, tot_shield, dict(dep, pool_file=pool_path.name, n_pool=len(pool)))
    L = [f"FRONTIER_FOCUS{tag} — what a loaded South Sudan budget cap buys under the deployment constraint plan_deploy.py last ran with; teams and focal points are the OUTPUT.",
         f"  constraint: {dep.get('constraint') or '(none)'}",
         f"  pool: {len(pool)} placed South Sudan sites in staging order (ECHO by selection frequency bucketed 0.1, then urgency; TANGO by urgency); grown one unit at a time, teams then FPs; cost = build_budget.summary (HQ, freight, bank, support, contingency in); CAR strand (K-teams) costed beside, outside the cap.",
         f"  months_paid M: TANGO M / ECHO wave 1 M-1 / ECHO wave 2 M-2 / FP [5, M]; M=13 is plan.yaml as it stands.",
         f"  coverage = share of the ground the constraint keeps (South Sudan cells off the penalised ground): {tot_gold} gold-target cells, herd UD {tot_ud:.1f}, shielded community edge {tot_shield:,.0f} km.", "",
         f"  {'cap M$':>6} {'months':>6} | {'teams':>5} {'ECHO':>4} {'TANGO':>5} {'FP':>3} {'staff':>5} | {'SSD loaded':>11} {'CAR strand':>10} {'backbone-only':>13} | {'gold':>5} {'herd':>5} {'shield':>6} | units (wave) ; first unit refused"]
    for r in rows:
        us = ", ".join(f"{u['id']} {u['place'].split('*')[0].strip()}{'=PM' if u.get('pm') else ''} {u['zone_uids']} (w{u['year']})" for u in r["units"])
        nr = r["next_refused"]; nr_s = f"{nr[0]} {nr[1]} {nr[2]} would be USD {nr[3]:,.0f}" if nr else "pool exhausted"
        L.append(f"  {r['cap_musd']:>6.1f} {r['months_paid']:>6} | {r['teams']:>5} {r['echo']:>4} {r['tango']:>5} {r['fp']:>3} {r['staff']:>5} | {r['ssd_loaded']:>11,.0f} {r['car_loaded']:>10,.0f} {r['backbone_only']:>13,.0f} | {r['coverage']['gold']:>5.0%} {r['coverage']['herd']:>5.0%} {r['coverage']['shield']:>6.0%} | {us} ; refused: {nr_s}")
    b = rows[0]["backbone_split"] if rows else {}
    L += ["", f"  backbone-only = the South Sudan request with NO team and NO focal point: PM, Juba desk, legal/paper track, survey and aviation, HQ oversight, contingency (loaded; M=13 row): USD {rows[0]['backbone_only']:,.0f} — this is why a 0.8 M cap buys no team." if rows else ""]
    L += ["", "  a row's team list is the input to plan_deploy.py for that budget: --echo-y2 ECHO --tango-y2 TANGO --fp-y2 FP --echo-y1 (wave-1 ECHO) --staff-y2 staff."]
    (OUT / f"FRONTIER_FOCUS{tag}.txt").write_text("\n".join(L) + "\n"); print("\n".join(L))
    json.dump(dict(constraint=dep.get("constraint"), deprioritise=dep.get("deprioritise"), spatial=[x["name"] for x in dep.get("spatial_constraint") or []], caps=a.caps, months=a.months,
                   kept_ground=dict(gold_cells=tot_gold, herd_ud=round(tot_ud, 2), shield_km=round(tot_shield)), rows=rows), open(OUT / f"FRONTIER_FOCUS{tag}.json", "w"), indent=1, ensure_ascii=False)

def write_lean(a, tag, rows, SC, tot_gold, tot_ud, tot_shield, dep):
    """FRONTIER_LEAN: scenario x (cap, months) — backbone-only, its saving against baseline, what the cap then buys."""
    base = {(r["cap_musd"], r["months_paid"]): r for r in rows if r["scenario"] == "baseline"}
    L = [f"FRONTIER_LEAN{tag} — what a loaded South Sudan cap buys once named backbone options (docs/plan/scenarios.yaml) are applied; plan.yaml itself unchanged.",
         f"  constraint: {dep.get('constraint') or '(none)'}",
         f"  pool: {dep['pool_file']} ({dep['n_pool']} placed South Sudan sites, staging order as published); rates as build_budget.RATES today (Starlink USD 25/month).",
         "  backbone-only = the South Sudan request with NO team and NO focal point (loaded). saving = baseline backbone - scenario backbone at the same contract length; a negative saving is an ADD.",
         "  teams/FPs are grown in the published stage order until the loaded SSD total would exceed the cap; CAR strand costed beside, outside the cap.",
         "  FP = PAID focal points; a unit marked =PM is the Project Manager doubling as the wave-1 focal point (plan.yaml pm_fp_town): no FP pay, not counted in FP/staff.",
         f"  coverage = share of the ground the constraint keeps: {tot_gold} gold-target cells, herd UD {tot_ud:.1f}, shielded community edge {tot_shield:,.0f} km.", "",
         f"  {'scenario':<22} {'cap':>4} {'mo':>3} | {'backbone':>9} {'saving':>8} | {'teams':>5} {'E':>2} {'T':>2} {'FP':>3} {'staff':>5} | {'SSD loaded':>10} {'headroom':>8} | {'gold':>5} {'herd':>5} {'shield':>6} | units (wave) ; first refused"]
    for r in rows:
        b = base[(r["cap_musd"], r["months_paid"])]; sav = b["backbone_only"] - r["backbone_only"]
        us = ", ".join(f"{u['id']} {u['place'].split('*')[0].strip()}{'=PM' if u.get('pm') else ''} (w{u['year']})" for u in r["units"])
        nr = r["next_refused"]; nr_s = f"{nr[0]} {nr[1].split('*')[0].strip()} -> {nr[3]:,.0f}" if nr else "pool exhausted"
        L.append(f"  {r['scenario']:<22} {r['cap_musd']:>4.1f} {r['months_paid']:>3} | {r['backbone_only']:>9,.0f} {sav:>8,.0f} | {r['teams']:>5} {r['echo']:>2} {r['tango']:>2} {r['fp']:>3} {r['staff']:>5} | {r['ssd_loaded']:>10,.0f} {r['cap_musd']*1e6 - r['ssd_loaded']:>8,.0f} | {r['coverage']['gold']:>5.0%} {r['coverage']['herd']:>5.0%} {r['coverage']['shield']:>6.0%} | {us} ; refused: {nr_s}")
    L += ["", "  what each option changes (the line's own basis text):"]
    for n, sc in SC.items():
        L.append(f"  - {n} [{sc.get('group')}] {sc.get('review', '')}")
        L.append(f"      {'removes' if sc.get('group') != 'adds' else 'adds'}: {sc.get('removes') or sc.get('adds') or ''}")
        L.append(f"      overrides: {json.dumps(sc['overrides'], ensure_ascii=False)}")
    (OUT / f"FRONTIER_LEAN{tag}.txt").write_text("\n".join(L) + "\n"); print("\n".join(L))
    json.dump(dict(constraint=dep.get("constraint"), caps=a.caps, months=a.months, scenarios={n: dict(group=sc.get("group"), review=sc.get("review"), removes=sc.get("removes"), adds=sc.get("adds"), of=sc.get("of"), overrides=sc["overrides"]) for n, sc in SC.items()},
                   kept_ground=dict(gold_cells=tot_gold, herd_ud=round(tot_ud, 2), shield_km=round(tot_shield)),
                   rows=[dict(r, saving=base[(r["cap_musd"], r["months_paid"])]["backbone_only"] - r["backbone_only"]) for r in rows]), open(OUT / f"FRONTIER_LEAN{tag}.json", "w"), indent=1, ensure_ascii=False)

if __name__ == "__main__": main()
