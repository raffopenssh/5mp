#!/usr/bin/env python3
"""EASY plan → one facts file → budget (.txt/.xlsx) → PIP summary text. The only command to
re-run after the plan changes:

    python3 scripts/easyplan.py            # writes data/plan_zones/solver/facts.json,
                                           # reports/BUDGET_EASY_<date>.{txt,xlsx},
                                           # reports/PIP_SUMMARY_EASY.txt (+ docs/plan copy)
    python3 scripts/easyplan.py --check    # exit 1 if the summary text has drifted from its template

WHERE TO EDIT WHAT
  the plan's decisions (horizon, months paid, one-offs) ....... docs/plan/plan.yaml
  the prose of the proposal .................................. docs/plan/PIP_SUMMARY_template.txt  ({{jinja}})
  unit costs / budget lines .................................. scripts/easybudget/build_budget.py
  team placements / zone classes ............................. re-run plan_solver.py solve, plan_deploy.py
  the map ..................................................... scripts/easypip/build_map.py --planner … (writes map_belt.json)
  boundary wording (metes-and-bounds, coords, landmarks) ..... scripts/plan_boundary.py [--narrate]  (writes boundaries.json; read into facts)
Every number in the summary is a template variable resolved from facts.json; none is typed
(AGENTS.md invariant 2). Zone names in the text are keyed by TEAM CODE (E1, T1, F1 …) and
the team→zone mapping comes from deploy.json, so a re-deploy moves the prose with it.
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOLVER = ROOT / "data/plan_zones/solver"
PLAN = ROOT / "docs/plan/plan.yaml"
TEMPLATE = ROOT / "docs/plan/PIP_SUMMARY_template.txt"
FACTS = SOLVER / "facts.json"
sys.path.insert(0, str(ROOT / "scripts/easybudget"))


def j(p):
    return json.load(open(p))


def num(x):
    return f"{x:,.0f}"


def mha(ha):
    return f"{ha/1e6:.2f} million ha"


def ha_words(ha):
    return mha(ha) if ha >= 1e6 else f"{num(ha)} ha"


MONTHS = {"09": "September", "10": "October", "11": "November", "12": "December", "01": "January"}


def boundary_names(props, n=4):
    """Named linear features from the stored boundary description (never the 'geological contact' or landmark lines)."""
    out = []
    for b in json.loads(props["boundary"]):
        if "geological contact" in b or b.startswith("landmarks"):
            continue
        name = re.sub(r" on the .*$", "", b)
        name = name.replace(" (1930s sheet)", "")
        if name.startswith("unnamed"):
            continue
        out.append(name)
        if len(out) >= n:
            break
    return out


def facts(P):
    deploy = j(SOLVER / "deploy.json"); solve = j(SOLVER / "solve.json")
    zones = {f["properties"]["uid"]: f["properties"] for f in j(SOLVER / "zones.geojson")["features"]}
    rank = j(SOLVER / "rank_conservancies.json"); movement = j(SOLVER / "movement.json")
    threat = j(SOLVER / "threat.json"); frontier = j(SOLVER / "frontier.json")
    pip = j(ROOT / "data/eval/pip_facts.json")
    belt = j(SOLVER / "map_belt.json") if (SOLVER / "map_belt.json").exists() else None
    bnd = j(SOLVER / "boundaries.json")["zones"] if (SOLVER / "boundaries.json").exists() else {}   # plan_boundary.py: metes-and-bounds per zone
    N = len(P["year_labels"])

    teams = {}
    for t in deploy["teams"]:
        z = zones[t["zone_uids"][0]] if t["kind"] != "FP" else None
        d = dict(id=t["id"], kind=t["kind"], year=t["year"], place=re.sub(r"\*.*$", "", t["place"]).strip(),
                 place_unverified="*" in t["place"], site_people=t["site_people"], zone_uids=t["zone_uids"],
                 lon=t["lon"], lat=t["lat"], water=t["water"], serves=t.get("serves"))
        if z:
            hb = json.loads(z["herd_bundles"])
            d.update(area_ha=z["area_ha"], people=z["population_est"], country=z["country"], gold=z["mine_top05_cells"],
                     reported=z["mine_reported"], candidates=z["mine_candidates"], new15=z["new_since_2015"],
                     named_pct=100 - z["unattributed_pct"], unnamed_pct=z["unattributed_pct"], herd_pct=z["fronts_transhumance_pct"],
                     crop03=z["cropland_2003_pct"], crop19=z["cropland_2019_pct"], boundary=boundary_names(z),
                     routes=len(hb), herds=sum(b["fronts_fit"] for b in hb),
                     onset=min((b["onset"] for b in hb), default=None), clusters=z["clusters"])
            d["onset_name"] = MONTHS.get(d["onset"], d["onset"]) if d["onset"] else None
            rr = next((r for r in rank["rows"] if r["uid"] == z["uid"]), None)
            if rr: d["shield_pct"] = round(rr["shield"] * 100); d["rank_all"] = len(rank["rows"])
            ec = next((e for e in deploy["echo_candidates"] if e["uid"] == z["uid"]), None)
            if ec: d["urgency_rank"] = ec["rank"]; d["watchlist"] = ec["gold_watch"]
            b = bnd.get(str(z["uid"]))
            if b:
                d["boundary_summary"] = b["summary"]; d["boundary_in_words"] = b.get("in_words"); d["boundary_legal"] = b["legal"]
                d["boundary_schedule"] = b["schedule"]; d["jurisdiction"] = b["jurisdiction"]; d["boundary_legs"] = b["legs"]; d["boundary_named_pct_legs"] = b["named_pct"]
        teams[t["id"]] = d
    # second TANGO on a zone already served
    seen = {}
    for t in sorted(teams.values(), key=lambda x: (x["year"], x["id"])):
        if t["kind"] == "TANGO":
            key = tuple(t["zone_uids"]); t["second_team_of"] = seen.get(key); seen.setdefault(key, t["id"])
    by_year = []
    for y in range(1, N + 1):
        cnt = lambda k, yy: sum(1 for t in teams.values() if t["kind"] == k and t["year"] == yy)
        cum = lambda k: sum(1 for t in teams.values() if t["kind"] == k and t["year"] <= y)
        by_year.append(dict(echo=cum("ECHO"), tango=cum("TANGO"), fp=cum("FP"),
                            new_echo=cnt("ECHO", y), new_tango=cnt("TANGO", y), new_fp=cnt("FP", y),
                            staff=cum("ECHO") * 5 + cum("TANGO") * 5 + cum("FP")))
    echo = [t for t in teams.values() if t["kind"] == "ECHO"]
    tango = [t for t in teams.values() if t["kind"] == "TANGO" and not t["second_team_of"]]
    core_block = max((z for z in zones.values() if z["cls"] == "core"), key=lambda z: z["area_ha"])
    park = pip["park"]
    F = dict(
        generated=date.today().isoformat(), plan=P, years=N,
        deploy=dict(by_year=by_year, teams=teams, fp_places=[t["place"] for t in sorted(teams.values(), key=lambda x: x["id"]) if t["kind"] == "FP"],
                    echo_total_ha=sum(t["area_ha"] for t in echo), echo_total_people=sum(t["people"] for t in echo),
                    echo_total_gold=sum(t["gold"] for t in echo), n_echo=len(echo),
                    tango_total_ha=sum(t["area_ha"] for t in tango), tango_total_people=sum(t["people"] for t in tango), n_tango_zones=len(tango),
                    echo_herd_min=min(t["herd_pct"] for t in echo), echo_herd_max=max(t["herd_pct"] for t in echo),
                    echo_unnamed_min=min(t["unnamed_pct"] for t in echo), echo_unnamed_max=max(t["unnamed_pct"] for t in echo)),
        solve=dict(units=solve["units"], by_class=solve["by_class"], corridor_people=solve["by_class"]["corridor"]["people"],
                   n_community=len([z for z in zones.values() if z["solver_class"] == "community"]),
                   max_core_ha=solve["max_core_ha"], capture=solve["corridor_capture"], axis_draws=solve["opts"]["axis_draws"],
                   max_half_width_km=solve["opts"]["max_width_km"], frontier_runs=len(frontier["rows"]),
                   core_block=dict(area_ha=core_block["area_ha"], people=core_block["population_est"], clearing20=core_block["clearing_since_2020_km2"],
                                   solver_class=core_block["solver_class"], boundary=boundary_names(core_block, 6))),
        movement=dict(fronts=movement["fronts_fit"] + movement["fronts_holdout"], bundles=len(movement["bundles"]),
                      hold_season=movement["hold_season"],
                      holdout_min=min(b["holdout_capture"] for z in zones.values() for b in json.loads(z["herd_bundles"])) if True else None,
                      holdout_max=max(b["holdout_capture"] for z in zones.values() for b in json.loads(z["herd_bundles"])),
                      null_max=max(c["null_allfire"] for b in movement["bundles"] for c in b["skill_curve"] if abs(c["q"] - 0.5) < 1e-9)),
        threat=dict(auc=min(threat["auc_spatial_holdout"]), block_km=20),
        gold=dict(skill=pip["gold"]["skill_top05"], anchors=pip["gold"]["n_anchors"]),
        park=dict(area_km2=park["area_km2"], fires=park["fire_detections_2024_2025"], nov_feb=park["nov_feb_share"], people=park["people"],
                  clearing_km2=park["clearing_km2"], snp_fronts=pip["corridor"]["park_snp_shared_fronts"]),
        xsa=dict(area_km2=pip["xsa"]["area_km2"]),
        belt=belt,
    )
    # the drawn park's perimeter legibility comes from the planner's assess of the KML (validation) if present
    val = SOLVER.parent / "conservancy_units" / "validation.json"
    if val.exists():
        for row in j(val):
            if "Pongo" in str(row.get("name", "")):
                F["park"]["named_pct"] = row["ref_legible_pct"]
                F["park"]["boundary"] = [re.sub(r" on the .*$", "", b).replace(" (1930s sheet)", "") for b in row["ref_boundary"] if not b.startswith("unnamed")]
    # jurisdiction roll-up for the registration paragraph: distinct SSD counties and payams the staffed zones fall in
    counties, payams = {}, set()
    for t in F["deploy"]["teams"].values():
        if t["kind"] == "FP": continue
        for r in t.get("jurisdiction", []):
            if r["country"] != "SSD": continue
            counties.setdefault(r["county"], r["state"]); payams.update(q["payam"] for q in r["payams"])
        t["counties_text"] = "; ".join(f"{r['county']}{' County' if r['country']=='SSD' else ' (' + r['country'] + ')'} {r['pct']}%" + (" (" + ", ".join(q["payam"] for q in r["payams"][:4]) + (", …" if len(r["payams"]) > 4 else "") + ")" if r["payams"] else "") for r in t.get("jurisdiction", []) if r["pct"] >= 5)
    F["deploy"]["counties"] = sorted(counties); F["deploy"]["n_counties"] = len(counties); F["deploy"]["n_payams"] = len(payams)
    F["deploy"]["states"] = sorted(set(counties.values()))
    # which state's Ministry of Mining (the artisanal-licence desk under the Mining Act 2012) covers which staffed zone
    by_state = {}
    for t in F["deploy"]["teams"].values():
        if t["kind"] == "FP": continue
        for r in t.get("jurisdiction", []):
            if r["country"] == "SSD" and r["pct"] >= 5: by_state.setdefault(r["state"], []).append(t["id"])
    F["deploy"]["mining_desks"] = [dict(state=k, pcode=next((r["state_pcode"] for t in F["deploy"]["teams"].values() for r in t.get("jurisdiction", []) if r.get("state") == k and r.get("state_pcode")), None), teams=sorted(set(v))) for k, v in sorted(by_state.items())]
    src = {r["source"] for t in F["deploy"]["teams"].values() for r in t.get("jurisdiction", []) if r["country"] == "SSD"}
    F["deploy"]["admin_source"] = ", ".join(sorted(src))
    import build_budget as B
    F["budget"] = B.summary(F, P)
    return F


def render(F, P):
    import jinja2
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(num=num, mha=mha, ha=ha_words, usd=lambda x: f"USD {x:,.0f}", musd=lambda x: f"USD {x/1e6:.2f} million",
                       kha=lambda x: f"{x/1e3:,.0f},000 ha" if x % 1000 == 0 else f"{x:,.0f} ha")
    WORDS = {0: "no", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
             11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen", 20: "twenty"}
    env.filters["words"] = lambda n: WORDS.get(int(n), str(n))
    T = F["deploy"]["teams"]
    order = lambda ts: sorted(ts, key=lambda t: (t["year"], t["id"]))
    echo_all = order([t for t in T.values() if t["kind"] == "ECHO"])
    tango_all = order([t for t in T.values() if t["kind"] == "TANGO"])
    fp_all = order([t for t in T.values() if t["kind"] == "FP"])
    for f in fp_all:   # serves: team codes standing in the zones this FP covers, by year
        f["serves_codes"] = [t["id"] for t in echo_all + tango_all if set(t["zone_uids"]) & set(f["zone_uids"])]
    core_block = F["solve"]["core_block"]
    return env.from_string(TEMPLATE.read_text()).render(
        F=F, T=T, P=P, B=F["budget"], CB=core_block, echo_all=echo_all, tango_all=tango_all, fp_all=fp_all,
        tango_zones=[t for t in tango_all if not t["second_team_of"]],
        echo_by_year={y: [t for t in echo_all if t["year"] == y] for y in (1, 2)},
        fp_y1=[t["place"] for t in fp_all if t["year"] == 1], fp_y2=[t["place"] for t in fp_all if t["year"] == 2])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-budget", action="store_true")
    ap.add_argument("--show", metavar="CODE", help="print one team card (E1, T2, F1 …) from facts.json and exit — the one-command answer to 'where is team X and what bounds it'")
    a = ap.parse_args()
    if a.show:
        t = json.load(open(FACTS))["deploy"]["teams"].get(a.show.upper()) or sys.exit(f"no team {a.show}; codes: {', '.join(json.load(open(FACTS))['deploy']['teams'])}")
        print(f"{a.show.upper()} — {t['kind']} team at {t['place']}, year {t['year']}, zone(s) {t['zone_uids']}")
        for k in ("area_ha", "people", "gold", "urgency_rank"):
            if k in t: print(f"  {k}: {t[k]}")
        for r in t.get("jurisdiction", []):
            w = {"SSD": "County", "COD": "Territoire", "CAF": "Sous-préfecture", "SDN": "Locality"}[r["country"]]
            print(f"  jurisdiction: {r['county']} {w}, {r['state']} ({r['country']}) {r['pct']}%" + (" — payams " + ", ".join(f"{q['payam']} ({q['pcode']}) {q['pct']}%" for q in r["payams"]) if r.get("payams") else ""))
        print("\n  IN WORDS: " + (t.get("boundary_in_words") or "—"))
        print("\n  SUMMARY: " + (t.get("boundary_summary") or "—"))
        print("\n  " + (t.get("boundary_schedule") or "").replace("\n", "\n  "))
        return
    P = yaml.safe_load(open(PLAN))
    F = facts(P)
    FACTS.write_text(json.dumps(F, indent=1, default=str))
    tag = date.today().strftime("%Y-%m")
    if not a.no_budget:
        import build_budget as B
        x = ROOT / f"reports/BUDGET_EASY_{tag}.xlsx"; t = ROOT / f"reports/BUDGET_EASY_{tag}.txt"
        B.build_xlsx(str(x), F, P); B.build_txt(str(t), x.name, F, P)
        print(f"budget: {B.summary(F, P)['total']:,} USD over {F['years']} years → {t.name}, {x.name}")
    txt = render(F, P)
    out = ROOT / "reports/PIP_SUMMARY_EASY.txt"
    if a.check:
        if out.exists() and out.read_text() == txt:
            print("summary: unchanged"); return
        sys.exit("summary: DRIFT — re-run without --check")
    out.write_text(txt); (ROOT / "docs/plan/PIP_SUMMARY_EASY.txt").write_text(txt)
    print(f"summary: {out} ({len(txt.splitlines())} lines); facts: {FACTS}")


if __name__ == "__main__":
    main()
