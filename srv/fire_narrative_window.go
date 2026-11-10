package srv

import (
	"fmt"
	"math"
	"sort"
	"strings"
	"time"
)

// A date-locked guest link (short_links.date_from/date_to) and the fire
// narrative.
//
// The narrative is ONE cached whole-history body (single writer:
// scripts/precompute_narratives_v5.py, invariant 10) that the UI subsets
// client-side. For a locked key that body is rebuilt here, in memory, on the
// way out — the cache is never written — so the guest gets the same shape
// holding only its window ("it clamps, it does not refuse",
// docs/agents/sharing.md).
//
// THE EDGE RULE. A group belongs to the window only if it BEGAN AND ENDED
// inside it (start_date >= from AND end_date <= to). A group straddling an
// edge is dropped whole, not cut: its figures (detections, FRP, distance,
// days) and its prose are statements about the whole chain, the cached struct
// holds no per-day breakdown to cut them with, and keeping it would publish an
// out-of-window date and a number partly earned outside the grant. The map
// agrees: guestTrajectoryEndSQL adds the same end_date bound to the viewport
// feature endpoints, so a line is drawn exactly when its row is listed.
//
// Every aggregate is then RECOMPUTED from the surviving groups with the
// writer's own formulas. What cannot be recomputed for a window is omitted,
// never left at its all-time value (invariant 7): hotspots, monthly series,
// the latitude comparison, and the between-year comparisons (trend direction,
// worst/best year, mean density) unless the window holds whole calendar years.

// FireNarrativeWindow names the basis of every number in a windowed body.
type FireNarrativeWindow struct {
	From string `json:"from"`
	To   string `json:"to"`
	Rule string `json:"rule"`
}

const fireWindowRule = "groups that began and ended within the window; every total is recomputed from them"

// guestTrajectoryEndSQL is the map's half of the edge rule: for a locked key,
// a fire trajectory must also END inside the window (the start bound is
// already applied by the clamped from/to).
func guestTrajectoryEndSQL(from, to string, locked bool, featureType string) (string, []interface{}) {
	if !locked || featureType != "fire_trajectory" {
		return "", nil
	}
	return " AND start_date >= ? AND end_date <= ?", []interface{}{from, to}
}

func round1(v float64) float64 { return math.Round(v*10) / 10 }

// sqliteWeek reproduces strftime('%Y-W%W'): week of year, Monday first, 00-53.
func sqliteWeek(t time.Time) string {
	mon0 := (int(t.Weekday()) + 6) % 7
	return fmt.Sprintf("%d-W%02d", t.Year(), (t.YearDay()-1+7-mon0)/7)
}

