#!/usr/bin/env python3
"""Fetch the public Crisis Tracker incident and community records.

WHY THIS EXISTS
---------------
The geology affinity model (scripts/geomaps/eval_affinity.py) is scored against
mine-occurrence lists that are all reachability-limited, and the four countries
we hold sheets for -- CAR, South Sudan, Sudan, Tanzania -- are exactly the
places where "nobody surveyed there" and "an armed group is there" are hard to
separate. Crisis Tracker is Invisible Children's community-reported incident
database for the CAR/DRC/South Sudan/Darfur border region. Unlike ACLED (which
we can only see at ADM1 aggregate, see docs/agents/acled.md) its PUBLIC map
carries per-incident coordinates, a `location_specifics` field whose values
include "In a Mine", a `livelihood_activity_at_time_of_incident` of "Mining",
and community profiles flagged `presence_of_artisanal_mining`.

That makes it a second SITE-RESOLUTION observation of artisanal mining in the
CAR/DRC/SSD band -- the same kind of evidence as IPIS's armed-actor flag, from
an independent observer network -- and, separately, a coverage probe.

WHAT WE TAKE, AND WHAT WE DO NOT
--------------------------------
The public endpoints are the JSON behind crisistracker.org/map: only records
Invisible Children have already cleared for public display (`map_report`,
`restricted_to_roles: ["public"]`) are returned. We take those and nothing else:
no sign-in, no returnee profiles, no combatant locations -- those 401 and must
stay that way. Returnee and combatant records are withheld deliberately, for
the safety of sources and victims (codebook p.6/p.13); do not add a login here.

The raw pull is a GITIGNORED CACHE of their data (data/crisistracker/). What we
commit is derived: mining-relevant sites only, with commodity labels
(scripts/crisistracker_commodity.py).

INCIDENT COVERAGE IS PAGED AROUND A SERVER CAP
----------------------------------------------
`request_type=max_records` returns at most 5000 records with no flag that it
truncated -- a full pull looks exactly like a complete one (invariant 1). The
whole archive is ~5.2k incidents, so it sits right on the cap. We therefore
fetch in two-year windows and union by id, and ASSERT that no single window
came back at the cap; if one does, the script fails and asks for a narrower
window rather than writing a quietly-short file.

Output:
    data/crisistracker/incidents.json    all public incidents (list)
    data/crisistracker/communities.json  all public communities (list)
    data/crisistracker/details/<id>.json  per-incident detail (mining subset)

Usage:
    python3 scripts/crisistracker_fetch.py            # list pull
    python3 scripts/crisistracker_fetch.py --details  # + detail for mine-linked
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "crisistracker"
BASE = "https://crisistracker.org"
UA = "5mp-conservation-monitor/1.0 (+conservation research; geology model eval)"

SOURCE = ("Crisis Tracker, a project of Invisible Children. "
          "https://crisistracker.org")
TERMS = ("Public map records only (records Invisible Children publish at "
         "crisistracker.org/map). Methodology: https://crisistracker.org/"
         "codebook.pdf . Bulk/extended data: "
         "https://crisistracker.org/about -> Request Data Export")
NOTICE = ("Incident records are reported by Crisis Tracker's community early-"
          "warning network and vetted by Invisible Children. Any commodity "
          "label, clustering or affinity score derived downstream is OURS, not "
          "theirs, and must not be attributed to Crisis Tracker or Invisible "
          "Children.")

# The whole public archive is ~5.2k rows and max_records caps at 5000, so a
# window must stay well under that. Two years is <=1.1k in the worst period.
CAP = 5000
FIRST_YEAR = 1990


def fetch(path, **params):
    q = urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(f"{BASE}{path}?{q}", headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            if attempt == 3:
                raise SystemExit(f"GET {path} failed after 4 tries: {e}")
            time.sleep(2 * (attempt + 1))


def incidents():
    """Union of every public incident, fetched in windows below the cap."""
    seen, out, windows = set(), [], []
    this_year = date.today().year
    bounds = [(FIRST_YEAR, 2007)] + [(y, y + 1)
                                     for y in range(2008, this_year + 2, 2)]
    for y0, y1 in bounds:
        d = fetch("/incidents.json",
                  record_type="incidents", request_type="max_records",
                  stream_type="violence",
                  start_date=f"{y0}-01-01", end_date=f"{y1}-12-31")
        recs = d.get("records") or []
        if len(recs) >= CAP:
            raise SystemExit(
                f"window {y0}-{y1} returned {len(recs)} records, at or above "
                f"the server cap of {CAP}: the pull is truncated and would "
                f"look complete. Narrow the window in bounds[] and re-run.")
        new = [r for r in recs if r["id"] not in seen]
        seen.update(r["id"] for r in recs)
        out.extend(new)
        windows.append({"from": f"{y0}-01-01", "to": f"{y1}-12-31",
                        "returned": len(recs), "new": len(new)})
        print(f"  {y0}-{y1}: {len(recs):5d} records, {len(new):5d} new "
              f"(total {len(out)})", file=sys.stderr)
        time.sleep(0.5)
    return out, windows


def communities():
    """All public communities, plus which characteristic flags each carries.

    The list endpoint returns no attributes, so each characteristic is a
    separate filtered pull and the flags are joined back by id -- that is the
    only way the public API exposes `presence_of_artisanal_mining`.
    """
    chars = ["presence_of_transhumance_pastoralism",
             "presence_of_artisanal_mining",
             "presence_of_regional_markets",
             "border_crossing_point",
             "bordering_active_conservation_area",
             "presence_of_wildlife_trafficking",
             "presence_of_intercommunal_violence"]
    base = fetch("/communities.json", record_type="communities",
                 request_type="max_records")["records"]
    by_id = {c["id"]: dict(c, characteristics=[]) for c in base}
    for ch in chars:
        d = fetch("/communities.json", record_type="communities",
                  request_type="max_records", characteristics=ch)
        recs = d.get("records") or []
        if len(recs) == len(base):
            # every community "has" the flag -> the filter was ignored, which
            # reads as a finding but is a no-op (invariant 1)
            raise SystemExit(f"characteristic {ch} matched all {len(base)} "
                             f"communities: the filter was ignored, not true.")
        for r in recs:
            if r["id"] in by_id:
                by_id[r["id"]]["characteristics"].append(ch)
        print(f"  {ch}: {len(recs)}", file=sys.stderr)
        time.sleep(0.5)
    return list(by_id.values())


def detail(incident_id):
    d = fetch(f"/incidents/{incident_id}.json")
    return (d or {}).get("incidents")


def envelope(meta, records, **extra):
    return dict({"source": SOURCE, "terms": TERMS, "notice": NOTICE,
                 "accessed": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                 "count": len(records), "records": records}, **extra)


# Server-side filters that select mine incidents structurally, whatever the
# note says. A note-text prefilter alone misses 21 of these -- an incident can
# be coded "In a Mine" and described without the word.
STRUCTURAL = [
    {"location_specifics": "in a mine"},
    {"livelihood_activity_at_time_of_incident": "mining"},
]


def structural_ids():
    ids = set()
    for f in STRUCTURAL:
        d = fetch("/incidents.json", record_type="incidents",
                  request_type="max_records", stream_type="violence",
                  start_date=f"{FIRST_YEAR}-01-01", end_date="2100-12-31", **f)
        recs = d.get("records") or []
        if not recs:
            raise SystemExit(f"structural filter {f} returned nothing -- the "
                             f"filter name changed; do not treat as 'no mines'")
        ids.update(r["id"] for r in recs)
        print(f"  {f}: {len(recs)}", file=sys.stderr)
        time.sleep(0.5)
    return ids


def is_mine_linked(r):
    """Rough note-text pre-filter for which incidents deserve a detail fetch.

    Deliberately generous: the note text is the only place a commodity is ever
    named, and the structured mine flags live in the detail record we have not
    fetched yet. Precision is the classifier's job, not this function's.
    """
    n = (r.get("public_display_note") or "").lower()
    return any(k in n for k in (
        "mine", "mining", "miner", "orpaill", "artisanal", "gold", "diamond",
        "quarry", "coltan", "tantal", "cassiterite", "wolfram", "tin ore",
        "dredg", "prospect", "chantier", "carri"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--details", action="store_true",
                    help="also fetch per-incident detail for mine-linked notes")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("incidents:", file=sys.stderr)
    incs, windows = incidents()
    (OUT_DIR / "incidents.json").write_text(json.dumps(
        envelope(None, incs, windows=windows), indent=1))
    print(f"  -> {len(incs)} incidents", file=sys.stderr)

    print("communities:", file=sys.stderr)
    coms = communities()
    (OUT_DIR / "communities.json").write_text(json.dumps(
        envelope(None, coms), indent=1))
    n_mine = sum(1 for c in coms
                 if "presence_of_artisanal_mining" in c["characteristics"])
    print(f"  -> {len(coms)} communities, {n_mine} flagged artisanal mining",
          file=sys.stderr)

    if args.details:
        dd = OUT_DIR / "details"
        dd.mkdir(exist_ok=True)
        print("structural mine filters:", file=sys.stderr)
        sids = structural_ids()
        todo = [r for r in incs if is_mine_linked(r) or r["id"] in sids]
        print(f"details: {len(todo)} candidates "
              f"({len(sids)} structurally coded as mine-related)",
              file=sys.stderr)
        got = 0
        for i, r in enumerate(todo):
            p = dd / f"{r['id']}.json"
            if p.exists():
                got += 1
                continue
            d = detail(r["id"])
            if d:
                p.write_text(json.dumps(d, indent=1))
                got += 1
            if i % 25 == 0:
                print(f"  {i}/{len(todo)}", file=sys.stderr)
            time.sleep(0.3)
        print(f"  -> {got} detail records", file=sys.stderr)


if __name__ == "__main__":
    main()
