#!/usr/bin/env python3
"""Build sudan250k_labels.kmz -- the KML twin of the labels GPKG, for Google
Earth / Locus / OsmAnd users who will never open QGIS.

Same sources as export_labels.sh's GPKG (labels_dedup + lines_stitched +
symbols, junk symbol captures excluded as in the GeoJSON export), one
consistent snapshot, row counts printed for the caller to verify (a manifest
is not trusted until its length is checked).

KML cannot carry typed columns, so what the GPKG says in schema this file
says in structure: one folder per label category / line kind / symbol class,
each placemark's description naming its provenance (kind, sheets, survey
years, sightings). The junk and collar label folders SHIP but start
unchecked -- they are evidence of what OCR saw, not places (same rule as the
GPKG: filterable, not deleted). Nothing is truncated.

Usage: export_kml.py labels.sqlite3 out.kmz   (prints "<nlabels> <nlines> <nsymbols>")
"""
import sqlite3
import sys
import zipfile
from xml.sax.saxutils import escape

db_path, out_path = sys.argv[1:3]
c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
sheet_year = dict(c.execute("SELECT id, year FROM sheets"))

ICON = "http://maps.google.com/mapfiles/kml/shapes/%s.png"

# KML colours are aabbggrr.
LABEL_CATS = [  # (category, kml colour, default visibility, blurb)
    ("place",      "ffffffff", 1, "settlements, wells-as-places, named localities"),
    ("water",      "ffff9933", 1, "rivers, khors, wells, hafirs"),
    ("terrain",    "ff5a87c8", 1, "jebels, hills, ridges"),
    ("vegetation", "ff4caf50", 1, "forest, scrub, grass notes"),
    ("route",      "ff1673f9", 1, "tracks, routes, named roads"),
    ("boundary",   "fff755a8", 1, "administrative and tribal boundaries"),
    ("note",       "ff7fd4ff", 1, "surveyors' marginal notes (water, going, hazards)"),
    ("collar",     "ff9e9e9e", 0, "sheet-margin text (titles, grid, imprints)"),
    ("junk",       "ff757575", 0, "OCR captures that are not map text"),
]
LINE_KINDS = {  # kind -> (colour, width)
    "watercourse": ("ffff9933", 2),
    "track":       ("ff0953b4", 2),
    "road":        ("ff1673f9", 3),
    "railway":     ("ff222222", 3),
    "telegraph":   ("ffafa39c", 2),
    "boundary":    ("fff755a8", 2),
}
SYM_ICONS = {  # symbol category -> (icon, colour)
    "water":      ("water",              "ffff9933"),
    "settlement": ("homegardenbusiness", "ff00d7ff"),
    "peak":       ("mountains",          "ff5a87c8"),
    "trig_point": ("triangle",           "ffffffff"),
    "tree":       ("parks",              "ff4caf50"),
    "station":    ("rail",               "ff222222"),
    "fort":       ("ranger_station",     "fff755a8"),
}
SYM_FALLBACK = ("placemark_circle", "ffd7d7d7")


def years_of(sheets):
    ys = [sheet_year[s] for s in (sheets or "").split(",")
          if s in sheet_year and sheet_year[s]]
    return (min(ys), max(ys)) if ys else (None, None)


def year_str(y0, y1):
    if not y0:
        return "year unknown"
    return f"surveyed {y0}" if y0 == y1 else f"surveyed {y0}\u2013{y1}"


out = []
w = out.append
w('<?xml version="1.0" encoding="UTF-8"?>\n'
  '<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document>\n'
  '<name>Sudan Survey 1:250,000 (1908\u20131976) \u2014 OCR labels, traced lines, symbols</name>\n'
  '<description>Machine transcription and tracing of the Sudan Survey 1:250,000 sheet series '
  '(Library of Congress g8310m.gct00289). Verify against the sheet before citing. '
  'Built by scripts/histmaps/export_kml.py; the GeoPackage twin carries the same data with typed columns.</description>\n')

for cat, colour, vis, _ in LABEL_CATS:
    w(f'<Style id="lab_{cat}"><IconStyle><scale>0.35</scale><color>{colour}</color>'
      f'<Icon><href>{ICON % "placemark_circle"}</href></Icon></IconStyle>'
      f'<LabelStyle><color>{colour}</color><scale>0.75</scale></LabelStyle></Style>\n')
for kind, (colour, width) in LINE_KINDS.items():
    w(f'<Style id="line_{kind}"><LineStyle><color>{colour}</color><width>{width}</width></LineStyle></Style>\n')
for cat in sorted(set(list(SYM_ICONS) + ["other"])):
    icon, colour = SYM_ICONS.get(cat, SYM_FALLBACK)
    w(f'<Style id="sym_{cat}"><IconStyle><scale>0.7</scale><color>{colour}</color>'
      f'<Icon><href>{ICON % icon}</href></Icon></IconStyle>'
      f'<LabelStyle><scale>0</scale></LabelStyle></Style>\n')

