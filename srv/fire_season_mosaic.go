package srv

// One front over the viewport.
//
// Each park's season front is measured on its own grid (boundary + margin)
// against its own season calendar, and stored as contours of DAY OF SEASON.
// Drawn side by side those grids overlap, and the overlap was two families
// of isochrones crossing at an angle — and, clipped to owned cells, lines
// that began from nowhere at every seam (the 2026-09-18 report: "starting
// lines from nowhere", "blocky"). The seam is not in the fires; it is in
// drawing per park what the fires do per landscape.
//
// So the bbox path draws ONE surface: every lattice cell in view takes the
// arrival day of the park grid that owns it (smallest grid with a value
// there — fire_season_own.go), converted to an ABSOLUTE day so calendars
// agree, and that mosaic is contoured once (marchingSquares, every 5 d on
// the reference's calendar). A line then runs across park seams as the
// front did; where two parks genuinely disagree about a date the line
// kinks, it does not stop. Speed (fire_season_lines.go) is the gradient of
// the same surface, so the weight is continuous too.
//
// Cost: a z6.5 viewport is ~150k cells × ~70 levels; memoised per
// quantised bbox / at / lines (the client quantises its bbox for exactly
// this) in an LRU with the LOD tiles' shape.

import (
	"container/list"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"time"
)

var mosaicCache = &tileCache{m: map[string]*tileCacheEntry{}, lru: list.New(), maxB: 48 << 20, ttl: 10 * time.Minute}

type mosaicGrid struct {
	*ownGrid
	season, start, end string
	startDay           float64 // season_start as days since epoch
}

func epochDays(iso string) (float64, bool) {
	t, ok := parseISODate(iso)
	if !ok {
		return 0, false
	}
	return math.Floor(float64(t.Unix()) / 86400), true
}

// mosaicGrids: the ownership set with each grid's season calendar.
func (s *Server) mosaicGrids(bb [4]float64, at string) []*mosaicGrid {
	rows, err := s.DB.Query(`
		SELECT area_id, season, season_start, season_end, nx, ny, res, x0, y0, front
		FROM fire_season_front
		WHERE x0 <= ? AND x0 + res * nx >= ? AND y0 <= ? AND y0 + res * ny >= ?
		ORDER BY area_id, season_start`, bb[2], bb[0], bb[3], bb[1])
	if err != nil {
		return nil
	}
	defer rows.Close()
	pick := map[string]*mosaicGrid{}
	held := map[string]bool{}
	var order []string
	for rows.Next() {
		var (
			area, season, st, en string
			nx, ny               int
			res, x0, y0          float64
			front                []byte
		)
		if rows.Scan(&area, &season, &st, &en, &nx, &ny, &res, &x0, &y0, &front) != nil || IsAOIID(area) {
			continue
		}
		sd, ok := epochDays(st)
		if !ok {
			continue
		}
		g := &mosaicGrid{ownGrid: newOwnGrid(area, nx, ny, res, x0, y0, front), season: season, start: st, end: en, startDay: sd}
		if _, seen := pick[area]; !seen {
			order = append(order, area)
		} else if held[area] {
			continue
		}
		pick[area] = g
		held[area] = at != "" && st <= at && at <= en
	}
	out := make([]*mosaicGrid, 0, len(order))
	for _, a := range order {
		out = append(out, pick[a])
	}
	return out
}

