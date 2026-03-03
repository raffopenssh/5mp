#!/bin/bash
cd /home/exedev/5mp
LOG=/home/exedev/5mp/osm_places_json.log

echo "=== OSM Places JSON Download Started: $(date) ===" | tee $LOG
source .venv/bin/activate

python scripts/download_osm_places_to_file.py 2>&1 | tee -a $LOG

echo "" | tee -a $LOG
echo "=== OSM Places JSON Download Complete: $(date) ===" | tee -a $LOG
