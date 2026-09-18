package srv

// Fire season front — the map's "where the burning season had arrived by
// when" overlay, and the vanguard chains that ran ahead of it.
//
// Written by scripts/fire_front.py (table fire_season_front, migration 066);
// this file only reads. Method and measurements: docs/agents/fire.md
// § Season front & vanguard.
//
//	GET /api/fire-season?area=<park|aoi>[&season=2024/25]
//	    → {"area","season","seasons":[...],"season_start","complete","latest_day",
//	       "stats":{...},"contours":<GeoJSON FeatureCollection>}
//	    Each contour Feature has properties {day, date, label}. `seasons`
//	    lists every stored season of the area (label, start, complete) so the
//	    UI can offer a picker without a second round trip.
//
//	GET /api/fire-vanguard?bbox=w,s,e,n&from=&to=&limit=
//	    → same wire format as /api/fire-anim-trajectories, restricted to
//	      vanguard groups (indexed: idx_fg_vanguard) and carrying per-vertex
//	      `leads` so the client can split a chain where the season caught up.
//
// Access: an AOI id is answered only if this request may see it
// (areaScopeParam contract — an invisible id 404s, never 403s).

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"
)

// vanguardRowsSQL is THE definition of "a vanguard chain" for every counting
// and drawing surface: the Kalman seed-ahead chains (feature_type
// 'fire_vanguard', scripts/fire_vanguard_kf.py) for every area listed in
// fire_vanguard_kf, and the plain trajectories flagged vanguard=1 for the
// rest — one population per area, never both (invariant 7). The subquery is
// a ≤ 200-row table; idx_fg_vanguard covers both feature types.
const vanguardRowsSQL = ` vanguard = 1 AND (feature_type = 'fire_vanguard' OR (feature_type = 'fire_trajectory' AND park_id NOT IN (SELECT area_id FROM fire_vanguard_kf)))`

// vanguardTracker says which population vanguardRowsSQL selects for an area:
// "kf" (Kalman seed-ahead chains) or "groups" (plain trajectories that began
// ahead). The word travels with every count so two surfaces cannot disagree
// silently.
func (s *Server) vanguardTracker(r *http.Request, area string) string {
	var n int
	s.DB.QueryRowContext(r.Context(), `SELECT COUNT(*) FROM fire_vanguard_kf WHERE area_id = ?`, area).Scan(&n)
	if n > 0 {
		return "kf"
	}
	return "groups"
}

