#!/bin/bash
# Weekly precompute - runs Sunday 2am UTC
# Refreshes all narrative caches and analysis data

LOG_DIR=/home/exedev/5mp/logs
LOG_FILE="$LOG_DIR/weekly_precompute_$(date +%Y%m%d).log"
mkdir -p "$LOG_DIR"

cd /home/exedev/5mp
source .venv/bin/activate

echo "=== Weekly Precompute Started: $(date) ===" >> "$LOG_FILE"

# 1. Precompute fire narratives
echo "[1/4] Precomputing fire narratives..." >> "$LOG_FILE"
python3 scripts/precompute_narratives_v3.py >> "$LOG_FILE" 2>&1

# 2. Precompute fire realtime data
echo "[2/4] Precomputing fire realtime..." >> "$LOG_FILE"
python3 scripts/precompute_fire_realtime.py >> "$LOG_FILE" 2>&1

# 3. Import updated JSON to database
echo "[3/4] Importing JSON to database..." >> "$LOG_FILE"
python3 scripts/import_json_to_db.py >> "$LOG_FILE" 2>&1

# 4. Rebuild server to pick up any changes
echo "[4/4] Restarting server..." >> "$LOG_FILE"
make build >> "$LOG_FILE" 2>&1
sudo systemctl restart srv 2>/dev/null || pkill -HUP server

echo "=== Weekly Precompute Completed: $(date) ===" >> "$LOG_FILE"

# Keep only last 8 weeks of logs
find "$LOG_DIR" -name "weekly_precompute_*.log" -mtime +56 -delete
