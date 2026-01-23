package srv

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestServerSetupAndHandlers(t *testing.T) {
	tempDB := filepath.Join(t.TempDir(), "test_server.sqlite3")
	t.Cleanup(func() { os.Remove(tempDB) })

	server, err := New(tempDB, "test-hostname")
	if err != nil {
		t.Fatalf("failed to create server: %v", err)
	}

	// Test root endpoint without auth
	t.Run("root endpoint unauthenticated", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		w := httptest.NewRecorder()

		server.HandleRoot(w, req)

		if w.Code != http.StatusOK {
			t.Errorf("expected status 200, got %d", w.Code)
		}

		body := w.Body.String()
		if !strings.Contains(body, "Conservation Patrol Tracker") {
			t.Errorf("expected page to contain headline, got body: %s", body)
		}
		if !strings.Contains(body, "Sign In") {
			t.Errorf("expected page to show sign in link, got body: %s", body)
		}
	})

	// Test login page renders
	t.Run("login page renders", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/login", nil)
		w := httptest.NewRecorder()

		server.HandleLoginPage(w, req)

		if w.Code != http.StatusOK {
			t.Errorf("expected status 200, got %d", w.Code)
		}

		body := w.Body.String()
		if !strings.Contains(body, "Sign In") {
			t.Errorf("expected login page, got body: %s", body)
		}
	})

	// Test register page renders
	t.Run("register page renders", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/register", nil)
		w := httptest.NewRecorder()

		server.HandleRegisterPage(w, req)

		if w.Code != http.StatusOK {
			t.Errorf("expected status 200, got %d", w.Code)
		}

		body := w.Body.String()
		if !strings.Contains(body, "Create Account") {
			t.Errorf("expected register page, got body: %s", body)
		}
	})

	// Test upload page requires auth
	t.Run("upload page redirects without auth", func(t *testing.T) {
		req := httptest.NewRequest(http.MethodGet, "/upload", nil)
		w := httptest.NewRecorder()

		server.RequireAuth(server.HandleUploadPage)(w, req)

		if w.Code != http.StatusSeeOther {
			t.Errorf("expected redirect (303), got %d", w.Code)
		}
		if loc := w.Header().Get("Location"); loc != "/login" {
			t.Errorf("expected redirect to /login, got %s", loc)
		}
	})
}
