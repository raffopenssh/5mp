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

# F10 — protected_area_id is a 100 km catchment, not the park. CMR_Nki is on the
# test-park list AS THE PRISTINE ONE and /stats credited it with 2,518 fires,
# every one of them outside the boundary (docs/agents/fire.md "F10"). The
# assertion is directional rather than a literal: the count moves with each
# night's ingest, but a rainforest park cannot plausibly out-burn a savanna one.
test_api "fire_stats_nki_is_not_a_savanna" "/api/parks/CMR_Nki/stats" "200" \
    "(.fire.total_fires // 0) < 5000"
test_api "fire_stats_chinko_still_burns" "/api/parks/CAF_Chinko/stats" "200" \
    "(.fire.total_fires // 0) > 10000"

# F11 — the weekly series must carry the satellite fleet, so the client can cut
# the line where three sensors replace one instead of drawing a 3x rise. The
# flag distinguishes "fleet never changed" from "nobody measured": if
# fire_sensor_epochs is empty this is false and the chart says so.
test_api "fire_trend_names_its_fleet" "/api/parks/CAF_Chinko/fire-trend" "200" \
    ".sensor_epochs_measured == true and ([.weeks[] | select(.sensors != null)] | length) > 100"
test_api "fire_trend_fleet_changes_at_2024" "/api/parks/CAF_Chinko/fire-trend" "200" \
    "([.weeks[] | select(.week >= \"2023-12-01\" and .week < \"2024-01-01\") | .sensor_count] | max) < ([.weeks[] | select(.week >= \"2024-02-01\" and .week < \"2024-04-01\") | .sensor_count] | min)"

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
# WP1: persistence measured from GHSL back-epochs. Chinko is a converted area
# (scripts/ghsl_epochs.py ran); the sum over by_persistence must equal the
# clusters the stamp measured, and the summary must say the sentence (derived
# counts, invariant 2 -- we assert presence and internal consistency, not a
# typed number).
test_api "settlement_persistence_chinko" "/api/parks/CAF_Chinko/settlement-narrative" "200" \
    '(.by_persistence | to_entries | map(.value) | add) == .settlement_count and (.summary | contains("GHSL built-up epochs"))'
test_api "classified_settlements" "/api/parks/COD_Virunga/classified-settlements" "200" "true"
test_api "classified_deforestation" "/api/parks/COD_Virunga/classified-deforestation" "200" "true"

yellow "\n=== Grid API ==="
test_api "grid_all" "/api/grid" "200" ".type == \"FeatureCollection\""

yellow "\n=== Global Stats ==="
test_api "global_stats" "/api/stats" "200" ".total_settlements > 0"

yellow "\n=== Search ==="
test_api "search_areas" "/api/areas/search" "200" "true"
test_api "wdpa_search" "/api/wdpa/search" "200" "true"

yellow "\n=== Admin Endpoints (alpha: password grants admin access) ==="
test_api "admin_gpx_logs" "/api/admin/gpx-logs" "200"
test_api "admin_learning_results" "/api/admin/learning-results" "200"
test_api "admin_pending" "/api/admin/pending-approvals" "200"
test_api "admin_learned_features" "/api/admin/learned-features?park_id=CAF_Chinko" "200"

yellow "\n=== Notifications ==="
test_api "notifications_list" "/api/notifications" "200" ".total >= 0"
test_api "notifications_has_items" "/api/notifications" "200" ".notifications | type == \"array\""

yellow "\n=== Publications ==="
test_api "publications_virunga" "/api/parks/COD_Virunga/publications" "200" "type == \"array\""
test_api "publications_count" "/api/parks/COD_Virunga/publications/count" "200" ".count >= 0"
# Publications migrated from WDPA IDs to park IDs (076bf2c); numeric IDs are
# now rejected by the park-ID middleware (3eb899c).
test_api "publications_wdpa_rejected" "/api/parks/669/publications" "400" ""
test_api "publications_mole" "/api/parks/GHA_Mole/publications" "200" "type == \"array\""

yellow "\n=== Infrastructure ==="
test_api "infrastructure_chinko" "/api/parks/CAF_Chinko/infrastructure" "200" "true"
test_api "legal_docs_virunga" "/api/parks/COD_Virunga/legal" "200" ".count >= 0"
test_api "legal_docs_serengeti" "/api/parks/TZA_Serengeti/legal" "200" ".count >= 0"

yellow "\n=== Geography detail tiers ==="
# major <= main <= all, per layer. A tier is a stable WHERE clause, so the
# nesting is a property of the definition, not of the data -- if it ever
# inverts, a tier predicate has stopped being a subset of the looser one and
# the buttons are lying about what they draw.
test_api "detail_counts_present" "/api/parks/CAF_Chinko/infrastructure" "200" \
    ".summary.detail_counts.road.all >= 1"
for layer in river road place; do
    test_api "detail_nested_${layer}" "/api/parks/CAF_Chinko/infrastructure" "200" \
        ".summary.detail_counts.${layer} | (.major <= .main) and (.main <= .all)"
done
# The tier reaches the feature builder, not just the counter: a served layer
# must match its own advertised count.
for tier in major main all; do
    printf "%-50s" "detail_features_road_${tier}"
    want=$(curl -s -m 60 -b "$COOKIE_FILE" "${BASE_URL}/api/parks/CAF_Chinko/infrastructure" \
           | jq -r ".summary.detail_counts.road.${tier}")
    got=$(curl -s -m 60 -b "$COOKIE_FILE" \
          "${BASE_URL}/api/parks/CAF_Chinko/features?type=road&detail=${tier}&limit=5000" \
          | jq -r '.features | length')
    if [[ "$want" == "$got" && "$got" -gt 0 ]]; then
        green "✓"; PASSED=$((PASSED + 1))
    else
        red "FAIL (button says $want, layer has $got)"
        FAILED=$((FAILED + 1)); ERRORS+=("detail_features_road_${tier}")
    fi
done
# An unknown or absent tier is "all", never an error: old share links and
# pinned-layer restores predate the param entirely.
printf "%-50s" "detail_unknown_falls_back_to_all"
a=$(curl -s -m 60 -b "$COOKIE_FILE" "${BASE_URL}/api/parks/CAF_Chinko/features?type=river&detail=wat" | jq -r '.features|length')
b=$(curl -s -m 60 -b "$COOKIE_FILE" "${BASE_URL}/api/parks/CAF_Chinko/features?type=river" | jq -r '.features|length')
if [[ "$a" == "$b" && "$a" -gt 0 ]]; then
    green "✓"; PASSED=$((PASSED + 1))
else
    red "FAIL ($a vs $b)"; FAILED=$((FAILED + 1)); ERRORS+=("detail_unknown_falls_back_to_all")
fi

yellow "\n=== Animator: fire points feasibility ==="
# The animator asks BEFORE offering the "fire points" chip, so a user is never
# invited to click something the server will refuse. mode=estimate is a pure
# read over fire_grid_day (~10 ms) and must be side-effect free.
test_api "fire_frames_estimate_shape" \
    "/api/fire-frames?mode=estimate&bbox=22,4,32,11&from=2024-01-01&to=2026-08-12" "200" \
    "(.estimate | type == \"number\") and (.points_ok | type == \"boolean\") and .max > 0"
# A continental multi-year window is far past the ceiling: the refusal is the
# whole point of the endpoint, and it must carry the NUMBER so the chip's hint
# can say how much too much it is.
test_api "fire_frames_estimate_refuses_continental" \
    "/api/fire-frames?mode=estimate&bbox=22,4,32,11&from=2024-01-01&to=2026-08-12" "200" \
    ".points_ok == false and .estimate > .max"