func (s *Server) HandleAPIFireSeason(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	area := strings.TrimSpace(q.Get("area"))
	if area == "" && q.Get("bbox") != "" {
		// No focus, a viewport: every park's front in view (bbox mode).
		s.fireSeasonBBox(w, r)
		return
	}
	if area == "" {
		// No focus: the area whose front grid holds the view centre. An AOI
		// the caller may see wins (it is the larger question); otherwise the
		// park whose grid is smallest, i.e. the one this point is most
		// central to. Nothing under the point = "not yet computed" below.
		lon, e1 := strconv.ParseFloat(q.Get("lon"), 64)
		lat, e2 := strconv.ParseFloat(q.Get("lat"), 64)
		if e1 != nil || e2 != nil {
			http.Error(w, `{"error":"area or lon,lat required"}`, http.StatusBadRequest)
			return
		}
		area = s.fireSeasonAreaAt(r, lon, lat)
		if area == "" {
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Cache-Control", "private, max-age=120")
			json.NewEncoder(w).Encode(map[string]interface{}{
				"area": nil, "seasons": []struct{}{}, "season": nil, "contours": nil,
				"status": "no area with a season front here",
			})
			return
		}
	}
	if IsAOIID(area) {
		if !ValidAOIID(area) {
			http.NotFound(w, r)
			return
		}
		if _, err := s.GetAOI(area, s.RequestPrincipalID(r), false); err != nil {
			http.NotFound(w, r)
			return
		}
	} else if !ValidParkID(area) {
		http.NotFound(w, r)
		return
	}

	type seasonRow struct {
		Label    string `json:"label"`
		Start    string `json:"start"`
		End      string `json:"end"`
		Complete bool   `json:"complete"`
		Latest   string `json:"latest_day,omitempty"`
	}
	rows, err := s.DB.QueryContext(r.Context(), `
		SELECT season, season_start, season_end, complete, COALESCE(latest_day,'')
		FROM fire_season_front WHERE area_id = ? ORDER BY season_start`, area)
	if err != nil {
		internalError(w, "query failed", err)
		return
	}
	var seasons []seasonRow
	for rows.Next() {
		var sr seasonRow
		var complete int
		if rows.Scan(&sr.Label, &sr.Start, &sr.End, &complete, &sr.Latest) == nil {
			sr.Complete = complete == 1
			seasons = append(seasons, sr)
		}
	}
	rows.Close()
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, max-age=600")
	if len(seasons) == 0 {
		// Not an error: the nightly rotation has not reached this area yet.
		json.NewEncoder(w).Encode(map[string]interface{}{
			"area": area, "seasons": []seasonRow{}, "season": nil, "contours": nil,
			"status": "not yet computed",
		})
		return
	}
	want := q.Get("season")
	if want == "" {
		// Default season: the one the reader's time window ENDS in (`at=`),
		// else the latest. A 2024/25 window drawn with the 2025/26 front
		// is a picture that contradicts its own slider.
		want = seasons[len(seasons)-1].Label
		if at := q.Get("at"); at != "" {
			for _, sr := range seasons {
				if sr.Start <= at && at <= sr.End {
					want = sr.Label
					break
				}
			}
		}
	}
	var (
		seasonStart, latest, contours, stats sql.NullString
		complete, startMonth, nx, ny         int
		computedAt                           string
		frontBlob, usualBlob                 []byte
		gRes, gX0, gY0                       float64
	)
	err = s.DB.QueryRowContext(r.Context(), `
		SELECT season_start, complete, start_month, latest_day, contours_json, stats_json, computed_at,
		       nx, ny, front, usual, res, x0, y0
		FROM fire_season_front WHERE area_id = ? AND season = ?`, area, want).
		Scan(&seasonStart, &complete, &startMonth, &latest, &contours, &stats, &computedAt,
			&nx, &ny, &frontBlob, &usualBlob, &gRes, &gX0, &gY0)
	if err != nil {
		http.Error(w, `{"error":"no such season"}`, http.StatusNotFound)
		return
	}
	// Where the front stands at `at` (the slider's end): the share of cells
	// that will carry a front this season which it has reached, and how
	// this season compares with the usual one over those cells (median of
	// front − usual, days; + = later than usual). Both from the stored
	// int16 grids — 40 KB, not the 300 KB contour JSON — so the stats row
	// and the hover tips can ask with summary=1 and get no geometry.
	var atDos *int
	var frontPct, usualOffset *float64
	if at := q.Get("at"); at != "" && seasonStart.Valid {
		if t, ok := parseISODate(at); ok {
			if t0, ok := parseISODate(seasonStart.String); ok {
				d := int(t.Sub(t0).Hours() / 24)
				atDos = &d
				frontPct, usualOffset = frontProgress(frontBlob, usualBlob, nx, ny, d, complete == 1)
			}
		}
	}
	// Vanguard chains of this area in this season: the count the chip menu
	// prints, from the same rows the layer draws (invariant 7: one number).
	var nVan int
	var sEnd string
	for _, sr := range seasons {
		if sr.Label == want {
			sEnd = sr.End
		}
	}
	s.DB.QueryRowContext(r.Context(), `
		SELECT COUNT(*) FROM feature_geometries INDEXED BY idx_fg_vanguard
		WHERE`+vanguardRowsSQL+` AND park_id=?
		  AND start_date >= ? AND start_date <= ?`, area, seasonStart.String, sEnd).Scan(&nVan)
	vanTracker := s.vanguardTracker(r, area)
	// …and in the reader's window (from/to), which is what the stats panel
	// counts everything else by. Two numbers, two names (invariant 7).
	var nVanWin interface{}
	if from, to := q.Get("from"), q.Get("to"); from != "" || to != "" {
		if from == "" {
			from = "2012-01-01"
		}
		if to == "" {
			to = time.Now().UTC().Format("2006-01-02")
		}
		var n int
		s.DB.QueryRowContext(r.Context(), `
			SELECT COUNT(*) FROM feature_geometries INDEXED BY idx_fg_vanguard
			WHERE`+vanguardRowsSQL+` AND park_id=? AND start_date >= ? AND start_date <= ?`, area, from, to).Scan(&n)
		nVanWin = n
	}
	var contoursOut json.RawMessage
	if q.Get("summary") == "" {
		contoursOut = json.RawMessage(orNull(contours))
	}
	// speed=1: the isochrones split by speed class (srv/fire_season_lines.go)
	// so the client can weight the lines — the speed map as a property of
	// the front, not a second raster.
	var speedOut, speedStatsOut interface{}
	if q.Get("speed") != "" && q.Get("summary") == "" {
		sc, sst := speedContours(orNull(contours), frontBlob, nx, ny, gRes, gX0, gY0)
		if sc != nil {
			speedOut, speedStatsOut = sc, sst
		}
	}
	// Early-burn ground (srv/fire_early_ground.go) rides along only when
	// asked (`early=1`): ~20 KB of cells the summary path must not pay for.
	// Its first-burn column is this season's, so the animator can light a
	// cell up when the season's first detection lands in it.
	var earlyOut interface{}
	if q.Get("early") != "" {
		earlyOut = s.earlyGround(area, want).wire(want)
	}
	// The report side (`leads=N`): the vanguard chains themselves, ranked by
	// how far they ran ahead, with their evidence tier, plus the tier
	// breakdown of every vanguard chain in the window — so one call tells a
	// report writer (human or agent) what the layer shows and how much of it
	// is measured. Window = from/to when given, else the season.
	var vanTop []vanLead
	var vanTiers map[string]int
	var vanMarks []vanMark
	if n, _ := strconv.Atoi(q.Get("leads")); n > 0 {
		wf, wt := q.Get("from"), q.Get("to")
		if wf == "" {
			wf = seasonStart.String
		}
		if wt == "" {
			wt = sEnd
		}
		vanTop, vanTiers, vanMarks = s.vanguardLeads(r, area, wf, wt, n)
	}
	// The season as a curve: share of front-bearing cells reached per 5 d
	// of season, this season and the usual one, so the popup can draw the
	// S-curve with the vanguard ignitions ticked along it — and so the
	// animator can say where the front stands at its PLAYHEAD without a
	// request per frame (`FireSeason.meta()` reads the curve at the
	// playhead's day of season; `front_reached_pct` here is at `at`). ~75
	// numbers, so it rides along with the contours too.
	var curve interface{}
	if fc, uc := frontCurve(frontBlob, usualBlob, nx, ny, 5, complete == 1); fc != nil {
		curve = map[string]interface{}{"step_days": 5, "front": fc, "usual": uc}
	}
	var frontStats struct {
		First  string `json:"front_first"`
		Median string `json:"front_median"`
		Last   string `json:"front_last"`
	}
	if stats.Valid {
		json.Unmarshal([]byte(stats.String), &frontStats)
	}
	json.NewEncoder(w).Encode(map[string]interface{}{
		"area":               area,
		"vanguard_groups":    nVan,
		"vanguard_in_window": nVanWin,
		"vanguard_tracker":   vanTracker,
		"vanguard_top":       vanTop,
		"vanguard_tiers":     vanTiers,
		"vanguard_marks":     vanMarks,
		"front_curve":        curve,
		"at_dos":             atDos,
		"front_reached_pct":  frontPct,
		"usual_offset_days":  usualOffset,
		"season":             want,
		"seasons":            seasons,
		"season_start":       seasonStart.String,
		"start_month":        startMonth,
		"complete":           complete == 1,
		"latest_day":         latest.String,
		"computed_at":        computedAt,
		"stats":              json.RawMessage(orNull(stats)),
		"contours":           contoursOut,
		"speed_contours":     speedOut,
		"speed_stats":        speedStatsOut,
		"early_ground":       earlyOut,
		// One sentence, written once here so a report, a tip and an agent
		// quote the same words (the UI's seasonFrontWords is its short form).
		"words": seasonWords(want, complete == 1, frontStats.First, frontStats.Median, frontStats.Last,
			frontPct, usualOffset, nVan, nVanWin, vanTiers),
	})
}

