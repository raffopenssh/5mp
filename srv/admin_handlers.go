package srv

import (
	"log/slog"
	"net/http"
	"time"

	"srv.exe.dev/db/dbgen"
)

type adminPageData struct {
	Hostname      string
	PendingUsers  []dbgen.User
	ApprovedUsers []dbgen.User
	Stats         adminStats
	Success       string
	Error         string
}

type adminStats struct {
	TotalUsers      int
	PendingCount    int
	ApprovedCount   int
	TotalUploads    int64
	TotalDistanceKm float64
	TotalPoints     int64
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

	data := adminPageData{
		Hostname:      s.Hostname,
		PendingUsers:  pendingUsers,
		ApprovedUsers: approvedUsers,
		Stats:         stats,
		Success:       r.URL.Query().Get("success"),
		Error:         r.URL.Query().Get("error"),
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
