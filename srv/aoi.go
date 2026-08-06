package srv

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"log/slog"
	"net/http"
	"regexp"
	"strings"
)

// Areas of interest (AOI): a user-drawn polygon promoted to a first-class
// object — arbitrary geometry, a fixed analysis window, an owner, and data
// fetched *for it* over days by scripts/aoi_runner.py.
//
// The single most important property is what an AOI is NOT: it is not a park.
// It lives in its own table and its own id space, so it can never enter
// keystones_with_boundaries.json, can never be a
// fire_detections.protected_area_id, and can never steal detections from the
// parks it overlaps (docs/PLAN_AOI_OVERLAY.md §3). It also means the ~40
// /api/parks/{id}/* endpoints need no visibility audit: a separate route
// prefix with one middleware is the whole enforcement surface.

// aoiIDRe validates AOI identifiers before they reach file paths or
// subprocess arguments: 'XSA_Study_Area', 'aoi_V1StGXR8Z5'.
var aoiIDRe = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_'\-.]*$`)

// ValidAOIID reports whether s looks like a legitimate AOI identifier.
func ValidAOIID(s string) bool {
	return len(s) <= 80 && aoiIDRe.MatchString(s)
}

// ---------------------------------------------------------------- principals

// principalRef is the stable, non-secret handle for an access password.
// Storing sha256(pwd)[:16] means the AOI tables never contain a credential
// and a leaked db.sqlite3 does not hand over the alpha passwords.
func principalRef(pwd string) string {
	sum := sha256.Sum256([]byte(pwd))
	return hex.EncodeToString(sum[:])[:16]
}

// SeedPrincipals ensures one 'password' principal exists per configured access
// password. Runs at startup because ACCESS_PASSWORDS lives in the environment,
// not in the .sql migration.
func (s *Server) SeedPrincipals() error {
	for _, pwd := range validPasswords {
		pwd = strings.TrimSpace(pwd)
		if pwd == "" {
			continue
		}
		// label: enough to recognise the tenant in the admin panel, not
		// enough to reconstruct the password.
		label := pwd[:min(3, len(pwd))] + "…"
		if _, err := s.DB.Exec(
			`INSERT OR IGNORE INTO principals (kind, ref, label) VALUES ('password', ?, ?)`,
			principalRef(pwd), label); err != nil {
			return err
		}
	}
	return nil
}

// RequestPrincipalID resolves the principal for a request, or 0 if none.
func (s *Server) RequestPrincipalID(r *http.Request) int64 {
	pwd := RequestPwd(r)
	if pwd == "" {
		return 0
	}
	var id int64
	err := s.DB.QueryRow(
		`SELECT id FROM principals WHERE kind='password' AND ref=?`,
		principalRef(pwd)).Scan(&id)
	if err != nil {
		return 0
	}
	return id
}

// visibilityFingerprint identifies the *audience* of a response for the
// response cache. Without it a Chink0-visible body would be served to
// everyone from the shared cache (docs/PLAN_AOI_OVERLAY.md §9).
func visibilityFingerprint(r *http.Request) string {
	pwd := RequestPwd(r)
	if pwd == "" {
		return "anon"
	}
	return principalRef(pwd)[:8]
}

// ---------------------------------------------------------------------- AOI

type AOI struct {
	ID         string          `json:"id"`
	Name       string          `json:"name"`
	Geometry   json.RawMessage `json:"geometry,omitempty"`
	BBox       [4]float64      `json:"bbox"`
	AreaKm2    float64         `json:"area_km2"`
	FromDate   string          `json:"from_date,omitempty"`
	ToDate     string          `json:"to_date,omitempty"`
	OwnerID    int64           `json:"-"`
	Visibility string          `json:"visibility"`
	State      string          `json:"state"`
	CreatedAt  string          `json:"created_at,omitempty"`
	Notes      string          `json:"notes,omitempty"`
	IsOwner    bool            `json:"is_owner"`
}

const aoiCols = `id, name, geometry,
	COALESCE(bbox_minx,0), COALESCE(bbox_miny,0), COALESCE(bbox_maxx,0), COALESCE(bbox_maxy,0),
	COALESCE(area_km2,0), COALESCE(from_date,''), COALESCE(to_date,''),
	COALESCE(owner_principal_id,0), visibility, state,
	COALESCE(created_at,''), COALESCE(notes,'')`

func scanAOI(sc interface{ Scan(...any) error }, withGeometry bool) (*AOI, error) {
	var a AOI
	var geo string
	if err := sc.Scan(&a.ID, &a.Name, &geo, &a.BBox[0], &a.BBox[1], &a.BBox[2],
		&a.BBox[3], &a.AreaKm2, &a.FromDate, &a.ToDate, &a.OwnerID,
		&a.Visibility, &a.State, &a.CreatedAt, &a.Notes); err != nil {
		return nil, err
	}
	if withGeometry {
		a.Geometry = json.RawMessage(geo)
	}
	return &a, nil
}

// aoiVisibleSQL is the one place visibility is decided. Everything that lists
// or fetches an AOI must go through it.
const aoiVisibleSQL = `(visibility='public'
	OR (? != 0 AND owner_principal_id = ?)
	OR (? != 0 AND EXISTS (SELECT 1 FROM aoi_grants g
		WHERE g.aoi_id = aois.id AND g.principal_id = ?)))`

