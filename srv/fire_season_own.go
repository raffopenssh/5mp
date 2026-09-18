package srv

// Cell ownership for the front contours.
//
// Every park's front is computed on its own grid — its boundary plus a
// margin — and neighbours' margins cover the same ground twice, each with
// its own season calendar. Drawn together, the overlap is two families of
// isochrones crossing at an angle: a woven mesh that reads as noise, and
// (with speed=1) as patchy weight. The cell rasters solved this in the
// client (fireseason.js ownCells): a lattice cell belongs to the SMALLEST
// grid that has a value there, the same point → area rule as
// fireSeasonAreaAt. Here the same rule clips the LINES, server-side, so one
// piece of ground carries one front however many parks' grids cover it, and
// the reference and its neighbours cut along the same seams.
//
// All grids sit on one 0.025° lattice (x0/y0 are multiples of res up to
// float noise), so a vertex maps to a lattice cell once and each grid is
// asked "do you hold a value there" by integer offset.

import (
	"context"
	"encoding/json"
	"math"
)

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

// owns: does A own the ground under lon/lat, against `others`?
func ownsPoint(a *ownGrid, others []*ownGrid, lon, lat float64) bool {
	gx, gy := int(math.Floor(lon/a.res)), int(math.Floor(lat/a.res))
	for _, b := range others {
		if b == a || b.area == a.area || !b.smallerThan(a) {
			continue
		}
		if b.has(gx, gy) {
			return false
		}
	}
	return true
}

// clipLines keeps the stretches of each line A owns, cutting at the seam
// (the boundary vertex is kept on both sides only if both are owned — a
// seam is a gap of one segment, so two areas' lines never overdraw).
func clipLines(lines [][][]float64, a *ownGrid, others []*ownGrid) [][][]float64 {
	if len(others) == 0 {
		return lines
	}
	out := make([][][]float64, 0, len(lines))
	for _, line := range lines {
		var run [][]float64
		for _, pt := range line {
			if len(pt) >= 2 && ownsPoint(a, others, pt[0], pt[1]) {
				run = append(run, pt)
				continue
			}
			if len(run) >= 2 {
				out = append(out, run)
			}
			run = nil
		}
		if len(run) >= 2 {
			out = append(out, run)
		}
	}
	return out
}

// clipContourJSON applies clipLines to a stored contours_json (or any
// feature list of MultiLineStrings) and returns the features that kept
// any geometry, re-encoded. `raw` untouched when nothing outranks A.
func clipContourJSON(raw string, a *ownGrid, others []*ownGrid) json.RawMessage {
	if raw == "" {
		return nil
	}
	any := false
	for _, b := range others {
		if b.area != a.area && b.smallerThan(a) {
			any = true
			break
		}
	}
	if !any {
		return json.RawMessage(raw)
	}
	var feats []speedFeat
	if json.Unmarshal([]byte(raw), &feats) != nil {
		return json.RawMessage(raw)
	}
	keep := make([]speedFeat, 0, len(feats))
	for _, f := range feats {
		var g mls
		if json.Unmarshal(f.Geometry, &g) != nil || g.Type != "MultiLineString" {
			keep = append(keep, f)
			continue
		}
		lines := clipLines(g.Coordinates, a, others)
		if len(lines) == 0 {
			continue
		}
		f.Geometry, _ = json.Marshal(mls{"MultiLineString", lines})
		keep = append(keep, f)
	}
	b, _ := json.Marshal(keep)
	return b
}

func clipSpeedFeats(feats []speedFeat, a *ownGrid, others []*ownGrid) []speedFeat {
	keep := make([]speedFeat, 0, len(feats))
	for _, f := range feats {
		var g mls
		if json.Unmarshal(f.Geometry, &g) != nil {
			continue
		}
		lines := clipLines(g.Coordinates, a, others)
		if len(lines) == 0 {
			continue
		}
		f.Geometry, _ = json.Marshal(mls{"MultiLineString", lines})
		keep = append(keep, f)
	}
	return keep
}

// ownershipGrids loads, for every PARK whose front grid intersects the box,
// the grid + front of the season `at` falls in (else its latest) — the set
// a clip is judged against. AOIs are not in it: an AOI is the larger
// question and is drawn whole over whatever parks it covers.
func (s *Server) ownershipGrids(ctx context.Context, bb [4]float64, at string) []*ownGrid {
	rows, err := s.DB.QueryContext(ctx, `
		SELECT area_id, season_start, season_end, nx, ny, res, x0, y0, front
		FROM fire_season_front
		WHERE x0 <= ? AND x0 + res * nx >= ? AND y0 <= ? AND y0 + res * ny >= ?
		ORDER BY area_id, season_start`, bb[2], bb[0], bb[3], bb[1])
	if err != nil {
		return nil
	}
	defer rows.Close()
	pick := map[string]*ownGrid{}
	held := map[string]bool{} // area → its pick holds `at`
	var order []string
	for rows.Next() {
		var (
			area, st, en string
			nx, ny       int
			res, x0, y0  float64
			front        []byte
		)
		if rows.Scan(&area, &st, &en, &nx, &ny, &res, &x0, &y0, &front) != nil || IsAOIID(area) {
			continue
		}
		g := newOwnGrid(area, nx, ny, res, x0, y0, front)
		if _, seen := pick[area]; !seen {
			order = append(order, area)
		} else if held[area] {
			continue
		}
		pick[area] = g
		held[area] = at != "" && st <= at && at <= en
	}
	out := make([]*ownGrid, 0, len(order))
	for _, a := range order {
		out = append(out, pick[a])
	}
	return out
}
