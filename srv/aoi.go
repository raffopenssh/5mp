package srv

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"log/slog"
	"math"
	"net/http"
	"regexp"
	"strings"
	"sync"
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
		// A guest capability acts AS the principal that issued it: the point
		// of a shared link is that the recipient sees the sender's private
		// AOI without becoming the sender. Everything downstream --
		// GetAOI(..., principalID), aoiGate, aoi_grants -- then needs no
		// notion of "guest" at all. See srv/shortlink.go.
		if g := GuestFromRequest(r); g != nil {
			return g.PrincipalID
		}
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
		// A GUEST IS NOT ANONYMOUS. It borrows a principal and therefore sees
		// that principal's private AOIs; fingerprinting it as "anon" would
		// file those bodies in the slot every unauthenticated reader draws
		// from -- the exact leak this function exists to prevent, arriving by
		// the back door. Its own slot, keyed on the slug: two guests of the
		// same principal legitimately share a body, but only with each other.
		if g := GuestFromRequest(r); g != nil {
			return "g" + g.Slug[:min(7, len(g.Slug))]
		}
		return "anon"
	}
	return principalRef(pwd)[:8]
}

// aoiIDSet is every AOI id, cached at startup. It exists to plug the one hole
// created by reusing park-shaped storage: AOI rows live in feature_geometries
// and fire_narrative_cache keyed by their own id, and an id like
// 'XSA_Study_Area' happens to satisfy parkIDRe. Without this, a private AOI
// would be readable through /api/parks/XSA_Study_Area/fire-narrative with no
// visibility check at all. Park routes therefore 404 on any AOI id; AOI data
// is only reachable through /api/aois/*.
var (
	aoiIDMu  sync.RWMutex
	aoiIDSet = map[string]bool{}
)

// RefreshAOIIDs reloads the id set. Called at startup and after any create or
// delete — cheap (one column, a handful of rows).
func (s *Server) RefreshAOIIDs() error {
	rows, err := s.DB.Query(`SELECT id FROM aois`)
	if err != nil {
		return err
	}
	defer rows.Close()
	set := map[string]bool{}
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return err
		}
		set[id] = true
	}
	aoiIDMu.Lock()
	aoiIDSet = set
	aoiIDMu.Unlock()
	return rows.Err()
}

// IsAOIID reports whether an id belongs to an AOI rather than a park.
func IsAOIID(id string) bool {
	aoiIDMu.RLock()
	defer aoiIDMu.RUnlock()
	return aoiIDSet[id]
}

// aoiExcludeSQL removes AOI-owned rows from a query over park-shaped storage
// (leading AND), the same shape as scannerInjectedSQLFilter.
//
// Two independent reasons, either of which is sufficient:
//
//  1. Privacy. A private AOI's derived rows sit in feature_geometries next to
//     park rows. Any query that is NOT keyed by an explicit park_id — the
//     bbox feature browser, the animator's dated trajectories, the global
//     dashboard counters — would otherwise serve them to every principal.
//     Visibility is enforced at the /api/aois/* boundary; these endpoints are
//     deliberately unfiltered raw-geography endpoints (§3), so the AOI rows
//     have to be kept out of them instead.
//
//  2. Double counting. AOI trajectories are rebuilt over ground the parks
//     already cover — XSA overlaps four parks — so summing stat_value across
//     feature_geometries without this filter inflates every global total.
//
// Cheap: `aois` holds a handful of rows and the subquery is a scan of a tiny
// table. Written as a subquery rather than an inlined id list so a create or
// delete cannot leave a stale filter behind.
func aoiExcludeSQL(col string) string {
	aoiIDMu.RLock()
	n := len(aoiIDSet)
	aoiIDMu.RUnlock()
	if n == 0 {
		return ""
	}
	// Every AOI-derived row in a park-shaped table is keyed by the bare AOI id,
	// so the NOT IN over the tiny `aois` table covers all of them. The
	// `NOT LIKE 'aoi:%'` clause is kept for pre-2026-08-07 rows: run_osm used to
	// write osm_places/roads_heigit under an `aoi:<id>` scope key that NO read
	// path resolved, so the AOI's real OSM ingest (12,956 roads, 432 places) was
	// invisible to its own popup, narratives and exports
	// (AGENTS.md "Areas of interest"). New writers use the bare id.
	return " AND " + col + " NOT IN (SELECT id FROM aois) AND " + col +
		" NOT LIKE 'aoi:%'"
}

