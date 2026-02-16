#!/bin/bash
# Daily publication sync - runs at 5am UTC
# Syncs research publications from OpenAlex for stale parks

LOG_DIR=/home/exedev/5mp/logs
LOG_FILE="$LOG_DIR/publications_sync_$(date +%Y%m%d).log"
mkdir -p "$LOG_DIR"

cd /home/exedev/5mp
source .venv/bin/activate 2>/dev/null || true

echo "=== Publication Sync Started: $(date) ===" >> "$LOG_FILE"

# Sync parks that haven't been updated in 7+ days
python3 scripts/sync_publications.py --stale --notify --limit 20 >> "$LOG_FILE" 2>&1

echo "=== Publication Sync Completed: $(date) ===" >> "$LOG_FILE"

# Keep only last 30 days of logs
find "$LOG_DIR" -name "publications_sync_*.log" -mtime +30 -delete
