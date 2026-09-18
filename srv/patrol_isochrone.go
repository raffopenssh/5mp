package srv

// Patrol isochrones — the season front's counterpart for the rangers: the
// day patrol PRESENCE had built up at a place, drawn as dated lines the way
// the fire front is, so the two can be read against each other across a
// year ("the front bent around the ground the teams had been on since
// June").
//
//	GET /api/patrol-isochrones?area=<park|aoi>|lon=&lat=&from=&to=[&mode=all|ground|air][&clip=1]
//	    → {"area","season","season_start","from","to","seasons":[{label,start,end}…],
//	       "grid":{x0,y0,res,nx,ny}, "cells", "patrol_days", "threshold", "kernel_cells",
//	       "contours":[Feature{dos,date,label,text}…],     dos = days since `from`
//	       "arrival":[[ix,iy,dos]…],                        per reached cell
//	       "pressure":{unit,levels,max,contours:[Feature{level,label,text}…]},
//	       "association":{…}, "status"}
//
// `pressure` is the same field the isochrones are cut from, read the other
// way: instead of WHEN a cell's presence crossed one level, HOW MUCH
// presence a cell holds at the window's end — kernel-weighted patrol-days
// within ~5 km — contoured at 1, 2, 5, 10, 20, 50 … (a log ladder: effort
// concentrates near stations, and equal steps would draw one knot and
// nothing else). The isopleth at `threshold` is therefore the isochrones'
// outermost line, which is the check that both describe one thing.
//
// Definition (one writer, this file). Grid = the area's season-front grid
// (2.5 km, scripts/fire_front.py), so a patrol cell and a fire cell are one
// cell. A PATROL-DAY is a cell with a patrol of one movement type in it on
// a day (track_points), weighted by that type (patrolModeWeight).
// Each patrol-day adds its weight × a max-normalised gaussian (σ = 2 cells
// ≈ 5 km, r = 4) around its cell — a foot day counts 1 in its own cell and
// ~0.9 next door, ~0.6 two cells out — and a
// cell is REACHED on the first day its accumulated presence since `from` is
// ≥ patrolThreshold (3 patrol-days within ~5 km). The reached-day field is
// smoothed (normalised convolution σ 2.5, like the front's σ 3) and
// contoured every 5 days, labelled every 15, in `from`'s calendar — the
// front's own contour recipe, so the two families of lines are the same
// object. Tenant-scoped through PatrolEnvs (docs/agents/auth.md); a guest
// link without the patrol scope gets the tenant that owns nothing and an
// honest "no patrol data" status.
//
// `association` is the one number that joins the two: over the reference
// season's front-bearing cells reached by `to`, median(front − usual) in
// cells patrols had reached BEFORE the front arrived vs the rest. It is a
// description at the front's own ±60 km scale, not an effect (invariant 12):
// the words say "arrived later than usual", never "held off".

import (
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	patrolThreshold   = 3.0 // patrol-days of presence (kernel-weighted) that make a cell "reached"
	patrolKernelR     = 4   // cells (σ = 2 cells ≈ 5 km: the reach of a team on the ground that day)
	patrolKernelSigma = 2.0
	patrolMinVerts    = 6  // a ring shorter than this after thinning is a speck, not a line
	patrolContourStep = 5  // days
	patrolLabelStep   = 15 // days
)

type patrolCellDay struct {
	ix, iy, day int
	w           float64 // 1 for a ground visit; patrolAirWeight for an overflight when mode=all
}

// patrolModeWeight: how much presence one cell-day of each movement type
// is worth, in patrol-days. A foot team in a cell for a day is the presence
// that meets a fire; a vehicle passes through; a helicopter can land; a
// fixed-wing crossing at 150 km/h sees but cannot act. Named, printed in
// the legend and the wire (`weights`), applied automatically — there is no
// mode picker: the reader gets one line family, and the tip says what it
// is made of. Not a tuning target (invariant 12): changing a weight must
// come with the association numbers before and after.
var patrolModeWeight = map[string]float64{
	"foot": 1.0, "vehicle": 0.7, "boat": 0.7, "rotor_wing": 0.4, "aircraft": 0.2, "fixed_wing": 0.2,
}

