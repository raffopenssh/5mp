package srv

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"net/http"
	"os/exec"
	"regexp"
	"strings"
	"time"
	"unicode"
)

// Write half of the AOI surface: draw-to-create, refresh, delete, and the
// estimate the create dialog shows before any of it is committed.
//
// The read half (srv/aoi.go) could lean on the park handlers. This half cannot:
// creating an AOI seeds a work queue that will spend real FIRMS quota and hours
// of CPU over days, so the endpoint's job is as much about *telling the truth
// beforehand* as about inserting a row. Hence POST /api/aois/estimate, which is
// deliberately free of side effects and callable while the user is still
// dragging vertices.

// ------------------------------------------------------------------ geometry

// aoiGeom is the subset of GeoJSON an AOI may be. Not a full parser: anything
// that is not a plain Polygon/MultiPolygon of finite lon/lat is rejected before
// it can reach shapely, the v5 chain, or a subprocess argument.
type aoiGeom struct {
	Type        string          `json:"type"`
	Coordinates json.RawMessage `json:"coordinates"`
}

// polygonRings normalises Polygon and MultiPolygon into a list of rings.
func polygonRings(g *aoiGeom) ([][][2]float64, error) {
	switch g.Type {
	case "Polygon":
		var p [][][2]float64
		if err := json.Unmarshal(g.Coordinates, &p); err != nil {
			return nil, fmt.Errorf("bad Polygon coordinates")
		}
		return p, nil
	case "MultiPolygon":
		var mp [][][][2]float64
		if err := json.Unmarshal(g.Coordinates, &mp); err != nil {
			return nil, fmt.Errorf("bad MultiPolygon coordinates")
		}
		var out [][][2]float64
		for _, p := range mp {
			out = append(out, p...)
		}
		return out, nil
	}
	return nil, fmt.Errorf("geometry must be Polygon or MultiPolygon")
}

// validateAOIGeom checks the geometry and returns its bbox and geodesic area.
//
// The vertex cap is not arbitrary: the polygon is stored as text, re-parsed by
// every runner, and traced by the animator's canvas clip on every frame. A
// hand-drawn AOI is tens of vertices; anything in the thousands is an imported
// boundary that belongs in a park record.
func validateAOIGeom(g *aoiGeom) (bbox [4]float64, areaKm2 float64, err error) {
	rings, err := polygonRings(g)
	if err != nil {
		return bbox, 0, err
	}
	if len(rings) == 0 {
		return bbox, 0, fmt.Errorf("geometry has no rings")
	}
	bbox = [4]float64{math.Inf(1), math.Inf(1), math.Inf(-1), math.Inf(-1)}
	n := 0
	for _, ring := range rings {
		if len(ring) < 4 {
			return bbox, 0, fmt.Errorf("a ring needs at least 4 positions (closed)")
		}
		for _, c := range ring {
			lon, lat := c[0], c[1]
			if math.IsNaN(lon) || math.IsNaN(lat) || math.IsInf(lon, 0) || math.IsInf(lat, 0) ||
				lon < -180 || lon > 180 || lat < -90 || lat > 90 {
				return bbox, 0, fmt.Errorf("coordinate out of range")
			}
			bbox[0] = math.Min(bbox[0], lon)
			bbox[1] = math.Min(bbox[1], lat)
			bbox[2] = math.Max(bbox[2], lon)
			bbox[3] = math.Max(bbox[3], lat)
			n++
		}
	}
	const maxVertices = 2000
	if n > maxVertices {
		return bbox, 0, fmt.Errorf("too many vertices (%d, max %d)", n, maxVertices)
	}
	areaKm2 = ringsAreaKm2(rings)
	if areaKm2 <= 0 {
		return bbox, 0, fmt.Errorf("polygon has no area")
	}
	return bbox, areaKm2, nil
}