// aoiScopeSQL replaces aoiExcludeSQL's "no AOI at all" with "this AOI only"
// (leading AND).
//
// The exclusion filter is the right default for every bbox-keyed endpoint, but
// it also made an AOI invisible to the one feature that exists to show it: the
// animator asks /api/fire-anim-trajectories for a rectangle, and over an AOI's
// concave interior that rectangle contains 38,725 AOI-owned trajectories which
// were all filtered out — days of ingest, unreachable, the gap animating black.
//
// So the raw-geography endpoints take an optional ?aoi=<id>, resolved through
// GetAOI by aoiScopeParam, which re-checks visibility and never trusts the
// parameter. When it is set the query serves *that AOI's rows and nothing
// else*, park rows included:
//
//   - the AOI's own chain (aoi_fires -> v5, GHSL, Hansen+alerts) is computed
//     over the WHOLE polygon, including the ~11% of it that lies inside parks,
//     so its rows are already a complete answer for that geometry;
//   - keeping the park rows too would therefore draw the overlap twice, and
//     the canvas clip means park rows outside the polygon never render anyway;
//   - every other AOI stays excluded, so privacy is unchanged.
//
// Endpoints that SUM (dashboard totals, /api/stats) must keep using
// aoiExcludeSQL: this one paints pixels, it does not add anything up.
func aoiScopeSQL(col, aoiID string) string {
	if aoiID == "" {
		return aoiExcludeSQL(col)
	}
	return " AND (" + col + " = " + quoteSQLString(aoiID) +
		" OR " + col + " = " + quoteSQLString("aoi:"+aoiID) + ")"
}

// areaScopeSQL scopes a park-keyed column to ONE area, park or AOI.
//
// aoiScopeSQL answers this for an AOI; a park needs the same thing and could
// not have it, which is why the stats panel had no way to say "these numbers
// are Chinko's". The two cases differ only in the alias: an AOI's derived rows
// exist under both `<id>` and `aoi:<id>`, a park's only under its own id.
//
// The empty id keeps aoiExcludeSQL's default — unscoped means "every park, and
// no AOI", because summing an AOI's rows next to the parks it overlaps counts
// the overlap twice (srv/aoi.go).
func areaScopeSQL(col, areaID string) string {
	if areaID == "" {
		return aoiExcludeSQL(col)
	}
	if IsAOIID(areaID) {
		return aoiScopeSQL(col, areaID)
	}
	return " AND " + col + " = " + quoteSQLString(areaID)
}

// quoteSQLString is only ever fed an id already validated by ValidAOIID (no
// quotes possible); the doubling is belt and braces because the result is
// concatenated into SQL rather than bound, which aoiExcludeSQL's shape forces.
func quoteSQLString(s string) string {
	return "'" + strings.ReplaceAll(s, "'", "''") + "'"
}

// aoiScopeParam reads ?aoi= and returns the id only if this request may see
// it. An invisible or unknown id yields "" — the endpoint then behaves exactly
// as before, so a guessed id is not an oracle and not an error either.
func (s *Server) aoiScopeParam(r *http.Request) string {
	id := r.URL.Query().Get("aoi")
	if id == "" || !ValidAOIID(id) || !IsAOIID(id) {
		return ""
	}
	if _, err := s.GetAOI(id, s.RequestPrincipalID(r), false); err != nil {
		return ""
	}
	return id
}

