#!/usr/bin/env python3
"""Reduce ACLED's Africa aggregated file to per-ADM1 conflict scalars.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not keep a copy of ACLED's dataset. ACLED's Content Usage Terms forbid
creating a dataset that substitutes for theirs, so the only thing that survives
this script is a handful of SCALARS per first-level admin unit -- events,
fatalities, peak weekly population exposure, and a year histogram -- which is
what the mining-reference evaluation needs and nothing more. The weekly rows are
read, summed and dropped. The source .xlsx stays in data/acled/ as a gitignored
cache of ACLED's own file; the derived scalars are what we commit.

The reason we need even this much: our mining truth lists (IPIS field visits,
imagery censuses, OSM tags) are all reachability-limited. Conflict is the most
plausible common cause of "nobody surveyed here", so a per-ADM1 conflict scalar
lets scripts/acled_coverage_bias.py ask whether an affinity score is measuring
geology or measuring where a surveyor could walk.

Input:  data/acled/Africa_aggregated_data_up_to_week_of-*.xlsx
        (scripts/acled_download.py)
Output: data/eval/acled/adm1_conflict.json   <- committed, small, derived

Usage:
    python3 scripts/acled_adm1.py [path/to/xlsx] [--from 1997-01-01]
"""
import argparse
import html
import json
import re
import sys
import unicodedata
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "acled"
OUT = ROOT / "data" / "eval" / "acled" / "adm1_conflict.json"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# The four countries our geology sheets and mining references cover.
WANTED = {
    "Central African Republic": "CAF",
    "South Sudan": "SSD",
    "Sudan": "SDN",
    "Tanzania": "TZA",
}

SOURCE = "ACLED (Armed Conflict Location & Event Data). www.acleddata.com"
CITATION = ('Clionadh Raleigh, Roudabeh Kishi, and Andrew Linke, "Political '
            'instability patterns are obscured by conflict dataset scope '
            'conditions, sources, and coding choices," Humanities and Social '
            'Sciences Communications, 25 February 2023. '
            'https://doi.org/10.1057/s41599-023-01559-4')
TERMS = "https://acleddata.com/contentusage"
NOTICE = ("Derived per-admin1 totals computed from ACLED's Africa aggregated "
          "data file. Not ACLED event data and not a substitute for it: the "
          "weekly rows were summed and discarded. Any inference drawn from "
          "these totals is ours and must not be attributed to ACLED.")

EPOCH = date(1899, 12, 30)  # Excel 1900 system incl. the Lotus leap-year bug


def fold(s):
    """Accent-folded key: ACLED writes Mambere-Kadei, GADM/IPIS Mambéré-Kadéï."""
    s = unicodedata.normalize("NFKD", (s or "").strip())
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def shared_strings(zf):
    data = zf.read("xl/sharedStrings.xml").decode("utf-8")
    return [html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))
            for si in re.findall(r"<si>(.*?)</si>", data, re.S)]


