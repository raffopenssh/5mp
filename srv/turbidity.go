package srv

// River turbidity + GFW alert evidence for mining detection.
//
// analysis/river_turbidity.py scans Sentinel-2 red reflectance along OSM
// waterways per park and writes "turbidity onset" alerts to
// data/turbidity/{park_id}.json. Point-source turbidity onsets on
// otherwise-clean rivers are the strongest available signal for artisanal
// alluvial gold mining in savanna parks (confirmed: Chinko headwaters mine at
// 7.446N 24.030E — invisible to GFW deforestation alerts and VIIRS fires).
//
// analysis/gfw_alerts.py writes 0.01-deg GFW integrated-alert clusters to
// data/gfw_alerts/{park_id}.json (100km buffered park bbox).
//
// This file loads both as classification evidence (scoreMining) and creates
// notifications + mining settlement candidates from turbidity alerts.

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"sync"
	"time"
)

// TurbidityAlert is one onset point from analysis/river_turbidity.py.
type TurbidityAlert struct {
	Lat                 float64 `json:"lat"`
	Lon                 float64 `json:"lon"`
	River               string  `json:"river"`
	Waterway            string  `json:"waterway"`
	Type                string  `json:"type"` // turbidity_onset | turbid_headwater
	Red                 int     `json:"red"`
	Ratio               float64 `json:"ratio"`
	DownstreamTurbidKm  float64 `json:"downstream_turbid_km"`
	Scene               string  `json:"scene"`
	Date                string  `json:"date"`
}

type turbidityFile struct {
	ParkID    string           `json:"park_id"`
	ScannedAt string           `json:"scanned_at"`
	Alerts    []TurbidityAlert `json:"alerts"`
}

// GFWCluster is one 0.01-deg alert cell from analysis/gfw_alerts.py.
type GFWCluster struct {
	Lat      float64 `json:"lat"`
	Lon      float64 `json:"lon"`
	N        int     `json:"n"`
	First    string  `json:"first"`
	Last     string  `json:"last"`
	HighConf int     `json:"high_conf"`
}

type gfwFile struct {
	ParkID   string       `json:"park_id"`
	Clusters []GFWCluster `json:"clusters"`
}

var (
	turbidityCache   = map[string][]TurbidityAlert{}
	gfwClusterCache  = map[string][]GFWCluster{}
	pitSiteCache     = map[string][]pitSite{}
	turbidityCacheMu sync.Mutex
)

func loadPitSites(parkID string) []pitSite {
	turbidityCacheMu.Lock()
	defer turbidityCacheMu.Unlock()
	if p, ok := pitSiteCache[parkID]; ok {
		return p
	}
	var f struct {
		Sites []pitSite `json:"sites"`
	}
	if data, err := os.ReadFile(fmt.Sprintf("data/mining_pits/%s.json", parkID)); err == nil {
		json.Unmarshal(data, &f)
	}
	pitSiteCache[parkID] = f.Sites
	return f.Sites
}

// nearestPitSiteKm returns distance (km) to the closest scored pit detection.
func nearestPitSiteKm(parkID string, lat, lon float64) (float64, *pitSite) {
	best := 1e9
	var bestP *pitSite
	sites := loadPitSites(parkID)
	for i := range sites {
		if sites[i].Score < 0.6 {
			continue
		}
		d := haversineDistance(lat, lon, sites[i].Lat, sites[i].Lon)
		if d < best {
			best = d
			bestP = &sites[i]
		}
	}
	return best, bestP
}

func loadTurbidityAlerts(parkID string) []TurbidityAlert {
	turbidityCacheMu.Lock()
	defer turbidityCacheMu.Unlock()
	if a, ok := turbidityCache[parkID]; ok {
		return a
	}
	var f turbidityFile
	data, err := os.ReadFile(fmt.Sprintf("data/turbidity/%s.json", parkID))
	if err == nil {
		json.Unmarshal(data, &f)
	}
	turbidityCache[parkID] = f.Alerts
	return f.Alerts
}

