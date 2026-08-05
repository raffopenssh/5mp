#!/bin/bash
# Rebuild fire trajectories for every park that has fire detections.
#
# Park enumeration comes from fire_detections, the only fire source. (It used to
# come from `ls data/raw-fire-viirs-*/`, a rolling ~6-month window of duplicated
# data, deleted 2026-08 -- handover #15.)

export PYTHONPATH=/usr/lib/python3/dist-packages:$PYTHONPATH

LOG="logs/nrt_park_rebuild_$(date +%Y%m%d_%H%M).log"

echo "=== NRT Park Rebuild $(date) ===" | tee -a "$LOG"
echo "" | tee -a "$LOG"

PARKS=$(sqlite3 db.sqlite3 \
  "SELECT DISTINCT protected_area_id FROM fire_detections
   WHERE protected_area_id IS NOT NULL ORDER BY 1")
PARK_COUNT=$(echo "$PARKS" | wc -l)

echo "Rebuilding $PARK_COUNT parks with fire data..." | tee -a "$LOG"
echo "" | tee -a "$LOG"

# One process for all parks: --parks reuses the keystone load, the DB
# connection and the sklearn/scipy import (per-park subprocesses cost ~6min).
python3 scripts/rebuild_fire_trajectories_v5.py \
    --parks "$(echo "$PARKS" | paste -sd,)" >> "$LOG" 2>&1

echo "" | tee -a "$LOG"
echo "=== Rebuild Complete $(date) ===" | tee -a "$LOG"
echo "Log: $LOG"
