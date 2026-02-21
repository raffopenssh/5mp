# Shelley Prompt for New 5MP Instance

Copy this prompt when starting a new Shelley conversation on a fresh VM:

---

## Setup Prompt

```
I need you to set up the 5MP Conservation Monitoring application on this new VM.

## Repository
Clone from: https://github.com/YOUR_ORG/5mp.git
(If private, I'll provide the token)

## Database
Download from: https://five-megapixel-conservation.exe.xyz:8000/static/downloads/5mp_backup_20260221.sqlite3
Password required: test2026

## Installation Steps
1. Install dependencies: golang, python3, sqlite3, gdal
2. Clone the repository
3. Create Python venv and install requirements.txt
4. Download the database backup
5. Verify database integrity with `sqlite3 db.sqlite3 "PRAGMA integrity_check"`
6. Build the Go server with `make build`
7. Set up systemd service
8. Start the server

## Fire Pipeline (After Server Working)
The fire data has issues (zigzags in historic data, some periods only show fires inside park).
Fix by running the full pipeline:

1. **Download NRT fires** (if FIRMS_API_KEY is set):
   ```bash
   source .venv/bin/activate
   export FIRMS_API_KEY="your_key_here"
   python3 scripts/fire_nrt/download_nrt.py --all --days 30 --buffer 50
   ```

2. **Rebuild fire groups** (clustering):
   ```bash
   python3 scripts/rebuild_park_fire_analysis_v2.py
   ```
   - Uses 15km DBSCAN clustering
   - Tracks groups across days
   - Output: data/fire_groups_v2/{park}.json

3. **Enrich with trajectories**:
   ```bash
   python3 scripts/analyze_fire_trajectories_v4.py
   ```
   - Adds context (rivers, roads, places)
   - Classifies fire types
   - Computes outcomes (STOPPED_INSIDE vs TRANSITED)
   - Output: data/fire_trajectories_v2/{park}.json

4. **Load to database**:
   ```bash
   python3 scripts/load_fire_trajectories_to_db.py --force
   ```
   - Updates feature_geometries table

5. **Precompute narratives**:
   ```bash
   python3 scripts/precompute_narratives_v4.py
   ```
   - Updates fire_narrative_cache table

## Data Sources
- Park boundaries: data/keystones_with_boundaries.json (162 parks)
- Fire detections: NASA FIRMS VIIRS (6M+ records in DB)
- Rivers: HydroRIVERS (data/rivers_hydro/)
- Roads: HeiGIT (data/roads_heigit/)
- Places: OSM (data/osm_places/)

## Passwords
- App access: test2026, REDACTED_PWD, REDACTED_PWD
- Admin: see secrets.env

## Feedback Mechanism
After installation, post the following to help debug:
1. Server status: `sudo systemctl status 5mp`
2. Database stats: `sqlite3 db.sqlite3 "SELECT COUNT(*) FROM fire_detections"`
3. Any errors in: `/tmp/5mp_install.log`

## Success Criteria
- Server responds at http://localhost:8000
- Can load a park popup (e.g., ?popup=CAF_Chinko)
- Fire narratives load correctly with proper date filtering
- No zigzag trajectories in recent data

## URLs
- Production: https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026
- DB Backup: https://five-megapixel-conservation.exe.xyz:8000/static/downloads/5mp_backup_20260221.sqlite3
- Fire Pipeline Docs: docs/FIRE_PIPELINE.md
```

---

## Quick Install Command

For a single-command install (after VM is ready):

```bash
# Set these first
export GITHUB_TOKEN="your_token_if_private"
export FIRMS_API_KEY="your_firms_api_key"

# Download and run setup
curl -sL https://five-megapixel-conservation.exe.xyz:8000/static/downloads/setup.sh | bash
```

---

## Troubleshooting

### Server won't start
```bash
# Check logs
journalctl -u 5mp -n 50

# Check if port in use
sudo lsof -i :8000

# Manual start for debugging
./server 2>&1 | tee server.log
```

### Database issues
```bash
# Integrity check
sqlite3 db.sqlite3 "PRAGMA integrity_check"

# Quick stats
sqlite3 db.sqlite3 "
SELECT 'fire_detections' as tbl, COUNT(*) FROM fire_detections
UNION ALL
SELECT 'feature_geometries', COUNT(*) FROM feature_geometries
UNION ALL  
SELECT 'fire_narrative_cache', COUNT(*) FROM fire_narrative_cache;
"
```

### Fire pipeline issues
```bash
# Test single park
python3 scripts/rebuild_park_fire_analysis_v2.py --park CAF_Chinko
python3 scripts/analyze_fire_trajectories_v4.py --park CAF_Chinko

# Check output
cat data/fire_groups_v2/CAF_Chinko.json | jq 'length'
cat data/fire_trajectories_v2/CAF_Chinko.json | jq '.[0]'
```
