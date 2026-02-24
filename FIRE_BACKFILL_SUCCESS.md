# Fire NRT Backfill - SUCCESSFUL - 2026-02-24

## Status: ✅ IN PROGRESS (Working!)

### Key Fix Applied

**Problem:** FIRMS NRT API only provides last 10 days of data. Historical backfill requires Standard Processing (SP) dataset.

**Solution:** Auto-detect dataset based on time range:
- **≤10 days:** Use `VIIRS_SNPP_NRT` (Near Real-Time)
- **>10 days:** Use `VIIRS_SNPP_SP` (Standard Processing)

### Code Changes

Modified `scripts/fire_nrt/download_nrt.py`:
```python
def download_fires_from_firms(bbox, days, proxy, source=None):
    # Auto-select source based on days
    if source is None:
        source = "VIIRS_SNPP_NRT" if days <= 10 else "VIIRS_SNPP_SP"
    
    if days <= 10:
        url = f"{BASE_URL}/area/csv/{MAP_KEY}/{source}/{bbox}/{days}"
    else:
        # For historical data, use date range with SP dataset
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        url = f"{BASE_URL}/area/csv/{MAP_KEY}/{source}/{bbox}/1/{start_date}"
```

### Current Progress

**Command:**
```bash
nohup python3 scripts/fire_nrt/download_nrt.py --all --backfill --start 2025-12-27 --end 2026-02-24 \
  > logs/fire_nrt_backfill_sp_20260224_0816.log 2>&1 &
```

**Stats (as of 08:18 UTC):**
- Parks processed: 37 / 162 (22%)
- Total fires downloaded: 4,620
- Fires inside parks: 1,020
- Status: Running smoothly
- PID: 6967
- Proxy: 18.229.170.122:3128
- Dataset: VIIRS_SNPP_SP (Standard Processing)

### Sample Results

| Park | Fires Total | Inside | Buffer |
|------|-------------|--------|--------|
| CAF_Bamingui-Bangoran | 961 | 156 | 805 |
| CAF_Manovo_Gounda_St_Floris | 1046 | 219 | 827 |
| CIV_Comoe | 340 | 285 | 55 |
| CMR_Bouba_Ndjida | 253 | 35 | 218 |
| BEN_W_Benin | 160 | 93 | 67 |
| CAF_Chinko | 193 | 8 | 185 |
| BEN_Pendjari | 91 | 8 | 83 |

### Monitoring

**Watch progress:**
```bash
./monitor_backfill.sh
```

**Live tail:**
```bash
tail -f logs/fire_nrt_backfill_sp_20260224_0816.log
```

**Check completion:**
```bash
grep "Progress:" logs/fire_nrt_backfill_sp_20260224_0816.log | tail -1
```

### ETA

At current rate (~2 seconds per park):
- Remaining: 125 parks
- Time: ~4-5 minutes
- Expected completion: ~08:25 UTC

### Next Steps

Once backfill completes:
1. Run trajectory rebuild for affected parks
2. Load fire groups to database
3. Update narrative cache
4. Verify data quality (no longitude 0 errors)

### Files

- **Log:** `logs/fire_nrt_backfill_sp_20260224_0816.log`
- **Monitor script:** `monitor_backfill.sh`
- **Code:** `scripts/fire_nrt/download_nrt.py`
- **Config:** `scripts/fire_nrt/config.py` (updated proxies)

### Success Criteria

✅ Fires downloading successfully  
✅ Using SP dataset for historical data  
✅ No proxy errors  
✅ Data includes fires inside parks  
🔄 Process running smoothly  
⏳ Completion pending (~5 mins)  

---
**Last Updated:** 2026-02-24 08:18 UTC  
**Status:** Running (22% complete)
