package srv

import (
	"archive/zip"
	"bytes"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"srv.exe.dev/db/dbgen"
)

type adminPageData struct {
	Hostname        string
	PendingUsers    []dbgen.User
	ApprovedUsers   []dbgen.User
	Stats           adminStats
	Success         string
	Error           string
	DiskAvailable   string
	NeededGHSLTiles []GHSLTileInfo
	HaveGHSLTiles   []string
	ProcessingStatus string
}

// GHSLTileInfo contains info about a GHSL tile and its download URL.
type GHSLTileInfo struct {
	Row int
	Col int
	ID  string
	URL string
}

type adminStats struct {
	TotalUsers      int
	PendingCount    int
	ApprovedCount   int
	TotalUploads    int64
	TotalDistanceKm float64
	TotalPoints     int64
}

// Processing status tracking
var (
	processingMu     sync.RWMutex
	processingStatus string
)

func setProcessingStatus(status string) {
	processingMu.Lock()
	defer processingMu.Unlock()
	processingStatus = status
}

func getProcessingStatus() string {
	processingMu.RLock()
	defer processingMu.RUnlock()
	return processingStatus
}

// All needed GHSL tiles: (row, col) pairs
var neededGHSLTiles = [][2]int{
	{5, 18}, {5, 19}, {6, 18}, {6, 19}, {6, 20},
	{7, 16}, {7, 17}, {7, 18}, {7, 19}, {7, 20}, {7, 21},
	{8, 17}, {8, 21}, {8, 22},
	{9, 18}, {9, 20}, {9, 21},
	{10, 19}, {10, 20}, {10, 21},
	{11, 19}, {11, 20}, {11, 21},
	{12, 19}, {12, 20},
}

// Already have these tiles
var haveGHSLTiles = map[string]bool{
	"R8_C18":  true,
	"R8_C19":  true,
	"R8_C20":  true,
	"R9_C19":  true,
	"R12_C21": true,
}

