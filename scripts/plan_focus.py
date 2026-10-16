#!/usr/bin/env python3
"""One file, one command: the focus question (docs/plan/focus.yaml) -> ranked pool -> budget frontier -> chosen deployment + budget.

    python3 -W ignore scripts/plan_focus.py pool|frontier|pick|all [--dry-run] [--focus docs/plan/focus.yaml]

pool      plan_deploy.py under the constraint in focus.yaml with the generous pool caps; the result is copied to
          deploy_focus_pool.json (the frontier grows from it; deploy.json itself is overwritten by `pick`).
frontier  plan_deploy_frontier.py --scenario ... for focus.budget.caps_musd x months -> FRONTIER_LEAN.txt/.json.
pick      the row (cap, months, scenario) in focus.budget.pick: (1) freeze docs/plan/plan.yaml to docs/plan/versions/,
          (2) write the scenario's overrides and the contract months into plan.yaml IN PLACE (comments kept; each edited
          line is tagged `# focus.yaml <scenario> <date>`), (3) plan_deploy.py at the row's team caps, (4) easyplan.py
          (facts -> budget -> summary). Team counts are read from the frontier row, never typed.
--dry-run prints every command and plan.yaml edit, runs nothing, writes nothing."""
import argparse, copy, datetime, json, re, shutil, subprocess, sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]; OUT = ROOT / "data/plan_zones/solver"; PLAN = ROOT / "docs/plan/plan.yaml"
sys.path.insert(0, str(ROOT / "scripts"))

def sh(cmd, dry):
    print("$ " + " ".join(str(c) for c in cmd), flush=True)
    if not dry: subprocess.run([str(c) for c in cmd], cwd=ROOT, check=True)

def constraint_flags(F):
    sp = F.get("spatial") or {}; fl = []
    fac = sp.get("factor", 0.25)
    if sp.get("east_of_line"): fl += ["--deprioritise-east-of", f"{sp['east_of_line']}={fac}"]
    if sp.get("line_clip_lat"): fl += ["--line-clip-lat", *map(str, sp["line_clip_lat"])]
    if sp.get("prioritise_polygon"): fl += ["--prioritise-polygon", f"{sp['prioritise_polygon']}={fac}"]
    for k in sp.get("deprioritise") or []: fl += ["--deprioritise", k if "=" in str(k) else f"{k}={fac}"]
    if F.get("constraint"): fl += ["--constraint", F["constraint"].strip()]
    return fl

def deploy_cmd(F, echo, tango, fp, staff, echo_y1=None):
    p = F["pool"]
    c = [sys.executable, "-W", "ignore", "scripts/plan_deploy.py", "--echo-y2", echo, "--tango-y2", tango, "--fp-y2", fp, "--staff-y2", staff,
         "--echo-car-y2", p.get("car_teams", 2), "--echo-cod-y2", 0, "--echo-sdn-y2", 0, "--robust-draws", p.get("robust_draws", 500), "--no-llm"]
    if echo_y1 is not None: c += ["--echo-y1", echo_y1]
    return c + constraint_flags(F)

def step_pool(F, dry):
    p = F["pool"]; sh(deploy_cmd(F, p["echo"], p["tango"], p["fp"], p["staff"]), dry)
    for ext in ("json", "txt"):
        src = OUT / ("deploy.json" if ext == "json" else "DEPLOY.txt"); dst = OUT / (f"deploy_focus_pool.{ext}" if ext == "json" else "DEPLOY_focus_pool.txt")
        print(f"$ cp {src.name} {dst.name}")
        if not dry: shutil.copy(src, dst)

def step_frontier(F, dry):
    b = F["budget"]; sc = b.get("scenarios", "all")
    c = [sys.executable, "-W", "ignore", "scripts/plan_deploy_frontier.py", "--caps", *map(str, b["caps_musd"]), "--months", *map(str, b["months"])]
    if sc: c += ["--scenario", *(["all"] if sc == "all" else sc)]
    sh(c, dry)

