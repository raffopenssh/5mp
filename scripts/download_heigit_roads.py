#!/usr/bin/env python3
"""
Download HeiGIT Planet road data for African countries.

Downloads road surface data with rich attributes:
- Surface type (paved/unpaved), highway class
- Passability scores, surface change detection
- Deep learning classifications

Processes country by country for memory efficiency.
Filters roads within park buffers and stores as JSON per park.
"""

import json
import requests
import sys
from pathlib import Path
from shapely.geometry import shape, mapping
from shapely.ops import transform
from shapely.prepared import prep
import pyproj
import time

OUTPUT_DIR = Path(__file__).parent.parent / 'data' / 'roads_heigit'
KEYSTONES_FILE = Path(__file__).parent.parent / 'data' / 'keystones_with_boundaries.json'

# African country codes (ISO 2-letter)
AFRICAN_COUNTRIES = [
    'DZ', 'AO', 'BJ', 'BW', 'BF', 'BI', 'CV', 'CM', 'CF', 'TD',
    'KM', 'CG', 'CD', 'DJ', 'EG', 'GQ', 'ER', 'SZ', 'ET', 'GA',
    'GM', 'GH', 'GN', 'GW', 'CI', 'KE', 'LS', 'LR', 'LY', 'MG',
    'MW', 'ML', 'MR', 'MU', 'MA', 'MZ', 'NA', 'NE', 'NG', 'RW',
    'ST', 'SN', 'SC', 'SL', 'SO', 'ZA', 'SS', 'SD', 'TZ', 'TG',
    'TN', 'UG', 'ZM', 'ZW'
]

# Map country codes to park prefixes
COUNTRY_CODE_TO_PREFIX = {
    'DZ': 'DZA', 'AO': 'AGO', 'BJ': 'BEN', 'BW': 'BWA', 'BF': 'BFA',
    'BI': 'BDI', 'CV': 'CPV', 'CM': 'CMR', 'CF': 'CAF', 'TD': 'TCD',
    'KM': 'COM', 'CG': 'COG', 'CD': 'COD', 'DJ': 'DJI', 'EG': 'EGY',
    'GQ': 'GNQ', 'ER': 'ERI', 'SZ': 'SWZ', 'ET': 'ETH', 'GA': 'GAB',
    'GM': 'GMB', 'GH': 'GHA', 'GN': 'GIN', 'GW': 'GNB', 'CI': 'CIV',
    'KE': 'KEN', 'LS': 'LSO', 'LR': 'LBR', 'LY': 'LBY', 'MG': 'MDG',
    'MW': 'MWI', 'ML': 'MLI', 'MR': 'MRT', 'MU': 'MUS', 'MA': 'MAR',
    'MZ': 'MOZ', 'NA': 'NAM', 'NE': 'NER', 'NG': 'NGA', 'RW': 'RWA',
    'ST': 'STP', 'SN': 'SEN', 'SC': 'SYC', 'SL': 'SLE', 'SO': 'SOM',
    'ZA': 'ZAF', 'SS': 'SSD', 'SD': 'SDN', 'TZ': 'TZA', 'TG': 'TGO',
    'TN': 'TUN', 'UG': 'UGA', 'ZM': 'ZMB', 'ZW': 'ZWE'
}

BUFFER_KM = 50  # Buffer around parks in km

