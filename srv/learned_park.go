package srv

// ParkLearner is the learner's whole state for one park, in memory, with no
// database in it: features, the events that are their evidence, and the
// reference data they are compared to. The server loads it from the learned_*
// tables, applies one upload and writes the difference back
// (srv/learned_store.go); cmd/movement-eval builds it empty and replays
// history through it. The decisions are therefore the same code in both.

import (
	"math"
	"sort"
	"time"
)

// LearnedFeature is one airfield, place or road section and its events.
type LearnedFeature struct {
	ID               int64 // database id; negative while unsaved
	Family           string
	SeedLat, SeedLon float64
	Coords           [][]float64 // road section: [lon,lat]...
	LengthM          float64
	Events           []*LearnerEvent
	Status           string // pending | auto_approved | approved | rejected
	Human            bool   // a reviewer set Status; the learner must not
	Ev               Evidence
	Score            float64
	Reason           string
	Changed          bool // evidence, status or score differ from what was loaded
	loadedStatus     string
	loadedSig        string
}

// GazetteerPlace is a named place from the reference gazetteer.
type GazetteerPlace struct {
	Name     string
	Lat, Lon float64
}

const (
	nearOSMPlaceM   = 500.0
	roadConnectM    = 150.0
	placeOnRoadM    = 150.0
	roadSectionMaxM = 5000.0 // a longer new stretch is cut: evidence is per section
)

type ParkLearner struct {
	Park      string
	Airfields []*LearnedFeature
	Places    []*LearnedFeature
	Roads     []*LearnedFeature
	// Reference data. All optional: nil only removes the corresponding term.
	KnownRoads     *corridorIndex
	OSMPlaces      []GazetteerPlace
	KnownAirfields []KnownAirfield

	NewEvents []*LearnerEvent // accepted since load, for the store to insert
	Updated   []*LearnerEvent // stops whose interval grew
	Dupes     int             // events refused as re-deliveries

	// Covered: per tracker, the time intervals of vehicle track already read
	// for roads. A re-delivered track is not read twice -- the second reading
	// would find sections the first one had not yet created and count a
	// traversal the first did not, so (tracker, time, section) alone is not
	// enough to make road evidence idempotent.
	Covered    map[string][][2]int64
	NewCovered []LearnerEvent

	nextTemp int64
	stops    map[string][]*LearnerEvent           // by tracker
	instants map[string]map[int64][]*LearnerEvent // kind -> hour -> events
	secIdx   *sectionIndex
	siteGrid map[string]map[[2]int][]*LearnedFeature
}

func NewParkLearner(park string) *ParkLearner {
	return &ParkLearner{Park: park, Covered: map[string][][2]int64{}, stops: map[string][]*LearnerEvent{}, instants: map[string]map[int64][]*LearnerEvent{}, siteGrid: map[string]map[[2]int][]*LearnedFeature{"airfield": {}, "place": {}}}
}

// Attach registers a feature loaded from the store (or made by Add).
func (pl *ParkLearner) Attach(f *LearnedFeature) {
	switch f.Family {
	case "airfield":
		pl.Airfields = append(pl.Airfields, f)
		pl.gridAdd(f)
	case "place":
		pl.Places = append(pl.Places, f)
		pl.gridAdd(f)
	case "road":
		pl.Roads = append(pl.Roads, f)
		if pl.secIdx == nil && len(f.Coords) > 0 {
			pl.secIdx = newSectionIndex(f.Coords[0][1])
		}
		if pl.secIdx != nil {
			pl.secIdx.add(f)
		}
	}
	f.loadedStatus, f.loadedSig = f.Status, f.sig()
}

func (f *LearnedFeature) sig() string {
	return f.Status + "|" + f.Ev.Kind + "|" + f.Ev.Last + "|" + time.Duration(len(f.Events)).String() +
		"|" + time.Duration(f.Ev.IdleActiveDays).String() + "|" + time.Duration(math.Round(f.Score*1000)).String()
}

const siteCellDeg = 0.02 // ~2.2 km: a site radius never exceeds one cell

func siteCell(lat, lon float64) [2]int {
	return [2]int{int(math.Floor(lat / siteCellDeg)), int(math.Floor(lon / siteCellDeg))}
}

func (pl *ParkLearner) gridAdd(f *LearnedFeature) {
	c := siteCell(f.SeedLat, f.SeedLon)
	pl.siteGrid[f.Family][c] = append(pl.siteGrid[f.Family][c], f)
}

