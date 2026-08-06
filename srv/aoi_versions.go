package srv

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"strings"
	"time"
)

// AOI versioning — editing an area forks it instead of mutating it.
//
// The reason is specific to what an AOI is. It is not a saved view; it is a
// question ("what happened inside this polygon, between these dates") plus the
// days of ingest that answered it. Its derived rows — fire trajectories,
// settlements, deforestation events, narratives — are keyed by its id and were
// computed for exactly that polygon and that window.
//
// Mutating either in place would leave those rows in the database as answers to
// a question nobody asked, with nothing to indicate it: the id did not change,
// so the popup, the share links and the report would all keep presenting stale
// output as current. The alternative — deleting and recomputing on edit — throws
// away days of work and breaks every existing share link.
//
// So an edit creates version N+1 in the same lineage and archives version N.
// Nothing is deleted. Where the polygons overlap, the new version's ingest is
// mostly cache hits anyway, because ingest is keyed by geography rather than by
// owner (docs/PLAN_AOI_OVERLAY.md §0 rule 2).
//
// Versions are told apart by their analysis window, which is what actually
// differs between them in practice — hence aoiVersionLabel().

// aoiVersionLabel is how a version identifies itself to a human.
//
// Not "v3": a user does not remember which numbered version was which. What
// they remember is the question — "the 2024 one", "the one that goes back to
// 2020". So the label leads with the window and keeps the number as a
// tiebreaker for the case the windows are identical (a geometry-only edit).
func aoiVersionLabel(a *AOI) string {
	from, to := a.FromDate, a.ToDate
	if from == "" {
		from = "all"
	}
	if to == "" {
		to = "now"
	}
	label := from + " → " + to
	if a.Version > 1 {
		label += " (v" + itoa(a.Version) + ")"
	}
	return label
}

type aoiVersionInfo struct {
	ID         string  `json:"id"`
	Name       string  `json:"name"`
	Version    int     `json:"version"`
	Label      string  `json:"label"`
	FromDate   string  `json:"from_date,omitempty"`
	ToDate     string  `json:"to_date,omitempty"`
	AreaKm2    float64 `json:"area_km2"`
	State      string  `json:"state"`
	Archived   bool    `json:"archived"`
	IsCurrent  bool    `json:"is_current"`
	CreatedAt  string  `json:"created_at,omitempty"`
	GeomChange bool    `json:"geometry_changed,omitempty"`
	// Restore is owner-only; the UI needs to know before offering the button,
	// or a shared-with-me lineage grows Restore buttons that 404.
	IsOwner bool `json:"is_owner"`
}

// loadAOIVersions returns every version in a lineage the principal may see,
// newest first.
func (s *Server) loadAOIVersions(lineage string, principalID int64) ([]aoiVersionInfo, error) {
	rows, err := s.DB.Query(`SELECT `+aoiCols+` FROM aois
		WHERE COALESCE(lineage_id, id) = ? AND `+aoiVisibleSQL+`
		ORDER BY version DESC`,
		lineage, principalID, principalID, principalID, principalID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []aoiVersionInfo
	var prevArea float64
	for rows.Next() {
		a, err := scanAOI(rows, false)
		if err != nil {
			return nil, err
		}
		v := aoiVersionInfo{
			ID: a.ID, Name: a.Name, Version: a.Version, Label: aoiVersionLabel(a),
			FromDate: a.FromDate, ToDate: a.ToDate, AreaKm2: a.AreaKm2,
			State: a.State, Archived: a.State == "archived",
			IsCurrent: a.State != "archived", CreatedAt: a.CreatedAt,
			IsOwner:   principalID != 0 && a.OwnerID == principalID,
		}
		// A geometry change is worth flagging because it is the one difference
		// the window label cannot express.
		if prevArea != 0 && absf(a.AreaKm2-prevArea) > 0.5 {
			v.GeomChange = true
		}
		prevArea = a.AreaKm2
		out = append(out, v)
	}
	return out, rows.Err()
}

func absf(f float64) float64 {
	if f < 0 {
		return -f
	}
	return f
}

// HandleAPIAOIVersions — GET /api/aois/{id}/versions
func (s *Server) HandleAPIAOIVersions(w http.ResponseWriter, r *http.Request) {
	a := s.requireAOI(w, r, false)
	if a == nil {
		return
	}
	lineage := a.LineageID
	if lineage == "" {
		lineage = a.ID
	}
	vs, err := s.loadAOIVersions(lineage, s.RequestPrincipalID(r))
	if err != nil {
		internalError(w, "could not load versions", err)
		return
	}
	if vs == nil {
		vs = []aoiVersionInfo{}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"lineage_id": lineage, "versions": vs, "current": a.ID})
}

type aoiEditReq struct {
	Name     string   `json:"name"`
	Geometry *aoiGeom `json:"geometry"`
	FromDate string   `json:"from_date"`
	ToDate   string   `json:"to_date"`
	Notes    string   `json:"notes"`
}