// ringsAreaKm2 is the spherical excess (shoelace on an authalic sphere), the
// same quantity aoi_lib.geodesic_area_km2 gets from pyproj. Outer rings add,
// inner rings subtract, which falls out of the signed area — so a donut AOI
// is priced as a donut. Agreement with the Python side is asserted in the
// tests against XSA's measured 485,150 km2.
func ringsAreaKm2(rings [][][2]float64) float64 {
	const R = 6371.0088 // authalic mean radius, km
	total := 0.0
	for i, ring := range rings {
		s := 0.0
		for j := 0; j < len(ring)-1; j++ {
			lon1 := ring[j][0] * math.Pi / 180
			lat1 := ring[j][1] * math.Pi / 180
			lon2 := ring[j+1][0] * math.Pi / 180
			lat2 := ring[j+1][1] * math.Pi / 180
			s += (lon2 - lon1) * (2 + math.Sin(lat1) + math.Sin(lat2))
		}
		a := math.Abs(s * R * R / 2)
		// Ring 0 of each polygon is the outer ring in GeoJSON; the rest are
		// holes. polygonRings flattens MultiPolygons, so this is approximate
		// for a multi with holes — acceptable: it is an estimate input, and
		// the authoritative area is recomputed by shapely at create time.
		if i == 0 {
			total += a
		} else {
			total -= a
		}
	}
	return math.Abs(total)
}

// ------------------------------------------------------------------ requests

type aoiCreateReq struct {
	Name       string   `json:"name"`
	Geometry   *aoiGeom `json:"geometry"`
	FromDate   string   `json:"from_date"`
	ToDate     string   `json:"to_date"`
	Visibility string   `json:"visibility"`
	Notes      string   `json:"notes"`
}

var isoDateRe = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)

// slugAOIID turns a name into an id that satisfies ValidAOIID and reads well in
// a URL, a filename and a subprocess argument. A time suffix keeps two AOIs of
// the same name apart without a lookup loop.
func slugAOIID(name string) string {
	var b strings.Builder
	b.WriteString("aoi_")
	prevUnderscore := true
	for _, r := range name {
		switch {
		// ASCII only: the id ends up in a URL path, a file name and a
		// subprocess argument, and ValidAOIID (which the middleware enforces)
		// is ASCII. A non-ASCII name still gets a usable id, just transliterated
		// by omission.
		case r < 128 && (unicode.IsLetter(r) || unicode.IsDigit(r)):
			b.WriteRune(r)
			prevUnderscore = false
		case !prevUnderscore:
			b.WriteByte('_')
			prevUnderscore = true
		}
		if b.Len() > 40 {
			break
		}
	}
	s := strings.Trim(b.String(), "_")
	if s == "aoi" || s == "" {
		s = "aoi"
	}
	return s + "_" + time.Now().UTC().Format("060102150405")
}

// windowDays is the length of the analysis window, defaulting the way the
// runner does (an empty from_date means "everything we can get", which for
// FIRMS is 2024-01-01 onward).
func windowDays(from, to string) int {
	f, err := time.Parse("2006-01-02", from)
	if err != nil {
		f = time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC)
	}
	t, err := time.Parse("2006-01-02", to)
	if err != nil {
		t = time.Now().UTC()
	}
	d := int(t.Sub(f).Hours() / 24)
	if d < 1 {
		d = 1
	}
	return d
}

// countriesForBBox counts the distinct country prefixes of the parks whose
// centre falls in the bbox — the same cheap heuristic aoi_runner.aoi_countries
// uses, and only an input to the OSM estimate.
func (s *Server) countriesForBBox(b [4]float64) int {
	if s.AreaStore == nil {
		return 1
	}
	seen := map[string]bool{}
	for i := range s.AreaStore.Areas {
		a := &s.AreaStore.Areas[i]
		lat, lon := a.CenterLatLon()
		if lon >= b[0] && lon <= b[2] && lat >= b[1] && lat <= b[3] {
			if j := strings.Index(a.ID, "_"); j > 0 {
				seen[a.ID[:j]] = true
			}
		}
	}
	if len(seen) == 0 {
		return 1
	}
	return len(seen)
}

