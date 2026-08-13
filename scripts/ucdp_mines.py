#!/usr/bin/env python3
"""Turn labelled UCDP GED events into mine SITES for the geology eval.

Same unit change as scripts/crisistracker_mines.py (invariant 7): GED's row is
an EVENT -- one attack, one date -- and the geology model is scored against
SITES. The Ndassima mine is attacked across years of GED releases; publishing
the event count as a site count would be a truth set of duplicates on one rock.

WHAT COUNTS AS A SITE
---------------------
Only `mine_site == "at_mine"` labels (scripts/ucdp_label_prompt.py): the event
happened at the mine or in a settlement the text itself calls a mining town,
so the coordinate is the working's. "mine_named"/"miners"/"no" rows are kept
in `excluded` with the reason -- auditable, not sites.

COORDINATE PRECISION IS GED'S OWN, AND IT GATES
-----------------------------------------------
GED codes `where_prec` per event: 1 = exact point, 2 = within ~25 km,
3 = second-order admin centroid, 4 = first-order admin centroid, 5 = country.
A prefecture or country centroid is not a location for a pit -- at 4-5 the
coordinate says nothing about the rock, so those are EXCLUDED (reason
`coarse_coordinates`), not published as approximate. 2-3 keep the site with
`precision: "approximate"`; only 1 earns `"reported"`. This is stricter than
Crisis Tracker's handling because GED tells us the radius and Crisis Tracker
only sometimes does.

COMMODITY PROVENANCE, AGAIN
---------------------------
Only `extracted` reaches `commodities`; `looted` stays in
`commodities_looted`, which no geology score may consume.

Input:  data/ucdp/ged_mining_candidates.json   (scripts/ucdp_fetch.py)
        data/eval/ucdp/event_labels.json       (llm_one_shot, see
                                                scripts/ucdp_label_prompt.py)
Output: data/eval/ucdp/mine_sites.json         <- committed, small, derived

Usage: python3 scripts/ucdp_mines.py
"""
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "ucdp"
OUT_DIR = ROOT / "data" / "eval" / "ucdp"

CLUSTER_KM = 1.0
NAME_KM = 10.0            # identical name merges only within this (see
                          # crisistracker_mines.py -- a name is identity, not
                          # position)
SPREAD_APPROX_KM = 5.0
MODEL = "gpt-5.6-sol"

COMMODITIES = {"gold", "diamond", "coltan", "cassiterite", "tin", "tungsten",
               "copper", "iron", "cobalt", "manganese", "chromite", "salt",
               "sand_gravel", "stone", "other"}

CITATION = ("Davies, Shawn, Garoun Engstr\u00f6m, Therese Pettersson & "
            "Magnus \u00d6berg (2026); Sundberg, Ralph & Erik Melander (2013) "
            "'Introducing the UCDP Georeferenced Event Dataset', Journal of "
            "Peace Research 50(4).")
NOTICE = ("Event records are UCDP's (Uppsala Conflict Data Program, "
          "https://ucdp.uu.se). The mine/commodity labels, the clustering "
          "into sites and any downstream geological score are OURS, produced "
          "by a language model over GED's location descriptions and source "
          "strings, and must not be attributed to UCDP. Commodity provenance "
          "is preserved: 'commodities' are materials the text says the site "
          "yields; materials that appear only as looted/taxed goods are held "
          "separately in 'commodities_looted' and are not evidence about the "
          "rock at that coordinate. Every site is here because organised "
          "violence occurred at it -- conflict presence is the SELECTION "
          "RULE of this list, never an observation to pool with survey "
          "flags (docs/agents/acled.md).")


