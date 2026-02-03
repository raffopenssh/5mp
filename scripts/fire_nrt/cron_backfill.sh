#!/bin/bash
# Slow backfill for 2025-2026 data - runs at 4am UTC
# Processes one day at a time to stay within API limits

LOG_DIR=/home/exedev/5mp/logs
LOG_FILE="$LOG_DIR/fire_backfill_$(date +%Y%m%d).log"
PROGRESS_FILE=/home/exedev/5mp/data/backfill_progress.txt
mkdir -p "$LOG_DIR"

cd /home/exedev/5mp

# Initialize progress if not exists
if [ ! -f "$PROGRESS_FILE" ]; then
    echo "2025-01-01" > "$PROGRESS_FILE"
fi

CURRENT_DATE=$(cat "$PROGRESS_FILE")
END_DATE=$(date -d "yesterday" +%Y-%m-%d)

echo "Backfill starting at $(date), current date: $CURRENT_DATE" >> "$LOG_FILE"

# Process up to 7 days per run (to stay within API limits)
for i in {1..7}; do
    if [[ "$CURRENT_DATE" > "$END_DATE" ]]; then
        echo "Backfill complete - reached current date" >> "$LOG_FILE"
        break
    fi
    
    echo "Processing $CURRENT_DATE" >> "$LOG_FILE"
    python3 scripts/fire_nrt/download_nrt.py --backfill --start "$CURRENT_DATE" --end "$CURRENT_DATE" >> "$LOG_FILE" 2>&1
    
    # Move to next day
    CURRENT_DATE=$(date -d "$CURRENT_DATE + 1 day" +%Y-%m-%d)
    echo "$CURRENT_DATE" > "$PROGRESS_FILE"
    
    # Sleep between days to respect rate limits
    sleep 60
done

echo "Backfill batch completed at $(date), next date: $CURRENT_DATE" >> "$LOG_FILE"
