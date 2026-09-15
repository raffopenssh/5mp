package srv

// Early-burn ground — "traditional early-burn ground": the cells of an
// area's front grid whose FIRST burn of the season came >= ahead_days before
// the local season front in >= min_share of the complete seasons held (and
// in at least min_early of them). Where the season ENTERS, season after
// season: routes, boundary burns, village rings. Written only by
// scripts/fire_front.py (table fire_early_ground, migration 069); read here
// and served inside /api/fire-season (`early=1`) so the layer rides the same
// area/season resolution as the front, and by the exports.
//
// Chosen over the early-season density by scripts/eval_fire_baseline.py on
// the leading-edge target (docs/agents/fire.md "Early-burn ground").

import (
	"database/sql"
	"encoding/json"
	"math"
)

// earlyGroundCell is one cell as the exports read it. The wire to the map is
// the compact array form ([ix, iy, early, held, days_ahead, month, usual_dos]).
type earlyGroundCell struct {
	IX, IY     int
	Early      int // seasons the cell burned early
	Held       int // seasons the cell carried a front
	DaysAhead  int // median days before the local front, over the early seasons
	MonthMode  int // calendar month of the first burn, mode over the early seasons (0 = unknown)
	UsualDos   int // median front day-of-season over the seasons held (-1 = none)
	FirstBurn  *int
	W, S, E, N float64 // footprint (degrees)
}

// earlyGroundRow is the stored row plus the derived answer for one season.
type earlyGroundRow struct {
	Area        string
	Status      string // "ok" | "insufficient" | "not yet computed"
	Rule        string
	AheadDays   int
	MinShare    float64
	MinEarly    int
	SeasonsHeld int
	Seasons     []string
	Res         float64
	X0, Y0      float64
	NX, NY      int
	Cells       [][]int
	FirstBurn   []*int // for the season asked, aligned with Cells; nil = none stored
	Stats       map[string]interface{}
	ComputedAt  string
}

// earlyGround reads the area's row. season selects which first-burn column
// rides along ("" = none). A missing row is "not yet computed" (the rotation
// has not reached the area), a row without cells is what the writer said
// (status + reason in Stats) — three states, three words (invariant 1).
func (s *Server) earlyGround(area, season string) *earlyGroundRow {
	var (
		row                earlyGroundRow
		seasons, cells, fb sql.NullString
		stats              sql.NullString
		res, x0, y0        sql.NullFloat64
		nx, ny             sql.NullInt64
	)
	err := s.DB.QueryRow(`SELECT rule, ahead_days, min_share, seasons_held, seasons_json, res, x0, y0, nx, ny,
			cells_json, first_burn_json, stats_json, computed_at FROM fire_early_ground WHERE area_id = ?`, area).
		Scan(&row.Rule, &row.AheadDays, &row.MinShare, &row.SeasonsHeld, &seasons, &res, &x0, &y0, &nx, &ny, &cells, &fb, &stats, &row.ComputedAt)
	row.Area = area
	if err != nil {
		row.Status = "not yet computed"
		return &row
	}
	json.Unmarshal([]byte(seasons.String), &row.Seasons)
	if stats.Valid {
		json.Unmarshal([]byte(stats.String), &row.Stats)
	}
	row.MinEarly = 2
	if v, ok := row.Stats["min_early"].(float64); ok {
		row.MinEarly = int(v)
	}
	if st, ok := row.Stats["status"].(string); ok {
		row.Status = st
	} else {
		row.Status = "ok"
	}
	if !cells.Valid {
		if row.Status == "ok" {
			row.Status = "insufficient"
		}
		return &row
	}
	row.Res, row.X0, row.Y0, row.NX, row.NY = res.Float64, x0.Float64, y0.Float64, int(nx.Int64), int(ny.Int64)
	json.Unmarshal([]byte(cells.String), &row.Cells)
	if season != "" && fb.Valid {
		var all map[string][]*int
		if json.Unmarshal([]byte(fb.String), &all) == nil {
			if col, ok := all[season]; ok && len(col) == len(row.Cells) {
				row.FirstBurn = col
			}
		}
	}
	return &row
}

