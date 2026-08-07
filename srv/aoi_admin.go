package srv

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

// The admin panel's "Access" tab: who owns which AOI, what each of its
// datasets is doing, and the two operator verbs that are not part of the
// owner-facing UI (enable/disable one dataset, kick the runner now).
//
// Three decisions worth keeping:
//
//  1. **It is scoped, not global.** Everything here goes through ListAOIs with
//     the caller's principal, so the tab shows the AOIs this principal may
//     already see. "Admin" in this app is any valid password (RequireAdmin
//     accepts the access cookie), so a global view would hand every alpha
//     tenant every other tenant's polygons — the one thing §2 exists to
//     prevent. An id must not be an oracle, and neither must a tab.
//
//  2. **`principals.label` is never served.** It is pwd[:3]+"…", written by
//     SeedPrincipals to be recognisable in a *local* sqlite session. Serving
//     it would put three characters of every other tenant's password on the
//     wire. The handle is `ref[:8]` — a sha256 prefix, non-secret by
//     construction — plus `is_you` for the row the caller is.
//
//  3. **It reports the queue; it does not reimplement it.** Lease state is
//     read straight from aoi_datasets (the same columns `--status` prints) and
//     "run now" is the existing /kick, which shells out to
//     scripts/aoi_runner.py. There is exactly one implementation of "work a
//     unit".

// aoiDatasetName bounds the dataset name before it reaches SQL or a log line;
// the set is fixed by DEFAULT_DATASETS in scripts/aoi_lib.py.
var aoiDatasetName = regexp.MustCompile(`^[a-z][a-z0-9_]{0,30}$`)

// aoiAdminPrincipal is one tenant, described without leaking a credential.
type aoiAdminPrincipal struct {
	ID      int64  `json:"id"`
	Handle  string `json:"handle"` // sha256(pwd)[:8] — non-secret by construction
	IsYou   bool   `json:"is_you"`
	Owns    int    `json:"owns"`    // AOIs owned
	Granted int    `json:"granted"` // AOIs shared with them
}

// aoiAdminDataset adds the operator-only lease columns to aoiDataset.
type aoiAdminDataset struct {
	aoiDataset
	LeaseOwner string `json:"lease_owner,omitempty"`
	LeaseUntil string `json:"lease_until,omitempty"`
	NextRunAt  string `json:"next_run_at,omitempty"`
	HasCursor  bool   `json:"has_cursor"`
}

type aoiAdminEntry struct {
	AOI      *AOI              `json:"aoi"`
	Datasets []aoiAdminDataset `json:"datasets"`
	OwnerRef string            `json:"owner_ref,omitempty"`
}

// HandleAPIAdminAccess — GET /api/admin/access
func (s *Server) HandleAPIAdminAccess(w http.ResponseWriter, r *http.Request) {
	me := s.RequestPrincipalID(r)
	aois, err := s.ListAOIs(me, false)
	if err != nil {
		internalError(w, "could not list aois", err)
		return
	}
	// Archived versions are excluded from ListAOIs on purpose (they would
	// double every edited polygon); the operator can still reach one by id.
	entries := make([]aoiAdminEntry, 0, len(aois))
	for _, a := range aois {
		ds, err := s.loadAOIAdminDatasets(a.ID)
		if err != nil {
			internalError(w, "could not read datasets", err)
			return
		}
		e := aoiAdminEntry{AOI: a, Datasets: ds}
		if a.OwnerID != 0 {
			var ref string
			if err := s.DB.QueryRow(`SELECT ref FROM principals WHERE id=?`,
				a.OwnerID).Scan(&ref); err == nil && len(ref) >= 8 {
				e.OwnerRef = ref[:8]
			}
		}
		entries = append(entries, e)
	}

	ps := []aoiAdminPrincipal{}
	rows, err := s.DB.Query(`SELECT p.id, p.ref,
		  (SELECT COUNT(*) FROM aois a WHERE a.owner_principal_id = p.id),
		  (SELECT COUNT(*) FROM aoi_grants g WHERE g.principal_id = p.id)
		FROM principals p WHERE p.kind='password' ORDER BY p.id`)
	if err != nil {
		internalError(w, "could not list principals", err)
		return
	}
	defer rows.Close()
	for rows.Next() {
		var p aoiAdminPrincipal
		var ref string
		if err := rows.Scan(&p.ID, &ref, &p.Owns, &p.Granted); err != nil {
			internalError(w, "could not scan principal", err)
			return
		}
		if len(ref) >= 8 {
			p.Handle = ref[:8]
		}
		p.IsYou = p.ID == me
		ps = append(ps, p)
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"aois":       entries,
		"principals": ps,
		"runner":     s.aoiRunnerHeartbeat(),
		"you":        me,
	})
}

