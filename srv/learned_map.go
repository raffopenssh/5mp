package srv

// GET /api/learned-map[?park_id=] -- what the learner has approved, as map
// geometry: runways as lines, landing zones and places as points, roads as
// lines, each carrying the evidence it was approved on. The map draws it by
// default (srv/static/learned.js); nothing has to be pinned or opened.
//
// Only APPROVED features are served, and only those the current learner has
// scored (evidence_json present) or a reviewer approved: a row a retired rule
// approved has no evidence to name, and a popup with no evidence is a claim.
//
// Learned features are derived from client patrol tracks, so the guard is the
// same as every other learned endpoint: outside the client tenant the answer
// is an empty collection (srv/test_env_guard.go).

import (
	"database/sql"
	"encoding/json"
	"net/http"
)

func (s *Server) HandleAPILearnedMap(w http.ResponseWriter, r *http.Request) {
	empty := map[string]any{"type": "FeatureCollection", "features": []any{}}
	if blockLearnedInTestEnv(w, r, empty) {
		return
	}
	ctx := r.Context()
	park := r.URL.Query().Get("park_id")
	feats := []map[string]any{}
	add := func(geom json.RawMessage, props map[string]any, evidence sql.NullString) {
		if evidence.Valid {
			var ev map[string]any
			if json.Unmarshal([]byte(evidence.String), &ev) == nil {
				for _, k := range []string{"events", "days", "trackers", "journeys", "landings", "takeoffs",
					"fixed_wing_journeys", "rotor_wing_journeys", "first_seen", "axis_deg", "axis_agreement",
					"known_airfield", "dwell_median_min", "near_osm_place", "on_known_road", "connected_ends", "idle_active_days"} {
					if v, ok := ev[k]; ok {
						props[k] = v
					}
				}
			}
		} else {
			props["unscored"] = true // approved by a reviewer before the learner kept evidence
		}
		feats = append(feats, map[string]any{"type": "Feature", "geometry": geom, "properties": props})
	}
	const where = ` WHERE status IN ('approved','auto_approved') AND (evidence_json IS NOT NULL OR status = 'approved') AND (? = '' OR park_id = ?)`

	rows, err := s.DB.QueryContext(ctx, `SELECT id, park_id, lat, lon, COALESCE(kind,'airfield'), length_m, line_geojson, status, score, last_seen, evidence_json
		FROM learned_airstrips`+where, park, park)
	if err != nil {
		http.Error(w, "query failed", http.StatusInternalServerError)
		return
	}
	for rows.Next() {
		var id int64
		var pid, kind, status string
		var lat, lon float64
		var length, score sql.NullFloat64
		var line, last, ev sql.NullString
		if rows.Scan(&id, &pid, &lat, &lon, &kind, &length, &line, &status, &score, &last, &ev) != nil {
			continue
		}
		props := func() map[string]any {
			p := map[string]any{"family": "airfield", "kind": kind, "id": id, "park_id": pid, "status": status, "last_seen": last.String}
			if score.Valid {
				p["score"] = score.Float64
			}
			// length_m is the observed ground-run extent; absent when unmeasured.
			if length.Valid && length.Float64 > 0 && line.Valid {
				p["length_m"] = length.Float64
			}
			return p
		}
		pt, _ := json.Marshal(map[string]any{"type": "Point", "coordinates": []float64{lon, lat}})
		add(pt, props(), ev)
		if line.Valid && kind == "airstrip" {
			p := props()
			p["runway"] = true
			add(json.RawMessage(line.String), p, ev)
		}
	}
	rows.Close()

	rows, err = s.DB.QueryContext(ctx, `SELECT id, park_id, lat, lon, COALESCE(place_type,'stop'), COALESCE(name,''), status, score, last_seen, evidence_json
		FROM learned_places`+where, park, park)
	if err == nil {
		for rows.Next() {
			var id int64
			var pid, kind, name, status string
			var lat, lon float64
			var score sql.NullFloat64
			var last, ev sql.NullString
			if rows.Scan(&id, &pid, &lat, &lon, &kind, &name, &status, &score, &last, &ev) != nil {
				continue
			}
			p := map[string]any{"family": "place", "kind": kind, "id": id, "park_id": pid, "status": status, "last_seen": last.String, "name": name}
			if score.Valid {
				p["score"] = score.Float64
			}
			pt, _ := json.Marshal(map[string]any{"type": "Point", "coordinates": []float64{lon, lat}})
			add(pt, p, ev)
		}
		rows.Close()
	}

	rows, err = s.DB.QueryContext(ctx, `SELECT id, park_id, geojson, length_m, status, score, last_seen, evidence_json
		FROM learned_roads`+where, park, park)
	if err == nil {
		for rows.Next() {
			var id int64
			var pid, gj, status string
			var length, score sql.NullFloat64
			var last, ev sql.NullString
			if rows.Scan(&id, &pid, &gj, &length, &status, &score, &last, &ev) != nil {
				continue
			}
			p := map[string]any{"family": "road", "kind": "road", "id": id, "park_id": pid, "status": status, "last_seen": last.String, "length_m": length.Float64}
			if score.Valid {
				p["score"] = score.Float64
			}
			add(json.RawMessage(gj), p, ev)
		}
		rows.Close()
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, no-store")
	json.NewEncoder(w).Encode(map[string]any{"type": "FeatureCollection", "features": feats,
		"source": "learned from patrol tracks", "complete": true})
}
