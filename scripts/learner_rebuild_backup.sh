#!/usr/bin/env bash
# Back up, verify, then (only with --delete) remove the rows a learner rebuild
# rewrites for one autofetch tenant. Scope is exactly:
#
#   learned_airstrips / learned_places / learned_roads and their *_history
#       for the parks named in $PARKS
#   feature_geometries rows the learner published (properties source=gpx_learner)
#       for those parks
#   effort_data, subcell_visits and gpx_upload_logs for env $ENV
#
# Nothing else is read for deletion. The dump holds only those rows (not the
# database) and is restored into a scratch database and compared row-for-row
# before anything is deleted; a mismatch aborts.
#
#   scripts/learner_rebuild_backup.sh af12 TZA_Nyerere,TZA_Ruaha            # dump + verify
#   scripts/learner_rebuild_backup.sh af12 TZA_Nyerere,TZA_Ruaha --delete   # ... then delete
#
# Restore: sqlite3 db.sqlite3 < backups/learner-rebuild-<env>-<stamp>.sql
set -euo pipefail
ENV="${1:?env}"; PARKS="${2:?parks, comma separated}"; MODE="${3:-}"
DB="${DB:-db.sqlite3}"
[[ "$ENV" =~ ^[a-z0-9]+$ && "$PARKS" =~ ^[A-Za-z0-9_,]+$ ]] || { echo "bad env/parks" >&2; exit 2; }
IN="'${PARKS//,/\',\'}'"
OUT="${OUT:-backups/learner-rebuild-$ENV-$(date -u +%Y%m%dT%H%M%S).sql}"
mkdir -p "$(dirname "$OUT")"

# table | WHERE
SCOPE=(
  "learned_airstrips|park_id IN ($IN)"
  "learned_places|park_id IN ($IN)"
  "learned_roads|park_id IN ($IN)"
  "learned_airstrips_history|park_id IN ($IN)"
  "learned_places_history|park_id IN ($IN)"
  "learned_roads_history|park_id IN ($IN)"
  "feature_geometries|park_id IN ($IN) AND json_extract(properties_json,'\$.source') = 'gpx_learner'"
  "effort_data|env = '$ENV'"
  "subcell_visits|env = '$ENV'"
  "gpx_upload_logs|env = '$ENV'"
)

# SCOPE_FILE=<file of "table|WHERE" lines> replaces the list above (same dump,
# verify and batched delete; order the lines so a row is deleted before the
# rows its WHERE reads).
if [[ -n "${SCOPE_FILE:-}" ]]; then mapfile -t SCOPE < <(grep -v '^\s*\(#\|$\)' "$SCOPE_FILE"); fi

count() { sqlite3 "$1" "SELECT COUNT(*) FROM $2 WHERE $3"; }

: > "$OUT"
echo "-- learner rebuild backup: env=$ENV parks=$PARKS $(date -u +%FT%TZ)" >> "$OUT"
for row in "${SCOPE[@]}"; do
  t="${row%%|*}"; w="${row#*|}"
  n=$(count "$DB" "$t" "$w")
  echo "-- $t: $n rows WHERE $w" >> "$OUT"
  sqlite3 "$DB" ".mode insert $t" "SELECT * FROM $t WHERE $w;" >> "$OUT"
  printf '%-28s %8d rows\n' "$t" "$n"
done
chmod 600 "$OUT"

# Verify: restore into a scratch db that has only the schema, compare counts
# and a content checksum per table.
SCRATCH=$(mktemp --suffix=.sqlite3); trap 'rm -f "$SCRATCH"' EXIT
for row in "${SCOPE[@]}"; do
  t="${row%%|*}"
  sqlite3 "$DB" "SELECT sql || ';' FROM sqlite_master WHERE type='table' AND name='$t'" | sqlite3 "$SCRATCH"
done
{ echo "BEGIN;"; cat "$OUT"; echo "COMMIT;"; } | sqlite3 "$SCRATCH"   # one transaction: per-row commits took 20+ min for 345k rows
sum() { sqlite3 "$1" ".mode list" "SELECT * FROM $2 WHERE $3 ORDER BY rowid;" | sha256sum | cut -c1-16; }
for row in "${SCOPE[@]}"; do
  t="${row%%|*}"; w="${row#*|}"
  a=$(count "$DB" "$t" "$w"); b=$(count "$SCRATCH" "$t" "1")
  sa=$(sum "$DB" "$t" "$w"); sb=$(sum "$SCRATCH" "$t" "1")
  [[ "$a" == "$b" && "$sa" == "$sb" ]] || { echo "VERIFY FAILED: $t live $a/$sa, restored $b/$sb" >&2; exit 1; }
done
echo "verified restorable: $OUT ($(du -h "$OUT" | cut -f1))"

[[ "$MODE" == "--delete" ]] || { echo "(no --delete: nothing removed)"; exit 0; }
for row in "${SCOPE[@]}"; do
  t="${row%%|*}"; w="${row#*|}"
  # Batched, so SQLite's single writer is released between statements.
  while :; do
    n=$(sqlite3 "$DB" "PRAGMA busy_timeout=60000; DELETE FROM $t WHERE rowid IN (SELECT rowid FROM $t WHERE $w LIMIT 5000); SELECT changes();" | tail -1)
    [[ "$n" -gt 0 ]] || break
  done
  printf 'deleted %-28s -> %d left\n' "$t" "$(count "$DB" "$t" "$w")"
done
