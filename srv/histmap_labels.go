package srv

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
)

// OCR'd + geocoded labels from the Sudan 1:250k historical sheets.
//
// Produced by scripts/histmaps/ocr_labels.py (vision-LLM transcription of
// every sheet window, pixel centers mapped through each sheet's geotransform
// to WGS84). The pipeline is long-running and resumable, so this database is
// routinely *partial* while a run is in flight -- and a partial answer must
// not read as a complete one (AGENTS.md invariant #1). Every response
// therefore carries `progress` (tiles done/total) and `complete`, and the
// meta endpoint advertises the same.
//
// The pipeline writes WAL; we read with mode=ro and see the latest committed
// batch. `labels_dedup` (built by `ocr_labels.py dedupe`, collapses the
// overlap-window duplicates) is preferred when it exists AND the raw table
// has not grown since -- a stale dedup would silently hide new sheets, so
// staleness is checked, not assumed.

const histLabelsDefaultPath = "data/histmaps/labels.sqlite3"

type histLabelsStore struct {
	mu   sync.Mutex
	db   *sql.DB
	path string
}

var histLabels = &histLabelsStore{path: histLabelsDefaultPath}

func (h *histLabelsStore) open() (*sql.DB, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.db != nil {
		return h.db, nil
	}
	if _, err := os.Stat(h.path); err != nil {
		return nil, fmt.Errorf("histmap labels not built: %w", err)
	}
	db, err := sql.Open("sqlite", "file:"+h.path+"?mode=ro&_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, err
	}
	h.db = db
	return db, nil
}

// histLabelsProgress reports how much of the OCR run has landed.
func histLabelsProgress(db *sql.DB) (done, total, nLabels int) {
	db.QueryRow(`SELECT count(*), sum(state IN ('done','blank')) FROM tiles`).Scan(&total, &done)
	db.QueryRow(`SELECT count(*) FROM labels`).Scan(&nLabels)
	return
}

// histLabelsTable prefers the deduplicated table when it exists and is
// non-empty. dedupe is a batch pass (`ocr_labels.py dedupe`), so while the
// OCR run is still appending, labels_dedup lags the raw table -- callers can
// see which one answered via `source` and re-run dedupe to refresh it.
// histLabelsHasFTS reports whether the pipeline has built the labels_fts
// full-text index (added 2026-08-13; older databases lack it).
func histLabelsHasFTS(db *sql.DB) bool {
	var n int
	db.QueryRow(`SELECT count(*) FROM sqlite_master WHERE name='labels_fts'`).Scan(&n)
	return n > 0
}

func histLabelsTable(db *sql.DB) string {
	var hasDedup int
	db.QueryRow(`SELECT count(*) FROM sqlite_master WHERE name='labels_dedup'`).Scan(&hasDedup)
	if hasDedup == 0 {
		return "labels"
	}
	var dedupN int
	db.QueryRow(`SELECT count(*) FROM labels_dedup`).Scan(&dedupN)
	if dedupN == 0 {
		return "labels"
	}
	return "labels_dedup"
}

type histLabel struct {
	Text string  `json:"text"`
	Kind string  `json:"kind"`
	Lon  float64 `json:"lon"`
	Lat  float64 `json:"lat"`
	NSrc int     `json:"n_src,omitempty"`   // dedup only: sightings merged
	Sheet string `json:"sheet,omitempty"`   // raw only
}

