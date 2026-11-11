package srv

// GET /api/admin/activity        -- the upload log: every file that reached
//                                   the patrol pipeline, fetched or uploaded.
// GET /api/admin/activity/detail -- one file, summarised.
//
// The log it replaced (/api/admin/gpx-logs, still served) was written for
// hand-uploaded tracks: a filename, a park and seven km columns. After the
// autofetch backfill a row is a WEEK of a ranger fleet -- 60,000 points, 1,300
// segments, four parks -- named "autofetch-20261105.gpx" and credited to one
// park, and its detail view shipped the 2.8 MB segment JSON to draw 1,300
// divs. So a row here says where the file came from, the period its tracks
// cover (not the minute it arrived), what moved, and what the learner made of
// it; and the detail is aggregated here rather than in the browser.
//
// Scope is the caller's patrol envs (own tenant + shared autofetch envs), and
// the whole thing is client-tenant only, like the logs it reads.

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

// logTime normalises the three spellings upload_time / start_time are stored
// in (RFC3339, SQL, and Go's time.String() with a monotonic suffix) to
// RFC3339 UTC. Unparseable input is returned as "" -- absent, not epoch.
func logTime(v string) string {
	v = strings.TrimSpace(v)
	if v == "" {
		return ""
	}
	if i := strings.Index(v, " m="); i > 0 {
		v = v[:i]
	}
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02 15:04:05.999999999 -0700 MST", "2006-01-02 15:04:05", "2006-01-02T15:04:05"} {
		if t, err := time.Parse(layout, v); err == nil {
			return t.UTC().Format(time.RFC3339)
		}
	}
	return ""
}

type activityModes struct {
	Foot      float64 `json:"foot"`
	Vehicle   float64 `json:"vehicle"`
	Boat      float64 `json:"boat"`
	FixedWing float64 `json:"fixed_wing"`
	RotorWing float64 `json:"rotor_wing"`
	// AircraftUnknown: aircraft km whose airframe was not measured. It is
	// its own bar, not folded into either subtype.
	AircraftUnknown float64 `json:"aircraft_unknown"`
}

func (m *activityModes) add(o activityModes) {
	m.Foot += o.Foot
	m.Vehicle += o.Vehicle
	m.Boat += o.Boat
	m.FixedWing += o.FixedWing
	m.RotorWing += o.RotorWing
	m.AircraftUnknown += o.AircraftUnknown
}

type activityLearned struct {
	ParkID    string  `json:"park_id"`
	Roads     int     `json:"roads"`
	RoadsKm   float64 `json:"roads_km"`
	Places    int     `json:"places"`
	Airfields int     `json:"airfields"`
}

type activityRow struct {
	ID         int64  `json:"id"`
	Source     string `json:"source"` // autofetch | upload
	SourceID   int64  `json:"source_id,omitempty"`
	SourceName string `json:"source_name,omitempty"`
	Filename   string `json:"filename"`
	Received   string `json:"received"`
	// From/To: the period the TRACKS cover. Absent when the file's fixes
	// carried no usable time.
	From     string        `json:"from,omitempty"`
	To       string        `json:"to,omitempty"`
	Points   int64         `json:"points"`
	Segments int64         `json:"segments"`
	Park     string        `json:"park,omitempty"`
	Status   string        `json:"status"`
	Rejected string        `json:"rejection_reason,omitempty"`
	Km       activityModes `json:"km"`
	Boundary float64       `json:"boundary_km"`
	Excluded float64       `json:"excluded_km"`
	// Learner: "learned" | "queued" | "running" | "failed" | "" (never
	// queued: outside the tenant that feeds the learner, or nothing to learn).
	Learner string            `json:"learner,omitempty"`
	Learned []activityLearned `json:"learned,omitempty"`
	CanEdit bool              `json:"can_edit"`

	uploadID int64
}

func sourceHost(u string) string {
	if p, err := url.Parse(u); err == nil && p.Host != "" {
		return p.Host
	}
	return u
}

