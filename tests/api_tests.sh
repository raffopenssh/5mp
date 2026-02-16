#!/bin/bash
# API Test Suite for 5MP Conservation Globe
# Run with: ./tests/api_tests.sh

set -e

BASE_URL="${BASE_URL:-http://localhost:8000}"
PWD="${TEST_PWD:-test2026}"

PASSED=0
FAILED=0
ERRORS=()

red() { echo -e "\033[31m$1\033[0m"; }
green() { echo -e "\033[32m$1\033[0m"; }
yellow() { echo -e "\033[33m$1\033[0m"; }

test_api() {
    local name="$1"
    local endpoint="$2"
    local expected_status="${3:-200}"
    local json_check="$4"  # jq expression that should return true
    
    printf "%-50s" "$name"
    
    local response
    local status
    
    response=$(curl -s -m 30 -b "$COOKIE_FILE" -w "\n%{http_code}" "${BASE_URL}${endpoint}" 2>/dev/null)
    status=$(echo "$response" | tail -1)
    body=$(echo "$response" | sed '$d')
    
    if [[ "$status" != "$expected_status" ]]; then
        red "FAIL (status: $status, expected: $expected_status)"
        FAILED=$((FAILED + 1))
        ERRORS+=("$name: status $status != $expected_status")
        return 1
    fi
    
    if [[ -n "$json_check" ]]; then
        local check_result
        check_result=$(echo "$body" | jq -r "$json_check" 2>/dev/null || echo "false")
        if [[ "$check_result" != "true" ]]; then
            red "FAIL (json check failed: $json_check)"
            FAILED=$((FAILED + 1))
            ERRORS+=("$name: json check failed")
            return 1
        fi
    fi
    
    green "✓"
    PASSED=$((PASSED + 1))
    return 0
}

test_api_post() {
    local name="$1"
    local endpoint="$2"
    local data="$3"
    local expected_status="${4:-200}"
    
    printf "%-50s" "$name"
    
    local status
    status=$(curl -s -o /dev/null -w "%{http_code}" -X POST -b "$COOKIE_FILE" "${BASE_URL}${endpoint}" \
        -H "Content-Type: application/json" -d "$data" 2>/dev/null)
    
    if [[ "$status" != "$expected_status" ]]; then
        red "FAIL (status: $status, expected: $expected_status)"
        FAILED=$((FAILED + 1))
        ERRORS+=("$name: status $status != $expected_status")
        return 1
    fi
    
    green "✓"
    PASSED=$((PASSED + 1))
    return 0
}

echo "======================================="
echo "5MP Conservation Globe - API Tests"
echo "Base URL: $BASE_URL"
echo "======================================="
echo

# Set up cookie file for auth
COOKIE_FILE=$(mktemp)
curl -s -c "$COOKIE_FILE" "${BASE_URL}/?pwd=${PWD}" > /dev/null
echo "Session cookie obtained"

yellow "=== Authentication ==="
# Note: Some endpoints may not require auth, depends on implementation
test_api "valid_password" "/api/stats" "200" "true"

yellow "\n=== Parks API ==="
test_api "park_stats" "/api/parks/CAF_Chinko/stats" "200" ".park_id == \"CAF_Chinko\""
test_api "park_stats_virunga" "/api/parks/COD_Virunga/stats" "200" ".park_id == \"COD_Virunga\""
test_api "park_stats_serengeti" "/api/parks/TZA_Serengeti/stats" "200" ".park_id == \"TZA_Serengeti\""
test_api "park_data_status" "/api/parks/CAF_Chinko/data-status" "200" "true"
test_api "park_feature_stats" "/api/parks/CAF_Chinko/feature-stats" "200" "true"

yellow "\n=== Fire API ==="
test_api "fire_narrative" "/api/parks/CAF_Chinko/fire-narrative" "200" ".park_id == \"CAF_Chinko\""
test_api "fire_realtime_7d" "/api/parks/CAF_Chinko/fire-realtime" "200" "true"
test_api "fire_realtime_28d" "/api/parks/COD_Virunga/fire-realtime" "200" "true"

