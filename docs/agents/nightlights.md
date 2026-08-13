# Nightlights (VNP46A3 Black Marble) — WP3

Site-anchored radiance at reported mine sites, **not** a raster layer. The
deliverable is a measured lift (mine sites vs random hull controls) in
`data/eval/nightlights_sites.json`; any UI layer is gated on that skill (R4).
Mining *detection* stays retired — this is inference over reference lists.

## Pieces

| File | Role |
|---|---|
| `scripts/fetch_nightlights.py` | CMR listing + Earthdata Cloud download of VNP46A3 monthly tiles; extracts 5×5 pixel windows per site into `data/nightlights/extracts/{tile}_{YYYY-MM}.npz`, deletes the 64 MB granule (extract-then-discard; disk is ~76% full) |
| `scripts/nightlight_sites.py` | Sites-in-area discovery, control sampling, rotation runner, skill eval, notifications |
| `data/nightlight_state.json` | Rotation state (file, not table — survives DB restore) |
| `data/eval/nightlights_sites.json` | Committed output: per-area per-site monthly series + skill block, R7 attribution |

## Auth — the two redirect traps

`EARTHDATA_TOKEN` in `secrets.env` (URS bearer, ~60-day expiry, uid raf2026,
current one expires 2026-10-12). Absent token → scripts exit naming the var
and notify `nightlights_failed`.

- Classic LAADS `/archive/allData/...` URLs **no longer accept bearer
  tokens** (303 → interactive OAuth/EULA). Use CMR search (no auth) +
  `https://data.laadsdaac.earthdatacloud.nasa.gov/prod-lads/VNP46A3/<name>`.
- The cloud endpoint 303s to a **pre-signed S3 URL**. A client that doesn't
  follow gets 0 bytes with a success code; a client that follows while
  re-sending the bearer header (urllib default) gets S3 400 "Only one auth
  mechanism allowed". `download()` does the redirect manually and strips the
  header. ~30 MB/s, ~2.5 s per granule.

## Data facts

- VNP46A3 v2 (archiveSet 5200): monthly composites, 2400×2400 px per 10°
  tile, dataset `AllAngle_Composite_Snow_Free` in **nW/(cm² sr)**,
  `_FillValue -999.9`; `*_Num` band (fill 65535) = cloud-free nights behind
  each pixel and travels with every kept radiance.
- Fill = **unobserved month = None, never 0** — dark and unseen are
  different states.
- Archive runs 2012-01 → ~2 months behind present; ~174 granules/tile.
  `list_granules` raises UNFINISHED if CMR returns <100 (R1).
- Sites: `data/eval/mining_reference.json` (3,494) +
  `data/eval/crisistracker/mine_sites.json` (50). 11 areas contain any;
  XSA_Study_Area has 71 (tiles h20v07/h20v08/h21v08); the rest have 1–5.

## Skill

Per area: 5 controls per mine site, rejection-sampled in the area hull with
**fixed seed 20260813** (controls must not move between runs). Lift =
mean(site 3×3 medians)/mean(control medians), 2000-permutation p, verdict
`lit` / `dark` / `unmeasured` — a null prints its power note beside it.
Extracts are per (tile,month) files holding *all* points for that tile; if
the site set grows, delete that tile's extracts and re-run.

## Schedule

```
20 2 * * *  nightlight_sites.py --rotate 1    # one stalest pending area/night
40 2 5 * *  nightlight_sites.py --append      # newest composite months, all areas
```

AOIs sort before parks on a fresh queue. A run with failed months keeps the
area pending and notifies `nightlights_failed`; only a clean run stamps
`PIPELINE_VERSION` (bump it to re-queue everything). `--list`, `--area X`,
`--eval-only` for manual use. Raw extracts are gitignored
(`data/nightlights/`); the eval JSON is committed.
