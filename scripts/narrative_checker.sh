#!/bin/bash
# Narrative Data Checker Script
# Run this to verify all narrative data is properly loaded and served
#
# Usage: ./scripts/narrative_checker.sh [BASE_URL] [PASSWORD]
# Example: ./scripts/narrative_checker.sh http://localhost:8000 test2026

BASE_URL="${1:-http://localhost:8000}"
PWD="${2:-test2026}"
TEST_PARKS=("CAF_Chinko" "TCD_Zakouma" "COD_Virunga")

echo "========================================"
echo "Narrative Data Checker"
echo "========================================"
echo "Base URL: $BASE_URL"
echo "Test Parks: ${TEST_PARKS[*]}"
echo ""

PASS=0
FAIL=0
WARN=0

check() {
    local name="$1"
    local condition="$2"
    local value="$3"
    
    if [ "$condition" = "true" ]; then
        echo "✅ $name: $value"
        ((PASS++))
    else
        echo "❌ $name: $value"
        ((FAIL++))
    fi
}

warn() {
    local name="$1"
    local value="$2"
    echo "⚠️  $name: $value"
    ((WARN++))
}

for PARK in "${TEST_PARKS[@]}"; do
    echo ""
    echo "========================================"
    echo "Testing: $PARK"
    echo "========================================"
    
    # Fire Narrative
    echo ""
    echo "--- Fire Narrative ---"
    FIRE=$(curl -s "$BASE_URL/api/parks/$PARK/fire-narrative?pwd=$PWD")
    
    FIRE_COUNT=$(echo "$FIRE" | jq -r '.narratives | length // 0')
    check "Fire narratives count" "$([ "$FIRE_COUNT" -gt 0 ] && echo true)" "$FIRE_COUNT"
    
    FIRE_SAMPLE=$(echo "$FIRE" | jq -r '.narratives[0].narrative // "NONE"' | head -c 80)
    if [ "$FIRE_SAMPLE" != "NONE" ] && [ -n "$FIRE_SAMPLE" ]; then
        echo "   Sample: \"$FIRE_SAMPLE...\""
    fi
    
    # Fire trend data
    TREND_SEASONALITY=$(echo "$FIRE" | jq -r '.trend.seasonality // "null"')
    check "Fire trend.seasonality" "$([ "$TREND_SEASONALITY" != "null" ] && echo true)" "$TREND_SEASONALITY"
    
    TREND_MONTHS=$(echo "$FIRE" | jq -r '.trend.months | length // 0')
    check "Fire trend.months count" "$([ "$TREND_MONTHS" -gt 0 ] && echo true)" "$TREND_MONTHS"
    
    TREND_WEEKS=$(echo "$FIRE" | jq -r '.trend.weeks | length // 0')
    if [ "$TREND_WEEKS" -gt 0 ]; then
        check "Fire trend.weeks count" "true" "$TREND_WEEKS"
    else
        warn "Fire trend.weeks count" "$TREND_WEEKS (optional)"
    fi
    
    LAT_COMP=$(echo "$FIRE" | jq -r '.trend.latitude_comparison.percentile // "null"')
    check "Fire latitude_comparison" "$([ "$LAT_COMP" != "null" ] && echo true)" "percentile=$LAT_COMP"
    
    # Deforestation Narrative
    echo ""
    echo "--- Deforestation Narrative ---"
    DEFO=$(curl -s "$BASE_URL/api/parks/$PARK/deforestation-narrative?pwd=$PWD")
    
    DEFO_YEARLY=$(echo "$DEFO" | jq -r '.yearly_stories | length // 0')
    check "Deforestation yearly_stories" "$([ "$DEFO_YEARLY" -gt 0 ] && echo true)" "$DEFO_YEARLY"
    
    DEFO_CLASSIFIED=$(echo "$DEFO" | jq -r '.classified_events | length // 0')
    check "Deforestation classified_events" "$([ "$DEFO_CLASSIFIED" -gt 0 ] && echo true)" "$DEFO_CLASSIFIED"
    
    DEFO_SAMPLE=$(echo "$DEFO" | jq -r '.classified_events[0].narrative // .yearly_stories[0].narrative // "NONE"' | head -c 80)
    if [ "$DEFO_SAMPLE" != "NONE" ] && [ -n "$DEFO_SAMPLE" ]; then
        echo "   Sample: \"$DEFO_SAMPLE...\""
    fi
    
    DEFO_SUMMARY=$(echo "$DEFO" | jq -r '.summary // "NONE"' | head -c 100)
    check "Deforestation summary" "$([ "$DEFO_SUMMARY" != "NONE" ] && [ -n "$DEFO_SUMMARY" ] && echo true)" "${DEFO_SUMMARY:0:60}..."
    
    # Settlement Narrative  
    echo ""
    echo "--- Settlement Narrative ---"
    SETTL=$(curl -s "$BASE_URL/api/parks/$PARK/settlement-narrative?pwd=$PWD")
    
    SETTL_COUNT=$(echo "$SETTL" | jq -r '.classified_settlements | length // 0')
    check "Settlement classified_settlements" "$([ "$SETTL_COUNT" -gt 0 ] && echo true)" "$SETTL_COUNT"
    
    SETTL_SAMPLE=$(echo "$SETTL" | jq -r '.classified_settlements[0].narrative // "NONE"' | head -c 80)
    if [ "$SETTL_SAMPLE" != "NONE" ] && [ -n "$SETTL_SAMPLE" ]; then
        echo "   Sample: \"$SETTL_SAMPLE...\""
    fi
    
    SETTL_SUMMARY=$(echo "$SETTL" | jq -r '.summary // "NONE"' | head -c 100)
    check "Settlement summary" "$([ "$SETTL_SUMMARY" != "NONE" ] && [ -n "$SETTL_SUMMARY" ] && echo true)" "${SETTL_SUMMARY:0:60}..."
    
    # Check database tables
    echo ""
    echo "--- Database Check ---"
done

echo ""
echo "========================================"
echo "SUMMARY"
echo "========================================"
echo "✅ Passed: $PASS"
echo "❌ Failed: $FAIL"
echo "⚠️  Warnings: $WARN"
echo ""

if [ $FAIL -gt 0 ]; then
    echo "❌ SOME CHECKS FAILED"
    echo ""
    echo "To fix missing data, run these scripts:"
    echo "  python scripts/analyze_fire_trajectories_v3.py  # Fire monthly/weekly trends"
    echo "  python scripts/precompute_narratives_v3.py      # All narrative caches"
    echo "  python scripts/load_json_data.py                # Load JSON to database"
    exit 1
else
    echo "✅ ALL CHECKS PASSED"
    exit 0
fi