// wire is the object /api/fire-season carries under "early_ground". The
// season count is the writer's, never typed here; a status other than "ok"
// carries its reason and no cells.
func (r *earlyGroundRow) wire(season string) map[string]interface{} {
	out := map[string]interface{}{
		"status": r.Status, "rule": r.Rule, "ahead_days": r.AheadDays, "min_share": r.MinShare, "min_early": r.MinEarly,
		"seasons_held": r.SeasonsHeld, "seasons": r.Seasons, "computed_at": r.ComputedAt,
	}
	if r.Status != "ok" {
		if reason, ok := r.Stats["reason"].(string); ok {
			out["reason"] = reason
		} else if r.Status == "not yet computed" {
			out["reason"] = "the nightly rotation has not built this area's early-burn ground yet"
		}
		return out
	}
	out["grid"] = map[string]interface{}{"x0": r.X0, "y0": r.Y0, "res": r.Res, "nx": r.NX, "ny": r.NY}
	out["bbox"] = []float64{r.X0, r.Y0, r.X0 + r.Res*float64(r.NX), r.Y0 + r.Res*float64(r.NY)}
	out["cells"] = r.Cells // [ix, iy, early, held, days_ahead, month, usual_dos]
	out["cell_fields"] = []string{"ix", "iy", "early", "held", "days_ahead", "month", "usual_dos"}
	out["count"] = len(r.Cells)
	out["km2"] = math.Round(float64(len(r.Cells)) * r.cellKm2())
	out["km2_per_cell"] = math.Round(r.cellKm2()*100) / 100
	if v, ok := r.Stats["chance_cells"]; ok {
		out["chance_cells"] = v
	}
	if v, ok := r.Stats["cells_held"]; ok {
		out["cells_held"] = v
	}
	out["first_burn_season"] = nil
	out["first_burn"] = nil
	if r.FirstBurn != nil {
		out["first_burn_season"] = season
		out["first_burn"] = r.FirstBurn // day-of-season of this season's first detection per cell (null = not yet)
	}
	return out
}

func (r *earlyGroundRow) cellKm2() float64 {
	latMid := r.Y0 + r.Res*float64(r.NY)/2
	return r.Res * 111.0 * r.Res * 111.0 * math.Cos(latMid*math.Pi/180)
}

// footprints turns the compact cells into export rows with their true
// footprint in degrees. Every row names its basis (rule, thresholds).
func (r *earlyGroundRow) footprints() []earlyGroundCell {
	out := make([]earlyGroundCell, 0, len(r.Cells))
	for i, c := range r.Cells {
		if len(c) < 7 {
			continue
		}
		e := earlyGroundCell{IX: c[0], IY: c[1], Early: c[2], Held: c[3], DaysAhead: c[4], MonthMode: c[5], UsualDos: c[6]}
		e.W = r.X0 + r.Res*float64(c[0])
		e.E = e.W + r.Res
		e.S = r.Y0 + r.Res*float64(c[1])
		e.N = e.S + r.Res
		if r.FirstBurn != nil && i < len(r.FirstBurn) {
			e.FirstBurn = r.FirstBurn[i]
		}
		out = append(out, e)
	}
	return out
}

var monthNames = []string{"", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"}

// earlyGroundWords is the one sentence a tip, a KML description and a
// report share for a cell — every number derived from the row.
func earlyGroundWords(c earlyGroundCell, ruleAhead int) string {
	w := "Early-burn ground: first burn of the season came early here in "
	w += itoa(c.Early) + " of " + itoa(c.Held) + " seasons"
	if c.DaysAhead > 0 {
		w += ", typically ~" + itoa(c.DaysAhead) + " days before the local front"
	}
	if c.MonthMode >= 1 && c.MonthMode <= 12 {
		w += ", usually in " + monthNames[c.MonthMode]
	}
	w += " (rule: first burn >= " + itoa(ruleAhead) + " d ahead of the front)."
	return w
}
