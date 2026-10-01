import json, sys, time, os, re
sys.path.insert(0, 'scripts'); os.environ.setdefault("HISTMAP_OCR_MODEL","x")
import plan_conservancy_units as P
import __main__; 
src = open('scripts/plan_deploy.py').read()
BRIEF_SYS = re.search(r'BRIEF_SYS = """(.*?)"""', src, re.S).group(1)
SYS2 = BRIEF_SYS + """
Hard rules (a brief that breaks one is wrong): (a) the SITE numbers (people, road, water) are the TEAM's fields only — never a number from the shortlist or from another settlement; (b) copy units as given (ha stays ha; never convert); (c) a zone's class is its solver_class word, nothing else; (d) an FP is one person in a town doing paper work with county, chiefs and state offices — no patrols, no beacons, no fieldwork; (e) a TANGO team records and accompanies, it builds nothing; (f) if a number is not in the input, leave it out."""
zs = json.load(open('data/plan_zones/solver/zones.geojson'))['features']; byuid = {f["properties"]["uid"]: f["properties"] for f in zs}
teams = {t['id']: t for t in json.load(open('data/plan_zones/solver/deploy.json'))['teams']}
def user_for(s):
    zdesc = []
    for u in s["zone_uids"][:4]:
        p = byuid.get(u, {})
        zdesc.append({k: p.get(k) for k in ("uid", "solver_class", "area_ha", "country", "population_est", "new_since_2015", "towns", "boundary", "unattributed_pct", "mine_top05_cells", "mine_candidates", "mine_watchlist", "mine_reported", "fronts_long", "herd_bundles", "clearing_since_2020_km2", "cropland_2019_pct", "rationale", "panel_name", "panel_story", "panel_boundary_in_words", "panel_herd_calendar")})
    return "TEAM:\n" + json.dumps({k: s[k] for k in ("id", "kind", "place", "lon", "lat", "site_people", "road_km", "water", "season", "why", "year")}, ensure_ascii=False) + "\n\nZONES SERVED (stored descriptions):\n" + json.dumps(zdesc, ensure_ascii=False)[:9000]
models = sys.argv[1].split(','); ids = sys.argv[2].split(','); sysp = SYS2 if '--strict' in sys.argv else BRIEF_SYS
for m in models:
    P.LLM_MODEL = m
    for tid in ids:
        t0=time.time()
        try: out = P.llm_json(sysp, user_for(teams[tid]), max_tokens=4000)
        except Exception as e: out={"error":str(e)[:200]}
        dt=time.time()-t0
        print(f"\n##### {m} {tid} {teams[tid]['place']} ({dt:.0f}s)\n{json.dumps(out, ensure_ascii=False, indent=0)}")
