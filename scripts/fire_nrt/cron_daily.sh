#!/bin/bash
# Daily fire data download - runs at 3am UTC
# Downloads last 5 days of NRT data to catch any updates

LOG_DIR=/home/exedev/5mp/logs
LOG_FILE="$LOG_DIR/fire_nrt_daily_$(date +%Y%m%d).log"
mkdir -p "$LOG_DIR"

cd /home/exedev/5mp
echo "Starting daily fire download at $(date)" >> "$LOG_FILE"

# Download last 5 days of NRT data
python3 scripts/fire_nrt/download_nrt.py --all --days 5 >> "$LOG_FILE" 2>&1

# Run trajectory analysis (to be implemented)
# python3 scripts/fire_nrt/analyze_trajectories.py >> "$LOG_FILE" 2>&1

echo "Completed at $(date)" >> "$LOG_FILE"

# Keep only last 30 days of logs
find "$LOG_DIR" -name "fire_nrt_daily_*.log" -mtime +30 -delete