// areaScopeParam reads ?aoi= or ?park_focus= and returns the id only if this
// request may see it. Same contract as aoiScopeParam in both directions: an
// unknown or invisible id yields "", so the endpoint answers exactly as it did
// before and an id is never an oracle.
//
// `park_focus=`, not `park=`: ParkIDMiddleware validates every `?park=` in the
// app and 404s an AOI id in one, so reusing it would make "focus on this area"
// a hard failure for half its inputs. One parameter that accepts both kinds of
// id is the point — focus is one idea, and the panel says the same sentence
// whichever kind of area is in it.
func (s *Server) areaScopeParam(r *http.Request) string {
	if id := s.aoiScopeParam(r); id != "" {
		return id
	}
	id := strings.TrimSpace(r.URL.Query().Get("park_focus"))
	if id == "" || !ValidParkID(id) || IsAOIID(id) {
		return ""
	}
	if s.AreaStore == nil || s.AreaStore.GetByID(id) == nil {
		return ""
	}
	return id
}

// areaScopeName is what the panel prints for a scope id.
func (s *Server) areaScopeName(r *http.Request, areaID string) string {
	if areaID == "" {
		return ""
	}
	if IsAOIID(areaID) {
		if a, err := s.GetAOI(areaID, s.RequestPrincipalID(r), false); err == nil {
			return a.Name
		}
		return areaID
	}
	if s.AreaStore != nil {
		if a := s.AreaStore.GetByID(areaID); a != nil && a.Name != "" {
			return a.Name
		}
	}
	return areaID
}

// aoiNotifSQLFilter keeps another principal's AOI out of the notification
// list (leading AND, plus the args to append).
//
// 'aoi_progress' rows are keyed by park_id = the AOI id and carry the AOI's
// *name* in their title, so without this every principal's notification panel
// would announce the existence and name of every private AOI — the polygon is
// the secret, but so is the fact that someone is watching one. Same shape as
// miningNotifSQLFilter(), except it needs the principal, hence the args.
//
// Written as "not an AOI at all, or an AOI you may see" so it is a no-op for
// the 99.99% of rows that are park notifications.
func aoiNotifSQLFilter(col string, principalID int64) (string, []any) {
	aoiIDMu.RLock()
	n := len(aoiIDSet)
	aoiIDMu.RUnlock()
	if n == 0 {
		return "", nil
	}
	sqlStr := " AND (" + col + " NOT IN (SELECT id FROM aois)" +
		" OR EXISTS (SELECT 1 FROM aois WHERE aois.id = " + col +
		" AND " + aoiVisibleSQL + "))"
	return sqlStr, []any{principalID, principalID, principalID, principalID}
}

// AOIHomeView is the viewport a session should open on: the union bbox of the
// AOIs this principal may see. An AOI is the reason its owner logs in, so
// landing on the whole of Africa makes them hunt for their own polygon; a
// tenant with no AOI keeps the continental default.
//
// Only the *bbox* is computed here. The zoom that frames it depends on the
// container size, which the server does not know — the client fits it (and a
// URL viewport always outranks this: a link's view is what the link means).
type AOIHomeView struct {
	BBox  [4]float64 `json:"bbox"` // minx, miny, maxx, maxy
	Count int        `json:"count"`
	ID    string     `json:"id"`   // set when exactly one AOI is visible
	Name  string     `json:"name"` // ditto
}

