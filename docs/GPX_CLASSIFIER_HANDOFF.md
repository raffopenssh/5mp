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

## Remaining / possible follow-ups
1. **Similar +? vs total bugs**: `UpdatePlaceStats` and `UpdateRoadStats` SQL use `+ 1`
   (safe), but `srv/gpx_learner.go:505-534` computes `newMatchCount := matchCount + 1` and
   passes `MatchCount: ptrInt64(1)` on CREATE — verify road auto-approve math is consistent.
   Also `srv/gpx_learner.go:1961` passes `ptrInt64(int64(road.TrackCount))` to a query — check
   whether that query is an INSERT (fine) or the additive UPDATE (bug).
2. **park_vehicle_stats total_time_hours**: was always 0; the Duration-recovery fix only helps
   NEW uploads. Old rows still have 0. Historical reprocessing would fix.
3. **Historical reprocessing** (open decision): stored `gpx_uploads.movement_type` for old
   autofetch rows disagrees with the new classifier in ~2/3 of cases (whole multi-track file got
   ONE majority type). Old `classified_segments_json` rows also lack SampledPoints. Reprocessing
   uploads from `upload_queue.file_content` (status='completed') would fix both — but consider
   dedup (file_hash), effort_data/grid rebuild implications, and 5.7M-row DB safety first.
4. **learned_places visit counts**: eyeball for inflation from repeated learner runs on the
   same upload (learning queue had 1426 completed jobs; jobs re-run per park per upload).

## Tools
- `cmd/gpx-classify` — per-track classifier debug (`-hints`, `-segments`).
- `cmd/gpx-pipeline` — full production pipeline incl. majority vote (`-nohints`, `-v`).
- `/tmp/erqv/main.go` — ER ground-truth confusion matrix (`-nohints`, `-all`).

## Build/deploy
`make build && sudo systemctl restart 5mp` — footer version must match `git rev-parse --short HEAD`.

## Untouched / unrelated dirty files in repo
`db/migrations/033-*.sql`, `scripts/extract_worldclim_grid_old.py`, various *.md at repo root,
data/raw-fire updates, db.sqlite3.bak* — from other work, left uncommitted deliberately.
