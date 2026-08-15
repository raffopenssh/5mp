#!/bin/bash
# Build the vector downloads of the OCR'd historical-map labels:
#   data/histmaps/sudan250k_labels.gpkg        (QGIS/geo tools, R-tree included)
#   data/histmaps/sudan250k_labels.geojson.gz  (everything else)
#
# Source of truth is labels_dedup in labels.sqlite3 (built by
# ocr_labels.py dedupe + categorize_labels.py apply). Refuses to run when
# the category pass has not been applied -- a labels file whose category
# column is NULL would read as "uncategorized archive", not as "you skipped
# a step". Row count is checked against the source (a manifest is not
# trusted until its length is checked).
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR/../../data/histmaps"

DB=labels.sqlite3
N=$(sqlite3 "$DB" "SELECT count(*) FROM labels_dedup")
NCAT=$(sqlite3 "$DB" "SELECT count(*) FROM labels_dedup WHERE category IS NOT NULL")
if [ "$N" -eq 0 ] || [ "$N" -ne "$NCAT" ]; then
  echo "labels_dedup: $N rows, $NCAT categorized -- run categorize_labels.py apply first" >&2
  exit 1
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
sqlite3 -header -csv "$DB" \
  "SELECT id, replace(replace(text, char(10), ' '), char(13), ' ') AS text, category, kind, n_src, sheets, lon, lat
   FROM labels_dedup ORDER BY id" > "$TMP/labels.csv"

CSVN=$(( $(wc -l < "$TMP/labels.csv") - 1 ))
[ "$CSVN" -eq "$N" ] || { echo "csv has $CSVN rows, expected $N" >&2; exit 1; }

ogr2ogr -f GPKG "$TMP/labels.gpkg" "$TMP/labels.csv" \
  -oo X_POSSIBLE_NAMES=lon -oo Y_POSSIBLE_NAMES=lat -oo KEEP_GEOM_COLUMNS=NO \
  -a_srs EPSG:4326 -nln sudan250k_labels \
  -lco IDENTIFIER="Sudan 1:250k OCR labels" \
  -lco DESCRIPTION="$N labels OCR'd from the Sudan Survey 1:250,000 series (1908-1976, LOC g8310m.gct00289). Machine transcription (fireworks/muse-glimmer-30b) + machine categorization -- verify against the sheet before citing. category: place|water|terrain|vegetation|route|boundary|note|collar|junk."
GN=$(sqlite3 "$TMP/labels.gpkg" "SELECT count(*) FROM sudan250k_labels")
[ "$GN" -eq "$N" ] || { echo "gpkg has $GN rows, expected $N" >&2; exit 1; }

# --- traced lines + captured symbols (trace_lines.py run/refine/dedupe/stitch) ---
# Optional layers: exported only when the trace pipeline has produced them.
# Row counts verified against the source, same as labels.
HAVE_LINES=$(sqlite3 "$DB" "SELECT count(*) FROM sqlite_master WHERE name='lines_stitched'")
if [ "$HAVE_LINES" -eq 1 ] && [ "$(sqlite3 "$DB" 'SELECT count(*) FROM lines_stitched')" -gt 0 ]; then
  read -r NLINES NSYM < <(python3 "$SCRIPT_DIR/export_lines_geojson.py" \
    "$DB" "$TMP/lines.geojson" "$TMP/symbols.geojson")
  LINE_DESC="$NLINES linear features (tracks, roads, railways, telegraph, watercourses, boundaries) vision-LLM traced then snapped to sheet ink; year_min/year_max = survey years of source sheets. Machine traced -- verify against the sheet before citing."
  ogr2ogr -f GPKG -update "$TMP/labels.gpkg" "$TMP/lines.geojson" \
    -nln sudan250k_lines -lco IDENTIFIER="Sudan 1:250k traced lines" \
    -lco DESCRIPTION="$LINE_DESC"
  GLN=$(sqlite3 "$TMP/labels.gpkg" "SELECT count(*) FROM sudan250k_lines")
  [ "$GLN" -eq "$NLINES" ] || { echo "gpkg lines: $GLN rows, expected $NLINES" >&2; exit 1; }
  if [ "$NSYM" -gt 0 ]; then
    ogr2ogr -f GPKG -update "$TMP/labels.gpkg" "$TMP/symbols.geojson" \
      -nln sudan250k_symbols -lco IDENTIFIER="Sudan 1:250k point symbols" \
      -lco DESCRIPTION="$NSYM point symbols (wells, cairns, camps, unclassified marks) captured during line tracing; category NULL until the bulk categorization pass runs."
    GSN=$(sqlite3 "$TMP/labels.gpkg" "SELECT count(*) FROM sudan250k_symbols")
    [ "$GSN" -eq "$NSYM" ] || { echo "gpkg symbols: $GSN rows, expected $NSYM" >&2; exit 1; }
  fi
  gzip -9 -c "$TMP/lines.geojson" > "$TMP/lines.geojson.gz"
else
  NLINES=0; NSYM=0
  echo "note: lines_stitched absent/empty -- labels-only export"
fi

ogr2ogr -f GeoJSON "$TMP/labels.geojson" "$TMP/labels.gpkg" sudan250k_labels \
  -lco COORDINATE_PRECISION=5 -lco RFC7946=YES
gzip -9 "$TMP/labels.geojson"

mv "$TMP/labels.gpkg" sudan250k_labels.gpkg
mv "$TMP/labels.geojson.gz" sudan250k_labels.geojson.gz
if [ "$NLINES" -gt 0 ]; then
  mv "$TMP/lines.geojson.gz" sudan250k_lines.geojson.gz
  ls -l sudan250k_lines.geojson.gz
fi
ls -l sudan250k_labels.gpkg sudan250k_labels.geojson.gz
echo "OK: $N labels, $NLINES lines, $NSYM symbols exported"
