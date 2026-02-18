#!/bin/bash
# Daily fire data download and incremental analysis - runs at 3am UTC
# Downloads last 5 days of NRT data with 50km buffer, runs incremental analysis

LOG_DIR=/home/exedev/5mp/logs
LOG_FILE="$LOG_DIR/fire_nrt_daily_$(date +%Y%m%d).log"
mkdir -p "$LOG_DIR"

cd /home/exedev/5mp
source .venv/bin/activate 2>/dev/null || true

echo "=== Daily Fire Update Started: $(date) ===" >> "$LOG_FILE"

# 1. Download last 5 days of NRT data (with 50km buffer)
echo "[1/4] Downloading NRT fire data (50km buffer)..." >> "$LOG_FILE"
python3 scripts/fire_nrt/download_nrt.py --all --days 5 --buffer 50 >> "$LOG_FILE" 2>&1

# 2. Run incremental fire analysis (last 14 days only)
echo "[2/4] Running incremental fire analysis v2..." >> "$LOG_FILE"
python3 scripts/rebuild_park_fire_analysis_v2.py --incremental --days 14 >> "$LOG_FILE" 2>&1

# 3. Run incremental trajectory enrichment
echo "[3/4] Running incremental trajectory analysis v4..." >> "$LOG_FILE"
python3 scripts/analyze_fire_trajectories_v4.py --incremental --days 14 >> "$LOG_FILE" 2>&1

# 4. Load fire trajectories to database
echo "[4/5] Loading fire trajectories to database..." >> "$LOG_FILE"
python3 scripts/load_fire_trajectories_to_db.py --force >> "$LOG_FILE" 2>&1

# 5. Update narrative cache for affected parks
echo "[5/6] Running incremental narrative precompute v4..." >> "$LOG_FILE"
python3 scripts/precompute_narratives_v4.py --incremental --days 14 >> "$LOG_FILE" 2>&1

# 6. Update fire group alerts in the Go server
echo "[6/6] Updating fire group alerts..." >> "$LOG_FILE"
curl -s -X POST "http://localhost:8000/api/admin/update-fire-alerts?pwd=test2026" >> "$LOG_FILE" 2>&1
echo "" >> "$LOG_FILE"

echo "=== Daily Fire Update Completed: $(date) ===" >> "$LOG_FILE"

# Keep only last 30 days of logs
find "$LOG_DIR" -name "fire_nrt_daily_*.log" -mtime +30 -delete
