# Handover: Fire UI issues after rebuild (2026-07-06)

User reported 3 issues (screenshots from mobile, CAF_Chinko + COD_Bili-Uere):

## 1. Pinning a fire group fails: "Feature not found: CAF_Chinko_2026_grp_2" — ROOT CAUSE FOUND

`scripts/daily_park_refresh.py` ran on CAF_Chinko today (cache `computed_at = 2026-07-06 10:01:35`).
Its step `call_refresh_endpoint()` POSTs `/api/refresh-park`, which calls the **Go**
`computeFireNarrativeForCache()` (srv/park_refresh.go → srv/fire_narrative_cache.go).

That Go path builds narratives via `getTrajectoryNarrativesFromJSON()` which:
- reads **legacy** `data/fire_trajectories_v2/{park}.json` (v2! only 137 parks have it),
- generates **sequential** feature_ids: `CAF_Chinko_2026_grp_1`, `_grp_2`, ... (line ~637:
  `fmt.Sprintf("%s_%d_grp_%d", parkID, t.Year, groupNum)`).

But `feature_geometries` holds **v5 hash IDs**: `CAF_Chinko_2026_grp_dcb35641`.
So the popup rows carry IDs that don't exist in the features API → "Feature not found".

The canonical cache writer is `scripts/precompute_narratives_v5.py` (reads
feature_geometries directly, uses real v5 feature_ids). The Go refresh **overwrote**
the good v5 cache with stale v2-derived data.

**Fix options:**
- Best: in `HandleAPIRefreshPark`, shell out to / replicate
  `precompute_narratives_v5.py --park X` instead of `computeFireNarrativeForCache`;
  or simply have `scripts/daily_park_refresh.py` run
  `precompute_narratives_v5.py` for the park AFTER `call_refresh_endpoint()`
  (order matters — python must win).
- Also fix/remove `getTrajectoryNarrativesFromJSON` (v2 files are stale; delete path?).
- Immediate remediation: rerun `python3 scripts/precompute_narratives_v5.py` (or per-park)
  to restore correct caches for any park refreshed by the rotation
  (see `data/daily_refresh_state.json` for affected parks).

## 2. "503 GROUPS" / "Currently Active (100)" — partially diagnosed

- Group counts (503/647) come from the same broken cache above: v2 files for Chinko
  contain 647 trajectories for the year range vs 314 v5 2026 groups in feature_geometries.
  Fixing #1 fixes the counts.
- "Currently Active (100)" = hard truncation in `handleFireRealtimeFromFeatures`
  (srv/fire_realtime_handlers.go ~line 1332: `if len(groups) > 100 { groups = groups[:100] }`).
  Angola/Congo parks in peak season legitimately have 100s of active trajectories
  (e.g. AGO_Luando: 456 with end_date within 3 days). Consider merging/clustering or
  raising the "active" bar (min fires/days) before calling it a bug.
- NOTE: `is_active` uses `end_date >= now-3d`; daily FIRMS update keeps extending
  end_date so many small groups stay "active".

## 3. Duplicate weekly charts — user wants ONE sparkline incl. prior-average

Popup fire section currently renders BOTH:
- new `areaSparkline` (async `/api/parks/{id}/fire-trend`, red fires + orange groups) — keep
- old `renderFireWeeklyChart` bar chart ("Weekly: 2026 vs prior avg", globe.html line ~675,
  invoked ~line 6917 via `trendData.weeks`) — REMOVE

**Requested:** remove the bar chart; add the **anterior median/prior-year average** as an
extra overlay series in the sparkline (fire-trend endpoint would need per-week prior-years
aggregate, or compute client-side from full history; `/api/parks/{id}/fire-trend` returns
weekly {fires, groups, stopped} from 2020 so client-side prior-median per ISO-week is easy —
fetch without from/to, bucket by week-of-year).

## State
- Nothing fixed yet; diagnosis only. Uncommitted worktree changes from the previous
  session (turbidity/mining pinning fallback, notification type lists, cron_notify)
  committed alongside this doc.
