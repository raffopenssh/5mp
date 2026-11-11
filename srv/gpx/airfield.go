package gpx

import (
	"math"
	"time"
)

// AirfieldEvent is one observed transition between ground and flight.
type AirfieldEvent struct {
	Kind       string // "takeoff" | "landing"
	Lat, Lon   float64
	Time       time.Time
	HeadingDeg float64 // direction of the take-off / landing run; NaN if unknown
	GroundMin  float64 // minutes observed on the ground next to the event
	// Roll is every fix between standing and flying (the take-off or landing
	// run and the minute either side). The learner measures a runway from
	// these across many events; one event rarely has more than two or three.
	Roll []Point
}

const (
	airfieldGroundKmh    = 25.0 // at or below: on the ground (parked, taxiing)
	airfieldStillKmh     = 5.0  // at or below for a full minute: standing
	airfieldMinGroundMin = 2    // minutes on the ground for the stop to count
	airfieldMinRunMin    = 3    // a flight run shorter than this is a GPS spike
	airfieldLookMin      = 3    // ground must be seen within this many minutes of the run
	airfieldMaxStepSec   = 240  // a fix gap longer than this hides whatever happened in it
)

// DetectAirfieldEvents finds where an aircraft journey actually left the
// ground and came back to it.
//
// The rule it replaces took the first and last point of every aircraft
// *segment* to be a take-off and a landing. A segment ends where a 30-minute
// window, a park boundary or the upload itself ends, so that rule learned an
// "airstrip" at every such edge — 250 pending ones in two parks. Here an event
// exists only when BOTH sides of the transition are observed: sustained flight
// on one side, the aircraft on the ground on the other, and no gap in the
// fixes between them wide enough to hide a different story. A journey that
// begins or ends in the air says nothing about where it landed.
func DetectAirfieldEvents(points []Point) []AirfieldEvent {
	v, _, _, ok := journeyKinematics(points)
	if !ok {
		return nil
	}
	var pts []Point // timed, strictly increasing: the same filter journeyKinematics applies
	for _, p := range points {
		if p.Time == nil || (len(pts) > 0 && !p.Time.After(*pts[len(pts)-1].Time)) {
			continue
		}
		pts = append(pts, p)
	}
	t0 := *pts[0].Time
	at := func(step int) time.Time { return t0.Add(time.Duration(float64(step)*journeyGridSec) * time.Second) }

	// observed: the fixes around [a, b] are dense enough to trust the
	// interpolated speed there.
	observed := func(a, b time.Time) bool {
		prev := -1
		for i := range pts {
			if pts[i].Time.Before(a.Add(-airfieldMaxStepSec * time.Second)) {
				continue
			}
			if prev >= 0 && pts[i].Time.Sub(*pts[prev].Time).Seconds() > airfieldMaxStepSec {
				return false
			}
			prev = i
			if pts[i].Time.After(b) {
				break
			}
		}
		return prev >= 0
	}
	// fixAt is the last fix at or before t (after=false) or the first at or after it.
	fixAt := func(t time.Time, after bool) Point {
		best := pts[0]
		for _, p := range pts {
			if after {
				if !p.Time.Before(t) {
					return p
				}
				best = p
			} else {
				if p.Time.After(t) {
					return best
				}
				best = p
			}
		}
		return best
	}
	// groundMinutes counts the consecutive ground minutes from a step, and
	// reports whether the aircraft actually stood still in them. Low speed on
	// the grid is not enough: a plane orbiting a carcass covers little NET
	// distance per minute and reads as 20 km/h. Parked is parked.
	groundMinutes := func(from, dir int) (float64, bool) {
		n, still := 0, false
		for i := from; i >= 0 && i < len(v) && v[i] <= airfieldGroundKmh; i += dir {
			n++
			if v[i] <= airfieldStillKmh {
				still = true
			}
		}
		return float64(n) * journeyGridSec / 60, still
	}

	between := func(a, b time.Time) []Point {
		var out []Point
		for _, p := range pts {
			if !p.Time.Before(a) && !p.Time.After(b) {
				out = append(out, p)
			}
		}
		return out
	}

	var events []AirfieldEvent
	for s := 0; s < len(v); {
		if v[s] <= flightSpeedKmh {
			s++
			continue
		}
		e := s
		for e < len(v) && v[e] > flightSpeedKmh {
			e++
		}
		if e-s >= airfieldMinRunMin {
			// Take-off: ground seen shortly before the run began.
			for g := s - 1; g >= 0 && g >= s-airfieldLookMin; g-- {
				if v[g] > airfieldGroundKmh {
					continue
				}
				if gm, still := groundMinutes(g, -1); still && gm >= airfieldMinGroundMin && observed(at(g), at(s+1)) {
					p := fixAt(at(g+1), false)
					q := fixAt(at(s+1), true)
					events = append(events, AirfieldEvent{"takeoff", p.Lat, p.Lon, *p.Time, bearingDeg(p, q), gm,
						between(at(g).Add(-time.Minute), at(s+1))})
				}
				break
			}
			// Landing: ground seen shortly after it ended.
			for g := e; g < len(v) && g < e+airfieldLookMin; g++ {
				if v[g] > airfieldGroundKmh {
					continue
				}
				if gm, still := groundMinutes(g, +1); still && gm >= airfieldMinGroundMin && observed(at(e-1), at(g+1)) {
					p := fixAt(at(g), true)
					q := fixAt(at(e-1), false)
					events = append(events, AirfieldEvent{"landing", p.Lat, p.Lon, *p.Time, bearingDeg(q, p), gm,
						between(at(e-1), at(g+1).Add(time.Minute))})
				}
				break
			}
		}
		s = e
	}
	return events
}

func bearingDeg(a, b Point) float64 {
	if a.Lat == b.Lat && a.Lon == b.Lon {
		return math.NaN()
	}
	la1, la2 := a.Lat*math.Pi/180, b.Lat*math.Pi/180
	dl := (b.Lon - a.Lon) * math.Pi / 180
	y := math.Sin(dl) * math.Cos(la2)
	x := math.Cos(la1)*math.Sin(la2) - math.Sin(la1)*math.Cos(la2)*math.Cos(dl)
	return math.Mod(math.Atan2(y, x)*180/math.Pi+360, 360)
}
