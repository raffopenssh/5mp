"""Configuration for FIRMS NRT fire data download."""

# FIRMS API Configuration
MAP_KEY = "REDACTED_FIRMS_KEY"
BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api"

# Working proxies (tested 2026-02-24 07:46)
PROXIES = [
    "18.229.170.122:3128",
    "43.161.214.161:1081",
]

# Data sources
SOURCES = {
    "nrt": "VIIRS_SNPP_NRT",
    "standard": "VIIRS_SNPP_SP",
    "modis_nrt": "MODIS_NRT",
}

# Transaction limits
MAX_TRANSACTIONS_PER_10MIN = 5000
RATE_LIMIT_DELAY = 0.5

# Trajectory analysis settings
MIN_TRAJECTORY_DAYS = 28
CLUSTER_DISTANCE_KM = 5
ACTIVE_FIRE_THRESHOLD_DAYS = 1

# Anonymous group naming
GROUP_NAMES = [
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", 
    "Golf", "Hotel", "India", "Juliet", "Kilo", "Lima",
    "Mike", "November", "Oscar", "Papa", "Quebec", "Romeo",
    "Sierra", "Tango", "Uniform", "Victor", "Whiskey", "X-ray",
    "Yankee", "Zulu"
]

# Database path
DB_PATH = "/home/exedev/5mp/db.sqlite3"