// HandleAPIHistMapLabels answers "what does the 1930s map say here".
//
//	GET /api/histmap/sudan250k/labels?bbox=W,S,E,N            (or)
//	GET /api/histmap/sudan250k/labels?lon=..&lat=..&radius_km=..
//	optional: q= substring filter, kind= filter, limit= (default 500, max 5000)
//
// Results are sorted nearest-first when lon/lat given. `truncated` is set
// when the limit cut the answer (invariant #8: say you truncated).
func (s *Server) HandleAPIHistMapLabels(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	db, err := histLabels.open()
	if err != nil {
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]any{"available": false, "reason": err.Error()})
		return
	}

	q := r.URL.Query()
	var minLon, minLat, maxLon, maxLat float64
	var centerLon, centerLat float64
	hasCenter := false
	switch {
	case q.Get("bbox") != "":
		b := parseFloatCSV(q.Get("bbox"), 4)
		if b == nil {
			http.Error(w, `bbox must be "W,S,E,N"`, http.StatusBadRequest)
			return
		}
		minLon, minLat, maxLon, maxLat = b[0], b[1], b[2], b[3]
	case q.Get("lon") != "" && q.Get("lat") != "":
		lon, err1 := strconv.ParseFloat(q.Get("lon"), 64)
		lat, err2 := strconv.ParseFloat(q.Get("lat"), 64)
		if err1 != nil || err2 != nil {
			http.Error(w, "bad lon/lat", http.StatusBadRequest)
			return
		}
		radiusKm := 10.0
		if v := q.Get("radius_km"); v != "" {
			if f, err := strconv.ParseFloat(v, 64); err == nil && f > 0 && f <= 500 {
				radiusKm = f
			}
		}
		d := radiusKm / 111.0
		minLon, maxLon = lon-d, lon+d
		minLat, maxLat = lat-d, lat+d
		centerLon, centerLat, hasCenter = lon, lat, true
	default:
		http.Error(w, "need bbox=W,S,E,N or lon=&lat=[&radius_km=]", http.StatusBadRequest)
		return
	}

	limit := 500
	if v := q.Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 5000 {
			limit = n
		}
	}

	table := histLabelsTable(db)
	// col BETWEEN ? AND ? -- sargable on idx_labels_lonlat (invariant #3).
	sqlq := "SELECT text, kind, lon, lat, " +
		map[string]string{"labels": "0, sheet", "labels_dedup": "n_src, ''"}[table] +
		" FROM " + table +
		" WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?"
	args := []any{minLon, maxLon, minLat, maxLat}
	if v := strings.TrimSpace(q.Get("q")); v != "" {
		if table == "labels" && histLabelsHasFTS(db) {
			// FTS5 prefix match on the raw table: "hagar" finds "HAGAR MASUDI"
			// without a LIKE scan. Query text is passed as a bound parameter
			// inside a quoted-phrase prefix so user input cannot inject FTS
			// syntax.
			sqlq += " AND id IN (SELECT rowid FROM labels_fts WHERE labels_fts MATCH ?)"
			args = append(args, `"`+strings.ReplaceAll(v, `"`, `""`)+`"*`)
		} else {
			sqlq += " AND text LIKE ? ESCAPE '\\'"
			args = append(args, "%"+strings.NewReplacer("%", "\\%", "_", "\\_").Replace(v)+"%")
		}
	}
	if v := q.Get("kind"); v != "" {
		sqlq += " AND kind = ?"
		args = append(args, v)
	}
	if hasCenter {
		sqlq += " ORDER BY (lon-?)*(lon-?) + (lat-?)*(lat-?)"
		args = append(args, centerLon, centerLon, centerLat, centerLat)
	}
	sqlq += " LIMIT ?"
	args = append(args, limit+1) // +1 so truncation is detectable, not guessed

	rows, err := db.Query(sqlq, args...)
	if err != nil {
		http.Error(w, "query failed: "+err.Error(), http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	labels := []histLabel{}
	for rows.Next() {
		var l histLabel
		if err := rows.Scan(&l.Text, &l.Kind, &l.Lon, &l.Lat, &l.NSrc, &l.Sheet); err == nil {
			labels = append(labels, l)
		}
	}
	truncated := false
	if len(labels) > limit {
		labels = labels[:limit]
		truncated = true
	}

	done, total, nAll := histLabelsProgress(db)
	json.NewEncoder(w).Encode(map[string]any{
		"available": true,
		"source":    table, // "labels" (raw, may contain overlap duplicates) or "labels_dedup"
		"labels":    labels,
		"count":     len(labels),
		"truncated": truncated,
		"complete":  total > 0 && done == total,
		"progress":  map[string]int{"tiles_done": done, "tiles_total": total, "labels_total": nAll},
		"attribution": "OCR of Sudan Survey 1:250,000 (1908-1976), Library of Congress g8310m.gct00289; " +
			"machine transcription -- verify against the sheet before citing",
	})
}