// HandleAPIAOIEstimate — POST /api/aois/estimate
//
// Side-effect free by design: the create dialog calls it on every edit of the
// polygon or the date window, so the user sees the cost of the thing they are
// about to ask for *before* asking for it. "This will take about 3 days" is
// the single most important sentence in the AOI flow.
func (s *Server) HandleAPIAOIEstimate(w http.ResponseWriter, r *http.Request) {
	var req aoiCreateReq
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&req); err != nil {
		http.Error(w, "bad JSON", http.StatusBadRequest)
		return
	}
	if req.Geometry == nil {
		http.Error(w, "geometry required", http.StatusBadRequest)
		return
	}
	bbox, area, err := validateAOIGeom(req.Geometry)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	est := estimateAOI(bbox, area, windowDays(req.FromDate, req.ToDate),
		s.countriesForBBox(bbox))
	writeJSON(w, http.StatusOK, est)
}

// HandleAPIAOICreate — POST /api/aois
//
// Creates the AOI and seeds its queue, then returns immediately. It does NOT
// run anything: the runner is a cron with a lease, and kicking off work inside
// an HTTP handler would give us a second, unsupervised writer racing the one
// that has the lease discipline (that race is exactly what stranded a lease on
// 2026-08-07). The response carries the estimate so the client can say when to
// come back, and the `clip` preview lands on the next slice.
func (s *Server) HandleAPIAOICreate(w http.ResponseWriter, r *http.Request) {
	principal := s.RequestPrincipalID(r)
	if principal == 0 {
		// An unowned AOI could never be deleted or scoped by its creator.
		http.Error(w, "a password is required to own an area", http.StatusForbidden)
		return
	}
	var req aoiCreateReq
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&req); err != nil {
		http.Error(w, "bad JSON", http.StatusBadRequest)
		return
	}
	req.Name = strings.TrimSpace(req.Name)
	if req.Name == "" || len(req.Name) > 80 {
		http.Error(w, "name required (1-80 chars)", http.StatusBadRequest)
		return
	}
	if req.Geometry == nil {
		http.Error(w, "geometry required", http.StatusBadRequest)
		return
	}
	bbox, area, err := validateAOIGeom(req.Geometry)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	for _, d := range []string{req.FromDate, req.ToDate} {
		if d != "" && !isoDateRe.MatchString(d) {
			http.Error(w, "dates must be YYYY-MM-DD", http.StatusBadRequest)
			return
		}
	}
	if req.Visibility != "public" && req.Visibility != "shared" {
		req.Visibility = "private"
	}

	// One AOI per principal per... nothing, but a cap: each one is days of
	// machine time, so an accidental double-click must not queue two.
	var live int
	s.DB.QueryRow(`SELECT COUNT(*) FROM aois WHERE owner_principal_id = ?`,
		principal).Scan(&live)
	if live >= 20 {
		http.Error(w, "too many areas of interest for this account", http.StatusTooManyRequests)
		return
	}

	id := slugAOIID(req.Name)
	geomJSON, _ := json.Marshal(map[string]any{
		"type": req.Geometry.Type, "coordinates": req.Geometry.Coordinates})

	tx, err := s.DB.Begin()
	if err != nil {
		internalError(w, "could not create area", err)
		return
	}
	defer tx.Rollback()
	if _, err := tx.Exec(`
		INSERT INTO aois (id, name, geometry, bbox_minx, bbox_miny, bbox_maxx,
		                  bbox_maxy, area_km2, from_date, to_date,
		                  owner_principal_id, visibility, state, notes)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)`,
		id, req.Name, string(geomJSON), bbox[0], bbox[1], bbox[2], bbox[3],
		area, nullIfEmpty(req.FromDate), nullIfEmpty(req.ToDate),
		principal, req.Visibility, nullIfEmpty(req.Notes)); err != nil {
		internalError(w, "could not create area", err)
		return
	}
	// Queue definition mirrors aoi_lib.DEFAULT_DATASETS. Duplicated rather than
	// shelled out to keep create synchronous and cheap; the test asserts the
	// two lists agree.
	for _, d := range defaultAOIDatasets {
		if _, err := tx.Exec(`INSERT OR IGNORE INTO aoi_datasets
			(aoi_id, dataset, priority, depends_on) VALUES (?,?,?,?)`,
			id, d.name, d.priority, nullIfEmpty(d.dependsOn)); err != nil {
			internalError(w, "could not queue datasets", err)
			return
		}
	}
	if err := tx.Commit(); err != nil {
		internalError(w, "could not create area", err)
		return
	}
	// The id set gates /api/parks/{id} 404s and aoiExcludeSQL — refresh before
	// anything can query the new rows.
	if err := s.RefreshAOIIDs(); err != nil {
		slog.Warn("refresh aoi ids", "error", err)
	}

	est := estimateAOI(bbox, area, windowDays(req.FromDate, req.ToDate),
		s.countriesForBBox(bbox))
	// Tell the user, in the notification panel, what they just started and how
	// long it will take. The runner then updates this same thread as it goes.
	s.notifyAOIQueued(id, req.Name, est)

	a, err := s.GetAOI(id, principal, true)
	if err != nil {
		internalError(w, "created but could not read back", err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"aoi": a, "estimate": est})
}

