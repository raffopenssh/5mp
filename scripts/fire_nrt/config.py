"""Configuration for FIRMS NRT fire data download."""

# FIRMS API Configuration
MAP_KEY = "REDACTED_FIRMS_KEY"
BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api"

# Working proxies (tested and verified Feb 2026)
# Put most reliable ones first
PROXIES = [
    "95.213.217.168:52004",   # Russia - most reliable
    "194.58.34.63:3128",      # Russia - reliable
    "43.155.138.148:3128",    # Hong Kong
    "66.80.0.115:3128", 
    "177.71.224.87:3128",
    "46.161.6.165:8080",
    "89.208.85.78:443",
    "217.77.102.18:3128",
    "18.219.243.198:3128",
    "43.130.6.42:80",
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
