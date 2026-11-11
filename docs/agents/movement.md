# Movement classification and the GPX learner

What kind of thing made a track (foot / vehicle / aircraft; boat; plane vs
helicopter) and what the learner derives from it (airstrips). **Decided from
movement alone** — speed, acceleration, turning — not from device labels and
not from the tracker's reporting interval. Labels (EarthRanger `<er:*>`, track
names) still win in production when present; the harness strips them, because
the ask is that plain GPX works.

## The harness — run it before and after any change here

```bash
go run ./cmd/movement-eval                 # score, compare to data/eval/movement/baseline.json
go run ./cmd/movement-eval -misses 20      # + largest misclassified segments, unlisted-strip candidates
go run ./cmd/movement-eval -resample 60    # decimate every track to 1 fix / N s: interval robustness
go run ./cmd/movement-eval -source raw     # one corpus only (raw | history | labelled)
go run ./cmd/movement-eval -snapshot db.sqlite3   # refresh the corpus from the live DB (read-only)
```

It replays every labelled track through the **production** path
(`SplitIntoSegments → RemoveStraightLineGaps → ValidateAndClassifyGPX`) with
all hints stripped and scores against device truth. Corpus under
`data/eval/movement/` (gitignored — rebuild with `-snapshot`):

| Corpus | Truth | Note |
|---|---|---|
| `raw/*.gpx` | `<er:subject_type/subtype>` of device-mounted trackers | only 7 days exist (`upload_queue` is pruned); **held out** from model training |
| `history.jsonl.gz` | the type production stored *with* the ER hint | recovered from `gpx_upload_logs` sampled points (≤300/segment); "foot" there is weak |
| `labelled/*.gpx` | filename prefix (`car_`, `boat_`, `helicopter_`, `plane_`, `foot_`) | only 2 survive; **there is no real boat track** — drop one here if found |
| `airfields.json` | OurAirports, Tanzania | bush strips are under-listed: a floor, not truth |

`baseline.json` is the pre-2026-11 code. Do not overwrite it casually
(`-write-baseline`); it is what "better" is measured against.

## Design: the journey is the unit, not the 30-minute window

`SplitIntoSegments` cuts 30-minute windows. A window cannot show a take-off, a
landing, or an hour-long speed plateau, so three passes in
`ValidateAndClassifyGPX` look at the whole **journey** (`JourneyChains`:
maximal runs of segments that are one tracker's continuous movement, whatever
each was classified as):

1. `refineTypesByJourney` (before the merge) — flight must be *sustained* (≥2
   consecutive hops > 90 km/h for 2 min; one fast hop is a GPS glitch); a
   "vehicle" window beside a flight with hops ≥ 60 km/h is that flight; a
   "foot" window that moved < 1 km/h beside a drive is the truck standing.
2. `mergeAdjacentSegments` — only merges segments that are **contiguous**
   (`pointsContiguous`). An autofetch file is many trackers end to end; without
   this the last window of one truck fused with the first of the next and the
   learner got teleporting tracks.
3. `assignJourneySubtypes` → `gpx.JourneySubtype`.
   - **Boat** (`BoatFromMovement`): a sustained speed plateau — ≥30 min, ≥12
     fixes, CV ≤ 0.25, max ≤ 1.3× avg, p10 ≥ 0.6× avg, no stop. Of 1,040
     device-labelled truck journeys ≥ 1 h none had CV < 0.22. Too short to
     show a plateau → no subtype, not a guess.
   - **Plane vs helicopter** (`AircraftSubtypeFromMovement`): a standardised
     logistic regression over `AircraftFeatures`, computed on a uniform 60 s
     time grid in units of the journey's own cruise speed. The signal is in
     the terminals: a plane is taxiing/rolling the minute before lift-off and
     after touchdown, a helicopter is still. Abstains (`""`) inside its band.
     **Absolute speed is deliberately not a feature** — here the helicopter
     cruises faster than the plane, which is a fact about two airframes.

`srv/upload.go` copies the journey subtype onto the raw segments, so the
effort map stores it (the per-window guess used to: one truck-km in six was
"boat").

### Retraining the aircraft model

`srv/gpx/aircraft_subtype_model.go` is **generated** — never edit it.

```bash
go run ./cmd/movement-eval -dump-aircraft /tmp/air.csv
python3 scripts/train_aircraft_subtype.py /tmp/air.csv   # trains on `history` only
go test ./srv/gpx && go run ./cmd/movement-eval
```