func (s *Server) HandleAPIAdminActivity(w http.ResponseWriter, r *http.Request) {
	if blockLearnedInTestEnv(w, r, map[string]any{"rows": []any{}, "total": 0, "unavailable": true}) {
		return
	}
	ctx := r.Context()
	qv := r.URL.Query()
	limit, offset := 30, 0
	if n, err := strconv.Atoi(qv.Get("limit")); err == nil && n > 0 && n <= 200 {
		limit = n
	}
	if n, err := strconv.Atoi(qv.Get("offset")); err == nil && n > 0 {
		offset = n
	}
	envs := s.PatrolEnvsJSON(r)
	where, args := PatrolEnvsSQL("l.env"), []any{envs}
	switch qv.Get("source") {
	case "autofetch":
		where += ` AND l.filename LIKE 'autofetch-%'`
	case "upload":
		where += ` AND l.filename NOT LIKE 'autofetch-%'`
	}
	if id, err := strconv.ParseInt(qv.Get("source_id"), 10, 64); err == nil && id > 0 {
		where += ` AND l.env = (SELECT data_env FROM autofetch_sources WHERE id = ? AND data_env != '')`
		args = append(args, id)
	}

	// Totals for the filter, over every row on record -- not the page.
	var total int
	var tot struct {
		Points sql.NullInt64
		From   sql.NullString
		To     sql.NullString
		km     [6]sql.NullFloat64
	}
	_ = s.DB.QueryRowContext(ctx, `SELECT COUNT(*), SUM(l.total_points), MIN(u.start_time), MAX(u.end_time),
		SUM(l.foot_km), SUM(l.vehicle_km), SUM(l.boat_km), SUM(l.fixed_wing_km), SUM(l.rotor_wing_km),
		SUM(MAX(COALESCE(l.aircraft_km,0) - COALESCE(l.fixed_wing_km,0) - COALESCE(l.rotor_wing_km,0), 0))
		FROM gpx_upload_logs l LEFT JOIN gpx_uploads u ON u.id = l.upload_id WHERE `+where, args...).
		Scan(&total, &tot.Points, &tot.From, &tot.To, &tot.km[0], &tot.km[1], &tot.km[2], &tot.km[3], &tot.km[4], &tot.km[5])

	writable := map[string]bool{}
	for _, e := range s.PatrolWriteEnvs(r) {
		writable[e] = true
	}

	rows, err := s.DB.QueryContext(ctx, `SELECT l.id, COALESCE(l.upload_id,0), l.env, l.filename, l.upload_time, l.total_points,
			COALESCE(l.total_segments,0), COALESCE(l.protected_area_name, l.protected_area_id, ''),
			COALESCE(l.processing_status,''), COALESCE(l.rejection_reason,''),
			COALESCE(l.foot_km,0), COALESCE(l.vehicle_km,0), COALESCE(l.boat_km,0), COALESCE(l.fixed_wing_km,0), COALESCE(l.rotor_wing_km,0),
			MAX(COALESCE(l.aircraft_km,0) - COALESCE(l.fixed_wing_km,0) - COALESCE(l.rotor_wing_km,0), 0),
			COALESCE(l.boundary_km,0), COALESCE(l.excluded_km,0),
			COALESCE(u.start_time,''), COALESCE(u.end_time,''),
			COALESCE(a.id,0), COALESCE(a.title,''), COALESCE(a.service_url,'')
		FROM gpx_upload_logs l
		LEFT JOIN gpx_uploads u ON u.id = l.upload_id
		LEFT JOIN autofetch_sources a ON a.data_env = l.env AND a.data_env != ''
		WHERE `+where+` ORDER BY l.upload_time DESC, l.id DESC LIMIT ? OFFSET ?`, append(args, limit, offset)...)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	out := []*activityRow{}
	byUpload := map[int64]*activityRow{}
	for rows.Next() {
		a := &activityRow{}
		var env, received, from, to, title, surl string
		if rows.Scan(&a.ID, &a.uploadID, &env, &a.Filename, &received, &a.Points, &a.Segments, &a.Park, &a.Status, &a.Rejected,
			&a.Km.Foot, &a.Km.Vehicle, &a.Km.Boat, &a.Km.FixedWing, &a.Km.RotorWing, &a.Km.AircraftUnknown,
			&a.Boundary, &a.Excluded, &from, &to, &a.SourceID, &title, &surl) != nil {
			continue
		}
		a.Received, a.From, a.To = logTime(received), logTime(from), logTime(to)
		a.Source = "upload"
		if strings.HasPrefix(a.Filename, "autofetch-") {
			a.Source = "autofetch"
			a.SourceName = title
			if a.SourceName == "" {
				a.SourceName = sourceHost(surl)
			}
		} else {
			a.SourceID = 0
		}
		a.CanEdit = writable[env]
		out = append(out, a)
		if a.uploadID > 0 {
			byUpload[a.uploadID] = a
		}
	}
	rows.Close()

	if len(byUpload) > 0 {
		ids := make([]any, 0, len(byUpload))
		for id := range byUpload {
			ids = append(ids, id)
		}
		in := strings.TrimSuffix(strings.Repeat("?,", len(ids)), ",")
		// What each file put on the map, per park (new_*_found counts
		// features that BECAME approved in that run: learnFromUpload).
		if lr, err := s.DB.QueryContext(ctx, `SELECT upload_id, park_id, SUM(COALESCE(new_roads_found,0)), SUM(COALESCE(new_roads_km,0)),
			SUM(COALESCE(new_places_found,0)), SUM(COALESCE(new_airstrips_found,0))
			FROM gpx_learning_results WHERE upload_id IN (`+in+`) GROUP BY upload_id, park_id ORDER BY park_id`, ids...); err == nil {
			for lr.Next() {
				var up int64
				var l activityLearned
				if lr.Scan(&up, &l.ParkID, &l.Roads, &l.RoadsKm, &l.Places, &l.Airfields) == nil {
					if a := byUpload[up]; a != nil {
						a.Learned = append(a.Learned, l)
					}
				}
			}
			lr.Close()
		}
		if lq, err := s.DB.QueryContext(ctx, `SELECT upload_id,
			SUM(status = 'pending'), SUM(status = 'processing'), SUM(status = 'failed'), SUM(status = 'completed')
			FROM gpx_learning_queue WHERE upload_id IN (`+in+`) GROUP BY upload_id`, ids...); err == nil {
			for lq.Next() {
				var up int64
				var pend, run, fail, done int
				if lq.Scan(&up, &pend, &run, &fail, &done) == nil {
					if a := byUpload[up]; a != nil {
						switch {
						case run > 0:
							a.Learner = "running"
						case pend > 0:
							a.Learner = "queued"
						case fail > 0:
							a.Learner = "failed"
						case done > 0:
							a.Learner = "learned"
						}
					}
				}
			}
			lq.Close()
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, no-store")
	json.NewEncoder(w).Encode(map[string]any{
		"rows": out, "total": total, "offset": offset, "complete": offset+len(out) >= total,
		"totals": map[string]any{
			"files": total, "points": tot.Points.Int64,
			"from": logTime(tot.From.String), "to": logTime(tot.To.String),
			"km": activityModes{tot.km[0].Float64, tot.km[1].Float64, tot.km[2].Float64, tot.km[3].Float64, tot.km[4].Float64, tot.km[5].Float64},
		},
	})
}

// activitySegment is the part of a ClassifiedSegment the summary reads; the
// sampled points (most of the 2.8 MB) are skipped by the decoder.
type activitySegment struct {
	Classification  string  `json:"classification"`
	MovementType    string  `json:"movement_type"`
	MovementSubtype string  `json:"movement_subtype"`
	ActivityType    string  `json:"activity_type"`
	DistanceKm      float64 `json:"distance_km"`
	Duration        string  `json:"duration"`
	AvgSpeedKmh     float64 `json:"avg_speed_kmh"`
	Reason          string  `json:"reason"`
	IncludeInEffort bool    `json:"include_in_effort"`
	Tracker         string  `json:"tracker"`
}

func (s *Server) HandleAPIAdminActivityDetail(w http.ResponseWriter, r *http.Request) {
	if blockLearnedInTestEnv(w, r, map[string]any{}) {
		return
	}
	id, err := strconv.ParseInt(r.URL.Query().Get("id"), 10, 64)
	if err != nil {
		http.Error(w, "Invalid id", http.StatusBadRequest)
		return
	}
	var segJSON, vErr, vWarn sql.NullString
	var patrolKm, boundaryKm, excludedKm float64
	var mins [3]sql.NullFloat64
	err = s.DB.QueryRowContext(r.Context(), `SELECT classified_segments_json, validation_errors, validation_warnings,
			COALESCE(patrol_km,0), COALESCE(boundary_km,0), COALESCE(excluded_km,0), foot_minutes, vehicle_minutes, aircraft_minutes
		FROM gpx_upload_logs WHERE id = ? AND `+PatrolEnvsSQL("env"), id, s.PatrolEnvsJSON(r)).
		Scan(&segJSON, &vErr, &vWarn, &patrolKm, &boundaryKm, &excludedKm, &mins[0], &mins[1], &mins[2])
	if err != nil {
		http.Error(w, "Upload not found", http.StatusNotFound)
		return
	}
	type group struct {
		Movement string  `json:"movement"`
		Subtype  string  `json:"subtype,omitempty"`
		Class    string  `json:"classification"`
		Counted  bool    `json:"in_effort"`
		Segments int     `json:"segments"`
		Km       float64 `json:"km"`
		Hours    float64 `json:"hours"`
	}
	type leg struct {
		Movement string  `json:"movement"`
		Subtype  string  `json:"subtype,omitempty"`
		Class    string  `json:"classification"`
		Counted  bool    `json:"in_effort"`
		Km       float64 `json:"km"`
		Kmh      float64 `json:"kmh"`
		Duration string  `json:"duration"`
		Reason   string  `json:"reason,omitempty"`
	}
	groups := map[string]*group{}
	trackers := map[string]bool{}
	var legs []leg
	var segs []activitySegment
	if segJSON.Valid {
		_ = json.Unmarshal([]byte(segJSON.String), &segs)
	}
	for _, sg := range segs {
		key := sg.MovementType + "|" + sg.MovementSubtype + "|" + sg.Classification + "|" + strconv.FormatBool(sg.IncludeInEffort)
		g := groups[key]
		if g == nil {
			g = &group{Movement: sg.MovementType, Subtype: sg.MovementSubtype, Class: sg.Classification, Counted: sg.IncludeInEffort}
			groups[key] = g
		}
		g.Segments++
		g.Km += sg.DistanceKm
		if d, err := time.ParseDuration(sg.Duration); err == nil {
			g.Hours += d.Hours()
		}
		if sg.Tracker != "" {
			trackers[sg.Tracker] = true
		}
		legs = append(legs, leg{sg.MovementType, sg.MovementSubtype, sg.Classification, sg.IncludeInEffort, sg.DistanceKm, sg.AvgSpeedKmh, sg.Duration, sg.Reason})
	}
	gl := make([]*group, 0, len(groups))
	for _, g := range groups {
		gl = append(gl, g)
	}
	sort.Slice(gl, func(i, j int) bool { return gl[i].Km > gl[j].Km })
	sort.Slice(legs, func(i, j int) bool { return legs[i].Km > legs[j].Km })
	const maxLegs = 12
	if len(legs) > maxLegs {
		legs = legs[:maxLegs]
	}
	list := func(v sql.NullString) []string {
		var out []string
		if v.Valid {
			_ = json.Unmarshal([]byte(v.String), &out)
		}
		return out
	}
	out := map[string]any{
		"id": id, "segments": len(segs), "groups": gl,
		// The longest legs only -- and said so, with the count they came from.
		"longest": legs, "longest_of": len(segs),
		"patrol_km": patrolKm, "boundary_km": boundaryKm, "excluded_km": excludedKm,
		"errors": list(vErr), "warnings": list(vWarn),
	}
	// A tracker count only where segments carry one; 0 would read as "none".
	if len(trackers) > 0 {
		out["trackers"] = len(trackers)
	}
	w.Header().Set("Cache-Control", "private, no-store")
	writeJSON(w, 200, out)
}

// ── Fetch log of one autofetch source ───────────────────────────────────────

type autofetchFetch struct {
	LogID    int64   `json:"log_id"`
	From     string  `json:"from,omitempty"`
	To       string  `json:"to,omitempty"`
	Received string  `json:"received"`
	Points   int64   `json:"points"`
	Km       float64 `json:"km"`
	Status   string  `json:"status"`
}

type autofetchLog struct {
	// Fetches: the most recent files this source delivered, oldest first.
	// FetchesTotal says how many there are, so a strip of 26 is not read
	// as the whole history.
	Fetches      []autofetchFetch `json:"fetches"`
	FetchesTotal int              `json:"fetches_total"`
	CoverFrom    string           `json:"cover_from,omitempty"`
	CoverTo      string           `json:"cover_to,omitempty"`
	// Archive: raw GPX kept on disk (data/autofetch_archive/<env>).
	ArchiveFiles int   `json:"archive_files"`
	ArchiveBytes int64 `json:"archive_bytes"`
	// Backfill: the resumable history re-fetch, when one was ever run.
	Backfill *autofetchBackfillSummary `json:"backfill,omitempty"`
}

type autofetchBackfillSummary struct {
	Windows int            `json:"windows"`
	ByState map[string]int `json:"by_state"`
	From    string         `json:"from,omitempty"`
	To      string         `json:"to,omitempty"`
	Points  int            `json:"points"`
	At      string         `json:"at,omitempty"`
	Errors  []string       `json:"errors,omitempty"`
}

const autofetchLogFetches = 26

func (s *Server) autofetchSourceLog(r *http.Request, sourceID int64, dataEnv string) *autofetchLog {
	if dataEnv == "" {
		return nil
	}
	ctx := r.Context()
	lg := &autofetchLog{Fetches: []autofetchFetch{}}
	var from, to sql.NullString
	_ = s.DB.QueryRowContext(ctx, `SELECT COUNT(*), MIN(u.start_time), MAX(u.end_time)
		FROM gpx_upload_logs l LEFT JOIN gpx_uploads u ON u.id = l.upload_id WHERE l.env = ?`, dataEnv).
		Scan(&lg.FetchesTotal, &from, &to)
	lg.CoverFrom, lg.CoverTo = logTime(from.String), logTime(to.String)
	rows, err := s.DB.QueryContext(ctx, `SELECT l.id, COALESCE(u.start_time,''), COALESCE(u.end_time,''), l.upload_time, l.total_points,
			COALESCE(u.total_distance_km, 0), COALESCE(l.processing_status,'')
		FROM gpx_upload_logs l LEFT JOIN gpx_uploads u ON u.id = l.upload_id
		WHERE l.env = ? ORDER BY l.upload_time DESC, l.id DESC LIMIT ?`, dataEnv, autofetchLogFetches)
	if err == nil {
		for rows.Next() {
			var f autofetchFetch
			var a, b, c string
			if rows.Scan(&f.LogID, &a, &b, &c, &f.Points, &f.Km, &f.Status) == nil {
				f.From, f.To, f.Received = logTime(a), logTime(b), logTime(c)
				lg.Fetches = append(lg.Fetches, f)
			}
		}
		rows.Close()
		// Oldest first, by the period covered: a backfill arrives in one
		// afternoon but describes five months.
		sort.SliceStable(lg.Fetches, func(i, j int) bool {
			a, b := lg.Fetches[i], lg.Fetches[j]
			if a.From != b.From {
				return a.From < b.From
			}
			return a.Received < b.Received
		})
	}
	if ents, err := os.ReadDir(filepath.Join(AutofetchArchiveDir, filepath.Base(dataEnv))); err == nil {
		for _, e := range ents {
			if e.IsDir() || !strings.HasSuffix(e.Name(), ".gpx.gz") {
				continue
			}
			if fi, err := e.Info(); err == nil {
				lg.ArchiveFiles++
				lg.ArchiveBytes += fi.Size()
			}
		}
	}
	statePath := filepath.Join("data", "autofetch_backfill", "source-"+strconv.FormatInt(sourceID, 10)+".json")
	if st, err := LoadBackfillState(statePath, sourceID); err == nil && len(st.Windows) > 0 {
		b := &autofetchBackfillSummary{Windows: len(st.Windows), ByState: map[string]int{}}
		for _, win := range st.Windows {
			b.ByState[win.Status]++
			b.Points += win.Points
			if b.From == "" || win.From < b.From {
				b.From = win.From
			}
			if win.To > b.To {
				b.To = win.To
			}
			if win.At > b.At {
				b.At = win.At
			}
			if win.Error != "" && len(b.Errors) < 3 {
				b.Errors = append(b.Errors, win.From[:min(10, len(win.From))]+": "+win.Error)
			}
		}
		lg.Backfill = b
	}
	return lg
}
