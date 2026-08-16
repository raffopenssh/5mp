package srv

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"os"
	"strconv"
)

// Traced linear features + captured point symbols from the Sudan 1:250k
// historical sheets (scripts/histmaps/trace_lines.py: vision-LLM trace ->
// snap-to-ink refine -> dedupe -> cross-tile stitch). Like the labels, the
// pipeline is resumable and this data is routinely PARTIAL while a run is in
// flight: every response carries `progress` and `complete` (invariant #1).

// histLinesProgress reports how much of the trace run has landed.
func histLinesProgress(db *sql.DB) (done, total, nLines int) {
	db.QueryRow(`SELECT count(*), COALESCE(sum(state IN ('done','blank')),0) FROM line_tiles`).Scan(&total, &done)
	db.QueryRow(`SELECT count(*) FROM lines_stitched`).Scan(&nLines)
	return
}

func histHasTable(db *sql.DB, name string) bool {
	var n int
	db.QueryRow(`SELECT count(*) FROM sqlite_master WHERE name=?`, name).Scan(&n)
	return n > 0
}

type histLine struct {
	Kind     string          `json:"kind"`
	Style    string          `json:"style,omitempty"`
	Name     string          `json:"name,omitempty"`
	YearMin  int             `json:"year_min,omitempty"`
	YearMax  int             `json:"year_max,omitempty"`
	LengthKm float64         `json:"length_km"`
	Pts      json.RawMessage `json:"pts"` // [[lon,lat],...]
}

