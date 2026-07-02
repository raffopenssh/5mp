# GPX Movement Classifier & Detection Pipeline — Handoff

## Goal (user request)
1. ✅ DONE — Fix movement-based assignment of type (foot/vehicle/aircraft + subtypes boat/fixed_wing/rotor_wing)
   relying on **movement only** — NOT track naming, NOT GPS sampling intervals.
2. ✅ MOSTLY DONE — Verify the classifier/detection pipeline (importer → classifier → learner) end-to-end.
3. ✅ DONE — Verify against EarthRanger import data.

## Test data
- `/tmp/gpxtest/*.gpx` — 14 labeled files (filenames are ground truth: car_, boat_, helicopter_,
  in_to_nyerere = fixed-wing Caravan flight, 5H-MSA = aircraft). Source: Google Drive file id
  1TfIMezxB3B_ZaDQr9poERRIj0r0PAlaw (zip; `curl -sL "https://docs.google.com/uc?export=download&id=1TfIMezxB3B_ZaDQr9poERRIj0r0PAlaw&confirm=t"`).
- `/tmp/erq/q*.gpx` — EarthRanger autofetch GPX extracted from `upload_queue.file_content`
  (per-track `<er:subject_type>` = ground truth: person/vehicle/aircraft).
  Re-extract: `sqlite3 db.sqlite3 "SELECT writefile('/tmp/erq/qID.gpx', file_content) FROM upload_queue WHERE id=ID"`.
- ER pipeline verification harness: `/tmp/erqv/main.go` (runs full production pipeline per ER track,
  compares majority movement type vs subject_type; `-nohints` strips ER metadata).

## Completed work (all committed, deployed as f6ae51b4)

### Commit 6c53c4c2 — movement-only classifier (`srv/gpx/movement_classifier.go`)
See git log for details: interval rules removed, takeoff/landing roll fixes, glitch filters,
P2–P98 elevation percentiles, InteriorStopEvents penalty, speed-gated elevation evidence,
extremeClimb rule, vehicle burst rules, subtype via P10 speed floor / SpeedCV / hover ratio.
All 14 labeled files classify correctly per-track (`go run ./cmd/gpx-classify /tmp/gpxtest/*.gpx`).

### Commit c02af451 — detection pipeline fixes (this conversation)
Full-pipeline verification (Parse → SplitIntoSegments → RemoveStraightLineGaps →
ValidateAndClassifyGPX → majority vote) found bugs the per-track check missed:
- **`srv/gpx/processor.go` RemoveStraightLineGaps**: relaxed gap thresholds now apply when the
  segment's own movement classification is aircraft/vehicle (was: only with external ER hints).
  Hint-less flights were shredded (helicopter→vehicle, fixed-wing losing 80% of distance).
- **`srv/gpx_validation.go`**: isRoadTrace/isBoundaryTrace skip now considers segment
  MovementType, not just Hint.Type (457 km fixed-wing transit was excluded as "road trace").
- **`ClassifiedSegment.SampledPoints`**: compact `[lon,lat,unixTs,elevM]` samples (≤300/seg,
  sentinel elev -100000) embedded in `classified_segments_json` so the learner recovers exact
  per-segment points. Old index-into-track_points reconstruction was wrong for uploads with
  >1000 points (sampling) or excluded segments (offset shift). Legacy fallback kept.
- **`srv/gpx_learner.go`**: recovers Duration from point timestamps (total_time_hours was
  always 0 in park_vehicle_stats); vehicle tracks now stored from patrol segments (GeoJSON
  built from points — vehicle_tracks table was empty); classifyAircraft prefers classifier
  subtype over avg-speed thresholds.
- New CLI: `go run ./cmd/gpx-pipeline [-nohints] [-v] file.gpx` — full production pipeline.

Result: all 14 labeled files classify correctly END-TO-END movement-only (previously 6 wrong
through the pipeline). ER confusion (full pipeline, hints on): vehicle 157/157, aircraft 22/22;
foot "mismatches" are er_mobile phones genuinely travelling at aircraft/vehicle speed — ER
subject_type=person describes the device carrier, not the movement mode. Movement-only:
vehicle 140/152 (slow creeping trucks→foot, ambiguous), aircraft 21/22.

### Commit f6ae51b4 — airstrip count doubling bug
`UpdateAirstripStats` SQL adds the param (`landing_count = landing_count + ?`) but the learner
passed the new TOTAL → exponential doubling (one airstrip had 536,870,911 = 2^29−1 landings).
Fixed to pass increment 1; corrupted rows repaired via log2(v+1); backup `db.sqlite3.bak-airstrips`.

### End-to-end verified live
Uploaded helicopter + car test files through `/api/upload/async`: correct movement_type in
`gpx_uploads` (aircraft/vehicle), per-point types in `track_points`, learner jobs completed,
park_vehicle_stats updated.

## Follow-up conversation (July 2, 2026): learner reset + admin UI