// vanLead is one vanguard chain as a report lists it: where it began, how far
// ahead of the season, how far it ran before the season caught up, and the
// evidence tier of its day order. `tier` prints "unmeasured" when the rebuild
// has not scored it (invariant 12), never an empty string.
type vanLead struct {
	ID        string   `json:"id"`
	Start     string   `json:"start"`
	End       string   `json:"end"`
	Days      int      `json:"days"`
	Fires     int      `json:"fires"`
	Km        float64  `json:"km"`
	Direction string   `json:"direction,omitempty"`
	LeadStart *int     `json:"lead_start"`
	LeadBasis string   `json:"lead_basis,omitempty"`
	AheadKm   float64  `json:"ahead_km"`
	AheadDays int      `json:"ahead_days"`
	Tier      string   `json:"tier"`
	Lon       float64  `json:"lon"`
	Lat       float64  `json:"lat"`
	Place     string   `json:"nearest_place,omitempty"`
	PlaceKm   float64  `json:"nearest_place_km,omitempty"`
	Narrative string   `json:"narrative,omitempty"`
	Tracker   string   `json:"tracker,omitempty"`
	EndCause  string   `json:"end_cause,omitempty"`
	Heading   *int     `json:"heading_deg,omitempty"`
	SpeedKmd  *float64 `json:"speed_kmd,omitempty"`
}

// vanguardLeads returns the area's vanguard chains that began in [from, to],
// the top n by ahead_km (ties: lead_start), and the evidence-tier histogram
// over ALL of them — the histogram must describe the population the count
// describes, not the shortlist.
// vanMark is the least a timeline needs of one vanguard chain: the day it
// began, how far ahead of the front, and its id so a tick can be clicked.
type vanMark struct {
	ID   string `json:"id"`
	Day  string `json:"d"`
	Lead *int   `json:"lead"`
	Tier string `json:"tier"`
}

