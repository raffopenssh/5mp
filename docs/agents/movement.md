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