// nearestSite: the nearest feature whose SEED is within radiusM. Seeds never
// move, so which site an event joins does not depend on what arrives later.
func (pl *ParkLearner) nearestSite(family string, lat, lon, radiusM float64) *LearnedFeature {
	c := siteCell(lat, lon)
	var best *LearnedFeature
	bd := radiusM
	for dx := -1; dx <= 1; dx++ {
		for dy := -1; dy <= 1; dy++ {
			for _, f := range pl.siteGrid[family][[2]int{c[0] + dx, c[1] + dy}] {
				if d := metersBetween(f.SeedLat, f.SeedLon, lat, lon); d <= bd {
					best, bd = f, d
				}
			}
		}
	}
	return best
}

func (pl *ParkLearner) newFeature(family string, lat, lon float64) *LearnedFeature {
	pl.nextTemp--
	f := &LearnedFeature{ID: pl.nextTemp, Family: family, SeedLat: lat, SeedLon: lon, Status: "pending"}
	return f
}

// accept adds ev to f unless the PARK already holds the same event. The
// check is park-wide, not per feature: by the time a window is delivered
// again more sites exist, the event may be nearest to a different one, and a
// per-feature check would then count it a second time.
func (pl *ParkLearner) accept(f *LearnedFeature, ev LearnerEvent) bool {
	ev.FeatureID = f.ID
	bucket := ev.Start.Unix() / 3600
	var cands []*LearnerEvent
	switch ev.Kind {
	case "stop":
		cands = pl.stops[ev.Tracker]
	case "traversal":
		cands = f.Events
	default:
		for h := bucket - 1; h <= bucket+1; h++ {
			cands = append(cands, pl.instants[ev.Kind][h]...)
		}
	}
	for i := len(cands) - 1; i >= 0; i-- {
		old := cands[i]
		if !SameEvent(*old, ev) && !coincident(*old, ev) {
			continue
		}
		pl.Dupes++
		if ev.Kind == "stop" && (ev.Start.Before(old.Start) || ev.End.After(old.End)) {
			m := mergeStay(*old, ev)
			old.Start, old.End, old.DwellMin = m.Start, m.End, m.DwellMin
			if old.ID != 0 {
				pl.Updated = append(pl.Updated, old)
			}
		}
		return false
	}
	e := ev
	f.Events = append(f.Events, &e)
	pl.index(&e)
	pl.NewEvents = append(pl.NewEvents, &e)
	return true
}

// index registers an event for park-wide de-duplication.
func (pl *ParkLearner) index(e *LearnerEvent) {
	switch e.Kind {
	case "stop":
		pl.stops[e.Tracker] = append(pl.stops[e.Tracker], e)
	case "landing", "takeoff":
		if pl.instants[e.Kind] == nil {
			pl.instants[e.Kind] = map[int64][]*LearnerEvent{}
		}
		h := e.Start.Unix() / 3600
		pl.instants[e.Kind][h] = append(pl.instants[e.Kind][h], e)
	}
}

// AttachEvent adds an event loaded from the store to its feature.
func (pl *ParkLearner) AttachEvent(f *LearnedFeature, e *LearnerEvent) {
	f.Events = append(f.Events, e)
	pl.index(e)
}

const (
	coincidentWithin = 2 * time.Minute
	coincidentM      = 300.0
)

// coincident: two TRACKERS reporting the same kind of airfield event at the
// same spot within two minutes are two devices aboard one aircraft, not two
// landings. Counting both doubled journeys and made "distinct trackers" mean
// "devices carried", which is not independent evidence.
func coincident(a, b LearnerEvent) bool {
	if a.Kind != b.Kind || (a.Kind != "landing" && a.Kind != "takeoff") || a.Tracker == b.Tracker {
		return false
	}
	return absDur(a.Start.Sub(b.Start)) <= coincidentWithin && metersBetween(a.Lat, a.Lon, b.Lat, b.Lon) <= coincidentM
}

// Add applies one upload's events and vehicle tracks. Order matters only
// through seeds, so uploads must be applied oldest first for a replay to
// reproduce history (the backfill does).
func (pl *ParkLearner) Add(events []LearnerEvent, tracks []VehicleTrack) {
	for _, ev := range events {
		family, radius := "place", placeSiteM
		if ev.Kind == "landing" || ev.Kind == "takeoff" {
			family, radius = "airfield", airfieldSiteM
		} else if ev.Kind != "stop" {
			continue
		}
		f := pl.nearestSite(family, ev.Lat, ev.Lon, radius)
		if f == nil {
			f = pl.newFeature(family, ev.Lat, ev.Lon)
			pl.Attach(f)
			f.loadedSig = "" // new: must be written
		}
		pl.accept(f, ev)
	}
	for _, t := range tracks {
		pl.addTrack(t)
	}
}

