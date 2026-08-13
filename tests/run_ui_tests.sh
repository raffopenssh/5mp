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
# The Map strip is a report OF those settings, so every state it can paint has
# to be a loadable link: a non-default basemap alone, a drape alone, and a
# drape that is filtered down to one commodity (the case that must not read as
# the complete rock map).
test_url_param "maplegend_basemap_only" "&basemap=satellite-esri" "" ""
test_url_param "maplegend_filtered_geology" "&geomap=car&geomap_host=car:gold&basemap=satellite-esri" "" ""
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
# The structural context lines are continental and independent of any sheet,
# so the link must restore with no geomap= at all — and a stale id must
# degrade to "not drawn", never to an error page.
test_url_param "geomap_structural" "&geomap_structural=active_faults,craton_edges" "" ""
test_url_param "geomap_structural_stale_id" "&geomap=car&geomap_structural=nosuchlayer" "" ""

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

# ── The Map strip (srv/static/maplegend.js) ───────────────────────────
#
# The strip reports the BASEMAP and the two drapes where the numbers are. Two
# properties are load-bearing and both are structural, so grep can see them:
#
#  * it must vanish in the default state (dark basemap, no overlay) rather than
#    print a "Basemap: Dark" row that is chrome, not information -- but the
#    opener has to survive, because it is the only route to the basemap and
#    overlay switches that is not four clicks deep in the admin panel;
#  * a FILTERED geology drape must say so. "Everything that can host gold" looks
#    exactly like the complete rock map, which is the same failure shape as a
#    truncated layer that does not announce its truncation.
src_guard "maplegend_quiet_state"         present "host.classList.toggle\('quiet'" "srv/static/maplegend.js"
# The opener survives the quiet state. Matched on the ASSIGNMENT, not on a
# one-line shape of it: the previous pattern spelled the whole statement
# including its braces, so the perf pass that split it over three lines turned
# a behavioural guard into a formatting guard and it failed for a fortnight
# without the behaviour ever changing.
src_guard "maplegend_opener_always"       present "host.innerHTML = opener" "srv/static/maplegend.js"
src_guard "maplegend_says_filtered"       present "geoFilterNote" "srv/static/maplegend.js"
# ── The resting strip ──────────────────────────────────────────────────────
# An untouched strip folds back to its icons (globe.css .ml-rest). Three
# things must stay true or the fold is a truncation that does not announce
# itself, which is the failure the strip exists to prevent:
#  * it FOLDS, it does not remove -- max-width/max-height, never display:none,
#    so every target keeps its accessible name and its title while folded;
#  * it says how much it folded (the swatch count on the chip), and that count
#    is DERIVED from the rendered swatches, never typed;
#  * it never rests while a menu is open or the pointer/focus is inside it.
src_guard "maplegend_rest_folds"          present "ml-rest" "srv/static/globe.css"
src_guard "maplegend_rest_not_display"    absent  "ml-rest .ml-swatches { display: none" "srv/static/globe.css"
src_guard "maplegend_rest_counts_derived" present "ml-sw:not" "srv/static/maplegend.js"
src_guard "maplegend_rest_blocked"        present "function restBlocked" "srv/static/maplegend.js"
# ── A zero must be provable ────────────────────────────────────────────────
# "the contact layer has not painted yet" and "it painted and there is nothing
# in this viewport" are two states. One flag for both froze `counting lines...`
# forever at z10.5 over one sheet. The second state is settled by the map's own
# word for "I have drawn" (idle since the layer set changed + sources loaded),
# never by a timeout.
src_guard "maplegend_zero_provable"       present "function contactsSettled" "srv/static/maplegend.js"
src_guard "maplegend_zero_not_timer"      present "idleNonce <= contactIdleAt" "srv/static/maplegend.js"
# A sheet or archive that is not installed keeps its row and says why; an
# absent row reads as "this map has no geology", a different claim.
src_guard "maplegend_refusal_kept"        present "refused" "srv/static/maplegend.js"
# Both overlay modules re-render the strip, so a share link or the admin panel
# can turn a drape on and the strip follows without a second code path.
src_guard "maplegend_hooked_geology"      present "MapLegend.refresh\(\);   // see renderHistMapPanel" "$GLOBE"
# The structural block must exist, and its skill line must be MEASURED OR THE
# WORD: a lift printed without the "unmeasured" fallback beside its absent
# case would leave a measured-looking blank (cross-cutting invariant 12).
src_guard "geo_structural_block"          present "function geoStructuralBlockHTML" "$GLOBE"
src_guard "geo_structural_unmeasured"     present ">unmeasured</span>" "$GLOBE"
# The lifts are the catalogue's (generated server-side), never typed into the
# template — a hardcoded lift would survive a re-evaluation silently.
src_guard "geo_structural_no_typed_lift"  absent  "[0-9]\.[0-9]+×" "$GLOBE"
# The structural layers are geology and live in the geology mixer (both tabs,
# via structuralFootHTML) — not only in the admin Advanced disclosure. Their
# lifts there come from the catalogue too, never typed.
src_guard "geo_structural_in_mixer"       present "function structuralFootHTML" "srv/static/maplegend.js"
src_guard "geo_structural_mixer_no_lift"  absent  "[0-9]\.[0-9]+×" "srv/static/maplegend.js"

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
