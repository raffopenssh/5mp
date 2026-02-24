#!/bin/bash
# Daily publication sync - runs at 5am UTC
# Triggers publication sync via the running server

LOG_DIR=/home/exedev/5mp/logs
LOG_FILE="$LOG_DIR/publications_sync_$(date +%Y%m%d).log"
mkdir -p "$LOG_DIR"

echo "=== Publication Sync Started: $(date) ===" >> "$LOG_FILE"

# Trigger publication sync via curl to the running server
curl -s -X POST "http://localhost:8000/api/admin/trigger-publication-sync?pwd=test2026" >> "$LOG_FILE" 2>&1 || \
  echo "Server not running or endpoint unavailable" >> "$LOG_FILE"

echo "=== Publication Sync Triggered: $(date) ===" >> "$LOG_FILE"

# Keep only last 30 days of logs
find "$LOG_DIR" -name "publications_sync_*.log" -mtime +30 -delete