// ListAOIs returns the AOIs a principal may see.
func (s *Server) ListAOIs(principalID int64, withGeometry bool) ([]*AOI, error) {
	rows, err := s.DB.Query(`SELECT `+aoiCols+` FROM aois WHERE `+aoiVisibleSQL+
		` ORDER BY name`, principalID, principalID, principalID, principalID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*AOI
	for rows.Next() {
		a, err := scanAOI(rows, withGeometry)
		if err != nil {
			return nil, err
		}
		a.IsOwner = principalID != 0 && a.OwnerID == principalID
		out = append(out, a)
	}
	return out, rows.Err()
}

// GetAOI loads one AOI if the principal may see it; sql.ErrNoRows otherwise.
// Callers must translate that into 404 — never 403, so an id is not an oracle.
func (s *Server) GetAOI(id string, principalID int64, withGeometry bool) (*AOI, error) {
	row := s.DB.QueryRow(`SELECT `+aoiCols+` FROM aois WHERE id = ? AND `+
		aoiVisibleSQL, id, principalID, principalID, principalID, principalID)
	a, err := scanAOI(row, withGeometry)
	if err != nil {
		return nil, err
	}
	a.IsOwner = principalID != 0 && a.OwnerID == principalID
	return a, nil
}

// ------------------------------------------------------------- middleware

type aoiCtxKey struct{}

// AOIMiddleware validates the id in /api/aois/{id}/... and rejects malformed
// ones. Visibility itself is resolved per handler via GetAOI (which needs the
// DB row anyway); this middleware exists for the same reason
// ParkIDMiddleware does: keep injection-shaped ids out of the handlers.
func AOIMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if rest, ok := strings.CutPrefix(r.URL.Path, "/api/aois/"); ok {
			id, _, _ := strings.Cut(rest, "/")
			if id != "" && !ValidAOIID(id) {
				http.Error(w, "invalid aoi id", http.StatusBadRequest)
				return
			}
		}
		if p := r.URL.Query().Get("aoi"); p != "" && !ValidAOIID(p) {
			http.Error(w, "invalid aoi id", http.StatusBadRequest)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// requireAOI resolves the path AOI for a request, writing 404 and returning
// nil when it does not exist *or* the principal may not see it.
func (s *Server) requireAOI(w http.ResponseWriter, r *http.Request, withGeometry bool) *AOI {
	id := r.PathValue("id")
	if !ValidAOIID(id) {
		http.Error(w, "invalid aoi id", http.StatusBadRequest)
		return nil
	}
	a, err := s.GetAOI(id, s.RequestPrincipalID(r), withGeometry)
	if err == sql.ErrNoRows {
		http.NotFound(w, r)
		return nil
	} else if err != nil {
		slog.Warn("aoi lookup", "id", id, "error", err)
		http.Error(w, "database error", http.StatusInternalServerError)
		return nil
	}
	return a
}

// ---------------------------------------------------------------- datasets

type aoiDataset struct {
	Dataset    string  `json:"dataset"`
	Enabled    bool    `json:"enabled"`
	State      string  `json:"state"`
	Priority   int     `json:"priority"`
	DependsOn  string  `json:"depends_on,omitempty"`
	UnitsTotal int     `json:"units_total"`
	UnitsDone  int     `json:"units_done"`
	Coverage   float64 `json:"coverage"`
	LastRunAt  string  `json:"last_run_at,omitempty"`
	Detail     string  `json:"detail,omitempty"`
}

func (s *Server) loadAOIDatasets(aoiID string) ([]aoiDataset, error) {
	rows, err := s.DB.Query(`
		SELECT dataset, enabled, state, priority, COALESCE(depends_on,''),
		       COALESCE(units_total,0), COALESCE(units_done,0),
		       COALESCE(coverage,0), COALESCE(last_run_at,''), COALESCE(detail,'')
		FROM aoi_datasets WHERE aoi_id = ? ORDER BY priority, dataset`, aoiID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []aoiDataset{}
	for rows.Next() {
		var d aoiDataset
		if err := rows.Scan(&d.Dataset, &d.Enabled, &d.State, &d.Priority,
			&d.DependsOn, &d.UnitsTotal, &d.UnitsDone, &d.Coverage,
			&d.LastRunAt, &d.Detail); err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

// ----------------------------------------------------------------- handlers

// HandleAPIAOIList — GET /api/aois
func (s *Server) HandleAPIAOIList(w http.ResponseWriter, r *http.Request) {
	list, err := s.ListAOIs(s.RequestPrincipalID(r), r.URL.Query().Get("geometry") == "1")
	if err != nil {
		slog.Warn("aoi list", "error", err)
		http.Error(w, "database error", http.StatusInternalServerError)
		return
	}
	if list == nil {
		list = []*AOI{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"aois": list, "count": len(list)})
}

// HandleAPIAOIGet — GET /api/aois/{id}: metadata + per-dataset coverage.
func (s *Server) HandleAPIAOIGet(w http.ResponseWriter, r *http.Request) {
	a := s.requireAOI(w, r, r.URL.Query().Get("geometry") != "0")
	if a == nil {
		return
	}
	ds, err := s.loadAOIDatasets(a.ID)
	if err != nil {
		slog.Warn("aoi datasets", "id", a.ID, "error", err)
		http.Error(w, "database error", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"aoi": a, "datasets": ds})
}
