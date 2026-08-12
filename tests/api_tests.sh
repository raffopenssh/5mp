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
# it came from. Two things must hold or it is the wrong download: it has to be
# a real GeoPackage (SQLite with the GPKG application_id), and it must carry a
# w_<commodity> column -- that column set is the reason it exists rather than
# the MBTiles, and it is derived per build, so a vectorizer change that drops
# every affinity would otherwise ship silently as a valid-but-useless file.
printf "%-50s" "geomap_geopackage_typed_and_filterable"
GEO_TMP=$(mktemp /tmp/geomapXXXX.gpkg)
GEO_CODE=$(curl -s -m 300 -o "$GEO_TMP" -w "%{http_code}" -b "$COOKIE_FILE" \
    "${BASE_URL}/api/geomap/car/geopackage")
GEO_APP=$(sqlite3 "$GEO_TMP" "PRAGMA application_id" 2>/dev/null || echo 0)
GEO_GOLD=$(sqlite3 "$GEO_TMP" 'SELECT COUNT(*) FROM geology_car WHERE "w_gold" IS NOT NULL' 2>/dev/null || echo 0)
GEO_STYLE=$(sqlite3 "$GEO_TMP" "SELECT COUNT(*) FROM layer_styles WHERE useAsDefault=1" 2>/dev/null || echo 0)
GEO_PROJ=$(sqlite3 "$GEO_TMP" "SELECT COUNT(*) FROM qgis_projects" 2>/dev/null || echo 0)
rm -f "$GEO_TMP"
if [[ "$GEO_CODE" == "200" && "$GEO_APP" == "1196444487" && "$GEO_GOLD" -gt 0 \
      && "$GEO_STYLE" -gt 0 && "$GEO_PROJ" -gt 0 ]]; then
    green "✓"; PASSED=$((PASSED + 1))
else
    red "FAIL (code $GEO_CODE, app_id $GEO_APP, gold units $GEO_GOLD, styles $GEO_STYLE, projects $GEO_PROJ)"
    FAILED=$((FAILED + 1)); ERRORS+=("geomap geopackage")
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
