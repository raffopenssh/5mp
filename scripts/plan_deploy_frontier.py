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
Writes FRONTIER_FOCUS<tag>.txt + .json next to deploy.json."""
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

def months_for(P_, M):
    P_ = copy.deepcopy(P_)
    P_["months_paid"] = dict(echo=[0, M - 1], tango=[0, M], fp=[P_["months_paid"]["fp"][0], M])
    P_["months_first_phase"] = dict(echo=[0, M - 2], tango=[0, M], fp=[P_["months_first_phase"]["fp"][0], M - 2])
    return P_

def by_year(teams, strands):
    rows = []
    for y in (1, 2):
        cnt = lambda k, yy, st="SSD": sum(1 for t in teams if t["kind"] == k and t["year"] == yy and (t.get("strand", "SSD") == st or k != "ECHO"))
        cum = lambda k, st="SSD": sum(1 for t in teams if t["kind"] == k and t["year"] <= y and (t.get("strand", "SSD") == st or k != "ECHO"))
        row = dict(echo=cum("ECHO"), tango=cum("TANGO"), fp=cum("FP"), new_echo=cnt("ECHO", y), new_tango=cnt("TANGO", y), new_fp=cnt("FP", y), staff=cum("ECHO") * 5 + cum("TANGO") * 5 + cum("FP"))
        for k in strands: row[f"echo_{k.lower()}"] = cum("ECHO", k); row[f"new_echo_{k.lower()}"] = cnt("ECHO", y, k); row[f"staff_{k.lower()}"] = cum("ECHO", k) * 5
        rows.append(row)
    return rows

def cost(F0, P_, teams):
    """loaded totals for this team list: (South Sudan request, CAR strand, all) — the budget builder's own summary()"""
    F = copy.deepcopy(F0); strands = list((F["deploy"].get("strands") or {}).keys())
    F["deploy"]["by_year"] = by_year(teams, strands)
    F["deploy"]["teams"] = {t["id"]: dict(t, place=t["place"], year=t["year"], strand=t.get("strand", "SSD")) for t in teams}
    F["deploy"]["fp_places"] = [t["place"] for t in teams if t["kind"] == "FP"]
    s = B.summary(F, P_)
    return s["ssd_total"], s.get("car_total", 0), s["total"], s

