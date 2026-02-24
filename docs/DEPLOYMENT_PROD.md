# Production Deployment Guide - Fire Pipeline v5

## Quick Deploy (from staging)

```bash
# Run on production server
./scripts/deploy_fire_v5_prod.sh
```

## Manual Steps

### 1. Download Data from Staging

```bash
cd /home/exedev/5mp
curl -L "https://five-megapixel-pipeline-background.exe.xyz:8000/static/downloads/fire_groups_v5.zip" -o /tmp/fire_groups_v5.zip
curl -L "https://five-megapixel-pipeline-background.exe.xyz:8000/static/downloads/fire_narratives_v5.zip" -o /tmp/fire_narratives_v5.zip
```

### 2. Extract and Load

```bash
# Backup first
cp db.sqlite3 db.sqlite3.bak.$(date +%Y%m%d)

# Extract
unzip -o /tmp/fire_groups_v5.zip -d .
unzip -o /tmp/fire_narratives_v5.zip -d .

# Load to database (~3 min)
python3 scripts/load_fire_groups_to_db.py --force
```

### 3. Update Narrative Cache

```bash
python3 scripts/update_narrative_cache.py
```

### 4. Rebuild and Restart

```bash
make build
sudo systemctl restart srv
```

## Cron Job Setup

Daily fire updates run at 3am UTC:

```bash
# Add to crontab
crontab -e

# Add this line:
0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py --days 7 >> logs/daily_fire.log 2>&1
```

Or one-liner:
```bash
(crontab -l 2>/dev/null | grep -v daily_fire_update; echo "0 3 * * * cd /home/exedev/5mp && python3 scripts/daily_fire_update.py --days 7 >> logs/daily_fire.log 2>&1") | crontab -
```

## Verification

```bash
# Check latest year in narratives
curl -s "http://localhost:8000/api/parks/CAF_Chinko/fire-narrative?pwd=test2026" | jq '.trend.years[-1]'
# Should show: 2025

# Check trajectory counts by year
sqlite3 db.sqlite3 "SELECT json_extract(metadata,'$.year') as year, count(*) FROM feature_geometries WHERE type='fire_trajectory' GROUP BY year ORDER BY year"
```

## Rollback

```bash
# Restore database from backup
cp db.sqlite3.bak.YYYYMMDD db.sqlite3
sudo systemctl restart srv
```
