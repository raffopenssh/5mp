# Continuation Instructions

## Current State (2026-01-27)

### Fire Group Infraction Analysis - COMPLETED

New analysis in `scripts/analyze_group_infractions.py`:

**Key Metrics:**
- `days_burning_inside`: How long fires detected inside PA
- `groups_transited`: Groups that passed through PA, continued burning outside (NO staff contact)
- `groups_stopped_inside`: Fires stopped inside PA (possible staff contact/intervention)
- Full trajectory: origin → entry → inside → exit → destination
- Cross-border tracking with 300km buffer

**Interpretation:**
- `STOPPED_INSIDE` = Good sign - rangers may have contacted the group
- `TRANSITED` = Bad sign - group burned through PA with no intervention
- Compare parks on these metrics for management effectiveness

**Sample Results (Chinko 2023):**
- 18 groups entered PA
- 12 stopped inside (67%) - potential ranger contact
- 6 transited (33%) - no intervention
- Avg 9.9 days burning inside

### Database Tables

```sql
-- Group-based trajectory analysis
park_group_infractions:
  - total_groups, transhumance_groups, herder_groups
  - avg_days_burning, median_days_burning, max_days_burning
  - groups_transited, groups_stopped_inside, groups_stopped_after
  - trajectories_json (full trajectory data with origin/dest)

-- Simple fire count analysis  
park_fire_analysis:
  - total_fires, dry_season_fires
  - total_infractions, infraction_rate
  - monthly_stats_json
```

### Fire Data Available

Downloaded 2022-2024 for African countries in `data/fire/viirs-jpss1/{year}/`
- Tanzania, Kenya, Ethiopia, Zambia, Zimbabwe, Mozambique
- Botswana, Namibia, South Africa, CAR, DRC, Angola
- Cameroon, Chad, Uganda, Rwanda, Gabon

### TODO

1. **Run full analysis** - Currently only 10 park-years done
   ```bash
   python3 scripts/analyze_group_infractions.py  # Takes ~10 min
   ```

2. **Add to park modal** - Show group infraction stats in globe.html popup
   - Groups entered this year
   - % that stopped inside (response rate)
   - Avg days burning inside

3. **OSM Roads / Roadless** - Not started
   - Use Overpass API to fetch roads
   - Calculate % area >1km from roads

4. **Keep park_analysis.html** but make it match globe.html style
   - Or just enhance the park modal with expandable details

### Key Files
- `scripts/analyze_group_infractions.py` - Group trajectory analysis
- `scripts/fire_group_detection.py` - DBSCAN clustering + trajectory linking
- `scripts/update_fire_infractions.py` - Simple fire count analysis

### Git
Push before ending: `git push origin main`
