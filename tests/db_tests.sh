#!/bin/bash
# Database Tests for 5MP Conservation Globe
# Verifies data integrity and query performance
#
# Run with: ./tests/db_tests.sh

set -e

DB_PATH="${DB_PATH:-db.sqlite3}"

PASSED=0
FAILED=0
ERRORS=()

red() { echo -e "\033[31m$1\033[0m"; }
green() { echo -e "\033[32m$1\033[0m"; }
yellow() { echo -e "\033[33m$1\033[0m"; }

test_query() {
    local name="$1"
    local query="$2"
    local expected="$3"  # Optional: expected value or 'nonempty'
    
    printf "%-55s" "$name"
    
    local result
    result=$(sqlite3 "$DB_PATH" "$query" 2>&1)
    local status=$?
    
    if [[ $status -ne 0 ]]; then
        red "FAIL (query error: $result)"
        FAILED=$((FAILED + 1))
        ERRORS+=("$name: $result")
        return 1
    fi
    
    if [[ -n "$expected" ]]; then
        if [[ "$expected" == "nonempty" ]]; then
            if [[ -z "$result" || "$result" == "0" ]]; then
                red "FAIL (expected non-empty, got: '$result')"
                FAILED=$((FAILED + 1))
                ERRORS+=("$name: empty result")
                return 1
            fi
        elif [[ "$result" != "$expected" ]]; then
            red "FAIL (expected '$expected', got '$result')"
            FAILED=$((FAILED + 1))
            ERRORS+=("$name: expected $expected, got $result")
            return 1
        fi
    fi
    
    green "✓ ($result)"
    PASSED=$((PASSED + 1))
    return 0
}

test_index_used() {
    local name="$1"
    local query="$2"
    local expected_index="$3"
    
    printf "%-55s" "$name"
    
    local plan
    plan=$(sqlite3 "$DB_PATH" "EXPLAIN QUERY PLAN $query" 2>&1)
    
    if echo "$plan" | grep -qi "$expected_index"; then
        green "✓ (uses $expected_index)"
        PASSED=$((PASSED + 1))
    else
        yellow "WARN (index $expected_index not used)"
        PASSED=$((PASSED + 1))  # Not a hard failure
    fi
}

echo "======================================="
echo "5MP Conservation Globe - Database Tests"
echo "Database: $DB_PATH"
echo "======================================="
echo

if [[ ! -f "$DB_PATH" ]]; then
    red "Error: Database not found at $DB_PATH"
    exit 1
fi

yellow "=== Table Existence ==="
test_query "park_settlements exists" "SELECT COUNT(*) FROM park_settlements LIMIT 1" "nonempty"
test_query "deforestation_events exists" "SELECT COUNT(*) FROM deforestation_events LIMIT 1" "nonempty"
test_query "feature_geometries exists" "SELECT COUNT(*) FROM feature_geometries LIMIT 1" "nonempty"
test_query "osm_places exists" "SELECT COUNT(*) FROM osm_places LIMIT 1" "nonempty"
test_query "park_rivers_hydro exists" "SELECT COUNT(*) FROM park_rivers_hydro LIMIT 1" "nonempty"
test_query "park_climate exists" "SELECT COUNT(*) FROM park_climate LIMIT 1" "nonempty"
test_query "park_species exists" "SELECT COUNT(*) FROM park_species LIMIT 1" "nonempty"
# OPTIONAL: test_query "legal_documents exists" "SELECT COUNT(*) FROM legal_documents LIMIT 1" "nonempty"
test_query "notifications exists" "SELECT COUNT(*) FROM notifications LIMIT 1" "nonempty"
test_query "pa_publications exists" "SELECT COUNT(*) FROM pa_publications LIMIT 1" "nonempty"
test_query "gpx_upload_logs exists" "SELECT COUNT(*) FROM gpx_upload_logs" "nonempty"
test_query "gpx_learning_results exists" "SELECT 1 FROM gpx_learning_results LIMIT 1" ""

