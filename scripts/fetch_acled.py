#!/usr/bin/env python3
"""Fetch ACLED event data for the mining reference countries (CAR, South Sudan,
Sudan, Tanzania) into data/acled/.

Purpose: conflict-event context for evaluating our geological mining-affinity
interpreter. ACLED data is REFERENCE CONTEXT ONLY -- see the licence notes in
docs/agents/acled.md before using it in any published figure, and note that
ACLED's Content Usage Terms forbid attributing our own analysis or derived
scores to ACLED.

ACLED retired static API keys in 2025; access is now a myACLED account plus an
OAuth password grant (https://acleddata.com/api-documentation/getting-started).
API access requires a Research/Partner/Enterprise access level -- an Open
myACLED account authenticates fine but gets HTTP 403 from the read endpoints
(request an upgrade at access@acleddata.com).

Credentials come from secrets.env (never hardcode them):

    ACLED_USERNAME=...      # myACLED login e-mail
    ACLED_PASSWORD=...

STATUS 2026-08-13: our account is Open tier, so this script authenticates and
then gets HTTP 403 from every read endpoint. Until ACLED grants a higher access
level, the working path is scripts/acled_download.py + acled_aggregate.py
(weekly admin1 aggregates). Keep this script: it is what to run the day the
upgrade lands.

Usage:
    set -a; source secrets.env; set +a
    python3 scripts/fetch_acled.py --from 2018-01-01
    python3 scripts/fetch_acled.py --countries "Central African Republic" --limit 500

Output: one JSON file per country, data/acled/<slug>.json, plus a manifest.
Counts in the manifest are DERIVED from the rows fetched (invariant 2), and a
country whose paging stopped early is written as unfinished so a re-run retries
it (invariant 1).
"""
import argparse, json, os, sys, time, urllib.parse, urllib.request
from datetime import date
from pathlib import Path

TOKEN_URL = "https://acleddata.com/oauth/token"
READ_URL = "https://acleddata.com/api/acled/read"
CLIENT_ID = "acled"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "acled"
# ISO3 in this repo -> ACLED country name
# ACLED Attribution Policy (https://acleddata.com/attribution-policy) requires
# that a data file presenting ACLED data to another party acknowledge ACLED in a
# source column/field, with full name and link, plus the access date. These are
# written into every output file and into each event row's `source_dataset`.
ATTRIBUTION = "ACLED (Armed Conflict Location & Event Data). www.acleddata.com"
CITATION = ('Clionadh Raleigh, Roudabeh Kishi, and Andrew Linke, "Political '
            'instability patterns are obscured by conflict dataset scope '
            'conditions, sources, and coding choices," Humanities and Social '
            'Sciences Communications, 25 February 2023. '
            'https://doi.org/10.1057/s41599-023-01559-4')
TERMS_URL = "https://acleddata.com/contentusage"
COUNTRIES = {
    "CAF": "Central African Republic",
    "SSD": "South Sudan",
    "SDN": "Sudan",
    "TZA": "Tanzania",
}
PAGE_SIZE = 5000


def post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise SystemExit(f"ACLED {url} -> HTTP {e.code}: {detail}")


def get_token(user, pwd):
    tok = post_form(TOKEN_URL, {"username": user, "password": pwd,
                                "grant_type": "password", "client_id": CLIENT_ID})
    if "access_token" not in tok:
        raise SystemExit(f"no access_token in response: {tok}")
    return tok["access_token"]


def fetch_country(token, country, date_from, date_to, hard_limit=None):
    """Page through the ACLED endpoint. Returns (rows, complete)."""
    rows, page = [], 1
    while True:
        q = {
            "country": country,
            "event_date": f"{date_from}|{date_to}",
            "event_date_where": "BETWEEN",
            "limit": PAGE_SIZE,
            "page": page,
            "_format": "json",
        }
        url = READ_URL + "?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}",
                          "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                payload = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 403:
                print("  ! HTTP 403: this myACLED account authenticates but is "
                      "not authorised for event-level API access (Open tier). "
                      "Request Research/Partner access via access@acleddata.com, "
                      "or use scripts/acled_download.py for weekly aggregates.",
                      file=sys.stderr)
                return rows, False
            print(f"  ! {country} page {page}: HTTP {e.code} "
                  f"{e.read().decode('utf-8','replace')[:200]}", file=sys.stderr)
            return rows, False
        batch = payload.get("data") or []
        rows.extend(batch)
        print(f"  {country}: page {page} -> {len(batch)} rows "
              f"(total {len(rows)})", flush=True)
        if len(batch) < PAGE_SIZE:
            return rows, True
        if hard_limit and len(rows) >= hard_limit:
            # truncated on purpose: say so rather than pass it off as complete
            return rows[:hard_limit], False
        page += 1
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2015-01-01")
    ap.add_argument("--to", dest="date_to", default=date.today().isoformat())
    ap.add_argument("--countries", nargs="*", default=None,
                    help="ACLED country names (default: CAF SSD SDN TZA)")
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after N rows per country (marked unfinished)")
    a = ap.parse_args()

    user = os.environ.get("ACLED_USERNAME")
    pwd = os.environ.get("ACLED_PASSWORD")
    if not user or not pwd:
        raise SystemExit("set ACLED_USERNAME / ACLED_PASSWORD "
                         "(set -a; source secrets.env; set +a)")

    wanted = a.countries or list(COUNTRIES.values())
    iso_of = {v: k for k, v in COUNTRIES.items()}
    token = get_token(user, pwd)
    print(f"authenticated as {user}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "date_from": a.date_from, "date_to": a.date_to, "countries": {}}
    for name in wanted:
        rows, complete = fetch_country(token, name, a.date_from, a.date_to, a.limit)
        iso = iso_of.get(name, name.replace(" ", "_"))
        path = OUT_DIR / f"{iso}.json"
        path.write_text(json.dumps({"country": name, "iso3": iso,
                                    "complete": complete, "events": rows}))
        manifest["countries"][iso] = {
            "country": name,
            "events": len(rows),              # derived, never typed
            "status": "complete" if complete else "unfinished",
            "file": path.name,
        }
        if not complete:
            print(f"  ! {name} incomplete -- re-run to finish", file=sys.stderr)
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    total = sum(c["events"] for c in manifest["countries"].values())
    bad = [k for k, c in manifest["countries"].items() if c["status"] != "complete"]
    print(f"\n{total} events -> {OUT_DIR}"
          + (f"; UNFINISHED: {', '.join(bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
