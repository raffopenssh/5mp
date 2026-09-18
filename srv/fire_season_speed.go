package srv

// Season speed map — the front as an arrival-time surface T(x,y): its
// gradient is slowness, 1/|∇T| is how fast the burning season travels
// (km/day), the map of where the season runs and where it stalls. The
// eikonal probe (scripts/fire_vanguard/eikonal.py) measured it on XSA
// 2024/25 — median 4.6 km/d, p10 1.9, p90 14.7 — and fire.md carried "not
// drawn yet" until 2026-09-14. It is a DESCRIPTIVE product of a finished or
// running season, not a predictor (as a live predictor Dijkstra on this
// field lost to last season's front, MAE 59 vs 7.8 d).
//
//	GET /api/fire-season-speed?area=<park|aoi>|lon=&lat=[&from=&to=|&at=YYYY-MM-DD|&season=2024/25]
//	    → {"area","season","bbox":[w,s,e,n],"grid":{x0,y0,res,nx,ny},
//	       "values":"<base64 uint8 per cell>", "encoding":{…},
//	       "seasons":[{"season","season_start","season_end","values","arrival"}…]  (every season the window touches),
//	       "stats":{"cells","p10_km_d","median_km_d","p90_km_d"},
//	       "legend":[{"km_d":…, "color":"#…"}…], "levels":{"n":255,"km_d_min":1,"km_d_max":50}}
//
// One byte per cell at the grid's own resolution (2.5 km), the same wire
// shape as the early-burn ground in /api/fire-season: the client
// (srv/static/cellfield.js) colours it, draws true squares that scale with
// zoom, and reads the value under the pointer from the array. Colour is a
// FIXED log ramp in km/day (legend below) so two areas can be compared;
// per-area stretching would make every map look the same.

import (
	"encoding/base64"
	"encoding/json"
	"image/color"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
)

// speedStops: km/day → colour. Warm and single-family with the fire
// palette (a stall is the season lingering, not a different subject):
// slow = deep rust, fast = pale cream. Log-spaced.
var speedStops = []struct {
	KmD   float64
	Color [3]uint8
}{
	{1, [3]uint8{0x7c, 0x2d, 0x12}},  // rust — the season crawls
	{3, [3]uint8{0xc2, 0x41, 0x0c}},  // ember
	{8, [3]uint8{0xf5, 0x9e, 0x0b}},  // amber
	{20, [3]uint8{0xfd, 0xe6, 0x8a}}, // straw
	{50, [3]uint8{0xff, 0xfb, 0xeb}}, // cream — the season sweeps through
}

// speedLevels quantise km/day to a byte on the log scale between the first
// and last stop (byte 0 is reserved for "no front", so 255 levels).
const speedLevels = 255

func speedOfLevel(l int) float64 {
	lo, hi := math.Log(speedStops[0].KmD), math.Log(speedStops[len(speedStops)-1].KmD)
	return math.Exp(lo + (hi-lo)*float64(l)/float64(speedLevels-1))
}

func levelOfSpeed(kmd float64) int {
	lo, hi := math.Log(speedStops[0].KmD), math.Log(speedStops[len(speedStops)-1].KmD)
	if kmd <= 0 {
		return 0
	}
	l := int(math.Round((math.Log(kmd) - lo) / (hi - lo) * float64(speedLevels-1)))
	if l < 0 {
		l = 0
	}
	if l > speedLevels-1 {
		l = speedLevels - 1
	}
	return l
}

func speedColor(kmd float64) color.NRGBA {
	if kmd <= speedStops[0].KmD {
		c := speedStops[0].Color
		return color.NRGBA{c[0], c[1], c[2], 255}
	}
	last := speedStops[len(speedStops)-1]
	if kmd >= last.KmD {
		return color.NRGBA{last.Color[0], last.Color[1], last.Color[2], 255}
	}
	for i := 1; i < len(speedStops); i++ {
		a, b := speedStops[i-1], speedStops[i]
		if kmd <= b.KmD {
			t := (math.Log(kmd) - math.Log(a.KmD)) / (math.Log(b.KmD) - math.Log(a.KmD))
			mix := func(x, y uint8) uint8 { return uint8(math.Round(float64(x) + t*(float64(y)-float64(x)))) }
			return color.NRGBA{mix(a.Color[0], b.Color[0]), mix(a.Color[1], b.Color[1]), mix(a.Color[2], b.Color[2]), 255}
		}
	}
	return color.NRGBA{last.Color[0], last.Color[1], last.Color[2], 255}
}

