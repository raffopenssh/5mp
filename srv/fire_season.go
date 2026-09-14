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
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"
)

func (s *Server) HandleAPIFireSeason(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	area := strings.TrimSpace(q.Get("area"))
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
	)
	err = s.DB.QueryRowContext(r.Context(), `
		SELECT season_start, complete, start_month, latest_day, contours_json, stats_json, computed_at,
		       nx, ny, front, usual
		FROM fire_season_front WHERE area_id = ? AND season = ?`, area, want).
		Scan(&seasonStart, &complete, &startMonth, &latest, &contours, &stats, &computedAt,
			&nx, &ny, &frontBlob, &usualBlob)
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
				frontPct, usualOffset = frontProgress(frontBlob, usualBlob, nx, ny, d)
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
		SELECT COUNT(*) FROM feature_geometries
		WHERE feature_type='fire_trajectory' AND vanguard=1 AND park_id=?
		  AND start_date >= ? AND start_date <= ?`, area, seasonStart.String, sEnd).Scan(&nVan)
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
			WHERE vanguard=1 AND park_id=? AND start_date >= ? AND start_date <= ?`, area, from, to).Scan(&n)
		nVanWin = n
	}
	var contoursOut json.RawMessage
	if q.Get("summary") == "" {
		contoursOut = json.RawMessage(orNull(contours))
	}
	json.NewEncoder(w).Encode(map[string]interface{}{
		"area":               area,
		"vanguard_groups":    nVan,
		"vanguard_in_window": nVanWin,
		"at_dos":             atDos,
		"front_reached_pct":  frontPct,
		"usual_offset_days":  usualOffset,
		"season":          want,
		"seasons":         seasons,
		"season_start":    seasonStart.String,
		"start_month":     startMonth,
		"complete":        complete == 1,
		"latest_day":      latest.String,
		"computed_at":     computedAt,
		"stats":           json.RawMessage(orNull(stats)),
		"contours":        contoursOut,
	})
}

// frontProgress reads the packed int16 day-of-season grids (scripts/
// fire_front.py pack(): -1 = no front) and answers, for day-of-season d:
// the share of front-bearing cells reached by d, and the median (front −
// usual) over those cells where both are known. nil = nothing to measure.
func frontProgress(front, usual []byte, nx, ny, d int) (*float64, *float64) {
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
		WHERE vanguard = 1
		  AND bbox_maxx >= ? AND bbox_minx <= ? AND bbox_maxy >= ? AND bbox_miny <= ?
		  AND start_date >= ? AND start_date <= ?
		  AND (end_date IS NULL OR end_date >= ?)`+
		aoiScopeSQL("park_id", s.aoiScopeParam(r)),
		bbox[0], bbox[2], bbox[1], bbox[3], scanFrom, to, from).Scan(&total)
	rows, err := s.DB.QueryContext(r.Context(), `
		SELECT feature_id, park_id, geojson, traj_days, properties_json,
		       COALESCE(start_date,''), COALESCE(end_date,'')
		FROM feature_geometries INDEXED BY idx_fg_vanguard
		WHERE vanguard = 1
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
		Tier      string       `json:"tier,omitempty"` // evidence_tier: the width the map draws (supported/weak wider)
		AheadKm   float64      `json:"ahead_km,omitempty"`
		AheadDays int          `json:"ahead_days,omitempty"`
		Season    string       `json:"season,omitempty"`
		Fires     int          `json:"fires,omitempty"`
		Days      int          `json:"days,omitempty"`
		Start     string       `json:"start,omitempty"`
		End       string       `json:"end,omitempty"`
	}
	out := make([]vanGroup, 0, 256)
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
			GroupType string  `json:"group_type"`
			Km        float64 `json:"distance_km"`
			Kmd       float64 `json:"avg_speed_km_day"`
			Fires     int     `json:"fires_total"`
			Days      int     `json:"days"`
			Leads     []*int  `json:"leads"`
			LeadStart *int    `json:"lead_start"`
			LeadBasis string  `json:"lead_basis"`
			AheadKm   float64 `json:"ahead_km"`
			AheadDays int     `json:"ahead_days"`
			Season    string  `json:"fire_season"`
			Tier      string  `json:"evidence_tier"`
		}
		if json.Unmarshal([]byte(propsJSON), &props) == nil {
			g.Type, g.Km, g.Kmd, g.Fires, g.Days = props.GroupType, props.Km, props.Kmd, props.Fires, props.Days
			g.Leads, g.LeadStart, g.LeadBasis, g.Tier = props.Leads, props.LeadStart, props.LeadBasis, props.Tier
			g.AheadKm, g.AheadDays, g.Season = props.AheadKm, props.AheadDays, props.Season
		}
		out = append(out, g)
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, max-age=600")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"from": from, "to": to, "limit": limit,
		"count": len(out), "total": total, "truncated": total > len(out),
		"lead_days": 10, "lead_max_days": 60, // VANGUARD_LEAD_DAYS/_MAX in scripts/fire_front.py — the flag is written there
		"groups": out,
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