// aoiHomeView unions the bboxes of the visible, non-archived AOIs.
// Returns nil when there are none, or when a row has no bbox yet.
func (s *Server) aoiHomeView(principalID int64) *AOIHomeView {
	if principalID == 0 {
		return nil
	}
	rows, err := s.DB.Query(`SELECT id, name, bbox_minx, bbox_miny, bbox_maxx, bbox_maxy
		FROM aois WHERE `+aoiVisibleSQL+aoiActiveSQL+`
		  AND bbox_minx IS NOT NULL AND bbox_maxx IS NOT NULL`,
		principalID, principalID, principalID, principalID)
	if err != nil {
		slog.Warn("aoi home view", "error", err)
		return nil
	}
	defer rows.Close()
	var out AOIHomeView
	for rows.Next() {
		var id, name string
		var x0, y0, x1, y1 float64
		if err := rows.Scan(&id, &name, &x0, &y0, &x1, &y1); err != nil {
			return nil
		}
		if out.Count == 0 {
			out.BBox = [4]float64{x0, y0, x1, y1}
			out.ID, out.Name = id, name
		} else {
			out.BBox[0] = math.Min(out.BBox[0], x0)
			out.BBox[1] = math.Min(out.BBox[1], y0)
			out.BBox[2] = math.Max(out.BBox[2], x1)
			out.BBox[3] = math.Max(out.BBox[3], y1)
			out.ID, out.Name = "", ""
		}
		out.Count++
	}
	if out.Count == 0 || out.BBox[2] <= out.BBox[0] || out.BBox[3] <= out.BBox[1] {
		return nil
	}
	return &out
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

	// Versioning (migration 042). An edit forks rather than mutates, because
	// an AOI's derived rows were computed for one polygon over one window --
	// changing either in place would silently turn them into answers to a
	// question nobody asked. Old versions are archived, not deleted.
	LineageID    string `json:"lineage_id,omitempty"`
	Version      int    `json:"version"`
	SupersededBy string `json:"superseded_by,omitempty"`
	ArchivedAt   string `json:"archived_at,omitempty"`
}

const aoiCols = `id, name, geometry,
	COALESCE(bbox_minx,0), COALESCE(bbox_miny,0), COALESCE(bbox_maxx,0), COALESCE(bbox_maxy,0),
	COALESCE(area_km2,0), COALESCE(from_date,''), COALESCE(to_date,''),
	COALESCE(owner_principal_id,0), visibility, state,
	COALESCE(created_at,''), COALESCE(notes,''),
	COALESCE(lineage_id, id), COALESCE(version,1),
	COALESCE(superseded_by,''), COALESCE(archived_at,'')`

