"""Configuration for FIRMS NRT fire data download."""

# FIRMS API Configuration
MAP_KEY = "REDACTED_FIRMS_KEY"
BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api"

# Working proxies (tested and verified Feb 6, 2026)
# These are verified to reach NASA FIRMS API
PROXIES = [
    "95.213.217.168:52004",   # Russia - verified working
    "89.208.85.78:443",       # Russia - verified working  
    "66.80.0.115:3128",       # verified working
    "46.161.6.165:8080",      # verified working
    "43.130.6.42:80",         # verified working
]

# Data sources (prefer NRT for recent data, standard for backfill)
SOURCES = {
    "nrt": "VIIRS_SNPP_NRT",      # Near Real-Time (last ~48 hours)
    "standard": "VIIRS_SNPP_SP",  # Standard Processing (2-3 days delay)
    "modis_nrt": "MODIS_NRT",     # MODIS Near Real-Time
}

# Transaction limits
MAX_TRANSACTIONS_PER_10MIN = 5000
RATE_LIMIT_DELAY = 0.5  # seconds between requests

# Trajectory analysis settings
MIN_TRAJECTORY_DAYS = 28  # Minimum days for trajectory analysis
CLUSTER_DISTANCE_KM = 5   # Max distance for clustering fires
ACTIVE_FIRE_THRESHOLD_DAYS = 1  # Fires within this many days are "active"

# Anonymous group naming (phonetic alphabet)
GROUP_NAMES = [
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", 
    "Golf", "Hotel", "India", "Juliet", "Kilo", "Lima",
    "Mike", "November", "Oscar", "Papa", "Quebec", "Romeo",
    "Sierra", "Tango", "Uniform", "Victor", "Whiskey", "X-ray",
    "Yankee", "Zulu"
]

# Database path
DB_PATH = "/home/exedev/5mp/db.sqlite3"
