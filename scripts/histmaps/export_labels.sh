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
cd "$(dirname "$0")/../../data/histmaps"

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

ogr2ogr -f GeoJSON "$TMP/labels.geojson" "$TMP/labels.gpkg" sudan250k_labels \
  -lco COORDINATE_PRECISION=5 -lco RFC7946=YES
gzip -9 "$TMP/labels.geojson"

mv "$TMP/labels.gpkg" sudan250k_labels.gpkg
mv "$TMP/labels.geojson.gz" sudan250k_labels.geojson.gz
ls -l sudan250k_labels.gpkg sudan250k_labels.geojson.gz
echo "OK: $N labels exported"
