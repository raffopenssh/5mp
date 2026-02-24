#!/bin/bash
# Rebuild fire trajectories for parks with new NRT data

set -e

echo "========================================="
echo "NRT Fire Rebuild - v5 Pipeline"
echo "========================================="
echo ""

RAW_DIR="data/raw-fire-viirs-20200101-20260222"
PARKS_WITH_DATA=$(ls $RAW_DIR/*.json 2>/dev/null | wc -l)

echo "Found $PARKS_WITH_DATA parks with raw fire data"
echo "Starting rebuild..."
echo ""

# Set Python path for sklearn
export PYTHONPATH=/usr/lib/python3/dist-packages:$PYTHONPATH

# Rebuild trajectories (will process all parks in raw dir)
python3 scripts/rebuild_fire_trajectories_v5.py 2>&1 | tee logs/nrt_rebuild_$(date +%Y%m%d_%H%M).log

echo ""
echo "========================================="
echo "Rebuild complete!"
echo "========================================="