// HandleAdminPage renders the admin dashboard with pending and approved users.
func (s *Server) HandleAdminPage(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	q := dbgen.New(s.DB)

	// Get pending users
	pendingUsers, err := q.ListPendingUsers(ctx)
	if err != nil {
		slog.Error("failed to list pending users", "error", err)
		http.Error(w, "Internal server error", http.StatusInternalServerError)
		return
	}

	// Get approved users
	approvedUsers, err := q.ListApprovedUsers(ctx)
	if err != nil {
		slog.Error("failed to list approved users", "error", err)
		http.Error(w, "Internal server error", http.StatusInternalServerError)
		return
	}

	// Get global stats for current year
	currentYear := int64(time.Now().Year())
	globalStats, err := q.GetGlobalStats(ctx, currentYear)
	if err != nil {
		slog.Warn("failed to get global stats", "error", err)
		// Continue with zero stats
	}

	stats := adminStats{
		TotalUsers:    len(pendingUsers) + len(approvedUsers),
		PendingCount:  len(pendingUsers),
		ApprovedCount: len(approvedUsers),
	}

	// Extract stats values (they come as interface{} from COALESCE)
	if globalStats.TotalUploads != nil {
		if v, ok := globalStats.TotalUploads.(int64); ok {
			stats.TotalUploads = v
		} else if v, ok := globalStats.TotalUploads.(float64); ok {
			stats.TotalUploads = int64(v)
		}
	}
	if globalStats.TotalDistanceKm != nil {
		if v, ok := globalStats.TotalDistanceKm.(float64); ok {
			stats.TotalDistanceKm = v
		} else if v, ok := globalStats.TotalDistanceKm.(int64); ok {
			stats.TotalDistanceKm = float64(v)
		}
	}
	if globalStats.TotalPoints != nil {
		if v, ok := globalStats.TotalPoints.(int64); ok {
			stats.TotalPoints = v
		} else if v, ok := globalStats.TotalPoints.(float64); ok {
			stats.TotalPoints = int64(v)
		}
	}

	// Build list of needed GHSL tiles with download URLs
	var neededTiles []GHSLTileInfo
	var haveTiles []string
	for _, tile := range neededGHSLTiles {
		tileID := fmt.Sprintf("R%d_C%d", tile[0], tile[1])
		if haveGHSLTiles[tileID] {
			haveTiles = append(haveTiles, tileID)
		} else {
			neededTiles = append(neededTiles, GHSLTileInfo{
				Row: tile[0],
				Col: tile[1],
				ID:  tileID,
				URL: fmt.Sprintf("https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/GHS_BUILT_S_E2018_GLOBE_R2023A_54009_10/V1-0/tiles/GHS_BUILT_S_E2018_GLOBE_R2023A_54009_10_V1_0_R%d_C%d.zip", tile[0], tile[1]),
			})
		}
	}

	data := adminPageData{
		Hostname:         s.Hostname,
		PendingUsers:     pendingUsers,
		ApprovedUsers:    approvedUsers,
		Stats:            stats,
		Success:          r.URL.Query().Get("success"),
		Error:            r.URL.Query().Get("error"),
		DiskAvailable:    "3.4GB",
		NeededGHSLTiles:  neededTiles,
		HaveGHSLTiles:    haveTiles,
		ProcessingStatus: getProcessingStatus(),
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := s.renderTemplate(w, "admin.html", data); err != nil {
		slog.Warn("render admin template", "error", err)
	}
}

// HandleApproveUser handles POST requests to approve a pending user.
func (s *Server) HandleApproveUser(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	userID := r.FormValue("user_id")

	if userID == "" {
		http.Redirect(w, r, "/admin?error=User+ID+required", http.StatusSeeOther)
		return
	}

	// Get the admin user who is approving
	adminUser := s.Auth.GetUserFromRequest(r)
	if adminUser == nil {
		http.Redirect(w, r, "/login", http.StatusSeeOther)
		return
	}

	q := dbgen.New(s.DB)
	now := time.Now()

	err := q.UpdateUserRole(ctx, dbgen.UpdateUserRoleParams{
		ID:         userID,
		Role:       "approved",
		ApprovedAt: &now,
		ApprovedBy: &adminUser.ID,
	})
	if err != nil {
		slog.Error("failed to approve user", "user_id", userID, "error", err)
		http.Redirect(w, r, "/admin?error=Failed+to+approve+user", http.StatusSeeOther)
		return
	}

	slog.Info("user approved", "user_id", userID, "approved_by", adminUser.Email)
	http.Redirect(w, r, "/admin?success=User+approved+successfully", http.StatusSeeOther)
}

// HandleRejectUser handles POST requests to reject/delete a pending user.
func (s *Server) HandleRejectUser(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	userID := r.FormValue("user_id")

	if userID == "" {
		http.Redirect(w, r, "/admin?error=User+ID+required", http.StatusSeeOther)
		return
	}

	// Get the admin user for logging
	adminUser := s.Auth.GetUserFromRequest(r)
	if adminUser == nil {
		http.Redirect(w, r, "/login", http.StatusSeeOther)
		return
	}

	q := dbgen.New(s.DB)

	// First, delete user's sessions
	if err := q.DeleteUserSessions(ctx, userID); err != nil {
		slog.Error("failed to delete user sessions", "user_id", userID, "error", err)
		// Continue anyway
	}

	// Delete the user (using raw SQL since no generated query exists)
	_, err := s.DB.ExecContext(ctx, "DELETE FROM users WHERE id = ?", userID)
	if err != nil {
		slog.Error("failed to delete user", "user_id", userID, "error", err)
		http.Redirect(w, r, "/admin?error=Failed+to+reject+user", http.StatusSeeOther)
		return
	}

	slog.Info("user rejected", "user_id", userID, "rejected_by", adminUser.Email)
	http.Redirect(w, r, "/admin?success=User+rejected+and+removed", http.StatusSeeOther)
}

// HandleUploadFire handles VIIRS fire data CSV uploads.
func (s *Server) HandleUploadFire(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	_ = ctx // for future use

	// Limit request body size (500MB for fire data)
	r.Body = http.MaxBytesReader(w, r.Body, 500<<20)

	if err := r.ParseMultipartForm(500 << 20); err != nil {
		http.Redirect(w, r, "/admin?error=Failed+to+parse+form:+"+err.Error(), http.StatusSeeOther)
		return
	}
	defer r.MultipartForm.RemoveAll()

	file, header, err := r.FormFile("fire_csv")
	if err != nil {
		http.Redirect(w, r, "/admin?error=No+file+provided", http.StatusSeeOther)
		return
	}
	defer file.Close()

	// Validate filename
	if !strings.HasSuffix(strings.ToLower(header.Filename), ".csv") {
		http.Redirect(w, r, "/admin?error=File+must+be+a+CSV", http.StatusSeeOther)
		return
	}

	// Create temp file
	tmpDir := filepath.Join(os.TempDir(), "5mpglobe-uploads")
	os.MkdirAll(tmpDir, 0755)
	tmpFile, err := os.CreateTemp(tmpDir, "fire-*.csv")
	if err != nil {
		slog.Error("failed to create temp file", "error", err)
		http.Redirect(w, r, "/admin?error=Failed+to+create+temp+file", http.StatusSeeOther)
		return
	}

	// Copy uploaded file to temp
	written, err := io.Copy(tmpFile, file)
	tmpFile.Close()
	if err != nil {
		os.Remove(tmpFile.Name())
		http.Redirect(w, r, "/admin?error=Failed+to+save+file", http.StatusSeeOther)
		return
	}

	slog.Info("fire data uploaded", "filename", header.Filename, "size", written, "temp", tmpFile.Name())

	// Start background processing with Python script
	go func() {
		setProcessingStatus(fmt.Sprintf("Processing fire data: %s (%d bytes)", header.Filename, written))
		defer func() {
			os.Remove(tmpFile.Name())
			setProcessingStatus("")
		}()

		// Run the streaming fire processor
		cmd := exec.Command(".venv/bin/python", "scripts/fire_processor_streaming.py", "--zip", tmpFile.Name())
		output, err := cmd.CombinedOutput()
		if err != nil {
			slog.Error("fire processing failed", "error", err, "output", string(output))
			setProcessingStatus("Fire processing failed: " + err.Error())
		} else {
			slog.Info("fire data processing complete", "filename", header.Filename, "output", string(output))
			setProcessingStatus("Fire data processed: " + header.Filename)
		}
		time.Sleep(5 * time.Second)
	}()

	http.Redirect(w, r, "/admin?success=Fire+data+uploaded.+Processing+in+background.", http.StatusSeeOther)
}

// HandleUploadGHSL handles GHSL tile ZIP uploads.
func (s *Server) HandleUploadGHSL(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	_ = ctx // for future use

	// Limit request body size (2GB for GHSL tiles)
	r.Body = http.MaxBytesReader(w, r.Body, 2<<30)

	if err := r.ParseMultipartForm(2 << 30); err != nil {
		http.Redirect(w, r, "/admin?error=Failed+to+parse+form:+"+err.Error(), http.StatusSeeOther)
		return
	}
	defer r.MultipartForm.RemoveAll()

	file, header, err := r.FormFile("ghsl_zip")
	if err != nil {
		http.Redirect(w, r, "/admin?error=No+file+provided", http.StatusSeeOther)
		return
	}
	defer file.Close()

	// Validate filename
	if !strings.HasSuffix(strings.ToLower(header.Filename), ".zip") {
		http.Redirect(w, r, "/admin?error=File+must+be+a+ZIP", http.StatusSeeOther)
		return
	}

	// Read file into memory for zip extraction
	data, err := io.ReadAll(file)
	if err != nil {
		http.Redirect(w, r, "/admin?error=Failed+to+read+file", http.StatusSeeOther)
		return
	}

	// Open as zip
	zipReader, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		http.Redirect(w, r, "/admin?error=Failed+to+open+ZIP:+"+err.Error(), http.StatusSeeOther)
		return
	}

	// Find TIF file in zip
	var tifFile *zip.File
	for _, f := range zipReader.File {
		if strings.HasSuffix(strings.ToLower(f.Name), ".tif") {
			tifFile = f
			break
		}
	}

	if tifFile == nil {
		http.Redirect(w, r, "/admin?error=No+TIF+file+found+in+ZIP", http.StatusSeeOther)
		return
	}

	// Extract tile ID from filename (e.g., GHS_BUILT_S_E2018_GLOBE_R2023A_54009_10_V1_0_R5_C18.tif)
	tileID := extractGHSLTileID(tifFile.Name)
	if tileID == "" {
		http.Redirect(w, r, "/admin?error=Could+not+determine+tile+ID+from+filename", http.StatusSeeOther)
		return
	}

	// Create destination directory
	ghslDir := filepath.Join("data", "ghsl", "GHS_BUILT_S_E2018_GLOBE_R2023A_54009_10_V1_0_"+tileID)
	if err := os.MkdirAll(ghslDir, 0700); err != nil {
		http.Redirect(w, r, "/admin?error=Failed+to+create+directory", http.StatusSeeOther)
		return
	}

	// Extract TIF file
	tifReader, err := tifFile.Open()
	if err != nil {
		http.Redirect(w, r, "/admin?error=Failed+to+open+TIF+in+ZIP", http.StatusSeeOther)
		return
	}
	defer tifReader.Close()

	destPath := filepath.Join(ghslDir, filepath.Base(tifFile.Name))
	destFile, err := os.Create(destPath)
	if err != nil {
		http.Redirect(w, r, "/admin?error=Failed+to+create+destination+file", http.StatusSeeOther)
		return
	}

	written, err := io.Copy(destFile, tifReader)
	destFile.Close()
	if err != nil {
		http.Redirect(w, r, "/admin?error=Failed+to+extract+TIF", http.StatusSeeOther)
		return
	}

	slog.Info("GHSL tile extracted", "tile", tileID, "path", destPath, "size", written)

	// Start background processing with Python script
	go func() {
		setProcessingStatus(fmt.Sprintf("Processing GHSL tile: %s", tileID))
		defer func() {
			// Clean up extracted TIF after processing to save disk space
			os.RemoveAll(ghslDir)
			setProcessingStatus("")
		}()

		// Run the streaming GHSL processor
		cmd := exec.Command(".venv/bin/python", "scripts/ghsl_processor_streaming.py", "--zip", destPath, "--keep")
		output, err := cmd.CombinedOutput()
		if err != nil {
			slog.Error("GHSL processing failed", "error", err, "output", string(output))
			setProcessingStatus("GHSL processing failed: " + err.Error())
		} else {
			slog.Info("GHSL tile processing complete", "tile", tileID, "output", string(output))
			setProcessingStatus("GHSL tile processed: " + tileID)
		}
		time.Sleep(5 * time.Second)
	}()

	http.Redirect(w, r, fmt.Sprintf("/admin?success=GHSL+tile+%s+uploaded.+Processing+in+background.", tileID), http.StatusSeeOther)
}

// extractGHSLTileID extracts the tile ID (e.g., R5_C18) from a GHSL filename.
func extractGHSLTileID(filename string) string {
	// Look for pattern R{num}_C{num}
	base := filepath.Base(filename)
	parts := strings.Split(base, "_")
	for i, part := range parts {
		if len(part) > 1 && part[0] == 'R' && i+1 < len(parts) && len(parts[i+1]) > 1 && parts[i+1][0] == 'C' {
			// Found R*_C* pattern - but need to handle the .tif suffix
			colPart := parts[i+1]
			colPart = strings.TrimSuffix(colPart, ".tif")
			colPart = strings.TrimSuffix(colPart, ".TIF")
			return part + "_" + colPart
		}
	}
	return ""
}

// HandleProcessingStatus returns the current processing status as JSON.
func (s *Server) HandleProcessingStatus(w http.ResponseWriter, r *http.Request) {
	status := getProcessingStatus()
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"status":%q}`, status)
}