// HandleAPIAOIEdit — POST /api/aois/{id}/edit
//
// Creates the next version and archives this one. Both the geometry and the
// window are optional: omitting one carries it forward, so "same shape, new
// dates" (the common case, since the window comes from the time slider) is a
// request with no coordinates in it at all.
//
// The new version starts with an empty queue and fills over days like any new
// AOI. It deliberately does NOT inherit the old version's derived rows: they
// were computed for a different question. What it does inherit for free is the
// *downloads* — FIRMS windows, GFW tiles, GHSL tiles are all cached by
// geography, so an edit that only moves the end date re-fetches almost nothing.
func (s *Server) HandleAPIAOIEdit(w http.ResponseWriter, r *http.Request) {
	old := s.requireAOIOwner(w, r)
	if old == nil {
		return
	}
	if old.State == "archived" {
		// Editing an archived version would fork history sideways and give two
		// live heads in one lineage. Restore it first (that is itself an edit
		// of the current head), or edit the current one.
		http.Error(w, "this version is archived — edit the current one", http.StatusConflict)
		return
	}
	var req aoiEditReq
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&req); err != nil {
		http.Error(w, "bad JSON", http.StatusBadRequest)
		return
	}

	// Carry forward whatever was not supplied.
	name := strings.TrimSpace(req.Name)
	if name == "" {
		name = old.Name
	}
	if len(name) > 80 {
		http.Error(w, "name too long", http.StatusBadRequest)
		return
	}
	from, to := req.FromDate, req.ToDate
	if from == "" {
		from = old.FromDate
	}
	if to == "" {
		to = old.ToDate
	}
	for _, d := range []string{from, to} {
		if d != "" && !isoDateRe.MatchString(d) {
			http.Error(w, "dates must be YYYY-MM-DD", http.StatusBadRequest)
			return
		}
	}

	var geomJSON string
	var bbox [4]float64
	var area float64
	if req.Geometry != nil {
		var err error
		bbox, area, err = validateAOIGeom(req.Geometry)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		b, _ := json.Marshal(map[string]any{
			"type": req.Geometry.Type, "coordinates": req.Geometry.Coordinates})
		geomJSON = string(b)
	} else {
		// Reload with geometry: requireAOIOwner skipped it.
		full, err := s.GetAOI(old.ID, s.RequestPrincipalID(r), true)
		if err != nil {
			internalError(w, "could not read geometry", err)
			return
		}
		geomJSON = string(full.Geometry)
		bbox, area = full.BBox, full.AreaKm2
	}

	// Nothing actually changed? Do not mint a version for a no-op — a lineage
	// full of identical entries is worse than no history at all.
	if req.Geometry == nil && name == old.Name && from == old.FromDate &&
		to == old.ToDate && strings.TrimSpace(req.Notes) == "" {
		writeJSON(w, http.StatusOK, map[string]any{
			"aoi": old, "unchanged": true})
		return
	}

	lineage := old.LineageID
	if lineage == "" {
		lineage = old.ID
	}
	newID := slugAOIID(name)
	if newID == old.ID {
		newID += "b" // slug carries a second-resolution timestamp; be safe
	}
	notes := strings.TrimSpace(req.Notes)
	if notes == "" {
		notes = old.Notes
	}

	tx, err := s.DB.Begin()
	if err != nil {
		internalError(w, "could not edit", err)
		return
	}
	defer tx.Rollback()

	var nextVer int
	if err := tx.QueryRow(`SELECT COALESCE(MAX(version),0)+1 FROM aois
		WHERE COALESCE(lineage_id,id) = ?`, lineage).Scan(&nextVer); err != nil {
		internalError(w, "could not version", err)
		return
	}
	if _, err := tx.Exec(`
		INSERT INTO aois (id, name, geometry, bbox_minx, bbox_miny, bbox_maxx,
			bbox_maxy, area_km2, from_date, to_date, owner_principal_id,
			visibility, state, notes, lineage_id, version)
		VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)`,
		newID, name, geomJSON, bbox[0], bbox[1], bbox[2], bbox[3], area,
		nullIfEmpty(from), nullIfEmpty(to), old.OwnerID, old.Visibility,
		nullIfEmpty(notes), lineage, nextVer); err != nil {
		internalError(w, "could not create version", err)
		return
	}
	for _, d := range defaultAOIDatasets {
		if _, err := tx.Exec(`INSERT OR IGNORE INTO aoi_datasets
			(aoi_id, dataset, priority, depends_on) VALUES (?,?,?,?)`,
			newID, d.name, d.priority, nullIfEmpty(d.dependsOn)); err != nil {
			internalError(w, "could not queue datasets", err)
			return
		}
	}
	// Archive the old head. Its rows stay exactly where they are: the whole
	// point is that a link to this version keeps resolving to what it showed.
	if _, err := tx.Exec(`UPDATE aois SET state='archived', superseded_by=?,
		archived_at=? WHERE id=?`,
		newID, time.Now().UTC().Format("2006-01-02 15:04:05"), old.ID); err != nil {
		internalError(w, "could not archive previous version", err)
		return
	}
	// Its queue must stop, or the runner keeps spending quota answering the
	// superseded question.
	if _, err := tx.Exec(`UPDATE aoi_datasets SET enabled=0
		WHERE aoi_id=? AND state != 'done'`, old.ID); err != nil {
		internalError(w, "could not stop previous queue", err)
		return
	}
	if err := tx.Commit(); err != nil {
		internalError(w, "could not edit", err)
		return
	}
	if err := s.RefreshAOIIDs(); err != nil {
		slog.Warn("refresh aoi ids", "error", err)
	}

	est := estimateAOI(bbox, area, windowDays(from, to), s.countriesForBBox(bbox))
	s.notifyAOIQueued(newID, name+" · "+from+"→"+to, est)

	a, err := s.GetAOI(newID, s.RequestPrincipalID(r), true)
	if err != nil {
		internalError(w, "created but could not read back", err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{
		"aoi": a, "estimate": est, "archived": old.ID, "version": nextVer})
}

