#!/bin/bash
# Cron job for syncing research publications
# Runs daily, processes stale parks, creates notifications for new papers

cd /home/exedev/5mp
LOG_FILE="logs/publications_sync_$(date +%Y%m%d).log"

echo "=== Publication Sync $(date) ===" >> "$LOG_FILE"

# Sync parks that haven't been updated in 7+ days
python3 scripts/sync_publications.py --stale --notify --limit 20 >> "$LOG_FILE" 2>&1

echo "=== Done $(date) ===" >> "$LOG_FILE"
