package srv

// The GeoPackage export job: queue, cache, notification, download, expiry.
//
// Shape borrowed from the MBTiles queue and park onboarding, with two
// deliberate differences (see db/migrations/047-geopackage-jobs.sql):
//
//  1. The table is the state, not an in-memory map. A download link that dies
//     on the next deploy makes the notification a lie, and the whole point of
//     the notification is that the user can walk away and come back.
//  2. It is a cache keyed by (area, window, effort, env). Asking twice for the
//     same file gets the same file — which is also what makes a shared link
//     meaningful rather than a one-shot spool slot.

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	gpkgOutputDir = "data/gpkg_output"
	gpkgTTL       = 21 * 24 * time.Hour
)

// One build at a time, process-wide. These are IO- and CPU-heavy over the same
// SQLite file every other request uses; two in parallel would make the site
// slow in a way that looks like a bug rather than like a download.
var gpkgBuildSem = make(chan struct{}, 1)

// errGPKGCancelled is how a build reports that it stopped because the user
// asked it to — a different thing from failing, and never surfaced as one.
var errGPKGCancelled = errors.New("export cancelled")

// gpkgCancels maps a live job id to the channel that aborts it. In-memory on
// purpose: a cancel can only reach a goroutine in THIS process, and a job
// orphaned by a restart is already reconciled by the startup sweeper. Closed
// exactly once, by whoever LoadAndDelete-s it.
var gpkgCancels sync.Map

type GeoPackageJob struct {
	ID        string          `json:"id"`
	AreaID    string          `json:"area_id"`
	AreaName  string          `json:"area_name"`
	IsAOI     bool            `json:"is_aoi"`
	FromDate  string          `json:"from_date,omitempty"`
	ToDate    string          `json:"to_date,omitempty"`
	Effort    bool            `json:"effort"`
	RawFire   bool            `json:"raw_fire"`
	State     string          `json:"state"`
	Progress  float64         `json:"progress"`
	Step      string          `json:"step,omitempty"`
	SizeBytes int64           `json:"size_bytes"`
	Layers    []gpkgLayerStat `json:"layers,omitempty"`
	Error     string          `json:"error,omitempty"`
	CreatedAt string          `json:"created_at"`
	Finished  string          `json:"finished_at,omitempty"`
	ExpiresAt string          `json:"expires_at,omitempty"`
	Downloads int             `json:"downloads"`
	URL       string          `json:"download_url,omitempty"`
	Cached    bool            `json:"cached,omitempty"`
	// View is present only for a viewport export. It is what makes the card
	// self-describing after a reload ("1 Mar 2025 · fire paths, settlements"
	// rather than "Map view"), and it is what Try-again re-sends.
	View *gpkgViewOpts `json:"view,omitempty"`
}

// The cache key IS the question. Anything that changes the bytes of the file
// must appear here or a second, different request is served the first one's
// answer — which is the worst failure this cache can have, because the file
// looks perfectly valid.
//
// A view export's question includes its viewport, its instant and its layer
// set, so those go in too (gpkgViewKey) — two paused frames of the same
// animation are different files and must not share a cache slot.
func gpkgCacheKey(areaID, from, to string, effort, rawFire bool, env string) string {
	// gpkgFormatVersion bumps when the file's SCHEMA or styling changes
	// (new column, new layer, renderer rewrite): the question is the same but
	// the right answer isn't, and a 21-day-old cached file must not be served
	// as if it had the new columns. v2: needs_review on deforestation,
	// protected_areas layer on AOI exports (with keystone attributes),
	// trajectory start_park/end_park/parks_touched, data-matched renderers.
	const gpkgFormatVersion = "v2"
	return strings.Join([]string{gpkgFormatVersion, areaID, from, to, fmt.Sprint(effort), fmt.Sprint(rawFire), env}, "|")
}

func gpkgKeyFor(o gpkgExportOpts) string {
	k := gpkgCacheKey(o.AreaID, o.FromDate, o.ToDate, o.Effort, o.RawFire, o.Env)
	// A scope-restricted export is a DIFFERENT file, not the same file for a
	// lesser reader: without this it would be served the account's own cached
	// copy, patrol layers included.
	if pt := o.patrolTenant(); pt != strOr(o.Env, clientTenant) {
		k += "|np"
	}
	if o.View != nil {
		k += "|" + gpkgViewKey(o.View)
	}
	return k
}