// mosaicAnswer builds and contours the surface. `ref` names the area whose
// calendar the levels and `dos` follow (the client's reference), else the
// commonest season start among the grids. Returns nil when no grid in the
// box carries a front.
func fireSeasonMosaic(grids []*mosaicGrid, bb [4]float64, ref, lines string, wantSpeed bool) map[string]interface{} {
	if len(grids) == 0 {
		return nil
	}
	res := grids[0].res
	// lattice extent: the bbox, snapped out to whole cells, ∩ the grids' union
	ux0, uy0, ux1, uy1 := math.Inf(1), math.Inf(1), math.Inf(-1), math.Inf(-1)
	for _, g := range grids {
		ux0 = math.Min(ux0, g.x0)
		uy0 = math.Min(uy0, g.y0)
		ux1 = math.Max(ux1, g.x0+res*float64(g.nx))
		uy1 = math.Max(uy1, g.y0+res*float64(g.ny))
	}
	x0 := math.Max(ux0, math.Floor(bb[0]/res)*res)
	y0 := math.Max(uy0, math.Floor(bb[1]/res)*res)
	x1 := math.Min(ux1, math.Ceil(bb[2]/res)*res)
	y1 := math.Min(uy1, math.Ceil(bb[3]/res)*res)
	nx, ny := int(math.Round((x1-x0)/res)), int(math.Round((y1-y0)/res))
	if nx < 2 || ny < 2 || nx*ny > 4_000_000 {
		return nil
	}
	gx0, gy0 := int(math.Round(x0/res)), int(math.Round(y0/res))
	// Which grid speaks for a cell. Hard ownership (smallest grid with a
	// value) still left seams: each park's front is measured from ITS
	// CATCHMENT's fires only (protected_area_id, fire.md F10), so a grid's
	// margin outside the catchment is a 60 km-window extrapolation that
	// contradicts the neighbour by 20–40 days. So the surface is FEATHERED:
	// a grid's weight at a cell is its depth from its own edge, saturating
	// at featherCells (≈ the window radius, where the edge bias ends); the
	// value is the weighted mean. One grid alone → its value; the overlap
	// → a transition, not a step. `owner` (for the inventory) is the
	// heaviest voice.
	const featherCells = 12
	order := make([]*mosaicGrid, len(grids))
	copy(order, grids)
	sort.Slice(order, func(i, j int) bool { return order[i].smallerThan(order[j].ownGrid) })
	// reference calendar
	var refStart float64
	refSeason, refStartISO := "", ""
	for _, g := range grids {
		if g.area == ref {
			refStart, refSeason, refStartISO = g.startDay, g.season, g.start
		}
	}
	if refStartISO == "" {
		count := map[string]int{}
		for _, g := range grids {
			count[g.start]++
		}
		best := -1
		for _, g := range grids {
			if count[g.start] > best || (count[g.start] == best && g.start > refStartISO) {
				best, refStart, refSeason, refStartISO = count[g.start], g.startDay, g.season, g.start
			}
		}
	}
	n := nx * ny
	v := make([]float64, n)
	mask := make([]bool, n)
	owner := make([]int8, n)
	for i := range owner {
		owner[i] = -1
	}
	any := false
	lo, hi := math.Inf(1), math.Inf(-1)
	for iy := 0; iy < ny; iy++ {
		for ix := 0; ix < nx; ix++ {
			gx, gy := gx0+ix, gy0+iy
			i := iy*nx + ix
			v[i] = math.NaN()
			var num, den, best float64
			for k, g := range order {
				jx, jy := gx-g.gx0, gy-g.gy0
				if jx < 0 || jy < 0 || jx >= g.nx || jy >= g.ny {
					continue
				}
				j := jy*g.nx + jx
				if 2*j+1 >= len(g.front) {
					continue
				}
				f := int16(uint16(g.front[2*j]) | uint16(g.front[2*j+1])<<8)
				if f < 0 {
					continue
				}
				depth := math.Min(math.Min(float64(jx), float64(g.nx-1-jx)), math.Min(float64(jy), float64(g.ny-1-jy))) + 1
				w := math.Min(depth, featherCells) / featherCells
				d := g.startDay + float64(f) - refStart // day on the reference's calendar
				num += w * d
				den += w
				if w > best && k < 127 {
					best, owner[i] = w, int8(k)
				}
			}
			if den > 0 {
				d := num / den
				v[i], mask[i], any = d, true, true
				lo, hi = math.Min(lo, d), math.Max(hi, d)
			}
		}
	}
	if !any {
		return nil
	}
	// levels every 5 d on the reference's calendar (labelled every 15, as fire_front.py)
	const step, labelStep = 5, 15
	l0, l1 := int(math.Floor(lo/step))*step, int(math.Ceil(hi/step))*step
	refT, _ := parseISODate(refStartISO)
	feats := make([]speedFeat, 0, (l1-l0)/step+1)
	for lvl := l0; lvl <= l1; lvl += step {
		if lines != "all" && (lvl%labelStep != 0 || (lines == "30" && lvl%30 != 0)) {
			continue
		}
		segs := marchingSquares(v, nx, ny, x0, y0, res, float64(lvl))
		if len(segs) == 0 {
			continue
		}
		mlines := make([][][]float64, 0, len(segs))
		for _, sg := range segs {
			ln := make([][]float64, len(sg))
			for k, p := range sg {
				ln[k] = []float64{p[0], p[1]}
			}
			mlines = append(mlines, ln)
		}
		d := refT.AddDate(0, 0, lvl)
		f := speedFeat{Type: "Feature"}
		f.Geometry, _ = json.Marshal(mls{"MultiLineString", mlines})
		f.Properties.Dos = lvl
		f.Properties.Date = d.Format("2006-01-02")
		f.Properties.Label = lvl%labelStep == 0
		f.Properties.Text = fmt.Sprintf("%d %s", d.Day(), d.Format("Jan"))
		feats = append(feats, f)
	}
	areas := make([]map[string]interface{}, 0, len(grids))
	cells := make(map[int8]int)
	for _, o := range owner {
		if o >= 0 {
			cells[o]++
		}
	}
	for k, g := range order {
		if cells[int8(k)] == 0 {
			continue
		}
		areas = append(areas, map[string]interface{}{"area": g.area, "season": g.season, "season_start": g.start, "cells": cells[int8(k)]})
	}
	out := map[string]interface{}{
		"season": refSeason, "season_start": refStartISO, "calendar_of": ref,
		"grid":     map[string]interface{}{"x0": x0, "y0": y0, "res": res, "nx": nx, "ny": ny},
		"contours": feats,
		"owners":   areas, // which park's front each stretch of ground came from, by cell count
		"words": "One arrival-time surface over the view: each 2.5 km cell blends the season fronts of the park grids covering it, each weighted by " +
			"its depth from its own grid edge (a park's front is measured from its catchment's fires, so its margin is the least trustworthy), on one calendar, " +
			"contoured every 5 days. A line crosses park seams; where two parks disagree the surface passes between them.",
	}
	if wantSpeed {
		speed := speedOfSurface(v, mask, nx, ny, res, y0)
		if sc, sst := splitBySpeed(feats, speed, nx, ny, res, x0, y0); sc != nil {
			out["speed_contours"], out["speed_stats"] = sc, sst
		}
	}
	return out
}