func scanAOI(sc interface{ Scan(...any) error }, withGeometry bool) (*AOI, error) {
	var a AOI
	var geo string
	if err := sc.Scan(&a.ID, &a.Name, &geo, &a.BBox[0], &a.BBox[1], &a.BBox[2],
		&a.BBox[3], &a.AreaKm2, &a.FromDate, &a.ToDate, &a.OwnerID,
		&a.Visibility, &a.State, &a.CreatedAt, &a.Notes,
		&a.LineageID, &a.Version, &a.SupersededBy, &a.ArchivedAt); err != nil {
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

// aoiActiveSQL hides archived versions (leading AND).
//
// Archived AOIs are still fully readable by id -- a share link to an old
// version must keep resolving to the data it described -- but they must not
// render on the map or clutter the list, or every edit would leave a duplicate
// polygon behind. They come back through search.
const aoiActiveSQL = ` AND state != 'archived'`

// ListAOIs returns the AOIs a principal may see. Archived versions are
// excluded; use SearchAOIs to find those.
func (s *Server) ListAOIs(principalID int64, withGeometry bool) ([]*AOI, error) {
	rows, err := s.DB.Query(`SELECT `+aoiCols+` FROM aois WHERE `+aoiVisibleSQL+
		aoiActiveSQL+` ORDER BY name`, principalID, principalID, principalID, principalID)
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

// reownSystemAOIProgress re-keys stale 'SYSTEM'-owned aoi_progress rows.
//
// scripts/aoi_runner.py originally wrote its per-run progress notification with
// notify_status()'s default park_id='SYSTEM'. That is a privacy hole, not an
// untidiness: aoiNotifSQLFilter() decides an AOI notification's visibility from
// park_id, so 'SYSTEM' was treated as an ordinary park notification and served
// to every principal -- message included, and that message named the AOI and
// its ingest progress. The fact that someone is watching a piece of ground is
// as much the secret as the polygon (AGENTS.md "Areas of interest").
//
// The writer was fixed to pass park_id=<aoi id>; rows written before that are
// still there. This runs at startup rather than as a hand-run statement because
// "run this UPDATE" was carried through two handovers without ever executing --
// blocked each time by SQLite's single writer, which the AOI Hansen unit holds
// for 35+ minutes.
//
// It is deliberately NOT a migration. A migration that cannot get a write slot
// fails NewServer, and systemd then restart-loops the whole app: a privacy
// tidy-up must never be able to take the site down. Best effort, retried every
// boot, converging the first time the lock is free.
//
// The id is recovered from the message, which the runner prefixed with
// '<aoi_id>/<dataset>: ...'. Rows whose prefix matches no known AOI are deleted:
// an aoi_progress row we cannot attribute cannot be shown safely, and it is a
// note about work that finished long ago.
func (s *Server) reownSystemAOIProgress() error {
	var n int
	if err := s.DB.QueryRow(`SELECT COUNT(*) FROM notifications
		WHERE notification_type='aoi_progress' AND park_id='SYSTEM'`).Scan(&n); err != nil {
		return err
	}
	if n == 0 {
		return nil
	}
	if _, err := s.DB.Exec(`UPDATE notifications
		   SET park_id = (SELECT a.id FROM aois a
		                   WHERE notifications.message LIKE a.id || '/%'),
		       message = replace(message,
		                   (SELECT a.id || '/' FROM aois a
		                     WHERE notifications.message LIKE a.id || '/%'), '')
		 WHERE notification_type='aoi_progress' AND park_id='SYSTEM'
		   AND EXISTS (SELECT 1 FROM aois a
		                WHERE notifications.message LIKE a.id || '/%')`); err != nil {
		return err
	}
	res, err := s.DB.Exec(`DELETE FROM notifications
		WHERE notification_type='aoi_progress' AND park_id='SYSTEM'`)
	if err != nil {
		return err
	}
	dropped, _ := res.RowsAffected()
	slog.Info("re-keyed stale aoi_progress notifications",
		"found", n, "unattributable_dropped", dropped)
	return nil
}

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

// aoiPark is one protected area the AOI overlaps, precomputed by
// scripts/aoi_clip.py into aoi_parks (migration 041). The popup links to
// these and the report folds them in; both fractions are served because
// "how much of the AOI" and "how much of the park" are different questions.
type aoiPark struct {
	ParkID     string  `json:"park_id"`
	Name       string  `json:"name,omitempty"`
	FracOfAOI  float64 `json:"frac_of_aoi"`
	FracOfPark float64 `json:"frac_of_park"`
}

func (s *Server) loadAOIParks(aoiID string) ([]aoiPark, error) {
	rows, err := s.DB.Query(`SELECT park_id, frac_of_aoi, frac_of_park
		FROM aoi_parks WHERE aoi_id = ? ORDER BY frac_of_aoi DESC`, aoiID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []aoiPark{}
	for rows.Next() {
		var p aoiPark
		if err := rows.Scan(&p.ParkID, &p.FracOfAOI, &p.FracOfPark); err != nil {
			return nil, err
		}
		if s.AreaStore != nil {
			for _, a := range s.AreaStore.Areas {
				if a.ID == p.ParkID {
					p.Name = a.Name
					break
				}
			}
		}
		out = append(out, p)
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
	// can_create tells the UI whether to offer the draw button. The frontend
	// cannot work this out for itself: a password may arrive as a cookie
	// rather than ?pwd=, so getPwd() is empty for a perfectly valid principal.
	// Only the server knows whether POST /api/aois would 403.
	writeJSON(w, http.StatusOK, map[string]any{
		"aois": list, "count": len(list),
		"can_create": s.RequestPrincipalID(r) != 0,
	})
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
	parks, err := s.loadAOIParks(a.ID)
	if err != nil {
		slog.Warn("aoi parks", "id", a.ID, "error", err)
		parks = nil
	}
	writeJSON(w, http.StatusOK, map[string]any{"aoi": a, "datasets": ds,
		"parks": parks})
}

// aoiGate wraps a park handler so it can serve an AOI id.
//
// The park handlers are not park-specific in any deep way: they resolve
// r.PathValue("id"), fail to match it in the AreaStore, and then read
// feature_geometries / fire_narrative_cache keyed by that id — which is
// exactly how AOI rows are written by the --aoi v5 chain. So the whole AOI
// read surface is the park handlers plus one visibility check, and there is no
// second copy of the narrative logic to drift.
//
// The gate is mandatory: without it these ids would be readable through
// /api/parks/* (which is why ParkIDMiddleware 404s on AOI ids).
func (s *Server) aoiGate(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if a := s.requireAOI(w, r, false); a != nil {
			next(w, r)
		}
	}
}

// resolveAreaGeom returns the display name and boundary GeoJSON for either a
// park or an AOI.
//
// Handlers that need geography (KML, Locus, anything that buffers a bbox out
// of the boundary) all wrote the same `for _, pa := range s.AreaStore.Areas`
// loop. An AOI is deliberately NOT in AreaStore -- putting it there would let
// park_assigner reassign detections away from the parks it overlaps -- so that
// loop silently yields an empty boundary and the handler emits a file with no
// geometry and no patrol effort (the effort bbox is derived from the
// boundary). Rather than special-case each one, they resolve through here.
//
// Visibility is NOT checked: callers reach this only after aoiGate.
func (s *Server) resolveAreaGeom(id string) (name, boundary string) {
	name = id
	if s.AreaStore != nil {
		for _, pa := range s.AreaStore.Areas {
			if pa.ID == id {
				if pa.Geometry.Type != "" {
					if b, err := json.Marshal(pa.Geometry); err == nil {
						boundary = string(b)
					}
				}
				return pa.Name, boundary
			}
		}
	}
	if !IsAOIID(id) {
		return name, ""
	}
	var n, geo string
	if err := s.DB.QueryRow(`SELECT name, geometry FROM aois WHERE id = ?`, id).
		Scan(&n, &geo); err != nil {
		return name, ""
	}
	return n, geo
}

// resolveAreaBBox returns the display name and bounding box for a park or an
// AOI, plus whether the id resolved at all.
//
// Same reason as resolveAreaGeom: the MBTiles handlers used
// s.AreaStore.GetByID(id) and 404'd on every AOI, because an AOI is
// deliberately not in AreaStore. Offline satellite tiles are the one export
// whose usefulness is *purely* geographic — there is nothing park-specific in
// a tile pyramid — so refusing them for an AOI was an accident of plumbing,
// not a decision.
//
// Visibility is NOT checked: callers reach this only after aoiGate.
func (s *Server) resolveAreaBBox(id string) (name string, bbox [4]float64, ok bool) {
	if s.AreaStore != nil {
		if a := s.AreaStore.GetByID(id); a != nil {
			latMin, latMax, lonMin, lonMax := a.GetBoundingBox()
			return a.Name, [4]float64{lonMin, latMin, lonMax, latMax}, true
		}
	}
	if !IsAOIID(id) {
		return id, bbox, false
	}
	var n string
	var x0, y0, x1, y1 sql.NullFloat64
	if err := s.DB.QueryRow(
		`SELECT name, bbox_minx, bbox_miny, bbox_maxx, bbox_maxy FROM aois WHERE id = ?`,
		id).Scan(&n, &x0, &y0, &x1, &y1); err != nil {
		return id, bbox, false
	}
	if !x0.Valid || !y0.Valid || !x1.Valid || !y1.Valid {
		return n, bbox, false
	}
	return n, [4]float64{x0.Float64, y0.Float64, x1.Float64, y1.Float64}, true
}

// HandleAPIAOIExportGeoJSON — GET /api/aois/{id}/export.geojson: the polygon
// itself, so it can be loaded into QGIS/Locus like a park boundary.
func (s *Server) HandleAPIAOIExportGeoJSON(w http.ResponseWriter, r *http.Request) {
	a := s.requireAOI(w, r, true)
	if a == nil {
		return
	}
	w.Header().Set("Content-Disposition", `attachment; filename="`+a.ID+`.geojson"`)
	writeJSON(w, http.StatusOK, map[string]any{
		"type": "FeatureCollection",
		"features": []any{map[string]any{
			"type":     "Feature",
			"geometry": a.Geometry,
			"properties": map[string]any{
				"id": a.ID, "name": a.Name, "area_km2": a.AreaKm2,
				"from_date": a.FromDate, "to_date": a.ToDate,
			},
		}},
	})
}