def rows(zf, strs):
    """Stream the sheet. 279k rows / 132 MB of XML: openpyxl cannot open it."""
    with zf.open("xl/worksheets/sheet1.xml") as fh:
        for _, el in ET.iterparse(fh, events=("end",)):
            if el.tag != NS + "row":
                continue
            row = {}
            for c in el:
                v = c.find(NS + "v")
                if v is None or v.text is None:
                    continue
                val = strs[int(v.text)] if c.get("t") == "s" else v.text
                row[re.match(r"([A-Z]+)", c.get("r")).group(1)] = val
            el.clear()
            yield row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", nargs="?")
    ap.add_argument("--from", dest="week_from", default="1997-01-01")
    a = ap.parse_args()

    if a.xlsx:
        src = Path(a.xlsx)
    else:
        cands = sorted(CACHE_DIR.glob("Africa_aggregated_data_up_to_week_of-*.xlsx"))
        if not cands:
            raise SystemExit("no Africa aggregated xlsx -- run "
                             "scripts/acled_download.py first")
        src = cands[-1]
    print(f"reading {src.name}")

    zf = zipfile.ZipFile(src)
    strs = shared_strings(zf)

    col = None
    agg = defaultdict(lambda: {"events": 0, "fatalities": 0, "exposure": 0.0,
                               "lat": None, "lon": None,
                               "by_year": defaultdict(int),
                               "by_event_type": defaultdict(int)})
    seen_countries = set()
    weeks_seen = set()
    for row in rows(zf, strs):
        if col is None:
            names = set(row.values())
            need = {"WEEK", "COUNTRY", "ADMIN1", "EVENT_TYPE", "EVENTS",
                    "FATALITIES"}
            if not need <= names:
                raise SystemExit(f"unexpected header, missing {need - names}")
            col = {v: k for k, v in row.items()}
            continue
        country = row.get(col["COUNTRY"])
        if not country:
            continue
        seen_countries.add(country)
        iso = WANTED.get(country)
        if not iso:
            continue
        week = (EPOCH + timedelta(days=int(float(row[col["WEEK"]])))).isoformat()
        if week < a.week_from:
            continue
        weeks_seen.add(week)
        d = agg[(iso, row.get(col["ADMIN1"]))]
        ev = int(float(row.get(col["EVENTS"], 0) or 0))
        d["events"] += ev
        d["fatalities"] += int(float(row.get(col["FATALITIES"], 0) or 0))
        d["by_year"][week[:4]] += ev
        d["by_event_type"][row.get(col["EVENT_TYPE"]) or "?"] += ev
        pe = float(row.get(col.get("POPULATION_EXPOSURE", ""), 0) or 0)
        d["exposure"] = max(d["exposure"], pe)
        d["lat"] = float(row.get(col.get("CENTROID_LATITUDE", ""), 0) or 0)
        d["lon"] = float(row.get(col.get("CENTROID_LONGITUDE", ""), 0) or 0)

    if not seen_countries:
        raise SystemExit("parsed no rows -- sheet layout changed")
    if not agg:
        raise SystemExit(f"none of {sorted(WANTED)} matched. Present: "
                         f"{sorted(seen_countries)[:12]}")

    out = {
        "source": SOURCE, "source_file": src.name,
        "accessed": datetime.now(timezone.utc).date().isoformat(),
        "citation": CITATION, "terms": TERMS, "notice": NOTICE,
        "week_from": min(weeks_seen), "week_to": max(weeks_seen),
        "unit": "count of ACLED-coded events, and reported fatalities, summed "
                "over the stated week range per first-level admin unit",
        "countries": {},
    }
    for (iso, admin1), d in sorted(agg.items()):
        c = out["countries"].setdefault(
            next(k for k, v in WANTED.items() if v == iso), {"adm1": {}})
        c["adm1"][admin1] = {
            "key": fold(admin1),
            "acled_centroid": [d["lon"], d["lat"]],
            "events": d["events"],
            "fatalities": d["fatalities"],
            "peak_weekly_population_exposure": d["exposure"],
            "events_by_year": dict(sorted(d["by_year"].items())),
            "events_by_event_type": dict(sorted(d["by_event_type"].items())),
        }
    for name, c in out["countries"].items():
        # derived, never typed (AGENTS.md invariant 2)
        c["adm1_units"] = len(c["adm1"])
        c["events"] = sum(u["events"] for u in c["adm1"].values())
        c["fatalities"] = sum(u["fatalities"] for u in c["adm1"].values())

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, sort_keys=False))
    for name, c in sorted(out["countries"].items()):
        print(f"  {name:26} {c['adm1_units']:>3} adm1  {c['events']:>6} events  "
              f"{c['fatalities']:>7} fatalities")
    print(f"{out['week_from']}..{out['week_to']} -> {OUT} "
          f"({OUT.stat().st_size // 1024} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
