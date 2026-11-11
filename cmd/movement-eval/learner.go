package main

// LEARNER REPLAY. The archive of everything the autofetch source ever
// delivered (data/autofetch_archive/<env>/*.gpx.gz) is classified blind and
// fed to the learner oldest first; then, for each day of that history, every
// feature is scored on the evidence available THAT day and the decision the
// server would have taken is recorded.
//
// Truth is what happened next: a feature is REAL if it was used again within
// srv.LearnerHorizonDays. It needs no reviewer and no reference list, it
// exists for every family, and it is the thing a reviewer is being asked to
// predict when they approve an airstrip. (For airfields the OurAirports list
// is reported beside it as an independent check; it is not the target,
// because bush strips are mostly missing from it.)
//
// The split is in time: the model is fitted on decisions taken up to the
// split day, and everything reported here is measured on decisions taken
// after it -- minus an embargo of one horizon, so no training label was
// computed from a day the test set also sees.

import (
	"compress/gzip"
	"context"
	"encoding/csv"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"srv.exe.dev/db"
	"srv.exe.dev/srv"
	"srv.exe.dev/srv/areas"
	"srv.exe.dev/srv/gpx"
)

type replayScore struct {
	// Held-out feature-days: one per feature per day it existed.
	Decisions int     `json:"decisions"`
	Positives int     `json:"positives"` // used again within the horizon
	Approved  int     `json:"approved"`
	Precision float64 `json:"precision"` // of approved feature-days, share used again
	Recall    float64 `json:"recall"`    // of feature-days used again, share approved
	Brier     float64 `json:"brier"`
	// Per feature, at the day it was FIRST auto-approved (held-out days only).
	FirstApproved   int     `json:"first_approved"`
	FirstApprovedOK float64 `json:"first_approved_precision"`
	Rejected        int     `json:"rejected"`
	RejectedWrong   float64 `json:"rejected_wrong"` // share of rejected feature-days used again
	Demotions       int     `json:"demotions"`
	// End state over the whole archive.
	Final map[string]int `json:"final"`
	// Airfields only: approved sites matching the reference list.
	ApprovedKnown int `json:"approved_known,omitempty"`
	RunwaysDrawn  int `json:"runways_drawn,omitempty"`
}

type replayReport struct {
	Archives  int                     `json:"archives"`
	From, To  string                  `json:"-"`
	SplitDay  string                  `json:"split_day"`
	Events    map[string]int          `json:"events"`
	Dupes     int                     `json:"duplicates_refused"`
	Redeliver int                     `json:"redelivery_added"` // events added by delivering every archive twice: must be 0
	Family    map[string]replayScore  `json:"family"`
	Calib     map[string][][3]float64 `json:"-"`
}

func classifyArchive(path string) ([]srv.ClassifiedSegment, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	zr, err := gzip.NewReader(f)
	if err != nil {
		return nil, err
	}
	d, err := gpx.ParseGPX(zr)
	if err != nil {
		return nil, err
	}
	for i := range d.Tracks {
		gpx.StripDeviceLabels(&d.Tracks[i]) // movement only, as the server does
	}
	segs := gpx.RemoveStraightLineGaps(gpx.SplitIntoSegments(d, 0))
	return srv.ValidateAndClassifyGPX(segs).ClassifiedSegments, nil
}

func dayEnd(day string) time.Time {
	t, _ := time.Parse("2006-01-02", day)
	return t.Add(24*time.Hour - time.Second)
}