// ── Roads ────────────────────────────────────────────────────────────────

// sectionIndex answers "which learned road section is this point on".
type sectionIndex struct {
	cellM  float64
	kx, ky float64
	grid   map[[2]int][]sectionSeg
}

type sectionSeg struct {
	a, b [2]float64
	f    *LearnedFeature
}

func newSectionIndex(refLat float64) *sectionIndex {
	return &sectionIndex{cellM: 250, kx: 111320 * math.Cos(refLat*math.Pi/180), ky: 110540, grid: map[[2]int][]sectionSeg{}}
}

func (s *sectionIndex) cellOf(lon, lat float64) [2]int {
	return [2]int{int(math.Floor(lon * s.kx / s.cellM)), int(math.Floor(lat * s.ky / s.cellM))}
}

func (s *sectionIndex) add(f *LearnedFeature) {
	for i := 0; i+1 < len(f.Coords); i++ {
		a, b := f.Coords[i], f.Coords[i+1]
		seg := sectionSeg{a: [2]float64{a[0], a[1]}, b: [2]float64{b[0], b[1]}, f: f}
		steps := int(havMeters(a, b)/s.cellM) + 1
		seen := map[[2]int]bool{}
		for k := 0; k <= steps; k++ {
			t := float64(k) / float64(steps)
			c := s.cellOf(a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t)
			for dx := -1; dx <= 1; dx++ {
				for dy := -1; dy <= 1; dy++ {
					key := [2]int{c[0] + dx, c[1] + dy}
					if !seen[key] {
						seen[key] = true
						s.grid[key] = append(s.grid[key], seg)
					}
				}
			}
		}
	}
}

// nearest returns the section within threshM of pt, other than skip.
func (s *sectionIndex) nearest(pt []float64, threshM float64, skip *LearnedFeature) *LearnedFeature {
	if s == nil {
		return nil
	}
	var best *LearnedFeature
	bd := threshM
	for _, seg := range s.grid[s.cellOf(pt[0], pt[1])] {
		if seg.f == skip {
			continue
		}
		if d := pointSegDistMeters(pt, seg.a[:], seg.b[:]); d <= bd {
			best, bd = seg.f, d
		}
	}
	return best
}

// resampleTimed is resampleLine that keeps time: [lon, lat, unix].
func resampleTimed(coords [][3]float64, stepM, maxGapM float64) [][][3]float64 {
	var pieces [][][3]float64
	var cur [][3]float64
	flush := func() {
		if len(cur) >= 2 {
			pieces = append(pieces, cur)
		}
		cur = nil
	}
	for i := 0; i+1 < len(coords); i++ {
		a, b := coords[i], coords[i+1]
		d := havMeters(a[:2], b[:2])
		if d > maxGapM {
			if cur != nil {
				cur = append(cur, a)
			}
			flush()
			continue
		}
		n := int(d/stepM) + 1
		for s := 0; s < n; s++ {
			t := float64(s) / float64(n)
			cur = append(cur, [3]float64{a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t, a[2] + (b[2]-a[2])*t})
		}
		if i+2 == len(coords) {
			cur = append(cur, b)
		}
	}
	flush()
	return pieces
}

// simplifyMeters drops points closer than tolM to the last kept one.
func simplifyMeters(coords [][]float64, tolM float64) [][]float64 {
	if len(coords) < 3 {
		return coords
	}
	out := [][]float64{coords[0]}
	for i := 1; i < len(coords)-1; i++ {
		if havMeters(out[len(out)-1], coords[i]) >= tolM {
			out = append(out, coords[i])
		}
	}
	return append(out, coords[len(coords)-1])
}

func lineLengthM(coords [][]float64) float64 {
	t := 0.0
	for i := 0; i+1 < len(coords); i++ {
		t += havMeters(coords[i], coords[i+1])
	}
	return t
}