type aoiDatasetDef struct {
	name      string
	priority  int
	dependsOn string
}

// Mirrors scripts/aoi_lib.py DEFAULT_DATASETS (asserted by TestAOIDatasetsMatchPython).
var defaultAOIDatasets = []aoiDatasetDef{
	{"clip", 5, ""},
	{"fire_gap", 10, ""},
	{"fire_v5", 20, "fire_gap"},
	{"gfw", 30, ""},
	{"deforestation", 35, "gfw"},
	{"hansen", 36, ""},
	{"ghsl", 40, ""},
	{"osm", 50, ""},
	{"gsw", 60, ""},
	{"hydro", 70, ""},
	{"basin", 80, ""},
}

// requireAOIOwner resolves the path AOI and insists the caller owns it.
// Non-owners get 404, not 403 — an id must not be an oracle (srv/aoi.go).
func (s *Server) requireAOIOwner(w http.ResponseWriter, r *http.Request) *AOI {
	a := s.requireAOI(w, r, false)
	if a == nil {
		return nil
	}
	if !a.IsOwner {
		http.NotFound(w, r)
		return nil
	}
	return a
}

// HandleAPIAOIRefresh — POST /api/aois/{id}/refresh
//
// Requeues datasets so the next runner slice picks them up. Again: it does not
// run them. `?dataset=` refreshes one; the default re-runs the cheap derived
// layers only, because re-running fire_gap would re-spend FIRMS quota on
// windows we already hold.
func (s *Server) HandleAPIAOIRefresh(w http.ResponseWriter, r *http.Request) {
	a := s.requireAOIOwner(w, r)
	if a == nil {
		return
	}
	only := r.URL.Query().Get("dataset")

	// A superseded version must not be restartable. Its queue was disabled by
	// the edit that forked it, which looks identical to a /cancel from the
	// outside -- but re-enabling it would spend days of FIRMS quota answering a
	// question the user has already replaced, and produce a second AOI competing
	// for the runner with its own successor. Editing is the way forward from
	// here; /restore is the way back.
	if a.SupersededBy != "" {
		http.Error(w, "superseded by "+a.SupersededBy, http.StatusConflict)
		return
	}

	// resume=1 is the inverse of /cancel, and deliberately a different query
	// rather than a mode of the default one. The default refresh re-runs the
	// cheap *derived* layers of a queue that is still enabled (a "recompute
	// this" button); resume re-enables a queue that was switched off, whatever
	// stage it had reached, and must not touch cursors — that is what makes it
	// a resume rather than a restart, and what keeps FIRMS quota unspent.
	if r.URL.Query().Get("resume") == "1" {
		res, err := execUserToggle(r.Context(), s.DB, `UPDATE aoi_datasets
			SET enabled=1, next_run_at=NULL, lease_owner=NULL, lease_until=NULL,
			    state=CASE WHEN state='running' THEN 'pending' ELSE state END
			WHERE aoi_id = ? AND enabled = 0 AND state != 'done'`, a.ID)
		if err != nil {
			internalError(w, "could not resume", err)
			return
		}
		n, _ := res.RowsAffected()
		// Reopen the progress thread the cancel closed.
		if _, err := execUserToggle(r.Context(), s.DB, `UPDATE notifications
			SET is_read=0 WHERE park_id=? AND notification_type='aoi_progress'`,
			a.ID); err != nil {
			slog.Warn("aoi resume notif", "error", err)
		}
		writeJSON(w, http.StatusOK, map[string]any{"resumed": n, "aoi": a.ID})
		return
	}

	q := `UPDATE aoi_datasets SET state='pending', next_run_at=NULL,
	          lease_owner=NULL, lease_until=NULL
	      WHERE aoi_id = ? AND enabled = 1 AND state != 'running'`
	args := []any{a.ID}
	if only != "" {
		if !isKnownAOIDataset(only) {
			http.Error(w, "unknown dataset", http.StatusBadRequest)
			return
		}
		q += ` AND dataset = ?`
		args = append(args, only)
	} else {
		// Cursor reset only for the derived layers; the downloads keep theirs.
		q += ` AND dataset IN ('clip','fire_v5','deforestation','basin')`
	}
	// Same wait-it-out treatment as archive/cancel: this is the Resume button
	// on the progress card, and a batch job holding the writer must not turn it
	// into an error the user can do nothing about.
	res, err := execUserToggle(r.Context(), s.DB, q, args...)
	if err != nil {
		internalError(w, "could not requeue", err)
		return
	}
	n, _ := res.RowsAffected()
	writeJSON(w, http.StatusOK, map[string]any{"requeued": n, "aoi": a.ID})
}