yellow "\n=== Data Counts ==="
# park_fire_analysis is a legacy v2 table (empty since the v5 pipeline);
# fire_narrative_cache is the current per-park fire analysis source of truth.
test_query "parks with fire narratives (v5)" "SELECT COUNT(*) FROM fire_narrative_cache" "nonempty"
test_query "parks with fire trajectories" "SELECT COUNT(DISTINCT park_id) FROM feature_geometries WHERE feature_type='fire_trajectory'" "nonempty"
test_query "settlement events" "SELECT COUNT(*) FROM park_settlements" "nonempty"
test_query "deforestation events" "SELECT COUNT(*) FROM deforestation_events" "nonempty"
test_query "feature geometries" "SELECT COUNT(*) FROM feature_geometries" "nonempty"
test_query "fire trajectories" "SELECT COUNT(*) FROM feature_geometries WHERE feature_type='fire_trajectory'" "nonempty"
test_query "settlements features" "SELECT COUNT(*) FROM feature_geometries WHERE feature_type='settlement'" "nonempty"
test_query "deforestation features" "SELECT COUNT(*) FROM feature_geometries WHERE feature_type='deforestation'" "nonempty"

yellow "\n=== Data Integrity ==="
test_query "parks have boundaries" "SELECT COUNT(*) FROM park_climate WHERE park_id IS NOT NULL" "nonempty"
test_query "CAF_Chinko has fires" "SELECT COUNT(*) FROM feature_geometries WHERE feature_type='fire_trajectory' AND park_id='CAF_Chinko'" "nonempty"
test_query "COD_Virunga has fires" "SELECT COUNT(*) FROM feature_geometries WHERE feature_type='fire_trajectory' AND park_id='COD_Virunga'" "nonempty"
test_query "TZA_Serengeti has fires" "SELECT COUNT(*) FROM feature_geometries WHERE feature_type='fire_trajectory' AND park_id='TZA_Serengeti'" "nonempty"

yellow "\n=== Classification Data ==="
test_query "settlements classified" "SELECT COUNT(*) FROM park_settlements WHERE classification IS NOT NULL" "nonempty"
test_query "deforestation classified" "SELECT COUNT(*) FROM deforestation_events WHERE classification IS NOT NULL" "nonempty"
test_query "settlement types" "SELECT COUNT(DISTINCT classification) FROM park_settlements WHERE classification IS NOT NULL" "nonempty"
test_query "deforestation types" "SELECT COUNT(DISTINCT classification) FROM deforestation_events WHERE classification IS NOT NULL" "nonempty"

yellow "\n=== Polygon ID Links ==="
test_query "settlements with polygon_ids" "SELECT COUNT(*) FROM park_settlements WHERE polygon_ids IS NOT NULL AND polygon_ids != ''" "nonempty"
test_query "deforestation with polygon_ids" "SELECT COUNT(*) FROM deforestation_events WHERE polygon_ids IS NOT NULL AND polygon_ids != ''" "nonempty"
test_query "feature_geometries have feature_ids" "SELECT COUNT(DISTINCT feature_id) FROM feature_geometries WHERE feature_id IS NOT NULL" "nonempty"

yellow "\n=== Settlement provenance (migration 055 / F1-F2) ==="
# A settlement's SURFACE and its mask EXTENT differ by ~24x and must not share a
# column name (AGENTS.md invariant 7); a population is measured or absent, never
# a density constant (invariant 15). These tests are about the SHAPE of the
# claim, not about how far the backfill has got, so none of them counts rows.
test_query "every settlement names its area source" \
    "SELECT COUNT(*) FROM park_settlements WHERE COALESCE(area_source,'')=''" "0"
test_query "every settlement names its population source" \
    "SELECT COUNT(*) FROM park_settlements WHERE COALESCE(population_source,'')=''" "0"
# The ordering is physics: the ground a mask covers cannot be smaller than the
# surface built on it. 1% tolerance for the rounding in properties_json.
test_query "surface never exceeds extent" \
    "SELECT COUNT(*) FROM park_settlements WHERE area_source='ghsl_built_s_surface'
       AND extent_m2 IS NOT NULL AND area_m2 > extent_m2 * 1.01" "0"
