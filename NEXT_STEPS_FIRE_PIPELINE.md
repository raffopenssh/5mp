# Next Steps: Process Backfilled Fire Data

## Current Status

✅ **Backfill Complete:** 9,942 fires downloaded (2025-12-27 to 2026-02-24)
✅ **Data Location:** `data/fire_nrt/{park_id}_nrt.json` (82 parks)
✅ **Code Updated:** SP dataset auto-selection, 30km default buffer

## What Needs to Run

The downloaded NRT fire data needs to be processed through the v5 pipeline:

### Option 1: Full V5 Pipeline (Recommended for Clean Integration)

```bash
# This will process ALL fire data from 2020-current with new fires integrated
cd /home/exedev/5mp

# 1. Rebuild fire trajectories v5 (processes all available fire data)
python3 scripts/rebuild_fire_trajectories_v5.py

# 2. Load to database
python3 scripts/load_fire_groups_to_db.py --force

# 3. Update narratives  
python3 scripts/precompute_narratives_v5.py
```

**Duration:** ~30-60 minutes for full rebuild

### Option 2: Incremental Pipeline (Faster, But May Need Tweaking)

The incremental mode should process only affected parks from the last 60 days:

```bash
cd /home/exedev/5mp

# Process only parks with new data
python3 scripts/rebuild_fire_trajectories_v5.py --incremental
python3 scripts/load_fire_groups_to_db.py --incremental  
python3 scripts/precompute_narratives_v5.py --incremental
```

**Duration:** ~5-10 minutes

**Note:** The incremental scripts may need the raw VIIRS data files updated. If errors occur, use Option 1.

### Option 3: Daily Fire Update (For Next Time)

Once the backfill is integrated, the daily cron will handle ongoing updates:

```bash
# Runs automatically at 3am UTC via cron
python3 scripts/daily_fire_update.py --days 5
```

This downloads last 5 days from FIRMS NRT API and processes incrementally.

## Parks with New Fires (Top 20)

| Park | Fires | Inside | Buffer |
|------|-------|--------|--------|
| TCD_Aouk | 1365 | 358 | 1007 |
| CAF_Manovo_Gounda_St_Floris | 1046 | 219 | 827 |
| CAF_Bamingui-Bangoran | 961 | 156 | 805 |
| GHA_Mole | 738 | 87 | 651 |
| SSD_Southern | 635 | 281 | 354 |
| SSD_Zeraf | 448 | 0 | 448 |
| SDN_Dinder | 340 | 42 | 298 |
| CIV_Comoe | 340 | 285 | 55 |
| SSD_Boma | 274 | 51 | 223 |
| CMR_Boumba-Djombi | 253 | 35 | 218 |
| CMR_Bouba_Ndjida | 253 | 35 | 218 |
| COD_Bili-Uere | 225 | 34 | 191 |
| ETH_Gambella | 221 | 0 | 221 |
| CMR_Lobéké | 218 | 58 | 160 |
| CAF_Chinko | 193 | 8 | 185 |
| BEN_W_Benin | 160 | 93 | 67 |
| KEN_Tsavo_East | 121 | 0 | 121 |
| NAM_Bwabwata | 110 | 0 | 110 |
| MOZ_Niassa | 108 | 0 | 108 |
| BEN_Pendjari | 91 | 8 | 83 |

**Total:** 82 parks with new fire data

## Dependencies

If you encounter "ModuleNotFoundError: No module named 'sklearn'":

```bash
pip3 install scikit-learn shapely geopandas
```

## Verification After Pipeline

```bash
# Check database has new 2026 data
sqlite3 db.sqlite3 "SELECT COUNT(*), json_extract(properties_json, '$.year') 
FROM feature_geometries 
WHERE feature_type='fire_trajectory' 
GROUP BY json_extract(properties_json, '$.year')"

# Check narratives updated
curl -s "http://localhost:8000/api/parks/CAF_Chinko/fire-narrative?pwd=test2026" | jq '.trend.years[-1]'

# Should show 2026 data now
```

## Known Issues

1. **Longitude 0 Error:** The backfill should fix this - verify no fires have lon=0
2. **FIRMS API Unreachable:** That's why we did backfill - daily cron will resume when available
3. **Disk Space:** Monitor `df -h` - pipeline generates temp files

## Files Generated

- `data/fire_nrt/*_nrt.json` - Downloaded NRT fires (9.3MB total)
- `data/fire_groups_v5/*.json` - Will be updated with new trajectories  
- `data/export/fire_narratives/*.json` - Will include 2026 data

---
**Ready to run when you have time!**