# A small window over one park is allowed -- if this ever fails closed, the
# chip is permanently dead and the high-zoom rendering unreachable.
test_api "fire_frames_estimate_allows_small" \
    "/api/fire-frames?mode=estimate&bbox=24.2,6.4,24.6,6.8&from=2025-12-01&to=2025-12-08" "200" \
    ".points_ok == true"
# The estimate must agree with what mode=points actually does, or the chip
# refuses views that would have worked (or offers ones that fall back).
printf "%-50s" "fire_frames_estimate_matches_points"
est_ok=$(curl -s -m 60 -b "$COOKIE_FILE" \
    "${BASE_URL}/api/fire-frames?mode=estimate&bbox=24.2,6.4,24.6,6.8&from=2025-12-01&to=2025-12-08" \
    | jq -r '.points_ok')
real_mode=$(curl -s -m 60 -b "$COOKIE_FILE" \
    "${BASE_URL}/api/fire-frames?mode=points&bbox=24.2,6.4,24.6,6.8&from=2025-12-01&to=2025-12-08" \
    | jq -r '.mode')
if [[ "$est_ok" == "true" && "$real_mode" == "points" ]]; then
    green "✓"; PASSED=$((PASSED + 1))
else
    red "FAIL (estimate says $est_ok, points returned $real_mode)"
    FAILED=$((FAILED + 1)); ERRORS+=("fire_frames_estimate_matches_points")
fi

yellow "\n=== Areas of interest (AOI) ==="
# Visibility is the whole point: an AOI owned by another principal must be
# invisible AND must 404 (never 403 — an id must not be an oracle).
test_api "aoi_list_empty_for_test_pwd" "/api/aois" "200" ".count == 0"
test_api "aoi_private_is_404_not_403" "/api/aois/XSA_Study_Area" "404" ""
test_api "aoi_bad_id_rejected" "/api/aois/..%2Fetc" "400" ""

# Same checks with the owning password, if it is configured locally.
AOI_PWD=$(grep -o 'ACCESS_PASSWORDS=.*' secrets.env 2>/dev/null | tr ',' '\n' | grep -i 'chink' | head -1)
if [[ -n "$AOI_PWD" ]]; then
    printf "%-50s" "aoi_visible_to_owner"
    body=$(curl -s -m 30 --get --data-urlencode "pwd=$AOI_PWD" "${BASE_URL}/api/aois")
    if [[ "$(echo "$body" | jq -r '.count >= 1')" == "true" ]]; then
        green "✓"; PASSED=$((PASSED + 1))
    else
        red "FAIL"; FAILED=$((FAILED + 1)); ERRORS+=("aoi_visible_to_owner")
    fi
    # Response cache must not serve the owner's body to the next caller.
    printf "%-50s" "aoi_cache_not_shared_across_principals"
    curl -s -m 30 --get --data-urlencode "pwd=$AOI_PWD" "${BASE_URL}/api/aois" > /dev/null
    n=$(curl -s -m 30 -b "$COOKIE_FILE" "${BASE_URL}/api/aois" | jq -r '.count')
    if [[ "$n" == "0" ]]; then
        green "✓"; PASSED=$((PASSED + 1))
    else
        red "FAIL (leaked $n AOIs)"; FAILED=$((FAILED + 1)); ERRORS+=("aoi cache leak")
    fi
fi

# The unfiltered raw-geography endpoints (bbox feature browser, animator
# trajectories) read feature_geometries without a park_id, so AOI-owned rows
# would be served to every principal and would double-count over the parks the
# AOI overlaps. aoiExcludeSQL() keeps them out; assert it here, over the AOI's
# own bbox where a regression is guaranteed to show.
XSA_BBOX="22.7,4.25,31.3,11.0"
for ep in "features-in-bbox?type=fire_trajectory&bbox=${XSA_BBOX}" \
          "fire-anim-trajectories?bbox=${XSA_BBOX}&from=2024-01-01&to=2026-01-01"; do
    name="aoi_rows_absent_from_${ep%%\?*}"
    printf "%-50s" "$name"
    body=$(curl -s -m 60 -b "$COOKIE_FILE" "${BASE_URL}/api/${ep}")
    if echo "$body" | grep -q 'XSA_Study_Area'; then
        red "FAIL (AOI rows leaked)"; FAILED=$((FAILED + 1)); ERRORS+=("$name")
    else
        green "✓"; PASSED=$((PASSED + 1))
    fi
done

# A pinned layer is a statement about ONE area, and for an AOI that scope has
# to travel as ?area= -- ?park=<aoi> is a hard 404 from ParkIDMiddleware, which
# is exactly how the first viewport-first AOI pin came to fetch nothing and
# report "0 features in view" while every layer claimed success.
#
# The AOI half needs the owner's password, which is not in a tracked file; skip
# rather than fail on a fresh checkout (`source secrets.env` first to run it).
if [ -n "${AOI_OWNER_PWD:-}" ]; then
    printf "%-50s" "features_bbox_area_param_scopes_to_aoi"
    body=$(curl -s -m 60 "${BASE_URL}/api/features-in-bbox?type=fire_trajectory&bbox=${XSA_BBOX}&limit=50&area=XSA_Study_Area&aoi=XSA_Study_Area&pwd=${AOI_OWNER_PWD}")
    if echo "$body" | grep -q '"total":0' || [ -z "$body" ]; then
        red "FAIL (AOI pin scope returned nothing)"; FAILED=$((FAILED + 1)); ERRORS+=("features_bbox_area_param")
    else
        green "✓"; PASSED=$((PASSED + 1))
    fi
fi

# /api/stats takes the SAME ?aoi= scope the map layers do -- focus mode dims
# the map to one area, and a panel above it still reporting the continent is a
# contradiction the user has to notice. Two halves, and the second is the one
# that matters: the scope must be visibility-checked, not taken on trust, or an
# id becomes an oracle for a private polygon's totals.
if [ -n "${AOI_OWNER_PWD:-}" ]; then
    printf "%-50s" "stats_aoi_scope_narrows_for_owner"
    glob=$(curl -s -m 60 --get --data-urlencode "pwd=$AOI_OWNER_PWD" "${BASE_URL}/api/stats" | jq -r '.total_settlements')
    scoped=$(curl -s -m 60 --get --data-urlencode "pwd=$AOI_OWNER_PWD" --data-urlencode "aoi=XSA_Study_Area" "${BASE_URL}/api/stats")
    n=$(echo "$scoped" | jq -r '.total_settlements'); sc=$(echo "$scoped" | jq -r '.scope')
    if [ "$sc" = "XSA_Study_Area" ] && [ "$n" -gt 0 ] && [ "$n" -lt "$glob" ]; then
        green "✓"; PASSED=$((PASSED + 1))
    else
        red "FAIL (scope=$sc, $n of $glob)"; FAILED=$((FAILED + 1)); ERRORS+=("stats_aoi_scope")
    fi
fi

printf "%-50s" "stats_aoi_scope_ignored_for_non_owner"
body=$(curl -s -m 60 -b "$COOKIE_FILE" "${BASE_URL}/api/stats?aoi=XSA_Study_Area")
if [ "$(echo "$body" | jq -r '.scope')" = "null" ]; then green "✓"; PASSED=$((PASSED + 1))
else red "FAIL (leaked scope)"; FAILED=$((FAILED + 1)); ERRORS+=("stats_aoi_scope_leak"); fi

# ?park=<aoi id> must still 404 -- the fix is a second param, not a hole in the
# middleware. (No password needed: the middleware runs before the handler.)
printf "%-50s" "features_bbox_park_param_still_404s_aoi"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 60 -b "$COOKIE_FILE" "${BASE_URL}/api/features-in-bbox?type=fire_trajectory&bbox=${XSA_BBOX}&park=XSA_Study_Area")
if [ "$code" = "404" ]; then green "✓"; PASSED=$((PASSED + 1))
else red "FAIL (got $code)"; FAILED=$((FAILED + 1)); ERRORS+=("features_bbox_park_param_404"); fi