// HandleAPIHistMapLines answers "what routes/rivers/boundaries does the
// 1930s map draw here".
//
//	GET /api/histmap/sudan250k/lines?bbox=W,S,E,N [&kind=track|road|railway|telegraph|watercourse|boundary] [&limit=]
func (s *Server) HandleAPIHistMapLines(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	db, err := histLabels.open()
	if err != nil || !histHasTable(db, "lines_stitched") {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]any{"available": false,
			"reason": "traced lines not built (scripts/histmaps/trace_lines.py)"})
		return
	}
	qs := r.URL.Query()
	b := parseFloatCSV(qs.Get("bbox"), 4)
	nameQ := qs.Get("q")
	if b == nil && nameQ == "" {
		http.Error(w, `need bbox=W,S,E,N or q=<name>`, http.StatusBadRequest)
		return
	}
	limit := 500
	if v := r.URL.Query().Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 2000 {
			limit = n
		}
	}
	// bbox intersect on indexed min/max columns (sargable, invariant #3)
	sqlq := `SELECT kind, COALESCE(style,''), COALESCE(name,''),
	        COALESCE(year_min,0), COALESCE(year_max,0), length_km, pts
	        FROM lines_stitched WHERE 1=1`
	args := []any{}
	if b != nil {
		sqlq += " AND maxlon >= ? AND minlon <= ? AND maxlat >= ? AND minlat <= ?"
		args = append(args, b[0], b[2], b[1], b[3])
	}
	if nameQ != "" {
		sqlq += " AND (name LIKE ? OR names_all LIKE ?)"
		args = append(args, "%"+nameQ+"%", "%"+nameQ+"%")
	}
	if v := qs.Get("kind"); v != "" {
		sqlq += " AND kind = ?"
		args = append(args, v)
	}
	sqlq += " ORDER BY length_km DESC LIMIT ?"
	args = append(args, limit+1)

	rows, err := db.Query(sqlq, args...)
	if err != nil {
		http.Error(w, "query failed: "+err.Error(), http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	lines := []histLine{}
	for rows.Next() {
		var l histLine
		var pts string
		if err := rows.Scan(&l.Kind, &l.Style, &l.Name, &l.YearMin, &l.YearMax, &l.LengthKm, &pts); err == nil {
			l.Pts = json.RawMessage(pts)
			lines = append(lines, l)
		}
	}
	truncated := false
	if len(lines) > limit {
		lines = lines[:limit] // longest-first: the cut drops the shortest, not a corner
		truncated = true
	}
	done, total, nAll := histLinesProgress(db)
	json.NewEncoder(w).Encode(map[string]any{
		"available": true,
		"lines":     lines,
		"count":     len(lines),
		"truncated": truncated,
		"complete":  total > 0 && done == total,
		"progress":  map[string]int{"tiles_done": done, "tiles_total": total, "lines_total": nAll},
		"attribution": "Traced from Sudan Survey 1:250,000 (LOC g8310m.gct00289); " +
			"machine traced -- verify against the sheet before citing",
	})
}

// HandleAPIHistMapAround is the one-call answer to "what did the 1930s map
// say HERE" -- labels + traced lines + captured symbols + surveyor notes
// near a point, in one self-describing JSON. Built for LLM consumption:
// no follow-up calls needed, every layer states its own completeness.
//
//	GET /api/histmap/sudan250k/around?lon=..&lat=..[&radius_km=10]
func (s *Server) HandleAPIHistMapAround(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	db, err := histLabels.open()
	if err != nil {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]any{"available": false, "reason": err.Error()})
		return
	}
	q := r.URL.Query()
	lon, err1 := strconv.ParseFloat(q.Get("lon"), 64)
	lat, err2 := strconv.ParseFloat(q.Get("lat"), 64)
	if err1 != nil || err2 != nil {
		http.Error(w, "need lon= and lat=", http.StatusBadRequest)
		return
	}
	radiusKm := 10.0
	if v := q.Get("radius_km"); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil && f > 0 && f <= 100 {
			radiusKm = f
		}
	}
	d := radiusKm / 111.0
	minLon, maxLon, minLat, maxLat := lon-d, lon+d, lat-d, lat+d
	out := map[string]any{"available": true, "lon": lon, "lat": lat, "radius_km": radiusKm}

	// sheet + survey year under the point
	if histHasTable(db, "sheets") {
		var id, title string
		var year int
		err := db.QueryRow(`SELECT id, title, year FROM sheets
			WHERE ? BETWEEN minlon AND maxlon AND ? BETWEEN minlat AND maxlat
			LIMIT 1`, lon, lat).Scan(&id, &title, &year)
		if err == nil {
			out["sheet"] = map[string]any{"id": id, "title": title, "survey_year": year}
		}
	}

	// labels nearest-first (notes surfaced separately: surveyor observations
	// are the layer humans and LLMs quote, so they get their own key)
	table := histLabelsTable(db)
	catCol := "''"
	if histLabelsHasCategory(db, table) {
		catCol = "COALESCE(category,'')"
	}
	topicCol, topicFilter := "''", ""
	if histLabelsHasColumn(db, table, "note_topic") {
		topicCol = "COALESCE(note_topic,'')"
		// note_topic='fragment' = OCR debris that survived as category=note;
		// suppressed here like junk (kept in DB and full /labels endpoint).
		topicFilter = " AND COALESCE(note_topic,'') != 'fragment'"
	}
	rows, err := db.Query(`SELECT text, kind, `+catCol+`, `+topicCol+`, lon, lat FROM `+table+`
		WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?
		AND COALESCE(category,'') NOT IN ('junk','collar')`+topicFilter+`
		ORDER BY (lon-?)*(lon-?)+(lat-?)*(lat-?) LIMIT 120`,
		minLon, maxLon, minLat, maxLat, lon, lon, lat, lat)
	if err == nil {
		labels := []histLabel{}
		notes := []histLabel{}
		for rows.Next() {
			var l histLabel
			if rows.Scan(&l.Text, &l.Kind, &l.Category, &l.NoteTopic, &l.Lon, &l.Lat) == nil {
				if l.Category == "note" {
					notes = append(notes, l)
				} else {
					labels = append(labels, l)
				}
			}
		}
		rows.Close()
		out["labels"] = labels
		out["surveyor_notes"] = notes
	}

	// traced lines crossing the window. Geometry is CLIPPED to the asked
	// window: a 2,000 km river must not dump its whole course into a 10 km
	// question (the /lines endpoint serves full geometry when wanted).
	if histHasTable(db, "lines_stitched") {
		rows, err := db.Query(`SELECT kind, COALESCE(style,''), COALESCE(name,''),
			COALESCE(year_min,0), COALESCE(year_max,0), length_km, pts FROM lines_stitched
			WHERE maxlon >= ? AND minlon <= ? AND maxlat >= ? AND minlat <= ?
			ORDER BY length_km DESC LIMIT 60`, minLon, maxLon, minLat, maxLat)
		if err == nil {
			type nearLine struct {
				Kind        string          `json:"kind"`
				Style       string          `json:"style,omitempty"`
				Name        string          `json:"name,omitempty"`
				YearMin     int             `json:"year_min,omitempty"`
				YearMax     int             `json:"year_max,omitempty"`
				LengthKm    float64         `json:"length_km"`
				PtsInWindow json.RawMessage `json:"pts_in_window"`
				Clipped     bool            `json:"clipped,omitempty"`
			}
			lines := []nearLine{}
			for rows.Next() {
				var l nearLine
				var pts string
				if rows.Scan(&l.Kind, &l.Style, &l.Name, &l.YearMin, &l.YearMax, &l.LengthKm, &pts) != nil {
					continue
				}
				var coords [][2]float64
				if json.Unmarshal([]byte(pts), &coords) != nil {
					continue
				}
				kept := make([][2]float64, 0, 32)
				for i, p := range coords {
					in := p[0] >= minLon && p[0] <= maxLon && p[1] >= minLat && p[1] <= maxLat
					if in {
						// keep one neighbour either side so clipped runs
						// still reach the window edge
						if len(kept) == 0 && i > 0 {
							kept = append(kept, coords[i-1])
						}
						kept = append(kept, p)
					} else if len(kept) > 0 && kept[len(kept)-1] == coords[i-1] && i > 0 {
						kept = append(kept, p)
					}
				}
				if len(kept) == 0 {
					continue // bbox brushed it but no vertex inside
				}
				l.Clipped = len(kept) < len(coords)
				j, _ := json.Marshal(kept)
				l.PtsInWindow = j
				lines = append(lines, l)
			}
			rows.Close()
			out["lines"] = lines
		}
	}

	// captured point symbols (wells, cairns, camps...)
	if histHasTable(db, "symbols") {
		rows, err := db.Query(`SELECT descr, COALESCE(category,''),
			COALESCE(name,''), lon, lat FROM symbols
			WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?
			AND COALESCE(category,'') NOT IN ('junk')
			ORDER BY (lon-?)*(lon-?)+(lat-?)*(lat-?) LIMIT 60`,
			minLon, maxLon, minLat, maxLat, lon, lon, lat, lat)
		if err == nil {
			type sym struct {
				Descr    string  `json:"descr"`
				Category string  `json:"category,omitempty"`
				Name     string  `json:"name,omitempty"`
				Lon      float64 `json:"lon"`
				Lat      float64 `json:"lat"`
			}
			syms := []sym{}
			for rows.Next() {
				var s sym
				if rows.Scan(&s.Descr, &s.Category, &s.Name, &s.Lon, &s.Lat) == nil {
					syms = append(syms, s)
				}
			}
			rows.Close()
			out["symbols"] = syms
		}
	}

	// honest completeness, per layer
	ld, lt, _ := histLabelsProgress(db)
	out["labels_complete"] = lt > 0 && ld == lt
	if histHasTable(db, "line_tiles") {
		td, tt, _ := histLinesProgress(db)
		out["lines_complete"] = tt > 0 && td == tt
		out["lines_progress"] = map[string]int{"tiles_done": td, "tiles_total": tt}
	}
	out["attribution"] = "Sudan Survey 1:250,000 (LOC g8310m.gct00289); machine transcription/tracing -- verify against the sheet before citing"
	json.NewEncoder(w).Encode(out)
}

// HandleAPIHistMapLinesDownload serves the batch GeoJSON export of the
// stitched lines (written by export_labels.sh alongside the labels GPKG --
// one consistent snapshot, invariant #8). The GPKG download already carries
// the lines as a layer; this is the standalone GeoJSON.
func (s *Server) HandleAPIHistMapLinesDownload(w http.ResponseWriter, r *http.Request) {
	f, err := os.Open("data/histmaps/sudan250k_lines.geojson.gz")
	if err != nil {
		http.Error(w, "lines export not built (scripts/histmaps/export_labels.sh)", http.StatusNotFound)
		return
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		http.Error(w, "stat failed", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/gzip")
	w.Header().Set("Content-Disposition", `attachment; filename="sudan250k_lines.geojson.gz"`)
	http.ServeContent(longDownload(w), r, "sudan250k_lines.geojson.gz", st.ModTime(), f)
}
