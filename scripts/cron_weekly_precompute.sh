#!/bin/bash
# Weekly precompute - runs Sunday 2am UTC
# Full rebuild of fire analysis, trajectories, and narratives

LOG_DIR=/home/exedev/5mp/logs
LOG_FILE="$LOG_DIR/weekly_precompute_$(date +%Y%m%d).log"
mkdir -p "$LOG_DIR"

cd /home/exedev/5mp
source .venv/bin/activate 2>/dev/null || true

echo "=== Weekly Precompute Started: $(date) ===" >> "$LOG_FILE"

# 1. Full fire analysis rebuild (v2 - cross-park aware, 50km buffer)
echo "[1/6] Rebuilding fire analysis v2..." >> "$LOG_FILE"
python3 scripts/rebuild_park_fire_analysis_v2.py >> "$LOG_FILE" 2>&1

# 2. Enrich trajectories with context (v4)
echo "[2/6] Enriching fire trajectories v4..." >> "$LOG_FILE"
python3 scripts/analyze_fire_trajectories_v4.py >> "$LOG_FILE" 2>&1

# 3. Import trajectories to database
echo "[3/6] Importing trajectories to database..." >> "$LOG_FILE"
python3 scripts/import_trajectories_to_db.py >> "$LOG_FILE" 2>&1

# 4. Precompute all narratives (fire, settlement, deforestation)
echo "[4/6] Precomputing narratives v4..." >> "$LOG_FILE"
python3 scripts/precompute_narratives_v4.py >> "$LOG_FILE" 2>&1

# 5. Import events (deforestation, settlements) with polygon links
echo "[5/6] Importing events from JSON..." >> "$LOG_FILE"
python3 scripts/import_events_from_json.py >> "$LOG_FILE" 2>&1

# 6. Rebuild and restart server
echo "[6/6] Rebuilding and restarting server..." >> "$LOG_FILE"
make build >> "$LOG_FILE" 2>&1
sudo systemctl restart srv 2>/dev/null || pkill -HUP -f "./server"

echo "=== Weekly Precompute Completed: $(date) ===" >> "$LOG_FILE"

# Keep only last 8 weeks of logs
find "$LOG_DIR" -name "weekly_precompute_*.log" -mtime +56 -delete
