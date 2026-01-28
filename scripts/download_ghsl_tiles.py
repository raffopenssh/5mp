#!/usr/bin/env python3
"""
GHSL Tile Downloader

Downloads missing GHSL (Global Human Settlement Layer) tiles from the JRC data portal.
Designed for GHS_BUILT_S 2018 10m resolution tiles.

Usage:
    python scripts/download_ghsl_tiles.py
    
    # Run in background:
    nohup python scripts/download_ghsl_tiles.py >> logs/ghsl_download.log 2>&1 &
"""

import os
import sys
import time
import zipfile
import logging
import subprocess
from pathlib import Path
from typing import List, Tuple
import json

# Configuration
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data" / "ghsl"
LOG_DIR = BASE_DIR / "logs"
PROGRESS_FILE = DATA_DIR / "download_progress.json"

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / 'ghsl_download.log')
    ]
)
logger = logging.getLogger(__name__)

# GHSL URL pattern for 2018 10m tiles
BASE_URL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2018_GLOBE_R2023A_54009_10/V1-0/tiles"
FILENAME_PATTERN = "GHS_BUILT_S_E2018_GLOBE_R2023A_54009_10_V1_0_R{row}_C{col}.zip"

# Missing tiles to download
MISSING_TILES = [
    (5, 18), (5, 19), (6, 18), (6, 19), (6, 20),
    (7, 16), (7, 17), (7, 18), (7, 19), (7, 20), (7, 21),
    (8, 17), (8, 21), (8, 22),
    (9, 18), (9, 20), (9, 21),
    (10, 19), (10, 20), (10, 21),
    (11, 19), (11, 20), (11, 21),
    (12, 19), (12, 20)
]


def get_tile_url(row: int, col: int) -> str:
    """Get the download URL for a tile."""
    filename = FILENAME_PATTERN.format(row=row, col=col)
    return f"{BASE_URL}/{filename}"


def get_tile_dir(row: int, col: int) -> Path:
    """Get the output directory for a tile."""
    dirname = FILENAME_PATTERN.format(row=row, col=col).replace('.zip', '')
    return DATA_DIR / dirname


def is_tile_downloaded(row: int, col: int) -> bool:
    """Check if a tile is already downloaded and extracted."""
    tile_dir = get_tile_dir(row, col)
    if tile_dir.exists():
        tif_files = list(tile_dir.glob("*.tif"))
        if tif_files:
            return True
    return False


