#!/bin/bash
# FAOLEX Legal Documents Sync - Weekly (Sunday 4am UTC)
# Syncs conservation-related legal documents from FAO FAOLEX database

LOG_DIR=/home/exedev/5mp/logs
LOG_FILE="$LOG_DIR/faolex_sync_$(date +%Y%m%d).log"
mkdir -p "$LOG_DIR"

cd /home/exedev/5mp

echo "=== FAOLEX Sync Started: $(date) ===" >> "$LOG_FILE"

# Trigger FAOLEX sync via curl to the running server
# The server's background worker handles the actual sync
curl -s -X POST "http://localhost:8000/api/admin/trigger-faolex-sync?pwd=test2026" >> "$LOG_FILE" 2>&1 || \
  echo "Server not running or endpoint unavailable" >> "$LOG_FILE"

echo "=== FAOLEX Sync Triggered: $(date) ===" >> "$LOG_FILE"

# Keep only last 8 weeks of logs
find "$LOG_DIR" -name "faolex_sync_*.log" -mtime +56 -delete
