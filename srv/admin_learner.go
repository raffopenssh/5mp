package srv

// GET /api/admin/learner -- the admin panel's one view of the event learner.
//
// It replaced three tabs written for the counter learner (Pending Approvals,
// Learning Results, Learned Features). Those listed the first 100 of ~3,200
// pending rows as a to-do list, printed counter columns nothing updates any
// more ("13 matches"), and offered "approve everything above 75 %" -- which,
// once confidence_pct became a calibrated probability, would have approved
// 463 features the fitted thresholds (77-94 %) had deliberately left pending.
//
// What a reviewer needs from the event learner is different: what it decided,
// on what evidence, how far each undecided feature is from its family's
// threshold, and a way to overrule it. So every row carries its evidence, its
// score, the threshold it is measured against and the reason for its state,
// and the response names its total so a page is never mistaken for the list
// (AGENTS.md invariant 8).
//
// Same guard as every learned endpoint: outside the client tenant the answer
// is empty (srv/test_env_guard.go).

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"sort"
	"strconv"
	"strings"

	"srv.exe.dev/db/dbgen"
)

type adminLearnerFeature struct {
	Family string   `json:"family"` // airfield | place | road
	Type   string   `json:"type"`   // airstrip | place | road: the id space approve/reject take
	ID     int64    `json:"id"`
	ParkID string   `json:"park_id"`
	Kind   string   `json:"kind"`
	Name   string   `json:"name,omitempty"`
	Lat    *float64 `json:"lat"`
	Lon    *float64 `json:"lon"`
	// LengthM: a road's length, or a runway's observed ground run (absent
	// when unmeasured -- never a default).
	LengthM *float64 `json:"length_m,omitempty"`
	Status  string   `json:"status"`
	Reason  string   `json:"reason,omitempty"`
	// Score is nil for a row the event learner never scored (approved by a
	// reviewer under the counter learner). nil is "unscored", not 0.
	Score     *float64 `json:"score"`
	ApproveAt float64  `json:"approve_at,omitempty"`
	// Decided: "learner" or "reviewer". A reviewer's decision is never
	// overridden by a later run (DecideStatus).
	Decided  string         `json:"decided"`
	LastSeen string         `json:"last_seen,omitempty"`
	Evidence map[string]any `json:"evidence,omitempty"`
	Where    map[string]any `json:"where,omitempty"`

	events int
}

// learnerState folds the four stored statuses into the three a reader thinks
// in. "map" is exactly what /api/learned-map draws.
func learnerState(status string) string {
	switch status {
	case "approved", "auto_approved":
		return "map"
	case "rejected":
		return "rejected"
	}
	return "pending"
}