// addTrack attributes every 50 m of a vehicle track to exactly one of: a
// known (HeiGIT) road, an existing learned section, or new ground. A run on
// an existing section is a TRAVERSAL of it; a run on new ground becomes a new
// section. Sections therefore never overlap -- the old matcher compared whole
// pieces at 60 % overlap and stored a new candidate for every partial match,
// which is how one track became five "roads".
func (pl *ParkLearner) addTrack(t VehicleTrack) {
	if len(t.Coords) < 2 || t.Coords[0][2] == 0 {
		return // untimed: a traversal that cannot be de-duplicated is not evidence
	}
	// Read only the stretches of time not read before.
	var fresh [][][3]float64
	var cur [][3]float64
	for _, c := range t.Coords {
		if pl.covered(t.Tracker, int64(c[2])) {
			if len(cur) >= 2 {
				fresh = append(fresh, cur)
			}
			cur = nil
			continue
		}
		cur = append(cur, c)
	}
	if len(cur) >= 2 {
		fresh = append(fresh, cur)
	}
	lo, hi := int64(t.Coords[0][2]), int64(t.Coords[len(t.Coords)-1][2])
	if len(fresh) > 0 {
		pl.NewCovered = append(pl.NewCovered, LearnerEvent{Kind: "coverage", Tracker: t.Tracker,
			Start: time.Unix(lo, 0).UTC(), End: time.Unix(hi, 0).UTC(), HeadingDeg: math.NaN()})
	}
	defer pl.Cover(t.Tracker, lo, hi)
	for _, run := range fresh {
		pl.addFreshTrack(t.Tracker, run)
	}
}

func (pl *ParkLearner) covered(tracker string, ts int64) bool {
	iv := pl.Covered[tracker]
	i := sort.Search(len(iv), func(i int) bool { return iv[i][1] >= ts })
	return i < len(iv) && iv[i][0] <= ts
}

// Cover records [lo, hi] as read, keeping the intervals sorted and merged.
func (pl *ParkLearner) Cover(tracker string, lo, hi int64) {
	iv := append(pl.Covered[tracker], [2]int64{lo, hi})
	sort.Slice(iv, func(i, j int) bool { return iv[i][0] < iv[j][0] })
	out := iv[:1]
	for _, x := range iv[1:] {
		if last := &out[len(out)-1]; x[0] <= last[1] {
			last[1] = max(last[1], x[1])
		} else {
			out = append(out, x)
		}
	}
	pl.Covered[tracker] = out
}

func (pl *ParkLearner) addFreshTrack(tracker string, coordsT [][3]float64) {
	for _, piece := range resampleTimed(coordsT, roadResampleStepM, roadMaxGapM) {
		type run struct {
			f      *LearnedFeature // nil = new ground
			known  bool
			lo, hi int
		}
		var runs []run
		for i, p := range piece {
			var f *LearnedFeature
			known := pl.KnownRoads != nil && pl.KnownRoads.near(p[:2], roadHeigitMatchM)
			if !known {
				f = pl.secIdx.nearest(p[:2], roadCorridorMatchM, nil)
			}
			if n := len(runs); n > 0 && runs[n-1].f == f && runs[n-1].known == known {
				runs[n-1].hi = i
			} else {
				runs = append(runs, run{f, known, i, i})
			}
		}
		// Distance along the piece: resampling yields AT MOST the step, so
		// lengths are measured, not counted.
		cum := make([]float64, len(piece))
		for i := 1; i < len(piece); i++ {
			cum[i] = cum[i-1] + havMeters(piece[i-1][:2], piece[i][:2])
		}
		for _, r := range runs {
			if r.known {
				continue
			}
			lenM := cum[r.hi] - cum[r.lo]
			if r.f != nil {
				if lenM >= math.Min(traversalMinM, traversalMinFrac*r.f.LengthM) {
					pl.traverse(r.f, tracker, piece[r.lo:r.hi+1])
				}
				continue
			}
			if lenM < roadMinPieceM {
				continue
			}
			// New ground, cut into sections so evidence stays local.
			for lo := r.lo; lo < r.hi; {
				hi := lo
				for hi < r.hi && cum[hi]-cum[lo] < roadSectionMaxM {
					hi++
				}
				if cum[r.hi]-cum[hi] < roadMinPieceM {
					hi = r.hi // do not leave a stub
				}
				pts := piece[lo : hi+1]
				coords := make([][]float64, len(pts))
				for i, p := range pts {
					coords[i] = []float64{math.Round(p[0]*1e6) / 1e6, math.Round(p[1]*1e6) / 1e6}
				}
				f := pl.newFeature("road", pts[0][1], pts[0][0])
				f.Coords = simplifyMeters(coords, 10)
				f.LengthM = lineLengthM(f.Coords)
				pl.Attach(f)
				f.loadedSig = ""
				pl.traverse(f, tracker, pts)
				lo = hi
			}
		}
	}
}