# ---- labels ---------------------------------------------------------------
nlab = 0
w('<Folder><name>Map labels (OCR)</name>\n')
for cat, _, vis, blurb in LABEL_CATS:
    rows = c.execute(
        "SELECT text, kind, COALESCE(note_topic,''), n_src, sheets, lon, lat"
        " FROM labels_dedup WHERE category=? ORDER BY id", (cat,)).fetchall()
    if not rows:
        continue
    w(f'<Folder><name>{escape(cat)} ({len(rows):,})</name>'
      f'<visibility>{vis}</visibility>'
      f'<description>{escape(blurb)}</description>\n')
    for text, kind, topic, n_src, sheets, lon, lat in rows:
        text = " ".join(text.split())
        y0, y1 = years_of(sheets)
        desc = [f"category: {cat}" + (f" / {topic}" if topic else ""),
                f"OCR kind: {kind}", year_str(y0, y1),
                f"sheets: {sheets}",
                f"sightings merged: {n_src}"]
        w(f'<Placemark><name>{escape(text)}</name><visibility>{vis}</visibility>'
          f'<styleUrl>#lab_{cat}</styleUrl>'
          f'<description>{escape("; ".join(desc))}</description>'
          f'<Point><coordinates>{lon:.5f},{lat:.5f}</coordinates></Point></Placemark>\n')
        nlab += 1
    w('</Folder>\n')
w('</Folder>\n')

# ---- traced lines ---------------------------------------------------------
nlines = 0
if c.execute("SELECT count(*) FROM sqlite_master WHERE name='lines_stitched'").fetchone()[0]:
    import json
    w('<Folder><name>Traced lines</name>\n')
    for kind, in c.execute("SELECT DISTINCT kind FROM lines_stitched ORDER BY kind"):
        rows = c.execute(
            "SELECT COALESCE(name,''), COALESCE(style,''), sheets,"
            " year_min, year_max, length_km, pts FROM lines_stitched"
            " WHERE kind=? ORDER BY length_km DESC", (kind,)).fetchall()
        w(f'<Folder><name>{escape(kind)} ({len(rows):,})</name>\n')
        style = f"#line_{kind}" if kind in LINE_KINDS else "#line_track"
        for name, lstyle, sheets, y0, y1, km, pts in rows:
            coords = " ".join(f"{p[0]:.5f},{p[1]:.5f}" for p in json.loads(pts))
            desc = [kind + (f" ({lstyle})" if lstyle else ""),
                    year_str(y0, y1), f"{km:.1f} km", f"sheets: {sheets}",
                    "machine traced -- verify against the sheet"]
            w(f'<Placemark><name>{escape(name or kind)}</name>'
              f'<styleUrl>{style}</styleUrl>'
              f'<description>{escape("; ".join(desc))}</description>'
              f'<LineString><tessellate>1</tessellate><coordinates>{coords}</coordinates></LineString></Placemark>\n')
            nlines += 1
        w('</Folder>\n')
    w('</Folder>\n')

# ---- symbols --------------------------------------------------------------
nsym = 0
if c.execute("SELECT count(*) FROM sqlite_master WHERE name='symbols'").fetchone()[0]:
    w('<Folder><name>Point symbols</name>\n')
    for cat, in c.execute("SELECT DISTINCT COALESCE(category,'unknown') FROM symbols"
                          " WHERE COALESCE(category,'') != 'junk' ORDER BY 1"):
        rows = c.execute(
            "SELECT descr, COALESCE(name,''), sheet, lon, lat FROM symbols"
            " WHERE COALESCE(category,'unknown')=? AND COALESCE(category,'') != 'junk'"
            " ORDER BY id", (cat,)).fetchall()
        w(f'<Folder><name>{escape(cat)} ({len(rows):,})</name>\n')
        style = f"#sym_{cat}" if cat in SYM_ICONS else "#sym_other"
        for descr, name, sheet, lon, lat in rows:
            y = sheet_year.get(sheet)
            desc = [descr or cat, year_str(y, y), f"sheet: {sheet}",
                    "machine classified -- verify against the sheet"]
            w(f'<Placemark><name>{escape(name or cat)}</name>'
              f'<styleUrl>{style}</styleUrl>'
              f'<description>{escape("; ".join(desc))}</description>'
              f'<Point><coordinates>{lon:.5f},{lat:.5f}</coordinates></Point></Placemark>\n')
            nsym += 1
        w('</Folder>\n')
    w('</Folder>\n')

w('</Document>\n</kml>\n')

with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    z.writestr("doc.kml", "".join(out))

print(f"{nlab} {nlines} {nsym}")