# The classification filter is server-side now (the cheap renderings ship no
# properties to filter on client-side). Two invariants: it really filters, and
# a type that HAS no classification serves the unfiltered superset rather than
# an empty answer -- "cannot apply" is not "excludes everything".
printf "%-50s" "features_bbox_class_filter"
all=$(curl -s -m 60 -b "$COOKIE_FILE" "${BASE_URL}/api/features-in-bbox?type=deforestation&bbox=23,5,27,9&limit=3000&area=CAF_Chinko" | grep -o '"total":[0-9]*' | head -1)
sub=$(curl -s -m 60 -b "$COOKIE_FILE" "${BASE_URL}/api/features-in-bbox?type=deforestation&bbox=23,5,27,9&limit=3000&area=CAF_Chinko&class=slash_burn" | grep -o '"total":[0-9]*' | head -1)
fire=$(curl -s -m 60 -b "$COOKIE_FILE" "${BASE_URL}/api/features-in-bbox?type=fire_trajectory&bbox=23,5,27,9&limit=3000&area=CAF_Chinko&class=slash_burn" | grep -o '"total":[0-9]*' | head -1)
fireall=$(curl -s -m 60 -b "$COOKIE_FILE" "${BASE_URL}/api/features-in-bbox?type=fire_trajectory&bbox=23,5,27,9&limit=3000&area=CAF_Chinko" | grep -o '"total":[0-9]*' | head -1)
av=${all#*:}; sv=${sub#*:}; fv=${fire#*:}; fav=${fireall#*:}
if [ "${sv:-0}" -gt 0 ] && [ "${sv:-0}" -lt "${av:-0}" ] && [ "${fv:-0}" = "${fav:-1}" ]; then
    green "✓"; PASSED=$((PASSED + 1))
else
    red "FAIL (all=$av filtered=$sv fire=$fv/$fav)"; FAILED=$((FAILED + 1)); ERRORS+=("features_bbox_class_filter")
fi

# The cheap tier for a PATH is a shorter path. seg=1 must return one chord per
# feature, parallel to points/ids -- a fire front collapsed to a centroid loses
# the only property that distinguishes it from a hotspot.
printf "%-50s" "features_bbox_seg_returns_chords"
body=$(curl -s -m 60 -b "$COOKIE_FILE" "${BASE_URL}/api/features-in-bbox?type=fire_trajectory&bbox=23,5,27,9&mode=points&seg=1&limit=50&area=CAF_Chinko")
if echo "$body" | grep -q '"render":"segments"' && echo "$body" | grep -q '"segs":'; then
    green "✓"; PASSED=$((PASSED + 1))
else
    red "FAIL (no segments)"; FAILED=$((FAILED + 1)); ERRORS+=("features_bbox_seg")
fi

# A GeoPackage peek must be a LOOKUP, not a build. ?aoi_menu_item=gpkg on a
# share link asks "is this file already there?" so it can download instead of
# making the recipient click -- if asking created a job, opening a shared link
# would kick off a multi-minute export of an area you were only shown.
# 404 = nothing built for this window; and the job count must not move.
printf "%-50s" "gpkg_peek_is_side_effect_free"
GPKG_BEFORE=$(sqlite3 db.sqlite3 "SELECT COUNT(*) FROM geopackage_jobs" 2>/dev/null || echo x)
GPKG_CODE=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_FILE" \
    "${BASE_URL}/api/parks/CAF_Chinko/export.gpkg?peek=1&from=1999-01-01&to=1999-01-02")
GPKG_AFTER=$(sqlite3 db.sqlite3 "SELECT COUNT(*) FROM geopackage_jobs" 2>/dev/null || echo y)
if [[ "$GPKG_CODE" == "404" && "$GPKG_BEFORE" == "$GPKG_AFTER" ]]; then
    green "✓"; PASSED=$((PASSED + 1))
else
    red "FAIL (code $GPKG_CODE, jobs $GPKG_BEFORE -> $GPKG_AFTER)"
    FAILED=$((FAILED + 1)); ERRORS+=("gpkg peek side effect")
fi

# The download menu's direct-URL entries carry NO `download="..."` attribute --
# Safari's "Copy Link" copies that attribute instead of the href, which is how a
# shareable download link turned into the bare string "XSA_Study_Area.kml". The
# filename therefore has to come from the server, date window included, or two
# different windows land in a downloads folder under one name.
printf "%-50s" "kml_filename_from_content_disposition"
KML_CD=$(curl -sI -b "$COOKIE_FILE" \
    "${BASE_URL}/api/parks/CAF_Chinko/export.kml?effort=0&from=2024-01-01&to=2024-06-30" \
    | tr -d '\r' | grep -i '^content-disposition:')
if [[ "$KML_CD" == *'filename="CAF_Chinko_2024-01-01_to_2024-06-30.kml"'* ]]; then
    green "✓"; PASSED=$((PASSED + 1))
else
    red "FAIL ($KML_CD)"; FAILED=$((FAILED + 1)); ERRORS+=("kml content-disposition")
fi

printf "%-50s" "export_links_have_no_download_attr"
if grep -q 'aoi-menu-item[^>]*download=' srv/templates/globe.html; then
    red "FAIL (a download= attribute is back in the export menu)"
    FAILED=$((FAILED + 1)); ERRORS+=("export menu download attr")
else
    green "✓"; PASSED=$((PASSED + 1))
fi

# The geology GeoPackage is built on first request and cached beside the units
# it came from. It is ONE file covering every sheet -- the map is one layer, so
# the data behind it is not a per-country jigsaw -- and three things must hold
# or it is the wrong download: it has to be a real GeoPackage (SQLite with the
# GPKG application_id), it must carry a w_<commodity> column (that column set is
# the reason it exists rather than the MBTiles, and it is derived per build, so
# a vectorizer change dropping every affinity would otherwise ship silently as a
# valid-but-useless file), and it must contain EVERY sheet the catalogue says it
# does. A package one country short renders as a country with no geology, which
# is indistinguishable from "no data here".
printf "%-50s" "geomap_geopackage_typed_and_filterable"
GEO_TMP=$(mktemp /tmp/geomapXXXX.gpkg)
GEO_CODE=$(curl -s -m 300 -L -o "$GEO_TMP" -w "%{http_code}" -b "$COOKIE_FILE" \
    "${BASE_URL}/api/geomap/geopackage")
GEO_APP=$(sqlite3 "$GEO_TMP" "PRAGMA application_id" 2>/dev/null || echo 0)
GEO_GOLD=$(sqlite3 "$GEO_TMP" 'SELECT COUNT(*) FROM geology_units WHERE "w_gold" IS NOT NULL' 2>/dev/null || echo 0)
GEO_STYLE=$(sqlite3 "$GEO_TMP" "SELECT COUNT(*) FROM layer_styles WHERE useAsDefault=1" 2>/dev/null || echo 0)
GEO_PROJ=$(sqlite3 "$GEO_TMP" "SELECT COUNT(*) FROM qgis_projects" 2>/dev/null || echo 0)
GEO_SHEETS=$(sqlite3 "$GEO_TMP" "SELECT COUNT(DISTINCT sheet) FROM geology_units" 2>/dev/null || echo 0)
# Derived from the catalogue, never typed here: a server with one sheet built
# must pass, and a third sheet added later must be checked without an edit.
GEO_WANT=$(curl -s -b "$COOKIE_FILE" "${BASE_URL}/api/geomap" \
    | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("geopackage_sheets") or []))' 2>/dev/null || echo 0)