def km(a, b):
    (lat1, lon1), (lat2, lon2) = a, b
    dy = (lat2 - lat1) * 111.32
    dx = (lon2 - lon1) * 111.32 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def cluster(points):
    n = len(points)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        a, b = find(i), find(j)
        if a != b:
            parent[b] = a

    for i in range(n):
        for j in range(i + 1, n):
            d = km(points[i]["ll"], points[j]["ll"])
            same_name = (points[i]["name"] and
                         points[i]["name"].lower() ==
                         (points[j]["name"] or "").lower())
            if d <= CLUSTER_KM or (same_name and d <= NAME_KM):
                union(i, j)
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return list(groups.values())


def main():
    cand_path = RAW / "ged_mining_candidates.json"
    lab_path = OUT_DIR / "event_labels.json"
    if not cand_path.exists():
        raise SystemExit(f"missing {cand_path} -- run scripts/ucdp_fetch.py")
    if not lab_path.exists():
        raise SystemExit(f"missing {lab_path} -- run scripts/"
                         "ucdp_label_prompt.py then llm_one_shot")
    doc = json.loads(cand_path.read_text())
    rows = {r["id"]: r for r in doc["rows"]}
    labels = {str(x["id"]): x for x in json.loads(lab_path.read_text())}
    missing = set(rows) - set(labels)
    if missing:
        raise SystemExit(f"{len(missing)} candidates have no label "
                         f"(e.g. {sorted(missing)[:5]}): re-run the labeller "
                         f"over the full prompt rather than scoring a subset")

    # ---- validate labels against their own source text --------------------
    # A model that paraphrases its evidence has stopped extracting.
    bad_evidence = []
    for i, x in labels.items():
        for c in x.get("commodities") or []:
            if c not in COMMODITIES:
                raise SystemExit(f"event {i}: commodity {c!r} outside the "
                                 f"prompt's closed list -- prompt and script "
                                 f"have drifted")
        ev = x.get("commodity_evidence")
        if ev:
            r = rows[i]
            hay = " ".join(r.get(k) or "" for k in (
                "where_description", "source_headline", "source_article"))
            if ev not in hay:
                bad_evidence.append(i)
    if bad_evidence:
        raise SystemExit(f"{len(bad_evidence)} labels quote evidence not "
                         f"present in their record ({bad_evidence[:5]}): the "
                         f"labeller paraphrased; re-run it")

    points, excluded = [], []
    for i, x in labels.items():
        r = rows[i]
        excl = {"id": i, "relid": r.get("relid"),
                "where": (r.get("where_description") or "")[:160]}
        if x["mine_site"] != "at_mine":
            excluded.append(dict(excl, reason=x["mine_site"]))
            continue
        prec = int(r["where_prec"])
        if prec >= 4:
            # a first-order-admin or country centroid places nothing
            excluded.append(dict(excl, reason="coarse_coordinates",
                                 where_prec=prec))
            continue
        extracted = (x.get("commodity_source") == "extracted")
        points.append({
            "id": i, "relid": r.get("relid"),
            "ll": (float(r["latitude"]), float(r["longitude"])),
            "name": x.get("site_name"),
            "commodities": sorted(x["commodities"]) if extracted else [],
            "commodities_looted": (sorted(x["commodities"])
                                   if x.get("commodity_source") == "looted"
                                   else []),
            "date": (r.get("date_start") or "")[:10],
            "iso3": r["iso3"],
            "adm1_reported": r.get("adm_1"),
            "where_prec": prec,
            "type_of_violence": int(r["type_of_violence"]),
            "sides": sorted({r.get("side_a"), r.get("side_b")} - {None, ""}),
            "best_deaths": int(r.get("best") or 0),
            "evidence": x.get("commodity_evidence"),
            "confidence": x.get("confidence"),
        })

    if not points:
        raise SystemExit("no at_mine events survived -- the labels or the "
                         "candidate pull are wrong; this is not 'no mines'")

    sites = []
    for idx, group in enumerate(cluster(points)):
        g = [points[i] for i in group]
        lat = sum(p["ll"][0] for p in g) / len(g)
        lon = sum(p["ll"][1] for p in g) / len(g)
        spread = max((km(a["ll"], b["ll"]) for a in g for b in g), default=0.0)
        names = [p["name"] for p in g if p["name"]]
        coms = sorted({c for p in g for c in p["commodities"]})
        looted = sorted({c for p in g for c in p["commodities_looted"]}
                        - set(coms))
        # GED tells us the radius: a site is "reported" only when at least one
        # constituent event is where_prec 1 and the cluster is tight.
        approx = (min(p["where_prec"] for p in g) >= 2
                  or spread > SPREAD_APPROX_KM)
        isos = {p["iso3"] for p in g}
        adm1s = [p["adm1_reported"] for p in g if p["adm1_reported"]]
        sites.append({
            "site_id": f"ged_{idx:03d}",
            "source": "ucdp_ged",
            "iso3": sorted(isos)[0] if len(isos) == 1 else None,
            "lon": round(lon, 6), "lat": round(lat, 6),
            "name": Counter(names).most_common(1)[0][0] if names else None,
            "adm1_reported": (Counter(adm1s).most_common(1)[0][0]
                              if adm1s else None),
            "commodities": coms,
            "commodities_looted": looted,
            "observed": "conflict_report",
            "precision": "approximate" if approx else "reported",
            "best_where_prec": min(p["where_prec"] for p in g),
            "cluster_spread_km": round(spread, 2),
            "events": len(g),
            "event_ids": sorted((p["id"] for p in g), key=int),
            "first_event": min(p["date"] for p in g),
            "last_event": max(p["date"] for p in g),
            # WHO fought there, not WHETHER anyone did: conflict presence is
            # this list's selection rule (see NOTICE).
            "sides": sorted({s for p in g for s in p["sides"]}),
            "best_deaths_total": sum(p["best_deaths"] for p in g),
            "min_label_confidence": min((p["confidence"] or 0) for p in g),
        })
    sites.sort(key=lambda s: (s["iso3"] or "ZZZ", s["site_id"]))

    by_name = defaultdict(list)
    for s in sites:
        if s["name"]:
            by_name[s["name"].lower()].append(s)
    for group in by_name.values():
        if len(group) > 1:
            for s in group:
                s["name_conflict"] = [o["site_id"] for o in group
                                      if o is not s]

    out = {
        "generated_by": "scripts/ucdp_mines.py",
        "source": "UCDP Georeferenced Event Dataset (GED) "
                  f"{doc['ged_version']}, {doc['source_file']}",
        "accessed": doc["accessed"],
        "citation": CITATION,
        "terms": doc["terms"],
        "notice": NOTICE,
        "labelled_by": f"llm_one_shot, model {MODEL}, prompt "
                       f"scripts/ucdp_label_prompt.py",
        "unit": "site (mine working), clustered from GED events at "
                f"{CLUSTER_KM} km single-link or identical site name",
        "totals": {
            "candidates_labelled": len(labels),
            "events_at_mine_located": len(points),
            "sites": len(sites),
            "sites_with_commodity": sum(1 for s in sites if s["commodities"]),
            "sites_approximate": sum(1 for s in sites
                                     if s["precision"] == "approximate"),
            "sites_with_name_conflict": sum(1 for s in sites
                                            if s.get("name_conflict")),
            "by_country": dict(Counter(s["iso3"] for s in sites)),
            "by_commodity": dict(Counter(c for s in sites
                                         for c in s["commodities"])),
            "excluded": dict(Counter(e["reason"] for e in excluded)),
        },
        "sites": sites,
        "excluded": excluded,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "mine_sites.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False))

    t = out["totals"]
    print(f"{t['candidates_labelled']} labelled -> "
          f"{t['events_at_mine_located']} at a located mine -> "
          f"{t['sites']} sites")
    print(f"  by country: {t['by_country']}")
    print(f"  commodity (extracted only): {t['by_commodity']} "
          f"on {t['sites_with_commodity']} sites")
    print(f"  approximate coordinate: {t['sites_approximate']}")
    print(f"  excluded: {t['excluded']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
