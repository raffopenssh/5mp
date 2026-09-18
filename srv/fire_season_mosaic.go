package srv

// One front over the viewport.
//
// Each park's season front is stored on its own grid (boundary + margin)
// as DAY OF SEASON on its own calendar. Drawn side by side those grids
// overlap; while each was measured from its catchment's fires alone the
// overlap was two families of isochrones crossing at an angle, and
// clipped to owned cells, lines that began from nowhere at every seam
// (2026-09-18: "starting lines from nowhere", "blocky"). The fix is
// upstream — fire_front.py measures every grid from every fire within a
// window's reach and on the landscape's calendar, so neighbours agree on
// shared ground (median |Δ| 0 d) — and this file is only the join.
//
// The bbox path draws ONE surface: every lattice cell in view takes the
// arrival day of the park grid that owns it (smallest grid with a value
// there — ownGrid below), converted to an ABSOLUTE day so calendars
// agree, and that mosaic is contoured once (marchingSquares, every 5 d on
// the reference's calendar). A line then runs across park seams as the
// front did. Speed (fire_season_lines.go) is the gradient of
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

// ownGrid: one park's stored front grid on the shared 0.025° lattice
// (x0/y0 are multiples of res up to float noise), so a lattice cell maps
// into any grid by integer offset. Ownership order: the SMALLEST grid with
// a value speaks for a cell — the point → area rule of fireSeasonAreaAt.
type ownGrid struct {
	area        string
	nx, ny      int
	res, x0, y0 float64
	front       []byte // int16 LE per cell; < 0 = no front
	gx0, gy0    int    // lattice index of cell (0,0)
}

func (g *ownGrid) n() int { return g.nx * g.ny }

// has reports whether g carries a front at lattice cell (gx, gy).
func (g *ownGrid) has(gx, gy int) bool {
	ix, iy := gx-g.gx0, gy-g.gy0
	if ix < 0 || iy < 0 || ix >= g.nx || iy >= g.ny {
		return false
	}
	i := iy*g.nx + ix
	if 2*i+1 >= len(g.front) {
		return false
	}
	return int16(uint16(g.front[2*i])|uint16(g.front[2*i+1])<<8) >= 0
}

func newOwnGrid(area string, nx, ny int, res, x0, y0 float64, front []byte) *ownGrid {
	return &ownGrid{area: area, nx: nx, ny: ny, res: res, x0: x0, y0: y0, front: front,
		gx0: int(math.Round(x0 / res)), gy0: int(math.Round(y0 / res))}
}

// smallerThan: B outranks A for a shared cell when B's grid is smaller (ties
// by id, so the rule is a total order and two grids never both yield).
func (b *ownGrid) smallerThan(a *ownGrid) bool {
	return b.n() < a.n() || (b.n() == a.n() && b.area < a.area)
}

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
	// Which grid speaks for a cell: the SMALLEST grid with a value there
	// (ownGrid.smallerThan). Since 2026-09-18 every park's front is measured
	// from every detection within a window's reach of its grid
	// (fire_front.py load_onsets), so two grids say the same day on shared
	// ground (median |delta| 0 d, Ruaha/Kitulo 3,738 cells) and a hard
	// join is seamless; the residual at a seam is two season calendars
	// (Ruaha Feb vs Kilombero Apr: 6 d), a real disagreement, drawn as a
	// small kink. An earlier version blended by depth-from-grid-edge and
	// drew the Chebyshev depth's axis-aligned isolines as staircases.
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
				d := g.startDay + float64(f) - refStart // day on the reference's calendar
				v[i], mask[i], any = d, true, true
				if k < 127 {
					owner[i] = int8(k)
				}
				lo, hi = math.Min(lo, d), math.Max(hi, d)
				break
			}
		}
	}
	if !any {
		return nil
	}
	near := seamRamp(v, mask, owner, nx, ny)
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
		"words": "One arrival-time surface over the view: each 2.5 km cell takes the season front of the smallest park grid covering it, on one calendar, " +
			"contoured every 5 days. Every park's front is measured from every fire within 60 km of its grid, so neighbours agree on shared ground and a line runs across park seams.",
	}
	if wantSpeed {
		speed := speedOfSurface(v, mask, nx, ny, res, y0)
		for i := range speed {
			if near[i] {
				speed[i] = math.NaN() // the ramp is a calendar step, not a stall: the run carries its neighbour's class
			}
		}
		if sc, sst := splitBySpeed(feats, speed, nx, ny, res, x0, y0); sc != nil {
			out["speed_contours"], out["speed_stats"] = sc, sst
		}
	}
	return out
}

// seamRamp turns the step at an owner boundary into a ramp. Two grids with
// different season calendars (Ruaha starts February, Kilombero April) put a
// cell's first burn of March in different seasons, so their fronts differ
// by a few days on shared ground; hard ownership then makes a contour run
// along the seam. Only cells within seamCells of a boundary are smoothed
// (normalised Gaussian, seamSigma) — everything else stays the stored
// value. Returns the cells touched, so speed does not read the ramp.
func seamRamp(v []float64, mask []bool, owner []int8, nx, ny int) []bool {
	const seamCells, seamSigma = 6, 2.0
	n := nx * ny
	near := make([]bool, n)
	var q []int
	for y := 0; y < ny; y++ {
		for x := 0; x < nx; x++ {
			i := y*nx + x
			if owner[i] < 0 {
				continue
			}
			if (x+1 < nx && owner[i+1] >= 0 && owner[i+1] != owner[i]) ||
				(y+1 < ny && owner[i+nx] >= 0 && owner[i+nx] != owner[i]) {
				for _, j := range []int{i, i + 1, i + nx} {
					if j < n && !near[j] && mask[j] {
						near[j] = true
						q = append(q, j)
					}
				}
			}
		}
	}
	if len(q) == 0 {
		return near
	}
	// dilate seamCells times (4-neighbourhood BFS)
	for step := 0; step < seamCells; step++ {
		var next []int
		for _, i := range q {
			x, y := i%nx, i/nx
			for _, j := range []int{i - 1, i + 1, i - nx, i + nx} {
				if j < 0 || j >= n || (j == i-1 && x == 0) || (j == i+1 && x == nx-1) || (j == i-nx && y == 0) || (j == i+nx && y == ny-1) {
					continue
				}
				if !near[j] && mask[j] {
					near[j] = true
					next = append(next, j)
				}
			}
		}
		q = next
	}
	sm := gaussianNC(v, mask, nx, ny, seamSigma)
	for i := range v {
		if near[i] && !math.IsNaN(sm[i]) {
			v[i] = sm[i]
		}
	}
	return near
}