func (s *Server) loadAOIAdminDatasets(aoiID string) ([]aoiAdminDataset, error) {
	rows, err := s.DB.Query(`
		SELECT dataset, enabled, state, priority, COALESCE(depends_on,''),
		       COALESCE(units_total,0), COALESCE(units_done,0),
		       COALESCE(coverage,0), COALESCE(last_run_at,''), COALESCE(detail,''),
		       COALESCE(lease_owner,''), COALESCE(lease_until,''),
		       COALESCE(next_run_at,''), (cursor IS NOT NULL)
		FROM aoi_datasets WHERE aoi_id = ? ORDER BY priority, dataset`, aoiID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []aoiAdminDataset{}
	for rows.Next() {
		var d aoiAdminDataset
		if err := rows.Scan(&d.Dataset, &d.Enabled, &d.State, &d.Priority,
			&d.DependsOn, &d.UnitsTotal, &d.UnitsDone, &d.Coverage,
			&d.LastRunAt, &d.Detail, &d.LeaseOwner, &d.LeaseUntil,
			&d.NextRunAt, &d.HasCursor); err != nil {
			return nil, err
		}
		if note := aoiBlockedDatasets[d.Dataset]; note != "" {
			d.State = "blocked"
			d.Detail = note
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

// aoiRunnerHeartbeat is data/aoi_status.json verbatim — the runner's own
// heartbeat, deliberately separate from the fire pipeline's so an AOI failure
// never makes the nightly fire cron look degraded.
func (s *Server) aoiRunnerHeartbeat() any {
	b, err := os.ReadFile(filepath.Join(".", "data", "aoi_status.json"))
	if err != nil {
		return nil
	}
	var v any
	if err := json.Unmarshal(b, &v); err != nil {
		return nil
	}
	return v
}

// HandleAPIAdminAOIDataset — POST /api/admin/aoi-dataset
//
// Body/query: aoi, dataset, enabled=0|1. Owner-only, like every other AOI
// write: RequireAdmin is satisfied by any valid password, so it cannot be the
// authorisation for touching someone else's queue.
//
// Disabling here is the per-dataset form of /cancel and keeps the **cursor**
// for the same reason: re-enabling must resume, not re-spend FIRMS quota.
func (s *Server) HandleAPIAdminAOIDataset(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "bad form", http.StatusBadRequest)
		return
	}
	id := strings.TrimSpace(r.FormValue("aoi"))
	dataset := strings.TrimSpace(r.FormValue("dataset"))
	if !ValidAOIID(id) || dataset == "" || !aoiDatasetName.MatchString(dataset) {
		http.Error(w, "invalid aoi or dataset", http.StatusBadRequest)
		return
	}
	a, err := s.GetAOI(id, s.RequestPrincipalID(r), false)
	if err == sql.ErrNoRows || (err == nil && !a.IsOwner) {
		http.NotFound(w, r) // never 403 — an id must not be an oracle
		return
	} else if err != nil {
		internalError(w, "aoi lookup", err)
		return
	}
	enabled, _ := strconv.Atoi(r.FormValue("enabled"))
	var res sql.Result
	if enabled == 1 {
		// Re-arm: clear any stale lease and let the next slice pick it up. The
		// cursor is left alone, which is what makes this a resume.
		res, err = execUserToggle(r.Context(), s.DB, `UPDATE aoi_datasets
			SET enabled=1, next_run_at=NULL, lease_owner=NULL, lease_until=NULL,
			    state=CASE WHEN state='done' THEN 'done' ELSE 'pending' END
			WHERE aoi_id=? AND dataset=?`, a.ID, dataset)
	} else {
		res, err = execUserToggle(r.Context(), s.DB, `UPDATE aoi_datasets
			SET enabled=0, lease_owner=NULL, lease_until=NULL,
			    state=CASE WHEN state='running' THEN 'pending' ELSE state END
			WHERE aoi_id=? AND dataset=?`, a.ID, dataset)
	}
	if err != nil {
		internalError(w, "could not update dataset", err)
		return
	}
	if n, _ := res.RowsAffected(); n == 0 {
		http.NotFound(w, r)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"aoi": a.ID, "dataset": dataset, "enabled": enabled == 1})
}