// HandleAPIAOICancel — POST /api/aois/{id}/cancel
//
// Stop fetching. The one thing an owner watching a multi-day progress card
// genuinely needs and cannot express any other way: the ingest is spending
// FIRMS quota and hours of CPU on a question they have changed their mind
// about, and neither archiving (which is about the screen) nor deleting (which
// throws away what already landed) says it.
//
// It disables every unfinished dataset and releases any lease, so the next
// runner slice simply finds nothing to do. It does NOT touch cursors or
// derived rows: whatever already landed stays queryable, and /refresh re-enables
// a dataset to carry on from exactly where it stopped — cancelling is a pause
// with an honest name, not a rollback.
//
// A 'running' row is set back to 'pending' rather than 'failed': the runner
// that owns it may still be mid-unit, and its own interrupt path (which is the
// normal exit — see scripts/aoi_runner.py) will release the lease cleanly.
func (s *Server) HandleAPIAOICancel(w http.ResponseWriter, r *http.Request) {
	a := s.requireAOIOwner(w, r)
	if a == nil {
		return
	}
	res, err := execUserToggle(r.Context(), s.DB, `UPDATE aoi_datasets
		SET enabled=0, lease_owner=NULL, lease_until=NULL,
		    state=CASE WHEN state='running' THEN 'pending' ELSE state END
		WHERE aoi_id=? AND state != 'done' AND enabled=1`, a.ID)
	if err != nil {
		internalError(w, "could not cancel", err)
		return
	}
	n, _ := res.RowsAffected()
	// Close the progress thread so the card stops claiming work is coming.
	// Marked read rather than deleted: "we stopped" is itself history. Best
	// effort — the cancel itself already succeeded, and a stale unread badge is
	// not worth failing the request the user actually made.
	if _, err := execUserToggle(r.Context(), s.DB, `UPDATE notifications SET is_read=1
		WHERE park_id=? AND notification_type='aoi_progress'`, a.ID); err != nil {
		slog.Warn("aoi cancel notif", "error", err)
	}
	writeJSON(w, http.StatusOK, map[string]any{"cancelled": n, "aoi": a.ID})
}

func isKnownAOIDataset(name string) bool {
	for _, d := range defaultAOIDatasets {
		if d.name == name {
			return true
		}
	}
	return false
}

