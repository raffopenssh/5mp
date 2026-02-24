#!/bin/bash
# ============================================================
# PRODUCTION DEPLOYMENT - Fire Pipeline v5
# Run on production server: ./scripts/deploy_fire_v5_prod.sh
# ============================================================
set -e

STAGING_URL="https://five-megapixel-pipeline-background.exe.xyz:8000"
cd /home/exedev/5mp

echo "=== Step 1: Pull latest code ==="
git pull origin main

echo "=== Step 2: Download fire data from staging ==="
curl -L "${STAGING_URL}/static/downloads/fire_groups_v5.zip" -o /tmp/fire_groups_v5.zip
curl -L "${STAGING_URL}/static/downloads/fire_narratives_v5.zip" -o /tmp/fire_narratives_v5.zip

echo "=== Step 3: Backup existing data ==="
cp -r data/fire_groups_v5 data/fire_groups_v5.bak.$(date +%Y%m%d) 2>/dev/null || true
cp db.sqlite3 db.sqlite3.bak.$(date +%Y%m%d)

echo "=== Step 4: Extract data files ==="
unzip -o /tmp/fire_groups_v5.zip -d .
unzip -o /tmp/fire_narratives_v5.zip -d .

echo "=== Step 5: Reload fire groups to database ==="
mkdir -p logs
python3 scripts/load_fire_groups_to_db.py --force 2>&1 | tee logs/load_v5_prod_$(date +%Y%m%d).log

echo "=== Step 6: Update narrative cache ==="
python3 << 'PYSCRIPT'
import json, sqlite3
from pathlib import Path
from datetime import datetime

conn = sqlite3.connect('db.sqlite3')
count = 0
for f in Path('data/export/fire_narratives').glob('*.json'):
    data = json.load(open(f))
    park_id = f.stem
    years = data.get('trend', {}).get('years', [])
    from_year = min(years) if years else 2020
    to_year = max(years) if years else 2025
    conn.execute('''
        INSERT OR REPLACE INTO fire_narrative_cache 
        (park_id, narrative_json, computed_at, from_year, to_year) 
        VALUES (?, ?, ?, ?, ?)
    ''', (park_id, json.dumps(data), datetime.now().isoformat(), from_year, to_year))
    count += 1
conn.commit()
conn.close()
print(f"Updated {count} narrative cache entries")
PYSCRIPT

echo "=== Step 7: Rebuild server ==="
make build

echo "=== Step 8: Restart service ==="
sudo systemctl restart srv
sleep 3

echo "=== Step 9: Verify deployment ==="
LATEST_YEAR=$(curl -s "http://localhost:8000/api/parks/CAF_Chinko/fire-narrative?pwd=test2026" | jq -r '.trend.years[-1]')
echo "Latest year in narrative: $LATEST_YEAR"

if [ "$LATEST_YEAR" = "2025" ]; then
    echo "✓ Deployment verified successfully!"
else
    echo "⚠ Warning: Expected 2025, got $LATEST_YEAR"
fi

echo "=== Step 10: Service status ==="
systemctl status srv --no-pager | head -20

echo ""
echo "============================================================"
echo "Deployment complete!"
echo "Verify at: https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026"
echo "============================================================"
