// movement-eval is the evaluation harness for the movement-type classifier.
//
// It replays EarthRanger autofetch GPX files (which carry the device's
// subject_type / subject_subtype / patrol_type as <er:*> extensions) through
// the FULL production pipeline with every hint stripped, and scores the
// "plain movement" answer against the ER metadata as ground truth.
//
//	go run ./cmd/movement-eval                       # score, print, compare to baseline
//	go run ./cmd/movement-eval -write-baseline       # freeze current numbers
//	go run ./cmd/movement-eval -misses 20            # list the worst misclassified segments
//	go run ./cmd/movement-eval -snapshot db.sqlite3  # copy raw autofetch GPX out of upload_queue
//
// The corpus lives under data/eval/movement (gitignored: ranger tracks); see
// loadCorpus for its three parts. upload_queue rows are deleted 7 days after
// completion, so -snapshot must run regularly for raw/ to grow; it is additive
// and keyed by queue id.
//
//	go run ./cmd/movement-eval -resample 120         # same movement, slower tracker
//	go run ./cmd/movement-eval -dump /tmp/segs.csv   # per-segment metrics for analysis
//
// Never tune the classifier by eye: change, run this, read the delta.
package main

import (
	"bufio"
	"compress/gzip"
	"database/sql"
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	_ "modernc.org/sqlite"
	"srv.exe.dev/srv"
	"srv.exe.dev/srv/gpx"
)

// truthFor maps ER metadata to (label, strong, subtype).
// strong: the tracker is bolted to the machine. weak: a phone/InReach on a
// person, whose mode we only infer from the patrol they were leading.
func truthFor(t gpx.Track) (label string, strong bool, sub string) {
	st, ss, pt := strings.ToLower(t.ERSubjectType), strings.ToLower(t.ERSubjectSubtype), strings.ToLower(t.ERPatrolType)
	switch st {
	case "vehicle":
		return "vehicle", true, ""
	case "aircraft":
		switch ss {
		case "helicopter", "heli":
			sub = "rotor_wing"
		case "plane", "fixed_wing":
			sub = "fixed_wing"
		}
		return "aircraft", true, sub
	case "person":
		switch {
		case strings.Contains(pt, "heli"):
			return "aircraft", false, "rotor_wing"
		case strings.Contains(pt, "plane"):
			return "aircraft", false, "fixed_wing"
		case ss == "ranger":
			return "foot", false, ""
		}
	}
	return "", false, ""
}

type cell struct {
	Segs int     `json:"segs"`
	Km   float64 `json:"km"`
	Min  float64 `json:"min"`
}

type matrix map[string]map[string]*cell // truth -> predicted -> cell

func (m matrix) add(truth, pred string, km, min float64) {
	if m[truth] == nil {
		m[truth] = map[string]*cell{}
	}
	c := m[truth][pred]
	if c == nil {
		c = &cell{}
		m[truth][pred] = c
	}
	c.Segs++
	c.Km += km
	c.Min += min
}

type score struct {
	Matrix      matrix             `json:"matrix"`
	RecallKm    map[string]float64 `json:"recall_km"`
	RecallSegs  map[string]float64 `json:"recall_segs"`
	PrecisionKm map[string]float64 `json:"precision_km"`
	MacroKm     float64            `json:"macro_recall_km"`
	MacroSegs   float64            `json:"macro_recall_segs"`
}

func (m matrix) score() score {
	s := score{Matrix: m, RecallKm: map[string]float64{}, RecallSegs: map[string]float64{}, PrecisionKm: map[string]float64{}}
	predKm, hitKm := map[string]float64{}, map[string]float64{}
	for truth, row := range m {
		var km, hit, n, nh float64
		for pred, c := range row {
			km += c.Km
			n += float64(c.Segs)
			predKm[pred] += c.Km
			if pred == truth {
				hit += c.Km
				nh += float64(c.Segs)
				hitKm[pred] += c.Km
			}
		}
		if km > 0 {
			s.RecallKm[truth] = hit / km
			s.MacroKm += hit / km
		}
		if n > 0 {
			s.RecallSegs[truth] = nh / n
			s.MacroSegs += nh / n
		}
	}
	for p, km := range predKm {
		if _, isTruth := m[p]; isTruth && km > 0 {
			s.PrecisionKm[p] = hitKm[p] / km
		}
	}
	if n := float64(len(m)); n > 0 {
		s.MacroKm /= n
		s.MacroSegs /= n
	}
	return s
}

type miss struct {
	File, ERType, Truth, Pred, Reason string
	Km, Min, Avg                      float64
	Pts                               int
	M                                 gpx.MovementMetrics
}