def assign(units, n_echo_y1_rule=lambda n: max(2, n // 2), n_fp_y1_rule=lambda n: max(1, n // 2)):
    """ids and waves as plan_deploy.py stages them: TANGO first wave; ECHO first max(2, n//2) first wave; FP first max(1, n//2) first wave"""
    out = []; ne = sum(1 for u in units if u["kind"] == "ECHO"); nf = sum(1 for u in units if u["kind"] == "FP"); ie = it = i_f = 0
    for u in units:
        u = dict(u)
        if u["kind"] == "ECHO": ie += 1; u["id"] = f"E{ie}"; u["year"] = 1 if ie <= n_echo_y1_rule(ne) else 2
        elif u["kind"] == "TANGO": it += 1; u["id"] = f"T{it}"; u["year"] = 1
        else: i_f += 1; u["id"] = f"F{i_f}"; u["year"] = 1 if i_f <= n_fp_y1_rule(nf) else 2
        out.append(u)
    return out

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default=""); ap.add_argument("--caps", type=float, nargs="+", default=[0.8, 1.0, 1.2], help="M USD, loaded South Sudan total")
    ap.add_argument("--months", type=int, nargs="+", default=[13, 8]); ap.add_argument("--max-fp", type=int, default=3); ap.add_argument("--max-echo", type=int, default=6); ap.add_argument("--max-tango", type=int, default=6)
    a = ap.parse_args(); tag = a.tag
    dep = json.load(open(OUT / f"deploy{tag}.json")); F0 = json.load(open(OUT / "facts.json")); Pyaml = yaml.safe_load(open(ROOT / "docs/plan/plan.yaml"))
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
    for M in a.months:
        P_ = months_for(Pyaml, M)
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
            fps = []
            for k in range(a.max_fp):                         # then focal points, one at a time, each by marginal coverage of the chosen teams
                nf = D.focal_points_of(chosen, k + 1, dep["fp_towns"], seed=fps)
                if len(nf) <= len(fps): break
                t = total_of(chosen + nf)
                steps.append(("FP", nf[-1]["place"], nf[-1]["zone_uids"], t[0]))
                if t[0] > cap * 1e6: refused = refused or steps[-1]; break
                fps = nf
            units = assign(chosen + fps); ssd_total, car_total, all_total, summ = cost(F0, P_, units + car)
            cov = coverage(units)
            rows.append(dict(cap_musd=cap, months_paid=M, teams=len(chosen), echo=cnt["ECHO"], tango=cnt["TANGO"], fp=len(fps), staff=5 * len(chosen) + len(fps),
                             ssd_loaded=ssd_total, car_loaded=car_total, backbone_only=base[0], per_phase=summ["ssd_per_year"], load_factor=summ["load_factor"],
                             units=[dict(id=u["id"], kind=u["kind"], place=u["place"], zone_uids=u["zone_uids"], year=u["year"], urgency=u.get("urgency"), robust=u.get("robust")) for u in units],
                             next_refused=refused, steps=steps, coverage=cov,
                             backbone_split=dict(fp_and_backbone=base[3]["backbone_loaded"], hq_and_fixed=[base[0] - sum(base[3]["backbone_loaded"])])))
    # ---------------------------------------------------------------- write
    L = [f"FRONTIER_FOCUS{tag} — what a loaded South Sudan budget cap buys under the deployment constraint plan_deploy.py last ran with; teams and focal points are the OUTPUT.",
         f"  constraint: {dep.get('constraint') or '(none)'}",
         f"  pool: {len(pool)} placed South Sudan sites in staging order (ECHO by selection frequency bucketed 0.1, then urgency; TANGO by urgency); grown one unit at a time, teams then FPs; cost = build_budget.summary (HQ, freight, bank, support, contingency in); CAR strand (K-teams) costed beside, outside the cap.",
         f"  months_paid M: TANGO M / ECHO wave 1 M-1 / ECHO wave 2 M-2 / FP [5, M]; M=13 is plan.yaml as it stands.",
         f"  coverage = share of the ground the constraint keeps (South Sudan cells off the penalised ground): {tot_gold} gold-target cells, herd UD {tot_ud:.1f}, shielded community edge {tot_shield:,.0f} km.", "",
         f"  {'cap M$':>6} {'months':>6} | {'teams':>5} {'ECHO':>4} {'TANGO':>5} {'FP':>3} {'staff':>5} | {'SSD loaded':>11} {'CAR strand':>10} {'backbone-only':>13} | {'gold':>5} {'herd':>5} {'shield':>6} | units (wave) ; first unit refused"]
    for r in rows:
        us = ", ".join(f"{u['id']} {u['place'].split('*')[0].strip()} {u['zone_uids']} (w{u['year']})" for u in r["units"])
        nr = r["next_refused"]; nr_s = f"{nr[0]} {nr[1]} {nr[2]} would be USD {nr[3]:,.0f}" if nr else "pool exhausted"
        L.append(f"  {r['cap_musd']:>6.1f} {r['months_paid']:>6} | {r['teams']:>5} {r['echo']:>4} {r['tango']:>5} {r['fp']:>3} {r['staff']:>5} | {r['ssd_loaded']:>11,.0f} {r['car_loaded']:>10,.0f} {r['backbone_only']:>13,.0f} | {r['coverage']['gold']:>5.0%} {r['coverage']['herd']:>5.0%} {r['coverage']['shield']:>6.0%} | {us} ; refused: {nr_s}")
    b = rows[0]["backbone_split"] if rows else {}
    L += ["", f"  backbone-only = the South Sudan request with NO team and NO focal point: PM, Juba desk, legal/paper track, survey and aviation, HQ oversight, contingency (loaded; M=13 row): USD {rows[0]['backbone_only']:,.0f} — this is why a 0.8 M cap buys no team." if rows else ""]
    L += ["", "  a row's team list is the input to plan_deploy.py for that budget: --echo-y2 ECHO --tango-y2 TANGO --fp-y2 FP --echo-y1 (wave-1 ECHO) --staff-y2 staff."]
    (OUT / f"FRONTIER_FOCUS{tag}.txt").write_text("\n".join(L) + "\n"); print("\n".join(L))
    json.dump(dict(constraint=dep.get("constraint"), deprioritise=dep.get("deprioritise"), spatial=[x["name"] for x in dep.get("spatial_constraint") or []], caps=a.caps, months=a.months,
                   kept_ground=dict(gold_cells=tot_gold, herd_ud=round(tot_ud, 2), shield_km=round(tot_shield)), rows=rows), open(OUT / f"FRONTIER_FOCUS{tag}.json", "w"), indent=1, ensure_ascii=False)

if __name__ == "__main__": main()