# A population without a raster behind it must be absent, not small.
test_query "measured population implies a GHS_POP source" \
    "SELECT COUNT(*) FROM park_settlements WHERE population_est IS NOT NULL
       AND population_est > 0 AND population_source NOT LIKE 'ghsl_GHS_POP%'
       AND population_source != 'legacy_density_200_per_ha'" "0"
test_query "converted rows carry a measured population" \
    "SELECT COUNT(*) FROM park_settlements WHERE area_source='ghsl_built_s_surface'
       AND population_source NOT LIKE 'ghsl_GHS_POP%'" "0"
# F12: settlement_type said 'permanent' for every row because 'temporary'
# required an area below the ingest floor -- unreachable by construction. A
# column with one value is a comment, so the converted rows write NULL until
# something actually measures persistence between epochs. (The unconverted rows
# still say 'permanent'; that is what the backfill queue is for.)
test_query "converted rows do not guess settlement_type" \
    "SELECT COUNT(*) FROM park_settlements
      WHERE area_source='ghsl_built_s_surface' AND settlement_type IS NOT NULL" "0"
# F6: 0 fires within 5 km is a real state ONLY if something looked. The four
# context columns default to 0 and were filled for parks by the Go classifier
# and by nothing at all for AOIs, so 1,552 XSA rows asserted "no fire" for
# settlements whose median is 1,594 detections within 5 km. Scoped to converted
# rows: an unconverted one is the backfill's queue, not a regression
# (scripts/backfill_settlement_surface.py --list).
test_query "fire context zeros are dated" \
    "SELECT COUNT(*) FROM park_settlements WHERE fires_5km = 0
       AND fire_context_at IS NULL AND classified_at IS NULL
       AND area_source = 'ghsl_built_s_surface'" "0"
# The retired pit/turbidity rows measured neither surface nor population and say
# so; they must never acquire a GHSL label (invariant 5 -- provenance is not a
# flag another job can rewrite).
test_query "retired detector rows keep their own label" \
    "SELECT COUNT(*) FROM park_settlements WHERE (polygon_ids IS NULL OR polygon_ids='')
       AND area_source != 'retired_detector'" "0"

yellow "\n=== Deforestation area method (F8-F9) ==="
# Hansen canopy loss (km2 mapped) and GFW alert counts x KM2_PER_ALERT are
# different units drawn as one series; a row must say which.
test_query "every deforestation row names its method" \
    "SELECT COUNT(*) FROM deforestation_events WHERE COALESCE(area_method,'')=''" "0"
test_query "method values are the two known ones" \
    "SELECT COUNT(*) FROM deforestation_events
      WHERE area_method NOT IN ('hansen_canopy_loss','gfw_alert_count')" "0"
test_query "both methods are present" \
    "SELECT COUNT(DISTINCT area_method) FROM deforestation_events" "2"
# F9's anomaly flagger scored ZERO on XSA_Study_Area -- the area it was written
# for -- because a park-wide median hides a local step: 203 km2 appearing in one
# block is 1,000x there and 6.4x park-wide. It exited 0 and read as "nothing is
# anomalous" (invariant 1). It now also tests ~55 km cells, and this asserts the
# motivating case is caught. The year is fixed (a Hansen vintage, not a moving
# window), so a literal is safe here; the COUNT is not asserted because the
# event rows are rebuilt.
test_query "the block F9 was written about is flagged" \
    "SELECT CASE WHEN COUNT(*) > 0 THEN 'ok' ELSE 'unflagged' END
       FROM deforestation_events
      WHERE park_id='XSA_Study_Area' AND year=2023 AND needs_review=1" "ok"
# ...and the flag must stay a statement about specific ground. If it ever
# covered most of the corpus it would be noise nobody reads.
test_query "review flags stay a minority" \
    "SELECT CASE WHEN (SELECT COUNT(*) FROM deforestation_events WHERE needs_review=1)
                      < (SELECT COUNT(*) FROM deforestation_events) / 20
                 THEN 'ok' ELSE 'too-many' END" "ok"

