package srv

// The learner's unit of evidence is an EVENT: one landing, one take-off, one
// stop, one traversal of a road section -- each with the tracker that made it
// and the time it happened. Everything the learner publishes (an airstrip, a
// place, a road and how sure it is) is an aggregate over events and can be
// recomputed from them.
//
// It replaced counters (landing_count += 1) that could not tell a second
// landing from the same landing delivered twice, could not count days or
// trackers, and could not be rebuilt. Two unit bugs hid behind them:
// detectStops and detectBases compared haversineDistance (KILOMETRES) with
// radii written in metres, so "within 50 m" was "within 50 km" -- every
// 30-minute segment was one stop, and every track end within 100 km was one
// base. That is where 1,400 pending places came from.
//
// Nothing in this file reads a device label or a reporting interval.

import (
	"math"
	"sort"
	"strconv"
	"time"

	"srv.exe.dev/srv/gpx"
)

// LearnerEvent is one observed thing a feature can be learned from.
type LearnerEvent struct {
	ID         int64
	Kind       string // landing | takeoff | stop | traversal
	Tracker    string
	Start, End time.Time
	Lat, Lon   float64
	HeadingDeg float64 // run direction of a landing / take-off; NaN when unknown
	// Subtype: for airfield events the journey's airframe (fixed_wing |
	// rotor_wing | "" when the model abstained); for stops how the tracker
	// had been moving (foot | vehicle | aircraft).
	Subtype  string
	Journey  string       // tracker + journey start: distinct journeys, not raw events
	DwellMin float64      // stops
	Roll     [][3]float64 // airfield events: ground-run fixes [lon, lat, km/h]
	// FeatureID is the learned_* row the event is evidence for (0 = not yet assigned).
	FeatureID int64
}

const (
	stopRadiusM      = 150.0            // a stay: every fix within this of the first
	stopMinDwell     = 20 * time.Minute // shorter is a pause, not a place
	stopMaxSilence   = 24 * time.Hour   // a longer silence hides whatever happened in it
	stopMergeGap     = 30 * time.Minute // two stays this close in time and space are one
	stopOutlierMax   = 10 * time.Minute // an excursion this short does not end a stay
	airfieldSiteM    = 1000.0           // events within this of a site are that site
	placeSiteM       = 200.0
	rollMinKmh       = 20.0 // slower is taxiing: it draws the apron, not the runway
	rollMaxKmh       = 80.0 // faster is, or is about to be, off the ground
	rollMaxClimbM    = 20.0
	airfieldDedupe   = 10 * time.Minute // same tracker, same kind, this close: one event
	traversalDedupe  = 30 * time.Minute
	traversalMinM    = 300.0 // a traversal covers at least this much of a section ...
	traversalMinFrac = 0.5   // ... or half of it
)

func metersBetween(lat1, lon1, lat2, lon2 float64) float64 {
	return haversineDistanceKm(lat1, lon1, lat2, lon2) * 1000
}

func eventDay(t time.Time) string { return t.UTC().Format("2006-01-02") }

// trackerOf names the device behind a segment. A file without track names
// (a hand-recorded GPX) has no identity to offer; its segments share "".
func trackerOf(cs ClassifiedSegment) string { return cs.Tracker }

