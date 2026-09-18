package srv

// Speed as a property of the front lines.
//
// The season speed map (fire_season_speed.go) is 1/|∇T| of the front's
// arrival-time surface — and the isochrones ARE that surface, drawn every
// 5 days: where they crowd the front stalled, where they spread it raced.
// Drawn as a raster beside them the same number was a wall of seamed
// orange blocks (docs/agents/animator.md "Speed across parks"). Here it
// rides the contours themselves: `/api/fire-season?speed=1` splits every
// isochrone into runs of one speed class and returns them as
// `speed_contours` — the same features (dos/date/label/text) plus
// `kmd` (km/day, the class centre) and `cls` (0 = slowest). The client
// draws them thick where the front stalled and thin where it raced, at
// every zoom, with no second payload: a run boundary duplicates one vertex.
//
// Classes are log-spaced between the speed ramp's ends (1 … 50 km/d),
// SPEED_CLASSES of them; the class of a vertex is that of the cell under
// it, and a run shorter than minRun vertices takes its predecessor's class
// (the field is smoothed σ=2 cells, so a one-vertex flicker is not
// information). Stats (p10/median/p90 over the cells the front reached)
// ride along as `speed_stats` so the legend needs no second request.

import (
	"encoding/json"
	"math"
	"sort"
)

const speedClasses = 8

// speedClassOf: km/day → class 0..speedClasses-1 on the log ramp.
func speedClassOf(kmd float64) int {
	lo, hi := math.Log(speedStops[0].KmD), math.Log(speedStops[len(speedStops)-1].KmD)
	if kmd <= 0 {
		return 0
	}
	c := int(math.Floor((math.Log(kmd) - lo) / (hi - lo) * speedClasses))
	if c < 0 {
		c = 0
	}
	if c > speedClasses-1 {
		c = speedClasses - 1
	}
	return c
}

// speedClassKmd: the geometric centre of a class, km/day (1 decimal).
func speedClassKmd(c int) float64 {
	lo, hi := math.Log(speedStops[0].KmD), math.Log(speedStops[len(speedStops)-1].KmD)
	v := math.Exp(lo + (hi-lo)*(float64(c)+0.5)/speedClasses)
	return math.Round(v*10) / 10
}

type speedFeat struct {
	Type       string          `json:"type"`
	Geometry   json.RawMessage `json:"geometry"`
	Properties struct {
		Dos   int     `json:"dos"`
		Date  string  `json:"date"`
		Label bool    `json:"label"`
		Text  string  `json:"text"`
		Kmd   float64 `json:"kmd,omitempty"`
		Cls   *int    `json:"cls,omitempty"`
	} `json:"properties"`
}

type mls struct {
	Type        string        `json:"type"`
	Coordinates [][][]float64 `json:"coordinates"`
}

// speedContours returns the isochrones split by speed class (nil when the
// front has no gradient to read) and the field's stats over the cells the
// front reached. `contours` is the stored contours_json; `front` the int16
// arrival grid it was drawn from.
func speedContours(contours string, front []byte, nx, ny int, res, x0, y0 float64) ([]speedFeat, map[string]interface{}) {
	if contours == "" || nx == 0 || ny == 0 {
		return nil, nil
	}
	speed := seasonSpeed(front, nx, ny, res, y0)
	if speed == nil {
		return nil, nil
	}
	var feats []speedFeat
	if json.Unmarshal([]byte(contours), &feats) != nil {
		return nil, nil
	}
	return splitBySpeed(feats, speed, nx, ny, res, x0, y0)
}