rm -f "$GEO_TMP"
if [[ "$GEO_CODE" == "200" && "$GEO_APP" == "1196444487" && "$GEO_GOLD" -gt 0 \
      && "$GEO_STYLE" -gt 0 && "$GEO_PROJ" -gt 0 \
      && "$GEO_WANT" -gt 0 && "$GEO_SHEETS" == "$GEO_WANT" ]]; then
    green "✓"; PASSED=$((PASSED + 1))
else
    red "FAIL (code $GEO_CODE, app_id $GEO_APP, gold units $GEO_GOLD, styles $GEO_STYLE, projects $GEO_PROJ, sheets $GEO_SHEETS/$GEO_WANT)"
    FAILED=$((FAILED + 1)); ERRORS+=("geomap geopackage")
fi

# The per-sheet path is in shipped links, in the handover doc and in
# render_gpkg.py. It must REDIRECT to the combined file, not 404: a 404 there
# reads as "the export was removed", which is not what happened.
printf "%-50s" "geomap_geopackage_legacy_sheet_path"
GEO_LOC=$(curl -s -o /dev/null -w "%{http_code} %{redirect_url}" -b "$COOKIE_FILE" \
    "${BASE_URL}/api/geomap/car/geopackage")
if [[ "$GEO_LOC" == 308*"/api/geomap/geopackage"* ]]; then
    green "✓"; PASSED=$((PASSED + 1))
else
    red "FAIL ($GEO_LOC)"
    FAILED=$((FAILED + 1)); ERRORS+=("geomap geopackage legacy path")
fi

