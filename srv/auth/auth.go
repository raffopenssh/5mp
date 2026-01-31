// Package auth provides authentication and session management.
package auth

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"errors"
	"log/slog"
	"net/http"
	"time"

	"golang.org/x/crypto/bcrypt"
	"srv.exe.dev/db/dbgen"
)

const (
	SessionCookieName = "session"
	SessionDuration   = 30 * 24 * time.Hour // 30 days
	bcryptCost        = 12
)

var (
	ErrInvalidCredentials = errors.New("invalid email or password")
	ErrUserNotApproved    = errors.New("account pending approval")
	ErrUserExists         = errors.New("user already exists")
	ErrInvalidSession     = errors.New("invalid or expired session")
	ErrSessionStorage     = errors.New("session storage error")
)

// User represents an authenticated user.
type User struct {
	ID       string
	Email    string
	Name     string
	Role     string
}

// Manager handles authentication operations.
type Manager struct {
	db *sql.DB
}

// NewManager creates a new auth manager.
func NewManager(db *sql.DB) *Manager {
	return &Manager{db: db}
}

// HashPassword creates a bcrypt hash of a password.
func HashPassword(password string) (string, error) {
	hash, err := bcrypt.GenerateFromPassword([]byte(password), bcryptCost)
	if err != nil {
		return "", err
	}
	return string(hash), nil
}

// CheckPassword verifies a password against a hash.
func CheckPassword(password, hash string) bool {
	err := bcrypt.CompareHashAndPassword([]byte(hash), []byte(password))
	return err == nil
}

// SessionIDLength is the expected length of a session ID in bytes (before hex encoding).
const SessionIDLength = 32