func isAirMode(mt string) bool { return mt == "aircraft" || mt == "fixed_wing" || mt == "rotor_wing" }

func modeWeight(mt string) float64 {
	if w, ok := patrolModeWeight[mt]; ok {
		return w
	}
	return 0.7 // an unlabelled track: treated as a vehicle
}

// patrolSeasonRow is one fire_season_front row: the grid the patrol field
// is measured on and the season it names.
type patrolSeasonRow struct {
	area, season, start, end string
	nx, ny                   int
	res, x0, y0              float64
	front, usual             []byte
}

type patrolSeasonOut struct {
	Label string `json:"label"`
	Start string `json:"start"`
	End   string `json:"end"`
}

// patrolRawVisit is one (cell, day, movement type) of track points on the
// GLOBAL 0.025° lattice (gx = floor(lon/res), gy = floor(lat/res)). Every
// season-front grid is aligned to that lattice (x0/res and y0/res are
// integers — asserted by scripts/fire_front.py's grid rule and checked here
// with patrolGridAligned), so one query over a viewport serves every park
// in it: an area's cell is (gx − x0/res, gy − y0/res).
type patrolRawVisit struct {
	gx, gy int
	day    string
	mt     string
}

func patrolGridAligned(x0, y0, res float64) bool {
	ax, ay := x0/res, y0/res
	return math.Abs(ax-math.Round(ax)) < 1e-6 && math.Abs(ay-math.Round(ay)) < 1e-6
}

func patrolModeSQL(mode string) string {
	switch mode {
	case "air":
		return "AND t.movement_type IN ('aircraft','fixed_wing','rotor_wing')"
	case "ground":
		return "AND t.movement_type NOT IN ('aircraft','fixed_wing','rotor_wing')"
	}
	return ""
}

// patrolRawVisits: presence in [from, to] over a lon/lat box, from the
// track points themselves (they carry the movement type; subcell_visits
// does not): one record per (cell, day, mode). The timestamp is stored as
// '2026-06-26 04:23:57 +0000 UTC'; its first 10 characters are the day.
func (s *Server) patrolRawVisits(r *http.Request, res, x0, y0, x1, y1 float64, from, to, mode string) ([]patrolRawVisit, error) {
	rows, err := s.DB.QueryContext(r.Context(), `
		SELECT CAST(floor(t.lon / ?) AS INTEGER), CAST(floor(t.lat / ?) AS INTEGER),
		       substr(t.timestamp, 1, 10), COALESCE(t.movement_type, '')
		FROM track_points t
		WHERE `+PatrolEnvsSQL("t.env")+`
		  AND t.lat >= ? AND t.lat < ? AND t.lon >= ? AND t.lon < ?
		  AND substr(t.timestamp, 1, 10) BETWEEN ? AND ? `+patrolModeSQL(mode)+`
		GROUP BY 1, 2, 3, 4`,
		res, res, s.PatrolEnvsJSON(r), y0, y1, x0, x1, from, to)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []patrolRawVisit
	for rows.Next() {
		var v patrolRawVisit
		if rows.Scan(&v.gx, &v.gy, &v.day, &v.mt) == nil {
			out = append(out, v)
		}
	}
	return out, nil
}

