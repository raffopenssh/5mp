#!/bin/bash
# Monitor NRT fire rebuild progress

LOG=$(ls -t logs/nrt_rebuild_*.log 2>/dev/null | head -1)

if [ -z "$LOG" ]; then
    echo "No rebuild log found"
    exit 1
fi

echo "========================================="
echo "NRT FIRE REBUILD MONITOR"  
echo "========================================="
echo ""

if ps aux | grep -q "[p]ython3 scripts/rebuild"; then
    echo "Status: ✅ RUNNING"
    PID=$(ps aux | grep "[p]ython3 scripts/rebuild" | awk '{print $2}')
    echo "PID: $PID"
else
    echo "Status: ⏹ COMPLETE or STOPPED"
fi

echo ""
echo "Progress (parks completed):"
COMPLETED=$(grep -c "-> .* groups" "$LOG")
echo "  $COMPLETED / 162 parks processed"

echo ""
echo "Fire groups created:"
TOTAL_GROUPS=$(grep "-> .* groups" "$LOG" | grep -oP '\d+(?= groups)' | awk '{s+=$1} END {print s}')
echo "  ${TOTAL_GROUPS:-0} trajectory groups"

echo ""
echo "Last 10 parks:"
tail -10 "$LOG" | grep "\[.*\]"

echo ""
echo "========================================="
echo "Log: $LOG"
echo "Tail: tail -f $LOG"
echo "========================================="
