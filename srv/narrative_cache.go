package srv

import (
	"database/sql"
	"fmt"
	"log/slog"
	"sort"
	"time"
)

// Generic narrative cache — see db/migrations/045-narrative-cache.sql.
//
// Why this exists next to fire_narrative_cache rather than inside it: the fire
// cache holds v5 hash feature_ids that only scripts/precompute_narratives_v5.py
// can mint (the Single Writer Rule in AGENTS.md), so it must be written by that
// script and nothing else. Everything in here is a pure function of rows this
// process can read, so the cache can be filled lazily by the reader and
// invalidated by a fingerprint of its inputs. No writer has to know it exists.
//
// It is keyed by park_id, and an AOI id IS a park_id in every park-shaped
// table, so parks and AOIs share one code path.

// narrativeSourceRev fingerprints the rows a cached narrative is derived from.
// COUNT + MAX(id) catches inserts, deletes and the delete-then-reinsert that
// every rebuild does; it costs one indexed aggregate.
func (s *Server) narrativeSourceRev(table, parkID string) string {
	var n int64
	var maxID sql.NullInt64
	err := s.DB.QueryRow(
		`SELECT COUNT(*), MAX(id) FROM `+table+` WHERE park_id = ?`, parkID,
	).Scan(&n, &maxID)
	if err != nil {
		// An unfingerprintable source must never look unchanged, or a stale
		// answer freezes forever. A unique rev misses the cache every time.
		return "err-" + time.Now().UTC().Format(time.RFC3339Nano)
	}
	return fmt.Sprintf("%d:%d", n, maxID.Int64)
}

// getCachedNarrative returns the stored payload if it was computed from the
// same source revision. A mismatch is a miss, not an error.
func (s *Server) getCachedNarrative(parkID, kind, params, rev string) ([]byte, string, bool) {
	var payload, storedRev, computedAt string
	err := s.DB.QueryRow(`
		SELECT payload, source_rev, computed_at
		FROM narrative_cache
		WHERE park_id = ? AND kind = ? AND params = ?
	`, parkID, kind, params).Scan(&payload, &storedRev, &computedAt)
	if err != nil || storedRev != rev {
		return nil, "", false
	}
	return []byte(payload), computedAt, true
}

// putCachedNarrative stores a payload. Best-effort by design: this is a cache,
// so a locked database must slow the next request down, never fail this one.
//
// params is a date window, and a user dragging the time slider mints a new one
// each time — with 10 MB payloads that is unbounded growth in a 1.8 GB
// database. So each (park, kind) keeps only the few most recent windows.
const narrativeCacheKeepPerKind = 6

func (s *Server) putCachedNarrative(parkID, kind, params, rev string, payload []byte) {
	_, err := s.DB.Exec(`
		INSERT OR REPLACE INTO narrative_cache
			(park_id, kind, params, payload, source_rev, computed_at)
		VALUES (?, ?, ?, ?, ?, ?)
	`, parkID, kind, params, string(payload), rev, time.Now().UTC().Format(time.RFC3339))
	if err != nil {
		slog.Warn("narrative cache write failed", "park", parkID, "kind", kind, "error", err)
		return
	}
	if _, err := s.DB.Exec(`
		DELETE FROM narrative_cache
		WHERE park_id = ? AND kind = ? AND params NOT IN (
			SELECT params FROM narrative_cache
			WHERE park_id = ? AND kind = ?
			ORDER BY computed_at DESC LIMIT ?
		)`, parkID, kind, parkID, kind, narrativeCacheKeepPerKind); err != nil {
		slog.Warn("narrative cache prune failed", "park", parkID, "kind", kind, "error", err)
	}
}

// --- per-request geo memo -------------------------------------------------
//
// The deforestation narrative enriches every event with "what is near this
// point": nearby settlements (a bbox scan of osm_places) and nearby rivers (a
// bbox scan of park_rivers_hydro). Both are ±1.0° windows, so consecutive
// events a few hundred metres apart re-run almost exactly the same query.
//
// This memo answers those two questions per 0.25° cell instead of per event, by
// fetching a superset window (cell ± (1.0 + cell)) once and then filtering it to
// each event's true ±1.0° window in Go. That is EXACT, not approximate: the
// filtered set is the same set the direct query would have returned, so park
// narratives are byte-identical to before.
//
// The one place exactness could be lost is the rivers query's `ORDER BY
// stream_order DESC LIMIT 200`: if the superset itself were truncated we could
// miss a row the narrower window would have kept. So the superset is fetched
// with a larger limit and, if it hits it, the memo declines and the caller runs
// the original per-point query.
type geoMemo struct {
	s        *Server
	parkID   string
	places   map[string][]OSMPlace
	rivers   map[string][]HydroRiver
	riversNo map[string]bool // cells where the superset was truncated
}

const geoMemoCell = 0.25
const geoMemoPad = 1.0 // must match the ±1.0° windows in the direct queries
const geoMemoRiverLimit = 4000