### Learner data reset & reprocess (decision: no historical data needed)
User decided old data isn't needed — clean slate, rely on 4-hourly EarthRanger autofetch.
Backups: `db.sqlite3.bak-learner-reset` (full DB) + `backups/backups_learner_20260702.sql`
(learner tables dump). Wiped: learned_roads/places/airstrips (+history), park_vehicle_stats,
vehicle_tracks, gpx_learning_results, gpx_learning_queue, track_points, gpx_uploads,
gpx_upload_logs, effort_data, new_upload notifications. Deleted upload_queue ids 93/94 (stale
Jan–Mar backfill blobs stuck 'processing'). Requeued the remaining 24 raw files → all
reprocessed cleanly with the new movement-only classifier (23 uploads, 61 learning jobs
completed; inflated visit counts / doubled airstrip counts / zero total_time_hours all gone).

### Bug fixed: learner FK failure for jobs without upload_id
`storeLearningResult`/`CreateVehicleTrack` passed `ptrInt64(0)` when job.UploadID was nil →
FOREIGN KEY constraint failure, whole learning job failed. Added `ptrUploadID()` (nil for 0).

### Admin UI additions (`srv/learned_kml.go`, globe.html)
- `GET /api/admin/learned-features-kml?scope=pending` or `?park_id=X` — sheet-level KML export
  (folders: Roads/Places/Airstrips, all pending or per-park). Subtle "⤓ KML" link on the
  Pending Approvals and Learned Features sheets (one button per sheet, not per row).
- Coverage line per park: "built on N data points / N tracks between DATE – DATE"
  (`learnedCoverageForPark`, surfaced in pending-approvals `coverage` map and
  learned-features `coverage` field).
- Learned Features tab rewritten: park dropdown populated from learning-results, renders
  its own table into #features-content (old code referenced non-existent element IDs).

## Known issue: learned_roads always empty (diagnosed July 2026)

0 rows in learned_roads despite 366 vehicle_tracks. Verified against real
`gpx_upload_logs.classified_segments_json` (534 segments, 25 logs). Two independent causes:

1. **HeiGIT path is dead code.** `processRoadSegment` → `findUnmatchedRoadPortions` only runs
   for `Classification == "road"`, but commit c02af451 intentionally skips isRoadTrace for
   vehicle/aircraft movement. Real data: 479 vehicle segments are all patrol(228)/idle(251);
   the single "road" segment is a degenerate stationary blob. HeiGIT matching never executes.
   (Latent bug there too: the roads_heigit bbox query only tests each road's first coordinate.)
2. **Cross-track analysis thresholds unreachable.** `detectRoadsFromTracks` needs ≥5 distinct
   segments in the same 10m cell, then a ≥100m connected component — but it runs per-upload
   only, and points (SampledPoints ≤300/seg, track_points ≤1000/upload, ER fixes ~5min apart)
   have median spacing ~717m vs 10m cells, with no line rasterization between points.
   Simulated with real data: best upload has 2 hot cells; analysis.Roads always empty
   (all gpx_learning_results have new_roads_found=0).

Not a data problem: pooling all vehicle segments park-wide shows cells hit by up to 39
distinct segments — rangers do repeat corridors.

### FIXED (commit 669a0004): park-wide incremental road learning

New `srv/road_learner.go` (`learnRoadsFromTrack`) runs inside learner jobs for every NEW
vehicle track: resample to 50m steps (break >5km gaps) → subtract portions within 100m of
HeiGIT roads → match pieces ≥300m against existing learned_roads corridors (60% of points
within 150m; +25% confidence per traversal, cap 95, auto-approve ≥90% & ≥5 traversals) or
create a new pending row at 25%. `storeVehicleTrack` now dedupes identical geometries per
park and only new tracks feed learning, so ER autofetch redelivery / requeues are idempotent
(verified: requeued upload 534, zero new rows or match increments).
Per-upload grid road creation was removed from `processCrossTrackAnalysis` (airstrips/bases
kept). `cmd/road-backfill` seeded learned_roads from deduplicated historical vehicle_tracks:
38 roads (Nyerere 13 / Ruaha 16 / Udzungwa 9), 4 with ≥2 traversals, 1 auto-approved.
Backup: `backups/roads_pre_backfill_20260702.sql`. Do not re-run the backfill on a non-empty
learned_roads table (it refuses without -force).

## Remaining / possible follow-ups
1. **Similar +? vs total bugs**: `UpdatePlaceStats`/`UpdateRoadStats`/`UpdateLearnedRoadMatch`
   SQL use `+ 1` (safe). `CreateLearnedRoad` from cross-track analysis passes
   `MatchCount: road.TrackCount` on INSERT (fine). Verified during this pass.
2. **learned_places visit counts**: post-reset values look sane; keep an eye on repeated
   learner runs re-counting the same underlying tracks as autofetch accumulates.

## Tools
- `cmd/gpx-classify` — per-track classifier debug (`-hints`, `-segments`).
- `cmd/gpx-pipeline` — full production pipeline incl. majority vote (`-nohints`, `-v`).
- `/tmp/erqv/main.go` — ER ground-truth confusion matrix (`-nohints`, `-all`).

## Build/deploy
`make build && sudo systemctl restart 5mp` — footer version must match `git rev-parse --short HEAD`.

## Untouched / unrelated dirty files in repo
`db/migrations/033-*.sql`, `scripts/extract_worldclim_grid_old.py`, various *.md at repo root,
data/raw-fire updates, db.sqlite3.bak* — from other work, left uncommitted deliberately.
