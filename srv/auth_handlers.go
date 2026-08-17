package srv

import (
	"html/template"
	"log/slog"
	"net/http"
	"strings"

	"srv.exe.dev/srv/auth"
)

type loginPageData struct {
	Hostname string
	Error    string
	Email    string
}

type registerPageData struct {
	Hostname string
	Error    string
	Success  bool
	Email    string
	Name     string
	Org      string
	OrgType  string
}

// HandleLoginPage renders the login form.
func (s *Server) HandleLoginPage(w http.ResponseWriter, r *http.Request) {
	// If already logged in, redirect to upload
	if user := s.Auth.GetUserFromRequest(r); user != nil {
		http.Redirect(w, r, "/upload", http.StatusSeeOther)
		return
	}

	data := loginPageData{Hostname: s.Hostname}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := s.renderTemplate(w, "login.html", data); err != nil {
		slog.Warn("render login template", "error", err)
	}
}

// HandleLogin processes login form submission.
func (s *Server) HandleLogin(w http.ResponseWriter, r *http.Request) {
	email := strings.TrimSpace(r.FormValue("email"))
	password := r.FormValue("password")

	sessionID, _, err := s.Auth.Login(r.Context(), email, password)
	if err != nil {
		data := loginPageData{
			Hostname: s.Hostname,
			Email:    email,
			Error:    err.Error(),
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(http.StatusUnauthorized)
		s.renderTemplate(w, "login.html", data)
		return
	}

	auth.SetSessionCookie(w, sessionID)
	http.Redirect(w, r, "/upload", http.StatusSeeOther)
}

// HandleLogout clears the session and redirects to login.
//
// A GUEST logging out is a different ending. It holds no password, so the
// password form is not "sign in again", it is a wall with no door; and the
// slug it arrived on lives in an HttpOnly cookie we are about to delete, so
// there is no link left on screen to reopen. It gets a page that says which
// of those two things happened. The key itself is NOT revoked (see
// ClearGuestCookie): closing a view on a borrowed laptop must not destroy the
// link for everyone else it was sent to.
func (s *Server) HandleLogout(w http.ResponseWriter, r *http.Request) {
	wasGuest := GuestFromRequest(r) != nil
	if cookie, err := r.Cookie(auth.SessionCookieName); err == nil {
		if err := s.Auth.Logout(r.Context(), cookie.Value); err != nil {
			// Session deletion failed, but we still clear the cookie
			// The error is already logged by Auth.Logout
			slog.Warn("logout session deletion failed, continuing with cookie clear")
		}
	}
	auth.ClearSessionCookie(w)
	// Alpha: also clear the shared access-password cookie so the session
	// chip logout works. Redirect to root, which shows the password form.
	ClearAccessPwdCookie(w)
	// A shared view is a session too: leaving it must leave nothing behind.
	ClearGuestCookie(w)
	if wasGuest {
		s.noticePage(w, http.StatusOK, "You have left the shared view",
			"The link you were sent still works &mdash; open it again to come back. "+
				"This browser no longer holds it.", "")
		return
	}
	http.Redirect(w, r, "/", http.StatusSeeOther)
}

// HandleRegisterPage renders the registration form.
func (s *Server) HandleRegisterPage(w http.ResponseWriter, r *http.Request) {
	data := registerPageData{Hostname: s.Hostname}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := s.renderTemplate(w, "register.html", data); err != nil {
		slog.Warn("render register template", "error", err)
	}
}

// HandleRegister processes registration form submission.
func (s *Server) HandleRegister(w http.ResponseWriter, r *http.Request) {
	email := strings.TrimSpace(r.FormValue("email"))
	password := r.FormValue("password")
	passwordConfirm := r.FormValue("password_confirm")
	name := strings.TrimSpace(r.FormValue("name"))
	org := strings.TrimSpace(r.FormValue("organization"))
	orgType := r.FormValue("organization_type")

	data := registerPageData{
		Hostname: s.Hostname,
		Email:    email,
		Name:     name,
		Org:      org,
		OrgType:  orgType,
	}

	// Validation
	if email == "" || password == "" || name == "" || org == "" {
		data.Error = "All fields are required"
		w.WriteHeader(http.StatusBadRequest)
		s.renderTemplate(w, "register.html", data)
		return
	}

	if len(password) < 8 {
		data.Error = "Password must be at least 8 characters"
		w.WriteHeader(http.StatusBadRequest)
		s.renderTemplate(w, "register.html", data)
		return
	}

	if password != passwordConfirm {
		data.Error = "Passwords do not match"
		w.WriteHeader(http.StatusBadRequest)
		s.renderTemplate(w, "register.html", data)
		return
	}

	err := s.Auth.Register(r.Context(), email, password, name, org, orgType)
	if err != nil {
		if err == auth.ErrUserExists {
			data.Error = "An account with this email already exists"
		} else {
			data.Error = "Registration failed: " + err.Error()
		}
		w.WriteHeader(http.StatusBadRequest)
		s.renderTemplate(w, "register.html", data)
		return
	}

	// Success
	data.Success = true
	data.Error = ""
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	s.renderTemplate(w, "register.html", data)
}

// RequireAuth is middleware that requires authentication.
func (s *Server) RequireAuth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		user := s.Auth.GetUserFromRequest(r)
		if user == nil {
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}
		next(w, r)
	}
}

// RequireAdmin is middleware that requires admin role.
// Alpha: accept any valid app password instead of
// requiring a full admin session, so all users can approve learnings,
// delete entries, etc.
func (s *Server) RequireAdmin(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// Alpha: allow access with valid password cookie or query param
		if cookie, err := r.Cookie("access_pwd"); err == nil && isValidPassword(cookie.Value) {
			next(w, r)
			return
		}
		if isValidPassword(r.URL.Query().Get("pwd")) {
			next(w, r)
			return
		}
		// Fall back to session-based admin check
		user := s.Auth.GetUserFromRequest(r)
		if user == nil {
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}
		if user.Role != "admin" {
			http.Error(w, "Admin access required", http.StatusForbidden)
			return
		}
		next(w, r)
	}
}

// RequireAdminOrLocal is middleware that requires admin role OR localhost origin.
// This allows cron jobs running on the same machine to call admin endpoints.
func (s *Server) RequireAdminOrLocal(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// Allow requests from localhost (for cron jobs)
		host := r.RemoteAddr
		if strings.HasPrefix(host, "127.0.0.1:") || strings.HasPrefix(host, "[::1]:") {
			next(w, r)
			return
		}
		// Otherwise require admin session
		user := s.Auth.GetUserFromRequest(r)
		if user == nil {
			http.Error(w, "Admin access required", http.StatusForbidden)
			return
		}
		if user.Role != "admin" {
			http.Error(w, "Admin access required", http.StatusForbidden)
			return
		}
		next(w, r)
	}
}

// renderTemplate with funcmap for templates
func (s *Server) renderTemplateWithFuncs(w http.ResponseWriter, name string, data any) error {
	tmpl := template.New(name).Funcs(template.FuncMap{
		"eq": func(a, b string) bool { return a == b },
	})
	tmpl, err := tmpl.ParseFiles(s.TemplatesDir + "/" + name)
	if err != nil {
		return err
	}
	return tmpl.Execute(w, data)
}