func gpkgViewKey(v *gpkgViewOpts) string {
	// Coordinates are rounded to ~10 m: a pixel of pan is the same view, and
	// without rounding every mousemove would mint a new 200 MB file.
	ls := append([]string{}, v.Layers...)
	sort.Strings(ls)
	return fmt.Sprintf("view:%.4f,%.4f,%.4f,%.4f@%s/%s/%s",
		v.BBox[0], v.BBox[1], v.BBox[2], v.BBox[3], v.At, strings.Join(ls, "."), v.AOIID)
}

func gpkgToken() string {
	b := make([]byte, 8)
	rand.Read(b)
	return hex.EncodeToString(b)
}

const gpkgCols = `id, area_id, area_name, is_aoi, COALESCE(from_date,''), COALESCE(to_date,''),
	effort, raw_fire, state, progress, COALESCE(step,''), COALESCE(size_bytes,0), COALESCE(layers_json,'[]'),
	COALESCE(error,''), created_at, COALESCE(finished_at,''), COALESCE(expires_at,''),
	COALESCE(downloads,0), COALESCE(file_path,''), COALESCE(view_json,'')`

type gpkgRowScanner interface{ Scan(...interface{}) error }

func scanGeoPackageJob(row gpkgRowScanner) (*GeoPackageJob, string, error) {
	var j GeoPackageJob
	var isAOI, effort, rawFire int
	var layers, path, viewJSON string
	if err := row.Scan(&j.ID, &j.AreaID, &j.AreaName, &isAOI, &j.FromDate, &j.ToDate,
		&effort, &rawFire, &j.State, &j.Progress, &j.Step, &j.SizeBytes, &layers, &j.Error,
		&j.CreatedAt, &j.Finished, &j.ExpiresAt, &j.Downloads, &path, &viewJSON); err != nil {
		return nil, "", err
	}
	if viewJSON != "" {
		var v gpkgViewOpts
		if json.Unmarshal([]byte(viewJSON), &v) == nil {
			j.View = &v
		}
	}
	j.IsAOI, j.Effort, j.RawFire = isAOI == 1, effort == 1, rawFire == 1
	json.Unmarshal([]byte(layers), &j.Layers)
	if j.State == "ready" {
		j.URL = "/api/geopackage/" + j.ID + "/download"
	}
	return &j, path, nil
}

// findGeoPackageJob returns the newest usable job for a cache key: a ready
// (unexpired, file still present) one, else one already building.
func (s *Server) findGeoPackageJob(key string) *GeoPackageJob {
	rows, err := s.DB.Query(`SELECT `+gpkgCols+` FROM geopackage_jobs
		WHERE cache_key = ? AND state IN ('ready','pending','running')
		ORDER BY created_at DESC LIMIT 5`, key)
	if err != nil {
		return nil
	}
	defer rows.Close()
	for rows.Next() {
		j, path, err := scanGeoPackageJob(rows)
		if err != nil {
			continue
		}
		if j.State == "ready" {
			// A file removed by the sweeper (or by hand) must not be offered:
			// the link would 404 after the user waited for nothing.
			if st, err := os.Stat(path); err != nil || st.Size() == 0 {
				s.DB.Exec(`UPDATE geopackage_jobs SET state='expired' WHERE id=?`, j.ID)
				continue
			}
			if j.ExpiresAt != "" && j.ExpiresAt < time.Now().UTC().Format(time.RFC3339) {
				continue
			}
		}
		return j
	}
	return nil
}

