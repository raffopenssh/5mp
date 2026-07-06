# WorldClim 2.1 Integration for Patrol Intensity

## Overview
Integrated WorldClim 2.1 monthly precipitation data to accurately classify dry/rainy months for each grid cell, accounting for actual local climate conditions.

## Implementation

### Data Source
- **WorldClim 2.1** - 2.5 minute resolution (~4.6km at equator)
- **Downloaded**: `wc2.1_2.5m_prec.zip` (69MB, 12 monthly GeoTIFF files)
- **URL**: https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_2.5m_prec.zip

### Processing Pipeline
1. **Extract per grid cell** (`scripts/extract_worldclim_grid.py`):
   - Queries all 58 grid cells from database
   - Reads precipitation from 12 monthly GeoTIFF files
   - Outputs `data/worldclim/grid_precip.json`

2. **Load on startup** (`cmd/srv/main.go`):
   - Loads JSON into global `GridPrecipData` struct
   - Thread-safe with RWMutex

3. **Classify months** (`srv/worldclim.go`):
   - Threshold: **< 50mm = dry**, **≥ 50mm = rainy**
   - Accounts for off-road accessibility (flooding/mud)

### Intensity Calculation

**3-Tier Fallback**:
1. **WorldClim data** (most accurate) - per grid cell precipitation
2. **Park climate** - from park_climate table
3. **Default** - Nov-Apr dry, May-Oct rainy

**Formula** (unchanged):
```
intensity = (dry_visits + rainy_visits * 0.3) / expected_dry_months
```

**Example - Grid 10.0_18.2 near Zakouma**:
```json
Monthly precip (mm): [0, 1, 4, 29, 73, 120, 209, 233, 159, 54, 3, 0]

Classification:
- Dry months (<50mm): Jan, Feb, Mar, Apr, Nov, Dec = 7 months
- Rainy months (≥50mm): May, Jun, Jul, Aug, Sep, Oct = 5 months

Intensity:
- Expected: 7 dry months
- Visited: 2 dry months
- Result: 2/7 = 0.286
```

**Before WorldClim** (hardcoded):
- All cells: 6 expected dry months (Nov-Apr)
- Cell visited 1 month: 1/6 = 0.167

**After WorldClim** (actual climate):
- Cell 10.0_18.2: 7 expected dry months (actual data)
- Cell visited 2 months: 2/7 = 0.286

### Why 50mm Threshold?

**Conservation patrol logistics**:
- **< 50mm/month**: Roads passable, foot/vehicle patrols viable
- **≥ 50mm/month**: Flooding/mud, off-road access limited
- **Aircraft**: Still works (hence 30% weight for rainy visits)

This reflects real constraints rangers face during wet season.

### Files Modified

| File | Purpose |
|------|---------|
| `srv/worldclim.go` | Load/classify precipitation data |
| `srv/worldclim_intensity.go` | Calculate intensity with WorldClim |
| `srv/api.go` | Use WorldClim in export |
| `cmd/srv/main.go` | Load WorldClim on startup |
| `scripts/extract_worldclim_grid.py` | Extract grid cell precip |
| `data/worldclim/grid_precip.json` | Monthly precip for 58 cells |

### Benefits

1. **Accurate per-pixel classification**: Each grid cell uses its own climate
2. **Handles regional variations**: Not all areas have Nov-Apr dry season
3. **Accounts for distance**: Grid cells 30km from park get local data
4. **Respects access constraints**: Threshold based on patrol logistics
5. **Maintains existing logic**: Same intensity formula, better inputs

### Coverage

- **Current**: 58 grid cells with patrol data
- **Expandable**: Can extract more cells as patrol coverage grows
- **Resolution**: 2.5 arcminute (~4.6km) - adequate for 0.1° grid cells

### Testing

```bash
# Check grid cell classification
curl "http://localhost:8000/api/export/patrol-pixels?parks=TCD_Zakouma&pwd=test2026" | \
  jq '.pixels | group_by(.lat) | map({lat: .[0].lat, intensity: .[0].intensity})'

# Result: Varying intensities (0.143 to 0.286)
# Previously: All 0.167 (same expected months)
```

## Future Enhancements

1. **More grid cells**: Extract WorldClim data as patrol coverage expands
2. **Higher resolution**: Use 30s data if needed (1km resolution)
3. **Dynamic thresholds**: Adjust 50mm based on terrain type
4. **Seasonal updates**: Account for climate change trends

## Commits

- **e8b97ebe**: Integrate WorldClim 2.1 precipitation data
- **7ae30ef2**: Use park climate data (replaced by WorldClim)
- **bfac01d9**: Fix intensity to match map

## Related

- WorldClim 2.1 docs: https://www.worldclim.org/data/worldclim21.html
- Intensity calculation: `srv/grid_query.go` (map uses same logic)
- KML export: Uses same patrol data
- CSV export: Already has patrol columns