# Structural context (JRC AKP faults + craton margins): the catalogue's own
# count must equal what the layer endpoint serves — a truncated layer is
# indistinguishable from a complete one, so the count is derived twice from
# the server itself and compared, never typed here. The skill block, when
# present, must carry lifts (generated numbers) and a scope naming the truth
# set; a layer without one must be exactly the word the UI prints: unmeasured.
printf "%-50s" "geomap_structural_served_whole"
STRUCT_OK="ok"
STRUCT_META=$(curl -s -b "$COOKIE_FILE" "${BASE_URL}/api/geomap" | python3 -c '
import json, sys
s = (json.load(sys.stdin).get("structural") or {})
for lid, e in s.items():
    if e.get("available"):
        sk = e.get("skill")
        skill_ok = 1 if (sk is None or (sk.get("lifts") and sk.get("scope"))) else 0
        print(lid, e["n"], e["url"], skill_ok)
    elif not e.get("reason"):
        print(lid, -1, "-", 0)' 2>/dev/null)
if [[ -z "$STRUCT_META" ]]; then STRUCT_OK="no structural block in catalogue"; fi
while read -r SL_ID SL_N SL_URL SL_SKILL; do
    [[ -z "$SL_ID" ]] && continue
    if [[ "$SL_N" == "-1" ]]; then STRUCT_OK="$SL_ID unavailable with no reason"; break; fi
    if [[ "$SL_SKILL" != "1" ]]; then STRUCT_OK="$SL_ID skill block without lifts or scope"; break; fi
    SL_GOT=$(curl -s --compressed -b "$COOKIE_FILE" "${BASE_URL}${SL_URL}" | python3 -c '
import json, sys
d = json.load(sys.stdin)
miss = [k for k in ("source","citation","terms","accessed") if not d.get(k)]
print(len(d.get("features") or []), ",".join(miss) or "-")' 2>/dev/null)
    if [[ "$SL_GOT" != "$SL_N -" ]]; then STRUCT_OK="$SL_ID: catalogue says $SL_N, endpoint served '$SL_GOT'"; break; fi
done <<< "$STRUCT_META"
if [[ "$STRUCT_OK" == "ok" ]]; then
    green "✓"; PASSED=$((PASSED + 1))
else
    red "FAIL ($STRUCT_OK)"
    FAILED=$((FAILED + 1)); ERRORS+=("geomap structural")
fi
if [[ "$STRUCT_OK" == "ok" ]]; then
    green "✓"; PASSED=$((PASSED + 1))
else
    red "FAIL ($STRUCT_OK)"
    FAILED=$((FAILED + 1)); ERRORS+=("geomap structural")
fi

yellow "\n=== Patrol data tenants ==="
# Patrol effort belongs to the account it was uploaded in (srv/tenant.go).
# These assertions are the boundary: a client password sees the pixels, every
# other password sees none -- and no cache may carry one answer to the other.
# Derived from PASSWORD_ENVS, never from a hardcoded prefix: the mapping is the
# definition of who owns the pixels, and a guessed pattern silently skips the
# whole block when a client is renamed.
CLIENT_PWD=$(grep -o '^PASSWORD_ENVS=.*' secrets.env 2>/dev/null | cut -d= -f2 \
    | tr ',' '\n' | grep ':prod$' | head -1 | cut -d: -f1)
if [[ -n "$CLIENT_PWD" ]]; then
    TZ_BBOX="28,-6,40,4"
    printf "%-50s" "patrol_pixels_visible_to_owning_tenant"
    n=$(curl -s -m 60 --get --data-urlencode "pwd=$CLIENT_PWD" \
        --data "bbox=$TZ_BBOX" "${BASE_URL}/api/grid" | jq -r '.features | length')
    if [[ "$n" -gt 0 ]]; then
        green "✓ ($n)"; PASSED=$((PASSED + 1))
    else
        red "FAIL (owner sees no pixels)"; FAILED=$((FAILED + 1)); ERRORS+=("patrol pixels missing for owner")
    fi

    # Every other password. Note the assertion is "does not see THESE pixels",
    # not "sees nothing": the sandbox has its own uploads, and a tenant with
    # its own patrols is the normal case. So compare over the client's bbox,
    # where anything at all is a leak, and check the animator too -- a fix that
    # only covers the map layer is not a fix.
    CLIENT_KM=$(curl -s -m 30 --get --data-urlencode "pwd=$CLIENT_PWD" \
        "${BASE_URL}/api/stats" | jq -r '.total_distance_km')
    for other in "test2026" "$AOI_PWD"; do
        [[ -z "$other" ]] && continue
        printf "%-50s" "patrol_pixels_hidden_from_${other:0:4}"
        n=$(curl -s -m 60 --get --data-urlencode "pwd=$other" \
            --data "bbox=$TZ_BBOX" "${BASE_URL}/api/grid" | jq -r '.features | length')
        km=$(curl -s -m 30 --get --data-urlencode "pwd=$other" "${BASE_URL}/api/stats" | jq -r '.total_distance_km')
        f=$(curl -s -m 60 --get --data-urlencode "pwd=$other" \
            --data "layer=effort&bbox=$TZ_BBOX&from=2025-01-01&to=2026-08-01&step=month" \
            "${BASE_URL}/api/fire-frames" | jq -r '[.frames[]?.p[]?] | length')
        if [[ "$n" == "0" && "$f" == "0" && "$km" != "$CLIENT_KM" ]]; then
            green "✓"; PASSED=$((PASSED + 1))
        else
            red "FAIL (grid $n, anim $f, km $km vs $CLIENT_KM)"; FAILED=$((FAILED + 1))
            ERRORS+=("patrol data leaked to $other")
        fi
    done

    # An authenticated body must never be cacheable by a shared cache or by a
    # browser's URL-keyed HTTP cache: that served the previous account's pixels
    # after switching password in one browser (2026-08-10).
    printf "%-50s" "authenticated_responses_are_private"
    hdrs=$(curl -s -m 30 -D- -o /dev/null --get --data-urlencode "pwd=$CLIENT_PWD" \
        --data "bbox=$TZ_BBOX" "${BASE_URL}/api/grid")
    if ! grep -qi 'cache-control:.*public' <<< "$hdrs" && grep -qi '^vary:.*cookie' <<< "$hdrs"; then
        green "✓"; PASSED=$((PASSED + 1))
    else
        red "FAIL"; FAILED=$((FAILED + 1)); ERRORS+=("authenticated response is publicly cacheable")
    fi

    # ARRIVING WITH A DIFFERENT ?pwd= MUST SWITCH THE SESSION, NOT HALF OF IT.
    #
    # The cookie branch used to strip the param and serve on with the OLD
    # cookie, while RequestPwd/RequestEnv prefer the param — so one page load
    # was two identities: a shell rendered as the new login, filled by XHRs
    # that were still the old one (a colleague's link showed your own tenant's
    # links, AOIs and patrol data). Asserted on the cookie the redirect sets
    # AND on what a following cookie-only request actually answers, because the
    # first alone would pass while the session still disagreed.
    printf "%-50s" "pwd_in_url_switches_the_session"
    SJ=$(mktemp)
    # The discriminator is the tenant's OWN autofetch list (one source for the
    # owner, none for anybody else), not a link count: a count that happens to
    # be 0 for both logins would pass while the session never switched.
    curl -s -m 30 -o /dev/null -c "$SJ" --get --data-urlencode "pwd=test2026" "${BASE_URL}/"
    before=$(curl -s -m 30 -b "$SJ" "${BASE_URL}/api/admin/autofetch" | jq -r '.sources | length // 0')
    setc=$(curl -s -m 30 -D- -o /dev/null -b "$SJ" -c "$SJ" \
        --get --data-urlencode "pwd=$CLIENT_PWD" "${BASE_URL}/" \
        | grep -ci '^set-cookie:.*access_pwd' || true)
    after=$(curl -s -m 30 -b "$SJ" "${BASE_URL}/api/admin/autofetch" | jq -r '.sources | length // 0')
    # And the same password twice must NOT re-issue the cookie — it is the same
    # session, and a Set-Cookie on every page view is a session that resets.
    again=$(curl -s -m 30 -D- -o /dev/null -b "$SJ" \
        --get --data-urlencode "pwd=$CLIENT_PWD" "${BASE_URL}/" \
        | grep -ci '^set-cookie:.*access_pwd' || true)
    rm -f "$SJ"
    if [[ "$setc" == "1" && "$again" == "0" && "$before" == "0" && "$after" -ge 1 ]]; then
        green "✓ (switched, not re-set)"; PASSED=$((PASSED + 1))
    else
        red "FAIL (set=$setc again=$again sources $before -> $after)"; FAILED=$((FAILED + 1))
        ERRORS+=("a ?pwd= for another login does not switch the session")
    fi

    # An EarthRanger subscription names a client's server, username and parks.
    printf "%-50s" "autofetch_sources_scoped_to_tenant"
    a=$(curl -s -m 30 --get --data-urlencode "pwd=$CLIENT_PWD" "${BASE_URL}/api/admin/autofetch" | jq -r '.sources | length')
    b=$(curl -s -m 30 --get --data-urlencode "pwd=test2026" "${BASE_URL}/api/admin/autofetch" | jq -r '.sources | length // 0')
    if [[ "$b" == "0" || "$b" == "null" ]]; then
        green "✓ (owner $a, other $b)"; PASSED=$((PASSED + 1))
    else
        red "FAIL (leaked $b sources)"; FAILED=$((FAILED + 1)); ERRORS+=("autofetch sources leaked")
    fi
fi

# ── Historical map labels: categories and vector downloads ────────────────
#
# The OCR'd label set (scripts/histmaps/ocr_labels.py + categorize_labels.py)
# and its batch exports (export_labels.sh). Skips cleanly when the archive is
# not installed on this server -- the endpoints answer 404 with a reason then.
hist_avail=$(curl -s -m 30 -b "$COOKIE_FILE" "${BASE_URL}/api/histmap" | jq -r '.available // false')
if [[ "$hist_avail" == "true" ]]; then
    test_api "histmap_labels_category_field" "/api/histmap/sudan250k/labels?lon=25.78&lat=9.81&radius_km=10&limit=5" "200" \
        '.labels | length > 0 and all(.category != null and .category != "")'
    test_api "histmap_labels_category_filter" "/api/histmap/sudan250k/labels?bbox=22,3,32,12&category=place&limit=5" "200" \
        '.labels | length > 0 and all(.category == "place")'
    # Downloads advertised in meta must name the FILE's row count (dedup),
    # not the raw table's -- two surfaces, one number (AGENTS invariant 7).
    test_api "histmap_labels_downloads_advertised" "/api/histmap" "200" \
        '.labels_downloads.gpkg.count > 0 and .labels_downloads.gpkg.count == .labels_downloads.geojson.count and .labels_downloads.kml.count == .labels_downloads.gpkg.count'
    printf "%-50s" "histmap_labels_download_range"
    st=$(curl -s -m 30 -b "$COOKIE_FILE" -o /dev/null -w "%{http_code}" -r 0-99 "${BASE_URL}/api/histmap/sudan250k/labels/download/gpkg")
    kst=$(curl -s -m 30 -b "$COOKIE_FILE" -o /dev/null -w "%{http_code}" -r 0-99 "${BASE_URL}/api/histmap/sudan250k/labels/download/kml")
    bad=$(curl -s -m 30 -b "$COOKIE_FILE" -o /dev/null -w "%{http_code}" "${BASE_URL}/api/histmap/sudan250k/labels/download/shapefile")
    if [[ "$st" == "206" && "$kst" == "206" && "$bad" == "400" ]]; then
        green "✓ (gpkg 206, kml 206, bad format 400)"; PASSED=$((PASSED + 1))
    else
        red "FAIL (gpkg $st, kml $kst, shapefile $bad)"; FAILED=$((FAILED + 1)); ERRORS+=("histmap labels download")
    fi
else
    yellow "histmap not installed here -- label tests skipped"
fi

# ── Shared links: names, capabilities, and what a capability may see ───────
#
# The four properties that make a guest link safe to send (docs/agents/
# sharing.md). Each has been a real bug in something, somewhere: a capability
# that could write, one that could mint more of itself, one that never expired,
# and one that silently carried more data than the sender was looking at.
if [[ -n "$CLIENT_PWD" ]]; then
    # Every slug minted here is destroyed at the end of the block. A test that
    # leaves live capabilities behind manufactures the exact thing this
    # feature exists to keep countable -- after a dozen runs the admin sheet
    # is a wall of keys nobody issued and nobody dares revoke.
    #
    # The ledger is a FILE, not a bash array: mint() is always called inside
    # $(...), which runs in a subshell, and an array appended to there is
    # discarded when the subshell exits. That silently "cleaned up" one slug of
    # eleven and reported success -- a no-op reading as an answer, which is the
    # recurring bug this repo warns about (AGENTS.md invariant 1).
    MINTED_LOG=$(mktemp)
    mint() {  # mint <json-body> -> slug
        local sl
        sl=$(curl -s -m 30 -X POST "${BASE_URL}/api/shortlink?pwd=${CLIENT_PWD}" \
            -H 'Content-Type: application/json' -d "$1" | jq -r '.slug // empty')
        [[ -n "$sl" ]] && printf '%s\n' "$sl" >> "$MINTED_LOG"
        printf '%s' "$sl"
    }
    scope_of() {
        local j
        j=$(curl -s -m 30 -X POST "${BASE_URL}/api/shortlink?pwd=${CLIENT_PWD}" \
            -H 'Content-Type: application/json' -d "$1")
        local sl; sl=$(jq -r '.slug // empty' <<< "$j")
        [[ -n "$sl" ]] && printf '%s\n' "$sl" >> "$MINTED_LOG"
        jq -r '.scope // ""' <<< "$j"
    }

    printf "%-50s" "guest_link_reads_without_a_password"
    G=$(mint '{"url":"/?layers=pixels,fires","guest":true}')
    JAR=$(mktemp); curl -s -m 30 -o /dev/null -c "$JAR" "${BASE_URL}/s/${G}"
    code=$(curl -s -m 60 -o /dev/null -w "%{http_code}" -b "$JAR" \
        --get --data "bbox=$TZ_BBOX" "${BASE_URL}/api/grid")
    if [[ "$code" == "200" ]]; then green "✓"; PASSED=$((PASSED + 1))
    else red "FAIL ($code)"; FAILED=$((FAILED + 1)); ERRORS+=("guest link cannot read"); fi

    printf "%-50s" "guest_is_refused_writes_and_admin"
    w=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -b "$JAR" -X POST "${BASE_URL}/api/aois")
    a=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -b "$JAR" "${BASE_URL}/api/admin/access")
    m=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -b "$JAR" -X POST "${BASE_URL}/api/shortlink" \
        -H 'Content-Type: application/json' -d '{"url":"/","guest":true}')
    if [[ "$w" != "200" && "$a" != "200" && "$m" != "200" ]]; then
        green "✓ (write $w, admin $a, mint $m)"; PASSED=$((PASSED + 1))
    else
        red "FAIL (write $w, admin $a, mint $m)"; FAILED=$((FAILED + 1))
        ERRORS+=("guest capability is not read-only")
    fi

    # EXPORTS ARE READS WEARING POSTs (srv/guest.go guestMayRead). A shared
    # link shows a download menu; every entry it shows must work, and every
    # write next to them must not: the builders (POST export.gpkg, view and
    # geology) pass, while DELETE on a job and the MBTiles builder (an
    # external write to the owner's Zenodo account) stay refused. peek=1 is
    # used so the test never spools a real file (404 = allowed through the
    # gate, nothing cached — the same contract gpkg_peek relies on).
    printf "%-50s" "guest_may_build_exports_but_not_delete"
    pk=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -b "$JAR" -X POST \
        "${BASE_URL}/api/parks/COD_Virunga/export.gpkg?peek=1&raw=0")
    vw=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -b "$JAR" -X POST \
        "${BASE_URL}/api/view/export.gpkg?peek=1&bbox=29,-2,30,-1")
    ge=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -b "$JAR" -X POST \
        -H 'Content-Type: application/json' -d '{}' "${BASE_URL}/api/geomap/geopackage")
    de=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -b "$JAR" -X DELETE \
        "${BASE_URL}/api/geopackage/nonexistent")
    mb=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -b "$JAR" -X POST \
        "${BASE_URL}/api/parks/COD_Virunga/mbtiles")
    # Allowed ≠ 200 here: peek answers 404-or-200 and the geology POST 400s
    # on an empty body. What must never come back is the gate's own 401.
    if [[ "$pk" != "401" && "$vw" != "401" && "$ge" != "401" \
          && "$de" == "401" && "$mb" == "401" ]]; then
        green "✓ (gpkg $pk, view $vw, geo $ge; del $de, tiles $mb)"; PASSED=$((PASSED + 1))
    else
        red "FAIL (gpkg $pk, view $vw, geo $ge, del $de, tiles $mb)"; FAILED=$((FAILED + 1))
        ERRORS+=("guest export gate wrong (allow builders, refuse deletes/mbtiles)")
    fi

    # THE POINT OF THE SCOPE. Patrol pixels are ranger movement, not public
    # geography: a link made from a view that was not showing them must not
    # carry them, or sharing a fire scar quietly ships the patrol history too.
    printf "%-50s" "guest_scope_withholds_patrol_pixels"
    G2=$(mint '{"url":"/?layers=fires","guest":true}')
    JAR2=$(mktemp); curl -s -m 30 -o /dev/null -c "$JAR2" "${BASE_URL}/s/${G2}"
    with=$(curl -s -m 60 -b "$JAR" --get --data "bbox=$TZ_BBOX" "${BASE_URL}/api/grid" | jq -r '.features | length')
    without=$(curl -s -m 60 -b "$JAR2" --get --data "bbox=$TZ_BBOX" "${BASE_URL}/api/grid" | jq -r '.features | length')
    # Both halves asserted: "0 and 0" would pass a one-sided check while
    # meaning the feature is simply broken (AGENTS.md invariant 1).
    if [[ "$with" -gt 0 && "$without" -eq 0 ]]; then
        green "✓ (with $with, without $without)"; PASSED=$((PASSED + 1))
    else
        red "FAIL (with $with, without $without)"; FAILED=$((FAILED + 1))
        ERRORS+=("guest scope does not gate patrol pixels")
    fi

    printf "%-50s" "guest_scope_cannot_be_widened_by_asking"
    s1=$(scope_of '{"url":"/?layers=fires","guest":true,"patrol":false}')
    s2=$(scope_of '{"url":"/?layers=fires","guest":true,"patrol":true}')
    # An owner MAY add patrol to a link (they can see it); the check that
    # matters is that a *guest* cannot -- and it is refused a POST outright.
    if [[ "$s1" != *patrol* && "$s2" == *patrol* ]]; then
        green "✓ (off='$s1' on='$s2')"; PASSED=$((PASSED + 1))
    else
        red "FAIL (off='$s1' on='$s2')"; FAILED=$((FAILED + 1))
        ERRORS+=("explicit patrol scope not honoured")
    fi

    printf "%-50s" "guest_link_expires_and_is_revocable"
    expjson=$(curl -s -m 30 -X POST "${BASE_URL}/api/shortlink?pwd=${CLIENT_PWD}" \
        -H 'Content-Type: application/json' -d '{"url":"/?t=exp","guest":true,"days":7}')
    exp=$(jq -r '.expires_at // ""' <<< "$expjson")
    expslug=$(jq -r '.slug // empty' <<< "$expjson")
    [[ -n "$expslug" ]] && printf '%s\n' "$expslug" >> "$MINTED_LOG"
    curl -s -m 30 -o /dev/null -X DELETE "${BASE_URL}/api/shortlink/${G2}?pwd=${CLIENT_PWD}"
    gone=$(curl -s -m 30 -o /dev/null -w "%{http_code}" "${BASE_URL}/s/${G2}")
    if [[ -n "$exp" && "$gone" == "404" ]]; then
        green "✓ (expires $exp, revoked $gone)"; PASSED=$((PASSED + 1))
    else
        red "FAIL (expires '$exp', revoked $gone)"; FAILED=$((FAILED + 1))
        ERRORS+=("guest link does not expire or cannot be revoked")
    fi

    # THE DATE LOCK. A frozen URL says what a key OPENS at; it says nothing
    # about what the holder does next, and dragging the time slider is the
    # first thing anyone does. Three things have to hold together, and each
    # one alone would pass while the feature is broken:
    #   * an ordinary key is NOT confined (or the lock is meaningless);
    #   * a locked key cannot reach outside its window;
    #   * it CLAMPS rather than refusing -- an error would be both a worse
    #     experience and a worse secret ("403 for June" says June exists).
    printf "%-50s" "guest_date_lock_confines_the_window"
    LJ=$(curl -s -m 30 -X POST "${BASE_URL}/api/shortlink?pwd=${CLIENT_PWD}" \
        -H 'Content-Type: application/json' \
        -d '{"url":"/?from=2026-05-01&to=2026-06-30","guest":true,"lock_dates":true}')
    L=$(jq -r '.slug // empty' <<< "$LJ")
    [[ -n "$L" ]] && printf '%s\n' "$L" >> "$MINTED_LOG"
    U=$(mint '{"url":"/?from=2026-05-01&to=2026-06-30","guest":true}')
    JAR3=$(mktemp); curl -s -m 30 -o /dev/null -c "$JAR3" "${BASE_URL}/s/${L}"
    JAR4=$(mktemp); curl -s -m 30 -o /dev/null -c "$JAR4" "${BASE_URL}/s/${U}"
    wide="from=2020-01-01&to=2026-12-31"
    trend() { curl -s -m 60 -b "$1" --get --data "$wide" \
        "${BASE_URL}/api/parks/COD_Virunga/fire-trend" | jq -r '[.weeks[].week] | min // ""'; }
    locked_min=$(trend "$JAR3"); open_min=$(trend "$JAR4")
    lcode=$(curl -s -m 60 -o /dev/null -w "%{http_code}" -b "$JAR3" --get --data "$wide" \
        "${BASE_URL}/api/parks/COD_Virunga/fire-trend")
    stored=$(jq -r '.date_from + ".." + .date_to' <<< "$LJ")
    # The locked key must start inside its window, the unlocked one before it
    # (proving the window is what did the narrowing and not an empty table),
    # and the narrowed request must still be a 200.
    if [[ "$locked_min" > "2026-04-25" && -n "$open_min" && "$open_min" < "2026-04-25" \
          && "$lcode" == "200" && "$stored" == "2026-05-01..2026-06-30" ]]; then
        green "✓ (locked from $locked_min, open from $open_min)"; PASSED=$((PASSED + 1))
    else
        red "FAIL (locked '$locked_min', open '$open_min', code $lcode, stored '$stored')"
        FAILED=$((FAILED + 1)); ERRORS+=("guest date lock does not confine the window")
    fi

    # A LOCK THAT LOCKS NOTHING IS THE WORST OUTCOME ON OFFER: the sender is
    # told the key is confined, the key is not, and nothing records the
    # difference. So a view with no dates is refused outright rather than
    # stored with an empty window (which means "unrestricted").
    printf "%-50s" "date_lock_refused_when_view_has_no_dates"
    nj=$(curl -s -m 30 -X POST "${BASE_URL}/api/shortlink?pwd=${CLIENT_PWD}" \
        -H 'Content-Type: application/json' -d '{"url":"/?z=4","guest":true,"lock_dates":true}')
    nslug=$(jq -r '.slug // empty' <<< "$nj")
    [[ -n "$nslug" ]] && printf '%s\n' "$nslug" >> "$MINTED_LOG"
    # A named link is never confined either: the recipient signs in, and a
    # signed-in session is not restricted by anything in this table.
    namedlock=$(curl -s -m 30 -X POST "${BASE_URL}/api/shortlink?pwd=${CLIENT_PWD}" \
        -H 'Content-Type: application/json' \
        -d '{"url":"/?from=2026-05-01&to=2026-06-30&nl=1","lock_dates":true}')
    nlslug=$(jq -r '.slug // empty' <<< "$namedlock")
    [[ -n "$nlslug" ]] && printf '%s\n' "$nlslug" >> "$MINTED_LOG"
    nlwin=$(jq -r '(.date_from // "") + (.date_to // "")' <<< "$namedlock")
    if [[ -z "$nslug" && -n "$nlslug" && -z "$nlwin" ]]; then
        green "✓"; PASSED=$((PASSED + 1))
    else
        red "FAIL (guest slug '$nslug', named window '$nlwin')"; FAILED=$((FAILED + 1))
        ERRORS+=("date lock accepted where it cannot be enforced")
    fi
    rm -f "$JAR3" "$JAR4"

    # A NAME is not a credential: it resolves behind the ordinary gate, so a
    # stranger holding one still gets the password form.
    printf "%-50s" "named_link_is_not_a_way_in"
    N=$(mint '{"url":"/?layers=pixels"}')
    code=$(curl -s -m 30 -o /dev/null -w "%{http_code}" "${BASE_URL}/s/${N}")
    body=$(curl -s -m 30 -L "${BASE_URL}/s/${N}" | head -c 4000)
    if [[ "$code" == "302" ]] && ! grep -q 'IS_GUEST = true' <<< "$body"; then
        green "✓"; PASSED=$((PASSED + 1))
    else
        red "FAIL ($code)"; FAILED=$((FAILED + 1)); ERRORS+=("named link authenticates by itself")
    fi
    rm -f "$JAR" "$JAR2"

    # Tags: set on one link, renamed everywhere, removable. A tag is one name
    # for one purpose — the whole-group rename exists because renaming one row
    # would fork the group out of the next "renew all".
    printf "%-50s" "tag_set_rename_everywhere_remove"
    T1=$(mint '{"url":"/?park_focus=CAF_Chinko&tagtest=1","tag":"apitest-tag"}')
    T2=$(mint '{"url":"/?park_focus=CMR_Nki&tagtest=2","tag":"apitest-tag"}')
    ren=$(curl -s -m 30 -X POST "${BASE_URL}/api/shortlinks/retag?pwd=${CLIENT_PWD}" \
        -H 'Content-Type: application/json' -d '{"tag":"apitest-tag","new_tag":"apitest-tag2"}' | jq -r '.renamed')
    one=$(curl -s -m 30 -X POST "${BASE_URL}/api/shortlink/${T1}/retag?pwd=${CLIENT_PWD}" \
        -H 'Content-Type: application/json' -d '{"tag":""}' | jq -r '.tag')
    vocab=$(curl -s -m 30 "${BASE_URL}/api/shortlink-tags?pwd=${CLIENT_PWD}" | jq -r '.tags | index("apitest-tag2") != null')
    if [[ "$ren" == "2" && "$one" == "" && "$vocab" == "true" ]]; then
        green "✓"; PASSED=$((PASSED + 1))
    else
        red "FAIL (renamed=$ren cleared='$one' vocab=$vocab)"; FAILED=$((FAILED + 1))
        ERRORS+=("shortlink tag lifecycle broken")
    fi

    # A LINK CARRIES A SET OF TAGS. The single-tag column (migration 060) made
    # the two truthful answers exclusive: a link cited by a report AND handed
    # out at a workshop had to pick, and picking took it out of the next
    # "renew #report" — the accident tags exist to prevent. Asserted on all
    # four verbs, because each one used to be the whole feature.
    printf "%-50s" "tags_are_a_set_not_one_word"
    TM=$(mint '{"url":"/?park_focus=CAF_Chinko&tagset=1","tags":["apitest-b","apitest-a"]}')
    # Sorted on read: two surfaces showing one link must order its tags one way.
    both=$(curl -s -m 30 "${BASE_URL}/api/shortlinks?pwd=${CLIENT_PWD}" \
        | jq -c --arg s "$TM" '[.groups[].links[] | select(.slug==$s) | .tags][0]')
    added=$(curl -s -m 30 -X POST "${BASE_URL}/api/shortlink/${TM}/retag?pwd=${CLIENT_PWD}" \
        -H 'Content-Type: application/json' -d '{"add":"apitest-c"}' | jq -c '.tags')
    # remove takes ONE tag off and leaves the rest — the chip's ×.
    dropped=$(curl -s -m 30 -X POST "${BASE_URL}/api/shortlink/${TM}/retag?pwd=${CLIENT_PWD}" \
        -H 'Content-Type: application/json' -d '{"remove":"apitest-a"}' | jq -c '.tags')
    # A word sanitising to nothing is an ERROR, not a silent no-op: accepting it
    # would store nothing while the user believes a tag was set (invariant 1).
    badcode=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -X POST \
        "${BASE_URL}/api/shortlink/${TM}/retag?pwd=${CLIENT_PWD}" \
        -H 'Content-Type: application/json' -d '{"add":"!!!"}')
    if [[ "$both" == '["apitest-a","apitest-b"]' && "$added" == '["apitest-a","apitest-b","apitest-c"]' \
       && "$dropped" == '["apitest-b","apitest-c"]' && "$badcode" == "400" ]]; then
        green "✓ (set, add, remove, 400)"; PASSED=$((PASSED + 1))
    else
        red "FAIL (both=$both added=$added dropped=$dropped bad=$badcode)"; FAILED=$((FAILED + 1))
        ERRORS+=("short link tags are not behaving as a set")
    fi

    # A tag must survive the two operations that change a link's identity:
    # renaming its slug (tags are keyed on it) and being renamed as a group
    # onto a tag the link ALREADY carries — the second one aborted the whole
    # rename when it was an UPDATE against the primary key.
    printf "%-50s" "tags_survive_slug_rename_and_merge"
    RS=$(curl -s -m 30 -X POST "${BASE_URL}/api/shortlink/${TM}/rename?pwd=${CLIENT_PWD}" \
        -H 'Content-Type: application/json' -d '{"slug":"apitest-renamed-tags"}' | jq -c '.tags')
    merged=$(curl -s -m 30 -X POST "${BASE_URL}/api/shortlinks/retag?pwd=${CLIENT_PWD}" \
        -H 'Content-Type: application/json' -d '{"tag":"apitest-b","new_tag":"apitest-c"}' | jq -r '.renamed')
    after=$(curl -s -m 30 "${BASE_URL}/api/shortlinks?pwd=${CLIENT_PWD}" \
        | jq -c '[.groups[].links[] | select(.slug=="apitest-renamed-tags") | .tags][0]')
    # An empty new_tag with delete:true removes the tag everywhere.
    gone=$(curl -s -m 30 -X POST "${BASE_URL}/api/shortlinks/retag?pwd=${CLIENT_PWD}" \
        -H 'Content-Type: application/json' -d '{"tag":"apitest-c","delete":true}' | jq -r '.renamed')
    if [[ "$RS" == '["apitest-b","apitest-c"]' && "$merged" == "1" \
       && "$after" == '["apitest-c"]' && "$gone" == "1" ]]; then
        green "✓ (rename keeps, merge dedupes, delete-all)"; PASSED=$((PASSED + 1))
    else
        red "FAIL (renamed=$RS merged=$merged after=$after gone=$gone)"; FAILED=$((FAILED + 1))
        ERRORS+=("short link tags lost across a slug rename or a merge")
    fi
    # The rename left an alias on the OLD slug and moved the row to the new one;
    # both are fixtures, so both go into the ledger the sweeper reads.
    printf 'apitest-renamed-tags\n' >> "$MINTED_LOG"

    # The vocabulary is what makes a tag the SAME word as last time, so it must
    # carry counts: a chooser that cannot tell "report (12 links)" from a typo
    # made once is a chooser that spreads the typo.
    printf "%-50s" "tag_vocabulary_carries_counts"
    TV=$(mint '{"url":"/?park_focus=CAF_Chinko&tagvocab=1","tags":["apitest-vocab"]}')
    det=$(curl -s -m 30 "${BASE_URL}/api/shortlink-tags?pwd=${CLIENT_PWD}" \
        | jq -r '[.detail[] | select(.tag=="apitest-vocab")] | "\(length):\(.[0].links)"')
    if [[ "$det" == "1:1" ]]; then
        green "✓"; PASSED=$((PASSED + 1))
    else
        red "FAIL (detail=$det)"; FAILED=$((FAILED + 1))
        ERRORS+=("tag vocabulary lost its counts")
    fi

    # A link belongs to the login that minted it. Another login must neither
    # see it in /api/shortlinks nor act on it — and the refusal is a 404, not
    # a 403, because an id must not be an oracle (AGENTS.md invariant 6).
    if [[ "$CLIENT_PWD" != "test2026" ]]; then
        printf "%-50s" "shortlinks_scoped_to_their_login"
        seen=$(curl -s -m 30 "${BASE_URL}/api/shortlinks?pwd=test2026" \
            | jq -r --arg s "$T2" '[.groups[].links[] | select(.slug==$s)] | length')
        del=$(curl -s -m 30 -o /dev/null -w "%{http_code}" -X DELETE \
            "${BASE_URL}/api/shortlink/${T2}?pwd=test2026")
        still=$(curl -s -m 30 "${BASE_URL}/api/shortlinks?pwd=${CLIENT_PWD}" \
            | jq -r --arg s "$T2" '[.groups[].links[] | select(.slug==$s)] | length')
        if [[ "$seen" == "0" && "$del" == "404" && "$still" == "1" ]]; then
            green "✓ (unseen, delete 404, survives)"; PASSED=$((PASSED + 1))
        else
            red "FAIL (seen=$seen del=$del still=$still)"; FAILED=$((FAILED + 1))
            ERRORS+=("share links leak across logins")
        fi
    fi

    # Teardown. The API revokes a guest rather than deleting it (that is the
    # point -- the sheet keeps the evidence), so the rows are removed directly
    # afterwards: these are test fixtures, not history worth keeping. Scoped by
    # exact slug, never by a LIKE over the url column, which would eventually
    # match somebody's real link.
    printf "%-50s" "shared_link_fixtures_cleaned_up"
    mapfile -t MINTED < "$MINTED_LOG"
    left=0
    for sl in "${MINTED[@]}"; do
        [[ -z "$sl" ]] && continue
        curl -s -m 30 -o /dev/null -X DELETE "${BASE_URL}/api/shortlink/${sl}?pwd=${CLIENT_PWD}"
        # The API revokes a guest rather than deleting it (that is the point --
        # the sheet keeps the evidence), so the row goes directly afterwards:
        # these are fixtures, not history. Scoped by exact slug, never by a
        # LIKE over url, which would eventually match a real link.
        # Tag rows are keyed on the slug, so a fixture's tags would outlive it
        # and keep showing up in the vocabulary and in every count.
        sqlite3 db.sqlite3 "DELETE FROM short_link_tags WHERE slug = '${sl}'" 2>/dev/null
        sqlite3 db.sqlite3 "DELETE FROM short_links WHERE slug = '${sl}' OR alias_of = '${sl}'" 2>/dev/null
        n=$(sqlite3 db.sqlite3 "SELECT COUNT(*) FROM short_links WHERE slug = '${sl}'" 2>/dev/null || echo 1)
        left=$((left + n))
    done
    rm -f "$MINTED_LOG"
    # The completeness check is DERIVED, not a typed-in count: every slug this
    # block still holds a variable for must appear in the ledger. A literal
    # "at least 8" would describe today's number of tests and quietly stop
    # meaning anything the moment one is added or removed (AGENTS.md
    # invariant 2) -- it was wrong within a minute of being written.
    missing=0
    for want in "$G" "$G2" "$N" "$expslug" "$L" "$U" "$nlslug" "$TM" "$TV"; do
        [[ -z "$want" ]] && { missing=$((missing + 1)); continue; }
        printf '%s\n' "${MINTED[@]}" | grep -qx "$want" || missing=$((missing + 1))
    done
    if [[ "$left" == "0" && "$missing" == "0" ]]; then
        green "✓ (${#MINTED[@]} removed)"; PASSED=$((PASSED + 1))
    else
        red "FAIL (${#MINTED[@]} minted, $left left, $missing untracked)"; FAILED=$((FAILED + 1))
        ERRORS+=("test short links not cleaned up")
    fi
fi

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