// HandleAPIAOIRestore — POST /api/aois/{id}/restore
//
// Brings an archived version back as the live head, archiving whatever head
// currently exists in that lineage. It does not create a version: the data is
// already there, this only moves which one is "current". Cheap and instant,
// which is the whole payoff of never deleting.
func (s *Server) HandleAPIAOIRestore(w http.ResponseWriter, r *http.Request) {
	a := s.requireAOIOwner(w, r)
	if a == nil {
		return
	}
	if a.State != "archived" {
		writeJSON(w, http.StatusOK, map[string]any{"aoi": a, "unchanged": true})
		return
	}
	lineage := a.LineageID
	if lineage == "" {
		lineage = a.ID
	}
	tx, err := s.DB.Begin()
	if err != nil {
		internalError(w, "could not restore", err)
		return
	}
	defer tx.Rollback()
	if _, err := tx.Exec(`UPDATE aois SET state='archived', archived_at=?
		WHERE COALESCE(lineage_id,id)=? AND state != 'archived' AND id != ?`,
		time.Now().UTC().Format("2006-01-02 15:04:05"), lineage, a.ID); err != nil {
		internalError(w, "could not archive current head", err)
		return
	}
	if _, err := tx.Exec(`UPDATE aois SET state='pending', archived_at=NULL,
		superseded_by=NULL WHERE id=?`, a.ID); err != nil {
		internalError(w, "could not restore", err)
		return
	}
	// Re-enable its queue: a restored version may well have been archived
	// mid-ingest, and it should carry on from its cursor.
	if _, err := tx.Exec(`UPDATE aoi_datasets SET enabled=1 WHERE aoi_id=?`, a.ID); err != nil {
		internalError(w, "could not resume queue", err)
		return
	}
	if err := tx.Commit(); err != nil {
		internalError(w, "could not restore", err)
		return
	}
	if err := s.RefreshAOIIDs(); err != nil {
		slog.Warn("refresh aoi ids", "error", err)
	}
	writeJSON(w, http.StatusOK, map[string]any{"restored": a.ID, "lineage_id": lineage})
}

// HandleAPIAOISearch — GET /api/aois/search?q=
//
// The way archived versions come back. Scoped by the same aoiVisibleSQL as
// everything else, so it can only ever surface what the principal's password
// already grants — searching is not a way around visibility, it is a way back
// to your own history.
//
// Deliberately includes archived rows (that is the point) and labels them, so
// the UI can show "Chinko buffer · 2024-01-01 → 2025-12-31 (archived)".
func (s *Server) HandleAPIAOISearch(w http.ResponseWriter, r *http.Request) {
	principal := s.RequestPrincipalID(r)
	q := strings.TrimSpace(r.URL.Query().Get("q"))
	includeArchived := r.URL.Query().Get("archived") != "0"

	sqlStr := `SELECT ` + aoiCols + ` FROM aois WHERE ` + aoiVisibleSQL
	args := []any{principal, principal, principal, principal}
	if q != "" {
		sqlStr += ` AND (name LIKE ? OR id LIKE ?)`
		like := "%" + q + "%"
		args = append(args, like, like)
	}
	if !includeArchived {
		sqlStr += aoiActiveSQL
	}
	// Live versions first, then most recent archives: someone searching for a
	// name almost always wants the one that is current.
	sqlStr += ` ORDER BY (state='archived'), version DESC, name LIMIT 50`

	rows, err := s.DB.Query(sqlStr, args...)
	if err != nil {
		internalError(w, "search failed", err)
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		a, err := scanAOI(rows, false)
		if err != nil {
			continue
		}
		a.IsOwner = principal != 0 && a.OwnerID == principal
		out = append(out, map[string]any{
			"id": a.ID, "name": a.Name, "label": aoiVersionLabel(a),
			"version": a.Version, "lineage_id": a.LineageID,
			"archived": a.State == "archived", "area_km2": a.AreaKm2,
			"from_date": a.FromDate, "to_date": a.ToDate,
			"bbox": a.BBox, "is_owner": a.IsOwner,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"results": out, "count": len(out)})
}
