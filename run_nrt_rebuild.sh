#!/bin/bash
# Rebuild fire trajectories for parks with new NRT data

set -e

echo "========================================="
echo "NRT Fire Rebuild - v5 Pipeline"
echo "========================================="
echo ""

# Park count from fire_detections (canonical). Previously counted files in
# data/raw-fire-viirs-*/, a rolling window scheduled for deletion (#15).
PARKS_WITH_DATA=$(sqlite3 db.sqlite3 \
  "SELECT COUNT(DISTINCT protected_area_id) FROM fire_detections
   WHERE protected_area_id IS NOT NULL")

echo "Found $PARKS_WITH_DATA parks with fire detections"
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
