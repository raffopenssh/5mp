# Fire Data Manifest - Raw CSVs deleted after import verification

Cleaned: 2026-07-09

Source: VIIRS JPSS-1 FIRMS country archives (2018-2024), 347 CSVs, 5.1 GB.

All detections were imported into `fire_detections` (27M rows,
2018-04-01 .. present) via `scripts/import_fire_country_csvs.py`
(idempotent INSERT OR IGNORE on (lat,lon,date,time,satellite)).

Verified before deletion: 1,100 sampled detections across
Angola 2024, DRC 2018, Tanzania 2021, CAR 2023 — 100% present in DB.

Re-download if ever needed: https://firms.modaps.eosdis.nasa.gov/country/