// startGeoPackageJob creates (or reuses) a job and kicks off the build.
func (s *Server) startGeoPackageJob(o gpkgExportOpts, isAOI bool, principalID int64, refresh bool) (*GeoPackageJob, error) {
	key := gpkgKeyFor(o)
	if !refresh {
		if j := s.findGeoPackageJob(key); j != nil {
			j.Cached = j.State == "ready"
			return j, nil
		}
	}
	id := gpkgToken()
	now := time.Now().UTC().Format(time.RFC3339)
	// One directory per job so the file's basename can be the name the user
	// downloads. That is not cosmetic: the embedded QGIS project references its
	// own container as "./<basename>.gpkg", so if the served name and the
	// stored name diverge, opening the project finds no layers — and an empty
	// project reads as a broken export, not as a renamed file.
	path := filepath.Join(gpkgOutputDir, id, gpkgDownloadName(o.AreaID, o.FromDate, o.ToDate, o.RawFire))
	if o.View != nil {
		path = filepath.Join(gpkgOutputDir, id, gpkgViewDownloadName(o))
	}
	var pid interface{}
	if principalID > 0 {
		pid = principalID
	}
	// A view export's question does not fit the columns (bbox, instant, chips),
	// and cache_key is not something a client should have to parse. Stored so a
	// card reloaded tomorrow can still say what it is and retry the same thing.
	viewJSON := ""
	if o.View != nil {
		if b, err := json.Marshal(o.View); err == nil {
			viewJSON = string(b)
		}
	}
	if _, err := s.DB.Exec(`INSERT INTO geopackage_jobs
		(id, cache_key, area_id, area_name, is_aoi, principal_id, env, from_date, to_date,
		 effort, raw_fire, state, file_path, view_json, created_at)
		VALUES (?,?,?,?,?,?,?,?,?,?,?, 'pending', ?, ?, ?)`,
		id, key, o.AreaID, o.AreaName, boolInt(isAOI), pid, o.Env, o.FromDate, o.ToDate,
		boolInt(o.Effort), boolInt(o.RawFire), path, viewJSON, now); err != nil {
		return nil, err
	}
	// The card is written HERE, not when the build starts. Only one export
	// builds at a time, so a second request can sit in the semaphore for
	// minutes — and during those minutes the user had clicked Download and got
	// a toast, an empty bell, and no way to tell whether anything was happening.
	// A queued job is a real state and has to say so.
	s.upsertGeoPackageNotification(id, o, "geopackage_progress",
		"Preparing GIS export: "+o.AreaName+gpkgTitleSuffix(o),
		"Queued. You can close this — the download stays available for 21 days.")
	go s.runGeoPackageJob(id, path, o, isAOI)
	return &GeoPackageJob{ID: id, AreaID: o.AreaID, AreaName: o.AreaName, IsAOI: isAOI,
		FromDate: o.FromDate, ToDate: o.ToDate, Effort: o.Effort, RawFire: o.RawFire,
		State: "pending", CreatedAt: now, View: o.View}, nil
}

// waitForGeoPackageJob gives a small export the chance to be a DOWNLOAD rather
// than a notification.
//
// A viewport export of a paused animation is usually seconds — the same click
// on the same button over a continent is minutes. Making the user watch the
// bell for a 2 MB file that was ready before they looked away is ceremony;
// blocking the request until a 400 MB one finishes is a timeout. So the
// handler waits a *bounded* moment and then answers honestly with whichever
// state the job is in — the job itself is unaffected either way, and the card
// is written at queue time regardless, so a fast export is still deletable
// from the bell and still expires with everything else.
//
// Deliberately short (and hard-capped) relative to WriteTimeout: this must
// never be the reason a request dies.
func (s *Server) waitForGeoPackageJob(id string, d time.Duration) *GeoPackageJob {
	if d > 20*time.Second {
		d = 20 * time.Second
	}
	deadline := time.Now().Add(d)
	for {
		j := s.loadGeoPackageJobByID(id)
		if j == nil || j.State == "ready" || j.State == "failed" || j.State == "expired" {
			return j
		}
		if time.Now().After(deadline) {
			return j
		}
		time.Sleep(250 * time.Millisecond)
	}
}

func (s *Server) loadGeoPackageJobByID(id string) *GeoPackageJob {
	row := s.DB.QueryRow(`SELECT `+gpkgCols+` FROM geopackage_jobs WHERE id = ?`, id)
	j, _, err := scanGeoPackageJob(row)
	if err != nil {
		return nil
	}
	return j
}

// Two exports of the same area differ by a gigabyte, so their cards have to be
// tellable apart at a glance — otherwise the bell shows two identical rows and
// the only way to know which is which is the file that lands.
func gpkgTitleSuffix(o gpkgExportOpts) string {
	if o.View != nil {
		if o.View.At != "" {
			// Human, because two paused frames of the same animation are two
			// cards in one bell and the instant is the only thing that tells
			// them apart.
			return " (view at " + gpkgHumanDate(o.View.At) + ")"
		}
		return " (current view)"
	}
	if o.RawFire {
		return ""
	}
	return " (no raw fire points)"
}

