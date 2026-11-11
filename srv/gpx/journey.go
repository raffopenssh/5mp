package gpx

import (
	"math"
	"sort"
)

// Journey-level subtype classification from movement alone.
//
// The 30-minute window the pipeline splits on is the wrong unit for "what kind
// of machine is this": a plane and a helicopter differ in how they leave the
// ground and come back to it, a boat in how long it holds one speed — and
// neither is visible inside half an hour of cruise. So subtypes are decided on
// the whole continuous journey (the merged segment), and read only what the
// thing DID: speed, acceleration, turning. Nothing here looks at names, device
// metadata, or the tracker's reporting interval — every feature is computed on
// a uniform 60-second time grid so that a 15-second and a 60-second tracker on
// the same flight produce the same numbers (cmd/movement-eval -resample).

const journeyGridSec = 60.0

// journeyKinematics resamples points onto a uniform time grid and returns the
// per-step ground speed (km/h), heading change (degrees per step, len-1) and
// speed change (km/h per step, len-1). ok is false without enough timed points.
func journeyKinematics(points []Point) (v, w, a []float64, ok bool) {
	var ts, xs, ys []float64
	var lat0 float64
	n := 0
	for _, p := range points {
		if p.Time != nil {
			lat0 += p.Lat
			n++
		}
	}
	if n < 4 {
		return nil, nil, nil, false
	}
	cos0 := math.Cos(lat0 / float64(n) * math.Pi / 180)
	const kmPerDeg = 6371.0 * math.Pi / 180
	for _, p := range points {
		if p.Time == nil {
			continue
		}
		t := float64(p.Time.UnixNano()) / 1e9
		if len(ts) > 0 && t <= ts[len(ts)-1] {
			continue // out of order / duplicate fix
		}
		ts = append(ts, t)
		xs = append(xs, p.Lon*cos0*kmPerDeg)
		ys = append(ys, p.Lat*kmPerDeg)
	}
	if len(ts) < 4 {
		return nil, nil, nil, false
	}
	steps := int((ts[len(ts)-1] - ts[0]) / journeyGridSec)
	if steps < 5 {
		return nil, nil, nil, false
	}
	gx := make([]float64, steps+1)
	gy := make([]float64, steps+1)
	j := 0
	for i := 0; i <= steps; i++ {
		t := ts[0] + float64(i)*journeyGridSec
		for j < len(ts)-2 && ts[j+1] < t {
			j++
		}
		f := (t - ts[j]) / (ts[j+1] - ts[j])
		gx[i] = xs[j] + f*(xs[j+1]-xs[j])
		gy[i] = ys[j] + f*(ys[j+1]-ys[j])
	}
	v = make([]float64, steps)
	hd := make([]float64, steps)
	for i := 0; i < steps; i++ {
		dx, dy := gx[i+1]-gx[i], gy[i+1]-gy[i]
		v[i] = math.Hypot(dx, dy) / (journeyGridSec / 3600)
		hd[i] = math.Atan2(dx, dy) * 180 / math.Pi
	}
	w = make([]float64, steps-1)
	a = make([]float64, steps-1)
	for i := 0; i < steps-1; i++ {
		d := math.Mod(hd[i+1]-hd[i]+540, 360) - 180
		w[i] = math.Abs(d)
		a[i] = v[i+1] - v[i]
	}
	return v, w, a, true
}

// quantile is numpy's default (linear) percentile on a copy of xs.
func quantile(xs []float64, q float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	s := append([]float64(nil), xs...)
	sort.Float64s(s)
	pos := q / 100 * float64(len(s)-1)
	lo := int(math.Floor(pos))
	hi := int(math.Ceil(pos))
	return s[lo] + (pos-float64(lo))*(s[hi]-s[lo])
}

// flightSpeedKmh separates "airborne" grid steps from taxi and ground time.
const flightSpeedKmh = 90.0

// AircraftFeatureNames is the order of AircraftFeatures' vector; the generated
// model table (aircraft_subtype_model.go) is keyed by these names.
var AircraftFeatureNames = []string{
	"r_stop", "r_crawl", "r_slow", "r_mid", "r_near", "r_cruise", "r_fast", "r_cv",
	"a_q01", "a_q05", "a_q25", "a_q75", "a_q95", "a_q99", "a_absmean",
	"w_q50", "w_q75", "w_q90", "w_q99", "w_gt45", "w_gt90", "lat_q95",
	"stop_frac", "stops_per_h",
	"up_m2", "up_m1", "up_0", "up_1", "up_2", "up_3", "up_4",
	"dn_p2", "dn_p1", "dn_0", "dn_1", "dn_2", "dn_3", "dn_4",
	"run_min_med",
}

// AircraftFeatures describes HOW an aircraft journey flew, in units of its own
// cruise speed so that a slow bush plane and a fast turboprop look alike:
//
//	r_*      share of airborne-envelope time in bands of speed/cruise
//	a_*      quantiles of minute-to-minute speed change / cruise
//	w_*      quantiles of minute-to-minute heading change while flying
//	lat_q95  speed × turn (a bank-angle proxy)
//	stop_*   landings inside the envelope
//	up_k     speed/cruise k minutes around lift-off (m2,m1 = the two minutes
//	         before: a plane is taxiing and rolling, a helicopter is still)
//	dn_k     the same, mirrored, around touchdown
//
// NaN marks a feature the journey cannot express (no observed take-off);
// the model imputes it. ok is false when the journey never sustained flight.
func AircraftFeatures(points []Point) (f []float64, cruiseKmh float64, ok bool) {
	v, w, a, ok := journeyKinematics(points)
	if !ok {
		return nil, 0, false
	}
	var flying []float64
	i0, i1 := -1, -1
	for i, s := range v {
		if s > flightSpeedKmh {
			flying = append(flying, s)
			if i0 < 0 {
				i0 = i
			}
			i1 = i
		}
	}
	if len(flying) < 3 {
		return nil, 0, false
	}
	vc := quantile(flying, 50)
	ve := v[i0 : i1+1]
	nE := float64(len(ve))

	bands := []float64{0, 0.1, 0.3, 0.6, 0.8, 0.95, 1.05, math.Inf(1)}
	hist := make([]float64, 7)
	var hi []float64
	stops, stopSteps := 0, 0
	for i, s := range ve {
		r := s / vc
		for b := 0; b < 7; b++ {
			if r >= bands[b] && r < bands[b+1] {
				hist[b]++
				break
			}
		}
		if r > 0.6 {
			hi = append(hi, r)
		}
		if s < 8 {
			stopSteps++
			if i == 0 || ve[i-1] >= 8 {
				stops++
			}
		}
	}
	for b := range hist {
		hist[b] /= nE
	}
	f = append(f, hist...)
	f = append(f, stddev(hi))

	var ae, wf, lat []float64
	var absSum float64
	for i := i0; i < i1; i++ {
		x := a[i] / vc
		ae = append(ae, x)
		absSum += math.Abs(x)
		if v[i] > 0.6*vc && v[i+1] > 0.6*vc {
			wf = append(wf, w[i])
		}
		lat = append(lat, v[i]*w[i]/vc/100)
	}
	if len(ae) == 0 {
		ae, lat = []float64{0}, []float64{0}
	}
	if len(wf) == 0 {
		wf = []float64{0}
	}
	for _, q := range []float64{1, 5, 25, 75, 95, 99} {
		f = append(f, quantile(ae, q))
	}
	f = append(f, absSum/float64(len(ae)))
	for _, q := range []float64{50, 75, 90, 99} {
		f = append(f, quantile(wf, q))
	}
	var g45, g90 float64
	for _, x := range wf {
		if x > 45 {
			g45++
		}
		if x > 90 {
			g90++
		}
	}
	f = append(f, g45/float64(len(wf)), g90/float64(len(wf)), quantile(lat, 95))
	f = append(f, float64(stopSteps)/nE, float64(stops)/(nE*journeyGridSec/3600))

	// Terminal profiles, averaged over every sustained flight run (>= 6 min).
	up := make([]float64, 7)
	dn := make([]float64, 7)
	nUp, nDn := 0, 0
	var runLens []float64
	for s := 0; s < len(v); {
		if v[s] <= flightSpeedKmh {
			s++
			continue
		}
		e := s
		for e < len(v) && v[e] > flightSpeedKmh {
			e++
		}
		runLens = append(runLens, float64(e-s)*journeyGridSec/60)
		if e-s >= 6 {
			at := func(i int) float64 {
				if i < 0 || i >= len(v) {
					return 0
				}
				return v[i] / vc
			}
			if s > 0 { // lift-off observed
				nUp++
				for k := 0; k < 7; k++ {
					up[k] += at(s - 2 + k)
				}
			}
			if e < len(v) { // touchdown observed
				nDn++
				for k := 0; k < 7; k++ {
					dn[k] += at(e + 1 - k)
				}
			}
		}
		s = e
	}
	for k := 0; k < 7; k++ {
		if nUp > 0 {
			f = append(f, up[k]/float64(nUp))
		} else {
			f = append(f, math.NaN())
		}
	}
	for k := 0; k < 7; k++ {
		if nDn > 0 {
			f = append(f, dn[k]/float64(nDn))
		} else {
			f = append(f, math.NaN())
		}
	}
	f = append(f, quantile(runLens, 50))
	return f, vc, true
}

func stddev(xs []float64) float64 {
	if len(xs) < 2 {
		return 0
	}
	var m float64
	for _, x := range xs {
		m += x
	}
	m /= float64(len(xs))
	var s float64
	for _, x := range xs {
		s += (x - m) * (x - m)
	}
	return math.Sqrt(s / float64(len(xs)))
}

// AircraftSubtypeFromMovement scores a journey with the learned model
// (aircraft_subtype_model.go, generated by scripts/train_aircraft_subtype.py)
// and answers only outside the model's abstain band: "" means the movement
// does not say, which is a different answer from either subtype.
func AircraftSubtypeFromMovement(points []Point) (subtype string, pRotor float64, ok bool) {
	f, _, ok := AircraftFeatures(points)
	if !ok || len(aircraftSubtypeModel.Coef) != len(f) {
		return "", 0, false
	}
	m := aircraftSubtypeModel
	z := m.Intercept
	for i, x := range f {
		if math.IsNaN(x) {
			x = m.Median[i]
		}
		lo, hi := m.Clip[i][0], m.Clip[i][1]
		x = math.Max(lo, math.Min(hi, x))
		z += m.Coef[i] * (x - m.Mean[i]) / m.Scale[i]
	}
	p := 1 / (1 + math.Exp(-z))
	switch {
	case p >= m.RotorAbove:
		return "rotor_wing", p, true
	case p <= m.FixedBelow:
		return "fixed_wing", p, true
	}
	return "", p, true
}

// aircraftSubtypeModelT is a standardised logistic regression: P(rotor_wing).
type aircraftSubtypeModelT struct {
	Intercept              float64
	Coef, Mean, Scale      []float64
	Median                 []float64    // imputed for NaN
	Clip                   [][2]float64 // training range (1st..99th pct): no extrapolation
	RotorAbove, FixedBelow float64
}

// Boat: a hull on water holds one throttle setting with nothing to brake for.
// Its speed sits on a plateau — the fastest minute is barely faster than the
// average one, the slowest barely slower — for as long as the trip lasts, and
// it never comes to a dead stop on the way. Ground vehicles on park roads do
// the opposite in every respect: of 1,040 device-labelled truck journeys of an
// hour or more, none had speed CV under 0.22 or max/avg under 1.23, and their
// median slow decile was 20% of the average. A short or thinly sampled journey
// cannot show a plateau, so it gets no subtype rather than a guess.
const (
	boatMinMinutes = 30.0
	boatMinFixes   = 12
)

// BoatFromMovement reports whether journey metrics show the sustained speed
// plateau of a boat.
func BoatFromMovement(m MovementMetrics, fixes int) bool {
	avg := m.AvgSpeedKmh
	if avg < 8 || avg > 50 || m.P90SpeedKmh >= 60 {
		return false
	}
	if m.DurationMinutes < boatMinMinutes || fixes < boatMinFixes {
		return false
	}
	if m.StopFrequency > 0 || m.InteriorStopEvents > 0 {
		return false
	}
	if m.SpeedCV > 0.25 || m.MaxSpeedKmh > 1.3*avg || m.P10SpeedKmh < 0.6*avg {
		return false
	}
	// On water the surface is level. Elevation, when the device has it, can
	// only veto: GPS altitude is too noisy to prove flatness.
	if m.HasElevation && m.ElevationRangeM > 80 {
		return false
	}
	return true
}

// JourneySubtype decides the subtype of one continuous journey of the given
// movement type. An authoritative hint (device metadata, an explicit track
// name) wins; otherwise the answer comes from movement alone, or is "".
func JourneySubtype(points []Point, movementType string, hint MovementHint) (string, float64) {
	if len(points) < 3 {
		return hintedSubtype(movementType, hint)
	}
	if s, c := hintedSubtype(movementType, hint); s != "" || hint.SubtypeHint == "land" {
		return s, c
	}
	switch movementType {
	case "vehicle":
		m := AnalyzeTrajectory(points)
		score := 0.0
		if hint.SubtypeHint == "boat_name_hint" {
			score = 0.9
		}
		if BoatFromMovement(m, len(points)) {
			return "boat", 0.9
		}
		if score > 0 && m.AvgSpeedKmh >= 5 && m.AvgSpeedKmh <= 50 && m.P90SpeedKmh < 60 {
			return "boat", score
		}
	case "aircraft":
		if s, p, ok := AircraftSubtypeFromMovement(points); ok && s != "" {
			if s == "fixed_wing" {
				p = 1 - p
			}
			return s, p
		}
	}
	return "", 0
}

// hintedSubtype is the part of the answer that is not inferred.
func hintedSubtype(movementType string, hint MovementHint) (string, float64) {
	switch movementType {
	case "vehicle":
		if hint.SubtypeHint == "boat" {
			return "boat", 1.0
		}
	case "aircraft":
		switch hint.SubtypeHint {
		case "helicopter":
			return "rotor_wing", 1.0
		case "fixed_wing":
			return "fixed_wing", 1.0
		case "helicopter_name_hint":
			return "rotor_wing", 0.9
		case "fixed_wing_name_hint":
			return "fixed_wing", 0.9
		}
	}
	return "", 0
}
