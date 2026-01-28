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
}

type pageData struct {
	Hostname string
	User     *auth.User
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
	return srv, nil
}

func (s *Server) HandleRoot(w http.ResponseWriter, r *http.Request) {
	user := s.Auth.GetUserFromRequest(r)

	data := pageData{
		Hostname: s.Hostname,
		User:     user,
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
	mux.HandleFunc("GET /upload", s.RequireAuth(s.HandleUploadPage))
	mux.HandleFunc("POST /upload", s.RequireAuth(s.HandleUpload))
	
	// Admin routes (require admin role)
	mux.HandleFunc("GET /admin", s.RequireAdmin(s.HandleAdminPage))
	mux.HandleFunc("POST /admin/approve", s.RequireAdmin(s.HandleApproveUser))
	mux.HandleFunc("POST /admin/reject", s.RequireAdmin(s.HandleRejectUser))
	mux.HandleFunc("POST /admin/upload/fire", s.RequireAdmin(s.HandleUploadFire))
	mux.HandleFunc("POST /admin/upload/ghsl", s.RequireAdmin(s.HandleUploadGHSL))
	mux.HandleFunc("GET /admin/status", s.RequireAdmin(s.HandleProcessingStatus))
	
	// API routes
	mux.HandleFunc("GET /api/grid", s.HandleAPIGrid)
	mux.HandleFunc("GET /api/areas", s.HandleAPIAreas)
	mux.HandleFunc("GET /api/areas/search", s.HandleAPIAreasSearch)
	mux.HandleFunc("GET /api/wdpa/search", s.HandleAPIWDPASearch)
	
	// API auth endpoints
	mux.HandleFunc("POST /api/login", s.HandleAPILogin)
	mux.HandleFunc("POST /api/register", s.HandleAPIRegister)
	mux.HandleFunc("POST /api/logout", s.HandleAPILogout)
	mux.HandleFunc("POST /api/upload", s.HandleAPIUpload)
	mux.HandleFunc("GET /api/stats", s.HandleAPIStats)
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
	mux.HandleFunc("GET /api/parks/{id}/publications", s.HandleAPIPublications)
	mux.HandleFunc("GET /api/parks/{id}/checklist", s.HandleAPIGetParkChecklist)
	mux.HandleFunc("GET /api/parks/{id}/stats", s.HandleAPIParkStats)

	// Static files
	mux.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.Dir(s.StaticDir))))
	
	slog.Info("starting server", "addr", addr)
	return http.ListenAndServe(addr, mux)
}


