#!/usr/bin/env python3
"""
Background Data Processing for 5MP Conservation Globe

Runs fire trajectory, GHSL, and OSM processing sequentially to avoid
memory issues. Uses file-based locking to prevent concurrent runs.

Usage:
    python scripts/run_background_processing.py [--fire] [--ghsl] [--osm] [--all]
"""

import os
import sys
import time
import json
import fcntl
import logging
import argparse
import subprocess
import gc
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).parent.parent
LOCK_FILE = BASE_DIR / "logs" / "background_processing.lock"
STATUS_FILE = BASE_DIR / "logs" / "background_processing_status.json"
LOG_DIR = BASE_DIR / "logs"

LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "background_processing.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def update_status(task: str, status: str, details: dict = None):
    """Update status file for monitoring"""
    status_data = {
        'current_task': task,
        'status': status,
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'details': details or {}
    }
    try:
        # Load existing status
        if STATUS_FILE.exists():
            with open(STATUS_FILE) as f:
                existing = json.load(f)
            status_data['history'] = existing.get('history', [])[-10:]  # Keep last 10
        else:
            status_data['history'] = []
        
        status_data['history'].append({
            'task': task,
            'status': status,
            'time': status_data['updated_at']
        })
        
        with open(STATUS_FILE, 'w') as f:
            json.dump(status_data, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to update status: {e}")


def run_fire_processing():
    """Run fire trajectory processing"""
    logger.info("Starting fire trajectory processing...")
    update_status('fire', 'running')
    
    fire_zip = BASE_DIR / "data" / "downloads" / "fire_data.zip"
    
    if not fire_zip.exists():
        logger.warning(f"Fire data ZIP not found: {fire_zip}")
        update_status('fire', 'skipped', {'reason': 'no data file'})
        return False
    
    try:
        # Run the streaming processor
        cmd = [
            sys.executable,
            str(BASE_DIR / "scripts" / "fire_processor_streaming.py"),
            "--zip", str(fire_zip)
        ]
        
        result = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=7200  # 2 hour timeout
        )
        
        if result.returncode == 0:
            logger.info("Fire processing completed successfully")
            update_status('fire', 'completed')
            return True
        else:
            logger.error(f"Fire processing failed: {result.stderr}")
            update_status('fire', 'failed', {'error': result.stderr[:500]})
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("Fire processing timed out")
        update_status('fire', 'timeout')
        return False
    except Exception as e:
        logger.error(f"Fire processing error: {e}")
        update_status('fire', 'error', {'error': str(e)})
        return False
    finally:
        gc.collect()


def run_ghsl_processing():
    """Run GHSL settlement processing"""
    logger.info("Starting GHSL processing...")
    update_status('ghsl', 'running')
    
    ghsl_zip = BASE_DIR / "data" / "downloads" / "ghsl_data.zip"
    
    if not ghsl_zip.exists():
        logger.warning(f"GHSL data ZIP not found: {ghsl_zip}")
        update_status('ghsl', 'skipped', {'reason': 'no data file'})
        return False
    
    try:
        cmd = [
            sys.executable,
            str(BASE_DIR / "scripts" / "ghsl_processor_streaming.py"),
            "--zip", str(ghsl_zip)
        ]
        
        result = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=7200
        )
        
        if result.returncode == 0:
            logger.info("GHSL processing completed successfully")
            update_status('ghsl', 'completed')
            return True
        else:
            logger.error(f"GHSL processing failed: {result.stderr}")
            update_status('ghsl', 'failed', {'error': result.stderr[:500]})
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("GHSL processing timed out")
        update_status('ghsl', 'timeout')
        return False
    except Exception as e:
        logger.error(f"GHSL processing error: {e}")
        update_status('ghsl', 'error', {'error': str(e)})
        return False
    finally:
        gc.collect()


def run_osm_processing(limit: int = None):
    """Run OSM roadless processing"""
    logger.info("Starting OSM roadless processing...")
    update_status('osm', 'running')
    
    try:
        cmd = [
            sys.executable,
            str(BASE_DIR / "scripts" / "osm_roadless_analysis.py")
        ]
        
        if limit:
            cmd.extend(["--limit", str(limit)])
        
        result = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=14400  # 4 hour timeout (OSM is slow due to API rate limits)
        )
        
        if result.returncode == 0:
            logger.info("OSM processing completed successfully")
            update_status('osm', 'completed')
            return True
        else:
            logger.error(f"OSM processing failed: {result.stderr}")
            update_status('osm', 'failed', {'error': result.stderr[:500]})
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("OSM processing timed out")
        update_status('osm', 'timeout')
        return False
    except Exception as e:
        logger.error(f"OSM processing error: {e}")
        update_status('osm', 'error', {'error': str(e)})
        return False
    finally:
        gc.collect()


def acquire_lock():
    """Acquire exclusive lock to prevent concurrent runs"""
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(f"{os.getpid()}\n{datetime.now(timezone.utc).isoformat()}")
        lock_fd.flush()
        return lock_fd
    except (IOError, OSError):
        return None


def release_lock(lock_fd):
    """Release the lock"""
    if lock_fd:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description='Background data processing')
    parser.add_argument('--fire', action='store_true', help='Run fire processing')
    parser.add_argument('--ghsl', action='store_true', help='Run GHSL processing')
    parser.add_argument('--osm', action='store_true', help='Run OSM processing')
    parser.add_argument('--osm-limit', type=int, help='Limit OSM parks to process')
    parser.add_argument('--all', action='store_true', help='Run all processing')
    args = parser.parse_args()
    
    # Default to all if nothing specified
    run_all = args.all or not (args.fire or args.ghsl or args.osm)
    
    # Acquire lock
    lock_fd = acquire_lock()
    if not lock_fd:
        logger.error("Another background processing instance is already running")
        print("ERROR: Another instance is running. Check logs/background_processing.lock")
        sys.exit(1)
    
    try:
        logger.info("Background processing started")
        update_status('init', 'started')
        
        results = {}
        
        if args.fire or run_all:
            results['fire'] = run_fire_processing()
            time.sleep(5)  # Brief pause between tasks
        
        if args.ghsl or run_all:
            results['ghsl'] = run_ghsl_processing()
            time.sleep(5)
        
        if args.osm or run_all:
            results['osm'] = run_osm_processing(args.osm_limit)
        
        logger.info(f"Background processing completed: {results}")
        update_status('done', 'completed', results)
        
    except Exception as e:
        logger.error(f"Background processing failed: {e}")
        update_status('error', 'failed', {'error': str(e)})
        raise
    finally:
        release_lock(lock_fd)


if __name__ == '__main__':
    main()
