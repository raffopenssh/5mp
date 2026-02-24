#!/bin/bash
# Monitor fire NRT backfill progress

LOG_FILE="logs/fire_nrt_backfill_sp_20260224_0816.log"

echo "========================================"
echo "FIRE NRT BACKFILL MONITOR"
echo "========================================"
echo ""

# Check if process is running
if ps aux | grep -q "[d]ownload_nrt.py"; then
    echo "Status: ✅ RUNNING"
    PID=$(ps aux | grep "[d]ownload_nrt.py" | awk '{print $2}')
    echo "PID: $PID"
else
    echo "Status: ⏹ STOPPED (completed or failed)"
fi

echo ""
echo "Progress:"
COMPLETED=$(grep -c "fires fetched" "$LOG_FILE" 2>/dev/null || echo "0")
echo "  Parks processed: $COMPLETED / 162"
echo "  Progress: $(( COMPLETED * 100 / 162 ))%"

echo ""
echo "Fires downloaded:"
TOTAL_FIRES=$(grep "fires fetched" "$LOG_FILE" 2>/dev/null | grep -oP '\d+(?= fires fetched)' | awk '{s+=$1} END {print s}')
echo "  Total: ${TOTAL_FIRES:-0} fires"

INSIDE_FIRES=$(grep "fires fetched" "$LOG_FILE" 2>/dev/null | grep -oP '\d+(?= inside)' | awk '{s+=$1} END {print s}')
echo "  Inside parks: ${INSIDE_FIRES:-0} fires"

echo ""
echo "Recent activity:"
tail -5 "$LOG_FILE" 2>/dev/null | grep "Park" || echo "  No recent activity"

echo ""
echo "========================================"
echo "Commands:"
echo "  Watch live: tail -f $LOG_FILE"
echo "  Kill process: kill $PID"
echo "========================================"
