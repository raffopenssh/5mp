#!/usr/bin/env python3
"""
Background downloader for NASA VIIRS fire data.
Downloads annual country files from FIRMS archive.
"""

import os
import sys
import time
import requests
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent.parent / "data" / "fire"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = DATA_DIR / "download_log.txt"

# All African countries with fire data
AFRICAN_COUNTRIES = [
    "Algeria", "Angola", "Benin", "Botswana", "Burkina_Faso", "Burundi",
    "Cameroon", "Cape_Verde", "Central_African_Republic", "Chad", "Comoros",
    "Democratic_Republic_of_the_Congo", "Republic_of_the_Congo", "Cote_dIvoire",
    "Djibouti", "Egypt", "Equatorial_Guinea", "Eritrea", "Eswatini", "Ethiopia",
    "Gabon", "Gambia", "Ghana", "Guinea", "Guinea-Bissau", "Kenya", "Lesotho",
    "Liberia", "Libya", "Madagascar", "Malawi", "Mali", "Mauritania", "Mauritius",
    "Morocco", "Mozambique", "Namibia", "Niger", "Nigeria", "Rwanda",
    "Sao_Tome_and_Principe", "Senegal", "Seychelles", "Sierra_Leone", "Somalia",
    "South_Africa", "South_Sudan", "Sudan", "Tanzania", "Togo", "Tunisia",
    "Uganda", "Zambia", "Zimbabwe"
]

# Years available
YEARS = [2018, 2019, 2020, 2021, 2022, 2023, 2024]

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def download_file(country, year):
    """Download a single country-year file."""
    filename = f"viirs-jpss1_{year}_{country}.csv"
    filepath = DATA_DIR / filename
    
    if filepath.exists():
        size_mb = filepath.stat().st_size / 1024 / 1024
        if size_mb > 0.1:  # Skip if file exists and is not empty
            log(f"SKIP {filename} (already exists, {size_mb:.1f} MB)")
            return True
    
    url = f"https://firms.modaps.eosdis.nasa.gov/data/country/viirs-jpss1/{year}/{filename}"
    
    log(f"DOWNLOAD {filename}...")
    
    try:
        response = requests.get(url, stream=True, timeout=600)
        
        if response.status_code == 404:
            log(f"NOT FOUND {filename} (no data for this country/year)")
            return True  # Not an error, just no data
        
        response.raise_for_status()
        
        # Write to temp file first
        temp_path = filepath.with_suffix('.tmp')
        total_size = 0
        with open(temp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=65536):
                f.write(chunk)
                total_size += len(chunk)
        
        # Move temp to final
        temp_path.rename(filepath)
        
        size_mb = total_size / 1024 / 1024
        log(f"OK {filename} ({size_mb:.1f} MB)")
        return True
        
    except requests.exceptions.Timeout:
        log(f"TIMEOUT {filename}")
        return False
    except requests.exceptions.ConnectionError as e:
        log(f"CONNECTION ERROR {filename}: {e}")
        return False
    except Exception as e:
        log(f"ERROR {filename}: {e}")
        return False

def main():
    log("=" * 60)
    log("Starting fire data download")
    log(f"Countries: {len(AFRICAN_COUNTRIES)}")
    log(f"Years: {YEARS}")
    log(f"Total files: {len(AFRICAN_COUNTRIES) * len(YEARS)}")
    log("=" * 60)
    
    # Priority countries first (for transhumance analysis)
    priority = [
        "Central_African_Republic", "Sudan", "South_Sudan", "Chad", 
        "Cameroon", "Democratic_Republic_of_the_Congo", "Nigeria",
        "Ethiopia", "Kenya", "Tanzania", "Zambia", "Zimbabwe",
        "Mozambique", "Botswana", "Namibia", "South_Africa", "Angola"
    ]
    
    # Reorder countries list
    countries = priority + [c for c in AFRICAN_COUNTRIES if c not in priority]
    
    success = 0
    failed = 0
    
    for country in countries:
        for year in YEARS:
            if download_file(country, year):
                success += 1
            else:
                failed += 1
                # Wait longer after failure (server might be overloaded)
                time.sleep(30)
            
            # Be nice to the server
            time.sleep(2)
    
    log("=" * 60)
    log(f"Download complete. Success: {success}, Failed: {failed}")
    log("=" * 60)

if __name__ == "__main__":
    main()
