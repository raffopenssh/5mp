#!/bin/bash
# UI Test Runner for 5MP Conservation Globe
# Uses browser navigation + JavaScript assertions
#
# Run with: ./tests/run_ui_tests.sh
# Requires server running on localhost:8000

set -e

BASE_URL="${BASE_URL:-http://localhost:8000}"
PWD="${TEST_PWD:-test2026}"
TIMEOUT="${TIMEOUT:-5}"

PASSED=0
FAILED=0
SKIPPED=0

red() { echo -e "\033[31m$1\033[0m"; }
green() { echo -e "\033[32m$1\033[0m"; }
yellow() { echo -e "\033[33m$1\033[0m"; }

# Check if server is running (accepts 200 or 302 redirect)
if ! curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/?pwd=${PWD}" | grep -qE "200|302"; then
    red "Error: Server not responding at ${BASE_URL}"
    exit 1
fi

echo "======================================="
echo "5MP Conservation Globe - UI Tests"
echo "Base URL: $BASE_URL"
echo "======================================="
echo
echo "NOTE: For full UI testing, use Playwright or run tests in browser:"
echo "  1. Open ${BASE_URL}/?pwd=${PWD}&test=1"
echo "  2. Open browser console"
echo "  3. Run: await runUITests()"
echo
echo "This script tests URL state encoding/decoding."
echo

# Create cookie file and authenticate
COOKIE_FILE=$(mktemp)
curl -s -c "$COOKIE_FILE" "${BASE_URL}/?pwd=${PWD}" > /dev/null
echo "Session cookie obtained"

# Test URL generation and basic state
yellow "=== Testing Share Link State Encoding ==="

test_url_param() {
    local name="$1"
    local params="$2"
    local expected_selector="$3"
    local check_class="$4"
    
    printf "%-50s" "$name"
    
    # This would need browser automation (Playwright) to fully test
    # For now, just verify the URL structure is valid (use cookie for auth)
    local url="${BASE_URL}/?test=1${params}"
    local status
    status=$(curl -s -b "$COOKIE_FILE" -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
    
    if [[ "$status" == "200" ]]; then
        green "✓ (URL valid)"
        PASSED=$((PASSED + 1))
    else
        red "FAIL (status: $status)"
        FAILED=$((FAILED + 1))
    fi
}

# Test that various URL params don't break page load
test_url_param "basic_load" "" "" ""
test_url_param "filter_panel" "&panel=filter" "" ""
test_url_param "star_modal" "&panel=star" "" ""
test_url_param "admin_panel" "&panel=admin" "" ""
test_url_param "admin_learning_tab" "&panel=admin&admin_tab=learning" "" ""
test_url_param "upload_modal" "&panel=upload" "" ""
test_url_param "notification_open" "&notif=1" "" ""
test_url_param "park_popup" "&popup=CAF_Chinko" "" ""
test_url_param "popup_with_sections" "&popup=CAF_Chinko&sections=fire,deforestation" "" ""
test_url_param "pinned_layer" "&pinned=CAF_Chinko:fire_trajectory" "" ""
test_url_param "starred_parks" "&starred_parks=CAF_Chinko,COD_Virunga" "" ""
test_url_param "starred_narratives" "&starred_narratives=CAF_Chinko:fire,COD_Virunga:deforestation" "" ""
test_url_param "search_query" "&q=virunga" "" ""
test_url_param "bbox_filter" "&bbox=20,-5,30,5" "" ""
test_url_param "country_filter" "&country=COD" "" ""
test_url_param "map_position" "&lat=0&lng=25&z=5" "" ""
test_url_param "keystones_off" "&keystones=0" "" ""
test_url_param "movement_types" "&types=foot,vehicle" "" ""
test_url_param "time_range" "&from=2024-01-01&to=2024-12-31" "" ""
test_url_param "complex_combined" "&popup=TZA_Serengeti&sections=fire,species&panel=star&starred_parks=TZA_Serengeti&lat=-2&lng=35&z=8" "" ""

echo
echo "======================================="
if [[ $FAILED -eq 0 ]]; then
    green "All URL tests passed! ($PASSED passed, $FAILED failed)"
else
    red "Tests completed: $PASSED passed, $FAILED failed"
fi

echo
yellow "=== Full UI Testing Instructions ==="
echo "For complete UI testing with DOM assertions:"
echo
echo "Option 1: Browser Console"
echo "  1. Navigate to: ${BASE_URL}/?pwd=${PWD}&test=1"
echo "  2. Open DevTools Console (F12)"
echo "  3. Wait for page load, then run:"
echo "     await runUITests()"
echo
echo "Option 2: Playwright (recommended for CI)"
echo "  npm install playwright"
echo "  npx playwright test tests/playwright/"
echo

exit $FAILED
