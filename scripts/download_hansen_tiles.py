#!/usr/bin/env python3
"""Download Hansen Global Forest Change lossyear tiles needed for Africa parks.

Downloads directly from Google Cloud Storage.
Uses windowed reads when processing (not decompressed).

Usage:
    python scripts/download_hansen_tiles.py
"""

import os
import sys
import urllib.request
import time
from pathlib import Path

BASE_URL = "https://storage.googleapis.com/earthenginepartners-hansen/GFC-2024-v1.12/Hansen_GFC-2024-v1.12_lossyear_{tile}.tif"

# Tiles needed for 162 African parks
TILES_NEEDED = [
    "00N_000E", "00N_010E", "00N_010W", "00N_020E", "00N_030E", "00N_040E",
    "10N_000E", "10N_010E", "10N_010W", "10N_020E", "10N_020W", "10N_030E",
    "10S_000E", "10S_010E", "10S_020E", "10S_030E",
    "20N_000E", "20N_010E",
    "20S_010E", "20S_020E", "20S_030E",
    "30S_010E", "30S_020E", "30S_030E",
    "40S_010E", "40S_020E"
]

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "hansen"

def download_tile(tile_id, force=False):
    """Download a single Hansen tile."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    url = BASE_URL.format(tile=tile_id)
    output_path = OUTPUT_DIR / f"lossyear_{tile_id}.tif"
    
    if output_path.exists() and not force:
        size_mb = output_path.stat().st_size / (1024*1024)
        print(f"[SKIP] {tile_id} already exists ({size_mb:.1f} MB)")
        return True
    
    print(f"[DOWNLOAD] {tile_id} from {url}")
    
    try:
        start = time.time()
        
        # Progress callback
        def reporthook(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                percent = min(100, downloaded * 100 / total_size)
                mb = downloaded / (1024*1024)
                total_mb = total_size / (1024*1024)
                sys.stdout.write(f"\r  {percent:.1f}% ({mb:.1f}/{total_mb:.1f} MB)")
                sys.stdout.flush()
        
        urllib.request.urlretrieve(url, output_path, reporthook)
        
        elapsed = time.time() - start
        size_mb = output_path.stat().st_size / (1024*1024)
        print(f"\n  Downloaded {size_mb:.1f} MB in {elapsed:.1f}s")
        return True
        
    except Exception as e:
        print(f"\n  ERROR: {e}")
        if output_path.exists():
            output_path.unlink()
        return False

def main():
    print(f"Hansen GFC Tile Downloader")
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Tiles to download: {len(TILES_NEEDED)}")
    print()
    
    success = 0
    failed = []
    
    for tile in TILES_NEEDED:
        if download_tile(tile):
            success += 1
        else:
            failed.append(tile)
    
    print()
    print(f"Summary: {success}/{len(TILES_NEEDED)} tiles downloaded")
    if failed:
        print(f"Failed tiles: {', '.join(failed)}")
    
    return 0 if not failed else 1

if __name__ == "__main__":
    sys.exit(main())
