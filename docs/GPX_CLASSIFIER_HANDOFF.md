# GPX Movement Classifier — Work-in-Progress Handoff

## Goal (user request)
1. Fix movement-based assignment of type (foot/vehicle/aircraft + subtypes boat/fixed_wing/rotor_wing)
   relying on **movement only** — NOT on track naming and NOT on GPS sampling intervals.
2. Then verify the classifier/detection pipeline (importer → classifier → learner) works properly.
3. Verify against EarthRanger import data (autofetch uploads in DB).

## Test data
- `/tmp/gpxtest/*.gpx` — 14 labeled files (filenames are ground truth: car_, boat_, helicopter_,
  in_to_nyerere = fixed-wing Caravan flight, 5H-MSA = aircraft, plain 83F_Pilot files:
  see below). Source: Google Drive file id 1TfIMezxB3B_ZaDQr9poERRIj0r0PAlaw (zip, download via
  `curl -sL "https://docs.google.com/uc?export=download&id=1TfIMezxB3B_ZaDQr9poERRIj0r0PAlaw&confirm=t"`).
- `/tmp/erq/q*.gpx` — EarthRanger autofetch GPX extracted from `upload_queue.file_content`
  (per-track `<er:subject_type>` = ground truth: person/vehicle/aircraft).
  Re-extract: `sqlite3 db.sqlite3 "SELECT writefile('/tmp/erq/qID.gpx', file_content) FROM upload_queue WHERE id=ID"`.

## Current status (all committed)
`srv/gpx/movement_classifier.go` changes:
- Removed all MedianIntervalSec/IntervalConsistency scoring rules (interval-based, forbidden).
- Takeoff/landing roll: threshold 80→95 km/h, capped at 2.5 km (longer = car on road), landing-roll bug fixed.
- Climb-rate glitch filter (>30 m/s discarded); speeds >400 km/h discarded as GPS glitches.
- Robust elevation range/max via P2–P98 percentiles (GPS spikes previously gave 3486 m ranges).
- New metric `InteriorStopEvents`: stationary runs >3 min mid-track ⇒ penalty for aircraft
  (capped 3.0, skipped when p90>150 km/h since helicopters land mid-patrol).
- Elevation evidence gated on speed (car descending escarpment ≠ aircraft); extremeClimb
  (range>2000 m @ >40 km/h) strongly boosts aircraft and suppresses vehicle score.
- Vehicle evidence: p90 13–80 km/h burst rule + max>25 km/h rule (slow trucks vs foot).
- Aircraft subtype: dropped accel-based + turn/linearity rules (fixed-wing survey grids look
  like heli maneuvering); now uses P10 speed floor (>100 = fixed, <50 = rotor), SpeedCV, hover ratio.
  Track-name hints (helicopter_/fixed_wing_name_hint) return directly at 0.9 confidence
  (user-authored labels, subtype only).

Results:
- All 14 /tmp/gpxtest files classify correctly, movement-only (`go run ./cmd/gpx-classify /tmp/gpxtest/*.gpx`).
- `go test ./srv/gpx/` passes; `go test ./srv/...` passes.
- ER verification (movement-only, moving tracks, harness formerly at /tmp/erqv/main.go — rewrite as needed):
  vehicle: 142 ok / 17→foot (slow trucks creeping ~5 km/h all day — genuinely ambiguous);
  aircraft: 21/22; foot(er_mobile/ranger "person"): mostly "wrong" but truth is unreliable —
  er_mobile phones at 140 km/h avg over 300+ km ARE in aircraft; ER subject_type=person is
  the device carrier, not the movement mode. With production hints path, vehicle/aircraft 100% correct.

## Remaining work (step 2 of user request)
- Verify/fix the **classifier & detection** steps end-to-end:
  - `srv/gpx_validation.go` ValidateAndClassifyGPX (road-trace/static/boundary detection, merging)
  - `srv/gpx_learner.go` (learning queue: airstrip/road/place learning from classified segments)
  - `srv/upload_queue.go` → `persistUploadWithValidation` → `persistUpload` (movement_type majority
    vote per upload; check per-segment types persisted correctly to track_points.movement_type)
- Note: stored `gpx_uploads.movement_type` for old autofetch rows disagrees with new classifier in
  ~2/3 cases mostly because the whole multi-track file gets ONE majority type. Consider whether
  historical rows should be reprocessed.
- After changes: `make build && sudo systemctl restart 5mp` (version footer must match git HEAD).

## Tools
- `cmd/gpx-classify/main.go` — CLI: `go run ./cmd/gpx-classify [-hints] [-segments] file.gpx...`
  prints movement type/subtype/activity/confidence + all metrics per track.

## Untouched / unrelated dirty files in repo
- `db/migrations/033-*.sql`, various *.md at repo root, data/raw-fire updates — from other work,
  left uncommitted deliberately.