def plan_edits(P_text, overrides, months, tag):
    """edit plan.yaml line by line: top-level `key:` values and the months_paid / months_first_phase children; comments kept."""
    lines = P_text.split("\n"); edits = []
    def set_top(key, val):
        pat = re.compile(rf"^{re.escape(key)}:\s*(.*?)(\s+#.*)?$")
        for i, ln in enumerate(lines):
            m = pat.match(ln)
            if m and not ln.startswith(" "):
                if yaml.safe_load(m.group(1) or "null") == val: return          # already this value: leave the line alone
                new = f"{key}: {json.dumps(val)}" + (m.group(2) or "") + f"  # focus.yaml {tag}"
                edits.append((ln, new)); lines[i] = new
                return
        lines.append(f"{key}: {json.dumps(val)}  # focus.yaml {tag} (new key)"); edits.append(("", lines[-1]))
    def set_child(block, key, val):
        for i, ln in enumerate(lines):
            if ln.startswith(block + ":"):
                for j in range(i + 1, len(lines)):
                    m = re.match(rf"^(\s+){re.escape(key)}:\s*(.*?)(\s+#.*)?$", lines[j])
                    if m:
                        if yaml.safe_load(m.group(2) or "null") == val: return
                        new = f"{m.group(1)}{key}: {json.dumps(val)}" + (m.group(3) or "") + f"  # focus.yaml {tag}"
                        edits.append((lines[j], new)); lines[j] = new
                        return
        raise SystemExit(f"plan.yaml: no {block}.{key}")
    for k, v in overrides.items():
        if k == "assumptions": set_top("assumptions", v)
        else: set_top(k, v)
    if months:
        M = months; P = yaml.safe_load(P_text)
        set_child("months_paid", "echo", [0, M - 1]); set_child("months_paid", "tango", [0, M]); set_child("months_paid", "fp", [P["months_paid"]["fp"][0], M])
        set_child("months_first_phase", "echo", [0, M - 2]); set_child("months_first_phase", "tango", [0, M]); set_child("months_first_phase", "fp", [P["months_first_phase"]["fp"][0], M - 2])
    return "\n".join(lines), edits

def step_pick(F, dry):
    import plan_deploy_frontier as PF
    pk = F["budget"]["pick"]; name = pk.get("scenario") or "baseline"
    J = json.load(open(OUT / "FRONTIER_LEAN.json")) if (OUT / "FRONTIER_LEAN.json").exists() else None
    rows = [r for r in (J or {}).get("rows", []) if r.get("scenario", "baseline") == name and r["cap_musd"] == pk["cap_musd"] and r["months_paid"] == pk["months"]]
    if not rows: raise SystemExit(f"no frontier row for {name} {pk['cap_musd']} M x {pk['months']} — run `frontier` first (scenarios must include {name})")
    r = rows[0]; ov = {} if name == "baseline" else PF.load_scenarios()[name]["overrides"]
    echo_y1 = sum(1 for u in r["units"] if u["kind"] == "ECHO" and u["year"] == 1)
    print(f"row: {name} {pk['cap_musd']} M x {pk['months']} mo -> {r['teams']} teams ({r['echo']} ECHO / {r['tango']} TANGO), {r['fp']} FP, {r['staff']} staff, SSD loaded {r['ssd_loaded']:,.0f}")
    stamp = datetime.date.today().isoformat(); ver = ROOT / f"docs/plan/versions/plan_{stamp}_before_pick_{name}.yaml"
    print(f"$ cp docs/plan/plan.yaml {ver.relative_to(ROOT)}")
    text, edits = plan_edits(PLAN.read_text(), ov, pk["months"], f"{name} {stamp}")
    for old, new in edits: print(f"  plan.yaml: {old.strip() or '(append)'}\n        ->   {new.strip()}")
    if not edits: print("  plan.yaml: no change")
    if not dry:
        ver.parent.mkdir(exist_ok=True); shutil.copy(PLAN, ver); PLAN.write_text(text)
    sh(deploy_cmd(F, r["echo"], r["tango"], r["fp"], r["staff"], echo_y1=echo_y1), dry)
    sh([sys.executable, "scripts/easyplan.py"], dry)
    print("next: re-run `frontier` if you want FRONTIER_LEAN's baseline to be this plan; the PIP generator is local-only (docs/agents/tex.md)")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["pool", "frontier", "pick", "all"]); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--focus", default=str(ROOT / "docs/plan/focus.yaml"))
    a = ap.parse_args(); F = yaml.safe_load(open(a.focus))
    for st in (["pool", "frontier", "pick"] if a.step == "all" else [a.step]):
        print(f"\n== {st}"); {"pool": step_pool, "frontier": step_frontier, "pick": step_pick}[st](F, a.dry_run)

if __name__ == "__main__": main()