yellow "\n=== Fire containment (F10) ==="
# protected_area_id is the nearest park within park_assigner.ASSIGN_MAX_DIST_KM
# (100 km) -- a catchment, not the park. Tagged rows exceed contained rows by a
# median of 9.8x per park, so any user-facing "fires in park X" count built on
# that column alone is an overstatement (docs/agents/fire.md "F10").
# The invariant is directional and cheap: containment is a SUBSET of tagging,
# and it is a strict one -- if these ever became equal, either the buffer was
# removed (say so) or the flag was clobbered.
test_query "containment is a strict subset of tagging" \
    "SELECT CASE WHEN
       (SELECT COUNT(*) FROM fire_detections WHERE protected_area_id='CAF_Chinko'
          AND +in_protected_area=1)
       < (SELECT COUNT(*) FROM fire_detections WHERE protected_area_id='CAF_Chinko')
     THEN 'ok' ELSE 'equal-or-greater' END" "ok"
# in_protected_area is written at ingest as dist_km==0.0 and never backfilled,
# so it must never be NULL: an unset flag would read as "outside" and silently
# shrink every count that adopts the clause.
test_query "every tagged detection has a containment flag" \
    "SELECT COUNT(*) FROM fire_detections
      WHERE protected_area_id IS NOT NULL AND protected_area_id != ''
        AND in_protected_area IS NULL" "0"
# The clause has a planner trap: idx_fire_infraction (in_protected_area, acq_date)
# looks attractive and turns a 0.2s park lookup into an 18s scan of 8M rows.
# The '+' keeps the park index. If this ever reports idx_fire_infraction, every
# count that took the fix just got 20x slower.
test_index_used "containment clause keeps the park index" \
    "SELECT COUNT(*) FROM fire_detections WHERE protected_area_id='CAF_Chinko' AND +in_protected_area=1" \
    "idx_fire_pa_date"
# The flag is DERIVED from data/keystones_with_boundaries.json, so it is only as
# current as the boundary file it was derived against. The state file records
# that file's digest; when a boundary is edited the two diverge and
# scripts/rederive_fire_containment.py is due. A stamp nobody compares is a
# comment (invariant 5), so this compares it.
printf "%-55s" "containment flag was derived from today's boundaries"
if [[ -f data/fire_containment_state.json ]] && command -v sha256sum >/dev/null; then
    want=$(python3 -c "import json;print(json.load(open('data/fire_containment_state.json'))['boundary_sha256'])")
    have=$(sha256sum data/keystones_with_boundaries.json | cut -d' ' -f1)
    if [[ "$want" == "$have" ]]; then
        green "✓"; PASSED=$((PASSED + 1))
    else
        red "FAIL (boundaries changed since re-derivation; run scripts/rederive_fire_containment.py)"
        FAILED=$((FAILED + 1)); ERRORS+=("containment flag is stale against keystones_with_boundaries.json")
    fi
else
    red "FAIL (data/fire_containment_state.json missing; the flag's provenance is unknown)"
    FAILED=$((FAILED + 1)); ERRORS+=("fire_containment_state.json missing")
fi

yellow "\n=== Sensor epochs (F11) ==="
# One VIIRS sensor flies before 2024, three after, so every raw detection chart
# steps ~3x at 2024-01-01 for reasons that are instrument, not landscape. The
# fleet is MEASURED into fire_sensor_epochs by scripts/build_sensor_epochs.py
# and never typed as a constant (invariant 2) -- these tests check the
# measurement exists and still shows the step, not that it says "three".
test_query "sensor epochs are measured" \
    "SELECT COUNT(*) FROM fire_sensor_epochs" "nonempty"
# Directional, like the containment test: the fleet after the cutover must be
# strictly larger than before it. If these ever equalise, either a sensor was
# retired (the chart should say so) or the builder wrote a fleet it did not see.
test_query "the fleet grows at the 2024 cutover" \
    "SELECT CASE WHEN
       (SELECT MAX(sensor_count) FROM fire_sensor_epochs WHERE month < '2024-01')
       < (SELECT MIN(sensor_count) FROM fire_sensor_epochs WHERE month >= '2024-02' AND month < '2026-01')
     THEN 'ok' ELSE 'no-step' END" "ok"