// ExtractLearnerEvents returns the landings, take-offs and stops in one
// upload's classified segments. Pure: cmd/movement-eval replays history
// through exactly this.
func ExtractLearnerEvents(segs []ClassifiedSegment) []LearnerEvent {
	var out []LearnerEvent

	// Airfield events: one pass per journey, so the taxi windows classified
	// "vehicle" either side of a flight are read together with it.
	for _, chain := range JourneyChains(segs) {
		sub, air := "", false
		for _, i := range chain {
			if segs[i].MovementType == "aircraft" {
				air = true
				if sub == "" {
					sub = segs[i].MovementSubtype
				}
			}
		}
		if !air {
			continue
		}
		pts := ChainPoints(segs, chain)
		tracker := trackerOf(segs[chain[0]])
		journey := tracker
		for _, p := range pts {
			if p.Time != nil {
				journey += "@" + strconv.FormatInt(p.Time.Unix(), 10)
				break
			}
		}
		for _, e := range gpx.DetectAirfieldEvents(pts) {
			out = append(out, LearnerEvent{
				Kind: e.Kind, Tracker: tracker, Start: e.Time, End: e.Time,
				Lat: e.Lat, Lon: e.Lon, HeadingDeg: e.HeadingDeg, Subtype: sub,
				Journey: journey, Roll: rollFixes(e),
			})
		}
	}

	// Stops: per tracker over ALL its fixes in the upload. A stay does not
	// respect 30-minute windows, and the windows it fills are classified
	// "idle" or "static" -- reading stops per segment missed every long one.
	type trk struct {
		pts []gpx.Point
		km  map[string]float64
	}
	byTracker := map[string]*trk{}
	var order []string
	for _, cs := range segs {
		k := trackerOf(cs)
		t := byTracker[k]
		if t == nil {
			t = &trk{km: map[string]float64{}}
			byTracker[k] = t
			order = append(order, k)
		}
		for _, p := range cs.Points {
			if p.Time != nil {
				t.pts = append(t.pts, p)
			}
		}
		if cs.MovementType != "" {
			t.km[cs.MovementType] += cs.DistanceKm
		}
	}
	for _, k := range order {
		t := byTracker[k]
		how, best := "", 0.0
		for _, m := range []string{"aircraft", "vehicle", "foot"} {
			if t.km[m] > best {
				how, best = m, t.km[m]
			}
		}
		for _, s := range detectStays(t.pts) {
			s.Tracker, s.Subtype = k, how
			out = append(out, s)
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Start.Before(out[j].Start) })
	return out
}

// detectStays is stay-point detection (Li et al. 2008; Zheng 2009) with the
// two corrections later work found necessary (Montoliu 2013; Aslak &
// Alessandretti's Infostop 2020):
//
//   - the anchor is the running CENTROID of the stay, not its first fix. A
//     first fix taken while still rolling in, or one bad fix, otherwise cuts
//     a night at camp into several "stops";
//   - a short excursion outside the radius (GPS multipath under canopy, a
//     walk to the river) does not end the stay if the tracker is back inside
//     within stopOutlierMax.
//
// A tracker that goes quiet while parked shows as two fixes hours apart at
// one spot; that is a stay too, up to stopMaxSilence.
func detectStays(pts []gpx.Point) []LearnerEvent {
	sort.SliceStable(pts, func(i, j int) bool { return pts[i].Time.Before(*pts[j].Time) })
	var stays []LearnerEvent
	for i := 0; i < len(pts); {
		clat, clon, n := pts[i].Lat, pts[i].Lon, 1.0
		lats, lons := []float64{pts[i].Lat}, []float64{pts[i].Lon}
		j := i // last inlier
		for k := i + 1; k < len(pts); k++ {
			if pts[k].Time.Sub(*pts[j].Time) > stopMaxSilence {
				break
			}
			if metersBetween(clat, clon, pts[k].Lat, pts[k].Lon) <= stopRadiusM {
				clat, clon, n = (clat*n+pts[k].Lat)/(n+1), (clon*n+pts[k].Lon)/(n+1), n+1
				lats, lons = append(lats, pts[k].Lat), append(lons, pts[k].Lon)
				j = k
				continue
			}
			if pts[k].Time.Sub(*pts[j].Time) > stopOutlierMax {
				break // gone for longer than an excursion: the stay ended at j
			}
		}
		dwell := pts[j].Time.Sub(*pts[i].Time)
		if dwell < stopMinDwell {
			i++
			continue
		}
		s := LearnerEvent{Kind: "stop", Start: *pts[i].Time, End: *pts[j].Time,
			Lat: median(lats), Lon: median(lons), HeadingDeg: math.NaN(), DwellMin: dwell.Minutes()}
		if n := len(stays); n > 0 && sameStay(stays[n-1], s) {
			stays[n-1] = mergeStay(stays[n-1], s)
		} else {
			stays = append(stays, s)
		}
		i = j + 1
	}
	return stays
}

func sameStay(a, b LearnerEvent) bool {
	return a.Tracker == b.Tracker &&
		metersBetween(a.Lat, a.Lon, b.Lat, b.Lon) <= stopRadiusM &&
		!b.Start.After(a.End.Add(stopMergeGap)) && !a.Start.After(b.End.Add(stopMergeGap))
}

func mergeStay(a, b LearnerEvent) LearnerEvent {
	if b.Start.Before(a.Start) {
		a.Start = b.Start
	}
	if b.End.After(a.End) {
		a.End = b.End
	}
	a.DwellMin = a.End.Sub(a.Start).Minutes()
	return a
}

