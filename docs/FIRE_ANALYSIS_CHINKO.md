# Chinko Fire Analysis 2022-2024

## Overview

Analysis of NASA VIIRS fire detections in the Chinko Conservation Area (CAR) and surrounding 50km buffer zone for 2022-2024.

## Fire Group Detection Algorithm

The algorithm detects and tracks fire groups by:
1. **Daily Clustering**: DBSCAN clustering (eps=15km, min_samples=8) on each day's fires
2. **Trajectory Linking**: Link clusters across days if centroids within 25km
3. **Classification**: Based on movement speed and pattern

### Classification Types

| Type | Speed | Pattern | Interpretation |
|------|-------|---------|----------------|
| transhumance | 5-15 km/day | Sustained southward | Nomadic herders with cattle |
| herder_local | 5-15 km/day | Variable direction | Local herder activity |
| herder_fast | 15-30 km/day | Short bursts | Rapid herder movement |
| management_vehicle | >15 km/day | Large spread | Vehicle-based burns |
| management_fast | >30 km/day | Very fast | Aircraft burns |
| village_persistent | <3 km/day | Long duration | Settlement activity |
| local_burning | 2-5 km/day | Short duration | Local agricultural burns |

## Key Findings

### Transhumance Patterns

| Year | Groups | Fires | Avg Speed | Avg Distance South |
|------|--------|-------|-----------|-------------------|
| 2022 | 16 | 19,333 | 10.2 km/day | 43 km |
| 2023 | 10 | 17,294 | 9.2 km/day | 35 km |
| 2024 | 13 | 8,850 | 10.8 km/day | 43 km |

### Movement Speed Interpretation

- **5-15 km/day**: Consistent with cattle herding pace
  - Cattle walk ~3-4 km/hour
  - Daily herding typically 4-6 hours of movement
  - Results in 12-24 km/day potential, but camps are set up for grazing
  
- **Peak transhumance months**:
  - 2022: February
  - 2023: January  
  - 2024: February

### Entry/Exit Points

Most transhumance groups enter from:
- **North (>7.0°N)**: Sudan/Chad border region
- Exit through **South (<6.5°N)**: Toward DRC border

## Major Groups Identified

### 2022 Season
- **2022-T1**: 46 days, 7,660 fires, 101km south (largest)
- Pattern: Entered Jan 14, exited Mar 10
- Route: 7.40°N → 6.49°N (northern border to south-central)

### 2023 Season  
- **2023-T1**: 15 days, 6,889 fires, 27km south
- Pattern: Very intense short burst in January
- Concentrated around 24.45°E longitude

### 2024 Season
- **2024-T1**: 23 days, 3,455 fires, 44km south
- Lower intensity than previous years
- December activity increasing (early start to season)

## Infractions (Fires Inside PA)

Fires inside the protected area boundary indicate illegal burning:
- Transhumance groups consistently pass through PA
- Peak infraction periods: Jan-Feb
- 2024 shows reduced activity - possible:
  - Better enforcement
  - Different migration routes
  - Climate/drought changes

## Data Quality Notes

- VIIRS NOAA-20 satellite data
- Detection confidence varies (low/nominal/high)
- Night fires (43% of dry season) indicate active burning during cattle movement
- Fire Radiative Power (FRP) 5-15 MW typical for grass fires

## Files

- `scripts/fire_group_detection.py`: Detection algorithm
- `data/fire/viirs-jpss1_YYYY_Central_African_Republic.csv`: Raw data