func (s *Server) HandleAPIAdminLearner(w http.ResponseWriter, r *http.Request) {
	if blockLearnedInTestEnv(w, r, map[string]any{
		"features": []any{}, "total": 0, "parks": []any{}, "model": map[string]any{}, "unavailable": true,
	}) {
		return
	}
	ctx := r.Context()
	qv := r.URL.Query()
	park, family, state, sortBy := qv.Get("park_id"), qv.Get("family"), qv.Get("state"), qv.Get("sort")
	limit, offset := 50, 0
	if n, err := strconv.Atoi(qv.Get("limit")); err == nil && n > 0 && n <= 200 {
		limit = n
	}
	if n, err := strconv.Atoi(qv.Get("offset")); err == nil && n > 0 {
		offset = n
	}

	// Every row, once. The three tables hold a few thousand rows between
	// them; reading them whole is what lets the counts, the filter and the
	// page come from one consistent read instead of four queries that can
	// disagree about a feature rescored in between.
	sources := []struct{ family, typ, sql string }{
		{"airfield", "airstrip", `SELECT id, park_id, COALESCE(kind,'airfield'), '', lat, lon,
			CASE WHEN line_geojson IS NOT NULL AND length_m > 0 THEN length_m END,
			COALESCE(status,'pending'), COALESCE(status_reason,''), score, COALESCE(approved_by,''), COALESCE(last_seen,''), evidence_json
			FROM learned_airstrips`},
		{"place", "place", `SELECT id, park_id, COALESCE(place_type,'stop'), COALESCE(name,''), lat, lon, NULL,
			COALESCE(status,'pending'), COALESCE(status_reason,''), score, COALESCE(approved_by,''), COALESCE(last_seen,''), evidence_json
			FROM learned_places`},
		{"road", "road", `SELECT id, park_id, 'road', '',
			COALESCE(json_extract(evidence_json,'$.lat'), json_extract(geojson,'$.coordinates[0][1]')),
			COALESCE(json_extract(evidence_json,'$.lon'), json_extract(geojson,'$.coordinates[0][0]')),
			length_m,
			COALESCE(status,'pending'), COALESCE(status_reason,''), score, COALESCE(approved_by,''), COALESCE(last_seen,''), evidence_json
			FROM learned_roads`},
	}
	type cell struct {
		Auto     int `json:"auto"`
		Reviewer int `json:"reviewer"`
		Pending  int `json:"pending"`
		Rejected int `json:"rejected"`
	}
	type parkSummary struct {
		ParkID string           `json:"park_id"`
		Total  int              `json:"total"`
		Counts map[string]*cell `json:"counts"`
		Events int              `json:"events"`
		From   string           `json:"from,omitempty"`
		To     string           `json:"to,omitempty"`
	}
	parks := map[string]*parkSummary{}
	var match []*adminLearnerFeature
	for _, src := range sources {
		rows, err := s.DB.QueryContext(ctx, src.sql)
		if err != nil {
			http.Error(w, "query failed", http.StatusInternalServerError)
			return
		}
		for rows.Next() {
			f := &adminLearnerFeature{Family: src.family, Type: src.typ}
			var lat, lon, length, score sql.NullFloat64
			var by string
			var ev sql.NullString
			if rows.Scan(&f.ID, &f.ParkID, &f.Kind, &f.Name, &lat, &lon, &length, &f.Status, &f.Reason, &score, &by, &f.LastSeen, &ev) != nil {
				continue
			}
			if lat.Valid && lon.Valid {
				f.Lat, f.Lon = &lat.Float64, &lon.Float64
			}
			if length.Valid && length.Float64 > 0 {
				f.LengthM = &length.Float64
			}
			f.Decided = "learner"
			if by != "" && by != learnerAuto {
				f.Decided = "reviewer"
			}
			if ev.Valid && score.Valid {
				f.Score = &score.Float64
			}

			ps := parks[f.ParkID]
			if ps == nil {
				ps = &parkSummary{ParkID: f.ParkID, Counts: map[string]*cell{"airfield": {}, "place": {}, "road": {}}}
				parks[f.ParkID] = ps
			}
			ps.Total++
			c := ps.Counts[f.Family]
			switch f.Status {
			case "auto_approved":
				c.Auto++
			case "approved":
				c.Reviewer++
			case "rejected":
				c.Rejected++
			default:
				c.Pending++
			}

			if (park != "" && f.ParkID != park) || (family != "" && f.Family != family) ||
				(state != "" && state != "all" && learnerState(f.Status) != state) {
				continue
			}
			if ev.Valid {
				if json.Unmarshal([]byte(ev.String), &f.Evidence) == nil {
					if n, ok := f.Evidence["events"].(float64); ok {
						f.events = int(n)
					}
					// Geometry and bookkeeping the row already states.
					for _, k := range []string{"line", "lat", "lon", "family", "kind"} {
						delete(f.Evidence, k)
					}
				}
			}
			if m, ok := learnedScoreModels[f.Family]; ok && m.Approve <= 1 {
				f.ApproveAt = m.Approve
			}
			match = append(match, f)
		}
		rows.Close()
	}

	scoreOf := func(f *adminLearnerFeature) float64 {
		if f.Score == nil {
			return -1
		}
		return *f.Score
	}
	sort.SliceStable(match, func(i, j int) bool {
		a, b := match[i], match[j]
		switch sortBy {
		case "recent":
			if a.LastSeen != b.LastSeen {
				return a.LastSeen > b.LastSeen
			}
		case "events":
			if a.events != b.events {
				return a.events > b.events
			}
		case "low":
			if scoreOf(a) != scoreOf(b) {
				return scoreOf(a) < scoreOf(b)
			}
		}
		if scoreOf(a) != scoreOf(b) {
			return scoreOf(a) > scoreOf(b)
		}
		if a.Family != b.Family {
			return a.Family < b.Family
		}
		return a.ID < b.ID
	})
	total := len(match)
	if offset > total {
		offset = total
	}
	page := match[offset:]
	if len(page) > limit {
		page = page[:limit]
	}
	// Village / river context for the page only, looked up now from the
	// tables the OSM rotation refreshes (srv/learned_context.go).
	for _, f := range page {
		if f.Lat == nil || f.Lon == nil {
			continue
		}
		where := map[string]any{}
		s.learnedContextAt(ctx, f.ParkID, *f.Lat, *f.Lon).apply(where)
		if len(where) > 0 {
			f.Where = where
		}
	}

	// What the learner has read, per park.
	if rows, err := s.DB.QueryContext(ctx, `SELECT park_id, COUNT(*), MIN(substr(t_start,1,10)), MAX(substr(t_start,1,10))
		FROM learned_events WHERE kind != 'coverage' GROUP BY park_id`); err == nil {
		for rows.Next() {
			var pid string
			var n int
			var from, to sql.NullString
			if rows.Scan(&pid, &n, &from, &to) == nil {
				if ps := parks[pid]; ps != nil {
					ps.Events, ps.From, ps.To = n, from.String, to.String
				}
			}
		}
		rows.Close()
	}
	parkList := make([]*parkSummary, 0, len(parks))
	for _, ps := range parks {
		parkList = append(parkList, ps)
	}
	sort.Slice(parkList, func(i, j int) bool {
		if parkList[i].Total != parkList[j].Total {
			return parkList[i].Total > parkList[j].Total
		}
		return parkList[i].ParkID < parkList[j].ParkID
	})

	model := map[string]any{}
	for fam, m := range learnedScoreModels {
		e := map[string]any{"reject": m.Reject, "features": m.Features}
		// Approve > 1 means no threshold met the precision target: the
		// family is never auto-approved, which is a state, not a number.
		if m.Approve <= 1 {
			e["approve"] = m.Approve
		}
		model[fam] = e
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, no-store")
	json.NewEncoder(w).Encode(map[string]any{
		"features":     page,
		"total":        total,
		"offset":       offset,
		"complete":     offset+len(page) >= total,
		"parks":        parkList,
		"model":        model,
		"horizon_days": LearnerHorizonDays,
		"queue":        s.learnerQueueState(r),
	})
}

// learnerQueueState reports the learning queue so "nothing new" can be told
// from "not finished": jobs waiting, running, failed, and running for so long
// that they are not running at all.
func (s *Server) learnerQueueState(r *http.Request) map[string]any {
	out := map[string]any{}
	rows, err := s.DB.QueryContext(r.Context(), `SELECT COALESCE(status,'pending'), COUNT(*),
		SUM(CASE WHEN status = 'processing' AND started_at < datetime('now','-1 hour') THEN 1 ELSE 0 END),
		MAX(COALESCE(completed_at, started_at, created_at))
		FROM gpx_learning_queue GROUP BY 1`)
	if err != nil {
		return out
	}
	defer rows.Close()
	for rows.Next() {
		var st string
		var n, stuck int
		var last sql.NullString
		if rows.Scan(&st, &n, &stuck, &last) != nil {
			continue
		}
		out[st] = n
		if stuck > 0 {
			out["stuck"] = stuck
		}
		if st == "completed" && last.Valid {
			out["last_completed_at"] = strings.Replace(last.String, " ", "T", 1) + "Z"
		}
	}
	return out
}

// POST /api/admin/learner/release {"type":"road|place|airstrip","id":n}
//
// Hands a reviewer's decision back to the learner. Approve and reject are
// permanent by design -- DecideStatus never overrides a reviewer -- so without
// this a mis-click on a 3,000-row list could not be taken back. The feature is
// re-decided now from its stored evidence, exactly as a learner run would.
func (s *Server) HandleAPIAdminLearnerRelease(w http.ResponseWriter, r *http.Request) {
	if refuseWriteInTestEnv(w, r) {
		return
	}
	var req struct {
		Type string `json:"type"`
		ID   int64  `json:"id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}
	table := ""
	switch req.Type {
	case "road":
		table = "learned_roads"
	case "place":
		table = "learned_places"
	case "airstrip":
		table = "learned_airstrips"
	default:
		http.Error(w, "Invalid type", http.StatusBadRequest)
		return
	}
	ctx := r.Context()
	var ev sql.NullString
	var prev string
	if err := s.DB.QueryRowContext(ctx, `SELECT evidence_json, COALESCE(status,'pending') FROM `+table+` WHERE id = ?`, req.ID).Scan(&ev, &prev); err != nil {
		http.Error(w, "Not found", http.StatusNotFound)
		return
	}
	status, reason := "pending", "returned to the learner; no event evidence kept"
	var by any
	var score any
	if ev.Valid {
		var e Evidence
		if json.Unmarshal([]byte(ev.String), &e) == nil {
			st, p, why := DecideStatus("pending", false, e)
			status, reason, score = st, why, p
			if st != "pending" {
				by = learnerAuto
			}
		}
	}
	// Snapshot the reviewed state first, as approve and reject do.
	q, who := dbgen.New(s.DB), "admin"
	switch req.Type {
	case "road":
		q.RecordRoadHistory(ctx, dbgen.RecordRoadHistoryParams{Action: "release", ActionBy: &who, ID: req.ID})
	case "place":
		q.RecordPlaceHistory(ctx, dbgen.RecordPlaceHistoryParams{Action: "release", ActionBy: &who, ID: req.ID})
	case "airstrip":
		q.RecordAirstripHistory(ctx, dbgen.RecordAirstripHistoryParams{Action: "release", ActionBy: &who, ID: req.ID})
	}
	if _, err := s.DB.ExecContext(ctx, `UPDATE `+table+` SET status = ?, status_reason = ?, approved_by = ?, approved_at = NULL,
		score = COALESCE(?, score), updated_at = CURRENT_TIMESTAMP WHERE id = ?`, status, reason, by, score, req.ID); err != nil {
		http.Error(w, "Failed to release feature", http.StatusInternalServerError)
		return
	}
	writeJSON(w, 200, map[string]any{"status": status, "reason": reason, "previous": prev})
}