// HandleAPIAOIDelete — DELETE /api/aois/{id}
//
// Removes the AOI and every derived row keyed by its id. The derived rows are
// the point: they live in park-shaped tables (feature_geometries,
// park_settlements, osm_places, ...) and would otherwise be orphaned there
// forever, invisible to aoiExcludeSQL the moment the aois row is gone —
// silently polluting the global counters this whole design exists to protect.
func (s *Server) HandleAPIAOIDelete(w http.ResponseWriter, r *http.Request) {
	a := s.requireAOIOwner(w, r)
	if a == nil {
		return
	}
	// Ordered so the aois row goes LAST: while it exists, aoiExcludeSQL still
	// masks any row we have not deleted yet. Deleting it first would expose
	// the remainder for the duration of the transaction.
	stmts := []struct {
		sql  string
		args []any
	}{
		{`DELETE FROM feature_geometries WHERE park_id = ?`, []any{a.ID}},
		{`DELETE FROM park_settlements WHERE park_id = ?`, []any{a.ID}},
		{`DELETE FROM deforestation_events WHERE park_id = ?`, []any{a.ID}},
		{`DELETE FROM fire_narrative_cache WHERE park_id = ?`, []any{a.ID}},
		{`DELETE FROM aoi_fires WHERE aoi_id = ?`, []any{a.ID}},
		{`DELETE FROM aoi_parks WHERE aoi_id = ?`, []any{a.ID}},
		{`DELETE FROM aoi_datasets WHERE aoi_id = ?`, []any{a.ID}},
		{`DELETE FROM aoi_grants WHERE aoi_id = ?`, []any{a.ID}},
		{`DELETE FROM notifications WHERE park_id = ?`, []any{a.ID}},
		// osm_places / roads_heigit are keyed by the bare AOI id like every
		// other unit. Both spellings are deleted: rows written before
		// 2026-08-07 used an `aoi:<id>` scope key (see aoiExcludeSQL).
		{`DELETE FROM osm_places WHERE park_id IN (?, ?)`, []any{a.ID, "aoi:" + a.ID}},
		{`DELETE FROM roads_heigit WHERE park_id IN (?, ?)`, []any{a.ID, "aoi:" + a.ID}},
		{`DELETE FROM park_rivers_hydro WHERE park_id = ?`, []any{a.ID}},
		{`DELETE FROM park_lakes_hydro WHERE park_id = ?`, []any{a.ID}},
		{`DELETE FROM park_waterbodies WHERE park_id = ?`, []any{a.ID}},
		{`DELETE FROM aois WHERE id = ?`, []any{a.ID}},
	}
	tx, err := s.DB.Begin()
	if err != nil {
		internalError(w, "could not delete", err)
		return
	}
	defer tx.Rollback()
	for _, st := range stmts {
		if _, err := tx.Exec(st.sql, st.args...); err != nil {
			// A missing optional table must not block the delete; a real
			// failure on `aois` will surface at commit.
			if strings.Contains(err.Error(), "no such table") {
				continue
			}
			internalError(w, "could not delete", err)
			return
		}
	}
	if err := tx.Commit(); err != nil {
		internalError(w, "could not delete", err)
		return
	}
	if err := s.RefreshAOIIDs(); err != nil {
		slog.Warn("refresh aoi ids", "error", err)
	}
	writeJSON(w, http.StatusOK, map[string]any{"deleted": a.ID})
}

// ------------------------------------------------------------- notifications

// notifyAOIQueued opens the progress thread for a new AOI.
//
// Scoped to the AOI's own park_id so it inherits the same visibility story as
// every other AOI row, and typed 'aoi_progress' so the frontend can render it
// as a live progress card rather than a line of text.
func (s *Server) notifyAOIQueued(id, name string, est AOIEstimateResult) {
	data, _ := json.Marshal(map[string]any{
		"aoi_id": id, "aoi_name": name, "eta_days": est.Days,
		"eta_seconds": est.TotalSec, "human": est.Human,
		"datasets_total": countPlannedDatasets(est),
	})
	if _, err := s.DB.Exec(`INSERT INTO notifications
		(park_id, notification_type, title, message, reference_id,
		 reference_data, created_at)
		VALUES (?, 'aoi_progress', ?, ?, ?, ?, datetime('now'))`,
		id, "Area queued: "+name, est.Human, id, string(data)); err != nil {
		slog.Warn("aoi notify", "error", err)
	}
}

func countPlannedDatasets(est AOIEstimateResult) int {
	n := 0
	for _, d := range est.Datasets {
		if !d.Blocked {
			n++
		}
	}
	return n
}