yellow "\n=== Features API ==="
test_api "features_fire_trajectory" "/api/parks/CAF_Chinko/features" "200" ".type == \"FeatureCollection\""
test_api "features_chinko_settlement" "/api/parks/CAF_Chinko/features" "200" ".type == \"FeatureCollection\""
test_api "features_virunga" "/api/parks/COD_Virunga/features" "200" ".type == \"FeatureCollection\""
test_api "features_serengeti" "/api/parks/TZA_Serengeti/features" "200" ".type == \"FeatureCollection\""

yellow "\n=== Climate & Species ==="
test_api "climate_chinko" "/api/parks/CAF_Chinko/climate" "200" ".park_id == \"CAF_Chinko\""
test_api "climate_virunga" "/api/parks/COD_Virunga/climate" "200" ".park_id == \"COD_Virunga\""
test_api "species_virunga" "/api/parks/COD_Virunga/species" "200" ".species | length > 0"
test_api "species_serengeti" "/api/parks/TZA_Serengeti/species" "200" ".species | length > 0"

yellow "\n=== Narratives ==="
test_api "deforestation_narrative" "/api/parks/COD_Virunga/deforestation-narrative" "200" "true"
test_api "settlement_narrative" "/api/parks/COD_Virunga/settlement-narrative" "200" "true"
test_api "classified_settlements" "/api/parks/COD_Virunga/classified-settlements" "200" "true"
test_api "classified_deforestation" "/api/parks/COD_Virunga/classified-deforestation" "200" "true"

yellow "\n=== Grid API ==="
test_api "grid_all" "/api/grid" "200" ".type == \"FeatureCollection\""

yellow "\n=== Global Stats ==="
test_api "global_stats" "/api/stats" "200" ".total_settlements > 0"

yellow "\n=== Search ==="
test_api "search_areas" "/api/areas/search" "200" "true"
test_api "wdpa_search" "/api/wdpa/search" "200" "true"

yellow "\n=== Admin Endpoints ==="
test_api "admin_gpx_logs" "/api/admin/gpx-logs" "200" "true"
test_api "admin_learning_results" "/api/admin/learning-results" "200" "true"
test_api "admin_pending" "/api/admin/pending-approvals" "200" "true"
test_api "admin_learned_features" "/api/admin/learned-features?park_id=CAF_Chinko" "200" "true"

yellow "\n=== Notifications ==="
test_api "notifications_list" "/api/notifications" "200" ".total >= 0"
test_api "notifications_has_items" "/api/notifications" "200" ".notifications | type == \"array\""

yellow "\n=== Publications ==="
test_api "publications_virunga" "/api/parks/COD_Virunga/publications" "200" "type == \"array\""
test_api "publications_count" "/api/parks/COD_Virunga/publications/count" "200" ".count >= 0"
# Publications use WDPA IDs - test with known WDPA ID 669 (Mole)
test_api "publications_wdpa" "/api/parks/669/publications" "200" "length >= 0"

yellow "\n=== Infrastructure ==="
test_api "infrastructure_chinko" "/api/parks/CAF_Chinko/infrastructure" "200" "true"
test_api "legal_docs_virunga" "/api/parks/COD_Virunga/legal" "200" ".count >= 0"
test_api "legal_docs_serengeti" "/api/parks/TZA_Serengeti/legal" "200" ".count >= 0"

echo
echo "======================================="
if [[ $FAILED -eq 0 ]]; then
    green "All tests passed! ($PASSED passed, $FAILED failed)"
    exit 0
else
    red "Tests failed: $PASSED passed, $FAILED failed"
    echo "Errors:"
    for err in "${ERRORS[@]}"; do
        red "  - $err"
    done
    exit 1
fi
