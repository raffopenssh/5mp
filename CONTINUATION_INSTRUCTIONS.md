# Continuation Instructions

## Current State (2026-01-28)

### Data Processing Complete

**Fire Data:**
- 1,764,155 fire detections stored (inside-park fires only)
- 398 park-group infractions analyzed (with trajectory JSON)
- Years: 2018-2024 processed
- Raw CSVs deleted to save space (5GB freed)

**GHSL Settlement Data:**
- 155 parks processed (limited by available tiles)
- Only 5 of 30 needed tiles available
- Settlements detected: varies by park coverage

**OSM Roadless:**
- 3 parks processed (analysis ongoing or interrupted)
- Script: `scripts/osm_roadless_analysis.py`

### Streaming Processors (Memory Efficient)

New scripts that process directly from ZIP files without full extraction:

1. **Fire Processor**: `scripts/fire_processor_streaming.py`
   - Streams fires from ZIP, processes per-park
   - Deletes ZIP after processing
   - Usage: `python scripts/fire_processor_streaming.py --zip /path/to/viirs.zip`

2. **GHSL Processor**: `scripts/ghsl_processor_streaming.py`
   - Extracts TIF to temp dir, processes, deletes
   - Usage: `python scripts/ghsl_processor_streaming.py --zip /path/to/tile.zip`

### Admin Upload Interface

- `/admin` page has bulk upload for VIIRS CSV and GHSL ZIP
- Uploads trigger background processing via Python scripts
- Files deleted after processing to save disk space
- Shows needed GHSL tile download links

### Park Stats API

`GET /api/parks/{id}/stats` returns:
- Fire activity with trajectory insights
- Settlement data (where available)
- Roadless percentage (where available)
- Narrative insights based on data

### Database Schema

Key tables:
```sql
fire_detections (1.7M rows) - individual fires inside parks
park_group_infractions (398 rows) - fire group analysis with trajectories_json
ghsl_data (155 rows) - settlement data per park
osm_roadless_data (3 rows) - roadless analysis
```

### TODO

1. **Resume OSM roadless analysis** - only 3 parks done
   ```bash
   source .venv/bin/activate
   nohup python scripts/osm_roadless_analysis.py > logs/osm_roadless.log 2>&1 &
   ```

2. **Get more GHSL tiles** - 25 needed, JRC server blocked from this network
   - Admin page shows download links for manual download
   - Upload via admin interface after downloading elsewhere

3. **Fire data 2025** - need FIRMS API access or COTS proxy
   - Current data ends at 2024

4. **Improve park modal** - show insights as collapsible narrative text

### Key Files

- `srv/park_stats_handlers.go` - park stats API with insights
- `srv/admin_handlers.go` - bulk upload handlers
- `scripts/fire_processor_streaming.py` - memory-efficient fire processing
- `scripts/ghsl_processor_streaming.py` - memory-efficient GHSL processing
- `scripts/osm_roadless_analysis.py` - roadless wilderness analysis

### Git

Commit frequently: `git add -A && git commit -m "message" && git push github main`