func (s *Server) vanguardLeads(r *http.Request, area, from, to string, n int) ([]vanLead, map[string]int, []vanMark) {
	rows, err := s.DB.QueryContext(r.Context(), `
		SELECT feature_id, start_date, end_date, geojson, properties_json
		FROM feature_geometries INDEXED BY idx_fg_vanguard
		WHERE`+vanguardRowsSQL+` AND park_id=? AND start_date >= ? AND start_date <= ?`, area, from, to)
	if err != nil {
		return nil, nil, nil
	}
	defer rows.Close()
	tiers := map[string]int{}
	var all []vanLead
	marks := []vanMark{}
	for rows.Next() {
		var fid, sd, ed, geojson, propsJSON string
		if rows.Scan(&fid, &sd, &ed, &geojson, &propsJSON) != nil {
			continue
		}
		var p struct {
			Days      int      `json:"days"`
			Fires     int      `json:"fires_total"`
			Km        float64  `json:"distance_km"`
			Direction string   `json:"direction"`
			LeadStart *int     `json:"lead_start"`
			LeadBasis string   `json:"lead_basis"`
			AheadKm   float64  `json:"ahead_km"`
			AheadDays int      `json:"ahead_days"`
			Tier      string   `json:"evidence_tier"`
			Place     string   `json:"nearest_place"`
			PlaceKm   float64  `json:"nearest_place_dist"`
			Narrative string   `json:"narrative"`
			Tracker   string   `json:"tracker"`
			EndCause  string   `json:"end_cause"`
			Heading   *int     `json:"heading_deg"`
			SpeedKmd  *float64 `json:"speed_kmd"`
		}
		if json.Unmarshal([]byte(propsJSON), &p) != nil {
			continue
		}
		if p.Tier == "" {
			p.Tier = "unmeasured"
		}
		tiers[p.Tier]++
		l := vanLead{ID: fid, Start: sd, End: ed, Days: p.Days, Fires: p.Fires, Km: p.Km, Direction: p.Direction,
			LeadStart: p.LeadStart, LeadBasis: p.LeadBasis, AheadKm: p.AheadKm, AheadDays: p.AheadDays, Tier: p.Tier,
			Place: p.Place, PlaceKm: p.PlaceKm, Narrative: p.Narrative,
			Tracker: p.Tracker, EndCause: p.EndCause, Heading: p.Heading, SpeedKmd: p.SpeedKmd}
		if pts := datedPoints(geojson, "", sd, ed); len(pts) > 0 {
			l.Lon, l.Lat = pts[0][0], pts[0][1]
		}
		all = append(all, l)
		marks = append(marks, vanMark{ID: fid, Day: sd, Lead: p.LeadStart, Tier: p.Tier})
	}
	sort.SliceStable(all, func(i, j int) bool {
		if all[i].AheadKm != all[j].AheadKm {
			return all[i].AheadKm > all[j].AheadKm
		}
		li, lj := 0, 0
		if all[i].LeadStart != nil {
			li = *all[i].LeadStart
		}
		if all[j].LeadStart != nil {
			lj = *all[j].LeadStart
		}
		return li > lj
	})
	if len(all) > n {
		all = all[:n]
	}
	if all == nil {
		all = []vanLead{}
	}
	sort.Slice(marks, func(i, j int) bool { return marks[i].Day < marks[j].Day })
	return all, tiers, marks
}

// seasonWords is the season's one-sentence summary. Every number in it is
// derived from the same values the JSON carries beside it; nothing is typed.
func seasonWords(season string, complete bool, first, median, last string, pct, usual *float64,
	nVan int, nVanWin interface{}, tiers map[string]int) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Fire season %s", season)
	// "half" and "the last of it" are medians over the cells the front has
	// reached; for a season in progress that is a share of itself, so only
	// a complete season may say them.
	if first != "" && complete {
		fmt.Fprintf(&b, ": the front first arrived %s, had reached half the area by %s and the last of it by %s", first, median, last)
	} else if first != "" {
		fmt.Fprintf(&b, ": the front first arrived %s", first)
	}
	if pct != nil {
		if *pct >= 99.5 {
			b.WriteString("; at the window's end the front was complete")
		} else if *pct <= 0 {
			b.WriteString("; at the window's end the front had not yet arrived")
		} else {
			fmt.Fprintf(&b, "; at the window's end the front had reached %.0f%% of the area", *pct)
			if usual != nil && math.Abs(*usual) >= 1 {
				if *usual < 0 {
					fmt.Fprintf(&b, ", %.0f days earlier than usual", -*usual)
				} else {
					fmt.Fprintf(&b, ", %.0f days later than usual", *usual)
				}
			}
		}
	}
	if !complete {
		b.WriteString(" (season in progress)")
	}
	b.WriteString(". ")
	n := nVan
	scope := "this season"
	if v, ok := nVanWin.(int); ok {
		n = v
		scope = "the window"
	}
	if n == 0 {
		fmt.Fprintf(&b, "No fire chain in %s began 10 or more days ahead of the front.", scope)
	} else {
		fmt.Fprintf(&b, "%d fire chain%s in %s began 10–60 days ahead of the front (vanguard) — the one population whose day-to-day order is measurably better than chance",
			n, plural(n), scope)
		if len(tiers) > 0 {
			meas := tiers["supported"] + tiers["weak"]
			unm := tiers["unmeasured"]
			if unm == n {
				b.WriteString("; their day order has not yet been scored (unmeasured)")
			} else {
				fmt.Fprintf(&b, "; %d of them with a confirmed day order (supported %d, weak %d)", meas, tiers["supported"], tiers["weak"])
				if unm > 0 {
					fmt.Fprintf(&b, ", %d unmeasured", unm)
				}
			}
		}
		b.WriteString(".")
	}
	return b.String()
}

func plural(n int) string {
	if n == 1 {
		return ""
	}
	return "s"
}

