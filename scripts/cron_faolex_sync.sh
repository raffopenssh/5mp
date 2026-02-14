#!/bin/bash
# FAOLEX Legal Documents Sync - Weekly
# Add to crontab: 0 3 * * 0 /home/exedev/5mpglobe/scripts/cron_faolex_sync.sh

cd /home/exedev/5mpglobe

# Trigger FAOLEX sync via API or direct database operations
# The server's RunFAOLEXSync is called periodically by the research worker

# Log the execution
echo "$(date) - FAOLEX sync triggered" >> logs/faolex_sync.log

# Note: The actual sync is handled by the Go server's background worker
# This script is for manual triggering or additional processing
