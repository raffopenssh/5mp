#!/bin/bash
# Rebuild only parks with NRT fire data

export PYTHONPATH=/usr/lib/python3/dist-packages:$PYTHONPATH

RAW_DIR="data/raw-fire-viirs-20200101-20260222"
LOG="logs/nrt_park_rebuild_$(date +%Y%m%d_%H%M).log"

echo "=== NRT Park Rebuild $(date) ===" | tee -a "$LOG"
echo "" | tee -a "$LOG"

PARKS=$(ls $RAW_DIR/*.json 2>/dev/null | xargs -n1 basename | sed 's/.json//')
PARK_COUNT=$(echo "$PARKS" | wc -l)

echo "Rebuilding $PARK_COUNT parks with NRT data..." | tee -a "$LOG"
echo "" | tee -a "$LOG"

COUNTER=0
for PARK in $PARKS; do
    COUNTER=$((COUNTER + 1))
    echo "[$COUNTER/$PARK_COUNT] Processing $PARK..." | tee -a "$LOG"
    python3 scripts/rebuild_fire_trajectories_v5.py --park "$PARK" >> "$LOG" 2>&1
done

echo "" | tee -a "$LOG"
echo "=== Rebuild Complete $(date) ===" | tee -a "$LOG"
echo "Log: $LOG"