// frontProgress reads the packed int16 day-of-season grids (scripts/
// fire_front.py pack(): -1 = no front) and answers, for day-of-season d:
// the share of front-bearing cells reached by d, and the median (front −
// usual) over those cells where both are known. nil = nothing to measure.
// The denominator is the season's front-bearing cells — but a season IN
// PROGRESS only bears a front where it has already arrived, so measured
// against itself it is "100 % complete" on its own latest day (XSA 2026/27
// read 100 % six weeks in, with the burning months still ahead). An
// incomplete season is therefore measured against the union of its own
// front cells and the cells the USUAL front reaches (a no-op must not read
// as an answer — AGENTS.md invariant 1).
func frontProgress(front, usual []byte, nx, ny, d int, complete bool) (*float64, *float64) {
	n := nx * ny
	if n <= 0 || len(front) < 2*n {
		return nil, nil
	}
	haveUsual := len(usual) >= 2*n
	total, reached := 0, 0
	offs := make([]int, 0, 1024)
	for i := 0; i < n; i++ {
		f := int(int16(uint16(front[2*i]) | uint16(front[2*i+1])<<8))
		if f < 0 {
			if !complete && haveUsual && int16(uint16(usual[2*i])|uint16(usual[2*i+1])<<8) >= 0 {
				total++ // usually reached, not yet this season
			}
			continue
		}
		total++
		if f <= d {
			reached++
			if haveUsual {
				u := int(int16(uint16(usual[2*i]) | uint16(usual[2*i+1])<<8))
				if u >= 0 {
					offs = append(offs, f-u)
				}
			}
		}
	}
	if total == 0 {
		return nil, nil
	}
	pct := 100 * float64(reached) / float64(total)
	var off *float64
	if len(offs) >= 20 { // a handful of cells is not a season's tendency
		sort.Ints(offs)
		m := float64(offs[len(offs)/2])
		if len(offs)%2 == 0 {
			m = (float64(offs[len(offs)/2-1]) + m) / 2
		}
		off = &m
	}
	return &pct, off
}

func orNull(n sql.NullString) string {
	if n.Valid && n.String != "" {
		return n.String
	}
	return "null"
}