// rollFixes keeps the fixes of a take-off or landing run that were made ON
// the runway: moving faster than a taxi, slower than flight, and (where the
// tracker reports height) no higher than where the aircraft stood.
func rollFixes(e gpx.AirfieldEvent) [][3]float64 {
	var out [][3]float64
	var groundEle *float64
	for _, p := range e.Roll {
		if p.Lat == e.Lat && p.Lon == e.Lon && p.Elevation != nil {
			groundEle = p.Elevation
		}
	}
	for i, p := range e.Roll {
		kmh := -1.0
		hop := func(a, b gpx.Point) float64 {
			dt := b.Time.Sub(*a.Time).Hours()
			if dt <= 0 {
				return -1
			}
			return haversineDistanceKm(a.Lat, a.Lon, b.Lat, b.Lon) / dt
		}
		// The faster of the two hops touching the fix: a fix reached at
		// flying speed, or left at it, is not a runway fix.
		if i > 0 {
			kmh = hop(e.Roll[i-1], p)
		}
		if i+1 < len(e.Roll) {
			kmh = math.Max(kmh, hop(p, e.Roll[i+1]))
		}
		if kmh < rollMinKmh || kmh > rollMaxKmh {
			continue
		}
		if groundEle != nil && p.Elevation != nil && math.Abs(*p.Elevation-*groundEle) > rollMaxClimbM {
			continue
		}
		out = append(out, [3]float64{p.Lon, p.Lat, math.Round(kmh)})
	}
	return out
}

// SameEvent is the event-level de-duplication rule: the same tracker doing
// the same kind of thing at the same time is one event, however many times
// its fixes are delivered. Stops are intervals and merge when they touch;
// the others are instants.
func SameEvent(a, b LearnerEvent) bool {
	if a.Kind != b.Kind || a.Tracker != b.Tracker {
		return false
	}
	switch a.Kind {
	case "stop":
		return sameStay(a, b)
	case "traversal":
		return a.FeatureID == b.FeatureID && absDur(a.Start.Sub(b.Start)) <= traversalDedupe
	default:
		return absDur(a.Start.Sub(b.Start)) <= airfieldDedupe &&
			metersBetween(a.Lat, a.Lon, b.Lat, b.Lon) <= airfieldSiteM
	}
}

func absDur(d time.Duration) time.Duration {
	if d < 0 {
		return -d
	}
	return d
}

// VehicleTrack is one vehicle segment as the road learner reads it.
type VehicleTrack struct {
	Tracker string
	Coords  [][3]float64 // lon, lat, unix seconds (0 when untimed)
}

// VehicleTracks returns the land-vehicle segments of an upload. A boat does
// not draw roads.
func VehicleTracks(segs []ClassifiedSegment) []VehicleTrack {
	var out []VehicleTrack
	for _, cs := range segs {
		if cs.MovementType != "vehicle" || cs.MovementSubtype == "boat" || len(cs.Points) < 2 {
			continue
		}
		if cs.Classification != "patrol" && cs.Classification != "road" {
			continue
		}
		vt := VehicleTrack{Tracker: trackerOf(cs)}
		for _, p := range cs.Points {
			ts := 0.0
			if p.Time != nil {
				ts = float64(p.Time.Unix())
			}
			vt.Coords = append(vt.Coords, [3]float64{p.Lon, p.Lat, ts})
		}
		out = append(out, vt)
	}
	return out
}

// ── Sites: which events are the same place ────────────────────────────────

// Site is a learned point feature (airstrip / landing zone / place) as the
// clustering sees it: a fixed seed position and an id.
type Site struct {
	ID       int64
	Lat, Lon float64
}

// AssignSite returns the nearest site within radiusM of the event, or nil.
// The seed never moves once a site exists, so the assignment of an event does
// not depend on what arrives after it: replaying history in time order gives
// the same sites as living through it.
func AssignSite(sites []Site, lat, lon, radiusM float64) *Site {
	var best *Site
	bd := radiusM
	for i := range sites {
		// cheap reject before the trigonometry
		if math.Abs(sites[i].Lat-lat) > radiusM/111000 {
			continue
		}
		if d := metersBetween(sites[i].Lat, sites[i].Lon, lat, lon); d <= bd {
			best, bd = &sites[i], d
		}
	}
	return best
}
