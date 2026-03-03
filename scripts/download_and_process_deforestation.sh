#!/bin/bash
set -e

cd /home/exedev/5mp
LOG=/home/exedev/5mp/deforestation_process.log

echo "=== Deforestation Processing Started: $(date) ===" | tee $LOG

TILES=(
    "10N_030E" "10S_020E" "10S_030E" "10N_020E" "20S_030E"
    "20S_020E" "10N_010W" "20N_010E" "20S_010E" "10S_010E"
    "20N_000E" "30S_010E" "30N_000E" "20N_010W" "20N_030E"
    "20N_020W" "20N_020E" "30S_020E"
)

BASE_URL="https://storage.googleapis.com/earthenginepartners-hansen/GFC-2023-v1.11/Hansen_GFC-2023-v1.11_lossyear"
HANSEN_DIR="data/hansen"

mkdir -p $HANSEN_DIR

echo "" | tee -a $LOG
echo "=== Downloading ${#TILES[@]} Hansen tiles ===" | tee -a $LOG

for tile in "${TILES[@]}"; do
    outfile="${HANSEN_DIR}/lossyear_${tile}.tif"
    if [ -f "$outfile" ]; then
        echo "  $tile: already exists, skipping" | tee -a $LOG
        continue
    fi
    echo "  Downloading $tile..." | tee -a $LOG
    curl -s -o "$outfile" "${BASE_URL}_${tile}.tif"
    size=$(ls -lh "$outfile" | awk '{print $5}')
    echo "    Done: $size" | tee -a $LOG
done

echo "" | tee -a $LOG
echo "=== Download complete. Running polygon processor ===" | tee -a $LOG
echo "" | tee -a $LOG

python3 scripts/process_deforestation_polygons.py 2>&1 | tee -a $LOG

echo "" | tee -a $LOG
echo "=== Processing Complete: $(date) ===" | tee -a $LOG