def download_tile_wget(row: int, col: int, timeout: int = 600) -> bool:
    """
    Download a tile using wget with retry logic.
    
    Args:
        row: Tile row
        col: Tile column
        timeout: Connection timeout in seconds
    
    Returns:
        True if successful, False otherwise
    """
    url = get_tile_url(row, col)
    filename = FILENAME_PATTERN.format(row=row, col=col)
    zip_path = DATA_DIR / filename
    tile_dir = get_tile_dir(row, col)
    
    logger.info(f"Downloading R{row}_C{col} from {url}")
    
    # Use wget with retry logic
    cmd = [
        "wget",
        "-q",  # Quiet
        "--show-progress",  # But show progress
        "--tries=3",  # Retry 3 times
        "--timeout=60",  # 60s timeout per operation
        "--waitretry=30",  # Wait 30s between retries
        "--continue",  # Resume partial downloads
        "-O", str(zip_path),
        url
    ]
    
    try:
        result = subprocess.run(
            cmd,
            timeout=timeout,
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            logger.error(f"wget failed for R{row}_C{col}: {result.stderr}")
            if zip_path.exists():
                zip_path.unlink()
            return False
        
        # Verify file was downloaded
        if not zip_path.exists() or zip_path.stat().st_size < 1000:
            logger.error(f"Downloaded file too small or missing for R{row}_C{col}")
            if zip_path.exists():
                zip_path.unlink()
            return False
        
        logger.info(f"Downloaded R{row}_C{col}: {zip_path.stat().st_size / 1024 / 1024:.1f} MB")
        
        # Extract
        return extract_tile(zip_path, tile_dir)
        
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout downloading R{row}_C{col}")
        if zip_path.exists():
            zip_path.unlink()
        return False
    except Exception as e:
        logger.error(f"Error downloading R{row}_C{col}: {e}")
        if zip_path.exists():
            zip_path.unlink()
        return False


def download_tile_curl(row: int, col: int, timeout: int = 600) -> bool:
    """
    Download a tile using curl as fallback.
    """
    url = get_tile_url(row, col)
    filename = FILENAME_PATTERN.format(row=row, col=col)
    zip_path = DATA_DIR / filename
    tile_dir = get_tile_dir(row, col)
    
    logger.info(f"Trying curl for R{row}_C{col}")
    
    cmd = [
        "curl",
        "-L",  # Follow redirects
        "-f",  # Fail silently on HTTP errors
        "--connect-timeout", "60",
        "--max-time", str(timeout),
        "--retry", "3",
        "--retry-delay", "30",
        "-o", str(zip_path),
        url
    ]
    
    try:
        result = subprocess.run(
            cmd,
            timeout=timeout + 60,
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            logger.error(f"curl failed for R{row}_C{col}: {result.stderr}")
            if zip_path.exists():
                zip_path.unlink()
            return False
        
        if not zip_path.exists() or zip_path.stat().st_size < 1000:
            logger.error(f"Downloaded file too small or missing for R{row}_C{col}")
            if zip_path.exists():
                zip_path.unlink()
            return False
        
        logger.info(f"Downloaded R{row}_C{col}: {zip_path.stat().st_size / 1024 / 1024:.1f} MB")
        
        return extract_tile(zip_path, tile_dir)
        
    except Exception as e:
        logger.error(f"Error with curl for R{row}_C{col}: {e}")
        if zip_path.exists():
            zip_path.unlink()
        return False


def extract_tile(zip_path: Path, tile_dir: Path) -> bool:
    """
    Extract a downloaded tile ZIP file.
    """
    try:
        tile_dir.mkdir(parents=True, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(tile_dir)
        
        # Verify TIF was extracted
        tif_files = list(tile_dir.glob("*.tif"))
        if not tif_files:
            logger.error(f"No TIF file found in {zip_path}")
            return False
        
        logger.info(f"Extracted to {tile_dir}: {[f.name for f in tif_files]}")
        
        # Clean up ZIP
        zip_path.unlink()
        
        return True
        
    except zipfile.BadZipFile:
        logger.error(f"Bad ZIP file: {zip_path}")
        zip_path.unlink()
        return False
    except Exception as e:
        logger.error(f"Error extracting {zip_path}: {e}")
        return False


def load_progress() -> dict:
    """Load download progress from file."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"downloaded": [], "failed": []}


def save_progress(progress: dict):
    """Save download progress to file."""
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2)


def download_missing_tiles(tiles: List[Tuple[int, int]] = None):
    """
    Download all missing tiles.
    """
    if tiles is None:
        tiles = MISSING_TILES
    
    progress = load_progress()
    
    # Filter out already downloaded tiles
    tiles_to_download = []
    for row, col in tiles:
        if is_tile_downloaded(row, col):
            logger.info(f"R{row}_C{col} already exists, skipping")
            if [row, col] not in progress["downloaded"]:
                progress["downloaded"].append([row, col])
        elif [row, col] in progress["downloaded"]:
            logger.info(f"R{row}_C{col} marked as downloaded but not found, retrying")
            tiles_to_download.append((row, col))
        else:
            tiles_to_download.append((row, col))
    
    logger.info(f"Tiles to download: {len(tiles_to_download)} of {len(tiles)}")
    save_progress(progress)
    
    # Download each tile
    for i, (row, col) in enumerate(tiles_to_download):
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing tile {i+1}/{len(tiles_to_download)}: R{row}_C{col}")
        logger.info(f"{'='*60}")
        
        success = False
        
        # Try wget first
        success = download_tile_wget(row, col)
        
        # If wget fails, try curl
        if not success:
            logger.info(f"wget failed, trying curl for R{row}_C{col}")
            time.sleep(10)  # Wait before retry
            success = download_tile_curl(row, col)
        
        if success:
            progress["downloaded"].append([row, col])
            if [row, col] in progress["failed"]:
                progress["failed"].remove([row, col])
            logger.info(f"SUCCESS: R{row}_C{col}")
        else:
            if [row, col] not in progress["failed"]:
                progress["failed"].append([row, col])
            logger.error(f"FAILED: R{row}_C{col}")
        
        save_progress(progress)
        
        # Wait between downloads to be nice to the server
        if i < len(tiles_to_download) - 1:
            logger.info("Waiting 5 seconds before next download...")
            time.sleep(5)
    
    # Final summary
    logger.info(f"\n{'='*60}")
    logger.info("DOWNLOAD SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"Downloaded: {len(progress['downloaded'])} tiles")
    logger.info(f"Failed: {len(progress['failed'])} tiles")
    
    if progress['failed']:
        logger.info(f"Failed tiles: {progress['failed']}")
    
    return progress


def check_status():
    """Check current download status."""
    print("\nGHSL Tile Download Status")
    print("=" * 50)
    
    # Check existing tiles
    existing = []
    missing = []
    
    for row, col in MISSING_TILES:
        if is_tile_downloaded(row, col):
            existing.append((row, col))
        else:
            missing.append((row, col))
    
    print(f"\nRequired tiles: {len(MISSING_TILES)}")
    print(f"Already downloaded: {len(existing)}")
    print(f"Still missing: {len(missing)}")
    
    if existing:
        print(f"\nExisting: {existing}")
    if missing:
        print(f"\nMissing: {missing}")
    
    # Check progress file
    if PROGRESS_FILE.exists():
        progress = load_progress()
        print(f"\nProgress file:")
        print(f"  Downloaded: {len(progress.get('downloaded', []))}")
        print(f"  Failed: {len(progress.get('failed', []))}")
        if progress.get('failed'):
            print(f"  Failed tiles: {progress['failed']}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='GHSL Tile Downloader')
    parser.add_argument('--status', action='store_true', help='Check download status')
    parser.add_argument('--tiles', type=str, help='Specific tiles to download (e.g., "5,18 5,19")')
    parser.add_argument('--retry-failed', action='store_true', help='Retry failed downloads only')
    args = parser.parse_args()
    
    if args.status:
        check_status()
        return
    
    tiles = None
    
    if args.retry_failed:
        progress = load_progress()
        if progress.get('failed'):
            tiles = [tuple(t) for t in progress['failed']]
            logger.info(f"Retrying {len(tiles)} failed tiles")
        else:
            logger.info("No failed tiles to retry")
            return
    elif args.tiles:
        # Parse specific tiles
        tiles = []
        for t in args.tiles.split():
            row, col = map(int, t.split(','))
            tiles.append((row, col))
        logger.info(f"Downloading specific tiles: {tiles}")
    
    download_missing_tiles(tiles)


if __name__ == '__main__':
    main()
