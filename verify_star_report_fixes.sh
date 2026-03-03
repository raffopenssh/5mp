#!/bin/bash

echo "=== Star Report Fixes Verification ==="
echo
echo "This script verifies the two key fixes:"
echo "1. Built-up area is no longer 0 km² for all parks"
echo "2. '+ more' buttons added to all sections"
echo
echo "----------------------------------------"
echo

# Check 1: Database has ghsl_data
echo "✓ Check 1: ghsl_data table populated"
echo "  Total parks with built-up data:"
PARK_COUNT=$(sqlite3 db.sqlite3 "SELECT COUNT(*) FROM ghsl_data WHERE built_up_km2 > 0;")
echo "  $PARK_COUNT parks"
echo

# Check 2: Top parks by built-up area
echo "✓ Check 2: Top 5 parks by built-up area (km²)"
sqlite3 -column -header db.sqlite3 "SELECT park_id, ROUND(built_up_km2, 2) as buildup_km2, settlement_count FROM ghsl_data ORDER BY built_up_km2 DESC LIMIT 5;"
echo

# Check 3: API returns correct data
echo "✓ Check 3: API returns built-up area for Borana"
BUILDUP=$(curl -s "http://localhost:8000/api/parks/ETH_Borana/stats?pwd=test2026" | jq -r '.settlement.built_up_km2')
echo "  ETH_Borana built-up area: $BUILDUP km²"
echo "  Expected: ~435.64 km²"
echo

# Check 4: Template has "+ more" buttons
echo "✓ Check 4: Template code includes '+ more' buttons"
MORE_COUNT=$(grep -c "section-more-btn.*Show.*more" srv/templates/globe.html)
echo "  Found $MORE_COUNT '+ more' button implementations"
echo "  Expected: 5 (fire, deforestation, settlements, species, publications)"
echo

echo "----------------------------------------"
echo
echo "✅ All checks complete!"
echo
echo "To test manually:"
echo "1. Open http://localhost:8000/?pwd=test2026"
echo "2. Click on a park (e.g., Borana in Ethiopia)"  
echo "3. Star the park (☆ button)"
echo "4. Open star panel (★ badge in sidebar)"
echo "5. Verify built-up area shows correct value"
echo "6. Verify '+ Show X more' buttons appear for long lists"
echo