Features come from the same Go code production runs; thresholds are chosen on
grouped CV inside the training corpus, never on `raw`/`labelled`.
`TestAircraftModelMatchesFeatures` fails if the feature list drifts.

## The learner: airstrips

`AirfieldObservations` → `gpx.DetectAirfieldEvents`, once per job per journey.
An event needs **both sides observed**: sustained flight on one, the aircraft
standing (≤ 5 km/h for a minute, ≥ 2 min on the ground) on the other, no fix
gap > 4 min between. A journey that begins or ends in the air says nothing.

The rule it replaced took both ends of every aircraft *segment* as take-off
and landing — i.e. every window edge, park-boundary crossing and file end —
and `detectAirstrips` additionally called any straight 500 m with an abrupt end
an airstrip, on trucks too. Low grid speed alone is also not ground: a plane
orbiting covers little net distance per minute.

## The event learner (2026-11-11) — places, roads, airstrips

Replaced the counter learner (`confidence = min(uses*20, 90)`, approve at a
fixed count). Its `detectStops` / `detectBases` / `simplifyCoords` compared
**kilometres with radii written in metres**, so every 30-minute segment was a
stop: that was the >1,000 pending places.

- **Events, not counters.** `learned_events` (migration 073) holds one row per
  landing, take-off, stop and road traversal, de-duplicated park-wide on
  tracker + time + kind (`SameEvent`); vehicle-track time already read is kept
  as kind `coverage`, so a re-delivered window adds nothing. The harness
  delivers every archive twice and requires **0** added events.
- **Evidence** (`srv/learned_evidence.go`): distinct days / journeys /
  trackers, both directions, axis agreement, runway line + length fitted from
  ground-run fixes (**absent if unmeasured**, and "at least N m"), fixed-wing
  vs rotor journeys (rotor-only = `landing_zone`; subtype unmeasured =
  `airfield`), reference-list match as a bonus, dwell, OSM/HeiGIT agreement.
- **Score** = P(used again within 28 d), sign-constrained logistic + Platt,
  fitted on the early days and thresholded on the later ones:
  `go run ./cmd/movement-eval -replay-only -dump-learner x.csv` →
  `scripts/train_learner_score.py x.csv` → `srv/learned_score_model.go`
  (**generated, never edit**). Thresholds target 97.5 % so the approved
  *state* holds ≥ 95 % through the demotion grace (`learnedDemoteMinIdle`).
- **States**: `auto_approved` / `pending` / `rejected`, each with a
  `status_reason`. `approved_by = 'auto'` marks a learner decision; a
  reviewer's `approved` or `rejected` is never overridden. Idle time is counted
  in days the *park* was active, so a quiet park demotes nothing.
- **Movement only**: `classifyByMovementOnly` (`srv/upload_queue.go`) moves ER
  device labels into `ClassifiedSegment.Label` before classifying.
- **Map**: `GET /api/learned-map` (client tenant only, approved only) +
  `srv/static/learned.js` (`?learned=0` hides). Nothing is drawn unasked
  (a guest link showed seven unexplained white discs, reported as a Safari
  fault): airstrips, runways and landing zones draw only for parks whose
  `infrastructure` ("Airstrips") pin is on, and that pin's own dot is hidden
  in their favour. The Airstrips and Patrol visits pins *and* their popup
  rows read `/api/learned-map` through `learnedForPark()` (not the admin
  list, 401 for guests), so a share link made after pinning carries them
  wherever the link carries patrol data, and a row exists exactly where its
  pin can draw. An airstrip is the Lucide `plane` icon, stroked white on a
  dark casing, no disc (a bitmap — alpha-as-SDF made the stroke muddy); road sections draw only for parks whose `road` pin is on
  and places only for parks whose `place` pin is on *and* `visits` pin is off
  (the visits circles are the same sites and borrow the learned tip via
  `learnedPlaceTip`). Fed from `updatePinnedIndicator`
  (`LearnedMap.setRoadParks` / `setPlaceParks` / `setAirParks`). Stacking is re-asserted by
  `LearnedMap.lift()` — also called from `lodlayer.js` `restack` — tracks
  (dark casing + dashes) over pinned roads, pin markers/labels over tracks. A MapLibre zoom
  expression must be outermost — nesting it in a `case` silently dropped the
  whole points layer.
- **Raw input is kept**: `data/autofetch_archive/<env>/*.gpx.gz`;
  `cleanupUploadQueue` deletes an autofetch row only once archived. Rebuild:
  `scripts/learner_rebuild_backup.sh <env> <parks> --delete`, then
  `cmd/autofetch-backfill` (polite, resumable, state in
  `data/autofetch_backfill/`). Re-run the replay after the archive grows.

