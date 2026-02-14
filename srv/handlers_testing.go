package srv

import (
	"html/template"
	"net/http"
)

// HandleTestPinning serves the UI pinning test page
func (s *Server) HandleTestPinning(w http.ResponseWriter, r *http.Request) {
	tmpl, err := template.ParseFiles("srv/templates/test_pinning.html")
	if err != nil {
		http.Error(w, "Failed to load test page", http.StatusInternalServerError)
		return
	}
	tmpl.Execute(w, nil)
}
