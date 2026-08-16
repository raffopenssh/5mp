#!/usr/bin/env python3
"""Emit GeoJSON of the traced lines (lines_stitched) and captured symbols
for export_labels.sh to fold into the GPKG. Counts printed for the caller
to verify (a manifest is not trusted until its length is checked)."""
import json
import sqlite3
import sys

db, lines_out, symbols_out = sys.argv[1:4]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

sheet_year = dict(c.execute("SELECT id, year FROM sheets"))

nl = 0
with open(lines_out, "w") as f:
    f.write('{"type":"FeatureCollection","features":[\n')
    first = True
    for (lid, kind, style, name, name_src, names_all, n_segs, n_sheets,
         sheets, year_min, year_max, length_km, pts) in c.execute(
            "SELECT id,kind,style,name,COALESCE(name_src,'traced'),names_all,"
            " n_segs,n_sheets,sheets,year_min,year_max,length_km,pts"
            " FROM lines_stitched"):
        feat = {"type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": json.loads(pts)},
                "properties": {"id": lid, "kind": kind, "style": style,
                               "name": name,
                               "name_src": name_src if name else None,
                               "names_all": names_all,
                               "n_segs": n_segs, "sheets": sheets,
                               "year_min": year_min, "year_max": year_max,
                               "length_km": length_km}}
        f.write(("" if first else ",\n") + json.dumps(feat))
        first = False
        nl += 1
    f.write("\n]}\n")

ns = 0
with open(symbols_out, "w") as f:
    f.write('{"type":"FeatureCollection","features":[\n')
    first = True
    # junk = tracer over-capture (text fragments, line crossings): kept in
    # the DB for audit, excluded from the user-facing export.
    for sid, sheet, descr, cat, name, ndist, lon, lat in c.execute(
            "SELECT id, sheet, descr, category, name, name_dist_km, lon, lat"
            " FROM symbols WHERE COALESCE(category,'') != 'junk'"):
        feat = {"type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                "properties": {"id": sid, "sheet": sheet,
                               "year": sheet_year.get(sheet),
                               "descr": descr, "category": cat,
                               "name": name, "name_dist_km": ndist}}
        f.write(("" if first else ",\n") + json.dumps(feat))
        first = False
        ns += 1
    f.write("\n]}\n")

print(f"{nl} {ns}")
