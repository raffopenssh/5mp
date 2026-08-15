package srv

import (
	"context"
	"database/sql"
	"fmt"
	"html/template"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"time"

	"srv.exe.dev/db"
	"srv.exe.dev/srv/areas"
	"srv.exe.dev/srv/auth"
)

type Server struct {
	DB                   *sql.DB
	Hostname             string
	TemplatesDir         string
	StaticDir            string
	AreaStore            *areas.AreaStore
	WDPAIndex            *areas.WDPAIndex
	Auth                 *auth.Manager
	LegalStore           *LegalStore
	GADMStore            *GADMStore
	GPXLearner           *GPXLearner
	UploadQueueProcessor *UploadQueueProcessor
	httpServer           *http.Server
	templates            map[string]*template.Template
}

// Version is set at build time via ldflags
var Version = "dev"

type pageData struct {
	Hostname string
	User     *auth.User
	Version  string
	IsTest   bool
	// HasPatrol reports whether this request's TENANT owns any patrol effort
	// at all (srv/tenant.go). Patrol pixels belong to the passwords they were
	// uploaded under, so for every other tenant the layer is legitimately
	// empty -- and an empty layer that still offers a toggle and an animator
	// chip reads as a broken feature, not as "not yours". The UI dims them.
	HasPatrol bool
	// AuthLabel identifies the current session in the alpha UI chip.
	// Today: the access password used. Later (user management): user email.
	// For a guest capability it is the LINK's name (srv/guest.go).
	AuthLabel string
	// IsGuest: this page is being read through a read-only shared link. The
	// server already refuses every write; the UI hides the controls that
	// would produce one, because an editor that 403s on save is worse than an
	// editor that was never offered.
	IsGuest bool
}

func New(dbPath, hostname string) (*Server, error) {
	_, thisFile, _, _ := runtime.Caller(0)
	baseDir := filepath.Dir(thisFile)
	srv := &Server{
		Hostname:     hostname,
		TemplatesDir: filepath.Join(baseDir, "templates"),
		StaticDir:    filepath.Join(baseDir, "static"),
	}
	if err := srv.setUpDatabase(dbPath); err != nil {
		return nil, err
	}
	if err := srv.loadTemplates(); err != nil {
		return nil, fmt.Errorf("load templates: %w", err)
	}
	srv.Auth = auth.NewManager(srv.DB)

	// One principal per configured access password (sha256 prefix, never the
	// secret). Seeded here rather than in the migration because
	// ACCESS_PASSWORDS lives in the environment.
	if err := srv.SeedPrincipals(); err != nil {
		slog.Warn("seed principals", "error", err)
	}
	if err := srv.RefreshAOIIDs(); err != nil {
		slog.Warn("load aoi ids", "error", err)
	}
	// Best effort, and a warning is the correct failure: this closes a privacy
	// hole in old rows, and it must never be able to take the site down (a
	// migration attempt at the same job restart-looped the service when the
	// write lock was held). It converges on the first boot that gets a slot.
	if err := srv.reownSystemAOIProgress(); err != nil {
		slog.Warn("reown aoi_progress notifications", "error", err)
	}

	// Start the GPX learner background processor
	srv.GPXLearner = NewGPXLearner(srv.DB)
	srv.GPXLearner.Start()

	// Start the upload queue processor
	srv.UploadQueueProcessor = NewUploadQueueProcessor(srv.DB, srv)
	srv.UploadQueueProcessor.Start()

	return srv, nil
}

