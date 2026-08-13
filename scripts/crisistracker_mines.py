#!/usr/bin/env python3
"""Turn labelled Crisis Tracker incidents into mine SITES for the geology eval.

The unit changes here, and that is the point (invariant 7). Crisis Tracker's
row is an INCIDENT: one attack, one date. The geology model is scored against
SITES: one working, one piece of ground. The same mine is attacked repeatedly --
"Yangou Waka" appears three times -- so publishing the incident count as a site
count would inflate the truth set with duplicates that all sit on one rock.

So incidents are clustered into sites (1 km, single-link, plus an exact name
match) and every output row says how many incidents it is made of.

WHAT COUNTS AS A SITE
---------------------
Only `mine_site == "at_mine"` from scripts/crisistracker_label_prompt.py: the
incident happened at the mine, so the coordinate is the mine's. "mine_named"
(an event near a mine) and "miners" (victims are miners, no location) are kept
in the file as `excluded` with the reason, because a list that silently drops
them cannot be audited -- but they are not sites.

COMMODITY PROVENANCE IS PART OF THE LABEL
-----------------------------------------
"They looted ... some diamonds and gold" names a commodity but not the rock
under the pin: loot travels. The labeller separates `extracted` from `looted`,
and only `extracted` reaches `commodities`. The looted ones are kept in
`commodities_looted`, which no geology score may consume. If we ever forget
that distinction, a supply chain gets scored as a lithology.

LOCATION PRECISION
------------------
`exact_location_of_incident_unknown` marks a coordinate the coder could not
pin; `distance_from_community` then bounds the error, up to "20+ km". At CAR
sheet scale (1:1,500,000) a 20 km error is ~13 mm on the printed map and can
cross a unit boundary, so those sites carry `precision: "approximate"` and the
eval can hold them out. They are NOT dropped here: dropping them silently would
bias the list toward the accessible places (the very bias this project exists
to measure).

Input:  data/crisistracker/details/*.json      (crisistracker_fetch.py)
        data/eval/crisistracker/note_labels.json (llm_one_shot, see
                                                 crisistracker_label_prompt.py)
Output: data/eval/crisistracker/mine_sites.json   <- committed, small, derived
        data/eval/crisistracker/communities_mining.json

Usage: python3 scripts/crisistracker_mines.py
"""
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "crisistracker"
OUT_DIR = ROOT / "data" / "eval" / "crisistracker"

CLUSTER_KM = 1.0
# A mine name is strong evidence of identity but weak evidence of position:
# "Yangou-Pendere" is reported from points 64 km apart, because a note may be
# pinned to the mine, to the nearest community, or to the axis it lies on. So
# the name merges two clusters only when they are already close. Beyond that
# the name is recorded as a conflict, not resolved into a centroid that sits on
# neither pit -- at 1:1,500,000 a 60 km centroid crosses several map units, and
# a truth point in the wrong unit scores the model on our own error.
NAME_KM = 10.0
# Above this spread a cluster's centroid is no longer a location; the site is
# still published (dropping it would bias the list toward well-pinned places)
# but its precision says so.
SPREAD_APPROX_KM = 5.0
MODEL = "gpt-5.6-sol"

# Country label as Crisis Tracker writes it -> ISO3. Their `community_country`
# is the nearest community's country, which is what we have.
ISO_OF = {"CAR": "CAF", "DRC": "COD", "S Sudan": "SSD", "Sudan": "SDN",
          "South Sudan": "SSD", "Uganda": "UGA"}

# Commodities the labeller may emit. A value outside this set means the prompt
# and this script have drifted apart, and it must stop the build rather than
# arrive in a truth set as a silent "other".
COMMODITIES = {"gold", "diamond", "coltan", "cassiterite", "tin", "tungsten",
               "copper", "iron", "cobalt", "manganese", "chromite", "salt",
               "sand_gravel", "stone", "other"}

SOURCE = ("Crisis Tracker, a project of Invisible Children. "
          "https://crisistracker.org (public map records)")
NOTICE = ("Incident reports are Crisis Tracker's. The mine/commodity labels, "
          "the clustering into sites and any downstream geological score are "
          "OURS, produced by a language model over the public display notes, "
          "and must not be attributed to Crisis Tracker or Invisible Children. "
          "Commodity provenance is preserved: 'commodities' are materials the "
          "note says the site yields; materials that appear only as looted "
          "goods are held separately in 'commodities_looted' and are not "
          "evidence about the rock at that coordinate.")