func loadGFWClusters(parkID string) []GFWCluster {
	turbidityCacheMu.Lock()
	defer turbidityCacheMu.Unlock()
	if c, ok := gfwClusterCache[parkID]; ok {
		return c
	}
	var f gfwFile
	data, err := os.ReadFile(fmt.Sprintf("data/gfw_alerts/%s.json", parkID))
	if err == nil {
		json.Unmarshal(data, &f)
	}
	gfwClusterCache[parkID] = f.Clusters
	return f.Clusters
}

// nearestTurbidityAlertKm returns distance (km) to the closest turbidity onset
// and that alert, or (1e9, nil) when none.
func nearestTurbidityAlertKm(parkID string, lat, lon float64) (float64, *TurbidityAlert) {
	best := 1e9
	var bestA *TurbidityAlert
	alerts := loadTurbidityAlerts(parkID)
	for i := range alerts {
		d := haversineDistance(lat, lon, alerts[i].Lat, alerts[i].Lon)
		if d < best {
			best = d
			bestA = &alerts[i]
		}
	}
	return best, bestA
}

// gfwAlertsNearby sums GFW integrated alerts within radiusKm.
func gfwAlertsNearby(parkID string, lat, lon, radiusKm float64) int {
	n := 0
	for _, c := range loadGFWClusters(parkID) {
		if haversineDistance(lat, lon, c.Lat, c.Lon) <= radiusKm {
			n += c.N
		}
	}
	return n
}

// SyncTurbidityAlerts scans data/turbidity/*.json, creates one notification
// per new alert (deduped on reference_id) and registers a mining settlement
// candidate for each alert with a significant downstream plume.
func (s *Server) SyncTurbidityAlerts() {
	entries, err := os.ReadDir("data/turbidity")
	if err != nil {
		return
	}
	for _, e := range entries {
		if e.Name() == "state.json" || len(e.Name()) < 6 || e.Name()[len(e.Name())-5:] != ".json" {
			continue
		}
		parkID := e.Name()[:len(e.Name())-5]
		// bypass cache: sync must see fresh scan output
		turbidityCacheMu.Lock()
		delete(turbidityCache, parkID)
		turbidityCacheMu.Unlock()
		for _, a := range loadTurbidityAlerts(parkID) {
			refID := fmt.Sprintf("turbidity_%s_%.3f_%.3f", parkID, a.Lat, a.Lon)
			var exists int
			s.DB.QueryRow(`SELECT COUNT(*) FROM notifications WHERE reference_id = ?`, refID).Scan(&exists)
			if exists > 0 {
				continue
			}
			kind := "turbidity onset"
			if a.Type == "turbid_headwater" {
				kind = "turbid headwaters"
			}
			title := fmt.Sprintf("Possible mining: %s on %s", kind, a.River)
			msg := fmt.Sprintf(
				"Sentinel-2 shows sediment plume (%s) at %.4f°, %.4f° on the %s — "+
					"~%.0f km of river turbid downstream (scene %s, %s). "+
					"Point-source turbidity is a signature of alluvial gold mining.",
				kind, a.Lat, a.Lon, a.River, a.DownstreamTurbidKm, a.Scene, a.Date)
			refData, _ := json.Marshal(a)
			_, err := s.DB.Exec(`
				INSERT INTO notifications (park_id, notification_type, title, message, reference_id, reference_data, created_at)
				VALUES (?, 'mining_alert', ?, ?, ?, ?, CURRENT_TIMESTAMP)`,
				parkID, title, msg, refID, string(refData))
			if err != nil {
				slog.Error("turbidity notification insert failed", "error", err)
				continue
			}
			slog.Info("created mining/turbidity notification", "park", parkID,
				"river", a.River, "lat", a.Lat, "lon", a.Lon,
				"downstream_turbid_km", a.DownstreamTurbidKm)

			// Major plume (>=10km turbid downstream): register a mining
			// settlement candidate at the onset point so it appears in the
			// settlements layer and gets full classification context.
			if a.DownstreamTurbidKm >= 10 {
				note := fmt.Sprintf("[Turbidity alert %s] Suspected alluvial mining near this %s of the %s.", a.Date, a.Type, a.River)
				s.RegisterMiningCandidate(parkID, a.Lat, a.Lon, 0, note)
			}
		}
	}
	s.syncPitSites()
}

