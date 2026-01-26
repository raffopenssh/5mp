package srv

import (
	"net/http"
	"os"
)

// Fire data API handlers

func (s *Server) handleFireDailyData(w http.ResponseWriter, r *http.Request) {
	// For now, serve from file - later from database
	data, err := os.ReadFile("data/fire/chinko_fires_by_day.json")
	if err != nil {
		http.Error(w, "Fire data not found", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(data)
}

func (s *Server) handleFireBoundary(w http.ResponseWriter, r *http.Request) {
	data, err := os.ReadFile("data/fire/chinko_boundary.json")
	if err != nil {
		http.Error(w, "Boundary not found", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(data)
}

func (s *Server) handleFireAnalysis(w http.ResponseWriter, r *http.Request) {
	s.renderTemplate(w, "fire_analysis.html", nil)
}
