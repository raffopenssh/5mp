#!/usr/bin/env python3
"""
Download GADM level 2 (district/sub-province) data for African countries.

GADM data source: https://gadm.org/download_country.html
Format: GeoJSON or Shapefile

This script downloads the data and extracts region names and bboxes.
"""

import json
import os
import sys
import time
import requests
from pathlib import Path
from zipfile import ZipFile
import io

# African country ISO3 codes
AFRICAN_COUNTRIES = [
    "AGO", "BEN", "BWA", "BFA", "BDI", "CMR", "CAF", "TCD", "COM", "COD", "COG",
    "CIV", "DJI", "EGY", "GNQ", "ERI", "SWZ", "ETH", "GAB", "GMB", "GHA", "GIN",
    "GNB", "KEN", "LSO", "LBR", "LBY", "MDG", "MWI", "MLI", "MRT", "MUS", "MAR",
    "MOZ", "NAM", "NER", "NGA", "RWA", "STP", "SEN", "SYC", "SLE", "SOM", "ZAF",
    "SSD", "SDN", "TZA", "TGO", "TUN", "UGA", "ZMB", "ZWE"
]

def download_gadm_geojson(country_iso, level=2):
    """Download GADM GeoJSON for a country at specified level."""
    # GADM 4.1 GeoJSON URL pattern
    url = f"https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_{country_iso}_{level}.json"
    
    try:
        print(f"  Downloading {country_iso} level {level}...")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print(f"  No level {level} data for {country_iso}")
            return None
        raise
    except Exception as e:
        print(f"  Error downloading {country_iso}: {e}")
        return None

def extract_regions_from_geojson(geojson, country_iso, level):
    """Extract region names and bboxes from GeoJSON."""
    regions = []
    
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        geom = feature.get("geometry", {})
        
        # Get region name (NAME_1 for level 1, NAME_2 for level 2, etc.)
        name_key = f"NAME_{level}"
        name = props.get(name_key, "")
        
        # Get variant names
        varname_key = f"VARNAME_{level}"
        varname = props.get(varname_key, "")
        
        # Get parent region name
        parent_key = f"NAME_{level-1}" if level > 1 else ""
        parent_name = props.get(parent_key, "") if parent_key else ""
        
        # Compute bbox from geometry
        bbox = compute_bbox(geom)
        
        # Get unique ID
        gid_key = f"GID_{level}"
        gid = props.get(gid_key, "")
        
        if name:
            region = {
                "id": gid,
                "name": name,
                "varnames": [v.strip() for v in varname.split("|") if v.strip()] if varname else [],
                "parent": parent_name,
                "country_code": country_iso,
                "level": level,
                "bbox": bbox
            }
            regions.append(region)
    
    return regions

def compute_bbox(geometry):
    """Compute bounding box from GeoJSON geometry."""
    coords = geometry.get("coordinates", [])
    geom_type = geometry.get("type", "")
    
    all_coords = []
    
    def flatten_coords(c, depth=0):
        if depth > 5:  # Safety limit
            return
        if isinstance(c[0], (int, float)):
            all_coords.append(c)
        else:
            for item in c:
                flatten_coords(item, depth + 1)
    
    try:
        flatten_coords(coords)
    except:
        return None
    
    if not all_coords:
        return None
    
    lons = [c[0] for c in all_coords]
    lats = [c[1] for c in all_coords]
    return [min(lons), min(lats), max(lons), max(lats)]

def main():
    output_dir = Path("data/gadm_level2")
    output_dir.mkdir(exist_ok=True)
    
    all_regions = []
    
    for country_iso in AFRICAN_COUNTRIES:
        cache_file = output_dir / f"{country_iso}_level2.json"
        
        # Check cache
        if cache_file.exists():
            print(f"Loading cached {country_iso}...")
            with open(cache_file) as f:
                regions = json.load(f)
        else:
            # Download
            geojson = download_gadm_geojson(country_iso, level=2)
            if geojson:
                regions = extract_regions_from_geojson(geojson, country_iso, level=2)
                # Cache
                with open(cache_file, "w") as f:
                    json.dump(regions, f)
            else:
                regions = []
            time.sleep(0.5)  # Rate limiting
        
        all_regions.extend(regions)
        print(f"  {country_iso}: {len(regions)} level 2 regions")
    
    # Write combined output
    output_path = Path("data/gadm_africa_level2.json")
    with open(output_path, "w") as f:
        json.dump({"regions": all_regions}, f, indent=2)
    
    print(f"\nTotal: {len(all_regions)} level 2 regions saved to {output_path}")

if __name__ == "__main__":
    main()
