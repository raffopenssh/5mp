#!/bin/bash
# Test script for NASA FIRMS Fire Data API

MAP_KEY="d20648f156456e42dacd1e5bf48a64c0"

echo "=== Testing NASA FIRMS API ==="
echo

# 1. Check MAP_KEY status
echo "1. Checking MAP_KEY status..."
curl -s "https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY=${MAP_KEY}"
echo -e "\n"

# 2. Check data availability
echo "2. Checking data availability..."
curl -s "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/${MAP_KEY}/all" | head -20
echo -e "\n"

# 3. Get list of countries
echo "3. Getting countries list (first 10)..."
curl -s "https://firms.modaps.eosdis.nasa.gov/api/countries/?format=json" | head -500
echo -e "\n"

# 4. Get fire data for USA, last 1 day, VIIRS SNPP sensor
echo "4. Getting fire data for USA (last 1 day, VIIRS_SNPP_NRT)..."
curl -s "https://firms.modaps.eosdis.nasa.gov/api/country/csv/${MAP_KEY}/VIIRS_SNPP_NRT/USA/1" | head -10
echo -e "\n"

# 5. Get fire data by bounding box (area) - example: small area in California
echo "5. Getting fire data by area (California region, last 1 day)..."
# Format: west,south,east,north
curl -s "https://firms.modaps.eosdis.nasa.gov/api/area/csv/${MAP_KEY}/VIIRS_SNPP_NRT/-122,36,-118,40/1" | head -10
echo -e "\n"

echo "=== API Test Complete ==="
