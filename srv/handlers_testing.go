package srv

import (
	"net/http"
)

// HandleTestPinning serves the UI pinning test page
func (s *Server) HandleTestPinning(w http.ResponseWriter, r *http.Request) {
	if err := s.renderTemplate(w, "test_pinning.html", nil); err != nil {
		http.Error(w, "Failed to load test page", http.StatusInternalServerError)
	}
}
