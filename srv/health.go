package srv

import (
	"encoding/json"
	"net/http"
)

// HandleHealthz returns server health status.
func (s *Server) HandleHealthz(w http.ResponseWriter, r *http.Request) {
	status := "ok"
	httpCode := http.StatusOK

	// Check database connectivity
	if err := s.DB.Ping(); err != nil {
		status = "db_error"
		httpCode = http.StatusServiceUnavailable
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(httpCode)
	json.NewEncoder(w).Encode(map[string]string{
		"status":  status,
		"version": Version,
	})
}
