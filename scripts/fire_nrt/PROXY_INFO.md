# Working Proxies for FIRMS API Access

## Background
The NASA FIRMS API (https://firms.modaps.eosdis.nasa.gov) is blocked from AWS IP ranges.
A proxy is required to download fire data.

## Working Proxies (tested Feb 2026)

These proxies were found to work with the FIRMS API:

```
43.155.138.148:3128   # Best - Hong Kong datacenter
95.213.217.168:52004  # Russia
194.58.34.63:3128     # Russia  
8.217.147.173:8080    # Singapore
177.71.224.87:3128    # Brazil
46.161.6.165:8080     # Russia
89.208.85.78:443      # Russia
217.77.102.18:3128    # Russia
```

## Finding New Proxies

If proxies stop working, get fresh ones from:
```bash
curl -s "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt" | head -50
```

Test with:
```bash
MAP_KEY="REDACTED_FIRMS_KEY"
PROXY="IP:PORT"
curl -s --proxy "http://$PROXY" --max-time 10 \
  "https://firms.modaps.eosdis.nasa.gov/mapserver/mapkey_status/?MAP_KEY=$MAP_KEY"
```

Expected response: `{ "transaction_limit" : 5000, "current_transactions": 0, "transaction_interval" : "10 minutes" }`

## FIRMS API Key

```
MAP_KEY: REDACTED_FIRMS_KEY
Transaction limit: 5000 / 10 minutes
```

## Usage

The download script automatically rotates through working proxies:
```bash
python3 scripts/fire_nrt/download_nrt.py --park COD_Virunga --days 5
python3 scripts/fire_nrt/download_nrt.py --all --days 5
```

## Proxy Sources

- https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt (updated frequently)
- https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt
- https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt
