package srv

// Evidence for a learned feature, and the decision made from it.
//
// A feature's confidence used to be min(uses*20, 90) and it was approved at a
// fixed count: five landings in one afternoon by one aircraft "proved" an
// airstrip. Here the evidence is what a reviewer would ask for -- on how many
// different days, by how many different journeys and trackers, was it seen
// both arriving and leaving, do the runs agree on one axis, when was it last
// used and how much has happened elsewhere since -- and the score is a
// probability fitted and thresholded in cmd/movement-eval on a held-out time
// split (srv/learned_score_model.go, generated). Every term is computed from
// LearnerEvents by pure functions, so the harness measures exactly what the
// server decides.

import (
	"encoding/json"
	"math"
	"os"
	"sort"
	"sync"
	"time"
)

// Evidence is everything known about one learned feature. Pointer fields are
// measurements that may be ABSENT (never a default standing in for one).
type Evidence struct {
	Family string `json:"family"` // airfield | place | road
	// Kind: airstrip (a fixed-wing journey used it) | landing_zone (rotor
	// only) | airfield (airframe not measured) | base | outpost | camp | stop | road
	Kind     string `json:"kind"`
	Events   int    `json:"events"`
	Days     int    `json:"days"`
	Trackers int    `json:"trackers"`
	First    string `json:"first_seen,omitempty"`
	Last     string `json:"last_seen,omitempty"`
	// IdleActiveDays: days since Last on which the park recorded events of
	// this family ELSEWHERE. Silence while nothing moved says nothing;
	// silence while everything else was busy is the contradiction.
	IdleActiveDays int `json:"idle_active_days"`

	// airfield
	Journeys      int          `json:"journeys,omitempty"`
	Landings      int          `json:"landings,omitempty"`
	Takeoffs      int          `json:"takeoffs,omitempty"`
	FixedWing     int          `json:"fixed_wing_journeys,omitempty"`
	RotorWing     int          `json:"rotor_wing_journeys,omitempty"`
	AxisDeg       *float64     `json:"axis_deg,omitempty"`       // runway axis, 0-180
	AxisAgreement *float64     `json:"axis_agreement,omitempty"` // 0-1 over AxisN headings
	AxisN         int          `json:"axis_n,omitempty"`
	LengthM       *float64     `json:"length_m,omitempty"` // observed ground-run extent: a lower bound
	Line          [][2]float64 `json:"line,omitempty"`     // [lon,lat] x2
	RollFixes     int          `json:"roll_fixes,omitempty"`
	Known         string       `json:"known_airfield,omitempty"`
	KnownM        *float64     `json:"known_airfield_m,omitempty"`

	// place
	DwellMedianMin *float64 `json:"dwell_median_min,omitempty"`
	DwellP25Min    *float64 `json:"dwell_p25_min,omitempty"`
	DwellP75Min    *float64 `json:"dwell_p75_min,omitempty"`
	NearPlace      string   `json:"near_osm_place,omitempty"`
	NearPlaceM     *float64 `json:"near_osm_place_m,omitempty"`
	OnKnownRoad    *bool    `json:"on_known_road,omitempty"`

	// road
	RoadLengthM   float64 `json:"road_length_m,omitempty"`
	ConnectedEnds *int    `json:"connected_ends,omitempty"` // ends meeting the known or learned network

	Lat float64 `json:"lat,omitempty"`
	Lon float64 `json:"lon,omitempty"`
}

func fptr(v float64) *float64 { return &v }

func baseEvidence(family string, events []LearnerEvent) Evidence {
	e := Evidence{Family: family, Events: len(events)}
	days, trk := map[string]bool{}, map[string]bool{}
	var first, last time.Time
	for _, ev := range events {
		days[eventDay(ev.Start)] = true
		trk[ev.Tracker] = true
		if first.IsZero() || ev.Start.Before(first) {
			first = ev.Start
		}
		if ev.Start.After(last) {
			last = ev.Start
		}
	}
	e.Days, e.Trackers = len(days), len(trk)
	if !first.IsZero() {
		e.First, e.Last = eventDay(first), eventDay(last)
	}
	return e
}

// SetIdle fills IdleActiveDays from the family's sorted active days in the park.
func (e *Evidence) SetIdle(activeDays []string, asOf time.Time) {
	lo := sort.SearchStrings(activeDays, e.Last+"\x00")
	hi := sort.SearchStrings(activeDays, eventDay(asOf)+"\x00")
	e.IdleActiveDays = max(hi-lo, 0)
}

// KnownAirfield is one row of data/eval/movement/airfields.json (OurAirports).
type KnownAirfield struct {
	Name string  `json:"name"`
	Lat  float64 `json:"lat"`
	Lon  float64 `json:"lon"`
}

const knownAirfieldM = 2000.0

var (
	knownAirfieldsOnce sync.Once
	knownAirfields     []KnownAirfield
)

// LoadKnownAirfields reads the reference list; absent or unreadable is an
// empty list, which only means no feature earns the bonus.
func LoadKnownAirfields(path string) []KnownAirfield {
	var out []KnownAirfield
	if b, err := os.ReadFile(path); err == nil {
		_ = json.Unmarshal(b, &out)
	}
	return out
}

func defaultKnownAirfields() []KnownAirfield {
	knownAirfieldsOnce.Do(func() { knownAirfields = LoadKnownAirfields("data/eval/movement/airfields.json") })
	return knownAirfields
}

// AirfieldEvidence aggregates the landings and take-offs of one site.
func AirfieldEvidence(events []LearnerEvent, known []KnownAirfield) Evidence {
	e := baseEvidence("airfield", events)
	journeys := map[string]string{}
	var lats, lons []float64
	var c2, s2 float64
	rollEvents := 0
	var roll [][2]float64
	for _, ev := range events {
		if ev.Kind == "landing" {
			e.Landings++
		} else {
			e.Takeoffs++
		}
		if cur, ok := journeys[ev.Journey]; !ok || cur == "" {
			journeys[ev.Journey] = ev.Subtype
		}
		lats, lons = append(lats, ev.Lat), append(lons, ev.Lon)
		if !math.IsNaN(ev.HeadingDeg) {
			a := 2 * ev.HeadingDeg * math.Pi / 180 // doubled: 090 and 270 are one runway
			c2, s2 = c2+math.Cos(a), s2+math.Sin(a)
			e.AxisN++
		}
		if len(ev.Roll) > 0 {
			rollEvents++
			for _, r := range ev.Roll {
				roll = append(roll, [2]float64{r[0], r[1]})
			}
		}
	}
	e.Journeys = len(journeys)
	for _, sub := range journeys {
		switch sub {
		case "fixed_wing":
			e.FixedWing++
		case "rotor_wing":
			e.RotorWing++
		}
	}
	switch {
	case e.FixedWing > 0:
		e.Kind = "airstrip"
	case e.RotorWing > 0:
		e.Kind = "landing_zone"
	default:
		e.Kind = "airfield"
	}
	if len(lats) > 0 {
		e.Lat, e.Lon = median(append([]float64(nil), lats...)), median(append([]float64(nil), lons...))
	}
	if e.AxisN >= 2 {
		e.AxisAgreement = fptr(math.Round(math.Hypot(c2, s2)/float64(e.AxisN)*1000) / 1000)
		e.AxisDeg = fptr(math.Round(math.Mod(math.Atan2(s2, c2)*90/math.Pi+180, 180)*10) / 10)
	}
	e.RollFixes = len(roll)
	if e.Kind == "airstrip" {
		measureRunway(&e, roll, rollEvents)
	}
	best := knownAirfieldM
	for _, k := range known {
		if math.Abs(k.Lat-e.Lat) > 0.05 {
			continue
		}
		if d := metersBetween(k.Lat, k.Lon, e.Lat, e.Lon); d <= best {
			best, e.Known, e.KnownM = d, k.Name, fptr(math.Round(d))
		}
	}
	return e
}

const (
	runwayMinFixes   = 6
	runwayMinEvents  = 3
	runwayMaxLateral = 60.0  // m off the fitted axis: beyond is a taxiway
	runwayMinLengthM = 200.0 // shorter is not a measurement of a runway
	runwayMaxAxisOff = 25.0  // deg between the fitted line and the runs' headings
)

// measureRunway fits a line through the ground-run fixes of many events. It
// sets Line/LengthM/AxisDeg only when the fixes actually describe one: enough
// of them, from enough separate events, narrow, long, and agreeing with the
// direction the aircraft were seen to run. Otherwise the fields stay absent.
// The length is the extent of the runs that were OBSERVED, so a lower bound.
func measureRunway(e *Evidence, roll [][2]float64, rollEvents int) {
	if len(roll) < runwayMinFixes || rollEvents < runwayMinEvents {
		return
	}
	var mlat, mlon float64
	for _, r := range roll {
		mlon, mlat = mlon+r[0], mlat+r[1]
	}
	mlon, mlat = mlon/float64(len(roll)), mlat/float64(len(roll))
	kx, ky := 111320*math.Cos(mlat*math.Pi/180), 110540.0
	fit := func(pts [][2]float64) (ux, uy, ratio float64) {
		var sxx, syy, sxy, cx, cy float64
		for _, p := range pts {
			cx, cy = cx+p[0], cy+p[1]
		}
		cx, cy = cx/float64(len(pts)), cy/float64(len(pts))
		for _, p := range pts {
			sxx += (p[0] - cx) * (p[0] - cx)
			syy += (p[1] - cy) * (p[1] - cy)
			sxy += (p[0] - cx) * (p[1] - cy)
		}
		th := 0.5 * math.Atan2(2*sxy, sxx-syy)
		ux, uy = math.Cos(th), math.Sin(th)
		l1 := sxx*ux*ux + 2*sxy*ux*uy + syy*uy*uy
		l2 := sxx + syy - l1
		if l1 <= 0 {
			return ux, uy, 1
		}
		return ux, uy, l2 / l1
	}
	xy := make([][2]float64, len(roll))
	for i, r := range roll {
		xy[i] = [2]float64{(r[0] - mlon) * kx, (r[1] - mlat) * ky}
	}
	ux, uy, _ := fit(xy)
	// Drop what lies off the axis (taxiway, apron) and fit once more.
	med := func(pts [][2]float64, ux, uy float64) (float64, float64) {
		var a, c []float64
		for _, p := range pts {
			a, c = append(a, p[0]*ux+p[1]*uy), append(c, -p[0]*uy+p[1]*ux)
		}
		return median(a), median(c)
	}
	_, c0 := med(xy, ux, uy)
	var kept [][2]float64
	for _, p := range xy {
		if math.Abs(-p[0]*uy+p[1]*ux-c0) <= runwayMaxLateral {
			kept = append(kept, p)
		}
	}
	if len(kept) < runwayMinFixes {
		return
	}
	ux, uy, ratio := fit(kept)
	if ratio > 0.05 {
		return
	}
	_, c0 = med(kept, ux, uy)
	lo, hi := math.Inf(1), math.Inf(-1)
	for _, p := range kept {
		a := p[0]*ux + p[1]*uy
		lo, hi = math.Min(lo, a), math.Max(hi, a)
	}
	if hi-lo < runwayMinLengthM {
		return
	}
	axis := math.Mod(math.Atan2(ux, uy)*180/math.Pi+360, 180)
	if e.AxisDeg != nil {
		d := math.Abs(axis - *e.AxisDeg)
		if d > 90 {
			d = 180 - d
		}
		if d > runwayMaxAxisOff {
			return // the fixes draw a line the aircraft did not fly along
		}
	}
	pt := func(a float64) [2]float64 {
		x, y := a*ux-c0*uy, a*uy+c0*ux
		return [2]float64{math.Round((mlon+x/kx)*1e6) / 1e6, math.Round((mlat+y/ky)*1e6) / 1e6}
	}
	e.Line = [][2]float64{pt(lo), pt(hi)}
	e.LengthM = fptr(math.Round(hi - lo))
	e.AxisDeg = fptr(math.Round(axis*10) / 10)
	e.RollFixes = len(kept)
	e.Lon, e.Lat = (e.Line[0][0]+e.Line[1][0])/2, (e.Line[0][1]+e.Line[1][1])/2
}

// PlaceEvidence aggregates the stops at one site. Its type is read from the
// dwell distribution and from how many different trackers keep coming back,
// not from one average.
func PlaceEvidence(events []LearnerEvent) Evidence {
	e := baseEvidence("place", events)
	var dw, lats, lons []float64
	for _, ev := range events {
		dw = append(dw, ev.DwellMin)
		lats, lons = append(lats, ev.Lat), append(lons, ev.Lon)
	}
	if len(dw) == 0 {
		e.Kind = "stop"
		return e
	}
	sort.Float64s(dw)
	q := func(p float64) *float64 { return fptr(math.Round(dw[int(p*float64(len(dw)-1)+0.5)])) }
	e.DwellMedianMin, e.DwellP25Min, e.DwellP75Min = q(0.5), q(0.25), q(0.75)
	e.Lat, e.Lon = median(lats), median(lons)
	m := *e.DwellMedianMin
	switch {
	case m >= 360 && e.Trackers >= 3 && e.Days >= 5:
		e.Kind = "base"
	case m >= 240 && e.Days >= 3:
		e.Kind = "outpost"
	case m >= 60:
		e.Kind = "camp"
	default:
		e.Kind = "stop"
	}
	return e
}

// RoadEvidence aggregates the traversals of one road section.
func RoadEvidence(events []LearnerEvent, lengthM float64) Evidence {
	e := baseEvidence("road", events)
	e.Kind, e.RoadLengthM = "road", math.Round(lengthM)
	return e
}

// ── Score ────────────────────────────────────────────────────────────────

// LearnerFeatureNames is the feature order per family; the generated model
// must match it (TestLearnedScoreModelMatchesFeatures).
var LearnerFeatureNames = map[string][]string{
	"airfield": {"ln_days", "ln_journeys", "ln_trackers", "both_directions", "axis_agreement", "known_airfield", "ln_idle_active_days"},
	"place":    {"ln_days", "ln_trackers", "ln_events", "ln_dwell_median", "near_osm_place", "on_known_road", "ln_idle_active_days"},
	"road":     {"ln_days", "ln_trackers", "ln_events", "ln_length_m", "connected_ends", "ln_idle_active_days"},
}

func b2f(b bool) float64 {
	if b {
		return 1
	}
	return 0
}

// LearnerFeatures is the model's view of the evidence.
func LearnerFeatures(e Evidence) []float64 {
	l := func(n int) float64 { return math.Log1p(float64(n)) }
	switch e.Family {
	case "airfield":
		axis := 0.0
		if e.AxisAgreement != nil {
			axis = *e.AxisAgreement
		}
		return []float64{l(e.Days), l(e.Journeys), l(e.Trackers), b2f(e.Landings > 0 && e.Takeoffs > 0), axis, b2f(e.Known != ""), l(e.IdleActiveDays)}
	case "place":
		dw := 0.0
		if e.DwellMedianMin != nil {
			dw = math.Log1p(*e.DwellMedianMin)
		}
		return []float64{l(e.Days), l(e.Trackers), l(e.Events), dw, b2f(e.NearPlace != ""), b2f(e.OnKnownRoad != nil && *e.OnKnownRoad), l(e.IdleActiveDays)}
	case "road":
		c := 0.0
		if e.ConnectedEnds != nil {
			c = float64(*e.ConnectedEnds) / 2
		}
		return []float64{l(e.Days), l(e.Trackers), l(e.Events), math.Log1p(e.RoadLengthM), c, l(e.IdleActiveDays)}
	}
	return nil
}

// learnedScoreModelT is one family's fitted logistic model and thresholds.
type learnedScoreModelT struct {
	Features  []string
	Intercept float64
	Coef      []float64
	// Approve: lowest score auto-approved; chosen for >= 95 % precision.
	// Reject: highest score auto-rejected (given the idle rule below).
	Approve, Reject float64
	// PlattA, PlattB recalibrate the raw logit on the held-out days:
	// p = sigmoid(PlattA*z + PlattB). Monotone, so it cannot reorder features.
	PlattA, PlattB float64
}

const (
	// learnedRejectMinIdle: nothing is rejected before the park had this many
	// active days in which the feature could have been used again.
	learnedRejectMinIdle = 14
	// LearnerHorizonDays: the score is P(used again within this many days).
	LearnerHorizonDays = 28
)

// learnedDemoteMinIdle: an auto-approved feature whose score has fallen below
// the approve threshold keeps its status until the park has been active on
// this many days without it. A score dipping for a quiet week is not a
// contradiction; a busy fortnight that never came back is. Chosen per family
// in the replay as the longest grace that keeps the approved STATE at >= 95 %
// precision: a camp is abandoned faster than an airstrip.
var learnedDemoteMinIdle = map[string]int{"airfield": 7, "road": 7, "place": 2}

// ScoreEvidence is the calibrated probability that the feature is used again
// within LearnerHorizonDays. ok is false when no model is fitted for the family.
func ScoreEvidence(e Evidence) (p float64, ok bool) {
	m, has := learnedScoreModels[e.Family]
	f := LearnerFeatures(e)
	if !has || len(m.Coef) != len(f) {
		return 0, false
	}
	z := m.Intercept
	for i, x := range f {
		z += m.Coef[i] * x
	}
	return 1 / (1 + math.Exp(-(m.PlattA*z + m.PlattB))), true
}

// DecideStatus maps evidence to one of three distinct states. A human's
// decision (approved / rejected with a name on it) is never overridden.
//
//	auto_approved  score >= Approve (and, for an airfield, the airframe measured)
//	rejected       score <= Reject AND idle through learnedRejectMinIdle active days
//	pending        everything else: insufficient evidence either way
//
// Demotion: an auto-approved feature whose score fell below Approve stays
// approved until learnedDemoteMinIdle active days have passed without it,
// then returns to pending.
func DecideStatus(prev string, human bool, e Evidence) (status string, p float64, reason string) {
	p, ok := ScoreEvidence(e)
	if human {
		return prev, p, "reviewer decision"
	}
	if !ok {
		return "pending", 0, "no fitted model"
	}
	m := learnedScoreModels[e.Family]
	gate := ""
	if e.Family == "airfield" && e.Kind == "airfield" {
		gate = "airframe not measured (plane or helicopter unknown)"
	}
	switch {
	case p >= m.Approve && gate == "":
		return "auto_approved", p, "evidence"
	case prev == "auto_approved" && e.IdleActiveDays < learnedDemoteMinIdle[e.Family] && gate == "":
		return "auto_approved", p, "approved earlier; still likely in use"
	case prev == "auto_approved":
		return "pending", p, "demoted: not seen again while the park was active elsewhere"
	case p <= m.Reject && e.IdleActiveDays >= learnedRejectMinIdle:
		return "rejected", p, "not seen again while the park was active elsewhere"
	case gate != "":
		return "pending", p, gate
	}
	return "pending", p, "insufficient evidence"
}