def km(a, b):
    (lat1, lon1), (lat2, lon2) = a, b
    dy = (lat2 - lat1) * 111.32
    dx = (lon2 - lon1) * 111.32 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def cluster(points):
    """Single-link clustering at CLUSTER_KM, or an identical site name."""
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
    lab_path = OUT_DIR / "note_labels.json"
    if not lab_path.exists():
        raise SystemExit(f"missing {lab_path} -- run scripts/"
                         "crisistracker_label_prompt.py then llm_one_shot")
    labels = {x["id"]: x for x in json.loads(lab_path.read_text())}
    details = {int(p.stem): json.loads(p.read_text())
               for p in (RAW / "details").glob("*.json")}
    # the country/region of the nearest community lives on the LIST record, not
    # the detail record -- without this join every site's iso3 is None, which
    # reads as "no country" rather than "we did not look"
    lst = {r["id"]: r for r in
           json.loads((RAW / "incidents.json").read_text())["records"]}
    if not details:
        raise SystemExit("no detail records -- run scripts/crisistracker_fetch"
                         ".py --details")
    missing = set(details) - set(labels)
    if missing:
        raise SystemExit(f"{len(missing)} fetched incidents have no label "
                         f"(e.g. {sorted(missing)[:5]}): re-run the labeller "
                         f"over the full prompt rather than scoring a subset")

    # ---- validate the labels against their own source text ---------------
    # A model that paraphrases its evidence has stopped extracting and started
    # summarising; a commodity we cannot find in the note is not a quotation.
    bad_evidence = []
    for i, x in labels.items():
        for c in x.get("commodities") or []:
            if c not in COMMODITIES:
                raise SystemExit(f"incident {i}: commodity {c!r} is outside the "
                                 f"prompt's closed list -- prompt and script "
                                 f"have drifted")
        ev = x.get("commodity_evidence")
        if ev:
            hay = " ".join(str(details[i].get(k) or "") for k in (
                "public_display_note", "other_looting_types",
                "goods_looted_property_destroyed")).lower()
            if ev.lower() not in hay:
                bad_evidence.append(i)

    points, excluded = [], []
    for i, x in labels.items():
        d = details[i]
        lat, lon = d.get("latitude"), d.get("longitude")
        note = d.get("public_display_note") or ""
        if x["mine_site"] != "at_mine":
            excluded.append({"id": i, "irn": d.get("irn"),
                             "reason": x["mine_site"], "note": note[:200]})
            continue
        if lat is None or lon is None or (abs(lat) < 1e-6 and abs(lon) < 1e-6):
            # Crisis Tracker writes an un-located incident as coordinates
            # "-,-", which arrives as 0.0/0.0. Null island is in the Gulf of
            # Guinea, not the CAR: a truth point there scores the model against
            # the sea.
            excluded.append({"id": i, "irn": d.get("irn"),
                             "reason": "no_coordinates", "note": note[:200]})
            continue
        extracted = (x.get("commodity_source") == "extracted")
        points.append({
            "id": i, "irn": d.get("irn"),
            "ll": (lat, lon), "name": x.get("site_name"),
            "commodities": sorted(x["commodities"]) if extracted else [],
            "commodities_looted": (sorted(x["commodities"])
                                   if x.get("commodity_source") == "looted"
                                   else []),
            "date": (d.get("start_date") or "")[:10],
            "iso3": ISO_OF.get((lst.get(i) or {}).get("community_country")),
            "country": (lst.get(i) or {}).get("community_country"),
            "adm1_reported": (lst.get(i) or {}).get("community_region"),
            "community": (lst.get(i) or {}).get("community_name"),
            "exact_unknown": bool(d.get("exact_location_of_incident_unknown")),
            "distance_from_community": d.get("distance_from_community"),
            "actor": d.get("public_actor_1"),
            "verification_rating": d.get("verification_rating"),
            "evidence": x.get("commodity_evidence"),
            "confidence": x.get("confidence"),
            "note": note,
        })

    if not points:
        raise SystemExit("no at_mine incidents survived -- the labels or the "
                         "detail pull are wrong; this is not 'no mines'")

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
        approx = all(p["exact_unknown"] for p in g) or spread > SPREAD_APPROX_KM
        isos = {p["iso3"] for p in g if p["iso3"]}
        adm1s = [p["adm1_reported"] for p in g if p["adm1_reported"]]
        sites.append({
            "site_id": f"ct_{idx:03d}",
            "source": "crisistracker",
            "iso3": sorted(isos)[0] if len(isos) == 1 else None,
            "lon": round(lon, 6), "lat": round(lat, 6),
            "name": Counter(names).most_common(1)[0][0] if names else None,
            "adm1_reported": (Counter(adm1s).most_common(1)[0][0]
                              if adm1s else None),
            "commodities": coms,
            "commodities_looted": looted,
            "observed": "community_report",
            "precision": "approximate" if approx else "reported",
            "cluster_spread_km": round(spread, 2),
            "incidents": len(g),
            "incident_irns": sorted(p["irn"] for p in g if p["irn"]),
            "first_incident": min(p["date"] for p in g),
            "last_incident": max(p["date"] for p in g),
            # WHO was there, not WHETHER anyone was: every site in this list
            # entered it because an armed group attacked it, so "armed" is the
            # selection rule and cannot be used as an observation the way
            # IPIS's surveyor flag can (see scripts/acled_coverage_bias.py).
            # The actor identity is still information -- LRA vs an
            # unidentified group is a different claim about the same pit.
            "site_armed_actor": sorted({p["actor"] for p in g if p["actor"]}),
            "max_verification_rating": max((p["verification_rating"] or 0)
                                           for p in g) or None,
            "min_label_confidence": min((p["confidence"] or 0) for p in g),
        })
    sites.sort(key=lambda s: (s["iso3"] or "ZZZ", s["site_id"]))

    # A name reported from two places that would not merge is a disagreement
    # about where that mine is. Both rows stay -- one of them is right -- but
    # each says the other exists, so an eval can hold the pair out.
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
        "generated_by": "scripts/crisistracker_mines.py",
        "source": SOURCE,
        "notice": NOTICE,
        "labelled_by": f"llm_one_shot, model {MODEL}, prompt "
                       f"scripts/crisistracker_label_prompt.py",
        "unit": "site (mine working), clustered from incidents at "
                f"{CLUSTER_KM} km single-link or identical site name",
        "totals": {
            "incidents_fetched": len(details),
            "incidents_at_mine": len(points),
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
            "evidence_not_verbatim": len(bad_evidence),
        },
        "sites": sites,
        "excluded": excluded,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "mine_sites.json").write_text(json.dumps(out, indent=1))

    # ---- communities flagged as artisanal-mining towns --------------------
    cpath = RAW / "communities.json"
    if cpath.exists():
        coms = json.loads(cpath.read_text())["records"]
        flagged = [c for c in coms
                   if "presence_of_artisanal_mining" in c["characteristics"]]
        (OUT_DIR / "communities_mining.json").write_text(json.dumps({
            "generated_by": "scripts/crisistracker_mines.py",
            "source": SOURCE,
            "notice": NOTICE,
            "unit": "community (settlement) whose Crisis Tracker profile "
                    "carries presence_of_artisanal_mining. A mining TOWN is "
                    "not a mine: the coordinate is the settlement centre and "
                    "the workings are around it. Use as a coverage/context "
                    "layer, never as a mine occurrence.",
            "count": len(flagged),
            "by_country": dict(Counter(c["country"] for c in flagged)),
            "communities": [dict(c, iso3=ISO_OF.get(c["country"]))
                            for c in flagged],
        }, indent=1))

    t = out["totals"]
    print(f"{t['incidents_fetched']} incidents fetched -> "
          f"{t['incidents_at_mine']} at a mine -> {t['sites']} sites")
    print(f"  by country: {t['by_country']}")
    print(f"  commodity (extracted only): {t['by_commodity']} "
          f"on {t['sites_with_commodity']} sites")
    print(f"  approximate coordinate: {t['sites_approximate']}")
    print(f"  excluded: {t['excluded']}")
    if bad_evidence:
        print(f"  ! {len(bad_evidence)} labels quote evidence not present in "
              f"their note: {bad_evidence[:5]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