// patrolAreaAnswer computes one area's isochrones + pressure from raw
// visits (any lattice cell outside the grid or the window is dropped). It
// is the whole of the single-area wire under the top level; the bbox path
// wraps one per park.
func patrolAreaAnswer(pick *patrolSeasonRow, seasons []patrolSeasonOut, raw []patrolRawVisit, from, to, mode, lines string, fromT, toT time.Time, nDays int) map[string]interface{} {
	nx, ny, res, x0, y0 := pick.nx, pick.ny, pick.res, pick.x0, pick.y0
	offX, offY := int(math.Round(x0/res)), int(math.Round(y0/res))
	var visits []patrolCellDay
	modeDays := map[string]int{}
	for _, rv := range raw {
		ix, iy := rv.gx-offX, rv.gy-offY
		if ix < 0 || iy < 0 || ix >= nx || iy >= ny {
			continue
		}
		dT, ok := parseISODate(rv.day)
		if !ok {
			continue
		}
		d := int(dT.Sub(fromT).Hours() / 24)
		if d < 0 || d >= nDays {
			continue
		}
		mt := rv.mt
		w := modeWeight(mt)
		if mt == "" {
			mt = "unlabelled"
		}
		modeDays[mt]++
		visits = append(visits, patrolCellDay{ix, iy, d, w})
	}
	base := map[string]interface{}{
		"area": pick.area, "season": pick.season, "season_start": pick.start, "season_end": pick.end, "from": from, "to": to, "seasons": seasons,
		"grid":      map[string]interface{}{"x0": x0, "y0": y0, "res": res, "nx": nx, "ny": ny},
		"threshold": patrolThreshold, "kernel_cells": patrolKernelSigma, "reach_km": 5, "mode": mode, "weights": patrolModeWeight,
		"unit":        "patrol-days (a 2.5 km cell with a patrol in it on a day, weighted by movement type — see weights)",
		"patrol_days": len(visits), "by_mode": modeDays,
	}
	if len(visits) == 0 {
		base["cells"] = 0
		base["status"] = "no patrol data in this window here"
		base["contours"] = []interface{}{}
		base["pressure"] = patrolPressure(make([]float64, nx*ny), nx, ny, x0, y0, res)
		return base
	}
	sort.Slice(visits, func(i, j int) bool { return visits[i].day < visits[j].day })

	// Accumulate presence day by day; a cell is reached the first day the
	// kernel-weighted sum crosses the threshold.
	acc := make([]float64, nx*ny)
	arrival := make([]float64, nx*ny)
	for i := range arrival {
		arrival[i] = math.NaN()
	}
	var kern [2*patrolKernelR + 1][2*patrolKernelR + 1]float64
	for dy := -patrolKernelR; dy <= patrolKernelR; dy++ {
		for dx := -patrolKernelR; dx <= patrolKernelR; dx++ {
			kern[dy+patrolKernelR][dx+patrolKernelR] = math.Exp(-float64(dx*dx+dy*dy) / (2 * patrolKernelSigma * patrolKernelSigma))
		}
	}
	reached := 0
	for _, v := range visits {
		for dy := -patrolKernelR; dy <= patrolKernelR; dy++ {
			yy := v.iy + dy
			if yy < 0 || yy >= ny {
				continue
			}
			for dx := -patrolKernelR; dx <= patrolKernelR; dx++ {
				xx := v.ix + dx
				if xx < 0 || xx >= nx {
					continue
				}
				i := yy*nx + xx
				acc[i] += v.w * kern[dy+patrolKernelR][dx+patrolKernelR]
				if math.IsNaN(arrival[i]) && acc[i] >= patrolThreshold {
					arrival[i] = float64(v.day)
					reached++
				}
			}
		}
	}
	base["cells"] = reached
	base["pressure"] = patrolPressure(acc, nx, ny, x0, y0, res)
	// The visits themselves, day-sorted, so the client can rebuild the
	// presence field at any playhead (kernel params ride beside them) and
	// draw the pressure lines growing as the animator runs.
	vis := make([][4]float64, 0, len(visits))
	for _, v := range visits {
		vis = append(vis, [4]float64{float64(v.ix), float64(v.iy), float64(v.day), v.w})
	}
	base["visits"] = vis
	base["kernel_r"] = patrolKernelR
	if reached == 0 {
		base["status"] = fmt.Sprintf("patrols present (%d patrol-days) but nowhere reached %g within ~5 km", len(visits), patrolThreshold)
		base["contours"] = []interface{}{}
		return base
	}
	arr := make([][3]int, 0, reached)
	mask := make([]bool, nx*ny)
	for i, a := range arrival {
		if !math.IsNaN(a) {
			mask[i] = true
			arr = append(arr, [3]int{i % nx, i / nx, int(a)})
		}
	}
	sm := gaussianNC(arrival, mask, nx, ny, 2.5)
	field := make([]float64, nx*ny)
	lo, hi := math.Inf(1), math.Inf(-1)
	for i := range field {
		if mask[i] {
			field[i] = sm[i]
			lo, hi = math.Min(lo, sm[i]), math.Max(hi, sm[i])
		} else {
			field[i] = math.NaN()
		}
	}
	feats := []map[string]interface{}{}
	l0 := int(math.Floor(lo/patrolContourStep)) * patrolContourStep
	l1 := int(math.Ceil(hi/patrolContourStep)) * patrolContourStep
	for lvl := l0; lvl <= l1; lvl += patrolContourStep {
		label := lvl%patrolLabelStep == 0
		// `lines` thins for an overview the same way /api/fire-season does
		// (15 = labelled 15-day lines, 30 = 30-day lines), so a viewport of
		// parks is drawn in one key.
		if (lines == "15" && !label) || (lines == "30" && (!label || lvl%30 != 0)) {
			continue
		}
		segs := marchingSquares(field, nx, ny, x0, y0, res, float64(lvl))
		if len(segs) == 0 {
			continue
		}
		d := fromT.AddDate(0, 0, lvl)
		feats = append(feats, map[string]interface{}{
			"type":     "Feature",
			"geometry": map[string]interface{}{"type": "MultiLineString", "coordinates": segs},
			"properties": map[string]interface{}{
				"dos": lvl, "date": d.Format("2006-01-02"), "label": label,
				"text": fmt.Sprintf("%d %s", d.Day(), d.Format("Jan")),
			},
		})
	}
	base["contours"] = feats
	base["arrival"] = arr
	base["association"] = patrolFrontAssociation(pick.front, pick.usual, arrival, nx, ny, pick.start, fromT, toT)
	base["status"] = "ok"
	return base
}

