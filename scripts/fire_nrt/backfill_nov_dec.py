#!/usr/bin/env python3
"""
Backfill fire data for Nov-Dec 2025 using NRT sources.
VIIRS_SNPP_SP only goes to 2025-10-31, so we need NRT for Nov/Dec.
"""

import sys
import time
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from download_nrt import (
    get_working_proxy, get_all_park_ids, get_park_bbox,
    fetch_fire_data, store_fire_data, RATE_LIMIT_DELAY, DB_PATH
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# NRT sources for Nov 2025 onwards
NRT_SOURCES = ["VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT"]


def backfill_date(date_str: str, proxy: str, park_ids: list) -> int:
    """Backfill a single date using NRT sources."""
    total = 0
    
    for park_id in park_ids:
        bbox = get_park_bbox(park_id)
        if not bbox:
            continue
        
        for source in NRT_SOURCES:
            fires = fetch_fire_data(
                bbox, proxy, days=1,
                source=source,
                date=date_str
            )
            
            if fires:
                inserted = store_fire_data(DB_PATH, park_id, fires)
                total += inserted
                if inserted > 0:
                    logger.info(f"  {park_id}/{source}: +{inserted} fires")
            
            time.sleep(RATE_LIMIT_DELAY / 2)  # Shorter delay, 2 sources
    
    return total


def main():
    proxy = get_working_proxy()
    if not proxy:
        logger.error("No working proxy")
        return 1
    
    logger.info(f"Using proxy: {proxy}")
    
    park_ids = get_all_park_ids()
    logger.info(f"Processing {len(park_ids)} parks")
    
    # Nov 1 to Dec 31, 2025
    start = datetime(2025, 11, 1)
    end = datetime(2025, 12, 31)
    
    current = start
    total_inserted = 0
    
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        logger.info(f"=== Backfilling {date_str} ===")
        
        inserted = backfill_date(date_str, proxy, park_ids)
        total_inserted += inserted
        
        logger.info(f"  Day total: {inserted} fires")
        current += timedelta(days=1)
    
    logger.info(f"=== COMPLETE: {total_inserted} total fires inserted ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
