# Continuation Instructions for Shelley

## Current State (2026-01-27)

### Completed
1. **Fire Group Detection Algorithm** (`scripts/fire_group_detection.py`)
   - DBSCAN clustering for daily fire clusters
   - Trajectory linking across days
   - Classification: transhumance, herder_local, management, village

2. **Enhanced Fire Analysis** (`scripts/fire_analysis_enhanced.py`)
   - Infraction detection (fires inside PA boundary using shapely)
   - Fire front latitude tracking by week/month
   - Monthly infraction statistics
   - Verified against Chinko 2023: 12,913 infractions, Feb 15 peak (969)

3. **Park Analysis Page** (`/park/{id}`)
   - Interactive map with fire visualization
   - Fire statistics by year
   - Monthly chart
   - Roadless area placeholder
   - Map layers toggle

4. **Fire Analysis Background Job** (`scripts/fire_analysis_job.py`)
   - 77 parks analyzed with 231 year-records
   - Results stored in `park_fire_analysis` table

5. **Repo Cleanup**
   - Removed .venv, binaries, database files from git
   - Comprehensive .gitignore

### Fire Analysis Key Metrics (Chinko 2023)
- Total fires in bbox: 46,559
- Infractions (inside PA): 12,913 (27.9%)
- Peak day: Feb 15 with 969 fires
- February highest infraction rate: 58.5%
- Net southward movement: 35.9 km

### TODO - Remaining Tasks

1. **OSM Roads Integration**
   - Implement Overpass API query in `HandleParkRoads`
   - Calculate roadless percentage (area >1km from roads)
   - Store results in database
   - Reference paper: "A global map of roadless areas and their conservation status"

2. **Fire Animation Enhancement**
   - Add daily fire layer to park_analysis.html
   - Animate fire detections over time
   - Color by infraction (red) vs buffer (orange)

3. **Update Fire Analysis Job**
   - Run `fire_analysis_enhanced.py` to populate infraction data
   - Migration 007 adds infraction columns

### API Endpoints Available
```
GET /park/{id}                    - Park analysis page
GET /api/park/{id}/fire-analysis  - Fire analysis JSON
GET /api/park/{id}/boundary       - Park boundary GeoJSON
GET /api/park/{id}/roads          - Road data (placeholder)
```

### NASA FIRMS API
- API Key: `d20648f156456e42dacd1e5bf48a64c0`
- VM may not reach NASA servers - use cached data
- Fire data in `data/fire/viirs-jpss1_2023_Central_African_Republic.csv`

### Key Files
- `scripts/fire_analysis_enhanced.py` - Infraction detection
- `scripts/fire_group_detection.py` - Movement pattern analysis
- `srv/park_analysis.go` - Park analysis handlers
- `srv/templates/park_analysis.html` - Interactive park view
- `db/migrations/007-fire-infractions.sql` - New infraction columns

### Database Tables
- `park_fire_analysis` - Fire analysis results per park/year
- `fire_detections` - Individual fire points (schema ready)
- `fire_daily_grid` - Aggregated per grid cell per day

### Git
- Repo: https://github.com/raffopenssh/5mp.git
- Always commit and push before ending session
