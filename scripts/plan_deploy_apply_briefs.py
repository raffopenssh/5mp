#!/usr/bin/env python3
"""Apply team briefs written out-of-band (one LLM call per team, answers as JSON files) to deploy.json and the two deploy
geojsons — the in-process narrator (plan_deploy.py narrate(), muse-glimmer-30b) has been dead since 2026-10-01.

  python3 scripts/plan_deploy_apply_briefs.py <dir with <ID>.out.json> <model name>

Each file is the narrator's JSON answer ({"short","brief","first_season"}) for the team <ID>; the prompt was built exactly as
narrate() builds it (BRIEF_SYS + team record + served zones' stored descriptions). `brief_model` records who wrote it."""
import json, sys
from pathlib import Path
OUT = Path(__file__).resolve().parents[1] / "data/plan_zones/solver"
src, model = Path(sys.argv[1]), sys.argv[2]
ans = {p.name.split(".")[0]: json.load(open(p)) for p in src.glob("*.out.json")}
d = json.load(open(OUT / "deploy.json"))
n = 0
for t in d["teams"]:
    a = ans.get(t["id"])
    if not a: continue
    t["brief"], t["first_season"], t["short"], t["brief_model"] = a["brief"].strip(), (a.get("first_season") or "").strip(), (a.get("short") or "").strip(), model
    n += 1
json.dump(d, open(OUT / "deploy.json", "w"), indent=1, ensure_ascii=False)
for fn in ("deploy_teams.geojson", "deploy_footprint.geojson"):
    fc = json.load(open(OUT / fn))
    for f in fc["features"]:
        t = next((x for x in d["teams"] if x["id"] == f["properties"]["id"]), None)
        if t and t.get("brief_model"):
            f["properties"].update(brief=t["brief"], first_season=t["first_season"], short=t["short"], brief_model=t["brief_model"])
    json.dump(fc, open(OUT / fn, "w"))
left = [t["id"] for t in d["teams"] if (t.get("brief") or "").startswith("(brief")]
print(f"applied {n} briefs ({model}); still missing: {left or 'none'}")