// pitSite is one bright-bare cluster from analysis/mining_pits.py.
type pitSite struct {
	Lat          float64 `json:"lat"`
	Lon          float64 `json:"lon"`
	Px           int     `json:"px"`
	AreaHa       float64 `json:"area_ha"`
	WaterKm      float64 `json:"water_km"`
	PondPx       int     `json:"pond_px"`
	Score        float64 `json:"score"`
	Scene        string  `json:"scene"`
	Date         string  `json:"date"`
	NewSince     string  `json:"new_since"`
	HistoricalPx *int    `json:"historical_px"`
}

// syncPitSites turns high-confidence pit detections (score >= 0.7) into
// mining_alert notifications and settlement candidates.
func (s *Server) syncPitSites() {
	entries, err := os.ReadDir("data/mining_pits")
	if err != nil {
		return
	}
	for _, e := range entries {
		if len(e.Name()) < 6 || e.Name()[len(e.Name())-5:] != ".json" {
			continue
		}
		parkID := e.Name()[:len(e.Name())-5]
		var f struct {
			Sites []pitSite `json:"sites"`
		}
		data, err := os.ReadFile("data/mining_pits/" + e.Name())
		if err != nil || json.Unmarshal(data, &f) != nil {
			continue
		}
		// Cap per park: notifications + auto-registration only for the
		// strongest detections (score >= 0.8, top 15 by score). The full
		// list stays visible in the Mining & Water Quality accordion.
		registered := 0
		for _, p := range f.Sites {
			if p.Score < 0.8 || registered >= 15 {
				continue
			}
			refID := fmt.Sprintf("pit_%s_%.3f_%.3f", parkID, p.Lat, p.Lon)
			var exists int
			s.DB.QueryRow(`SELECT COUNT(*) FROM notifications WHERE reference_id = ?`, refID).Scan(&exists)
			if exists > 0 {
				continue
			}
			newness := ""
			if p.NewSince != "" {
				newness = fmt.Sprintf(" Ground was vegetated on %s — clearing is new.", p.NewSince)
			}
			title := fmt.Sprintf("Possible mining pits: %.1f ha bare-earth cluster near river", p.AreaHa)
			msg := fmt.Sprintf(
				"Sentinel-2 shows a persistent bright bare-earth cluster (%.1f ha) at "+
					"%.4f°, %.4f°, %.1f km from the nearest waterway (scene %s, %s).%s "+
					"Riverside pit clusters are the visual signature of artisanal gold mining.",
				p.AreaHa, p.Lat, p.Lon, p.WaterKm, p.Scene, p.Date, newness)
			refData, _ := json.Marshal(p)
			if _, err := s.DB.Exec(`
				INSERT INTO notifications (park_id, notification_type, title, message, reference_id, reference_data, created_at)
				VALUES (?, 'mining_alert', ?, ?, ?, ?, CURRENT_TIMESTAMP)`,
				parkID, title, msg, refID, string(refData)); err != nil {
				slog.Error("pit notification insert failed", "error", err)
				continue
			}
			slog.Info("created pit mining notification", "park", parkID,
				"lat", p.Lat, "lon", p.Lon, "area_ha", p.AreaHa, "score", p.Score)
			note := fmt.Sprintf("[Pit detection %s] %.1f ha bare-earth cluster %.1f km from waterway; suspected mining pits/camp.%s",
				p.Date, p.AreaHa, p.WaterKm, newness)
			s.RegisterMiningCandidate(parkID, p.Lat, p.Lon, p.AreaHa*10000, note)
			registered++
		}
	}
}

