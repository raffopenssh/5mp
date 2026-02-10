#!/bin/bash
cd /home/exedev/5mp
LOG=/home/exedev/5mp/heigit_roads.log

echo "=== HeiGIT Roads Download Started: $(date) ===" | tee $LOG
source .venv/bin/activate

python scripts/download_heigit_roads.py 2>&1 | tee -a $LOG

echo "" | tee -a $LOG
echo "=== HeiGIT Roads Download Complete: $(date) ===" | tee -a $LOG