// replayLearner runs the replay. dump, when set, receives the training table.
func replayLearner(archiveDir, dbPath string, parks []string, known []srv.KnownAirfield, dump string) (*replayReport, error) {
	files, _ := filepath.Glob(filepath.Join(archiveDir, "*.gpx.gz"))
	sort.Strings(files)
	if len(files) == 0 {
		return nil, nil
	}
	ctx := context.Background()
	store, err := areas.LoadKeystones("data")
	if err != nil {
		return nil, fmt.Errorf("areas: %w", err)
	}
	pls := map[string]*srv.ParkLearner{}
	for _, p := range parks {
		pls[p] = srv.NewParkLearner(p)
		pls[p].KnownAirfields = known
	}
	if sdb, err := db.Open(dbPath); err == nil {
		defer sdb.Close()
		for _, p := range parks {
			pls[p] = srv.NewReferenceParkLearner(ctx, sdb, p, known)
		}
	} else {
		fmt.Printf("    (no reference db at %s: road/gazetteer agreement terms are absent)\n", dbPath)
	}
	rep := &replayReport{Archives: len(files), Events: map[string]int{}, Family: map[string]replayScore{}, Calib: map[string][][3]float64{}}
	var all [][]srv.ClassifiedSegment
	for _, f := range files {
		segs, err := classifyArchive(f)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", f, err)
		}
		all = append(all, segs)
		for _, p := range parks {
			srv.ReplayUpload(pls[p], segs, store)
		}
	}
	added := 0
	addedBy := map[string]int{}
	for _, p := range parks {
		added += len(pls[p].NewEvents)
		addedBy[p] = len(pls[p].NewEvents)
	}
	// Event-level de-duplication, measured: deliver everything a second time.
	for _, segs := range all {
		for _, p := range parks {
			srv.ReplayUpload(pls[p], segs, store)
		}
	}
	for _, p := range parks {
		rep.Redeliver += len(pls[p].NewEvents)
		rep.Dupes += pls[p].Dupes
		if os.Getenv("REPLAY_DEBUG") != "" {
			for _, e := range pls[p].NewEvents[len(pls[p].NewEvents)-0:] {
				_ = e
			}
			k := map[string]int{}
			for i, e := range pls[p].NewEvents {
				if i >= addedBy[p] {
					k[e.Kind]++
					if e.Kind != "traversal" {
						fmt.Println("   redelivered", p, e.Kind, e.Tracker, e.Start, e.End, e.Lat, e.Lon)
					}
				}
			}
			fmt.Println("   redelivery by kind", p, k)
		}
	}
	rep.Redeliver -= added
	all = nil

	fams := func(pl *srv.ParkLearner) map[string][]*srv.LearnedFeature {
		return map[string][]*srv.LearnedFeature{"airfield": pl.Airfields, "place": pl.Places, "road": pl.Roads}
	}
	first, last := "9999", ""
	for _, pl := range pls {
		for _, fs := range fams(pl) {
			for _, f := range fs {
				for _, e := range f.Events {
					d := e.Start.UTC().Format("2006-01-02")
					first, last = min(first, d), max(last, d)
					rep.Events[e.Kind]++
				}
			}
		}
	}
	if last == "" {
		return rep, nil
	}
	rep.From, rep.To = first, last
	t0, t1 := dayEnd(first), dayEnd(last)
	nDays := int(t1.Sub(t0).Hours()/24) + 1
	h := srv.LearnerHorizonDays
	// Days whose label is known: day + horizon <= last. Train on the first
	// half of those, embargo one horizon, test on the rest.
	labelled := nDays - h
	split := labelled / 2
	testFrom := split + h
	if testFrom >= labelled { // short archive: no embargo possible, say so
		testFrom = split + 1
	}
	rep.SplitDay = t0.Add(time.Duration(split) * 24 * time.Hour).Format("2006-01-02")

	var w *csv.Writer
	if dump != "" {
		df, err := os.Create(dump)
		if err != nil {
			return nil, err
		}
		defer df.Close()
		w = csv.NewWriter(df)
		defer w.Flush()
		w.Write([]string{"family", "park", "feature", "day", "day_index", "set", "label", "idle_active_days", "kind", "known", "features"})
	}

	type agg struct {
		n, pos, app, appPos, rej, rejPos, firstN, firstOK, dem int
		brier                                                  float64
		bins                                                   [10][3]float64
	}
	A := map[string]*agg{"airfield": {}, "place": {}, "road": {}}
	everApproved := map[*srv.LearnedFeature]bool{}
	for di := 0; di < nDays; di++ {
		asOf := t0.Add(time.Duration(di) * 24 * time.Hour)
		horizon := asOf.Add(time.Duration(h) * 24 * time.Hour)
		for _, park := range parks {
			pl := pls[park]
			prev := map[*srv.LearnedFeature]string{}
			for _, fs := range fams(pl) {
				for _, f := range fs {
					prev[f] = f.Status
				}
			}
			pl.Rescore(asOf)
			for fam, fs := range fams(pl) {
				a := A[fam]
				for _, f := range fs {
					if len(f.Events) == 0 || f.Events[0].Start.After(asOf) {
						continue
					}
					if prev[f] == "auto_approved" && f.Status == "pending" && di >= testFrom {
						a.dem++
					}
					if di >= labelled {
						continue
					}
					label := 0
					for _, e := range f.Events {
						if e.Start.After(asOf) && !e.Start.After(horizon) {
							label = 1
							break
						}
					}
					set := "embargo"
					if di <= split {
						set = "train"
					} else if di >= testFrom {
						set = "test"
					}
					if w != nil {
						var fv []string
						for _, x := range srv.LearnerFeatures(f.Ev) {
							fv = append(fv, strconv.FormatFloat(x, 'f', 5, 64))
						}
						w.Write([]string{fam, park, strconv.FormatInt(f.ID, 10), asOf.Format("2006-01-02"), strconv.Itoa(di), set,
							strconv.Itoa(label), strconv.Itoa(f.Ev.IdleActiveDays), f.Ev.Kind, f.Ev.Known, strings.Join(fv, ";")})
					}
					if set != "test" {
						if f.Status == "auto_approved" {
							everApproved[f] = true
						}
						continue
					}
					a.n++
					a.pos += label
					d := f.Score - float64(label)
					a.brier += d * d
					b := min(int(f.Score*10), 9)
					a.bins[b][0]++
					a.bins[b][1] += f.Score
					a.bins[b][2] += float64(label)
					switch f.Status {
					case "auto_approved":
						a.app++
						a.appPos += label
						if !everApproved[f] {
							everApproved[f] = true
							a.firstN++
							a.firstOK += label
						}
					case "rejected":
						a.rej++
						a.rejPos += label
					}
				}
			}
		}
	}
	div := func(a, b int) float64 {
		if b == 0 {
			return -1 // unmeasured (NaN does not survive JSON)
		}
		return float64(a) / float64(b)
	}
	for fam, a := range A {
		s := replayScore{Decisions: a.n, Positives: a.pos, Approved: a.app, Precision: div(a.appPos, a.app), Recall: div(a.appPos, a.pos),
			FirstApproved: a.firstN, FirstApprovedOK: div(a.firstOK, a.firstN), Rejected: a.rej, RejectedWrong: div(a.rejPos, a.rej),
			Demotions: a.dem, Final: map[string]int{}}
		if a.n > 0 {
			s.Brier = a.brier / float64(a.n)
		}
		for _, pl := range pls {
			for _, f := range fams(pl)[fam] {
				s.Final[f.Status]++
				if fam == "airfield" && f.Status == "auto_approved" {
					if f.Ev.Known != "" {
						s.ApprovedKnown++
					}
					if len(f.Ev.Line) == 2 {
						s.RunwaysDrawn++
					}
				}
			}
		}
		rep.Family[fam] = s
		for _, b := range a.bins {
			if b[0] > 0 {
				rep.Calib[fam] = append(rep.Calib[fam], [3]float64{b[1] / b[0], b[2] / b[0], b[0]})
			}
		}
	}
	return rep, nil
}

