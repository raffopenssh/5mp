# Continuation Instructions for Shelley

## Current State (2026-01-27)

### Completed
1. **Fire Group Detection Algorithm** (`scripts/fire_group_detection.py`)
   - DBSCAN clustering for daily fire clusters
   - Trajectory linking across days
   - Classification: transhumance, herder_local, management, village

2. **Fire Analysis Background Job** (`scripts/fire_analysis_job.py`)
   - Running in background analyzing all parks
   - Check progress: `tail -f /tmp/fire_analysis.log`
   - Results stored in `park_fire_analysis` table

3. **Fire Data Available** (`data/fire/`)
   - CAR 2022-2024
   - Tanzania, Kenya, Ethiopia, Zambia, Zimbabwe, Mozambique, Botswana, Namibia, South Africa 2022-2024
   - More can be extracted from zip files in data/fire/

### TODO - Park Interactive Analysis View

1. **Create park_analysis.html template**
   - Use globe.html as base but focused on single park
   - Add fire layer with time slider animation
   - Show fire statistics, transhumance groups
   - Filter by date range, fire type
   - Add "Analyze Fire" button to existing PA popup

2. **Add API endpoints needed**:
   ```go
   GET /api/park/{id}/fires?year=2023 - Fire data for park
   GET /api/park/{id}/analysis - Fire analysis results
   GET /api/park/{id}/roads - Road data for roadless calculation
   ```

3. **Park Modal Enhancement**
   - Add button to open interactive analysis view
   - Show fire statistics summary
   - Link to papers, legal texts

### TODO - Roadless Area Analysis

User referenced paper: "A global map of roadless areas and their conservation status"

1. **Get road data**: Use OpenStreetMap via Overpass API
   ```
   [out:json];
   way["highway"](bbox);
   out geom;
   ```

2. **Calculate roadless ratio**:
   - Buffer roads by 1km (or configurable)
   - Cut buffered roads from park polygon
   - Calculate remaining area / total area
   - Store in database

3. **Background job** for roadless calculation for all parks

### NASA FIRMS API
- VM cannot reach NASA FIRMS servers (blocked/timeout)
- Use Google Drive for data: User can share files
- gdown installed in .venv for downloading

### Key Files
- `scripts/fire_group_detection.py` - Core algorithm
- `scripts/fire_analysis_job.py` - Background job
- `data/keystones_basic.json` - Park list
- `data/keystones_with_boundaries.json` - Park boundaries
- `docs/FIRE_ANALYSIS_CHINKO.md` - Analysis documentation

### Running Services
- Server: `./server` on port 8000
- Fire analysis job: Check if still running with `pgrep -f fire_analysis`

### Database Tables
- `park_fire_analysis` - Fire analysis results per park/year
- `fire_detections` - Individual fire points (schema exists, not populated)
- `fire_data_sync` - Track what data has been fetched

### Git
- Repo: https://github.com/raffopenssh/5mp.git
- Remote: github
- Always commit and push before ending session