// HandleAPIFireVanguard answers "which fire chains in this rectangle began
// ahead of the season front", in the animator's wire format.
//
// It is its own endpoint, not a ?vanguard=1 flag on the animator's, because
// the population is two orders of magnitude smaller (XSA: 749 of 14,729
// groups) and the point is to draw ALL of them; the spread collector that
// keeps a continental animation honest would here throw away exactly the
// chains a director wants to see. `truncated` is still reported.
func (s *Server) HandleAPIFireVanguard(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	bboxParts := strings.Split(q.Get("bbox"), ",")
	if len(bboxParts) != 4 {
		http.Error(w, `{"error":"bbox required"}`, http.StatusBadRequest)
		return
	}
	var bbox [4]float64
	for i, p := range bboxParts {
		v, err := strconv.ParseFloat(strings.TrimSpace(p), 64)
		if err != nil {
			http.Error(w, `{"error":"invalid bbox"}`, http.StatusBadRequest)
			return
		}
		bbox[i] = v
	}
	from, to := q.Get("from"), q.Get("to")
	if from == "" {
		from = "2012-01-01"
	}
	if to == "" {
		to = time.Now().UTC().Format("2006-01-02")
	}
	limit := 6000
	if lv, err := strconv.Atoi(q.Get("limit")); err == nil && lv > 0 && lv <= 40000 {
		limit = lv
	}
	fromT, _ := parseISODate(from)
	scanFrom := from
	if !fromT.IsZero() {
		scanFrom = fromT.AddDate(0, 0, -trajMaxSpanDays).Format("2006-01-02")
	}
	var total int
	s.DB.QueryRowContext(r.Context(), `
		SELECT COUNT(*) FROM feature_geometries INDEXED BY idx_fg_vanguard
		WHERE`+vanguardRowsSQL+`
		  AND bbox_maxx >= ? AND bbox_minx <= ? AND bbox_maxy >= ? AND bbox_miny <= ?
		  AND start_date >= ? AND start_date <= ?
		  AND (end_date IS NULL OR end_date >= ?)`+
		aoiScopeSQL("park_id", s.aoiScopeParam(r)),
		bbox[0], bbox[2], bbox[1], bbox[3], scanFrom, to, from).Scan(&total)
	rows, err := s.DB.QueryContext(r.Context(), `
		SELECT feature_id, park_id, geojson, traj_days, properties_json,
		       COALESCE(start_date,''), COALESCE(end_date,'')
		FROM feature_geometries INDEXED BY idx_fg_vanguard
		WHERE`+vanguardRowsSQL+`
		  AND bbox_maxx >= ? AND bbox_minx <= ? AND bbox_maxy >= ? AND bbox_miny <= ?
		  AND start_date >= ? AND start_date <= ?
		  AND (end_date IS NULL OR end_date >= ?)`+
		aoiScopeSQL("park_id", s.aoiScopeParam(r))+`
		ORDER BY COALESCE(lead_start,0) DESC LIMIT ?`,
		bbox[0], bbox[2], bbox[1], bbox[3], scanFrom, to, from, limit)
	if err != nil {
		internalError(w, "query failed", err)
		return
	}
	defer rows.Close()

	type vanGroup struct {
		ID        string       `json:"id"`
		Park      string       `json:"park"`
		Type      string       `json:"type,omitempty"`
		Km        float64      `json:"km,omitempty"`
		Kmd       float64      `json:"kmd,omitempty"`
		T0        string       `json:"t0"`
		Pts       [][3]float64 `json:"pts"`
		Leads     []*int       `json:"leads"`
		LeadStart *int         `json:"lead_start"`
		LeadBasis string       `json:"lead_basis,omitempty"`
		Tier      string       `json:"tier,omitempty"` // evidence_tier: one word for the day-order evidence
		Bits      *float64     `json:"bits,omitempty"` // evidence_bits: log2 LR vs day-shuffled null — the width the map draws (FireSeason.evidenceMul)
		AheadKm   float64      `json:"ahead_km,omitempty"`
		AheadDays int          `json:"ahead_days,omitempty"`
		Season    string       `json:"season,omitempty"`
		Fires     int          `json:"fires,omitempty"`
		Days      int          `json:"days,omitempty"`
		Start     string       `json:"start,omitempty"`
		End       string       `json:"end,omitempty"`
		// Kalman seed-ahead chains only (scripts/fire_vanguard_kf.py):
		// which tracker drew this line, why it ended ('ongoing' = last seen
		// within the gap budget of the newest data — still moving), and the
		// filter's heading/speed at the end.
		Tracker  string   `json:"tracker"`
		EndCause string   `json:"end_cause,omitempty"`
		Heading  *int     `json:"heading_deg,omitempty"`
		SpeedKmd *float64 `json:"speed_kmd,omitempty"`
		SeedLead *float64 `json:"seed_lead,omitempty"`
	}
	out := make([]vanGroup, 0, 256)
	trackers := map[string]int{}
	for rows.Next() {
		var fid, park, geojson, propsJSON, sd, ed string
		var days sql.NullString
		if err := rows.Scan(&fid, &park, &geojson, &days, &propsJSON, &sd, &ed); err != nil {
			continue
		}
		pts := datedPoints(geojson, days.String, sd, ed)
		if len(pts) == 0 {
			continue
		}
		g := vanGroup{ID: fid, Park: park, Pts: pts, Start: sd, End: ed, T0: sd}
		var props struct {
			GroupType string   `json:"group_type"`
			Km        float64  `json:"distance_km"`
			Kmd       float64  `json:"avg_speed_km_day"`
			Fires     int      `json:"fires_total"`
			Days      int      `json:"days"`
			Leads     []*int   `json:"leads"`
			LeadStart *int     `json:"lead_start"`
			LeadBasis string   `json:"lead_basis"`
			AheadKm   float64  `json:"ahead_km"`
			AheadDays int      `json:"ahead_days"`
			Season    string   `json:"fire_season"`
			Tier      string   `json:"evidence_tier"`
			Bits      *float64 `json:"evidence_bits"`
			Tracker   string   `json:"tracker"`
			EndCause  string   `json:"end_cause"`
			Heading   *int     `json:"heading_deg"`
			SpeedKmd  *float64 `json:"speed_kmd"`
			SeedLead  *float64 `json:"seed_lead"`
		}
		g.Tracker = "groups"
		if json.Unmarshal([]byte(propsJSON), &props) == nil {
			g.Type, g.Km, g.Kmd, g.Fires, g.Days = props.GroupType, props.Km, props.Kmd, props.Fires, props.Days
			g.Leads, g.LeadStart, g.LeadBasis, g.Tier, g.Bits = props.Leads, props.LeadStart, props.LeadBasis, props.Tier, props.Bits
			g.AheadKm, g.AheadDays, g.Season = props.AheadKm, props.AheadDays, props.Season
			if props.Tracker == "kf" {
				g.Tracker, g.EndCause, g.Heading, g.SpeedKmd, g.SeedLead = "kf", props.EndCause, props.Heading, props.SpeedKmd, props.SeedLead
			}
		}
		trackers[g.Tracker]++
		out = append(out, g)
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, max-age=600")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"from": from, "to": to, "limit": limit,
		"count": len(out), "total": total, "truncated": total > len(out),
		"lead_days": 10, "lead_max_days": 60, // VANGUARD_LEAD_DAYS/_MAX in scripts/fire_front.py — the flag is written there
		"trackers": trackers, // how many of the chains each tracker drew: 'kf' (Kalman seed-ahead) / 'groups' (plain trajectories that began ahead)
		"groups":   out,
	})
}

// fireSeasonAreaAt picks the area whose season-front grid contains a point.
func (s *Server) fireSeasonAreaAt(r *http.Request, lon, lat float64) string {
	rows, err := s.DB.QueryContext(r.Context(), `
		SELECT area_id, MIN(res * nx * res * ny) AS cells
		FROM fire_season_front
		WHERE x0 <= ? AND x0 + res * nx >= ? AND y0 <= ? AND y0 + res * ny >= ?
		GROUP BY area_id ORDER BY cells ASC`, lon, lon, lat, lat)
	if err != nil {
		return ""
	}
	defer rows.Close()
	park := ""
	pid := s.RequestPrincipalID(r)
	for rows.Next() {
		var id string
		var cells float64
		if rows.Scan(&id, &cells) != nil {
			continue
		}
		if IsAOIID(id) {
			if _, err := s.GetAOI(id, pid, false); err == nil {
				return id
			}
			continue
		}
		if park == "" {
			park = id
		}
	}
	return park
}