type report struct {
	Corpus struct {
		Files  int            `json:"files"`
		Tracks int            `json:"tracks"`
		Points int            `json:"points"`
		ByER   map[string]int `json:"points_by_er_type"`
	} `json:"corpus"`
	Plain       score                   `json:"plain_strong"`          // hints stripped, device-mounted truth
	PlainWeak   score                   `json:"plain_weak"`            // hints stripped, person-carried truth (inferred)
	Subtype     score                   `json:"plain_subtype"`         // aircraft truth: fixed_wing vs rotor_wing
	VehSubtype  score                   `json:"plain_vehicle_subtype"` // vehicle truth: land vs boat
	BySubject   matrix                  `json:"plain_by_er_subtype"`   // ER subtype -> predicted
	Hinted      score                   `json:"hinted_strong"`         // reference: what production stores today
	BySource    map[string]score        `json:"plain_by_source"`
	Learner     map[string]learnerScore `json:"learner"`
	Replay      *replayReport           `json:"replay,omitempty"`
	misses      []miss
	subtypeMiss []miss
}

func stripHints(t gpx.Track) gpx.Track {
	t.Name, t.Activity, t.ERSubjectType, t.ERSubjectSubtype, t.ERPatrolType = "", "", "", "", ""
	return t
}

// predicted reduces a classified segment to the answer a reader of the map
// gets: a movement type, or the reason it was dropped from effort.
func predicted(cs srv.ClassifiedSegment) string {
	switch cs.Classification {
	case "patrol", "aircraft":
		if cs.MovementType == "" {
			return "none"
		}
		return cs.MovementType
	default:
		return "x:" + cs.Classification
	}
}

func run(t gpx.Track) []srv.ClassifiedSegment {
	d := &gpx.GPXData{Tracks: []gpx.Track{t}}
	segs := gpx.RemoveStraightLineGaps(gpx.SplitIntoSegments(d, 0))
	return srv.ValidateAndClassifyGPX(segs).ClassifiedSegments
}

// dumpAircraftFeatures writes one row per aircraft journey (the unit the
// subtype model sees) with its device-truth subtype: the training table for
// scripts/train_aircraft_subtype.py. Features come from the same Go code
// production runs, so training and serving cannot drift apart.
func dumpAircraftFeatures(w *csv.Writer, it item, tsub string, strong bool, segs []srv.ClassifiedSegment) {
	for ci, chain := range srv.JourneyChains(segs) {
		km, air := 0.0, false
		for _, i := range chain {
			if segs[i].MovementType == "aircraft" {
				air = true
				km += segs[i].DistanceKm
			}
		}
		if !air {
			continue
		}
		f, vc, ok := gpx.AircraftFeatures(srv.ChainPoints(segs, chain))
		if !ok {
			continue
		}
		row := []string{it.Source, it.File, fmt.Sprint(ci), tsub, fmt.Sprint(strong), strconv.FormatFloat(km, 'f', 2, 64), strconv.FormatFloat(vc, 'f', 1, 64)}
		for _, x := range f {
			if math.IsNaN(x) {
				row = append(row, "")
			} else {
				row = append(row, strconv.FormatFloat(x, 'g', 6, 64))
			}
		}
		w.Write(row)
	}
}

// item is one labelled track of the corpus.
type item struct {
	Source string // raw | history | labelled
	File   string
	Track  gpx.Track
}

type histRow struct {
	Log  int64        `json:"log"`
	Type string       `json:"type"`
	Sub  string       `json:"sub,omitempty"`
	Pts  [][4]float64 `json:"pts"`
}