// splitBySpeed is speedContours on parsed features and a ready speed field
// (km/day per cell, NaN off the front) — the mosaic path's entry.
func splitBySpeed(feats []speedFeat, speed []float64, nx, ny int, res, x0, y0 float64) ([]speedFeat, map[string]interface{}) {
	classAt := func(pt []float64) int {
		ix, iy := int(math.Floor((pt[0]-x0)/res)), int(math.Floor((pt[1]-y0)/res))
		if ix < 0 || iy < 0 || ix >= nx || iy >= ny {
			return -1
		}
		v := speed[iy*nx+ix]
		if math.IsNaN(v) {
			return -1
		}
		return speedClassOf(v)
	}
	const minRun = 4
	out := make([]speedFeat, 0, len(feats)*3)
	for _, f := range feats {
		var g mls
		if json.Unmarshal(f.Geometry, &g) != nil || g.Type != "MultiLineString" {
			continue
		}
		byClass := make([][][][]float64, speedClasses)
		for _, line := range g.Coordinates {
			if len(line) < 2 {
				continue
			}
			// class per vertex; a vertex off the field carries its predecessor's
			cls := make([]int, len(line))
			prev := -1
			for i, pt := range line {
				c := classAt(pt)
				if c < 0 {
					c = prev
				}
				cls[i], prev = c, c
			}
			for i := len(line) - 1; i >= 0; i-- { // a leading gap takes the first answer
				if cls[i] < 0 && i+1 < len(line) {
					cls[i] = cls[i+1]
				}
			}
			if cls[0] < 0 {
				continue // no field under this line at all
			}
			// runs; a short one joins its predecessor
			type run struct{ c, a, b int } // class, first vertex, last vertex (inclusive)
			var runs []run
			for i := 0; i < len(line); i++ {
				if len(runs) > 0 && runs[len(runs)-1].c == cls[i] {
					runs[len(runs)-1].b = i
					continue
				}
				runs = append(runs, run{cls[i], i, i})
			}
			merged := runs[:0]
			for _, r := range runs {
				if len(merged) > 0 && r.b-r.a+1 < minRun {
					merged[len(merged)-1].b = r.b
					continue
				}
				if len(merged) > 0 && merged[len(merged)-1].c == r.c {
					merged[len(merged)-1].b = r.b
					continue
				}
				merged = append(merged, r)
			}
			for k, r := range merged {
				a := r.a
				if k > 0 {
					a-- // share the boundary vertex so the runs join
				}
				if r.b-a < 1 {
					continue
				}
				byClass[r.c] = append(byClass[r.c], line[a:r.b+1])
			}
		}
		for c, lines := range byClass {
			if len(lines) == 0 {
				continue
			}
			geom, _ := json.Marshal(mls{"MultiLineString", lines})
			sf := speedFeat{Type: "Feature", Geometry: geom}
			sf.Properties = f.Properties
			sf.Properties.Kmd = speedClassKmd(c)
			cc := c
			sf.Properties.Cls = &cc
			out = append(out, sf)
		}
	}
	vals := make([]float64, 0, nx*ny)
	for _, v := range speed {
		if !math.IsNaN(v) {
			vals = append(vals, v)
		}
	}
	sort.Float64s(vals)
	pct := func(p float64) interface{} {
		if len(vals) == 0 {
			return nil
		}
		return math.Round(vals[int(p*float64(len(vals)-1))]*10) / 10
	}
	classes := make([]map[string]interface{}, speedClasses)
	for c := range classes {
		classes[c] = map[string]interface{}{"cls": c, "km_d": speedClassKmd(c)}
	}
	stats := map[string]interface{}{
		"cells": len(vals), "p10_km_d": pct(0.10), "median_km_d": pct(0.50), "p90_km_d": pct(0.90),
		"smoothing_sigma_cells": 2, "cell_km": math.Round(res*111*10) / 10,
		"classes": classes, "km_d_min": speedStops[0].KmD, "km_d_max": speedStops[len(speedStops)-1].KmD,
		"words": "Line weight is the speed of the season front, km/day, from the gradient of its arrival-time surface (smoothed σ=2 cells): thick where it stalled, thin where it raced. Descriptive, not a forecast.",
	}
	return out, stats
}