// gaussianNC smooths a masked grid by normalised convolution (values·mask
// blurred over mask blurred), separable, σ in cells — the same operation the
// probe used (scipy gaussian_filter of value and of mask). Cells outside the
// mask get an interpolated value, which the caller masks back out.
func gaussianNC(v []float64, mask []bool, nx, ny int, sigma float64) []float64 {
	r := int(math.Ceil(3 * sigma))
	k := make([]float64, 2*r+1)
	for i := -r; i <= r; i++ {
		k[i+r] = math.Exp(-float64(i*i) / (2 * sigma * sigma))
	}
	num := make([]float64, nx*ny)
	den := make([]float64, nx*ny)
	for i := range v {
		if mask[i] {
			num[i], den[i] = v[i], 1
		}
	}
	blur := func(src []float64) []float64 {
		tmp := make([]float64, nx*ny)
		for y := 0; y < ny; y++ {
			for x := 0; x < nx; x++ {
				var s, wsum float64
				for d := -r; d <= r; d++ {
					xx := x + d
					if xx < 0 || xx >= nx {
						continue
					}
					s += k[d+r] * src[y*nx+xx]
					wsum += k[d+r]
				}
				tmp[y*nx+x] = s / wsum
			}
		}
		out := make([]float64, nx*ny)
		for y := 0; y < ny; y++ {
			for x := 0; x < nx; x++ {
				var s, wsum float64
				for d := -r; d <= r; d++ {
					yy := y + d
					if yy < 0 || yy >= ny {
						continue
					}
					s += k[d+r] * tmp[yy*nx+x]
					wsum += k[d+r]
				}
				out[y*nx+x] = s / wsum
			}
		}
		return out
	}
	bn, bd := blur(num), blur(den)
	out := make([]float64, nx*ny)
	for i := range out {
		if bd[i] > 1e-6 {
			out[i] = bn[i] / bd[i]
		}
	}
	return out
}

// seasonSpeed turns a packed int16 front grid into km/day per cell (NaN
// where the season has no front). res in degrees, y0 the grid's south edge.
func seasonSpeed(front []byte, nx, ny int, res, y0 float64) []float64 {
	n := nx * ny
	if n == 0 || len(front) < 2*n {
		return nil
	}
	v := make([]float64, n)
	mask := make([]bool, n)
	any := false
	for i := 0; i < n; i++ {
		f := int(int16(uint16(front[2*i]) | uint16(front[2*i+1])<<8))
		if f >= 0 {
			v[i], mask[i], any = float64(f), true, true
		}
	}
	if !any {
		return nil
	}
	return speedOfSurface(v, mask, nx, ny, res, y0)
}

// speedOfSurface: 1/|∇T| of an arrival-day surface (days; `mask` where it
// holds a value), km/day per cell, NaN off the mask. Smoothed σ=2 cells
// first, central differences, like the eikonal probe.
func speedOfSurface(v []float64, mask []bool, nx, ny int, res, y0 float64) []float64 {
	n := nx * ny
	out := make([]float64, n)
	sm := gaussianNC(v, mask, nx, ny, 2)
	kmY := res * 111.0
	kmX := res * 111.0 * math.Cos((y0+res*float64(ny)/2)*math.Pi/180)
	for y := 0; y < ny; y++ {
		for x := 0; x < nx; x++ {
			i := y*nx + x
			if !mask[i] {
				out[i] = math.NaN()
				continue
			}
			// central differences (one-sided at the edges), like np.gradient
			x0, x1, dx := x-1, x+1, 2*kmX
			if x0 < 0 {
				x0, dx = x, kmX
			}
			if x1 >= nx {
				x1, dx = x, dx-kmX
			}
			y0i, y1, dy := y-1, y+1, 2*kmY
			if y0i < 0 {
				y0i, dy = y, kmY
			}
			if y1 >= ny {
				y1, dy = y, dy-kmY
			}
			gx := (sm[y*nx+x1] - sm[y*nx+x0]) / dx
			gy := (sm[y1*nx+x] - sm[y0i*nx+x]) / dy
			slow := math.Hypot(gx, gy) // day/km
			if slow < 1e-3 {
				slow = 1e-3
			}
			out[i] = 1 / slow
		}
	}
	return out
}