// frontCurve is the season as one line: for every `step` days of season, the
// share of front-bearing cells the front has reached — and the same for the
// usual (median of past seasons) grid, so the reader sees this season's
// S-curve beside the usual one. `usual` is nil when no past season exists.
// nil, nil when nothing carries a front. The popup's season sparkline is
// drawn from this; nothing there is computed client-side.
// Same denominator rule as frontProgress: an incomplete season's curve is
// a share of the cells the front usually reaches, not of itself.
func frontCurve(front, usual []byte, nx, ny, step int, complete bool) (fc, uc []float64) {
	n := nx * ny
	if n <= 0 || len(front) < 2*n || step <= 0 {
		return nil, nil
	}
	const days = 366
	hf := make([]int, days+1)
	hu := make([]int, days+1)
	tf, tu := 0, 0
	haveUsual := len(usual) >= 2*n
	for i := 0; i < n; i++ {
		f := int(int16(uint16(front[2*i]) | uint16(front[2*i+1])<<8))
		if f >= 0 {
			if f > days {
				f = days
			}
			hf[f]++
			tf++
		}
		if haveUsual {
			u := int(int16(uint16(usual[2*i]) | uint16(usual[2*i+1])<<8))
			if u >= 0 {
				if u > days {
					u = days
				}
				hu[u]++
				tu++
				if f < 0 && !complete {
					tf++ // usually reached, not yet this season: in the denominator, never in the histogram
				}
			}
		}
	}
	if tf == 0 {
		return nil, nil
	}
	cum := func(h []int, total int) []float64 {
		if total == 0 {
			return nil
		}
		out := make([]float64, 0, days/step+2)
		acc := 0
		for d := 0; d <= days; d++ {
			acc += h[d]
			if d%step == 0 || d == days {
				out = append(out, math.Round(1000*float64(acc)/float64(total))/10)
			}
		}
		return out
	}
	return cum(hf, tf), cum(hu, tu)
}

