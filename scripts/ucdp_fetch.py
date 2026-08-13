#!/usr/bin/env python3
"""Fetch UCDP GED and keep the mine-related candidate events for labelling.

WHY UCDP AT ALL (docs/PLAN_NEW_DATA_LAYERS.md WP5)
--------------------------------------------------
UCDP GED is the third conflict source in this project, and like the other two
it enters as TEXT to be qualified, not as rows to be counted: ACLED's terms
forbid rows in a public export (its derived ADM1 scalars live in
data/eval/acled/), Crisis Tracker covers only the LRA-affected east. GED's
`where_description` and `source_article` prose names mines the same way Crisis
Tracker's notes do ("mining town of Bambou", "Ndassima gold mine"), across a
longer period (1989-) and all of our sheet countries. The same
extract-with-verbatim-evidence pipeline turns those into geology truth points.

WHAT THIS SCRIPT DOES
---------------------
Downloads the GED yearly-release CSV zip (the API now requires a token; the
bulk file does not), filters to the sheet countries, and keeps every event
whose prose matches a broad mining keyword. The keyword net is deliberately
loose -- "gold"/"diamond" catch loot and trade too -- because the LLM labeller
(scripts/ucdp_label_prompt.py) is the instrument that separates a working from
a supply chain. This script only decides what is worth labelling.

The download is a cache of UCDP's file and stays out of git
(data/ucdp/*.csv, *.zip); the candidate list data/ucdp/ged_mining_candidates.json
is the labeller's input and also uncommitted (it embeds UCDP's source prose).
Committed artefacts are the derived labels and sites in data/eval/ucdp/.

Invariant 1: a corpus far off the expected size aborts. GED 26.1 holds 417,968
events (an embedded-newline-aware csv count, not `wc -l`), 18,623 in our countries, 201 keyword candidates; a pull that finds
none of those numbers within tolerance is a broken filter, not a quiet world.

Usage: python3 scripts/ucdp_fetch.py [--version 26.1]
"""
import argparse
import csv
import io
import json
import re
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "ucdp"

# The geology sheets' countries, in GED's own country names.
COUNTRIES = {
    "Central African Republic": "CAF",
    "DR Congo (Zaire)": "COD",
    "South Sudan": "SSD",
    "Sudan": "SDN",
    "Tanzania": "TZA",
}

# Loose on purpose: precision comes from the labeller, recall comes from here.
KW = re.compile(
    r"\b(mine|mines|mining|miner|miners|gold|goldfield|diamond|diamonds"
    r"|quarry|carri[e\u00e8]re|orpaillage)\b", re.I)

KEEP = ("id", "relid", "year", "type_of_violence", "conflict_name",
        "side_a", "side_b", "where_prec", "where_description", "adm_1",
        "adm_2", "latitude", "longitude", "date_start", "date_end", "best",
        "source_headline", "source_article", "event_clarity")

# Expected corpus sizes for 26.1, ±20%: the abort thresholds that make a
# filter-matched-nothing run fail loudly instead of freezing an empty answer.
EXPECT = {"total": 417_968, "in_countries": 18_623, "candidates": 201}


def fetch(version):
    slug = "ged" + version.replace(".", "")
    url = f"https://ucdp.uu.se/downloads/ged/{slug}-csv.zip"
    dest = RAW / f"{slug}-csv.zip"
    if not dest.exists():
        print(f"  downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=600) as r:
            dest.write_bytes(r.read())
    zf = zipfile.ZipFile(dest)
    names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    if len(names) != 1:
        sys.exit(f"{dest}: expected one CSV inside, found {names}")
    return names[0], io.TextIOWrapper(zf.open(names[0]), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="26.1")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)

    csv.field_size_limit(10 ** 8)
    csv_name, fh = fetch(args.version)
    total = in_countries = 0
    rows = []
    for row in csv.DictReader(fh):
        total += 1
        iso = COUNTRIES.get(row["country"])
        if not iso:
            continue
        in_countries += 1
        text = " ".join(row.get(k) or "" for k in
                        ("where_description", "source_headline",
                         "source_article"))
        if not KW.search(text):
            continue
        kept = {k: row[k] for k in KEEP}
        kept["iso3"] = iso
        rows.append(kept)

    # Invariant 1. A different GED version will drift, hence the wide band;
    # a broken filter lands nowhere near it.
    for name, got in (("total", total), ("in_countries", in_countries),
                      ("candidates", len(rows))):
        want = EXPECT[name]
        if not (0.8 * want <= got):
            sys.exit(f"UNFINISHED: {name}={got}, expected ~{want} "
                     f"(GED {args.version}); the filter matched too little")

    out = RAW / "ged_mining_candidates.json"
    out.write_text(json.dumps({
        "source": "UCDP Georeferenced Event Dataset (GED)",
        "ged_version": args.version,
        "source_file": csv_name,
        "accessed": date.today().isoformat(),
        "citation": ("Davies, Shawn, Garoun Engstr\u00f6m, Therese Pettersson "
                     "& Magnus \u00d6berg (2026); Sundberg, Ralph & Erik "
                     "Melander (2013) 'Introducing the UCDP Georeferenced "
                     "Event Dataset', Journal of Peace Research 50(4)."),
        "terms": "https://ucdp.uu.se (free to use with citation; "
                 "no formal licence document)",
        "notice": "Cache of UCDP's prose for labelling; not committed. "
                  "Committed artefacts are the derived labels/sites in "
                  "data/eval/ucdp/.",
        "countries": COUNTRIES,
        "totals": {"ged_events": total, "in_countries": in_countries,
                   "candidates": len(rows)},
        "rows": rows,
    }, indent=1, ensure_ascii=False))
    print(f"  {total} GED events, {in_countries} in sheet countries, "
          f"{len(rows)} mining-keyword candidates -> {out}")


if __name__ == "__main__":
    main()