// generateSessionID creates a random session ID.
func generateSessionID() (string, error) {
	b := make([]byte, SessionIDLength)
	if _, err := rand.Read(b); err != nil {
		slog.Error("failed to generate session ID", "error", err)
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// isValidSessionID checks if a session ID has the expected format.
// This provides early validation before hitting the database.
func isValidSessionID(sessionID string) bool {
	if len(sessionID) != SessionIDLength*2 { // hex encoding doubles the length
		return false
	}
	_, err := hex.DecodeString(sessionID)
	return err == nil
}

// generateUserID creates a random user ID.
func generateUserID() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// Register creates a new user account (pending approval).
func (m *Manager) Register(ctx context.Context, email, password, name, org, orgType string) error {
	q := dbgen.New(m.db)

	// Check if user exists
	_, err := q.GetUserByEmail(ctx, email)
	if err == nil {
		return ErrUserExists
	}

	userID, err := generateUserID()
	if err != nil {
		return err
	}

	passwordHash, err := HashPassword(password)
	if err != nil {
		return err
	}

	err = q.CreateUser(ctx, dbgen.CreateUserParams{
		ID:               userID,
		Email:            email,
		Name:             name,
		Organization:     org,
		OrganizationType: orgType,
		Role:             "pending",
		CreatedAt:        time.Now(),
	})
	if err != nil {
		return err
	}

	// Set password
	return q.UpdateUserPassword(ctx, dbgen.UpdateUserPasswordParams{
		PasswordHash: passwordHash,
		ID:           userID,
	})
}

// Login authenticates a user and creates a session.
// Returns the session ID on success.
func (m *Manager) Login(ctx context.Context, email, password string) (string, *User, error) {
	q := dbgen.New(m.db)

	user, err := q.GetUserByEmail(ctx, email)
	if err != nil {
		if err != sql.ErrNoRows {
			slog.Error("database error during login", "email", email, "error", err)
		}
		return "", nil, ErrInvalidCredentials
	}

	if !CheckPassword(password, user.PasswordHash) {
		return "", nil, ErrInvalidCredentials
	}

	if user.Role == "pending" {
		return "", nil, ErrUserNotApproved
	}

	// Create session
	sessionID, err := generateSessionID()
	if err != nil {
		slog.Error("failed to generate session ID during login", "user_id", user.ID, "error", err)
		return "", nil, ErrSessionStorage
	}

	now := time.Now()
	err = q.CreateSession(ctx, dbgen.CreateSessionParams{
		ID:        sessionID,
		UserID:    user.ID,
		CreatedAt: now,
		ExpiresAt: now.Add(SessionDuration),
	})
	if err != nil {
		slog.Error("failed to create session", "user_id", user.ID, "error", err)
		return "", nil, ErrSessionStorage
	}

	slog.Info("user logged in", "user_id", user.ID, "email", user.Email)
	return sessionID, &User{
		ID:    user.ID,
		Email: user.Email,
		Name:  user.Name,
		Role:  user.Role,
	}, nil
}

// Logout invalidates a session.
// Returns nil if the session was deleted or didn't exist.
// Returns an error only if there was a database problem.
func (m *Manager) Logout(ctx context.Context, sessionID string) error {
	if !isValidSessionID(sessionID) {
		// Invalid session ID format - nothing to delete
		return nil
	}

	q := dbgen.New(m.db)
	err := q.DeleteSession(ctx, sessionID)
	if err != nil {
		slog.Error("failed to delete session during logout", "error", err)
		return err
	}
	return nil
}

// GetUserFromSession retrieves the user for a session ID.
// Returns ErrInvalidSession if the session doesn't exist or is expired.
// Returns ErrSessionStorage if there was a database error.
func (m *Manager) GetUserFromSession(ctx context.Context, sessionID string) (*User, error) {
	// Validate session ID format before hitting the database
	if !isValidSessionID(sessionID) {
		return nil, ErrInvalidSession
	}

	q := dbgen.New(m.db)

	sess, err := q.GetSession(ctx, sessionID)
	if err != nil {
		if err == sql.ErrNoRows {
			// Session not found or expired - this is expected behavior
			return nil, ErrInvalidSession
		}
		// Unexpected database error - log it
		slog.Error("database error retrieving session", "error", err)
		return nil, ErrSessionStorage
	}

	return &User{
		ID:    sess.UserID,
		Email: sess.Email,
		Name:  sess.Name,
		Role:  sess.Role,
	}, nil
}

// GetUserFromRequest extracts the user from request cookies.
// Returns nil if no valid session is found.
// Database errors are logged but still return nil (graceful degradation).
func (m *Manager) GetUserFromRequest(r *http.Request) *User {
	cookie, err := r.Cookie(SessionCookieName)
	if err != nil {
		// No session cookie - expected for unauthenticated users
		return nil
	}

	user, err := m.GetUserFromSession(r.Context(), cookie.Value)
	if err != nil {
		// ErrInvalidSession is expected for expired/invalid sessions
		// ErrSessionStorage is already logged by GetUserFromSession
		return nil
	}

	return user
}

// SetSessionCookie sets the session cookie on the response.
func SetSessionCookie(w http.ResponseWriter, sessionID string) {
	http.SetCookie(w, &http.Cookie{
		Name:     SessionCookieName,
		Value:    sessionID,
		Path:     "/",
		HttpOnly: true,
		Secure:   true,
		SameSite: http.SameSiteLaxMode,
		MaxAge:   int(SessionDuration.Seconds()),
	})
}

// ClearSessionCookie removes the session cookie.
func ClearSessionCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{
		Name:     SessionCookieName,
		Value:    "",
		Path:     "/",
		HttpOnly: true,
		Secure:   true,
		SameSite: http.SameSiteLaxMode,
		MaxAge:   -1,
	})
}

// CleanupExpiredSessions removes expired sessions from the database.
// This should be called periodically (e.g., via a background goroutine).
func (m *Manager) CleanupExpiredSessions(ctx context.Context) error {
	q := dbgen.New(m.db)
	err := q.DeleteExpiredSessions(ctx)
	if err != nil {
		slog.Error("failed to cleanup expired sessions", "error", err)
		return err
	}
	slog.Debug("cleaned up expired sessions")
	return nil
}
