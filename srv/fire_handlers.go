package srv

import (
	"database/sql"
	"encoding/json"
	"log"
	"net/http"
	"os"
)

// Fire data API handlers

func (s *Server) handleFireDailyData(w http.ResponseWriter, r *http.Request) {
	data, err := os.ReadFile("data/fire/chinko_fires_by_day.json")
	if err != nil {
		http.Error(w, "Fire data not found", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(data)
}

func (s *Server) handleFireDailyGeoJSON(w http.ResponseWriter, r *http.Request) {
	data, err := os.ReadFile("data/fire/chinko_daily_geojson.json")
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

func (s *Server) handleFireAnimation(w http.ResponseWriter, r *http.Request) {
	s.renderTemplate(w, "fire_animation.html", nil)
}

// Park fire analysis API
func (s *Server) handleParkFireAnalysis(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if parkID == "" {
		http.Error(w, "Park ID required", http.StatusBadRequest)
		return
	}

	rows, err := s.DB.Query(`
		SELECT year, total_fires, dry_season_fires, transhumance_groups,
		       transhumance_fires, avg_transhumance_speed, herder_groups,
		       management_groups, village_groups, analysis_json
		FROM park_fire_analysis
		WHERE park_id = ?
		ORDER BY year DESC
	`, parkID)
	if err != nil {
		log.Printf("Query error: %v", err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type YearAnalysis struct {
		Year                 int             `json:"year"`
		TotalFires           int             `json:"total_fires"`
		DrySeasonFires       int             `json:"dry_season_fires"`
		TranshumanceGroups   int             `json:"transhumance_groups"`
		TranshumanceFires    int             `json:"transhumance_fires"`
		AvgTranshumanceSpeed float64         `json:"avg_transhumance_speed"`
		HerderGroups         int             `json:"herder_groups"`
		ManagementGroups     int             `json:"management_groups"`
		VillageGroups        int             `json:"village_groups"`
		Groups               json.RawMessage `json:"groups,omitempty"`
	}

	results := make([]YearAnalysis, 0)
	for rows.Next() {
		var ya YearAnalysis
		var analysisJSON sql.NullString
		var drySeasonFires, transhumanceFires, herderGroups, mgmtGroups, villageGroups sql.NullInt64
		var avgSpeed sql.NullFloat64
		
		err := rows.Scan(&ya.Year, &ya.TotalFires, &drySeasonFires,
			&ya.TranshumanceGroups, &transhumanceFires, &avgSpeed,
			&herderGroups, &mgmtGroups, &villageGroups, &analysisJSON)
		if err != nil {
			log.Printf("Scan error: %v", err)
			continue
		}
		
		if drySeasonFires.Valid { ya.DrySeasonFires = int(drySeasonFires.Int64) }
		if transhumanceFires.Valid { ya.TranshumanceFires = int(transhumanceFires.Int64) }
		if avgSpeed.Valid { ya.AvgTranshumanceSpeed = avgSpeed.Float64 }
		if herderGroups.Valid { ya.HerderGroups = int(herderGroups.Int64) }
		if mgmtGroups.Valid { ya.ManagementGroups = int(mgmtGroups.Int64) }
		if villageGroups.Valid { ya.VillageGroups = int(villageGroups.Int64) }
		if analysisJSON.Valid {
			ya.Groups = json.RawMessage(analysisJSON.String)
		}
		
		results = append(results, ya)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(results)
}
