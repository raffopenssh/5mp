package srv

import (
	"database/sql"
	"fmt"
	"html/template"
	"log/slog"
	"net/http"
	"path/filepath"
	"runtime"

	"srv.exe.dev/db"
	"srv.exe.dev/srv/areas"
	"srv.exe.dev/srv/auth"
)

type Server struct {
	DB           *sql.DB
	Hostname     string
	TemplatesDir string
	StaticDir    string
	AreaStore    *areas.AreaStore
	WDPAIndex    *areas.WDPAIndex
	Auth         *auth.Manager
	LegalStore   *LegalStore
	GADMStore    *GADMStore
	GPXLearner   *GPXLearner
	UploadQueueProcessor *UploadQueueProcessor
}

// Version is set at build time via ldflags
var Version = "dev"

type pageData struct {
	Hostname string
	User     *auth.User
	Version  string
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
	srv.Auth = auth.NewManager(srv.DB)
	
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

	data := pageData{
		Hostname: s.Hostname,
		User:     user,
		Version:  Version,
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := s.renderTemplate(w, "globe.html", data); err != nil {
		slog.Warn("render template", "url", r.URL.Path, "error", err)
	}
}

func (s *Server) renderTemplate(w http.ResponseWriter, name string, data any) error {
	path := filepath.Join(s.TemplatesDir, name)
	tmpl, err := template.ParseFiles(path)
	if err != nil {
		return fmt.Errorf("parse template %q: %w", name, err)
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
	
	// Public routes
	mux.HandleFunc("GET /{$}", s.HandleRoot)
	mux.HandleFunc("GET /login", s.HandleLoginPage)
	mux.HandleFunc("POST /login", s.HandleLogin)
	mux.HandleFunc("GET /logout", s.HandleLogout)
	mux.HandleFunc("GET /register", s.HandleRegisterPage)
	mux.HandleFunc("POST /register", s.HandleRegister)
	
	// Protected routes (require auth)
	mux.HandleFunc("GET /upload", s.HandleUploadPage)
	mux.HandleFunc("POST /upload", s.HandleUpload)

	// Async upload endpoints
	mux.HandleFunc("POST /api/upload/async", s.HandleAsyncUpload)
	mux.HandleFunc("GET /api/upload/status/{id}", s.HandleUploadStatus)
	
	// Admin routes (require admin role)
	mux.HandleFunc("GET /admin", s.RequireAdmin(s.HandleAdminPage))
	mux.HandleFunc("POST /admin/approve", s.RequireAdmin(s.HandleApproveUser))
	mux.HandleFunc("POST /admin/reject", s.RequireAdmin(s.HandleRejectUser))
	mux.HandleFunc("POST /admin/upload/fire", s.RequireAdmin(s.HandleUploadFire))
	mux.HandleFunc("POST /admin/upload/ghsl", s.RequireAdmin(s.HandleUploadGHSL))
	mux.HandleFunc("GET /admin/status", s.RequireAdmin(s.HandleProcessingStatus))
	
	// API routes
	mux.HandleFunc("GET /api/version", s.HandleAPIVersion)
	mux.HandleFunc("GET /api/grid", s.HandleAPIGrid)
	mux.HandleFunc("GET /api/grid/{id}/effort", s.HandleAPIGridCellEffort)
	mux.HandleFunc("GET /api/worldclim/test", s.HandleWorldClimTest)
	mux.HandleFunc("GET /api/areas", s.HandleAPIAreas)
	mux.HandleFunc("GET /api/areas/search", s.HandleAPIAreasSearch)
	mux.HandleFunc("GET /api/wdpa/search", s.HandleAPIWDPASearch)
	
	// API auth endpoints
	mux.HandleFunc("POST /api/login", s.HandleAPILogin)
	mux.HandleFunc("POST /api/register", s.HandleAPIRegister)
	mux.HandleFunc("POST /api/logout", s.HandleAPILogout)
	mux.HandleFunc("POST /api/upload", s.HandleAPIUpload)
	mux.HandleFunc("GET /api/stats", s.HandleAPIStats)
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
	mux.HandleFunc("GET /api/export/merged.kml", s.HandleAPIMergedKML)
	
	// MBTiles generation endpoints
	mux.HandleFunc("POST /api/parks/{id}/mbtiles", s.HandleAPIMBTilesCreate)
	mux.HandleFunc("GET /api/parks/{id}/mbtiles/estimate", s.HandleAPIMBTilesEstimate)
	mux.HandleFunc("GET /api/mbtiles/{id}/status", s.HandleAPIMBTilesStatus)
	mux.HandleFunc("GET /api/mbtiles/{id}/download", s.HandleAPIMBTilesDownload)
	mux.HandleFunc("GET /api/mbtiles", s.HandleAPIMBTilesList)
	mux.HandleFunc("GET /api/parks/{id}/climate", s.HandleAPIParkClimate)
	mux.HandleFunc("GET /api/parks/{id}/infrastructure", s.HandleAPIParkInfrastructure)
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
	mux.HandleFunc("GET /api/fire-alerts", s.HandleAPIFireAlerts)
	mux.HandleFunc("POST /api/admin/update-fire-alerts", s.HandleAPIUpdateFireAlerts)
	mux.HandleFunc("POST /api/update-fire-alerts", s.HandleAPIUpdateFireAlerts) // Non-admin for cron
	mux.HandleFunc("GET /api/parks/{id}/deforestation-narrative", s.HandleAPIDeforestationNarrative)
	mux.HandleFunc("GET /api/parks/{id}/settlement-narrative", s.HandleAPISettlementNarrative)
	mux.HandleFunc("GET /api/parks/{id}/classified-settlements", s.HandleAPIClassifiedSettlements)
	mux.HandleFunc("GET /api/parks/{id}/classified-deforestation", s.HandleAPIClassifiedDeforestation)

	// Export endpoint
	mux.HandleFunc("GET /api/export/parks", s.HandleAPIExportParks)
	mux.HandleFunc("GET /api/export/patrol-pixels", s.HandleAPIExportPatrolPixels)
	
	// Admin APIs
	mux.HandleFunc("GET /api/admin/gpx-logs", s.HandleAPIGPXUploadLogs)
	mux.HandleFunc("GET /api/admin/learning-results", s.HandleAPILearningResults)
	mux.HandleFunc("GET /api/admin/pending-approvals", s.HandleAPIPendingApprovals)
	// Admin sync triggers - use password auth, not session (for cron jobs)
	mux.HandleFunc("POST /api/admin/trigger-faolex-sync", s.HandleAPITriggerFAOLEXSync)
	mux.HandleFunc("POST /api/admin/trigger-publication-sync", s.HandleAPITriggerPublicationSync)
	mux.HandleFunc("GET /api/admin/learned-features", s.HandleAPILearnedFeatures)
	mux.HandleFunc("GET /api/admin/feature-history", s.HandleAPIFeatureHistory)
	mux.HandleFunc("POST /api/admin/rollback-feature", s.HandleAPIRollbackFeature)
	mux.HandleFunc("GET /api/parks/{id}/patrol-mcp", s.HandleAPIPatrolMCP)
	mux.HandleFunc("GET /api/parks/{id}/learned-stats", s.HandleAPILearnedFeatureStats)
	mux.HandleFunc("POST /api/admin/approve-feature", s.RequireAdmin(s.HandleAPIApproveLearnedFeature))
	mux.HandleFunc("POST /api/admin/reject-feature", s.RequireAdmin(s.HandleAPIRejectLearnedFeature))
	mux.HandleFunc("POST /api/admin/bulk-approve", s.RequireAdmin(s.HandleAPIBulkApprove))
	mux.HandleFunc("POST /api/admin/bulk-reject", s.RequireAdmin(s.HandleAPIBulkReject))
	mux.HandleFunc("POST /api/admin/delete-upload", s.RequireAdmin(s.HandleAPIDeleteUpload))
	mux.HandleFunc("GET /api/admin/upload-detail", s.HandleAPIUploadDetail)
	mux.HandleFunc("POST /api/admin/hide-notification", s.RequireAdmin(s.HandleAPIHideNotification))

	// Automated fetch (EarthRanger/PAMDAS GPS sync)
	mux.HandleFunc("GET /api/admin/autofetch", s.HandleAPIAutofetchList)
	mux.HandleFunc("POST /api/admin/autofetch/add", s.HandleAPIAutofetchAdd)
	mux.HandleFunc("POST /api/admin/autofetch/disable", s.HandleAPIAutofetchDisable)
	mux.HandleFunc("POST /api/admin/autofetch/enable", s.HandleAPIAutofetchEnable)
	mux.HandleFunc("POST /api/admin/autofetch/delete", s.HandleAPIAutofetchDelete)
	mux.HandleFunc("POST /api/admin/autofetch/run", s.HandleAPIAutofetchRunNow)
	mux.HandleFunc("GET /api/admin/autofetch/script", s.HandleAPIAutofetchScript)

	// RSS Feed for starred items
	mux.HandleFunc("GET /api/feed", s.HandleAPIFeed)
	mux.HandleFunc("GET /test/pinning", s.HandleTestPinning)

	// Notifications
	mux.HandleFunc("GET /api/notifications", s.HandleGetNotifications)
	mux.HandleFunc("POST /api/notifications/{id}/read", s.HandleMarkNotificationRead)
	mux.HandleFunc("POST /api/notifications/read-all", s.HandleMarkAllNotificationsRead)

	// Static files
	mux.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.Dir(s.StaticDir))))
	
	// Initialize MBTiles queue
	InitMBTilesQueue("data/mbtiles_output", s.DB)
	
	slog.Info("starting server", "addr", addr)
	
	// Wrap with password protection middleware, then gzip compression
	protectedHandler := s.PasswordMiddleware(mux)
	compressedHandler := GzipMiddleware(protectedHandler)
	return http.ListenAndServe(addr, compressedHandler)
}


