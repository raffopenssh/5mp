# Shelley Prompt for New 5MP Instance

Copy this prompt when starting a new Shelley conversation on a fresh VM.

---

## Quick Setup Prompt

```
Set up 5MP Conservation Monitoring on this VM.

## Step 1: Install System Dependencies
sudo apt update && sudo apt install -y golang python3 python3-pip python3-venv sqlite3 git curl make

## Step 2: Database Download
Download from production (1.8GB):
curl -L "https://five-megapixel-conservation.exe.xyz:8000/static/downloads/5mp_backup_20260221.sqlite3?pwd=test2026" -o db.sqlite3
sqlite3 db.sqlite3 "PRAGMA integrity_check"

## Step 3: Clone Repository
The code is already at /home/exedev/5mp (if not, clone it).
cd /home/exedev/5mp

## Step 4: Python Environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

## Step 5: Build & Run Server
make build
./server &

Test: curl http://localhost:8000/api/health

## Step 6: Set Up systemd (Persistent)
Create /etc/systemd/system/5mp.service:
[Unit]
Description=5MP Conservation Monitoring
After=network.target

[Service]
WorkingDirectory=/home/exedev/5mp
ExecStart=/home/exedev/5mp/server
Restart=always
User=exedev
Environment=PORT=8000

[Install]
WantedBy=multi-user.target

Then:
sudo systemctl daemon-reload
sudo systemctl enable 5mp
sudo systemctl start 5mp

## Credentials
- App: pwd=test2026, REDACTED_PWD, REDACTED_PWD
- Admin: see secrets.env
```

---

## Fire Pipeline Fix (The Main Task)

The production data has **zigzag trajectories** in historic fire data. This was caused by including all individual fire detections in trajectory arrays instead of using daily centroids.

### Fixed Script Location
The fix is in `scripts/rebuild_park_fire_analysis_v2.py` - it now uses daily centroids for trajectories.

### Run Full Pipeline Rebuild

```bash
cd /home/exedev/5mp
source .venv/bin/activate

# 1. Rebuild all fire groups (uses daily centroids now)
python3 scripts/rebuild_park_fire_analysis_v2.py
# Output: data/fire_groups_v2/{park}.json
# Takes ~15-30 minutes for all 162 parks

# 2. Enrich with context (rivers, roads, places, classification)
python3 scripts/analyze_fire_trajectories_v4.py  
# Output: data/fire_trajectories_v2/{park}.json
# Takes ~20-40 minutes

# 3. Load trajectories to database
python3 scripts/load_fire_trajectories_to_db.py --force
# Updates feature_geometries table

# 4. Precompute narratives
python3 scripts/precompute_narratives_v4.py
# Updates fire_narrative_cache table
```

### Test Single Park First
```bash
python3 scripts/rebuild_park_fire_analysis_v2.py --park CAF_Chinko
python3 scripts/analyze_fire_trajectories_v4.py --park CAF_Chinko

# Verify - should show smooth trajectories (1 point per day)
python3 -c "
import json
with open('data/fire_trajectories_v2/CAF_Chinko.json') as f:
    data = json.load(f)
# Multi-day trajectory should have exactly days points
g = [t for t in data if t['days'] > 5][0]
print(f'Days: {g[\"days\"]}, Points: {len(g[\"trajectory\"])}')
print('First 5 points:', g['trajectory'][:5])
"
```

---

## Data Sources Already in DB

| Table | Records | Description |
|-------|---------|-------------|
| fire_detections | 6.1M | Raw VIIRS fires (2018-2026) |
| feature_geometries | 453K | GeoJSON polygons/lines |
| park_rivers | 215K | HydroRIVERS data |
| osm_places | 271K | Place names |

### No External Downloads Needed
The database backup has all the fire_detections. The pipeline rebuilds from that data.

---

## Background Jobs Setup (Optional)

For daily fire updates, set up cron:

```bash
# /etc/cron.d/5mp-fire
# Daily at 3am UTC - download NRT and update pipeline
0 3 * * * exedev cd /home/exedev/5mp && ./scripts/fire_nrt/cron_daily.sh >> /tmp/fire_daily.log 2>&1
```

Requires FIRMS API key:
```bash
export FIRMS_API_KEY="your_key_here"
```

---

## Verification Checklist

```bash
# Server running?
curl http://localhost:8000/api/health

# Database OK?
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM fire_detections"
# Should be ~6M

# Fire groups rebuilt?
ls data/fire_groups_v2/*.json | wc -l
# Should be 161+

# Trajectories enriched?
ls data/fire_trajectories_v2/*.json | wc -l  
# Should be 150+

# Narrative cache populated?
sqlite3 db.sqlite3 "SELECT COUNT(*) FROM fire_narrative_cache"
# Should be 162

# No zigzags? (daily points only)
python3 -c "
import json
with open('data/fire_trajectories_v2/CAF_Chinko.json') as f:
    data = json.load(f)
for g in data[:5]:
    if g['days'] != len(g['trajectory']):
        print(f'WARNING: {g[\"group_id\"]}: days={g[\"days\"]}, points={len(g[\"trajectory\"])}')
print('Check complete')
"
```

---

## URLs

| Purpose | URL |
|---------|-----|
| Production | https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026 |
| DB Backup | https://five-megapixel-conservation.exe.xyz:8000/static/downloads/5mp_backup_20260221.sqlite3?pwd=test2026 |
| Docs | docs/FIRE_PIPELINE.md, docs/API.md |

---

## Troubleshooting

### "No module named X"
```bash
source .venv/bin/activate
pip install shapely scikit-learn numpy
```

### "Database is locked"
Stop the server before running heavy scripts:
```bash
sudo systemctl stop 5mp
# run scripts
sudo systemctl start 5mp
```

### Check Progress
```bash
# Watch fire groups being built
watch -n 5 'ls data/fire_groups_v2/*.json | wc -l'

# Watch trajectory enrichment
watch -n 5 'ls data/fire_trajectories_v2/*.json | wc -l'
```

---

## Copy Back to Production

After pipeline completes, copy results back:

```bash
# From new VM - create archive
cd /home/exedev/5mp
tar czf fire_data_fixed.tar.gz data/fire_groups_v2/ data/fire_trajectories_v2/

# Copy to production (or use scp/rsync)
# Then on production:
tar xzf fire_data_fixed.tar.gz
python3 scripts/load_fire_trajectories_to_db.py --force
python3 scripts/precompute_narratives_v4.py
sudo systemctl restart 5mp
```