func newGeoMemo(s *Server, parkID string) *geoMemo {
	return &geoMemo{s: s, parkID: parkID,
		places:   map[string][]OSMPlace{},
		rivers:   map[string][]HydroRiver{},
		riversNo: map[string]bool{}}
}

func memoCell(lat, lon float64) (float64, float64, string) {
	cl := float64(int(lat/geoMemoCell)) * geoMemoCell
	co := float64(int(lon/geoMemoCell)) * geoMemoCell
	return cl, co, fmt.Sprintf("%.2f/%.2f", cl, co)
}

// nearestPlaces mirrors findNearestPlaces for a fixed set of place types.
func (m *geoMemo) nearestPlaces(lat, lon float64, limit int, types []string, key string) []OSMPlace {
	cl, co, ck := memoCell(lat, lon)
	ck = key + "|" + ck
	rows, ok := m.places[ck]
	if !ok {
		q := `SELECT id, park_id, place_type, name, lat, lon FROM osm_places
		      WHERE park_id = ? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?`
		args := []interface{}{m.parkID,
			cl - geoMemoPad, cl + geoMemoCell + geoMemoPad,
			co - geoMemoPad, co + geoMemoCell + geoMemoPad}
		if len(types) > 0 {
			q += " AND place_type IN (?" + repeatComma(len(types)-1) + ")"
			for _, t := range types {
				args = append(args, t)
			}
		}
		r, err := m.s.DB.Query(q, args...)
		if err != nil {
			return nil
		}
		for r.Next() {
			var p OSMPlace
			if r.Scan(&p.ID, &p.ParkID, &p.PlaceType, &p.Name, &p.Lat, &p.Lon) == nil {
				rows = append(rows, p)
			}
		}
		r.Close()
		m.places[ck] = rows
	}
	var out []OSMPlace
	for _, p := range rows {
		if p.Lat < lat-geoMemoPad || p.Lat > lat+geoMemoPad ||
			p.Lon < lon-geoMemoPad || p.Lon > lon+geoMemoPad {
			continue
		}
		p.Distance = haversineDistance(lat, lon, p.Lat, p.Lon)
		out = append(out, p)
	}
	sortByDistance(out)
	if len(out) > limit {
		out = out[:limit]
	}
	return out
}

// nearestRivers mirrors findNearestRiverToPoint. ok=false means "ask the
// database directly" — the superset was truncated, so the memo cannot promise
// the same answer.
func (m *geoMemo) nearestRivers(lat, lon float64, limit int) ([]HydroRiver, bool) {
	cl, co, ck := memoCell(lat, lon)
	if m.riversNo[ck] {
		return nil, false
	}
	rows, ok := m.rivers[ck]
	if !ok {
		r, err := m.s.DB.Query(`
			SELECT COALESCE(name,''), COALESCE(length_km,0), 0, COALESCE(stream_order,0),
			       COALESCE(lat,0), COALESCE(lon,0)
			FROM park_rivers_hydro
			WHERE park_id = ? AND name != '' AND name IS NOT NULL
			  AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
			ORDER BY stream_order DESC LIMIT ?`,
			m.parkID,
			cl-geoMemoPad, cl+geoMemoCell+geoMemoPad,
			co-geoMemoPad, co+geoMemoCell+geoMemoPad, geoMemoRiverLimit)
		if err != nil {
			return nil, false
		}
		for r.Next() {
			var h HydroRiver
			if r.Scan(&h.Name, &h.LengthKm, &h.DischargeCMS, &h.StreamOrder,
				&h.CentroidLat, &h.CentroidLon) == nil {
				rows = append(rows, h)
			}
		}
		r.Close()
		if len(rows) >= geoMemoRiverLimit {
			m.riversNo[ck] = true
			return nil, false
		}
		m.rivers[ck] = rows
	}
	// Same shape as the direct query: window, then top-200 by stream order,
	// then nearest N of those.
	var win []HydroRiver
	for _, h := range rows {
		if h.CentroidLat < lat-geoMemoPad || h.CentroidLat > lat+geoMemoPad ||
			h.CentroidLon < lon-geoMemoPad || h.CentroidLon > lon+geoMemoPad {
			continue
		}
		win = append(win, h)
	}
	if len(win) > 200 {
		win = win[:200] // rows arrive stream_order DESC
	}
	for i := range win {
		win[i].DistanceKm = haversineDistance(lat, lon, win[i].CentroidLat, win[i].CentroidLon)
	}
	sortRiversByDistance(win)
	if len(win) > limit {
		win = win[:limit]
	}
	return win, true
}

func repeatComma(n int) string {
	out := ""
	for i := 0; i < n; i++ {
		out += ",?"
	}
	return out
}

func sortByDistance(p []OSMPlace) {
	sort.Slice(p, func(i, j int) bool { return p[i].Distance < p[j].Distance })
}

func sortRiversByDistance(r []HydroRiver) {
	sort.Slice(r, func(i, j int) bool { return r[i].DistanceKm < r[j].DistanceKm })
}