func gpkgHumanDate(iso string) string {
	t, err := time.Parse("2006-01-02", iso)
	if err != nil {
		return iso
	}
	return t.Format("2 Jan 2006")
}

// A view export's filename has to say WHICH view: several of them land in the
// same Downloads folder during one session and they differ only by an instant
// and a rectangle.
func gpkgViewDownloadName(o gpkgExportOpts) string {
	fn := "5mp_view"
	if o.AreaID != "" {
		fn = sanitizeFileToken(o.AreaID) + "_view"
	}
	if o.View.At != "" {
		fn += "_" + sanitizeFileToken(o.View.At)
	} else if o.ToDate != "" {
		fn += "_" + sanitizeFileToken(o.ToDate)
	}
	return fn + ".gpkg"
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

// gpkgDownloadName is the user-facing filename AND the on-disk basename.
// The filename says which of the two exports this is, because both will end up
// in the same Downloads folder and they differ by a gigabyte, not by a detail.
func gpkgDownloadName(areaID, from, to string, rawFire bool) string {
	fn := sanitizeFileToken(areaID)
	if from != "" || to != "" {
		fn += "_" + sanitizeFileToken(strOr(from, "start")) + "_to_" + sanitizeFileToken(strOr(to, "now"))
	}
	if !rawFire {
		fn += "_no_raw_fire"
	}
	return fn + ".gpkg"
}

func sanitizeFileToken(s string) string {
	var b strings.Builder
	for _, r := range s {
		if r == '_' || r == '-' || (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func (s *Server) runGeoPackageJob(id, path string, o gpkgExportOpts, isAOI bool) {
	defer func() {
		if rec := recover(); rec != nil {
			slog.Error("geopackage build panicked", "id", id, "err", rec)
			s.failGeoPackageJob(id, o, fmt.Sprint(rec))
		}
	}()
	// The cancel channel lives exactly as long as the goroutine that can act
	// on it. DELETE on a live job closes it; the build notices between rows.
	cancel := make(chan struct{})
	gpkgCancels.Store(id, cancel)
	defer gpkgCancels.Delete(id)
	o.Cancel = cancel
	// Queued behind another build: only one runs at a time, and "waiting for
	// another export to finish" is a different thing from "0%" — a progress bar
	// that has not moved for four minutes reads as broken.
	if len(gpkgBuildSem) > 0 {
		s.DB.Exec(`UPDATE geopackage_jobs SET step='waiting for another export' WHERE id=?`, id)
		s.upsertGeoPackageNotification(id, o, "geopackage_progress",
			"Preparing GIS export: "+o.AreaName+gpkgTitleSuffix(o),
			"Waiting for another export to finish first.")
	}
	// Cancellable even while queued: a job waiting behind another export owns
	// nothing yet, so aborting it here is free.
	select {
	case gpkgBuildSem <- struct{}{}:
	case <-cancel:
		s.cleanupCancelledGeoPackageJob(id, path)
		return
	}
	defer func() { <-gpkgBuildSem }()

	os.MkdirAll(filepath.Dir(path), 0o755)
	s.DB.Exec(`UPDATE geopackage_jobs SET state='running', started_at=?, step='starting' WHERE id=?`,
		time.Now().UTC().Format(time.RFC3339), id)
	s.upsertGeoPackageNotification(id, o, "geopackage_progress",
		"Preparing GIS export: "+o.AreaName+gpkgTitleSuffix(o),
		"Collecting layers for QGIS. You can close this — the download stays available for 21 days.")

	var lastWrite time.Time
	o.Progress = func(frac float64, label string) {
		// Throttled: the notification card polls, and a write per layer is
		// enough to move a progress bar without contending for the one writer.
		if time.Since(lastWrite) < 900*time.Millisecond && frac < 1 {
			return
		}
		lastWrite = time.Now()
		s.DB.Exec(`UPDATE geopackage_jobs SET progress=?, step=? WHERE id=?`, frac, "writing "+label, id)
		s.upsertGeoPackageNotification(id, o, "geopackage_progress",
			"Preparing GIS export: "+o.AreaName+gpkgTitleSuffix(o),
			fmt.Sprintf("%d%% — writing %s", int(frac*100), label))
	}

	// A view export answers a different question with the same machinery; it is
	// not the area export plus a filter, so it is a different builder.
	build := s.buildAreaGeoPackage
	if o.View != nil {
		build = s.buildViewGeoPackage
	}
	stats, err := build(path, o)
	if err != nil {
		if errors.Is(err, errGPKGCancelled) {
			s.cleanupCancelledGeoPackageJob(id, path)
			return
		}
		os.Remove(path)
		s.failGeoPackageJob(id, o, err.Error())
		return
	}
	var size int64
	if st, e := os.Stat(path); e == nil {
		size = st.Size()
	}
	// The row is the job. If a DELETE raced the start of this goroutine (the
	// cancel channel not yet registered), the row is gone — publishing a card
	// for it would resurrect what the user just removed.
	var exists int
	s.DB.QueryRow(`SELECT COUNT(*) FROM geopackage_jobs WHERE id = ?`, id).Scan(&exists)
	if exists == 0 {
		s.cleanupCancelledGeoPackageJob(id, path)
		return
	}
	layersJSON, _ := json.Marshal(stats)
	nowT := time.Now().UTC()
	s.DB.Exec(`UPDATE geopackage_jobs SET state='ready', progress=1, step='', size_bytes=?,
		layers_json=?, finished_at=?, expires_at=? WHERE id=?`,
		size, string(layersJSON), nowT.Format(time.RFC3339), nowT.Add(gpkgTTL).Format(time.RFC3339), id)

	total := 0
	for _, l := range stats {
		total += l.Count
	}
	s.upsertGeoPackageNotification(id, o, "geopackage_ready",
		"GIS export ready: "+o.AreaName+gpkgTitleSuffix(o),
		fmt.Sprintf("%s · %d layers · %s features · styled for QGIS. Link valid until %s.",
			humanBytes(size), len(stats), formatThousands(total), nowT.Add(gpkgTTL).Format("2 Jan 2006")))
	slog.Info("geopackage ready", "id", id, "area", o.AreaID, "bytes", size, "layers", len(stats))
}

func (s *Server) failGeoPackageJob(id string, o gpkgExportOpts, msg string) {
	s.DB.Exec(`UPDATE geopackage_jobs SET state='failed', error=?, finished_at=? WHERE id=?`,
		msg, time.Now().UTC().Format(time.RFC3339), id)
	s.upsertGeoPackageNotification(id, o, "geopackage_failed",
		"GIS export failed: "+o.AreaName, msg)
}

// cleanupCancelledGeoPackageJob is the whole aftermath of a cancel: the
// half-written directory, the job row and the notification card all go
// together. Nothing is left behind on purpose — a cancelled export was the
// user saying "I don't want this", and a card saying "cancelled" would be one
// more thing to delete. Asking again simply rebuilds (the export is a pure
// function of its options).
func (s *Server) cleanupCancelledGeoPackageJob(id, path string) {
	if path != "" {
		os.RemoveAll(filepath.Dir(path))
	}
	s.DB.Exec(`DELETE FROM geopackage_jobs WHERE id = ?`, id)
	s.DB.Exec(`DELETE FROM notifications WHERE notification_type LIKE 'geopackage_%' AND reference_id = ?`, id)
	slog.Info("geopackage export cancelled", "id", id)
}

// One notification row per EXPORT QUESTION, rewritten in place — the card is
// the job's status, not a log of it (same reasoning as the AOI progress card:
// it is server state, so it survives a closed laptop).
//
// Keyed by cache_key, not by job id: a `refresh=1` mints a new job for the same
// area and window, and four identical "GIS export ready: Chinko" cards is how a
// notification panel stops being read. The superseded job's FILE is kept and
// its link keeps working for the full 21 days — a link someone was given must
// not break because the sender later rebuilt it — but its card gives way.
func (s *Server) upsertGeoPackageNotification(id string, o gpkgExportOpts, typ, title, msg string) {
	url := "?gpkg=" + id
	key := gpkgKeyFor(o)
	res, err := s.DB.Exec(`UPDATE notifications
		SET notification_type=?, title=?, message=?, reference_id=?, reference_url=?,
		    is_read=0, created_at=datetime('now')
		WHERE notification_type LIKE 'geopackage\_%' ESCAPE '\'
		  AND reference_id IN (SELECT id FROM geopackage_jobs WHERE cache_key = ?)`,
		typ, title, msg, id, url, key)
	if err == nil {
		if n, _ := res.RowsAffected(); n > 0 {
			return
		}
	}
	s.DB.Exec(`INSERT INTO notifications
		(park_id, notification_type, title, message, reference_id, reference_url, env, created_at)
		VALUES (?,?,?,?,?,?,?, datetime('now'))`,
		o.AreaID, typ, title, msg, id, url, strOr(o.Env, clientTenant))
}

func humanBytes(n int64) string {
	switch {
	case n >= 1<<30:
		return fmt.Sprintf("%.1f GB", float64(n)/float64(1<<30))
	case n >= 1<<20:
		return fmt.Sprintf("%.0f MB", float64(n)/float64(1<<20))
	case n >= 1<<10:
		return fmt.Sprintf("%.0f KB", float64(n)/float64(1<<10))
	}
	return fmt.Sprintf("%d B", n)
}

func formatThousands(n int) string {
	s := fmt.Sprint(n)
	var out []byte
	for i, c := range []byte(s) {
		if i > 0 && (len(s)-i)%3 == 0 {
			out = append(out, ',')
		}
		out = append(out, c)
	}
	return string(out)
}

// ---- expiry sweeper ------------------------------------------------------

var gpkgSweepOnce sync.Once

// StartGeoPackageSweeper deletes expired files and their rows hourly, and
// reconciles jobs left 'running' by a restart — a job whose goroutine died is
// not coming back, and leaving it at 40% forever is the "no-op that reads as an
// answer" failure this codebase keeps re-learning.
func (s *Server) StartGeoPackageSweeper() {
	gpkgSweepOnce.Do(func() {
		go func() {
			s.sweepGeoPackages(true)
			for range time.Tick(time.Hour) {
				s.sweepGeoPackages(false)
			}
		}()
	})
}

func (s *Server) sweepGeoPackages(startup bool) {
	now := time.Now().UTC().Format(time.RFC3339)
	rows, err := s.DB.Query(`SELECT id, COALESCE(file_path,'') FROM geopackage_jobs
		WHERE (expires_at IS NOT NULL AND expires_at <> '' AND expires_at < ?)
		   OR state = 'expired'`, now)
	if err == nil {
		var ids []string
		for rows.Next() {
			var id, path string
			if rows.Scan(&id, &path) == nil {
				if path != "" {
					os.RemoveAll(filepath.Dir(path))
				}
				ids = append(ids, id)
			}
		}
		rows.Close()
		for _, id := range ids {
			s.DB.Exec(`DELETE FROM geopackage_jobs WHERE id = ?`, id)
			s.DB.Exec(`DELETE FROM notifications WHERE notification_type LIKE 'geopackage_%' AND reference_id = ?`, id)
		}
		if len(ids) > 0 {
			slog.Info("geopackage sweeper removed expired exports", "count", len(ids))
		}
	}
	if startup {
		s.DB.Exec(`UPDATE geopackage_jobs SET state='failed',
			error='interrupted by a server restart — start the export again'
			WHERE state IN ('pending','running')`)
	}
	// Orphan notifications: a card whose job row is gone can never resolve —
	// the client polls, gets 404, and shows "checking…" forever. A notification
	// is the job's status, so no job means no card.
	s.DB.Exec(`DELETE FROM notifications
		WHERE notification_type LIKE 'geopackage\_%' ESCAPE '\'
		  AND (reference_id IS NULL
		       OR reference_id NOT IN (SELECT id FROM geopackage_jobs))`)
	// Orphan directories: a row deleted by hand leaves bytes behind. Each job
	// owns one directory named by its id, so the check is "is this id still a
	// job" rather than a filename match.
	entries, _ := os.ReadDir(gpkgOutputDir)
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		dir := filepath.Join(gpkgOutputDir, e.Name())
		var n int
		s.DB.QueryRow(`SELECT COUNT(*) FROM geopackage_jobs WHERE id = ?`, e.Name()).Scan(&n)
		if n == 0 {
			if st, err := os.Stat(dir); err == nil && time.Since(st.ModTime()) > time.Hour {
				os.RemoveAll(dir)
			}
		}
	}
}

// ---- handlers ------------------------------------------------------------

// HandleAPIAreaGeoPackage — POST/GET /api/{parks|aois}/{id}/export.gpkg.
// Returns the job, creating it if needed. Never blocks on the build: the
// browser gets a card to watch, not a five-minute spinner.
//
// `?peek=1` is the same question WITHOUT the side effect: "is this exact export
// already built?". It exists because a share link that opens the download menu
// has to know whether the entry it points at is a file or a five-minute build,
// and asking through the normal path would start the build merely by looking.
// Answers 404 when nothing matches — a peek is a lookup, not an error.
func (s *Server) HandleAPIAreaGeoPackage(w http.ResponseWriter, r *http.Request) {
	areaID := r.PathValue("id")
	if areaID == "" {
		http.Error(w, "area id required", http.StatusBadRequest)
		return
	}
	name, _ := s.resolveAreaGeom(areaID)
	q := r.URL.Query()
	o := gpkgExportOpts{
		AreaID:    areaID,
		AreaName:  name,
		FromDate:  q.Get("from"),
		ToDate:    q.Get("to"),
		Env:       RequestEnv(r),
		PatrolEnv: PatrolEnv(r),
		Effort:    q.Get("effort") != "0",
		// Default on: "GeoPackage" means everything unless asked otherwise.
		RawFire: q.Get("raw") != "0",
	}
	if q.Get("peek") == "1" {
		j := s.findGeoPackageJob(gpkgKeyFor(o))
		if j == nil {
			w.Header().Set("Cache-Control", "no-store")
			http.Error(w, "no export for this area and window", http.StatusNotFound)
			return
		}
		j.Cached = j.State == "ready"
		w.Header().Set("Cache-Control", "no-store")
		writeJSON(w, http.StatusOK, j)
		return
	}
	job, err := s.startGeoPackageJob(o, IsAOIID(areaID), s.RequestPrincipalID(r), q.Get("refresh") == "1")
	if err != nil {
		slog.Warn("geopackage job", "area", areaID, "err", err)
		http.Error(w, "could not start export", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, job)
}

// HandleAPIGeoPackageStatus — GET /api/geopackage/{id}. No-store: it is live.
func (s *Server) HandleAPIGeoPackageStatus(w http.ResponseWriter, r *http.Request) {
	j, _, ok := s.loadGeoPackageJob(w, r)
	if !ok {
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusOK, j)
}

// HandleAPIGeoPackageList — GET /api/geopackage: this principal's exports, so
// the UI can offer a ready file instead of rebuilding one.
func (s *Server) HandleAPIGeoPackageList(w http.ResponseWriter, r *http.Request) {
	pid := s.RequestPrincipalID(r)
	args := []interface{}{RequestEnv(r), pid}
	q := `SELECT ` + gpkgCols + ` FROM geopackage_jobs
		WHERE env = ? AND (principal_id IS NULL OR principal_id = ?)`
	if area := r.URL.Query().Get("area"); area != "" {
		q += " AND area_id = ?"
		args = append(args, area)
	}
	q += " ORDER BY created_at DESC LIMIT 50"
	rows, err := s.DB.Query(q, args...)
	if err != nil {
		http.Error(w, "database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	out := []*GeoPackageJob{}
	for rows.Next() {
		if j, _, err := scanGeoPackageJob(rows); err == nil {
			out = append(out, j)
		}
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"exports": out, "count": len(out)})
}

// loadGeoPackageJob resolves a job and enforces visibility. An AOI export is
// only visible to a principal who can see the AOI — 404, not 403, so an id is
// not an oracle (srv/aoi.go).
func (s *Server) loadGeoPackageJob(w http.ResponseWriter, r *http.Request) (*GeoPackageJob, string, bool) {
	id := r.PathValue("id")
	row := s.DB.QueryRow(`SELECT `+gpkgCols+` FROM geopackage_jobs WHERE id = ?`, id)
	j, path, err := scanGeoPackageJob(row)
	if err != nil {
		http.NotFound(w, r)
		return nil, "", false
	}
	if j.IsAOI {
		if _, err := s.GetAOI(j.AreaID, s.RequestPrincipalID(r), false); err != nil {
			http.NotFound(w, r)
			return nil, "", false
		}
	}
	return j, path, true
}

// HandleAPIGeoPackageDownload — GET /api/geopackage/{id}/download.
//
// (Delete is below.)
func (s *Server) HandleAPIGeoPackageDownload(w http.ResponseWriter, r *http.Request) {
	j, path, ok := s.loadGeoPackageJob(w, r)
	if !ok {
		return
	}
	if j.State != "ready" {
		http.Error(w, "export is "+j.State, http.StatusConflict)
		return
	}
	f, err := os.Open(path)
	if err != nil {
		s.DB.Exec(`UPDATE geopackage_jobs SET state='expired' WHERE id=?`, j.ID)
		http.Error(w, "export file is no longer available — start it again", http.StatusGone)
		return
	}
	defer f.Close()
	st, _ := f.Stat()
	s.DB.Exec(`UPDATE geopackage_jobs SET downloads = downloads + 1, last_download_at = ? WHERE id = ?`,
		time.Now().UTC().Format(time.RFC3339), j.ID)

	fn := filepath.Base(path)
	w.Header().Set("Content-Type", "application/geopackage+sqlite3")
	w.Header().Set("Content-Disposition", `attachment; filename="`+fn+`"`)
	if st != nil {
		w.Header().Set("Content-Length", fmt.Sprint(st.Size()))
	}
	http.ServeContent(w, r, fn, time.Now(), f)
}

// HandleAPIGeoPackageDelete — DELETE /api/geopackage/{id}.
//
// The 21-day TTL is a promise that a shared link keeps working, not a rule that
// a gigabyte has to sit on disk for three weeks after someone has downloaded it
// and moved on. Without this the only ways to reclaim the space were to wait or
// to touch the server, and the card offered "you can close this" — which hides
// the row and keeps the file, i.e. exactly the kind of control that looks like
// it did something and did not.
//
// It deletes the bytes, the row and the card together. Deliberately NOT a soft
// delete: a row left behind is a cache entry, so the next identical request
// would be answered with a file that is no longer there — the export would
// report ready and then 410 on download.
//
// Visibility is the same check as status and download (404, not 403, for an
// AOI the caller cannot see), so an id is not an oracle here either.
func (s *Server) HandleAPIGeoPackageDelete(w http.ResponseWriter, r *http.Request) {
	j, path, ok := s.loadGeoPackageJob(w, r)
	if !ok {
		return
	}
	// A build in flight owns the directory it is writing into, so it cannot be
	// deleted out from under the writer. Instead the DELETE becomes a CANCEL:
	// close the job's cancel channel and let the build goroutine — the one
	// owner of the directory — stop at the next row and clean up everything
	// itself (file, row, card). 202, not 200: the cleanup is the goroutine's,
	// and it happens a moment after this response.
	if j.State == "pending" || j.State == "running" {
		if c, ok := gpkgCancels.LoadAndDelete(j.ID); ok {
			close(c.(chan struct{}))
			writeJSON(w, http.StatusAccepted, map[string]interface{}{"cancelled": true, "id": j.ID})
			return
		}
		// No live goroutine in this process (restart raced the sweeper, or the
		// row is stale). Nothing is writing, so the direct cleanup is safe.
		s.cleanupCancelledGeoPackageJob(j.ID, path)
		writeJSON(w, http.StatusOK, map[string]interface{}{"deleted": true, "id": j.ID})
		return
	}
	if path != "" {
		// One directory per job (the basename must match the download name),
		// so the directory is the file.
		if err := os.RemoveAll(filepath.Dir(path)); err != nil {
			slog.Warn("geopackage delete", "id", j.ID, "err", err)
		}
	}
	s.DB.Exec(`DELETE FROM geopackage_jobs WHERE id = ?`, j.ID)
	s.DB.Exec(`DELETE FROM notifications WHERE notification_type LIKE 'geopackage_%' AND reference_id = ?`, j.ID)
	writeJSON(w, http.StatusOK, map[string]interface{}{"deleted": true, "id": j.ID})
}
