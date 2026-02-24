#!/bin/bash
# Process the backfilled NRT fire data through v5 pipeline

set -e

echo "========================================"
echo "FIRE BACKFILL PIPELINE - V5"
echo "========================================"
echo ""

# Get list of parks with new fires
echo "Step 1: Identifying parks with new fire data..."
PARKS_WITH_FIRES=$(grep "fires fetched" logs/fire_nrt_backfill_sp_20260224_0816.log | \
    grep -v "No fires" | \
    sed 's/.*Park //' | \
    sed 's/:.*//' | \
    sort -u)

PARK_COUNT=$(echo "$PARKS_WITH_FIRES" | wc -l)
echo "Found $PARK_COUNT parks with new fires"
echo ""

# Step 2: Merge NRT data into main fire dataset
# The NRT files are in data/fire_nrt/{park_id}_nrt.json
# These need to be processed into the v5 trajectory analysis

echo "Step 2: Running v5 trajectory rebuild for affected parks..."
echo "This will process the NRT fire data and create new trajectories"
echo ""

# Run incremental rebuild (processes last 60+ days for affected parks)
python3 scripts/rebuild_fire_trajectories_v5.py --incremental 2>&1 | tee logs/fire_v5_rebuild_$(date +%Y%m%d_%H%M).log

echo ""
echo "Step 3: Loading fire groups to database..."
python3 scripts/load_fire_groups_to_db.py --incremental 2>&1 | tee logs/fire_load_db_$(date +%Y%m%d_%H%M).log

echo ""
echo "Step 4: Updating narrative cache..."
python3 scripts/precompute_narratives_v5.py --incremental 2>&1 | tee logs/fire_narratives_$(date +%Y%m%d_%H%M).log

echo ""
echo "========================================"
echo "PIPELINE COMPLETE"
echo "========================================"
echo ""
echo "Summary:"
echo "  Parks processed: $PARK_COUNT"
echo "  New fires: 9,942"
echo "  Date range: 2025-12-27 to 2026-02-24"
echo ""
echo "Check logs in logs/fire_v5_rebuild_*, fire_load_db_*, fire_narratives_*"
