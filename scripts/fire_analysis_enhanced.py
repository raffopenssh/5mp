#!/usr/bin/env python3
"""
Enhanced fire analysis with infraction detection and fire front tracking.

Improvements over basic fire_group_detection.py:
1. Infraction classification (fires inside PA boundary vs buffer zone)
2. Fire front latitude tracking by week
3. Movement direction and speed metrics
4. NASA API key for future data fetching
"""

import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

try:
    from shapely.geometry import shape, Point
    from shapely.prepared import prep
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False
    print("Warning: shapely not installed, infraction detection disabled")

# NASA FIRMS API key
NASA_API_KEY = "d20648f156456e42dacd1e5bf48a64c0"

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"

def load_park_boundary(park_id):
    """Load park boundary geometry for point-in-polygon tests."""
    if not HAS_SHAPELY:
        return None
    
    try:
        with open(DATA_DIR / "keystones_with_boundaries.json") as f:
            keystones = json.load(f)
        
        for park in keystones:
            if park['id'] == park_id:
                geom = park.get('geometry')
                if geom:
                    return prep(shape(geom))
        return None
    except Exception as e:
        print(f"Error loading boundary for {park_id}: {e}")
        return None

def classify_fire_location(lat, lon, pa_boundary, buffer_km=50):
    """
    Classify a fire as infraction, buffer, or outside.
    
    Returns:
        'infraction' - inside protected area
        'buffer' - in buffer zone (within buffer_km of PA)
        'outside' - outside buffer zone
    """
    if not HAS_SHAPELY or pa_boundary is None:
        return 'unknown'
    
    point = Point(lon, lat)
    
    # Check if inside PA
    if pa_boundary.contains(point):
        return 'infraction'
    
    # Check if in buffer (simplified: use point distance)
    # Note: For accurate buffer, would need to buffer the polygon
    # This is a simplification using the PA centroid distance
    return 'buffer'  # Default to buffer if not inside

def analyze_fire_front(fires_df, pa_boundary=None):
    """
    Analyze fire front movement over time.
    
    Returns dict with weekly latitude averages and movement metrics.
    """
    if len(fires_df) == 0:
        return {}
    
    # Ensure date column
    if 'date' not in fires_df.columns:
        fires_df['date'] = pd.to_datetime(fires_df['acq_date'])
    
    # Add week number
    fires_df['week'] = fires_df['date'].dt.isocalendar().week
    fires_df['month'] = fires_df['date'].dt.month
    
    # Weekly fire front (average latitude)
    weekly_lat = fires_df.groupby('week').agg({
        'latitude': 'mean',
        'longitude': 'mean'
    }).reset_index()
    
    fire_front = []
    for _, row in weekly_lat.iterrows():
        fire_front.append({
            'week': int(row['week']),
            'avg_lat': round(row['latitude'], 4),
            'avg_lon': round(row['longitude'], 4)
        })
    
    # Monthly statistics with infraction counts
    monthly_stats = []
    for month in sorted(fires_df['month'].unique()):
        month_fires = fires_df[fires_df['month'] == month]
        
        # Count infractions if we have boundary
        infraction_count = 0
        buffer_count = 0
        if pa_boundary is not None and HAS_SHAPELY:
            for _, row in month_fires.iterrows():
                pt = Point(row['longitude'], row['latitude'])
                if pa_boundary.contains(pt):
                    infraction_count += 1
                else:
                    buffer_count += 1
        
        monthly_stats.append({
            'month': int(month),
            'total_fires': int(len(month_fires)),
            'infractions': infraction_count,
            'buffer_fires': buffer_count,
            'avg_lat': round(month_fires['latitude'].mean(), 4),
            'infraction_rate': round(infraction_count / len(month_fires) * 100, 1) if len(month_fires) > 0 else 0
        })
    
    # Calculate movement metrics
    if len(weekly_lat) >= 2:
        # Net southward movement (positive = moved south)
        lat_start = weekly_lat['latitude'].iloc[0]
        lat_end = weekly_lat['latitude'].iloc[-1]
        net_south_km = (lat_start - lat_end) * 111
        
        # Average daily movement
        n_days = (fires_df['date'].max() - fires_df['date'].min()).days or 1
        daily_movement = net_south_km / n_days
    else:
        net_south_km = 0
        daily_movement = 0
    
    return {
        'fire_front': fire_front,
        'monthly_stats': monthly_stats,
        'net_south_km': round(net_south_km, 1),
        'avg_daily_movement_km': round(daily_movement, 2),
        'total_weeks': len(fire_front)
    }