class RoadDownloader:
    def __init__(self):
        self.parks = self._load_parks()
        self.park_buffers = {}
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        
    def _load_parks(self):
        """Load park boundaries from keystones file"""
        if not KEYSTONES_FILE.exists():
            print(f"ERROR: {KEYSTONES_FILE} not found")
            return []
        
        with open(KEYSTONES_FILE) as f:
            data = json.load(f)
        
        parks = []
        for p in data:
            if p.get('geometry'):
                parks.append({
                    'id': p['id'],
                    'country_code': p['id'].split('_')[0],
                    'geometry': p['geometry']
                })
        
        print(f"Loaded {len(parks)} parks with boundaries")
        return parks
    
    def _create_buffer(self, geometry, buffer_km):
        """Create a buffer around a geometry in km"""
        try:
            geom = shape(geometry)
            
            # Project to UTM for accurate buffering
            centroid = geom.centroid
            utm_zone = int((centroid.x + 180) / 6) + 1
            utm_crs = f"+proj=utm +zone={utm_zone} +datum=WGS84"
            
            project_to_utm = pyproj.Transformer.from_crs(
                "EPSG:4326", utm_crs, always_xy=True
            ).transform
            project_to_wgs = pyproj.Transformer.from_crs(
                utm_crs, "EPSG:4326", always_xy=True
            ).transform
            
            # Buffer in meters
            geom_utm = transform(project_to_utm, geom)
            buffered_utm = geom_utm.buffer(buffer_km * 1000)
            buffered_wgs = transform(project_to_wgs, buffered_utm)
            
            return buffered_wgs
        except Exception as e:
            print(f"  Buffer error: {e}")
            return None
    
    def _get_parks_for_country(self, country_prefix):
        """Get parks for a country prefix"""
        return [p for p in self.parks if p['country_code'] == country_prefix]
    
    def _download_country_roads(self, country_code):
        """Download road data for a country"""
        url = f"https://warm.storage.heigit.org/heigit-hdx-public/planet_road_data/heigit_{country_code}_planet_roadsurface_lines.geojson"
        
        print(f"  Downloading from {url}...")
        
        try:
            response = requests.get(url, timeout=600, stream=True)
            if response.status_code == 404:
                print(f"  No data available for {country_code}")
                return None
            response.raise_for_status()
            
            # Parse JSON
            data = response.json()
            features = data.get('features', [])
            print(f"  Downloaded {len(features)} road features")
            return features
            
        except requests.exceptions.Timeout:
            print(f"  Timeout downloading {country_code}")
            return None
        except Exception as e:
            print(f"  Error downloading {country_code}: {e}")
            return None
    
    def _filter_roads_for_park(self, roads, park_buffer):
        """Filter roads that intersect with park buffer"""
        if not park_buffer:
            return []
        
        prep_buffer = prep(park_buffer)
        filtered = []
        
        for road in roads:
            try:
                road_geom = shape(road['geometry'])
                if prep_buffer.intersects(road_geom):
                    # Clip to buffer
                    clipped = road_geom.intersection(park_buffer)
                    if not clipped.is_empty:
                        road_copy = dict(road)
                        road_copy['geometry'] = mapping(clipped)
                        filtered.append(road_copy)
            except Exception:
                continue
        
        return filtered
    
    def process_country(self, country_code):
        """Process all parks for a country"""
        country_prefix = COUNTRY_CODE_TO_PREFIX.get(country_code)
        if not country_prefix:
            print(f"Unknown country code: {country_code}")
            return 0
        
        parks = self._get_parks_for_country(country_prefix)
        if not parks:
            print(f"No parks for {country_code} ({country_prefix})")
            return 0
        
        print(f"\nProcessing {country_code} ({country_prefix}): {len(parks)} parks")
        
        # Download roads for country
        roads = self._download_country_roads(country_code)
        if not roads:
            return 0
        
        # Process each park
        total_roads = 0
        for park in parks:
            park_id = park['id']
            
            # Create buffer if not cached
            if park_id not in self.park_buffers:
                self.park_buffers[park_id] = self._create_buffer(
                    park['geometry'], BUFFER_KM
                )
            
            buffer = self.park_buffers[park_id]
            
            # Filter roads
            park_roads = self._filter_roads_for_park(roads, buffer)
            
            if park_roads:
                # Extract and save with ALL attributes
                output_file = OUTPUT_DIR / f"{park_id}.json"
                
                formatted_roads = []
                for road in park_roads:
                    props = road.get('properties', {})
                    formatted_roads.append({
                        'geometry': road['geometry'],
                        # OSM attributes
                        'osm_id': props.get('osm_id'),
                        'highway': props.get('osm_tags_highway'),
                        'surface': props.get('osm_tags_surface'),
                        'osm_surface_class': props.get('OSM_surface_class'),
                        'osm_length': props.get('OSM_length'),
                        # Deep learning classifications
                        'dl_class_2024': props.get('DL_road_class_2024'),
                        'dl_class_2020': props.get('DL_road_class_2020'),
                        'surface_change': props.get('surface_change_paved'),
                        # Pixel counts for verification
                        'paved_pixels_2024': props.get('paved_pixels_2024'),
                        'unpaved_pixels_2024': props.get('unpaved_pixels_2024'),
                        # Passability scores
                        'passability_code': props.get('Passability_Alphanumeric_Code'),
                        'passability_desc': props.get('Passability_Descriptive_Code'),
                        'passability_risk': props.get('Passability_Numerical_Risk_Score'),
                        # Road width class
                        'rw_class': props.get('rw_class'),
                    })
                
                with open(output_file, 'w') as f:
                    json.dump(formatted_roads, f)
                
                print(f"    {park_id}: {len(park_roads)} roads")
                total_roads += len(park_roads)
        
        # Clear roads from memory
        del roads
        
        return total_roads
    
    def run(self, countries=None):
        """Process all countries or specific list"""
        if countries is None:
            countries = AFRICAN_COUNTRIES
        
        print("=" * 60)
        print("HeiGIT Road Data Download")
        print(f"Processing {len(countries)} countries")
        print(f"Output: {OUTPUT_DIR}")
        print("=" * 60)
        
        total = 0
        for i, country in enumerate(countries, 1):
            print(f"\n[{i}/{len(countries)}] {country}")
            count = self.process_country(country)
            total += count
            
            # Small delay between countries
            time.sleep(2)
        
        print("\n" + "=" * 60)
        print(f"Complete! Total roads: {total}")
        print("=" * 60)


if __name__ == '__main__':
    downloader = RoadDownloader()
    
    # Can pass specific countries as args
    if len(sys.argv) > 1:
        countries = sys.argv[1:]
    else:
        countries = None
    
    downloader.run(countries)
