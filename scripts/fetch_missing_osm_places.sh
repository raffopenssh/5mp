#!/bin/bash
cd /home/exedev/5mp
LOG=/home/exedev/5mp/osm_places.log

echo "=== OSM Places Fetch Started: $(date) ===" | tee $LOG
source .venv/bin/activate

python3 scripts/download_osm_places.py 2>&1 | tee -a $LOG

echo "" | tee -a $LOG
echo "=== OSM Places Fetch Complete: $(date) ===" | tee -a $LOG
