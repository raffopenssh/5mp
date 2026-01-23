// Package auth provides authentication and session management.
package auth

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"errors"
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

// generateSessionID creates a random session ID.
func generateSessionID() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
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
		return "", nil, err
	}

	now := time.Now()
	err = q.CreateSession(ctx, dbgen.CreateSessionParams{
		ID:        sessionID,
		UserID:    user.ID,
		CreatedAt: now,
		ExpiresAt: now.Add(SessionDuration),
	})
	if err != nil {
		return "", nil, err
	}

	return sessionID, &User{
		ID:    user.ID,
		Email: user.Email,
		Name:  user.Name,
		Role:  user.Role,
	}, nil
}

// Logout invalidates a session.
func (m *Manager) Logout(ctx context.Context, sessionID string) error {
	q := dbgen.New(m.db)
	return q.DeleteSession(ctx, sessionID)
}

// GetUserFromSession retrieves the user for a session ID.
func (m *Manager) GetUserFromSession(ctx context.Context, sessionID string) (*User, error) {
	q := dbgen.New(m.db)

	sess, err := q.GetSession(ctx, sessionID)
	if err != nil {
		return nil, err
	}

	return &User{
		ID:    sess.UserID,
		Email: sess.Email,
		Name:  sess.Name,
		Role:  sess.Role,
	}, nil
}

// GetUserFromRequest extracts the user from request cookies.
func (m *Manager) GetUserFromRequest(r *http.Request) *User {
	cookie, err := r.Cookie(SessionCookieName)
	if err != nil {
		return nil
	}

	user, err := m.GetUserFromSession(r.Context(), cookie.Value)
	if err != nil {
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
func (m *Manager) CleanupExpiredSessions(ctx context.Context) error {
	q := dbgen.New(m.db)
	return q.DeleteExpiredSessions(ctx)
}