// loadCorpus reads the three corpora under root:
//
//	raw/*.gpx        autofetch files as fetched; truth = <er:*> device metadata
//	history.jsonl    segments recovered from gpx_upload_logs (points sampled to
//	                 <=300/segment); truth = the type production stored WITH the
//	                 ER hint. Device-mounted trackers are forced to their ER type
//	                 there, so vehicle/aircraft are near-truth; "foot" is the
//	                 classifier's own answer and is only ever scored as weak.
//	labelled/*.gpx   hand-labelled files; truth = filename prefix
func loadCorpus(root string) []item {
	var items []item
	parse := func(f string) *gpx.GPXData {
		fh, err := os.Open(f)
		if err != nil {
			fatal(err)
		}
		defer fh.Close()
		d, err := gpx.ParseGPX(fh)
		if err != nil {
			fmt.Fprintf(os.Stderr, "%s: %v\n", f, err)
			return nil
		}
		return d
	}
	files, _ := filepath.Glob(filepath.Join(root, "raw", "*.gpx"))
	sort.Strings(files)
	for _, f := range files {
		if d := parse(f); d != nil {
			for _, t := range d.Tracks {
				items = append(items, item{"raw", filepath.Base(f), t})
			}
		}
	}
	files, _ = filepath.Glob(filepath.Join(root, "labelled", "*.gpx"))
	sort.Strings(files)
	for _, f := range files {
		d := parse(f)
		if d == nil {
			continue
		}
		st, ss := "", ""
		switch b := strings.ToLower(filepath.Base(f)); {
		case strings.HasPrefix(b, "car_"), strings.HasPrefix(b, "vehicle_"):
			st, ss = "vehicle", "car"
		case strings.HasPrefix(b, "boat_"):
			st, ss = "vehicle", "boat"
		case strings.HasPrefix(b, "helicopter_"), strings.HasPrefix(b, "heli_"):
			st, ss = "aircraft", "helicopter"
		case strings.HasPrefix(b, "plane_"), strings.HasPrefix(b, "in_to_"), strings.HasPrefix(b, "5h-"):
			st, ss = "aircraft", "plane"
		case strings.HasPrefix(b, "foot_"), strings.HasPrefix(b, "walk_"):
			st, ss = "person", "ranger"
		default:
			continue // unlabelled
		}
		for _, t := range d.Tracks {
			t.ERSubjectType, t.ERSubjectSubtype, t.ERPatrolType = st, ss, ""
			items = append(items, item{"labelled", filepath.Base(f), t})
		}
	}
	if fh, err := os.Open(filepath.Join(root, "history.jsonl.gz")); err == nil {
		defer fh.Close()
		zr, err := gzip.NewReader(fh)
		if err != nil {
			fatal(err)
		}
		sc := bufio.NewScanner(zr)
		sc.Buffer(make([]byte, 1<<20), 64<<20)
		for sc.Scan() {
			var h histRow
			if json.Unmarshal(sc.Bytes(), &h) != nil || len(h.Pts) < 2 {
				continue
			}
			t := gpx.Track{}
			switch h.Type {
			case "vehicle":
				t.ERSubjectType = "vehicle"
			case "aircraft":
				t.ERSubjectType = "aircraft"
				switch h.Sub {
				case "rotor_wing":
					t.ERSubjectSubtype = "helicopter"
				case "fixed_wing":
					t.ERSubjectSubtype = "plane"
				}
			case "foot":
				t.ERSubjectType, t.ERSubjectSubtype = "person", "ranger"
			default:
				continue
			}
			pts := make([]gpx.Point, 0, len(h.Pts))
			for _, v := range h.Pts {
				p := gpx.Point{Lon: v[0], Lat: v[1]}
				if v[2] != 0 {
					tm := time.Unix(int64(v[2]), 0).UTC()
					p.Time = &tm
				}
				if v[3] != -100000 {
					e := v[3]
					p.Elevation = &e
				}
				pts = append(pts, p)
			}
			// A fused segment kept the subtype of its FIRST member only, so
			// that is the only piece whose subtype label can be trusted.
			pieces := splitContinuous(pts)
			t.Segments = pieces[:1]
			items = append(items, item{"history", fmt.Sprintf("log%d", h.Log), t})
			if len(pieces) > 1 {
				t.ERSubjectSubtype = ""
				if h.Type == "foot" {
					t.ERSubjectSubtype = "ranger"
				}
				t.Segments = pieces[1:]
				items = append(items, item{"history", fmt.Sprintf("log%d", h.Log), t})
			}
		}
	}
	return items
}

// splitContinuous cuts recovered history points wherever they stop being one
// subject's continuous movement. Stored segments were merged by type with no
// continuity check (fixed 2026-11), so one stored "segment" can chain several
// trackers end to end: time runs backwards or jumps at each seam.
func splitContinuous(pts []gpx.Point) [][]gpx.Point {
	var dts []float64
	for i := 1; i < len(pts); i++ {
		if pts[i].Time != nil && pts[i-1].Time != nil {
			if dt := pts[i].Time.Sub(*pts[i-1].Time).Seconds(); dt > 0 {
				dts = append(dts, dt)
			}
		}
	}
	maxGap := 900.0
	if len(dts) > 0 {
		sort.Float64s(dts)
		if g := 6 * dts[len(dts)/2]; g > maxGap {
			maxGap = g
		}
	}
	var out [][]gpx.Point
	cur := []gpx.Point{pts[0]}
	for i := 1; i < len(pts); i++ {
		a, b := pts[i-1], pts[i]
		brk := a.Time == nil || b.Time == nil
		if !brk {
			dt := b.Time.Sub(*a.Time).Seconds()
			km := gpx.CalculateDistance([]gpx.Point{a, b})
			brk = dt <= 0 || dt > maxGap || km/(dt/3600) > 400
		}
		if brk {
			out = append(out, cur)
			cur = nil
		}
		cur = append(cur, b)
	}
	return append(out, cur)
}