func (pl *ParkLearner) traverse(f *LearnedFeature, tracker string, pts [][3]float64) {
	mid := pts[len(pts)/2]
	pl.accept(f, LearnerEvent{Kind: "traversal", Tracker: tracker,
		Start: time.Unix(int64(pts[0][2]), 0).UTC(), End: time.Unix(int64(pts[len(pts)-1][2]), 0).UTC(),
		Lat: mid[1], Lon: mid[0], HeadingDeg: math.NaN(), Subtype: "vehicle"})
}

// ── Scoring ──────────────────────────────────────────────────────────────

func activeDays(fs []*LearnedFeature, asOf time.Time) []string {
	set := map[string]bool{}
	for _, f := range fs {
		for _, e := range f.Events {
			if !e.Start.After(asOf) {
				set[eventDay(e.Start)] = true
			}
		}
	}
	out := make([]string, 0, len(set))
	for d := range set {
		out = append(out, d)
	}
	sort.Strings(out)
	return out
}

// EvidenceAsOf computes a feature's evidence from its events up to asOf.
// ok is false when it has none yet.
func (pl *ParkLearner) EvidenceAsOf(f *LearnedFeature, asOf time.Time, active []string) (Evidence, bool) {
	evs := make([]LearnerEvent, 0, len(f.Events))
	for _, e := range f.Events {
		if !e.Start.After(asOf) {
			evs = append(evs, *e)
		}
	}
	if len(evs) == 0 {
		return Evidence{Family: f.Family}, false
	}
	var e Evidence
	switch f.Family {
	case "airfield":
		e = AirfieldEvidence(evs, pl.KnownAirfields)
	case "place":
		e = PlaceEvidence(evs)
		best := nearOSMPlaceM
		for _, p := range pl.OSMPlaces {
			if math.Abs(p.Lat-e.Lat) > 0.01 {
				continue
			}
			if d := metersBetween(p.Lat, p.Lon, e.Lat, e.Lon); d <= best {
				best, e.NearPlace, e.NearPlaceM = d, p.Name, fptr(math.Round(d))
			}
		}
		pt := []float64{e.Lon, e.Lat}
		// Reference roads only. A learned section is not independent
		// agreement (the vehicle that stopped drew it), and reading its
		// status would make the place score depend on the road model.
		if pl.KnownRoads != nil {
			on := pl.KnownRoads.near(pt, placeOnRoadM)
			e.OnKnownRoad = &on
		}
	case "road":
		e = RoadEvidence(evs, f.LengthM)
		if len(f.Coords) >= 2 {
			n := 0
			for _, end := range [][]float64{f.Coords[0], f.Coords[len(f.Coords)-1]} {
				if pl.KnownRoads != nil && pl.KnownRoads.near(end, roadConnectM) || pl.secIdx.nearest(end, roadConnectM, f) != nil {
					n++
				}
			}
			e.ConnectedEnds = &n
			mid := f.Coords[len(f.Coords)/2]
			e.Lon, e.Lat = mid[0], mid[1]
		}
	}
	e.SetIdle(active, asOf)
	return e, true
}

// Rescore recomputes evidence, score and status for every feature as of
// asOf. Every feature, not just those the upload touched: a feature nobody
// came back to changes too -- its idle count grows, and that is what demotes it.
func (pl *ParkLearner) Rescore(asOf time.Time) {
	for _, fs := range [][]*LearnedFeature{pl.Roads, pl.Airfields, pl.Places} {
		active := activeDays(fs, asOf)
		for _, f := range fs {
			e, ok := pl.EvidenceAsOf(f, asOf, active)
			if !ok {
				continue
			}
			f.Ev = e
			f.Status, f.Score, f.Reason = DecideStatus(f.Status, f.Human, e)
			f.Changed = f.sig() != f.loadedSig
		}
	}
}

// Counts returns features per family and status.
func (pl *ParkLearner) Counts() map[string]map[string]int {
	out := map[string]map[string]int{}
	for fam, fs := range map[string][]*LearnedFeature{"airfield": pl.Airfields, "place": pl.Places, "road": pl.Roads} {
		out[fam] = map[string]int{}
		for _, f := range fs {
			out[fam][f.Status]++
		}
	}
	return out
}