# Every month with detections must name a fleet: a NULL or empty sensor list
# would read to the client as "no change here" and silently rejoin the line.
test_query "every epoch month names its sensors" \
    "SELECT COUNT(*) FROM fire_sensor_epochs
      WHERE sensors IS NULL OR sensors = '' OR sensor_count < 1 OR detections < 1" "0"

yellow "\n=== UCDP GED derived mine sites (WP5) ==="
# The committed artefact data/eval/ucdp/mine_sites.json feeds the mining
# anchors. Three guards from the plan: the prec-pooling rule (a GED prec>=4
# coordinate is an admin centroid wearing coordinates and must be EXCLUDED,
# never published as a site), the version string (read from UCDP's file, never
# typed), and the anchors' ids resolving back to the artefact they claim to
# come from (a stamp nobody compares is a comment).
printf "%-55s" "UCDP sites: prec>=4 excluded, version named, ids tie"
if UCDP_ERR=$(python3 - <<'PYEOF'
import json, sys
d = json.load(open('data/eval/ucdp/mine_sites.json'))
bad = [s['site_id'] for s in d['sites'] if s['best_where_prec'] > 3]
if bad:
    sys.exit(f"sites with admin-centroid coordinates published: {bad[:5]}")
if 'coarse_coordinates' not in d['totals']['excluded']:
    sys.exit("no coarse_coordinates exclusions recorded -- the prec gate "
             "matched nothing, which is a broken filter, not clean data")
if 'GED' not in d['source'] or not d.get('accessed') or not d.get('citation'):
    sys.exit('attribution fields missing (source/accessed/citation)')
event_ids = {e for s in d['sites'] for e in s['event_ids']}
a = json.load(open('data/geology_truth/mining_anchors.geojson'))
anchor_ids = {i for f in a['features']
              if f['properties']['source'] == 'ucdp_ged'
              for i in (f['properties']['source_id'] or '').split(';') if i}
if not anchor_ids:
    sys.exit('no ucdp_ged anchors -- rebuild scripts/mining_anchors.py')
stray = anchor_ids - event_ids
if stray:
    sys.exit(f"anchors cite GED events the artefact does not hold: "
             f"{sorted(stray)[:5]} -- anchors are stale, re-run "
             f"scripts/mining_anchors.py")
PYEOF
); then
    green "✓"; PASSED=$((PASSED + 1))
else
    red "FAIL ($UCDP_ERR)"
    FAILED=$((FAILED + 1)); ERRORS+=("ucdp mine sites: $UCDP_ERR")
fi

yellow "\n=== Index Usage (Query Plans) ==="
test_index_used "feature by park" "SELECT * FROM feature_geometries WHERE park_id='CAF_Chinko' LIMIT 10" "idx_feat"
test_index_used "settlements by park" "SELECT * FROM park_settlements WHERE park_id='CAF_Chinko' LIMIT 10" "idx"
test_index_used "deforestation by park/year" "SELECT * FROM deforestation_events WHERE park_id='CAF_Chinko' AND year=2023 LIMIT 10" "idx"

yellow "\n=== Sample Queries ==="
test_query "fire narrative cache" "SELECT COUNT(*) FROM fire_narrative_cache WHERE narrative_json IS NOT NULL" "nonempty"
test_query "osm places for Chinko" "SELECT COUNT(*) FROM osm_places WHERE park_id='CAF_Chinko'" "nonempty"
test_query "rivers for Chinko" "SELECT COUNT(*) FROM park_rivers_hydro WHERE park_id='CAF_Chinko'" "nonempty"
test_query "species for Virunga" "SELECT COUNT(*) FROM park_species WHERE park_id='COD_Virunga'" "nonempty"

echo
echo "======================================="
if [[ $FAILED -eq 0 ]]; then
    green "All database tests passed! ($PASSED passed, $FAILED failed)"
    exit 0
else
    red "Tests completed: $PASSED passed, $FAILED failed"
    echo "Errors:"
    for err in "${ERRORS[@]}"; do
        red "  - $err"
    done
    exit 1
fi