func (s *Server) HandleAPIFireSeasonSpeed(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	area := strings.TrimSpace(q.Get("area"))
	if area == "" && q.Get("bbox") != "" {
		s.fireSeasonSpeedBBox(w, r)
		return
	}
	if area == "" {
		// No focus: the area whose grid holds the view centre, resolved by
		// the same rule as /api/fire-season so both overlays name one area.
		lon, e1 := strconv.ParseFloat(q.Get("lon"), 64)
		lat, e2 := strconv.ParseFloat(q.Get("lat"), 64)
		if e1 != nil || e2 != nil {
			http.Error(w, `{"error":"area, bbox or lon,lat required"}`, http.StatusBadRequest)
			return
		}
		area = s.fireSeasonAreaAt(r, lon, lat)
		if area == "" {
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Cache-Control", "private, max-age=120")
			json.NewEncoder(w).Encode(map[string]interface{}{"area": nil, "season": nil, "status": "no area with a season front here"})
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
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, max-age=600")
	from, to, at := q.Get("from"), q.Get("to"), q.Get("at")
	out, err := s.speedAreaAnswer(r, area, q.Get("season"), from, to, at)
	if err != nil {
		internalError(w, "query failed", err)
		return
	}
	speedCommon(out, from, to)
	json.NewEncoder(w).Encode(out)
}

// speedCommon adds the wire's shared vocabulary (legend, levels, words):
// top-level in the single-area answer, once beside `areas` in bbox mode.
func speedCommon(out map[string]interface{}, from, to string) {
	legend := make([]map[string]interface{}, 0, len(speedStops))
	for _, st := range speedStops {
		legend = append(legend, map[string]interface{}{"km_d": st.KmD, "color": rgbHex(st.Color)})
	}
	out["legend"] = legend
	// byte b > 0 → level b-1 → km/day = km_d_min·(km_d_max/km_d_min)^(level/(n-1)); `stops` is the colour ramp
	out["levels"] = map[string]interface{}{"n": speedLevels, "km_d_min": speedStops[0].KmD, "km_d_max": speedStops[len(speedStops)-1].KmD}
	out["encoding"] = map[string]interface{}{"type": "uint8", "none": 0, "offset": 1, "order": "row-major, row 0 = south"}
	out["arrival_encoding"] = map[string]interface{}{"type": "uint8", "none": 0, "day_of_season": "2*(b-1)", "step_days": 2}
	out["window"] = map[string]interface{}{"from": from, "to": to}
	out["words"] = "How fast the season front travels, km/day, from the gradient of its arrival-time surface " +
		"(smoothed σ=2 cells). Descriptive, not a forecast."
}

// speedAreaAnswer is one area's speed field(s) — the object the single
// path serves at the top level and the bbox path lists under `areas`.
//
// Which seasons: explicit `want`; else every season overlapping the
// slider's window [from, to] (a window is a statement about time, and a
// cell's answer is the LAST front that reached it inside the window —
// fireseason.js picks per cell), else the one `at` falls in, else the
// latest. The primary season (top-level `season`/`values`/`stats`) is the
// latest overlapping one, the same rule as /api/fire-season, so the two
// overlays name one season.
func (s *Server) speedAreaAnswer(r *http.Request, area, want, from, to, at string) (map[string]interface{}, error) {
	type seasonRow struct {
		lbl, st, en string
		complete    bool
	}
	var rows []seasonRow
	{
		rs, err := s.DB.QueryContext(r.Context(), `
			SELECT season, season_start, season_end, complete FROM fire_season_front
			WHERE area_id = ? ORDER BY season_start`, area)
		if err != nil {
			return nil, err
		}
		for rs.Next() {
			var sr seasonRow
			var c int
			if rs.Scan(&sr.lbl, &sr.st, &sr.en, &c) == nil {
				sr.complete = c == 1
				rows = append(rows, sr)
			}
		}
		rs.Close()
	}
	if to == "" {
		to = at
	}
	var chosen []seasonRow
	for _, sr := range rows {
		switch {
		case want != "":
			if sr.lbl == want {
				chosen = append(chosen, sr)
			}
		case from != "" && to != "":
			if sr.st <= to && sr.en >= from {
				chosen = append(chosen, sr)
			}
		case to != "":
			if sr.st <= to && to <= sr.en {
				chosen = []seasonRow{sr}
			}
		}
	}
	if len(chosen) == 0 && want == "" && len(rows) > 0 {
		// a window before/after every season, or no window: the latest
		// season that had begun by `to` (else the latest of all)
		pick := rows[len(rows)-1]
		for _, sr := range rows {
			if to == "" || sr.st <= to {
				pick = sr
			}
		}
		chosen = []seasonRow{pick}
	}
	if len(chosen) == 0 {
		return map[string]interface{}{"area": area, "season": nil, "status": "not yet computed"}, nil
	}
	// The primary season: the latest chosen one that HAS a front. A
	// season a few weeks old carries a few hundred cells; its stats are
	// honest for it, but the field the reader sees is the union.
	type seasonField struct {
		row    seasonRow
		levels []byte
		arr    []byte
		vals   []float64
	}
	var (
		fields      []seasonField
		nx, ny      int
		res, x0, y0 float64
	)
	for _, sr := range chosen {
		var (
			fnx, fny       int
			fres, fx0, fy0 float64
			front          []byte
		)
		err := s.DB.QueryRowContext(r.Context(), `
			SELECT nx, ny, res, x0, y0, front FROM fire_season_front WHERE area_id = ? AND season = ?`,
			area, sr.lbl).Scan(&fnx, &fny, &fres, &fx0, &fy0, &front)
		if err != nil {
			continue
		}
		if len(fields) > 0 && (fnx != nx || fny != ny || fres != res) {
			continue // a season on another grid cannot share the canvas
		}
		speed := seasonSpeed(front, fnx, fny, fres, fy0)
		if speed == nil {
			continue
		}
		nx, ny, res, x0, y0 = fnx, fny, fres, fx0, fy0
		// One byte per cell, row 0 = the grid's SOUTH edge: 0 = no front this
		// season, 1..speedLevels = level + 1 on the log ramp. `arrival` is
		// the front's day of season per cell in 2-day steps (byte 1..255 →
		// day 0..508; 0 = none), the animator's cue for when a cell lights.
		// The client (srv/static/cellfield.js) decodes both once, draws
		// squares, and reads the speed under the pointer from the array.
		levels := make([]byte, fnx*fny)
		arr := make([]byte, fnx*fny)
		vals := make([]float64, 0, fnx*fny)
		for i, v := range speed {
			if math.IsNaN(v) {
				continue
			}
			vals = append(vals, v)
			levels[i] = byte(levelOfSpeed(v) + 1)
			f := int(int16(uint16(front[2*i]) | uint16(front[2*i+1])<<8))
			if f >= 0 {
				d := f/2 + 1
				if d > 255 {
					d = 255
				}
				arr[i] = byte(d)
			}
		}
		sort.Float64s(vals)
		fields = append(fields, seasonField{row: sr, levels: levels, arr: arr, vals: vals})
	}
	if len(fields) == 0 {
		return map[string]interface{}{"area": area, "season": chosen[len(chosen)-1].lbl, "status": "no front this season"}, nil
	}
	primary := fields[len(fields)-1]
	vals := primary.vals
	pct := func(p float64) float64 {
		if len(vals) == 0 {
			return math.NaN()
		}
		i := int(p * float64(len(vals)-1))
		return math.Round(vals[i]*10) / 10
	}
	seasonsOut := make([]map[string]interface{}, 0, len(fields))
	for _, f := range fields {
		seasonsOut = append(seasonsOut, map[string]interface{}{
			"season": f.row.lbl, "season_start": f.row.st, "season_end": f.row.en, "complete": f.row.complete,
			"cells":   len(f.vals),
			"values":  base64.StdEncoding.EncodeToString(f.levels),
			"arrival": base64.StdEncoding.EncodeToString(f.arr),
		})
	}
	return map[string]interface{}{
		"area":   area,
		"season": primary.row.lbl,
		"status": "ok",
		"bbox":   []float64{x0, y0, x0 + res*float64(nx), y0 + res*float64(ny)},
		"grid":   map[string]interface{}{"x0": x0, "y0": y0, "res": res, "nx": nx, "ny": ny},
		"values": base64.StdEncoding.EncodeToString(primary.levels), // uint8 per cell, see speedCommon.encoding
		"stats": map[string]interface{}{
			"cells": len(vals), "p10_km_d": pct(0.10), "median_km_d": pct(0.50), "p90_km_d": pct(0.90),
			"smoothing_sigma_cells": 2, "cell_km": math.Round(res*111*10) / 10,
		},
		// every season the window touches, oldest first; `arrival` is the
		// front's day of season per cell (byte b > 0 → day 2·(b−1)), so a
		// cell can be drawn the day the front reached it and the last season
		// to reach a cell inside the window wins
		"seasons": seasonsOut,
	}, nil
}

// fireSeasonSpeedBBox — GET /api/fire-season-speed?bbox=w,s,e,n[&from=&to=|&at=][&limit=][&exclude=]
//
// The front's bbox rule (fireSeasonBBox) for the speed field: every PARK
// whose grid intersects the bbox, each with the seasons its window
// touches, nearest the bbox centre first, `limit` + `truncated` (invariant
// 8), `exclude` for the reference the caller already holds. Legend and
// levels ride once at the top. A dense field is ~2 × nx·ny bytes per
// season before gzip (mostly zeros: a 100×100 park gzips to a few KB), so
// a continent at 30 areas × 6 seasons is a one-off few hundred KB, which
// is why the client asks with a quantised bbox and a small-screen limit.
func (s *Server) fireSeasonSpeedBBox(w http.ResponseWriter, r *http.Request) {
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
	limit := 60
	if n, err := strconv.Atoi(q.Get("limit")); err == nil && n > 0 && n <= 200 {
		limit = n
	}
	exclude := q.Get("exclude")
	from, to, at := q.Get("from"), q.Get("to"), q.Get("at")
	rs, err := s.DB.QueryContext(r.Context(), `
		SELECT DISTINCT area_id, x0 + res * nx / 2.0, y0 + res * ny / 2.0
		FROM fire_season_front
		WHERE x0 <= ? AND x0 + res * nx >= ? AND y0 <= ? AND y0 + res * ny >= ?`, bb[2], bb[0], bb[3], bb[1])
	if err != nil {
		internalError(w, "query failed", err)
		return
	}
	type cand struct {
		id   string
		dist float64
	}
	var cands []cand
	cx, cy := (bb[0]+bb[2])/2, (bb[1]+bb[3])/2
	seen := map[string]bool{}
	for rs.Next() {
		var id string
		var gx, gy float64
		if rs.Scan(&id, &gx, &gy) != nil || id == exclude || IsAOIID(id) || seen[id] {
			continue
		}
		seen[id] = true
		cands = append(cands, cand{id, math.Hypot(gx-cx, gy-cy)})
	}
	rs.Close()
	sort.Slice(cands, func(i, j int) bool { return cands[i].dist < cands[j].dist })
	total := len(cands)
	if len(cands) > limit {
		cands = cands[:limit]
	}
	areas := make([]map[string]interface{}, 0, len(cands))
	for _, c := range cands {
		a, err := s.speedAreaAnswer(r, c.id, "", from, to, at)
		if err != nil {
			internalError(w, "query failed", err)
			return
		}
		if a["status"] != "ok" {
			continue
		}
		delete(a, "values") // the primary season's field is seasons[last].values; not shipped twice
		areas = append(areas, a)
	}
	out := map[string]interface{}{
		"mode": "bbox", "areas": areas, "count": len(areas), "total": total, "truncated": total > len(cands),
	}
	speedCommon(out, from, to)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, max-age=600")
	json.NewEncoder(w).Encode(out)
}

func rgbHex(c [3]uint8) string {
	const hx = "0123456789abcdef"
	b := make([]byte, 7)
	b[0] = '#'
	for i, v := range c {
		b[1+2*i], b[2+2*i] = hx[v>>4], hx[v&15]
	}
	return string(b)
}