// filterFireNarrativeToWindow rewrites n in place to describe [from, to] only.
// areaKm2 <= 0 means the density figures are omitted rather than guessed.
func filterFireNarrativeToWindow(n *FireNarrative, from, to string, areaKm2 float64) {
	if n == nil {
		return
	}
	kept := make([]FireGroupStory, 0, 64)
	for _, st := range n.Narratives {
		if st.StartDate == "" || st.EndDate == "" || st.StartDate < from || st.EndDate > to {
			continue
		}
		// Legacy v2 dates, if a row still carries them, obey the same bound.
		if (st.EntryDate != "" && (st.EntryDate < from || st.EntryDate > to)) ||
			(st.LastInside != "" && (st.LastInside < from || st.LastInside > to)) {
			continue
		}
		// group_num is an ordinal over the WHOLE history ("group 1580" says
		// 1,579 came before); renumber within the window. feature_id stays —
		// it is the key the map selects by.
		st.GroupNum = len(kept) + 1
		kept = append(kept, st)
	}

	seasonality := ""
	if n.Trend != nil {
		seasonality = n.Trend.Seasonality // climatology, not fire data
	}
	basis := n.TotalFiresBasis
	if basis == "" {
		basis = "detections in fire groups near or inside the park (v5 trajectories)"
	}

	out := FireNarrative{
		ParkID:          n.ParkID,
		ParkName:        n.ParkName,
		Narratives:      kept,
		KeyPlaces:       n.KeyPlaces, // OSM places: undated
		TotalGroups:     len(kept),
		TotalFiresBasis: basis + ", limited to " + fireWindowRule + " (" + from + " – " + to + ")",
		Window:          &FireNarrativeWindow{From: from, To: to, Rule: fireWindowRule},
		TrajectoryTypes: map[string]int{},
		Seasons:         map[string]int{},
		Directions:      map[string]int{},
	}
	if y, err := time.Parse("2006-01-02", to); err == nil {
		out.Year = y.Year()
	}

	type yearAcc struct {
		s                      FireYearSummary
		days, speed            float64
		stopped, transited, mg int
	}
	byYear := map[int]*yearAcc{}
	months := map[string]int{}
	weeks := map[string]int{}
	var frp, zig float64
	stoppedPct := 0
	isMgmt := func(st FireGroupStory) bool { return strings.Contains(strings.ToLower(st.GroupType), "management") }
	for _, st := range kept {
		t, err := time.Parse("2006-01-02", st.StartDate)
		if err != nil {
			continue
		}
		ya := byYear[t.Year()]
		if ya == nil {
			ya = &yearAcc{}
			ya.s.Year = t.Year()
			byYear[t.Year()] = ya
		}
		ya.s.TotalGroups++
		ya.s.TotalFires += st.FiresTotal
		ya.s.TotalFRP += st.TotalFRP
		ya.days += float64(st.Days)
		ya.speed += st.AvgSpeedKmDay
		months[t.Month().String()]++
		weeks[sqliteWeek(t)]++

		out.TotalFires += st.FiresTotal
		frp += st.TotalFRP
		zig += st.ZigzagRatio
		tt := st.TrajectoryType
		if tt == "" {
			tt = "unknown"
		}
		out.TrajectoryTypes[tt]++
		switch tt {
		case "erratic":
			out.ErraticCount++
		case "zigzag":
			out.ZigzagCount++
		case "clean":
			out.CleanCount++
		}
		if st.Season != "" {
			out.Seasons[st.Season]++
		}
		if st.Direction != "" {
			out.Directions[st.Direction]++
		}
		if isMgmt(st) {
			out.ManagementFires++
			ya.mg++
		}
		if st.CrossBorder {
			out.CrossBorderGroups++
		}
		switch st.Position {
		case "entirely_outside":
			out.OutsideParkGroups++
		case "ends_inside", "contained":
			out.StoppedInsideGroups++
			ya.stopped++
		case "transits":
			out.TransitedGroups++
			ya.transited++
		}
		if st.PctInside > 80 {
			stoppedPct++
		}
	}
	out.TotalFRP = round1(frp)
	if g := len(kept); g > 0 {
		out.AvgZigzagRatio = math.Round(zig/float64(g)*100) / 100
		out.ResponseRate = round1(float64(stoppedPct) / float64(g) * 100)
	}
	// Peak month: most group starts, ties broken by name so it is stable.
	for m, c := range months {
		if c > months[out.PeakMonth] || (c == months[out.PeakMonth] && m < out.PeakMonth) {
			out.PeakMonth = m
		}
	}

	trend := &FireTrendAnalysis{Years: []FireYearSummary{}, TrendDirection: "insufficient_data", Seasonality: seasonality}
	years := make([]int, 0, len(byYear))
	for y := range byYear {
		years = append(years, y)
	}
	sort.Ints(years)
	var rateSum float64
	var whole []FireYearSummary // calendar years lying wholly inside the window
	for _, y := range years {
		ya := byYear[y]
		g := float64(ya.s.TotalGroups)
		ya.s.StoppedInside, ya.s.Transited, ya.s.ManagementFires = ya.stopped, ya.transited, ya.mg
		ya.s.ResponseRate = round1(float64(ya.stopped) / g * 100)
		ya.s.TotalFRP = round1(ya.s.TotalFRP)
		ya.s.AvgDaysBurning = round1(ya.days / g)
		ya.s.AvgSpeedKmDay = round1(ya.speed / g)
		if areaKm2 > 0 {
			ya.s.GroupsPerKm2 = math.Round(g/areaKm2*1000*1e4) / 1e4
		}
		rateSum += ya.s.ResponseRate
		trend.Years = append(trend.Years, ya.s)
		if from <= fmt.Sprintf("%04d-01-01", y) && to >= fmt.Sprintf("%04d-12-31", y) {
			whole = append(whole, ya.s)
		}
	}
	if len(years) > 0 {
		trend.AvgResponseRate = round1(rateSum / float64(len(years)))
	}
	if out.PeakMonth != "" {
		trend.PeakMonths = []string{out.PeakMonth}
	}
	// Comparisons BETWEEN years are only made between whole years: a January
	// ranked against a twelve-month neighbour is not a "worst year".
	if len(whole) > 0 {
		worst, best, sum := whole[0], whole[0], 0
		for _, y := range whole {
			sum += y.TotalGroups
			if y.TotalGroups > worst.TotalGroups {
				worst = y
			}
			if y.TotalGroups < best.TotalGroups {
				best = y
			}
		}
		trend.WorstYear, trend.WorstYearGroups = worst.Year, worst.TotalGroups
		trend.BestYear, trend.BestYearRate = best.Year, best.ResponseRate
		if areaKm2 > 0 {
			trend.AvgGroupsPerKm2 = math.Round(float64(sum)/float64(len(whole))/areaKm2*1000*1e4) / 1e4
		}
		trend.Narrative = fmt.Sprintf("Worst year in the window: %d (%d groups).", worst.Year, worst.TotalGroups)
		if len(whole) >= 3 {
			var recent, older float64
			for i, y := range whole {
				if i >= len(whole)-2 {
					recent += float64(y.TotalGroups) / 2
				} else {
					older += float64(y.TotalGroups) / float64(len(whole)-2)
				}
			}
			switch {
			case recent > older*1.2:
				trend.TrendDirection = "increasing"
				trend.Narrative = "Fire activity has increased in recent years. " + trend.Narrative
			case recent < older*0.8:
				trend.TrendDirection = "decreasing"
				trend.Narrative = "Fire activity has decreased in recent years. " + trend.Narrative
			default:
				trend.TrendDirection = "stable"
				trend.Narrative = "Fire activity has remained relatively stable. " + trend.Narrative
			}
		}
	}
	// Weekly series from the same groups (not a second DB query that could
	// count a straddler the list above dropped).
	wk := make([]string, 0, len(weeks))
	for w := range weeks {
		wk = append(wk, w)
	}
	sort.Strings(wk)
	for _, w := range wk {
		ws := FireWeekSummary{Week: w, Groups: weeks[w]}
		if areaKm2 > 0 {
			ws.GroupsPerKm2 = float64(weeks[w]) / areaKm2
		}
		trend.Weeks = append(trend.Weeks, ws)
	}
	out.Trend = trend

	// Prose: rebuilt, because the cached sentence quotes all-time numbers.
	g := len(kept)
	if g == 0 {
		out.Summary = fmt.Sprintf("No fire groups began and ended within %s – %s near %s.", from, to, n.ParkName)
	} else {
		parts := []string{fmt.Sprintf("From %s to %s, %s experienced %s fire detections across %d fire groups that began and ended in that window.",
			from, to, n.ParkName, commaInt(out.TotalFires), g)}
		if out.ManagementFires > 0 {
			parts = append(parts, fmt.Sprintf("%d (%d%%) appear to be management burns.", out.ManagementFires, out.ManagementFires*100/g))
		}
		if out.CrossBorderGroups > 0 {
			parts = append(parts, fmt.Sprintf("%d (%d%%) crossed boundaries.", out.CrossBorderGroups, out.CrossBorderGroups*100/g))
		}
		if out.StoppedInsideGroups > 0 {
			parts = append(parts, fmt.Sprintf("%d (%d%%) stopped inside.", out.StoppedInsideGroups, out.StoppedInsideGroups*100/g))
		}
		if out.ErraticCount > 0 {
			parts = append(parts, fmt.Sprintf("%d erratic trajectories detected.", out.ErraticCount))
		}
		if out.PeakMonth != "" && len(months) > 1 {
			parts = append(parts, fmt.Sprintf("Peak: %s.", out.PeakMonth))
		}
		out.Summary = strings.Join(parts, " ")
	}
	*n = out
}

func commaInt(v int) string {
	s := fmt.Sprintf("%d", v)
	for i := len(s) - 3; i > 0; i -= 3 {
		s = s[:i] + "," + s[i:]
	}
	return s
}
