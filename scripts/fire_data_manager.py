#!/usr/bin/env python3
"""
Fire Data Manager for 5MP Conservation Globe

Downloads and manages NASA VIIRS fire detection data for protected areas.
- Bulk historical data from FIRMS country archive (2018-2024)
- Recent data via API (2025+)
- Stores in SQLite database
- Tracks herder group movements
- Identifies infractions (fires inside PAs)
"""

import os
import sys
import json
import sqlite3
import requests
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np
from sklearn.cluster import DBSCAN
from collections import defaultdict

# Configuration
NASA_API_KEY = "d20648f156456e42dacd1e5bf48a64c0"
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data" / "fire"
DB_PATH = BASE_DIR / "db.sqlite3"

# Countries for transhumance tracking (CAR and neighbors)
TRANSHUMANCE_COUNTRIES = [
    "Central_African_Republic",
    "Sudan",
    "South_Sudan", 
    "Chad",
    "Cameroon",
    "Democratic_Republic_of_the_Congo"
]

# Years available for bulk download
BULK_YEARS = range(2018, 2025)

# FIRMS API limits
API_RATE_LIMIT = 10  # requests per minute
API_MAX_DAYS = 10    # max days per request

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class FireDataManager:
    """Manages NASA VIIRS fire detection data"""
    
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.keystones = self._load_keystones()
        
    def _load_keystones(self):
        """Load keystone protected areas"""
        keystones_path = BASE_DIR / "data" / "keystones_with_boundaries.json"
        if keystones_path.exists():
            with open(keystones_path) as f:
                return json.load(f)
        # Fallback to basic
        keystones_path = BASE_DIR / "data" / "keystones_basic.json"
        with open(keystones_path) as f:
            return json.load(f)
    
    def get_db_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    # ==================== BULK DATA DOWNLOAD ====================
    
    def download_bulk_country_data(self, country, year, force=False):
        """
        Download annual VIIRS data for a country from FIRMS archive.
        Files are ~40-100MB per country per year.
        """
        filename = f"viirs-jpss1_{year}_{country}.csv"
        filepath = self.data_dir / filename
        
        if filepath.exists() and not force:
            logger.info(f"Already have {filename}")
            return filepath
        
        url = f"https://firms.modaps.eosdis.nasa.gov/data/country/viirs-jpss1/{year}/{filename}"
        logger.info(f"Downloading {url}...")
        
        try:
            response = requests.get(url, stream=True, timeout=300)
            response.raise_for_status()
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            logger.info(f"Downloaded {filename} ({filepath.stat().st_size / 1024 / 1024:.1f} MB)")
            return filepath
        except Exception as e:
            logger.error(f"Failed to download {filename}: {e}")
            return None
    
    def download_all_bulk_data(self, countries=None, years=None):
        """
        Download all bulk historical data.
        This is a background task - may take hours.
        """
        countries = countries or TRANSHUMANCE_COUNTRIES
        years = years or BULK_YEARS
        
        total = len(countries) * len(years)
        done = 0
        
        for country in countries:
            for year in years:
                done += 1
                logger.info(f"Progress: {done}/{total}")
                self.download_bulk_country_data(country, year)
                time.sleep(1)  # Be nice to the server
    
    # ==================== API DATA DOWNLOAD ====================
    
    def fetch_api_data(self, bbox, start_date, end_date):
        """
        Fetch fire data via NASA FIRMS API for a bounding box.
        bbox: (west, south, east, north)
        """
        west, south, east, north = bbox
        area = f"{west},{south},{east},{north}"
        
        all_data = []
        current_date = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        
        while current_date <= end:
            days = min(API_MAX_DAYS, (end - current_date).days + 1)
            date_str = current_date.strftime("%Y-%m-%d")
            
            url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{NASA_API_KEY}/VIIRS_NOAA20_NRT/{area}/{days}/{date_str}"
            
            try:
                response = requests.get(url, timeout=60)
                response.raise_for_status()
                
                if response.text.strip():
                    lines = response.text.strip().split('\n')
                    if len(lines) > 1:  # Has data beyond header
                        all_data.extend(lines[1:])  # Skip header
                        logger.info(f"Fetched {len(lines)-1} fires for {date_str}")
                
                time.sleep(60 / API_RATE_LIMIT)  # Rate limiting
                
            except Exception as e:
                logger.error(f"API error for {date_str}: {e}")
            
            current_date += timedelta(days=days)
        
        return all_data
    
    def update_park_via_api(self, park_id, buffer_km=50):
        """
        Update fire data for a specific park using the API.
        Only fetches data newer than what we have.
        """
        park = next((p for p in self.keystones if p['id'] == park_id), None)
        if not park:
            logger.error(f"Park not found: {park_id}")
            return
        
        # Get park bbox with buffer
        lat, lon = park['coordinates']['lat'], park['coordinates']['lon']
        # Rough conversion: 1 degree ≈ 111 km
        buffer_deg = buffer_km / 111
        bbox = (lon - buffer_deg, lat - buffer_deg, lon + buffer_deg, lat + buffer_deg)
        
        # Check what data we already have
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT last_date FROM fire_data_sync WHERE park_id = ?",
            (park_id,)
        )
        row = cursor.fetchone()
        
        if row and row['last_date']:
            start_date = (datetime.strptime(row['last_date'], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            # Start from beginning of 2025 (bulk covers 2024)
            start_date = "2025-01-01"
        
        end_date = datetime.now().strftime("%Y-%m-%d")
        
        if start_date > end_date:
            logger.info(f"{park_id} is up to date")
            return
        
        logger.info(f"Fetching {park_id} data from {start_date} to {end_date}")
        data = self.fetch_api_data(bbox, start_date, end_date)
        
        if data:
            self._insert_fire_data(data, park_id)
        
        # Update sync record
        cursor.execute("""
            INSERT OR REPLACE INTO fire_data_sync 
            (park_id, bbox_west, bbox_south, bbox_east, bbox_north, buffer_km, last_date, last_sync_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (park_id, *bbox, buffer_km, end_date, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    # ==================== DATA LOADING ====================
    
    def load_bulk_file_to_db(self, filepath, park_filter=None):
        """
        Load a bulk CSV file into the database.
        Optionally filter to only data relevant to specific parks.
        """
        logger.info(f"Loading {filepath}...")
        
        df = pd.read_csv(filepath)
        logger.info(f"Read {len(df)} fire detections")
        
        if park_filter:
            # Filter to parks of interest (with buffer)
            filtered_dfs = []
            for park_id in park_filter:
                park = next((p for p in self.keystones if p['id'] == park_id), None)
                if not park:
                    continue
                lat, lon = park['coordinates']['lat'], park['coordinates']['lon']
                buffer = 0.5  # ~50km in degrees
                mask = (
                    (df['latitude'] >= lat - buffer) & 
                    (df['latitude'] <= lat + buffer) &
                    (df['longitude'] >= lon - buffer) &
                    (df['longitude'] <= lon + buffer)
                )
                filtered_dfs.append(df[mask].copy())
            
            if filtered_dfs:
                df = pd.concat(filtered_dfs).drop_duplicates()
            else:
                df = pd.DataFrame()
        
        if len(df) == 0:
            logger.info("No relevant data found")
            return
        
        # Insert into database
        conn = self.get_db_connection()
        
        # Prepare data for insertion
        records = []
        for _, row in df.iterrows():
            # Calculate grid cell ID (0.1 degree resolution)
            lat_cell = round(row['latitude'] * 10) / 10
            lon_cell = round(row['longitude'] * 10) / 10
            grid_cell_id = f"{lat_cell:.1f}_{lon_cell:.1f}"
            
            records.append((
                row['latitude'],
                row['longitude'],
                row.get('bright_ti4', row.get('brightness')),
                row['scan'],
                row['track'],
                row['acq_date'],
                str(row['acq_time']).zfill(4),
                row['satellite'],
                row['instrument'],
                row['confidence'],
                row.get('version', ''),
                row.get('bright_ti5', row.get('bright_t31')),
                row['frp'],
                row['daynight'],
                grid_cell_id
            ))
        
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT OR IGNORE INTO fire_detections
            (latitude, longitude, brightness, scan, track, acq_date, acq_time,
             satellite, instrument, confidence, version, bright_t31, frp, daynight, grid_cell_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, records)
        
        conn.commit()
        logger.info(f"Inserted {cursor.rowcount} new records")
        conn.close()
    
    def _insert_fire_data(self, csv_lines, park_id=None):
        """Insert fire data from CSV lines into database"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        for line in csv_lines:
            parts = line.split(',')
            if len(parts) < 14:
                continue
            
            lat, lon = float(parts[0]), float(parts[1])
            lat_cell = round(lat * 10) / 10
            lon_cell = round(lon * 10) / 10
            grid_cell_id = f"{lat_cell:.1f}_{lon_cell:.1f}"
            
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO fire_detections
                    (latitude, longitude, brightness, scan, track, acq_date, acq_time,
                     satellite, instrument, confidence, version, bright_t31, frp, daynight, grid_cell_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    lat, lon,
                    float(parts[2]) if parts[2] else None,
                    float(parts[3]) if parts[3] else None,
                    float(parts[4]) if parts[4] else None,
                    parts[5],
                    parts[6],
                    parts[7],
                    parts[8],
                    parts[9],
                    parts[10],
                    float(parts[11]) if parts[11] else None,
                    float(parts[12]) if parts[12] else None,
                    parts[13],
                    grid_cell_id
                ))
            except Exception as e:
                logger.warning(f"Failed to insert: {e}")
        
        conn.commit()
        conn.close()
    
    # ==================== PA BOUNDARY MATCHING ====================
    
    def mark_fires_in_protected_areas(self):
        """
        Update fire_detections to mark which fires are inside protected areas.
        Uses point-in-polygon test against WDPA boundaries.
        """
        from shapely.geometry import Point, shape
        
        # Load boundaries
        boundaries_path = BASE_DIR / "data" / "keystones_with_boundaries.json"
        if not boundaries_path.exists():
            logger.error("No boundaries file found")
            return
        
        with open(boundaries_path) as f:
            keystones = json.load(f)
        
        # Build spatial index of PA polygons
        pa_polygons = []
        for pa in keystones:
            if 'boundary' in pa and pa['boundary']:
                try:
                    geom = shape(pa['boundary'])
                    pa_polygons.append((pa['id'], geom, geom.bounds))
                except Exception as e:
                    logger.warning(f"Failed to parse boundary for {pa['id']}: {e}")
        
        logger.info(f"Loaded {len(pa_polygons)} PA boundaries")
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        # Get fires without PA assignment
        cursor.execute("""
            SELECT id, latitude, longitude 
            FROM fire_detections 
            WHERE in_protected_area IS NULL OR in_protected_area = 0
            LIMIT 100000
        """)
        fires = cursor.fetchall()
        
        logger.info(f"Checking {len(fires)} fires...")
        
        updates = []
        for fire in fires:
            point = Point(fire['longitude'], fire['latitude'])
            
            for pa_id, polygon, bounds in pa_polygons:
                # Quick bounds check first
                if not (bounds[0] <= fire['longitude'] <= bounds[2] and 
                        bounds[1] <= fire['latitude'] <= bounds[3]):
                    continue
                
                if polygon.contains(point):
                    updates.append((1, pa_id, fire['id']))
                    break
            else:
                updates.append((0, None, fire['id']))
        
        # Batch update
        cursor.executemany("""
            UPDATE fire_detections 
            SET in_protected_area = ?, protected_area_id = ?
            WHERE id = ?
        """, updates)
        
        conn.commit()
        logger.info(f"Updated {len(updates)} fire records")
        conn.close()
    
    # ==================== GROUP MOVEMENT TRACKING ====================
    
    def analyze_herder_movements(self, park_id, year):
        """
        Analyze fire patterns to identify herder group movements.
        Uses DBSCAN clustering on spatial-temporal fire data.
        
        Returns group trajectories with:
        - Group ID (e.g., "Chinko_2023_G1")
        - Daily positions (as LineString)
        - Origin/destination
        - Days active
        - Fire count
        - Infractions (fires inside PA)
        """
        park = next((p for p in self.keystones if p['id'] == park_id), None)
        if not park:
            logger.error(f"Park not found: {park_id}")
            return []
        
        lat, lon = park['coordinates']['lat'], park['coordinates']['lon']
        buffer = 0.5  # ~50km
        
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        # Get fire data for the dry season (Nov-Mar is peak transhumance)
        cursor.execute("""
            SELECT id, latitude, longitude, acq_date, acq_time, frp, 
                   in_protected_area, protected_area_id
            FROM fire_detections
            WHERE latitude BETWEEN ? AND ?
              AND longitude BETWEEN ? AND ?
              AND acq_date LIKE ?
            ORDER BY acq_date, acq_time
        """, (lat - buffer, lat + buffer, lon - buffer, lon + buffer, f"{year}%"))
        
        fires = cursor.fetchall()
        conn.close()
        
        if len(fires) < 100:
            logger.info(f"Not enough fire data for {park_id} in {year}")
            return []
        
        logger.info(f"Analyzing {len(fires)} fires for {park_id} in {year}")
        
        # Convert to DataFrame for analysis
        df = pd.DataFrame([dict(f) for f in fires])
        df['date'] = pd.to_datetime(df['acq_date'])
        df['day_of_year'] = df['date'].dt.dayofyear
        
        # Normalize coordinates and time for clustering
        # Scale: ~10km spatial, 1 day temporal
        coords = df[['latitude', 'longitude', 'day_of_year']].values.copy()
        coords[:, 0] *= 10  # lat: 0.1 deg ≈ 11km -> scale to ~1km units
        coords[:, 1] *= 10  # lon
        coords[:, 2] *= 0.3  # time: 3 days ~ equivalent to 10km movement
        
        # DBSCAN clustering
        # eps=3 means ~30km spatial or ~10 days temporal separation
        clustering = DBSCAN(eps=3, min_samples=20).fit(coords)
        df['cluster'] = clustering.labels_
        
        # Filter out noise (-1)
        df_clustered = df[df['cluster'] >= 0]
        
        # Analyze each cluster (potential herder group)
        groups = []
        for cluster_id in df_clustered['cluster'].unique():
            cluster_df = df_clustered[df_clustered['cluster'] == cluster_id]
            
            # Get daily centroids for trajectory
            daily = cluster_df.groupby('acq_date').agg({
                'latitude': 'mean',
                'longitude': 'mean',
                'id': 'count',
                'frp': 'sum',
                'in_protected_area': 'sum'
            }).rename(columns={'id': 'fire_count', 'in_protected_area': 'infractions'})
            daily = daily.reset_index()
            daily = daily.sort_values('acq_date')
            
            if len(daily) < 3:
                continue
            
            # Calculate trajectory
            trajectory = [
                [row['longitude'], row['latitude']] 
                for _, row in daily.iterrows()
            ]
            
            # Origin and destination
            origin = {'lat': daily.iloc[0]['latitude'], 'lon': daily.iloc[0]['longitude']}
            destination = {'lat': daily.iloc[-1]['latitude'], 'lon': daily.iloc[-1]['longitude']}
            
            # Determine direction (N->S or S->N)
            lat_change = destination['lat'] - origin['lat']
            direction = "southward" if lat_change < -0.1 else ("northward" if lat_change > 0.1 else "stationary")
            
            # Assign group name
            group_name = f"{park_id}_{year}_G{cluster_id + 1}"
            
            groups.append({
                'group_id': group_name,
                'year': year,
                'park_id': park_id,
                'cluster_id': cluster_id,
                'start_date': daily.iloc[0]['acq_date'],
                'end_date': daily.iloc[-1]['acq_date'],
                'days_active': len(daily),
                'total_fires': int(daily['fire_count'].sum()),
                'total_frp': float(daily['frp'].sum()),
                'total_infractions': int(daily['infractions'].sum()),
                'direction': direction,
                'origin': origin,
                'destination': destination,
                'trajectory': {
                    'type': 'LineString',
                    'coordinates': trajectory
                },
                'daily_positions': daily.to_dict('records')
            })
        
        logger.info(f"Identified {len(groups)} herder groups for {park_id} in {year}")
        return groups
    
    def get_infractions_table(self, park_id, year=None):
        """
        Get a table of all infractions (fires inside PA) with group assignments.
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        query = """
            SELECT acq_date, COUNT(*) as fire_count, 
                   SUM(frp) as total_frp,
                   AVG(latitude) as avg_lat, AVG(longitude) as avg_lon
            FROM fire_detections
            WHERE protected_area_id = ?
              AND in_protected_area = 1
        """
        params = [park_id]
        
        if year:
            query += " AND acq_date LIKE ?"
            params.append(f"{year}%")
        
        query += " GROUP BY acq_date ORDER BY acq_date"
        
        cursor.execute(query, params)
        infractions = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return infractions


# ==================== CLI INTERFACE ====================

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Fire Data Manager')
    subparsers = parser.add_subparsers(dest='command')
    
    # Download bulk data
    dl = subparsers.add_parser('download', help='Download bulk data')
    dl.add_argument('--country', help='Specific country')
    dl.add_argument('--year', type=int, help='Specific year')
    
    # Load bulk data to DB
    load = subparsers.add_parser('load', help='Load CSV to database')
    load.add_argument('file', help='CSV file to load')
    load.add_argument('--park', help='Filter to specific park')
    
    # Update via API
    update = subparsers.add_parser('update', help='Update park via API')
    update.add_argument('park_id', help='Park ID')
    
    # Mark PA boundaries
    mark = subparsers.add_parser('mark-pas', help='Mark fires in protected areas')
    
    # Analyze movements
    analyze = subparsers.add_parser('analyze', help='Analyze herder movements')
    analyze.add_argument('park_id', help='Park ID')
    analyze.add_argument('year', type=int, help='Year to analyze')
    
    args = parser.parse_args()
    
    manager = FireDataManager()
    
    if args.command == 'download':
        if args.country and args.year:
            manager.download_bulk_country_data(args.country, args.year)
        else:
            manager.download_all_bulk_data()
    
    elif args.command == 'load':
        parks = [args.park] if args.park else None
        manager.load_bulk_file_to_db(args.file, parks)
    
    elif args.command == 'update':
        manager.update_park_via_api(args.park_id)
    
    elif args.command == 'mark-pas':
        manager.mark_fires_in_protected_areas()
    
    elif args.command == 'analyze':
        groups = manager.analyze_herder_movements(args.park_id, args.year)
        print(json.dumps(groups, indent=2, default=str))
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