Known flaws, measured not fixed: recall is 17–31 % at this precision (140 days
of history); one runway can yield a second "airstrip" at its far end; an event
outside every park is credited to the park its tracker last flew in (Dar es
Salaam and Morogoro appear under Nyerere); other parks touched by af12 tracks
(Udzungwa, Uluguru, …) still hold old counter-era rows (`evidence_json IS
NULL`) beside new ones; `gpx_learning_queue` id 2348 is stuck `processing`.

## Admin panel: Activity + Learner (2026-11-11)

The reviewer surface for everything above. Frontend `srv/static/admin.js`
(`window.AdminUI`), shell markup in `globe.html` `#admin-panel`, CSS at the end
of `globe.css`. Backend `srv/admin_learner.go` (`GET /api/admin/learner`,
`POST /api/admin/learner/release`) and `srv/admin_activity.go`
(`GET /api/admin/activity`, `/activity/detail`, and the `log` block on each
`/api/admin/autofetch` source). All client-tenant only, like the tables.

- Tabs are `uploads` (Activity), `learning` (Learner), `autofetch`, `access`,
  `map-settings`. `pending` and `features` are aliases in `ADMIN_TAB_ALIAS`
  (old links keep working) — do not re-add them as tabs.
- **No "approve all above N %".** The old button used 75 against a
  `confidence_pct`; the calibrated lines are per family (`model.approve`), and
  a feature under its line is undecided on purpose. Bulk actions act only on
  rows the reviewer ticked.
- A row prints the score **with its line** (`approve_at`); `score: null`
  (counter-era, reviewer-approved) prints "not scored", never 0 %.
- Approve / Reject set a reviewer decision the learner never overrides;
  **Hand back** (`/learner/release`) clears it and re-decides from stored
  evidence. Undo restores who had decided, not just the status.
- Activity rows lead with the period the tracks **cover** (`gpx_uploads.
  start_time/end_time`), not arrival: a backfill arrives in one afternoon. The
  fetch-log bars on an autofetch card are ordered the same way.
- `upload_time` is stored in three spellings; `logTime()` normalises. The
  detail endpoint aggregates segments server-side (the stored JSON is ~2.8 MB
  per weekly file) and returns the 12 longest legs *of N*.
- Aircraft km with no measured airframe is its own bar
  (`aircraft_unknown`), never folded into plane or helicopter.
- Removed with the old tabs: `/api/admin/gpx-logs`, `/pending-approvals`,
  `/learning-results`, `/upload-detail`, `/approve-high-confidence`.
  `/api/admin/learned-features` stays (the park panel reads it).
- A learning job left `processing` by a restart is settled at startup
  (`GPXLearner.recoverInterrupted`): requeued if its upload exists, closed as
  `cancelled` if not.

## Measured (2026-11-11; 536 files, 745,833 points, hints stripped)

| | before | after |
|---|---|---|
| type macro recall, km / segments | 98.6 % / 72.0 % | 99.0 % / 79.7 % |
| vehicle segments called foot | 3,516 | 2,000 |
| land-vehicle km called boat | 15.8 % | 0.1 % |
| plane/helicopter macro recall, km (all / held-out `raw`) | 43 % / 27 % | 78 % / 79 % |
| rotor_wing precision, km | 5.8 % | 70 % |
| fixed-wing learner: sites / used once / events at a reused site | 380 / 175 / 63 % | 35 / 11 / 92 % |

## Known limits — do not paper over

- **One fleet.** One helicopter, one or two planes, one landscape. The
  aircraft model is out-of-sample in *time* (`raw`), not in *airframe*.
- **Sampling.** Subtype needs fixes ≤ ~60 s: at `-resample 180` plane/heli
  macro recall falls to 57 %. Type and boat rules hold at 180 s.
- **Boat recall is unmeasured** (no real boat track); only its false-positive
  rate is. `TestBoatFromMovement` is synthetic.
- **Vehicle → foot, 2,000 segments / 1,219 km** remain: a truck creeping at
  2–9 km/h for a window is not separable from a walker by movement.
- `learned_airstrips` still holds ~250 pending rows from the old rule; they
  are not deleted (needs confirmation), and re-delivered autofetch windows can
  count one landing twice.
- `haversineDistance` returns **km**, `extractAircraftPatterns` treats it as
  metres (legacy path, now only used by the harness comparison).