// fireSeasonBBox — GET /api/fire-season?bbox=w,s,e,n[&at=][&lines=all|15|30][&limit=][&exclude=]
//
// The unfocused map used to draw ONE area's front: the park under the view
// centre. Two parks side by side then showed contours in one and none in the
// other, which reads as "no data there", not as "not the one at the centre"
// (report 2026-09-17: "contours should show consistently across parks").
// This answers for every PARK whose front grid intersects the bbox, each at
// the season the caller's `at` falls in (else its latest) — the same rule as
// the single-area path, so the picture agrees with the stats row.
//
// Parks only: an AOI's grid spans the parks inside it and would draw a second
// front over each; an AOI is drawn when it is the focus (?area=).
//
// `lines` thins the contours for an overview (15 = the labelled 15-day
// lines, 30 = the 30-day lines): a continent of 5-day lines is ~9 MB and a
// thicket. `limit` caps the areas (nearest the bbox centre first) and the
// answer says so (`truncated`, invariant 8). `exclude` names the area the
// caller already holds (the reference), so it is not shipped twice.
func (s *Server) fireSeasonBBox(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	parts := strings.Split(q.Get("bbox"), ",")
	if len(parts) != 4 {
		http.Error(w, `{"error":"bbox must be w,s,e,n"}`, http.StatusBadRequest)
		return
	}
	var bb [4]float64
	for i, p := range parts {
		v, err := strconv.ParseFloat(strings.TrimSpace(p), 64)
		if err != nil {
			http.Error(w, `{"error":"bbox must be w,s,e,n"}`, http.StatusBadRequest)
			return
		}
		bb[i] = v
	}
	lines := q.Get("lines")
	if lines != "15" && lines != "30" {
		lines = "all"
	}
	limit := 60
	if n, err := strconv.Atoi(q.Get("limit")); err == nil && n > 0 && n <= 200 {
		limit = n
	}
	at := q.Get("at")
	exclude := q.Get("exclude")
	early := q.Get("early") != ""
	wantSpeed := q.Get("speed") != "" && q.Get("summary") == ""
	// The picture itself is ONE surface over the box (srv/fire_season_mosaic.go),
	// the reference included — per-area `contours` below are empty when it
	// is served, the areas list stays as the inventory (and the early-burn
	// ground rides per area as before). Memoised per quantised ask.
	var mosaic json.RawMessage
	if q.Get("summary") == "" && q.Get("mosaic") != "0" {
		mkey := fmt.Sprintf("%v|%s|%s|%s|%v", bb, at, lines, exclude, wantSpeed)
		if e := mosaicCache.get(mkey); e != nil {
			mosaic = e.body
		} else if m := fireSeasonMosaic(s.mosaicGrids(bb, at, exclude), bb, exclude, lines, wantSpeed); m != nil {
			mosaic, _ = json.Marshal(m)
			mosaicCache.put(mkey, mosaic, "")
		}
	}

	type row struct {
		Area, Season, Start, End string
		Complete                 bool
		Contours                 string
		dist                     float64
	}
	type seasonRef struct {
		Label string `json:"label"`
		Start string `json:"start"`
		End   string `json:"end"`
	}
	seasonsOf := map[string][]seasonRef{}
	rows, err := s.DB.QueryContext(r.Context(), `
		SELECT area_id, season, season_start, season_end, complete, COALESCE(contours_json,''),
		       x0 + res * nx / 2.0, y0 + res * ny / 2.0
		FROM fire_season_front
		WHERE x0 <= ? AND x0 + res * nx >= ? AND y0 <= ? AND y0 + res * ny >= ?
		ORDER BY area_id, season_start`, bb[2], bb[0], bb[3], bb[1])
	if err != nil {
		internalError(w, "query failed", err)
		return
	}
	cx, cy := (bb[0]+bb[2])/2, (bb[1]+bb[3])/2
	// Per area: the season `at` falls in, else the latest.
	pick := map[string]*row{}
	var order []string
	for rows.Next() {
		var rw row
		var complete int
		var gx, gy float64
		if rows.Scan(&rw.Area, &rw.Season, &rw.Start, &rw.End, &complete, &rw.Contours, &gx, &gy) != nil {
			continue
		}
		if rw.Area == exclude || IsAOIID(rw.Area) {
			continue
		}
		rw.Complete = complete == 1
		rw.dist = math.Hypot(gx-cx, gy-cy)
		seasonsOf[rw.Area] = append(seasonsOf[rw.Area], seasonRef{rw.Season, rw.Start, rw.End})
		cur, seen := pick[rw.Area]
		if !seen {
			order = append(order, rw.Area)
			pick[rw.Area] = &rw
			continue
		}
		// rows arrive by season_start: a later row replaces the pick unless
		// the pick already holds `at`.
		if at != "" && cur.Start <= at && at <= cur.End {
			continue
		}
		pick[rw.Area] = &rw
	}
	rows.Close()
	total := len(order)
	sort.Slice(order, func(i, j int) bool { return pick[order[i]].dist < pick[order[j]].dist })
	if len(order) > limit {
		order = order[:limit]
	}
	type feat struct {
		Type       string          `json:"type"`
		Geometry   json.RawMessage `json:"geometry"`
		Properties struct {
			Dos   int    `json:"dos"`
			Date  string `json:"date"`
			Label bool   `json:"label"`
			Text  string `json:"text"`
		} `json:"properties"`
	}
	type areaOut struct {
		Area        string      `json:"area"`
		Season      string      `json:"season"`
		SeasonStart string      `json:"season_start"`
		SeasonEnd   string      `json:"season_end"`
		Complete    bool        `json:"complete"`
		Seasons     []seasonRef `json:"seasons"`
		Contours    interface{} `json:"contours"`
		// early=1: the park's early-burn ground (srv/fire_early_ground.go)
		// with this season's first-burn column, as the single path's
		// `early_ground` — so the squares can be drawn for every park in
		// view, not only the one under the centre.
		Early interface{} `json:"early_ground,omitempty"`
		// speed=1: the isochrones split by speed class (srv/fire_season_lines.go),
		// thinned like `contours`.
		Speed      interface{} `json:"speed_contours,omitempty"`
		SpeedStats interface{} `json:"speed_stats,omitempty"`
	}
	out := make([]areaOut, 0, len(order))
	for _, id := range order {
		rw := pick[id]
		ao := areaOut{Area: rw.Area, Season: rw.Season, SeasonStart: rw.Start, SeasonEnd: rw.End, Complete: rw.Complete, Seasons: seasonsOf[id]}
		if early {
			ao.Early = s.earlyGround(rw.Area, rw.Season).wire(rw.Season)
		}
		if q.Get("summary") != "" || mosaic != nil {
			ao.Contours = []feat{} // early-burn ground alone, or the mosaic draws
		} else if lines == "all" || rw.Contours == "" {
			if rw.Contours == "" {
				ao.Contours = []feat{}
			} else {
				ao.Contours = json.RawMessage(rw.Contours)
			}
		} else {
			var fs []feat
			if json.Unmarshal([]byte(rw.Contours), &fs) != nil {
				fs = nil
			}
			keep := make([]feat, 0, len(fs)/3+1)
			for _, f := range fs {
				if !f.Properties.Label {
					continue
				}
				if lines == "30" && f.Properties.Dos%30 != 0 {
					continue
				}
				keep = append(keep, f)
			}
			ao.Contours = keep
		}
		if wantSpeed && rw.Contours != "" && mosaic == nil {
			var (
				fnx, fny       int
				fres, fx0, fy0 float64
				front          []byte
			)
			if s.DB.QueryRowContext(r.Context(), `
				SELECT nx, ny, res, x0, y0, front FROM fire_season_front WHERE area_id = ? AND season = ?`,
				rw.Area, rw.Season).Scan(&fnx, &fny, &fres, &fx0, &fy0, &front) != nil {
				front = nil
			}
			sc, sst := speedContours(rw.Contours, front, fnx, fny, fres, fx0, fy0)
			if sc != nil {
				keep := make([]speedFeat, 0, len(sc))
				for _, f := range sc {
					if lines != "all" && (!f.Properties.Label || (lines == "30" && f.Properties.Dos%30 != 0)) {
						continue
					}
					keep = append(keep, f)
				}
				ao.Speed, ao.SpeedStats = keep, sst
			}
		}
		out = append(out, ao)
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, max-age=600")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"mode":      "bbox",
		"at":        at,
		"lines":     lines,
		"areas":     out,
		"mosaic":    mosaic,
		"count":     len(out),
		"total":     total,
		"truncated": total > len(out),
	})
}