// patrolPickSeason: the reference season among an area's rows — the one
// `to` falls in (else the latest begun by `to`) — the same rule as
// /api/fire-season, so both overlays name one.
func patrolPickSeason(rows []patrolSeasonRow, to string) (*patrolSeasonRow, []patrolSeasonOut) {
	var pick *patrolSeasonRow
	var seasons []patrolSeasonOut
	for i := range rows {
		rw := &rows[i]
		seasons = append(seasons, patrolSeasonOut{rw.season, rw.start, rw.end})
		if pick == nil || rw.start <= to {
			pick = rw
		}
	}
	return pick, seasons
}

// patrolWindow resolves from/to/clip against the picked season: `from`
// defaults to the season's start; clip=1 pulls it up to the season's
// start (presence is counted per season — the front's rule, and what
// keeps a 2020–2026 slider under the 800 d cap).
func patrolWindow(pick *patrolSeasonRow, from, to string, clip bool) (string, time.Time, time.Time, int, bool) {
	if from == "" || from > to {
		from = pick.start
	}
	if clip && from < pick.start {
		from = pick.start
	}
	fromT, ok1 := parseISODate(from)
	toT, ok2 := parseISODate(to)
	if !ok1 || !ok2 {
		return from, fromT, toT, 0, false
	}
	nDays := int(toT.Sub(fromT).Hours()/24) + 1
	return from, fromT, toT, nDays, true
}

