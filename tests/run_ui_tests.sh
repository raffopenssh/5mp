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
test_url_param "map_sheet_link" "&panel=admin&admin_tab=map-settings&map_sheet=car" "" ""
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
# The selected map feature rides in the link as a PLACE (?tip=lng,lat), with
# ?tip_layer= only picking within the stack found there. Restoring re-asks the
# live map, so a stale link must degrade to "nothing selected", never to an
# error page.
# Every geology setting is shareable, and the panel's own Advanced disclosure is
# one of them: "look at this" should reproduce what the sender was reading, not
# just the map. Opacity is the exception that has to be TESTED as an absence —
# no parameter means "adapt to the basemap", because a value tuned over the dark
# basemap is nearly invisible over satellite imagery, so freezing the computed
# number into a link would break the layer for whoever opens it elsewhere.
test_url_param "geomap_all_settings" "&geomap=sudan,car&geomap_opacity=30&geomap_color=ink&geomap_pattern=0&geomap_lith=sandstone&geomap_adv=1" "" ""
test_url_param "geomap_auto_opacity" "&geomap=car" "" ""
test_url_param "geomap_advanced_without_layer" "&panel=admin&admin_tab=map-settings&geomap_adv=1" "" ""
test_url_param "geomap_stale_lithology" "&geomap=car&geomap_lith=nosuchrock" "" ""
test_url_param "tip_selection" "&tip=22.62154,6.61277" "" ""
test_url_param "tip_selection_layer" "&tip=22.62154,6.61277&tip_layer=geomap-fill-car" "" ""
test_url_param "tip_selection_stale" "&tip=0,0&tip_layer=lod-nope-line" "" ""
test_url_param "complex_combined" "&popup=TZA_Serengeti&sections=fire,species&panel=star&starred_parks=TZA_Serengeti&lat=-2&lng=35&z=8" "" ""
# A link carrying BOTH a viewport and something that flies somewhere (a
# country, a park popup, an AOI) must open at the viewport it names -- the
# restorers used to win that race. The DOM assertion lives in the Playwright
# spec; here we only pin that the combination is a valid page.
test_url_param "viewport_wins_over_country" "&lat=24.9331&lng=2.6151&z=6.1&country=Kenya&popup=CAF_Chinko" "" ""
test_url_param "viewport_with_animation" "&lat=6.5&lng=24.5&z=7&date_preset=90d&anim=fireGrid,trajs,deforest&anim_paused=1" "" ""

# ── Source guards: a click must not be swallowed ──────────────────────
#
# These are grep assertions, not DOM ones, because the bug they guard is a
# STRUCTURAL one and grep can see it where a screenshot cannot: something that
# covers the viewport taking an answer away instead of ranking below it. Each
# line below was a real defect (2026-08-12) in which the map still looked
# perfectly right while a whole layer had become unclickable.
yellow "=== Testing map-click arbitration (source guards) ==="

src_guard() {
    local name="$1" mode="$2" pattern="$3" file="$4"
    printf "%-50s" "$name"
    if grep -qE "$pattern" "$file"; then found=1; else found=0; fi
    if [[ "$mode" == "present" && $found -eq 1 ]] || [[ "$mode" == "absent" && $found -eq 0 ]]; then
        green "PASS"; PASSED=$((PASSED + 1))
    else
        red "FAIL ($mode: $pattern)"; FAILED=$((FAILED + 1))
    fi
}

GLOBE="srv/templates/globe.html"

# The park is a RANKED layer, not an exception. setBackdropGuard silenced every
# negative-priority tip over a park polygon, so inside a park -- most of what
# this map is for -- geology and the AOI were erased, not outranked.
src_guard "park_is_a_maptip_layer"        present "MapTip.register\('areas-fill'" "$GLOBE"
src_guard "no_backdrop_guard"             absent  "setBackdropGuard\(function" "$GLOBE"
# The ladder itself: park between feature layers (0) and the AOI (-20).
src_guard "park_priority_minus_10"        present "priority: -10" "$GLOBE"
src_guard "aoi_priority_minus_20"         present "priority: -20" "$GLOBE"
src_guard "geology_priority_minus_30"     present "priority: -30" "srv/static/geomap.js"
# A modifier click belongs to the app's multi-select, so MapTip stands down for
# the whole click -- declining per layer only let the AOI answer instead.
src_guard "maptip_defers_to_modifier"     present "shiftKey \|\| oe.metaKey" "srv/static/maptip.js"
# The popup's x is ours. MapLibre's, re-parented into a drag handle that calls
# setPointerCapture, did nothing on Safari.
src_guard "popup_close_is_ours"           present "data-act', 'close'" "srv/static/floatui.js"
src_guard "maplibre_close_not_reparented" absent  "fui-bar-btns'\).appendChild\(mlClose\)" "srv/static/floatui.js"
# A sheet that cannot be added yet is unfinished, not done (invariant 1):
# ?geomap=sudan,car queued both on one idle and the second evaporated.
src_guard "geomap_add_retries"            present "pendingAdd" "srv/static/geomap.js"

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
