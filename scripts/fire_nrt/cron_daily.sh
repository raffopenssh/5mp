#!/bin/bash
# Daily fire data download - runs at 3am UTC
# Downloads last 5 days of NRT data with 50km buffer around parks

LOG_DIR=/home/exedev/5mp/logs
LOG_FILE="$LOG_DIR/fire_nrt_daily_$(date +%Y%m%d).log"
mkdir -p "$LOG_DIR"

cd /home/exedev/5mp
source .venv/bin/activate 2>/dev/null || true

echo "=== Daily Fire Update Started: $(date) ===" >> "$LOG_FILE"

# Download last 5 days of NRT data (with 50km buffer)
echo "[1/1] Downloading NRT fire data (50km buffer)..." >> "$LOG_FILE"
python3 scripts/fire_nrt/download_nrt.py --all --days 5 --buffer 50 >> "$LOG_FILE" 2>&1

echo "=== Daily Fire Update Completed: $(date) ===" >> "$LOG_FILE"

# Keep only last 30 days of logs
find "$LOG_DIR" -name "fire_nrt_daily_*.log" -mtime +30 -delete