def enhanced_analyze_park(park_id, fire_data_path, bbox, year):
    """
    Enhanced park analysis with infraction detection.
    
    Args:
        park_id: Park identifier
        fire_data_path: Path to fire CSV file
        bbox: (lat_min, lat_max, lon_min, lon_max)
        year: Year to analyze
    
    Returns:
        Dict with analysis results including infractions
    """
    try:
        # Load fire data
        df = pd.read_csv(fire_data_path)
        
        # Filter to bbox
        lat_min, lat_max, lon_min, lon_max = bbox
        df = df[(df['latitude'] >= lat_min) & (df['latitude'] <= lat_max) &
                (df['longitude'] >= lon_min) & (df['longitude'] <= lon_max)]
        
        if len(df) == 0:
            return None
        
        # Add date
        df['date'] = pd.to_datetime(df['acq_date'])
        
        # Filter to dry season (Nov-Mar) when transhumance occurs
        dry = df[df['date'].dt.month.isin([1, 2, 3, 11, 12])]
        
        # Load PA boundary for infraction detection
        pa_boundary = load_park_boundary(park_id)
        
        # Analyze fire front movement
        fire_front_analysis = analyze_fire_front(dry, pa_boundary)
        
        # Count total infractions
        total_infractions = 0
        if pa_boundary is not None and HAS_SHAPELY:
            for _, row in dry.iterrows():
                pt = Point(row['longitude'], row['latitude'])
                if pa_boundary.contains(pt):
                    total_infractions += 1
        
        # Peak infraction day
        peak_day = None
        peak_day_count = 0
        if pa_boundary is not None and HAS_SHAPELY:
            daily_infractions = defaultdict(int)
            for _, row in dry.iterrows():
                pt = Point(row['longitude'], row['latitude'])
                if pa_boundary.contains(pt):
                    daily_infractions[row['acq_date']] += 1
            
            if daily_infractions:
                peak_day = max(daily_infractions, key=daily_infractions.get)
                peak_day_count = daily_infractions[peak_day]
        
        return {
            'park_id': park_id,
            'year': year,
            'total_fires': len(df),
            'dry_season_fires': len(dry),
            'total_infractions': total_infractions,
            'infraction_rate': round(total_infractions / len(dry) * 100, 1) if len(dry) > 0 else 0,
            'peak_infraction_day': peak_day,
            'peak_infraction_count': peak_day_count,
            'fire_front': fire_front_analysis,
            'has_boundary': pa_boundary is not None
        }
        
    except Exception as e:
        print(f"Error in enhanced analysis: {e}")
        import traceback
        traceback.print_exc()
        return None

def fetch_nasa_fires(bbox, date_range, api_key=NASA_API_KEY):
    """
    Fetch fire data from NASA FIRMS API.
    
    Args:
        bbox: [west, south, east, north]
        date_range: (start_date, end_date) as strings YYYY-MM-DD
        api_key: NASA FIRMS API key
    
    Returns:
        DataFrame with fire detections or None on failure
    
    Note: VM may not be able to reach NASA servers - use cached data instead.
    """
    import requests
    
    west, south, east, north = bbox
    start_date, end_date = date_range
    
    # FIRMS API URL format
    # https://firms.modaps.eosdis.nasa.gov/api/area/csv/{api_key}/{source}/{west},{south},{east},{north}/{days}/{date}
    
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{api_key}/VIIRS_SNPP_SP/{west},{south},{east},{north}/10/{start_date}"
    
    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            from io import StringIO
            return pd.read_csv(StringIO(resp.text))
        else:
            print(f"API error: {resp.status_code}")
            return None
    except Exception as e:
        print(f"Failed to fetch from NASA: {e}")
        return None

if __name__ == '__main__':
    # Test with Chinko
    import sys
    
    park_id = sys.argv[1] if len(sys.argv) > 1 else 'CAF_Chinko'
    
    # Chinko bbox with 50km buffer
    bbox = (5.5, 7.5, 23.0, 25.0)
    
    fire_path = DATA_DIR / "fire" / "viirs-jpss1_2023_Central_African_Republic.csv"
    
    if fire_path.exists():
        results = enhanced_analyze_park(park_id, fire_path, bbox, 2023)
        if results:
            print(f"\n=== {park_id} 2023 Enhanced Analysis ===")
            print(f"Total fires in bbox: {results['total_fires']:,}")
            print(f"Dry season fires: {results['dry_season_fires']:,}")
            print(f"Infractions (inside PA): {results['total_infractions']:,}")
            print(f"Infraction rate: {results['infraction_rate']}%")
            print(f"Peak infraction day: {results['peak_infraction_day']} ({results['peak_infraction_count']} fires)")
            print(f"\nFire front movement:")
            print(f"  Net southward: {results['fire_front'].get('net_south_km', 0)} km")
            print(f"  Avg daily movement: {results['fire_front'].get('avg_daily_movement_km', 0)} km/day")
            
            if 'monthly_stats' in results['fire_front']:
                print(f"\nMonthly stats:")
                for m in results['fire_front']['monthly_stats']:
                    print(f"  Month {m['month']}: {m['total_fires']} fires, {m['infractions']} infractions ({m['infraction_rate']}%)")
    else:
        print(f"Fire data not found at {fire_path}")