func (s *Server) HandleAPIPatrolIsochrones(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	area := strings.TrimSpace(q.Get("area"))
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, max-age=120")
	if area == "" && q.Get("bbox") != "" {
		s.patrolIsochronesBBox(w, r)
		return
	}
	if area == "" {
		lon, e1 := strconv.ParseFloat(q.Get("lon"), 64)
		lat, e2 := strconv.ParseFloat(q.Get("lat"), 64)
		if e1 != nil || e2 != nil {
			http.Error(w, `{"error":"area, bbox or lon,lat required"}`, http.StatusBadRequest)
			return
		}
		area = s.fireSeasonAreaAt(r, lon, lat)
		if area == "" {
			json.NewEncoder(w).Encode(map[string]interface{}{"area": nil, "status": "no area with a season front here"})
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

	to := q.Get("to")
	if to == "" {
		to = time.Now().UTC().Format("2006-01-02")
	}
	// Every season of the area (label, start, end): the animator fetches
	// one window per season so a multi-year slider draws each year's
	// isochrones in its own place (the handler caps a window at 800 d).
	var rows []patrolSeasonRow
	{
		rs, err := s.DB.QueryContext(r.Context(), `
			SELECT area_id, season, season_start, season_end, nx, ny, res, x0, y0, front, usual
			FROM fire_season_front WHERE area_id = ? ORDER BY season_start`, area)
		if err != nil {
			internalError(w, "query failed", err)
			return
		}
		for rs.Next() {
			var rw patrolSeasonRow
			if rs.Scan(&rw.area, &rw.season, &rw.start, &rw.end, &rw.nx, &rw.ny, &rw.res, &rw.x0, &rw.y0, &rw.front, &rw.usual) == nil {
				rows = append(rows, rw)
			}
		}
		rs.Close()
	}
	pick, seasonsOut := patrolPickSeason(rows, to)
	if pick == nil {
		json.NewEncoder(w).Encode(map[string]interface{}{"area": area, "season": nil, "status": "not yet computed"})
		return
	}
	from, fromT, toT, nDays, ok := patrolWindow(pick, q.Get("from"), to, q.Get("clip") == "1")
	if !ok {
		http.Error(w, `{"error":"bad from/to"}`, http.StatusBadRequest)
		return
	}
	if nDays < 1 || nDays > 800 {
		http.Error(w, `{"error":"window must be 1..800 days"}`, http.StatusBadRequest)
		return
	}
	mode := q.Get("mode")
	if mode != "air" && mode != "ground" {
		mode = "all"
	}
	x1, y1 := pick.x0+pick.res*float64(pick.nx), pick.y0+pick.res*float64(pick.ny)
	raw, err := s.patrolRawVisits(r, pick.res, pick.x0, pick.y0, x1, y1, from, to, mode)
	if err != nil {
		internalError(w, "query failed", err)
		return
	}
	json.NewEncoder(w).Encode(patrolAreaAnswer(pick, seasonsOut, raw, from, to, mode, q.Get("lines"), fromT, toT, nDays))
}

// patrolIsochronesBBox — GET /api/patrol-isochrones?bbox=w,s,e,n&from=&to=[&lines=all|15|30][&limit=][&exclude=][&visits=1]
//
// The fire front's bbox rule (fireSeasonBBox) for the rangers: unfocused,
// the map drew ONE area's isochrones (the park under the view centre), so
// two patrolled parks side by side showed lines in one and none in the
// other. This answers for every PARK whose grid intersects the bbox AND
// holds a patrol-day in the window — each the full single-area wire
// (contours, pressure, visits for the playhead), at the season `to` falls
// in, always clipped to it (the single path's clip=1: presence is counted
// per season). Parks the caller can see nothing in are not listed but are
// counted (`candidates`), so an empty `areas` says "no patrols here", not
// "no parks here" (invariant 1). One track_points scan over the union of
// the candidate grids serves them all (patrolRawVisit). `limit` caps the
// areas nearest the bbox centre first and `truncated` says so (invariant
// 8); `exclude` names the reference the caller already holds.
func (s *Server) patrolIsochronesBBox(w http.ResponseWriter, r *http.Request) {
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
	to := q.Get("to")
	if to == "" {
		to = time.Now().UTC().Format("2006-01-02")
	}
	lines := q.Get("lines")
	if lines != "15" && lines != "30" {
		lines = "all"
	}
	limit := 60
	if n, err := strconv.Atoi(q.Get("limit")); err == nil && n > 0 && n <= 200 {
		limit = n
	}
	exclude := q.Get("exclude")
	withVisits := q.Get("visits") == "1"
	mode := q.Get("mode")
	if mode != "air" && mode != "ground" {
		mode = "all"
	}
	rs, err := s.DB.QueryContext(r.Context(), `
		SELECT area_id, season, season_start, season_end, nx, ny, res, x0, y0, front, usual
		FROM fire_season_front
		WHERE x0 <= ? AND x0 + res * nx >= ? AND y0 <= ? AND y0 + res * ny >= ?
		ORDER BY area_id, season_start`, bb[2], bb[0], bb[3], bb[1])
	if err != nil {
		internalError(w, "query failed", err)
		return
	}
	byArea := map[string][]patrolSeasonRow{}
	var order []string
	for rs.Next() {
		var rw patrolSeasonRow
		if rs.Scan(&rw.area, &rw.season, &rw.start, &rw.end, &rw.nx, &rw.ny, &rw.res, &rw.x0, &rw.y0, &rw.front, &rw.usual) != nil {
			continue
		}
		if rw.area == exclude || IsAOIID(rw.area) || !patrolGridAligned(rw.x0, rw.y0, rw.res) {
			continue
		}
		if _, seen := byArea[rw.area]; !seen {
			order = append(order, rw.area)
		}
		byArea[rw.area] = append(byArea[rw.area], rw)
	}
	rs.Close()
	type cand struct {
		pick    *patrolSeasonRow
		seasons []patrolSeasonOut
		from    string
		fromT   time.Time
		toT     time.Time
		nDays   int
		dist    float64
	}
	cands := map[string]*cand{}
	var ux0, uy0, ux1, uy1 = math.Inf(1), math.Inf(1), math.Inf(-1), math.Inf(-1)
	var res float64
	var minFrom string
	var kept []string
	cx, cy := (bb[0]+bb[2])/2, (bb[1]+bb[3])/2
	for _, id := range order {
		pick, seasons := patrolPickSeason(byArea[id], to)
		if pick == nil {
			continue
		}
		from, fromT, toT, nDays, ok := patrolWindow(pick, q.Get("from"), to, true)
		if !ok || nDays < 1 || nDays > 800 {
			continue
		}
		if res != 0 && pick.res != res {
			continue // one lattice per answer (every grid is 0.025°; a stranger would mis-index)
		}
		res = pick.res
		c := &cand{pick: pick, seasons: seasons, from: from, fromT: fromT, toT: toT, nDays: nDays,
			dist: math.Hypot(pick.x0+pick.res*float64(pick.nx)/2-cx, pick.y0+pick.res*float64(pick.ny)/2-cy)}
		cands[id] = c
		kept = append(kept, id)
		ux0, uy0 = math.Min(ux0, pick.x0), math.Min(uy0, pick.y0)
		ux1, uy1 = math.Max(ux1, pick.x0+pick.res*float64(pick.nx)), math.Max(uy1, pick.y0+pick.res*float64(pick.ny))
		if minFrom == "" || from < minFrom {
			minFrom = from
		}
	}
	out := []map[string]interface{}{}
	withPatrols := 0
	if len(kept) > 0 {
		raw, err := s.patrolRawVisits(r, res, ux0, uy0, ux1, uy1, minFrom, to, mode)
		if err != nil {
			internalError(w, "query failed", err)
			return
		}
		// Bucket the lattice cells by area (a cell can lie in two
		// overlapping grids; it belongs to both).
		sort.Slice(kept, func(i, j int) bool { return cands[kept[i]].dist < cands[kept[j]].dist })
		for _, id := range kept {
			c := cands[id]
			p := c.pick
			offX, offY := int(math.Round(p.x0/res)), int(math.Round(p.y0/res))
			var mine []patrolRawVisit
			for _, v := range raw {
				ix, iy := v.gx-offX, v.gy-offY
				if ix >= 0 && iy >= 0 && ix < p.nx && iy < p.ny && v.day >= c.from {
					mine = append(mine, v)
				}
			}
			if len(mine) == 0 {
				continue
			}
			withPatrols++
			if len(out) >= limit {
				continue
			}
			a := patrolAreaAnswer(p, c.seasons, mine, c.from, to, mode, lines, c.fromT, c.toT, c.nDays)
			if !withVisits {
				delete(a, "visits") // ~⅔ of the bytes; only an animator needs them (visits=1)
			}
			out = append(out, a)
		}
	}
	json.NewEncoder(w).Encode(map[string]interface{}{
		"mode":       "bbox",
		"to":         to,
		"lines":      lines,
		"areas":      out,
		"count":      len(out),
		"total":      withPatrols,
		"candidates": len(kept),
		"truncated":  withPatrols > len(out),
	})
}

// patrolPressureLadder: the contour levels offered, in patrol-days within
// ~5 km; only those below the field's maximum are cut.
var patrolPressureLadder = []float64{1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000}

// patrolPressure contours the accumulated presence at the window's end.
// The field is defined everywhere (0 where nobody went), so the lines close
// around the effort rather than stopping at a mask edge. Labelled at every
// level: a ladder line without its number is a ring, not a quantity.
func patrolPressure(acc []float64, nx, ny int, x0, y0, res float64) map[string]interface{} {
	mx := 0.0
	for _, v := range acc {
		if v > mx {
			mx = v
		}
	}
	feats := []map[string]interface{}{}
	levels := []float64{}
	for _, lvl := range patrolPressureLadder {
		if lvl > mx {
			break
		}
		lines := marchingSquares(acc, nx, ny, x0, y0, res, lvl)
		if len(lines) == 0 {
			continue
		}
		levels = append(levels, lvl)
		feats = append(feats, map[string]interface{}{
			"type":     "Feature",
			"geometry": map[string]interface{}{"type": "MultiLineString", "coordinates": lines},
			"properties": map[string]interface{}{
				"level": lvl, "label": true, "text": strconv.FormatFloat(lvl, 'f', -1, 64),
			},
		})
	}
	return map[string]interface{}{
		"unit":     "patrol-days within ~5 km of the cell at the window's end (weighted by movement type — see weights; the isochrone threshold is on this scale)",
		"levels":   levels,
		"max":      math.Round(mx*10) / 10,
		"contours": feats,
	}
}

// patrolFrontAssociation: over the reference season's cells whose front had
// arrived by `to` and which carry a usual front, median(front − usual) in
// days for cells patrols reached before the front vs the rest. nil groups
// under 20 cells (a handful is not a tendency).
func patrolFrontAssociation(front, usual []byte, arrival []float64, nx, ny int, seasonStart string, fromT, toT time.Time) map[string]interface{} {
	n := nx * ny
	if len(front) < 2*n || len(usual) < 2*n {
		return nil
	}
	s0, ok := parseISODate(seasonStart)
	if !ok {
		return nil
	}
	dosTo := int(toT.Sub(s0).Hours() / 24)
	shift := int(fromT.Sub(s0).Hours() / 24) // patrol day index → day of season
	var before, other []float64
	for i := 0; i < n; i++ {
		f := int(int16(uint16(front[2*i]) | uint16(front[2*i+1])<<8))
		u := int(int16(uint16(usual[2*i]) | uint16(usual[2*i+1])<<8))
		if f < 0 || u < 0 || f > dosTo {
			continue
		}
		off := float64(f - u)
		if !math.IsNaN(arrival[i]) && int(arrival[i])+shift <= f {
			before = append(before, off)
		} else {
			other = append(other, off)
		}
	}
	med := func(v []float64) interface{} {
		if len(v) < 20 {
			return nil
		}
		sort.Float64s(v)
		return v[len(v)/2]
	}
	return map[string]interface{}{
		"basis":                  "median of (this season's front − usual front) in days, + = later than usual, over front-bearing cells reached by `to`",
		"patrolled_before_front": map[string]interface{}{"cells": len(before), "offset_days": med(before)},
		"other":                  map[string]interface{}{"cells": len(other), "offset_days": med(other)},
		"min_cells":              20,
	}
}

// marchingSquares contours a masked (NaN) grid at one level; cell (ix,iy)
// centres sit at x0+(ix+0.5)res, y0+(iy+0.5)res. Segments are linked into
// polylines by shared endpoints and thinned to ~¼ cell, like fire_front.py.
func marchingSquares(z []float64, nx, ny int, x0, y0, res, level float64) [][][2]float64 {
	type pt [2]float64
	key := func(p pt) string { return fmt.Sprintf("%.5f,%.5f", p[0], p[1]) }
	interp := func(ax, ay, az, bx, by, bz float64) pt {
		t := (level - az) / (bz - az)
		if t < 0 {
			t = 0
		}
		if t > 1 {
			t = 1
		}
		return pt{ax + t*(bx-ax), ay + t*(by-ay)}
	}
	var segs [][2]pt
	for iy := 0; iy < ny-1; iy++ {
		for ix := 0; ix < nx-1; ix++ {
			// corners: 0 = (ix,iy) SW, 1 = (ix+1,iy) SE, 2 = (ix+1,iy+1) NE, 3 = (ix,iy+1) NW
			v := [4]float64{z[iy*nx+ix], z[iy*nx+ix+1], z[(iy+1)*nx+ix+1], z[(iy+1)*nx+ix]}
			if math.IsNaN(v[0]) || math.IsNaN(v[1]) || math.IsNaN(v[2]) || math.IsNaN(v[3]) {
				continue
			}
			cx := [4]float64{x0 + (float64(ix)+0.5)*res, x0 + (float64(ix)+1.5)*res, x0 + (float64(ix)+1.5)*res, x0 + (float64(ix)+0.5)*res}
			cy := [4]float64{y0 + (float64(iy)+0.5)*res, y0 + (float64(iy)+0.5)*res, y0 + (float64(iy)+1.5)*res, y0 + (float64(iy)+1.5)*res}
			idx := 0
			for c := 0; c < 4; c++ {
				if v[c] >= level {
					idx |= 1 << c
				}
			}
			if idx == 0 || idx == 15 {
				continue
			}
			edge := func(e int) pt { // edge e joins corner e and (e+1)%4
				a, b := e, (e+1)%4
				return interp(cx[a], cy[a], v[a], cx[b], cy[b], v[b])
			}
			add := func(e1, e2 int) { segs = append(segs, [2]pt{edge(e1), edge(e2)}) }
			switch idx {
			case 1, 14:
				add(3, 0)
			case 2, 13:
				add(0, 1)
			case 3, 12:
				add(3, 1)
			case 4, 11:
				add(1, 2)
			case 6, 9:
				add(0, 2)
			case 7, 8:
				add(3, 2)
			case 5, 10:
				// saddle: resolve by the centre value
				c := (v[0] + v[1] + v[2] + v[3]) / 4
				if (c >= level) == (idx == 5) {
					add(3, 0)
					add(1, 2)
				} else {
					add(0, 1)
					add(3, 2)
				}
			}
		}
	}
	if len(segs) == 0 {
		return nil
	}
	// link: endpoint → segment indices
	ends := map[string][]int{}
	for i, s := range segs {
		ends[key(s[0])] = append(ends[key(s[0])], i)
		ends[key(s[1])] = append(ends[key(s[1])], i)
	}
	used := make([]bool, len(segs))
	var out [][][2]float64
	take := func(p pt) (pt, bool) {
		for _, i := range ends[key(p)] {
			if used[i] {
				continue
			}
			used[i] = true
			if key(segs[i][0]) == key(p) {
				return segs[i][1], true
			}
			return segs[i][0], true
		}
		return p, false
	}
	for i := range segs {
		if used[i] {
			continue
		}
		used[i] = true
		line := []pt{segs[i][0], segs[i][1]}
		for { // grow forward
			np, ok := take(line[len(line)-1])
			if !ok {
				break
			}
			line = append(line, np)
		}
		for { // grow backward
			np, ok := take(line[0])
			if !ok {
				break
			}
			line = append([]pt{np}, line...)
		}
		if len(line) < 3 {
			continue
		}
		thin := [][2]float64{{r4(line[0][0]), r4(line[0][1])}}
		last := line[0]
		for k := 1; k < len(line); k++ {
			if math.Abs(line[k][0]-last[0])+math.Abs(line[k][1]-last[1]) >= res*0.25 || k == len(line)-1 {
				thin = append(thin, [2]float64{r4(line[k][0]), r4(line[k][1])})
				last = line[k]
			}
		}
		if len(thin) >= patrolMinVerts {
			out = append(out, thin)
		}
	}
	return out
}

func r4(v float64) float64 { return math.Round(v*1e4) / 1e4 }