// HandleAPIAOIProgress — GET /api/aois/{id}/progress
//
// The polling endpoint behind the live notification card. Deliberately tiny
// and cache-free: it is hit every few seconds while an ingest runs, and a
// cached body would freeze the progress bar at whatever the first caller saw.
func (s *Server) HandleAPIAOIProgress(w http.ResponseWriter, r *http.Request) {
	a := s.requireAOI(w, r, false)
	if a == nil {
		return
	}
	ds, err := s.loadAOIDatasets(a.ID)
	if err != nil {
		internalError(w, "could not read progress", err)
		return
	}
	done, running, planned, blocked := 0, 0, 0, 0
	stopped := 0
	var frac float64
	var current, detail string
	for _, d := range ds {
		if !d.Enabled {
			// Disabled-and-unfinished is what /cancel leaves behind. Counting
			// it as "not planned" would make a cancelled AOI report 100% ready,
			// which is the one number the card must never invent.
			if d.State != "done" && aoiBlockedDatasets[d.Dataset] == "" {
				stopped++
			}
			continue
		}
		if d.State == "blocked" || aoiBlockedDatasets[d.Dataset] != "" {
			blocked++
			continue
		}
		planned++
		switch d.State {
		case "done":
			done++
			frac += 1
		case "running":
			running++
			current, detail = d.Dataset, d.Detail
			if d.UnitsTotal > 0 {
				frac += float64(d.UnitsDone) / float64(d.UnitsTotal)
			}
		default:
			if d.UnitsTotal > 0 {
				frac += float64(d.UnitsDone) / float64(d.UnitsTotal)
			}
		}
	}
	pct := 0.0
	if planned > 0 {
		pct = 100 * frac / float64(planned)
	}
	state := "queued"
	switch {
	case done == planned && planned > 0:
		state = "ready"
	case running > 0:
		state = "running"
	case done > 0:
		state = "partial"
	}
	// Cancelled outranks everything except work actually in flight: if a runner
	// still holds a lease the honest answer is "running", and the next slice
	// will find the queue empty.
	// A superseded version's queue is disabled too (an edit forks: v1 keeps its
	// data and stops fetching). That is NOT the same sentence as the user
	// pressing Stop, and conflating them offered a Resume button that would
	// re-spend quota answering a question v2 already replaced. Report it so the
	// card can say so instead.
	superseded := a.SupersededBy != ""
	if stopped > 0 && running == 0 {
		state = "cancelled"
		if superseded {
			state = "superseded"
		}
	}
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusOK, map[string]any{
		"aoi_id": a.ID, "name": a.Name, "state": state,
		"datasets_done": done, "datasets_total": planned,
		"datasets_blocked": blocked, "datasets_stopped": stopped,
		"archived": a.State == "archived", "is_owner": a.IsOwner,
		"superseded_by": a.SupersededBy,
		"percent":       math.Round(pct*10) / 10,
		"current":       current, "detail": detail,
	})
}

// ---------------------------------------------------------------- runner kick
//
// Not wired to create (see HandleAPIAOICreate). This exists for the admin
// panel: an operator who has just fixed something wants the queue to move now,
// not at noon. It shells out to the same runner with a short budget so the
// lease discipline is unchanged — there is exactly one implementation of "work
// a unit", and it is scripts/aoi_runner.py.
func (s *Server) HandleAPIAOIKick(w http.ResponseWriter, r *http.Request) {
	a := s.requireAOIOwner(w, r)
	if a == nil {
		return
	}
	if !ValidAOIID(a.ID) {
		http.Error(w, "invalid id", http.StatusBadRequest)
		return
	}
	cmd := exec.Command("python3", "scripts/aoi_runner.py",
		"--aoi", a.ID, "--minutes", "20", "--budget", "40")
	if err := cmd.Start(); err != nil {
		internalError(w, "could not start runner", err)
		return
	}
	go func() { _ = cmd.Wait() }()
	writeJSON(w, http.StatusAccepted, map[string]any{"started": a.ID})
}

var _ = sql.ErrNoRows