func (s *Server) HandleRoot(w http.ResponseWriter, r *http.Request) {
	user := s.Auth.GetUserFromRequest(r)

	// Session chip label: prefer real user identity (future user management),
	// fall back to the alpha access password.
	authLabel := RequestPwd(r)
	if user != nil && user.Email != "" {
		authLabel = user.Email
	}
	// A guest holds a link, not a password, and the chip must say so: showing
	// a password there would be showing them one they were never given, and
	// showing nothing would leave them unable to tell a shared view from a
	// signed-in one. IsGuest also switches the UI to read-only.
	guest := GuestFromRequest(r)
	if guest != nil {
		authLabel = guest.Title
		if authLabel == "" {
			authLabel = "shared link"
		}
	}

	data := pageData{
		Hostname: s.Hostname,
		User:     user,
		Version:  Version,
		IsTest:   RequestEnv(r) == sandboxTenant,
		// PatrolEnv, not RequestEnv: a shared link that does not carry the
		// patrol capability owns no patrol data as far as this page is
		// concerned, so the UI dims the layer for exactly the reason it dims
		// it for a tenant with no uploads -- the answer would be empty.
		HasPatrol: s.tenantHasPatrol(PatrolEnv(r)),
		AuthLabel: authLabel,
		IsGuest:   guest != nil,
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := s.renderTemplate(w, "globe.html", data); err != nil {
		slog.Warn("render template", "url", r.URL.Path, "error", err)
	}
}

func (s *Server) loadTemplates() error {
	s.templates = make(map[string]*template.Template)
	entries, err := filepath.Glob(filepath.Join(s.TemplatesDir, "*.html"))
	if err != nil {
		return fmt.Errorf("glob templates: %w", err)
	}
	for _, path := range entries {
		name := filepath.Base(path)
		tmpl, err := template.ParseFiles(path)
		if err != nil {
			return fmt.Errorf("parse template %q: %w", name, err)
		}
		s.templates[name] = tmpl
	}
	slog.Info("cached templates", "count", len(s.templates))
	return nil
}

func (s *Server) renderTemplate(w http.ResponseWriter, name string, data any) error {
	tmpl, ok := s.templates[name]
	if !ok {
		return fmt.Errorf("template %q not found", name)
	}
	if err := tmpl.Execute(w, data); err != nil {
		return fmt.Errorf("execute template %q: %w", name, err)
	}
	return nil
}

// SetupDatabase initializes the database connection and runs migrations
func (s *Server) setUpDatabase(dbPath string) error {
	wdb, err := db.Open(dbPath)
	if err != nil {
		return fmt.Errorf("failed to open db: %w", err)
	}
	s.DB = wdb
	if err := db.RunMigrations(wdb); err != nil {
		return fmt.Errorf("failed to run migrations: %w", err)
	}
	return nil
}

// Serve starts the HTTP server with the configured routes
func (s *Server) Serve(addr string) error {
	mux := http.NewServeMux()

	// Rate limiters
	authRL := newRateLimiter(1, time.Second, 10)  // 10 burst, 1/sec refill
	uploadRL := newRateLimiter(1, time.Second, 5) // 5 burst, 1/sec refill

	// Public routes
	mux.HandleFunc("GET /{$}", s.HandleRoot)
	mux.HandleFunc("GET /login", s.HandleLoginPage)
	mux.HandleFunc("GET /impressum", s.HandleImpressum)
	mux.HandleFunc("GET /datenschutz", s.HandleDatenschutz)
	mux.HandleFunc("POST /login", RateLimitMiddleware(authRL, s.HandleLogin))
	mux.HandleFunc("GET /logout", s.HandleLogout)
	mux.HandleFunc("GET /register", s.HandleRegisterPage)
	mux.HandleFunc("POST /register", RateLimitMiddleware(authRL, s.HandleRegister))

	// Protected routes (require auth)
	mux.HandleFunc("GET /upload", s.HandleUploadPage)
	mux.HandleFunc("POST /upload", RateLimitMiddleware(uploadRL, s.HandleUpload))

	// Async upload endpoints
	mux.HandleFunc("POST /api/upload/async", RateLimitMiddleware(uploadRL, s.HandleAsyncUpload))
	mux.HandleFunc("GET /api/upload/status/{id}", s.HandleUploadStatus)

	// Admin routes (require admin role)
	mux.HandleFunc("GET /admin", s.RequireAdmin(s.HandleAdminPage))
	mux.HandleFunc("POST /admin/approve", s.RequireAdmin(s.HandleApproveUser))
	mux.HandleFunc("POST /admin/reject", s.RequireAdmin(s.HandleRejectUser))
	mux.HandleFunc("POST /admin/upload/fire", s.RequireAdmin(s.HandleUploadFire))
	mux.HandleFunc("POST /admin/upload/ghsl", s.RequireAdmin(s.HandleUploadGHSL))
	mux.HandleFunc("GET /admin/status", s.RequireAdmin(s.HandleProcessingStatus))

	// Short links (srv/shortlink.go). /s/{slug} is either a name for a long
	// URL or a read-only capability; both resolve here.
	mux.HandleFunc("GET /s/{slug}", s.HandleShortLink)
	mux.HandleFunc("POST /api/shortlink", s.HandleAPIShortLinkCreate)
	mux.HandleFunc("POST /api/shortlink/{slug}/rename", s.HandleAPIShortLinkRename)
	mux.HandleFunc("DELETE /api/shortlink/{slug}", s.HandleAPIShortLinkDelete)
	mux.HandleFunc("GET /api/shortlinks", s.HandleAPIShortLinkList)

	// API routes
	mux.HandleFunc("GET /api/version", s.HandleAPIVersion)
	mux.HandleFunc("GET /api/pipeline-status", s.HandleAPIPipelineStatus)

	// Historical map overlay (Sudan Survey 1:250k, 1908-1944). Not under
	// /api/parks/* on purpose: it is a basemap-level raster keyed by tile
	// coordinate, not by park id.
	mux.HandleFunc("GET /api/histmap", s.HandleAPIHistMapMeta)
	mux.HandleFunc("GET /api/histmap/sudan250k/download", s.HandleAPIHistMapDownload)
	mux.HandleFunc("GET /api/histmap/sudan250k/labels", s.HandleAPIHistMapLabels)
	mux.HandleFunc("GET /api/histmap/sudan250k/labels/download/{format}", s.HandleAPIHistMapLabelsDownload)
	mux.HandleFunc("GET /api/histmap/sudan250k/lines", s.HandleAPIHistMapLines)
	mux.HandleFunc("GET /api/histmap/sudan250k/lines/download", s.HandleAPIHistMapLinesDownload)
	mux.HandleFunc("GET /api/histmap/sudan250k/around", s.HandleAPIHistMapAround)
	mux.HandleFunc("GET /api/histmap/sudan250k/{z}/{x}/{y}", s.HandleAPIHistMapTile)
	// Geology overlays (Sudan GRAS 2004, CAR BRGM 1964) -- vector tiles, not
	// raster: the units are data the client recolours, hides and groups by
	// commodity. Same 204-on-miss and ?v=<rev> conventions as the histmap.
	mux.HandleFunc("GET /api/geomap", s.HandleAPIGeoMap)
	mux.HandleFunc("GET /api/geomap/{sheet}/download", s.HandleAPIGeoMapDownload)
	// One GeoPackage for every sheet: the map is one layer, so the data behind
	// it is one file. The per-sheet path stays as a redirect because links to it
	// are already in circulation (and in docs) -- a 404 there would read as "the
	// export was removed", which is not what happened.
	mux.HandleFunc("GET /api/geomap/geopackage", s.HandleAPIGeoMapGeoPackage)
	// The CURRENT VIEW as a GeoPackage. POST because the body is the selection
	// the client resolved (unit keys + contact pairs), which is too long for a
	// URL and is not a name for anything: it is one reader's filter, built per
	// request and never cached. The GET above stays the whole catalogue and
	// keeps its stamped cache; a filtered build must not overwrite it.
	// A guest link cannot reach this (guestMayRead is GET/HEAD only), which is
	// correct — not because the geology is private, but because a capability
	// must not be able to make the server build things.
	mux.HandleFunc("POST /api/geomap/geopackage", s.HandleAPIGeoMapGeoPackageView)
	mux.HandleFunc("GET /api/geomap/{sheet}/geopackage", s.HandleAPIGeoMapGeoPackageLegacy)
	// Continental structural linework (JRC AKP faults + craton margins) —
	// whole GeoJSON, not tiles: 415 features. Its own prefix, NOT
	// /api/geomap/structural/{layer}: that pattern conflicts with
	// GET /api/geomap/{sheet}/download in Go's ServeMux (both match
	// "structural/download", neither is more specific) and the server
	// panics at startup.
	mux.HandleFunc("GET /api/geomap-structural/{layer}", s.HandleAPIGeoMapStructural)
	mux.HandleFunc("GET /api/geomap/{sheet}/{z}/{x}/{y}", s.HandleAPIGeoMapTile)
	mux.HandleFunc("GET /api/grid", s.HandleAPIGrid)
	mux.HandleFunc("GET /api/nearby-places", s.HandleAPINearbyPlaces)
	mux.HandleFunc("GET /api/grid/{id}/effort", s.HandleAPIGridCellEffort)
	mux.HandleFunc("GET /api/worldclim/test", s.HandleWorldClimTest)
	mux.HandleFunc("GET /api/areas", s.HandleAPIAreas)
	mux.HandleFunc("GET /api/areas/search", s.HandleAPIAreasSearch)
	mux.HandleFunc("GET /api/wdpa/search", s.HandleAPIWDPASearch)
	mux.HandleFunc("POST /api/onboarding/request", s.HandleAPIRequestOnboard)
	mux.HandleFunc("POST /api/onboarding/cancel", s.HandleAPICancelOnboard)
	mux.HandleFunc("GET /api/onboarding", s.HandleAPIOnboardingStatus)

	// Areas of interest (AOI). Separate id space and route prefix from parks
	// on purpose: one middleware + per-handler visibility check is the whole
	// enforcement surface (docs/PLAN_AOI_OVERLAY.md §9).
	mux.HandleFunc("GET /api/aois", s.HandleAPIAOIList)
	// Literal before the wildcard: 'search' is not an id, and the only reason
	// the mux gets that right on its own is Go 1.22's specificity rule.
	mux.HandleFunc("GET /api/aois/search", s.HandleAPIAOISearch)
	mux.HandleFunc("GET /api/aois/{id}", s.HandleAPIAOIGet)
	// Read surface: the park handlers, gated on visibility. They key off
	// r.PathValue("id") and read the same tables the --aoi v5 chain writes.
	mux.HandleFunc("GET /api/aois/{id}/fire-narrative", s.aoiGate(s.HandleAPIFireNarrative))
	mux.HandleFunc("GET /api/aois/{id}/fire-trend", s.aoiGate(s.HandleAPIFireTrend))
	mux.HandleFunc("GET /api/aois/{id}/fire-realtime", s.aoiGate(s.HandleAPIFireRealtime))
	mux.HandleFunc("GET /api/aois/{id}/features", s.aoiGate(s.HandleAPIParkFeatures))
	mux.HandleFunc("GET /api/aois/{id}/deforestation-narrative", s.aoiGate(s.HandleAPIDeforestationNarrative))
	mux.HandleFunc("GET /api/aois/{id}/settlement-narrative", s.aoiGate(s.HandleAPISettlementNarrative))
	mux.HandleFunc("GET /api/aois/{id}/export.geojson", s.HandleAPIAOIExportGeoJSON)
	// The rest of the read surface an AOI genuinely has data for. Same
	// handlers, same gate. What is deliberately ABSENT is as load-bearing as
	// what is here: species, climate, publications, legal and checklist are
	// per-protected-area facts, and averaging them over 485,000 km2 would
	// invent a number. The AOI popup points at the intersecting parks instead
	// (AGENTS.md "Areas of interest" an AOI is not").
	mux.HandleFunc("GET /api/aois/{id}/feature-stats", s.aoiGate(s.HandleAPIParkFeatureStats))
	mux.HandleFunc("GET /api/aois/{id}/classified-settlements", s.aoiGate(s.HandleAPIClassifiedSettlements))
	mux.HandleFunc("GET /api/aois/{id}/classified-deforestation", s.aoiGate(s.HandleAPIClassifiedDeforestation))
	mux.HandleFunc("GET /api/aois/{id}/settlement-intensity", s.aoiGate(s.HandleAPISettlementIntensity))
	mux.HandleFunc("GET /api/aois/{id}/infrastructure", s.aoiGate(s.HandleAPIParkInfrastructure))
	mux.HandleFunc("GET /api/aois/{id}/basin", s.aoiGate(s.HandleAPIParkBasin))
	// Exports. Both resolve their boundary through resolveAreaGeom(), which
	// knows about AOIs -- an AOI is not in AreaStore by design.
	mux.HandleFunc("GET /api/aois/{id}/export.kml", s.aoiGate(s.HandleAPIParkKML))
	mux.HandleFunc("GET /api/aois/{id}/export.locus", s.aoiGate(s.HandleAPIParkLocus))
	// GeoPackage: every layer, whole, typed and styled for QGIS. It cannot be
	// served inline (millions of fire detections, well past WriteTimeout), so
	// this returns a job and the file is fetched from /api/geopackage/{id}.
	mux.HandleFunc("POST /api/aois/{id}/export.gpkg", s.aoiGate(s.HandleAPIAreaGeoPackage))
	mux.HandleFunc("GET /api/aois/{id}/export.gpkg", s.aoiGate(s.HandleAPIAreaGeoPackage))
	// Offline satellite tiles. A tile pyramid has nothing park-specific in it
	// -- it is a rectangle of imagery -- so the only reason an AOI could not
	// have one was that the handlers looked the id up in AreaStore, where an
	// AOI deliberately never appears. They go through resolveAreaBBox() now.
	// The job status/download routes are shared with parks: a job id is opaque
	// and unguessable, and the file is imagery, not the polygon.
	mux.HandleFunc("POST /api/aois/{id}/mbtiles", s.aoiGate(s.HandleAPIZenodoMBTilesCreate))
	mux.HandleFunc("GET /api/aois/{id}/mbtiles/estimate", s.aoiGate(s.HandleAPIZenodoMBTilesEstimate))
	// Write surface (docs/PLAN_AOI_OVERLAY.md §3f). None of these run the
	// ingest: scripts/aoi_runner.py owns the lease discipline and is the only
	// thing that works a unit. These queue, requeue, price and report.
	//
	// NOTE ordering: 'POST /api/aois/estimate' must be registered before any
	// '/api/aois/{id}' pattern would swallow it. Go 1.22's mux prefers the more
	// specific literal, so this is documentation rather than load-bearing --
	// but 'estimate' is also rejected by ValidAOIID collisions only by luck,
	// so keep it a literal.
	mux.HandleFunc("POST /api/aois/estimate", s.HandleAPIAOIEstimate)
	mux.HandleFunc("POST /api/aois", s.HandleAPIAOICreate)
	mux.HandleFunc("GET /api/aois/{id}/progress", s.HandleAPIAOIProgress)
	mux.HandleFunc("POST /api/aois/{id}/refresh", s.HandleAPIAOIRefresh)
	mux.HandleFunc("POST /api/aois/{id}/kick", s.HandleAPIAOIKick)
	// Cancel = stop fetching, keep what landed. The abort the progress card
	// offers; /refresh is the way to resume from the same cursors.
	mux.HandleFunc("POST /api/aois/{id}/cancel", s.HandleAPIAOICancel)
	mux.HandleFunc("DELETE /api/aois/{id}", s.HandleAPIAOIDelete)
	// Versioning (migration 042): an edit forks, it never mutates. See
	// srv/aoi_versions.go for why.
	mux.HandleFunc("GET /api/aois/{id}/versions", s.HandleAPIAOIVersions)
	mux.HandleFunc("POST /api/aois/{id}/edit", s.HandleAPIAOIEdit)
	mux.HandleFunc("POST /api/aois/{id}/restore", s.HandleAPIAOIRestore)
	// Rename is NOT an edit: a label is not the question, so it keeps the id
	// (and every share link and derived row keyed by it) and forks nothing.
	mux.HandleFunc("POST /api/aois/{id}/rename", s.HandleAPIAOIRename)
	// Archive = hide the overlay, keep the data and the links. The button most
	// users reach for when they mean "delete"; /restore is the way back.
	mux.HandleFunc("POST /api/aois/{id}/archive", s.HandleAPIAOIArchive)

	// API auth endpoints
	mux.HandleFunc("POST /api/login", RateLimitMiddleware(authRL, s.HandleAPILogin))
	mux.HandleFunc("POST /api/register", RateLimitMiddleware(authRL, s.HandleAPIRegister))
	mux.HandleFunc("POST /api/logout", s.HandleAPILogout)
	mux.HandleFunc("POST /api/upload", RateLimitMiddleware(uploadRL, s.HandleAPIUpload))
	mux.HandleFunc("GET /api/stats", s.HandleAPIStats)
	mux.HandleFunc("GET /api/features-in-bbox", s.HandleAPIFeaturesInBBox)
	// One feature by row id: what a zoomed-out point dot resolves to when the
	// user hovers it (srv/features_bbox.go).
	mux.HandleFunc("GET /api/feature-detail", s.HandleAPIFeatureDetail)
	// "Export what is on my screen" — a viewport, not an area, because the
	// common case is a paused animation over several countries (srv/gpkg_view.go).
	mux.HandleFunc("POST /api/view/export.gpkg", s.HandleAPIViewGeoPackage)
	mux.HandleFunc("GET /api/view/export.gpkg", s.HandleAPIViewGeoPackage)
	mux.HandleFunc("GET /api/parks/export", s.HandleAPIParksExport)
	mux.HandleFunc("GET /api/activity", s.HandleAPIActivity)

	// Fire data endpoints
	mux.HandleFunc("GET /api/fire/chinko/daily", s.handleFireDailyData)
	mux.HandleFunc("GET /api/fire/chinko/boundary", s.handleFireBoundary)
	mux.HandleFunc("GET /api/fire/daily-geojson", s.handleFireDailyGeoJSON)
	mux.HandleFunc("GET /fire", s.handleFireAnalysis)
	mux.HandleFunc("GET /fire/animation", s.handleFireAnimation)
	mux.HandleFunc("GET /api/park/{id}/fire-analysis", s.handleParkFireAnalysis)
	mux.HandleFunc("GET /api/park/{id}/boundary", s.HandleParkBoundary)
	mux.HandleFunc("GET /api/park/{id}/roads", s.HandleParkRoads)
	mux.HandleFunc("GET /park/{id}", s.HandleParkAnalysis)

	// Legal framework endpoints
	mux.HandleFunc("GET /api/legal/pa/", s.HandleAPILegalByPA)
	mux.HandleFunc("GET /api/legal/", s.HandleAPILegalByCountry)

	// Checklist endpoints
	mux.HandleFunc("GET /api/checklist/schema", s.HandleAPIChecklistSchema)
	mux.HandleFunc("POST /api/checklist/update", s.HandleAPIUpdateChecklistItem)

	// Publications endpoints (more specific routes first)
	mux.HandleFunc("GET /api/parks/{id}/publications/count", s.HandleAPIPublicationCount)
	mux.HandleFunc("GET /api/parks/{id}/data-status", s.HandleAPIParkDataStatus)
	mux.HandleFunc("GET /api/parks/{id}/infractions", s.HandleAPIParkInfractionSummary)
	mux.HandleFunc("GET /api/parks/{id}/features", s.HandleAPIParkFeatures)
	mux.HandleFunc("GET /api/parks/{id}/feature-stats", s.HandleAPIParkFeatureStats)
	mux.HandleFunc("GET /api/parks/{id}/export.kml", s.HandleAPIParkKML)
	mux.HandleFunc("GET /api/parks/{id}/export.locus", s.HandleAPIParkLocus)
	mux.HandleFunc("POST /api/parks/{id}/export.gpkg", s.HandleAPIAreaGeoPackage)
	mux.HandleFunc("GET /api/parks/{id}/export.gpkg", s.HandleAPIAreaGeoPackage)
	// Job status + download are shared by parks and AOIs: the id is an opaque
	// token and the handler re-checks AOI visibility on every hit.
	mux.HandleFunc("GET /api/geopackage", s.HandleAPIGeoPackageList)
	mux.HandleFunc("GET /api/geopackage/{id}", s.HandleAPIGeoPackageStatus)
	mux.HandleFunc("GET /api/geopackage/{id}/download", s.HandleAPIGeoPackageDownload)
	// Delete before the TTL expires: the 21 days are a promise about links, not
	// an obligation to keep a gigabyte around after the file has been used.
	mux.HandleFunc("DELETE /api/geopackage/{id}", s.HandleAPIGeoPackageDelete)
	mux.HandleFunc("GET /api/export/merged.kml", s.HandleAPIMergedKML)

	// MBTiles generation endpoints (Zenodo-backed, with legacy fallback)
	mux.HandleFunc("POST /api/parks/{id}/mbtiles", s.HandleAPIZenodoMBTilesCreate)
	mux.HandleFunc("GET /api/parks/{id}/mbtiles/estimate", s.HandleAPIZenodoMBTilesEstimate)
	mux.HandleFunc("GET /api/mbtiles/{id}/status", s.HandleAPIZenodoMBTilesStatus)
	mux.HandleFunc("GET /api/mbtiles/{id}/download", s.HandleAPIZenodoMBTilesDownload)
	mux.HandleFunc("GET /api/mbtiles", s.HandleAPIZenodoMBTilesList)
	mux.HandleFunc("GET /api/parks/{id}/climate", s.HandleAPIParkClimate)
	mux.HandleFunc("GET /api/parks/{id}/turbidity", s.HandleAPIParkTurbidity)
	mux.HandleFunc("GET /api/parks/{id}/infrastructure", s.HandleAPIParkInfrastructure)
	mux.HandleFunc("GET /api/parks/{id}/basin", s.HandleAPIParkBasin)
	mux.HandleFunc("GET /api/parks/{id}/species", s.HandleAPIParkSpecies)
	mux.HandleFunc("GET /api/parks/{id}/settlement-intensity", s.HandleAPISettlementIntensity)
	mux.HandleFunc("GET /api/parks/{id}/publications", s.HandleAPIPublications)
	mux.HandleFunc("GET /api/parks/{id}/legal", s.HandleAPILegalDocuments)
	mux.HandleFunc("GET /api/parks/{id}/checklist", s.HandleAPIGetParkChecklist)
	mux.HandleFunc("GET /api/parks/{id}/stats", s.HandleAPIParkStats)
	mux.HandleFunc("GET /api/parks/{id}/documents", s.HandleAPIParkDocuments)
	mux.HandleFunc("GET /api/parks/{id}/management-plans", s.HandleAPIParkManagementPlans)

	// Narrative endpoints (rich textual descriptions with OSM place names)
	mux.HandleFunc("GET /api/parks/{id}/fire-narrative", s.HandleAPIFireNarrative)
	mux.HandleFunc("GET /api/parks/{id}/fire-realtime", s.HandleAPIFireRealtime)
	mux.HandleFunc("GET /api/parks/{id}/fire-trend", s.HandleAPIFireTrend)
	mux.HandleFunc("GET /api/fire-alerts", s.HandleAPIFireAlerts)
	mux.HandleFunc("GET /api/fire-frames", s.HandleAPIFireFrames)
	mux.HandleFunc("GET /api/fire-anim-trajectories", s.HandleAPIFireAnimTrajectories)
	mux.HandleFunc("POST /api/admin/update-fire-alerts", s.RequireAdmin(s.HandleAPIUpdateFireAlerts))
	mux.HandleFunc("POST /api/update-fire-alerts", s.RequireAdminOrLocal(s.HandleAPIUpdateFireAlerts))
	mux.HandleFunc("POST /api/refresh-park", s.RequireAdminOrLocal(s.HandleAPIRefreshPark))
	mux.HandleFunc("GET /api/parks/{id}/deforestation-narrative", s.HandleAPIDeforestationNarrative)
	mux.HandleFunc("GET /api/parks/{id}/settlement-narrative", s.HandleAPISettlementNarrative)
	mux.HandleFunc("GET /api/parks/{id}/classified-settlements", s.HandleAPIClassifiedSettlements)
	mux.HandleFunc("GET /api/parks/{id}/classified-deforestation", s.HandleAPIClassifiedDeforestation)

	// Export endpoint
	mux.HandleFunc("GET /api/export/parks", s.HandleAPIExportParks)
	mux.HandleFunc("GET /api/export/patrol-pixels", s.HandleAPIExportPatrolPixels)

	// Admin APIs
	mux.HandleFunc("GET /api/admin/gpx-logs", s.RequireAdmin(s.HandleAPIGPXUploadLogs))
	mux.HandleFunc("GET /api/admin/learning-results", s.RequireAdmin(s.HandleAPILearningResults))
	mux.HandleFunc("GET /api/admin/pending-approvals", s.RequireAdmin(s.HandleAPIPendingApprovals))
	mux.HandleFunc("POST /api/admin/trigger-faolex-sync", s.RequireAdminOrLocal(s.HandleAPITriggerFAOLEXSync))
	mux.HandleFunc("POST /api/admin/trigger-publication-sync", s.RequireAdminOrLocal(s.HandleAPITriggerPublicationSync))
	mux.HandleFunc("GET /api/admin/learned-features", s.RequireAdmin(s.HandleAPILearnedFeatures))
	mux.HandleFunc("GET /api/admin/learned-features-kml", s.RequireAdmin(s.HandleAPILearnedFeaturesKML))
	mux.HandleFunc("GET /api/admin/feature-history", s.RequireAdmin(s.HandleAPIFeatureHistory))
	mux.HandleFunc("POST /api/admin/rollback-feature", s.RequireAdmin(s.HandleAPIRollbackFeature))
	mux.HandleFunc("GET /api/parks/{id}/patrol-mcp", s.HandleAPIPatrolMCP)
	mux.HandleFunc("GET /api/parks/{id}/learned-stats", s.HandleAPILearnedFeatureStats)
	mux.HandleFunc("POST /api/admin/approve-feature", s.RequireAdmin(s.HandleAPIApproveLearnedFeature))
	mux.HandleFunc("POST /api/admin/reject-feature", s.RequireAdmin(s.HandleAPIRejectLearnedFeature))
	mux.HandleFunc("POST /api/admin/bulk-approve", s.RequireAdmin(s.HandleAPIBulkApprove))
	mux.HandleFunc("POST /api/admin/approve-high-confidence", s.RequireAdmin(s.HandleAPIApproveHighConfidence))
	mux.HandleFunc("POST /api/admin/bulk-reject", s.RequireAdmin(s.HandleAPIBulkReject))
	mux.HandleFunc("POST /api/admin/delete-upload", s.RequireAdmin(s.HandleAPIDeleteUpload))
	mux.HandleFunc("POST /api/admin/bulk-delete-uploads", s.RequireAdmin(s.HandleAPIBulkDeleteUploads))
	mux.HandleFunc("GET /api/admin/upload-detail", s.HandleAPIUploadDetail)
	mux.HandleFunc("POST /api/admin/hide-notification", s.RequireAdmin(s.HandleAPIHideNotification))

	// Access tab: AOI ownership + per-dataset queue control. RequireAdmin is
	// satisfied by any valid password here, so both handlers scope themselves to
	// the caller's principal (srv/aoi_admin.go) rather than trusting the gate.
	mux.HandleFunc("GET /api/admin/access", s.RequireAdmin(s.HandleAPIAdminAccess))
	mux.HandleFunc("POST /api/admin/aoi-dataset", s.RequireAdmin(s.HandleAPIAdminAOIDataset))

	// Automated fetch (EarthRanger/PAMDAS GPS sync)
	mux.HandleFunc("GET /api/admin/autofetch", s.HandleAPIAutofetchList)
	mux.HandleFunc("POST /api/admin/autofetch/add", s.HandleAPIAutofetchAdd)
	mux.HandleFunc("POST /api/admin/autofetch/disable", s.HandleAPIAutofetchDisable)
	mux.HandleFunc("POST /api/admin/autofetch/enable", s.HandleAPIAutofetchEnable)
	mux.HandleFunc("POST /api/admin/autofetch/delete", s.HandleAPIAutofetchDelete)
	mux.HandleFunc("POST /api/admin/autofetch/run", s.HandleAPIAutofetchRunNow)
	mux.HandleFunc("POST /api/admin/rebuild-effort", s.HandleAPIRebuildEffort)
	mux.HandleFunc("GET /api/admin/autofetch/script", s.HandleAPIAutofetchScript)

	// RSS Feed for starred items
	mux.HandleFunc("GET /api/feed", s.HandleAPIFeed)
	mux.HandleFunc("GET /test/pinning", s.HandleTestPinning)

	// Notifications
	mux.HandleFunc("GET /api/notifications", s.HandleGetNotifications)
	mux.HandleFunc("POST /api/notifications/{id}/read", s.HandleMarkNotificationRead)
	mux.HandleFunc("POST /api/notifications/read-all", s.HandleMarkAllNotificationsRead)

	// Health check (bypasses password middleware)
	mux.HandleFunc("GET /healthz", s.HandleHealthz)

	// Static files
	mux.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.Dir(s.StaticDir))))

	// SEO files at root (also allowed unauthenticated in PasswordMiddleware)
	mux.HandleFunc("GET /robots.txt", func(w http.ResponseWriter, r *http.Request) {
		http.ServeFile(w, r, s.StaticDir+"/robots.txt")
	})
	mux.HandleFunc("GET /sitemap.xml", func(w http.ResponseWriter, r *http.Request) {
		http.ServeFile(w, r, s.StaticDir+"/sitemap.xml")
	})

	// Initialize MBTiles queue (legacy disk-based, as fallback)
	InitMBTilesQueue("data/mbtiles_output", s.DB)

	// GeoPackage export cache: hourly expiry sweep + a startup pass that fails
	// jobs a restart orphaned (a card frozen at 40% forever is worse than an
	// error the user can retry).
	s.StartGeoPackageSweeper()

	// Initialize Zenodo-backed MBTiles queue (preferred)
	if token := os.Getenv("ZENODO_TOKEN"); token != "" {
		if err := InitZenodoMBTilesQueue(token, s.DB); err != nil {
			slog.Warn("Failed to init Zenodo MBTiles queue", "error", err)
		}
	} else {
		slog.Info("ZENODO_TOKEN not set, MBTiles will use disk storage only")
	}

	slog.Info("starting server", "addr", addr)

	// Wrap with security headers, compression, and password protection
	// ResponseCacheMiddleware sits inside Password (auth still enforced) and
	// inside Gzip (caches uncompressed bodies; gzip recompresses per client).
	protectedHandler := SecurityHeadersMiddleware(PrivateCacheMiddleware(GzipMiddleware(s.PasswordMiddleware(s.ResponseCacheMiddleware(ParkIDMiddleware(AOIMiddleware(mux)))))))

	s.httpServer = &http.Server{
		Addr:         addr,
		Handler:      protectedHandler,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 120 * time.Second,
		IdleTimeout:  120 * time.Second,
	}
	return s.httpServer.ListenAndServe()
}

// Shutdown gracefully stops the HTTP server.
func (s *Server) Shutdown() error {
	if s.httpServer == nil {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return s.httpServer.Shutdown(ctx)
}
