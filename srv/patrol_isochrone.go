package srv

// Patrol isochrones — the season front's counterpart for the rangers: the
// day patrol PRESENCE had built up at a place, drawn as dated lines the way
// the fire front is, so the two can be read against each other across a
// year ("the front bent around the ground the teams had been on since
// June").
//
//	GET /api/patrol-isochrones?area=<park|aoi>|lon=&lat=&from=&to=[&mode=all|ground|air]
//	    → {"area","season","season_start","from","to",
//	       "grid":{x0,y0,res,nx,ny}, "cells", "patrol_days", "threshold", "kernel_cells",
//	       "contours":[Feature{dos,date,label,text}…],     dos = days since `from`
//	       "arrival":[[ix,iy,dos]…],                        per reached cell
//	       "association":{…}, "status"}
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

func (s *Server) HandleAPIPatrolIsochrones(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	area := strings.TrimSpace(q.Get("area"))
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, max-age=120")
	if area == "" {
		lon, e1 := strconv.ParseFloat(q.Get("lon"), 64)
		lat, e2 := strconv.ParseFloat(q.Get("lat"), 64)
		if e1 != nil || e2 != nil {
			http.Error(w, `{"error":"area or lon,lat required"}`, http.StatusBadRequest)
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

	// The reference season: the one `to` falls in (else the latest begun by
	// `to`) — the same rule as /api/fire-season, so both overlays name one.
	to := q.Get("to")
	if to == "" {
		to = time.Now().UTC().Format("2006-01-02")
	}
	from := q.Get("from")
	type row struct {
		season, start, end string
		nx, ny             int
		res, x0, y0        float64
		front, usual       []byte
	}
	var pick *row
	{
		rs, err := s.DB.QueryContext(r.Context(), `
			SELECT season, season_start, season_end, nx, ny, res, x0, y0, front, usual
			FROM fire_season_front WHERE area_id = ? ORDER BY season_start`, area)
		if err != nil {
			internalError(w, "query failed", err)
			return
		}
		for rs.Next() {
			var rw row
			if rs.Scan(&rw.season, &rw.start, &rw.end, &rw.nx, &rw.ny, &rw.res, &rw.x0, &rw.y0, &rw.front, &rw.usual) != nil {
				continue
			}
			if pick == nil || rw.start <= to {
				c := rw
				pick = &c
			}
		}
		rs.Close()
	}
	if pick == nil {
		json.NewEncoder(w).Encode(map[string]interface{}{"area": area, "season": nil, "status": "not yet computed"})
		return
	}
	if from == "" || from > to {
		from = pick.start
	}
	fromT, ok1 := parseISODate(from)
	toT, ok2 := parseISODate(to)
	if !ok1 || !ok2 {
		http.Error(w, `{"error":"bad from/to"}`, http.StatusBadRequest)
		return
	}
	nDays := int(toT.Sub(fromT).Hours()/24) + 1
	if nDays < 1 || nDays > 800 {
		http.Error(w, `{"error":"window must be 1..800 days"}`, http.StatusBadRequest)
		return
	}
	nx, ny, res, x0, y0 := pick.nx, pick.ny, pick.res, pick.x0, pick.y0
	x1, y1 := x0+res*float64(nx), y0+res*float64(ny)

	// Presence in the window over the grid's bbox, from the track points
	// themselves (they carry the movement type; subcell_visits does not):
	// one record per (cell, day, mode), each weighted by patrolModeWeight.
	// `mode=ground|air` restricts to one family for the API reader who
	// wants that picture; the UI never asks — the default is everything,
	// weighted. The timestamp is stored as '2026-06-26 04:23:57 +0000 UTC';
	// its first 10 characters are the day.
	mode := q.Get("mode")
	if mode != "air" && mode != "ground" {
		mode = "all"
	}
	modeSQL := ""
	if mode == "air" {
		modeSQL = "AND t.movement_type IN ('aircraft','fixed_wing','rotor_wing')"
	} else if mode == "ground" {
		modeSQL = "AND t.movement_type NOT IN ('aircraft','fixed_wing','rotor_wing')"
	}
	rows, err := s.DB.QueryContext(r.Context(), `
		SELECT CAST(floor((t.lon - ?) / ?) AS INTEGER), CAST(floor((t.lat - ?) / ?) AS INTEGER),
		       substr(t.timestamp, 1, 10), COALESCE(t.movement_type, ''), COUNT(*)
		FROM track_points t
		WHERE `+PatrolEnvsSQL("t.env")+`
		  AND t.lat >= ? AND t.lat < ? AND t.lon >= ? AND t.lon < ?
		  AND substr(t.timestamp, 1, 10) BETWEEN ? AND ? `+modeSQL+`
		GROUP BY 1, 2, 3, 4`,
		x0, res, y0, res, s.PatrolEnvsJSON(r), y0, y1, x0, x1, from, to)
	if err != nil {
		internalError(w, "query failed", err)
		return
	}
	var visits []patrolCellDay
	modeDays := map[string]int{}
	for rows.Next() {
		var ix, iy, n int
		var day, mt string
		if rows.Scan(&ix, &iy, &day, &mt, &n) != nil {
			continue
		}
		if ix < 0 || iy < 0 || ix >= nx || iy >= ny {
			continue
		}
		dT, ok := parseISODate(day)
		if !ok {
			continue
		}
		d := int(dT.Sub(fromT).Hours() / 24)
		if d < 0 || d >= nDays {
			continue
		}
		w := modeWeight(mt)
		if mt == "" {
			mt = "unlabelled"
		}
		modeDays[mt]++
		visits = append(visits, patrolCellDay{ix, iy, d, w})
	}
	rows.Close()
	base := map[string]interface{}{
		"area": area, "season": pick.season, "season_start": pick.start, "from": from, "to": to,
		"grid":      map[string]interface{}{"x0": x0, "y0": y0, "res": res, "nx": nx, "ny": ny},
		"threshold": patrolThreshold, "kernel_cells": patrolKernelSigma, "reach_km": 5, "mode": mode, "weights": patrolModeWeight,
		"unit":        "patrol-days (a 2.5 km cell with a patrol in it on a day, weighted by movement type — see weights)",
		"patrol_days": len(visits), "by_mode": modeDays,
	}
	if len(visits) == 0 {
		base["cells"] = 0
		base["status"] = "no patrol data in this window here"
		base["contours"] = []interface{}{}
		json.NewEncoder(w).Encode(base)
		return
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
	if reached == 0 {
		base["status"] = fmt.Sprintf("patrols present (%d patrol-days) but nowhere reached %g within ~5 km", len(visits), patrolThreshold)
		base["contours"] = []interface{}{}
		json.NewEncoder(w).Encode(base)
		return
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
		lines := marchingSquares(field, nx, ny, x0, y0, res, float64(lvl))
		if len(lines) == 0 {
			continue
		}
		d := fromT.AddDate(0, 0, lvl)
		feats = append(feats, map[string]interface{}{
			"type":     "Feature",
			"geometry": map[string]interface{}{"type": "MultiLineString", "coordinates": lines},
			"properties": map[string]interface{}{
				"dos": lvl, "date": d.Format("2006-01-02"), "label": lvl%patrolLabelStep == 0,
				"text": fmt.Sprintf("%d %s", d.Day(), d.Format("Jan")),
			},
		})
	}
	base["contours"] = feats
	base["arrival"] = arr
	base["association"] = patrolFrontAssociation(pick.front, pick.usual, arrival, nx, ny, pick.start, fromT, toT)
	base["status"] = "ok"
	json.NewEncoder(w).Encode(base)
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