// RegisterMiningCandidate inserts (or finds) a park_settlements row for a
// suspected mining site and classifies it. Returns the settlement id.
func (s *Server) RegisterMiningCandidate(parkID string, lat, lon, areaM2 float64, note string) (int64, error) {
	var id int64
	err := s.DB.QueryRow(`
		SELECT id FROM park_settlements
		WHERE park_id = ? AND ABS(lat - ?) < 0.01 AND ABS(lon - ?) < 0.01`,
		parkID, lat, lon).Scan(&id)
	if err == nil {
		return id, nil // already registered nearby
	}
	res, err := s.DB.Exec(`
		INSERT INTO park_settlements (park_id, lat, lon, area_m2, settlement_type, in_buffer, detected_at)
		VALUES (?, ?, ?, ?, 'temporary', 1, CURRENT_TIMESTAMP)`,
		parkID, lat, lon, areaM2)
	if err != nil {
		return 0, err
	}
	id, _ = res.LastInsertId()

	st := ClassifiedSettlement{ID: id, ParkID: parkID, Lat: lat, Lon: lon, AreaM2: areaM2}
	s.ClassifySettlement(parkID, &st)
	narrative := st.Narrative
	if note != "" {
		narrative = note + " " + narrative
	}
	s.DB.Exec(`
		UPDATE park_settlements SET
			classification = ?, classification_confidence = ?, narrative = ?,
			fires_5km = ?, fire_seasonality = ?, deforest_nearby_km2 = ?,
			classified_at = CURRENT_TIMESTAMP
		WHERE id = ?`,
		st.Classification, st.Confidence, narrative,
		st.FiresWithin5km, st.FireSeasonality, st.DeforestNearby, id)
	slog.Info("registered mining candidate", "park", parkID, "id", id,
		"class", st.Classification, "confidence", st.Confidence)
	return id, nil
}

// StartTurbidityWatcher periodically syncs turbidity scan output into
// notifications (python cron writes the JSON; we surface it).
func (s *Server) StartTurbidityWatcher() {
	go func() {
		time.Sleep(30 * time.Second) // let server settle
		for {
			s.SyncTurbidityAlerts()
			time.Sleep(6 * time.Hour)
		}
	}()
}

// HandleAPIParkTurbidity serves the mining/water-quality evidence bundle for
// a park: raw turbidity scan output (alerts + per-river coverage — shown
// separately in the UI so users can judge plausibility), mining-classified
// settlements, and a GFW corroboration summary.
func (s *Server) HandleAPIParkTurbidity(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")

	// raw scan file (includes rivers[] coverage which loadTurbidityAlerts drops)
	var scan map[string]any
	if data, err := os.ReadFile(fmt.Sprintf("data/turbidity/%s.json", parkID)); err == nil {
		json.Unmarshal(data, &scan)
	}

	type miningSite struct {
		ID             int64   `json:"id"`
		Lat            float64 `json:"lat"`
		Lon            float64 `json:"lon"`
		Classification string  `json:"classification"`
		Confidence     float64 `json:"confidence"`
		Narrative      string  `json:"narrative"`
	}
	sites := []miningSite{}
	rows, err := s.DB.Query(`
		SELECT id, lat, lon, classification, COALESCE(classification_confidence,0),
		       COALESCE(narrative,'')
		FROM park_settlements
		WHERE park_id = ? AND classification = 'mining'
		ORDER BY classification_confidence DESC LIMIT 100`, parkID)
	if err == nil {
		defer rows.Close()
		for rows.Next() {
			var m miningSite
			if rows.Scan(&m.ID, &m.Lat, &m.Lon, &m.Classification,
				&m.Confidence, &m.Narrative) == nil {
				sites = append(sites, m)
			}
		}
	}

	gfw := loadGFWClusters(parkID)
	gfwTotal := 0
	for _, c := range gfw {
		gfwTotal += c.N
	}

	// pit-detection scan output (analysis/mining_pits.py): bright-bare
	// clusters along river corridors, verified for persistence + newness.
	var pits map[string]any
	if data, err := os.ReadFile(fmt.Sprintf("data/mining_pits/%s.json", parkID)); err == nil {
		json.Unmarshal(data, &pits)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"park_id":            parkID,
		"scan":               scan, // null when park not yet scanned
		"pits":               pits, // null when pit scan not yet run
		"mining_settlements": sites,
		"gfw_clusters":       len(gfw),
		"gfw_total_alerts":   gfwTotal,
	})
}