func pct(x float64) string {
	if x < 0 || math.IsNaN(x) {
		return "unmeasured"
	}
	return fmt.Sprintf("%.1f%%", 100*x)
}

func printReplay(r *replayReport) {
	fmt.Printf("\n== LEARNER REPLAY: %d archives, %s .. %s; fitted on decisions up to %s, scored on later days (embargo %d d)\n",
		r.Archives, r.From, r.To, r.SplitDay, srv.LearnerHorizonDays)
	fmt.Printf("    events: %d landings, %d take-offs, %d stops, %d road traversals; %d re-deliveries refused\n",
		r.Events["landing"], r.Events["takeoff"], r.Events["stop"], r.Events["traversal"], r.Dupes)
	fmt.Printf("    delivering every archive a second time added %d events (must be 0)\n", r.Redeliver)
	fmt.Println("    truth = used again within the horizon. precision/recall over held-out feature-days;")
	fmt.Println("    'first' = features at the day they were first auto-approved.")
	fmt.Printf("    %-9s %9s %9s %9s %10s %8s %14s %12s %9s %6s\n", "family", "decisions", "approved", "precision", "recall", "Brier", "first (n, ok)", "rejected", "wrong", "demot.")
	for _, fam := range []string{"airfield", "place", "road"} {
		s := r.Family[fam]
		fmt.Printf("    %-9s %9d %9d %9s %10s %8.3f %5d %8s %12d %9s %6d\n", fam, s.Decisions, s.Approved, pct(s.Precision), pct(s.Recall),
			s.Brier, s.FirstApproved, pct(s.FirstApprovedOK), s.Rejected, pct(s.RejectedWrong), s.Demotions)
	}
	for _, fam := range []string{"airfield", "place", "road"} {
		s := r.Family[fam]
		fmt.Printf("    %-9s end state: %d approved, %d pending, %d rejected", fam, s.Final["auto_approved"], s.Final["pending"], s.Final["rejected"])
		if fam == "airfield" {
			fmt.Printf("; of the approved %d are on the reference list, %d have a measured runway line", s.ApprovedKnown, s.RunwaysDrawn)
		}
		fmt.Println()
		if len(r.Calib[fam]) > 0 {
			fmt.Printf("      calibration (predicted -> observed, n):")
			for _, b := range r.Calib[fam] {
				fmt.Printf("  %.2f->%.2f (%.0f)", b[0], b[1], b[2])
			}
			fmt.Println()
		}
	}
}
