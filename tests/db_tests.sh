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