// snapshotHistory recovers labelled segments from gpx_upload_logs for
// autofetch uploads older than the oldest raw file (raw supersedes them).
func snapshotHistory(db *sql.DB, root string) error {
	var cutoff sql.NullString
	_ = db.QueryRow(`SELECT MIN(created_at) FROM upload_queue WHERE filename LIKE 'autofetch-%'`).Scan(&cutoff)
	if !cutoff.Valid {
		cutoff.String = "9999"
	}
	out := filepath.Join(root, "history.jsonl.gz")
	if _, err := os.Stat(out); err == nil {
		fmt.Println("snapshot: history.jsonl.gz exists, left alone (delete it to rebuild)")
		return nil
	}
	rows, err := db.Query(`SELECT id, classified_segments_json FROM gpx_upload_logs
		WHERE filename LIKE 'autofetch-%' AND classified_segments_json IS NOT NULL AND upload_time < ? ORDER BY id`, cutoff.String)
	if err != nil {
		return err
	}
	defer rows.Close()
	fh, err := os.OpenFile(out, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	defer fh.Close()
	zw := gzip.NewWriter(fh)
	defer zw.Close()
	enc := json.NewEncoder(zw)
	logs, n := 0, 0
	for rows.Next() {
		var id int64
		var js string
		if err := rows.Scan(&id, &js); err != nil {
			return err
		}
		var segs []srv.ClassifiedSegment
		if json.Unmarshal([]byte(js), &segs) != nil {
			continue
		}
		logs++
		for _, cs := range segs {
			if cs.MovementType == "" || len(cs.SampledPoints) < 2 {
				continue
			}
			if err := enc.Encode(histRow{id, cs.MovementType, cs.MovementSubtype, cs.SampledPoints}); err != nil {
				return err
			}
			n++
		}
	}
	fmt.Printf("snapshot: %d history logs (before %s), %d labelled segments -> %s\n", logs, cutoff.String, n, out)
	return rows.Err()
}

func snapshot(dbPath, root string) error {
	dir := filepath.Join(root, "raw")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	db, err := sql.Open("sqlite", "file:"+dbPath+"?mode=ro")
	if err != nil {
		return err
	}
	defer db.Close()
	rows, err := db.Query(`SELECT id, file_content FROM upload_queue WHERE filename LIKE 'autofetch-%' AND status='completed'`)
	if err != nil {
		return err
	}
	defer rows.Close()
	n, added := 0, 0
	for rows.Next() {
		var id int64
		var b []byte
		if err := rows.Scan(&id, &b); err != nil {
			return err
		}
		n++
		p := filepath.Join(dir, fmt.Sprintf("q%d.gpx", id))
		if _, err := os.Stat(p); err == nil {
			continue
		}
		if err := os.WriteFile(p, b, 0o600); err != nil {
			return err
		}
		added++
	}
	fmt.Printf("snapshot: %d queue rows, %d new files in %s\n", n, added, dir)
	if err := rows.Err(); err != nil {
		return err
	}
	return snapshotHistory(db, root)
}

// decimate keeps one point per >= sec seconds: the same movement as a slower
// tracker would have reported it. A classifier that reads movement and not the
// device's reporting interval must score the same on it.
func decimate(t gpx.Track, sec float64) gpx.Track {
	if sec <= 0 {
		return t
	}
	out := t
	out.Segments = nil
	for _, seg := range t.Segments {
		var keep []gpx.Point
		var last *time.Time
		for i, p := range seg {
			if p.Time == nil || last == nil || p.Time.Sub(*last).Seconds() >= sec || i == len(seg)-1 {
				keep = append(keep, p)
				if p.Time != nil {
					last = p.Time
				}
			}
		}
		out.Segments = append(out.Segments, keep)
	}
	return out
}

func main() {
	root := flag.String("dir", "data/eval/movement", "corpus root (raw/, labelled/, history.jsonl.gz)")
	basePath := flag.String("baseline", "", "baseline to compare against (default <dir>/baseline.json)")
	writeBase := flag.Bool("write-baseline", false, "overwrite the baseline with this run")
	nMiss := flag.Int("misses", 0, "print the N largest (km) misclassified strong-truth segments")
	snap := flag.String("snapshot", "", "sqlite db to copy the corpus from (upload_queue + gpx_upload_logs), then exit")
	minKm := flag.Float64("min-km", 0, "only score segments at least this long")
	sources := flag.String("source", "raw,history,labelled", "which corpora to score")
	resample := flag.Float64("resample", 0, "decimate every track to one point per N seconds before classifying (robustness check)")
	dump := flag.String("dump", "", "write one CSV row of metrics per scored segment")
	archive := flag.String("archive", "data/autofetch_archive/af12", "autofetch archive the learner replay reads (*.gpx.gz)")
	refDB := flag.String("ref-db", "db.sqlite3", "database the replay reads reference roads and places from")
	replayParks := flag.String("replay-parks", "TZA_Nyerere,TZA_Ruaha", "parks the learner replay scores")
	dumpLearner := flag.String("dump-learner", "", "write the learner training table (one row per feature per day)")
	replayOnly := flag.Bool("replay-only", false, "run only the learner replay")
	dumpAir := flag.String("dump-aircraft", "", "write the aircraft-subtype training table (one row per labelled aircraft journey)")
	flag.Parse()
	if *basePath == "" {
		*basePath = filepath.Join(*root, "baseline.json")
	}

	doReplay := func() *replayReport {
		var known []srv.KnownAirfield
		for _, a := range loadAirfields(*root) {
			known = append(known, srv.KnownAirfield{Name: a.Name, Lat: a.Lat, Lon: a.Lon})
		}
		r, err := replayLearner(*archive, *refDB, strings.Split(*replayParks, ","), known, *dumpLearner)
		if err != nil {
			fatal(err)
		}
		if r == nil {
			fmt.Printf("\n== LEARNER REPLAY: unmeasured (no archive in %s)\n", *archive)
			return nil
		}
		printReplay(r)
		return r
	}
	if *replayOnly {
		doReplay()
		return
	}

	if *snap != "" {
		if err := snapshot(*snap, *root); err != nil {
			fatal(err)
		}
		return
	}

	items := loadCorpus(*root)
	if len(items) == 0 {
		fatal(fmt.Errorf("no corpus in %s — run with -snapshot db.sqlite3", *root))
	}

	var rep report
	rep.Corpus.ByER = map[string]int{}
	rep.BySource = map[string]score{}
	plain, weak, sub, hinted, bySubj, vsub := matrix{}, matrix{}, matrix{}, matrix{}, matrix{}, matrix{}
	bySource, subBySource := map[string]matrix{}, map[string]matrix{}
	seenFile := map[string]bool{}

	var dw *csv.Writer
	if *dump != "" {
		fh, err := os.Create(*dump)
		if err != nil {
			fatal(err)
		}
		defer fh.Close()
		dw = csv.NewWriter(fh)
		defer dw.Flush()
		dw.Write([]string{"source", "file", "er", "truth", "strong", "tsub", "pred", "psub", "pts", "km", "min",
			"avg", "p90", "p10", "max", "iv", "lin", "smooth", "bvar", "stopf", "istops", "cv", "turn", "sharp", "hover",
			"accel", "has_elev", "elev_rng", "to_roll", "ld_roll", "to_acc", "ld_dec", "bbox"})
	}

	var obsNew, obsOld []srv.AirfieldObservation
	var aw *csv.Writer
	if *dumpAir != "" {
		fh, err := os.Create(*dumpAir)
		if err != nil {
			fatal(err)
		}
		defer fh.Close()
		aw = csv.NewWriter(fh)
		defer aw.Flush()
		aw.Write(append([]string{"source", "file", "chain", "sub", "strong", "km", "cruise_kmh"}, gpx.AircraftFeatureNames...))
	}

	for _, it := range items {
		if !strings.Contains(","+*sources+",", ","+it.Source+",") {
			continue
		}
		t := decimate(it.Track, *resample)
		truth, strong, tsub := truthFor(t)
		npts := 0
		for _, s := range t.Segments {
			npts += len(s)
		}
		if !seenFile[it.Source+it.File] {
			seenFile[it.Source+it.File] = true
			rep.Corpus.Files++
		}
		rep.Corpus.Tracks++
		rep.Corpus.Points += npts
		erKey := t.ERSubjectType + "/" + t.ERSubjectSubtype
		if t.ERPatrolType != "" {
			erKey += "/" + t.ERPatrolType
		}
		rep.Corpus.ByER[it.Source+":"+erKey] += npts
		if truth == "" {
			continue
		}
		// History "foot" is the classifier's own earlier answer: never strong.
		if bySource[it.Source] == nil {
			bySource[it.Source], subBySource[it.Source] = matrix{}, matrix{}
		}
		blind := run(stripHints(t))
		if truth == "aircraft" {
			// Scored per device-truth airframe: a plane can only land on a
			// strip, a helicopter lands wherever the work is.
			for _, o := range srv.AirfieldObservations(blind) {
				o.Subtype = tsub
				obsNew = append(obsNew, o)
			}
			for _, o := range srv.LegacyAirfieldObservations(blind) {
				o.Subtype = tsub
				obsOld = append(obsOld, o)
			}
		}
		if aw != nil && truth == "aircraft" && tsub != "" {
			dumpAircraftFeatures(aw, it, tsub, strong, blind)
		}
		for _, cs := range blind {
			p := predicted(cs)
			// A parked tracker has no movement to classify: not scored.
			if p == "x:idle" || p == "x:static" || cs.DistanceKm < *minKm {
				continue
			}
			min := cs.Duration.Minutes()
			ps := cs.MovementSubtype
			if ps == "" {
				ps = "none"
			}
			var m gpx.MovementMetrics
			if dw != nil || p != truth || ps != tsub {
				m = gpx.AnalyzeTrajectory(cs.Points)
			}
			if dw != nil {
				f := func(v float64) string { return strconv.FormatFloat(v, 'f', 3, 64) }
				dw.Write([]string{it.Source, it.File, erKey, truth, fmt.Sprint(strong), tsub, p, ps, fmt.Sprint(len(cs.Points)), f(cs.DistanceKm), f(min),
					f(m.AvgSpeedKmh), f(m.P90SpeedKmh), f(m.P10SpeedKmh), f(m.MaxSpeedKmh), f(m.MedianIntervalSec), f(m.LinearityScore), f(m.SmoothnessFactor),
					f(m.BearingVariance), f(m.StopFrequency), fmt.Sprint(m.InteriorStopEvents), f(m.SpeedCV), f(m.MeanTurnAngleDeg), f(m.SharpTurnRatio), f(m.HoverRatio),
					f(m.AccelerationScore), fmt.Sprint(m.HasElevation), f(m.ElevationRangeM), f(m.TakeoffRollM), f(m.LandingRollM), f(m.TakeoffAccelKmhs), f(m.LandingDecelKmhs), f(m.BoundingBoxKm)})
			}
			if !strong {
				weak.add(truth, p, cs.DistanceKm, min)
				continue
			}
			plain.add(truth, p, cs.DistanceKm, min)
			bySource[it.Source].add(truth, p, cs.DistanceKm, min)
			bySubj.add(erKey, p, cs.DistanceKm, min)
			if p != truth {
				rep.misses = append(rep.misses, miss{it.File, erKey, truth, p, cs.Reason, cs.DistanceKm, min, cs.AvgSpeedKmh, len(cs.Points), m})
			}
			if truth == "vehicle" && p == "vehicle" {
				// The autofetch fleet is trucks, cars, motorcycles and plant:
				// every device-mounted vehicle here is on land unless its
				// subject says boat.
				vt, vp := "land", "land"
				if strings.Contains(strings.ToLower(t.ERSubjectSubtype), "boat") {
					vt = "boat"
				}
				if ps == "boat" {
					vp = "boat"
				}
				vsub.add(vt, vp, cs.DistanceKm, min)
			}
			if truth == "aircraft" && p == "aircraft" && tsub != "" {
				sub.add(tsub, ps, cs.DistanceKm, min)
				subBySource[it.Source].add(tsub, ps, cs.DistanceKm, min)
				if ps != tsub {
					rep.subtypeMiss = append(rep.subtypeMiss, miss{it.File, erKey, tsub, ps, cs.Reason, cs.DistanceKm, min, cs.AvgSpeedKmh, len(cs.Points), m})
				}
			}
		}
		if strong && it.Source == "raw" {
			for _, cs := range run(t) {
				p := predicted(cs)
				if p == "x:idle" || p == "x:static" || cs.DistanceKm < *minKm {
					continue
				}
				hinted.add(truth, p, cs.DistanceKm, cs.Duration.Minutes())
			}
		}
	}

	rep.Plain, rep.PlainWeak, rep.Subtype, rep.Hinted, rep.BySubject = plain.score(), weak.score(), sub.score(), hinted.score(), bySubj
	rep.VehSubtype = vsub.score()
	for k, m := range bySource {
		rep.BySource[k] = m.score()
		rep.BySource[k+":subtype"] = subBySource[k].score()
	}

	fmt.Printf("corpus: %d files, %d tracks, %d points", rep.Corpus.Files, rep.Corpus.Tracks, rep.Corpus.Points)
	if *resample > 0 {
		fmt.Printf("  [decimated to >=%.0fs]", *resample)
	}
	fmt.Println()
	printScore("PLAIN movement vs device truth, all sources (the number to improve)", rep.Plain)
	for _, k := range keys(rep.BySource) {
		s := rep.BySource[k]
		if len(s.Matrix) > 0 {
			fmt.Printf("    %-18s macro recall %5.1f%% km  %5.1f%% segs\n", k, 100*s.MacroKm, 100*s.MacroSegs)
		}
	}
	printMatrix("  by ER subtype (km)", bySubj)
	printScore("PLAIN subtype, aircraft only", rep.Subtype)
	printScore("PLAIN subtype, vehicles only (land vs boat)", rep.VehSubtype)
	rep.Learner = map[string]learnerScore{}
	if known := loadAirfields(*root); len(known) > 0 {
		fmt.Printf("\n== LEARNER: take-offs / landings (reference: %d OurAirports airfields; bush strips are under-listed, so 'known' is a floor)\n", len(known))
		fmt.Println("    'reused' = events at a site used >= 5 times. A real strip is used again; an artefact is not.")
		for _, sub := range []string{"fixed_wing", "rotor_wing"} {
			for _, k := range []string{"legacy", "journey"} {
				obs := obsOld
				if k == "journey" {
					obs = obsNew
				}
				var o []srv.AirfieldObservation
				for _, x := range obs {
					if x.Subtype == sub {
						o = append(o, x)
					}
				}
				l := scoreAirfields(o, known)
				rep.Learner[k+":"+sub] = l
				fmt.Printf("    %-10s %-8s %5d events | %4d sites, %4d used once | reused %5.1f%% of events in %3d sites | known airfields found %2d (%4.1f%% of events)\n",
					sub, k, l.Events, l.Sites, l.OnceSites, l.ReusedPct, l.BusySites, l.KnownFound, l.EventsKnownPct)
			}
		}
		if *nMiss > 0 {
			fmt.Println("    busiest journey-rule sites away from any known airfield (candidates for unlisted strips):")
			for _, u := range rep.Learner["journey:fixed_wing"].unknown {
				fmt.Println("     ", u)
			}
		}
	}
	printScore("PLAIN vs person-carried truth (weak: inferred, or the classifier's own stored answer)", rep.PlainWeak)
	printScore("HINTED reference, raw only (what production stores)", rep.Hinted)

	rep.Replay = doReplay()

	if *nMiss > 0 {
		printMisses("misclassified type", rep.misses, *nMiss)
		printMisses("misclassified aircraft subtype", rep.subtypeMiss, *nMiss)
	}

	if *resample > 0 || *sources != "raw,history,labelled" || *minKm > 0 {
		fmt.Println("\n(filtered run: not compared to the baseline)")
		return
	}
	if *writeBase {
		b, _ := json.MarshalIndent(rep, "", " ")
		if err := os.WriteFile(*basePath, b, 0o644); err != nil {
			fatal(err)
		}
		fmt.Println("\nbaseline written:", *basePath)
		return
	}
	var base report
	if b, err := os.ReadFile(*basePath); err == nil && json.Unmarshal(b, &base) == nil {
		fmt.Printf("\nvs baseline (%d pts → %d pts)\n", base.Corpus.Points, rep.Corpus.Points)
		if base.Corpus.Points != rep.Corpus.Points {
			fmt.Println("  ! corpus changed since baseline: deltas mix code and data; re-freeze with -write-baseline")
		}
		delta("type macro recall km", base.Plain.MacroKm, rep.Plain.MacroKm)
		delta("type macro recall segs", base.Plain.MacroSegs, rep.Plain.MacroSegs)
		for _, k := range keys(rep.Plain.RecallKm) {
			delta("type recall km "+k, base.Plain.RecallKm[k], rep.Plain.RecallKm[k])
			delta("type recall segs "+k, base.Plain.RecallSegs[k], rep.Plain.RecallSegs[k])
		}
		delta("subtype macro recall km", base.Subtype.MacroKm, rep.Subtype.MacroKm)
		delta("subtype macro recall segs", base.Subtype.MacroSegs, rep.Subtype.MacroSegs)
		delta("learner fixed-wing reuse, legacy → journey rule", rep.Learner["legacy:fixed_wing"].ReusedPct/100, rep.Learner["journey:fixed_wing"].ReusedPct/100)
		delta("land vehicle NOT called boat, km", base.VehSubtype.RecallKm["land"], rep.VehSubtype.RecallKm["land"])
		for _, k := range keys(rep.BySource) {
			delta(k+" macro km", base.BySource[k].MacroKm, rep.BySource[k].MacroKm)
		}
		if base.Replay != nil && rep.Replay != nil {
			if base.Replay.Archives != rep.Replay.Archives {
				fmt.Printf("  ! replay archive grew since baseline (%d -> %d files): replay deltas mix code and data\n", base.Replay.Archives, rep.Replay.Archives)
			}
			for _, k := range []string{"airfield", "place", "road"} {
				delta("replay "+k+" approve precision", base.Replay.Family[k].Precision, rep.Replay.Family[k].Precision)
				delta("replay "+k+" approve recall", base.Replay.Family[k].Recall, rep.Replay.Family[k].Recall)
			}
		}
	} else {
		fmt.Println("\nno baseline at", *basePath, "(-write-baseline to freeze)")
	}
}

func delta(name string, a, b float64) {
	mark := " "
	if b < a-0.002 {
		mark = "▼"
	} else if b > a+0.002 {
		mark = "▲"
	}
	fmt.Printf("  %s %-32s %6.1f%% → %6.1f%%  (%+.1f)\n", mark, name, 100*a, 100*b, 100*(b-a))
}

func keys[V any](m map[string]V) []string {
	k := make([]string, 0, len(m))
	for s := range m {
		k = append(k, s)
	}
	sort.Strings(k)
	return k
}

func printMatrix(title string, m matrix) {
	preds := map[string]bool{}
	for _, row := range m {
		for p := range row {
			preds[p] = true
		}
	}
	cols := keys(preds)
	fmt.Printf("%s\n    %-28s", title, "truth \\ predicted")
	for _, c := range cols {
		fmt.Printf("%16s", c)
	}
	fmt.Println()
	for _, t := range keys(m) {
		fmt.Printf("    %-28s", t)
		for _, c := range cols {
			if x := m[t][c]; x != nil {
				fmt.Printf("%9.0fkm/%-4d", x.Km, x.Segs)
			} else {
				fmt.Printf("%16s", "·")
			}
		}
		fmt.Println()
	}
}

func printScore(title string, s score) {
	fmt.Printf("\n== %s\n", title)
	if len(s.Matrix) == 0 {
		fmt.Println("    (no segments: unmeasured)")
		return
	}
	printMatrix("  km/segments", s.Matrix)
	for _, k := range keys(s.RecallKm) {
		fmt.Printf("    %-10s recall %5.1f%% km  %5.1f%% segs   precision %5.1f%% km\n", k, 100*s.RecallKm[k], 100*s.RecallSegs[k], 100*s.PrecisionKm[k])
	}
	fmt.Printf("    macro recall: %.1f%% km, %.1f%% segs\n", 100*s.MacroKm, 100*s.MacroSegs)
}

func printMisses(title string, ms []miss, n int) {
	sort.Slice(ms, func(i, j int) bool { return ms[i].Km > ms[j].Km })
	fmt.Printf("\n== %s: %d, top %d by km\n", title, len(ms), n)
	for i, m := range ms {
		if i >= n {
			break
		}
		x := m.M
		fmt.Printf("  %-10s %-22s %s→%-10s %6.1fkm %5.0fmin pts=%-4d avg=%.0f p90=%.0f max=%.0f iv=%.0fs lin=%.2f smooth=%.2f bvar=%.2f stopf=%.2f istops=%d cv=%.2f turn=%.0f sharp=%.2f hover=%.2f\n",
			m.File, m.ERType, m.Truth, m.Pred, m.Km, m.Min, m.Pts, x.AvgSpeedKmh, x.P90SpeedKmh, x.MaxSpeedKmh, x.MedianIntervalSec,
			x.LinearityScore, x.SmoothnessFactor, x.BearingVariance, x.StopFrequency, x.InteriorStopEvents, x.SpeedCV, x.MeanTurnAngleDeg, x.SharpTurnRatio, x.HoverRatio)
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, "movement-eval:", err)
	os.Exit(1)
}

type airfield struct {
	ID   string  `json:"id"`
	Name string  `json:"name"`
	Lat  float64 `json:"lat"`
	Lon  float64 `json:"lon"`
}

type learnerScore struct {
	Events         int     `json:"events"`
	EventsKnownPct float64 `json:"events_known_pct"`
	Sites          int     `json:"sites"`
	SitesKnownPct  float64 `json:"sites_known_pct"`
	BusySites      int     `json:"busy_sites"` // used >= 5 times: what auto-approval acts on
	BusyKnownPct   float64 `json:"busy_known_pct"`
	KnownFound     int     `json:"known_found"`
	OnceSites      int     `json:"once_sites"`
	ReusedPct      float64 `json:"reused_pct"`
	unknown        []string
}

func loadAirfields(root string) []airfield {
	var a []airfield
	if b, err := os.ReadFile(filepath.Join(root, "airfields.json")); err == nil {
		json.Unmarshal(b, &a)
	}
	return a
}

func distKm(lat1, lon1, lat2, lon2 float64) float64 {
	dy := (lat2 - lat1) * 111.2
	dx := (lon2 - lon1) * 111.2 * math.Cos(lat1*math.Pi/180)
	return math.Hypot(dx, dy)
}

// scoreAirfields clusters observations the way the learner's table does
// (a new site unless one exists within 0.01 degrees) and asks how many land on
// an airfield somebody else has already mapped.
func scoreAirfields(obs []srv.AirfieldObservation, known []airfield) learnerScore {
	const nearKm = 2.0 // a runway is up to ~1.5 km long and the reference is one point on it
	type site struct {
		lat, lon float64
		n        int
		known    string
	}
	var sites []*site
	var s learnerScore
	found := map[string]bool{}
	evKnown := 0
	for _, o := range obs {
		k := ""
		for _, a := range known {
			if distKm(o.Lat, o.Lon, a.Lat, a.Lon) <= nearKm {
				k = a.ID
				break
			}
		}
		if k != "" {
			evKnown++
			found[k] = true
		}
		var hit *site
		for _, st := range sites {
			if math.Abs(st.lat-o.Lat) < 0.01 && math.Abs(st.lon-o.Lon) < 0.01 {
				hit = st
				break
			}
		}
		if hit == nil {
			hit = &site{lat: o.Lat, lon: o.Lon, known: k}
			sites = append(sites, hit)
		}
		hit.n++
	}
	s.Events, s.Sites, s.KnownFound = len(obs), len(sites), len(found)
	sk, bk, reused := 0, 0, 0
	sort.Slice(sites, func(i, j int) bool { return sites[i].n > sites[j].n })
	for _, st := range sites {
		if st.known != "" {
			sk++
		}
		if st.n == 1 {
			s.OnceSites++
		}
		if st.n >= 5 {
			reused += st.n
			s.BusySites++
			if st.known != "" {
				bk++
			} else if len(s.unknown) < 15 {
				s.unknown = append(s.unknown, fmt.Sprintf("%.4f,%.4f  %d events", st.lat, st.lon, st.n))
			}
		}
	}
	pct := func(a, b int) float64 {
		if b == 0 {
			return 0
		}
		return 100 * float64(a) / float64(b)
	}
	s.ReusedPct = pct(reused, len(obs))
	s.EventsKnownPct, s.SitesKnownPct, s.BusyKnownPct = pct(evKnown, len(obs)), pct(sk, len(sites)), pct(bk, s.BusySites)
	return s
}
