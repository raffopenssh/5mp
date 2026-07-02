package srv

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

// HandleAPILearnedFeaturesKML exports learned features as a single KML file.
// GET /api/admin/learned-features-kml?scope=pending            — all pending features (Pending Approvals sheet)
// GET /api/admin/learned-features-kml?park_id=XXX              — all learned features for a park (Learned Features sheet)
func (s *Server) HandleAPILearnedFeaturesKML(w http.ResponseWriter, r *http.Request) {
	scope := r.URL.Query().Get("scope")
	parkID := r.URL.Query().Get("park_id")

	var where string
	var args []interface{}
	var docName, filename string

	switch {
	case scope == "pending":
		where = "status = 'pending'"
		docName = "5MP Learned Features — Pending Approvals"
		filename = fmt.Sprintf("5mp_pending_features_%s.kml", time.Now().Format("20060102"))
	case parkID != "":
		where = "park_id = ? AND status IN ('pending','approved','auto_approved')"
		args = append(args, parkID)
		docName = "5MP Learned Features — " + parkID
		filename = fmt.Sprintf("5mp_learned_features_%s_%s.kml", parkID, time.Now().Format("20060102"))
	default:
		where = "status IN ('pending','approved','auto_approved')"
		docName = "5MP Learned Features — All Parks"
		filename = fmt.Sprintf("5mp_learned_features_%s.kml", time.Now().Format("20060102"))
	}

	var sb strings.Builder
	sb.WriteString(`<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<name>` + kmlEscape(docName) + `</name>
<Style id="road"><LineStyle><color>ff00a5ff</color><width>3</width></LineStyle></Style>
<Style id="place"><IconStyle><color>ff4ee0a3</color><scale>0.9</scale></IconStyle></Style>
<Style id="airstrip"><IconStyle><color>fffa7a3b</color><scale>1.1</scale></IconStyle></Style>
`)

	// Roads
	rows, err := s.DB.Query(`SELECT id, park_id, geojson, COALESCE(length_m,0), COALESCE(match_count,0), COALESCE(confidence_pct,0), COALESCE(status,'pending') FROM learned_roads WHERE `+where, args...)
	if err == nil {
		sb.WriteString("<Folder><name>Roads</name>\n")
		for rows.Next() {
			var id, matchCount int64
			var pid, geojson, status string
			var lengthM, conf float64
			if rows.Scan(&id, &pid, &geojson, &lengthM, &matchCount, &conf, &status) != nil {
				continue
			}
			geom := geojsonToKMLGeometry(geojson)
			if geom == "" {
				continue
			}
			fmt.Fprintf(&sb, "<Placemark><name>Road %d (%s)</name><description>%s</description><styleUrl>#road</styleUrl>%s</Placemark>\n",
				id, kmlEscape(pid),
				kmlEscape(fmt.Sprintf("%.1f km, %d traversals, %.0f%% confidence, %s", lengthM/1000, matchCount, conf, status)),
				geom)
		}
		rows.Close()
		sb.WriteString("</Folder>\n")
	}

	// Places
	rows, err = s.DB.Query(`SELECT id, park_id, lat, lon, COALESCE(place_type,'unknown'), COALESCE(visit_count,0), COALESCE(confidence_pct,0), COALESCE(status,'pending') FROM learned_places WHERE `+where, args...)
	if err == nil {
		sb.WriteString("<Folder><name>Places</name>\n")
		for rows.Next() {
			var id, visits int64
			var pid, ptype, status string
			var lat, lon, conf float64
			if rows.Scan(&id, &pid, &lat, &lon, &ptype, &visits, &conf, &status) != nil {
				continue
			}
			fmt.Fprintf(&sb, "<Placemark><name>%s %d (%s)</name><description>%s</description><styleUrl>#place</styleUrl><Point><coordinates>%f,%f,0</coordinates></Point></Placemark>\n",
				kmlEscape(ptype), id, kmlEscape(pid),
				kmlEscape(fmt.Sprintf("%d visits, %.0f%% confidence, %s", visits, conf, status)),
				lon, lat)
		}
		rows.Close()
		sb.WriteString("</Folder>\n")
	}

	// Airstrips
	rows, err = s.DB.Query(`SELECT id, park_id, lat, lon, heading_deg, COALESCE(length_m,0), COALESCE(aircraft_type,'mixed'), COALESCE(landing_count,0), COALESCE(takeoff_count,0), COALESCE(confidence_pct,0), COALESCE(status,'pending') FROM learned_airstrips WHERE `+where, args...)
	if err == nil {
		sb.WriteString("<Folder><name>Airstrips</name>\n")
		for rows.Next() {
			var id, landings, takeoffs int64
			var pid, atype, status string
			var lat, lon, lengthM, conf float64
			var heading sql.NullFloat64
			if rows.Scan(&id, &pid, &lat, &lon, &heading, &lengthM, &atype, &landings, &takeoffs, &conf, &status) != nil {
				continue
			}
			hdg := "n/a"
			if heading.Valid {
				hdg = fmt.Sprintf("%.0f°", heading.Float64)
			}
			fmt.Fprintf(&sb, "<Placemark><name>Airstrip %d: %s (%s)</name><description>%s</description><styleUrl>#airstrip</styleUrl><Point><coordinates>%f,%f,0</coordinates></Point></Placemark>\n",
				id, kmlEscape(atype), kmlEscape(pid),
				kmlEscape(fmt.Sprintf("%d landings, %d takeoffs, heading %s, length %.0f m, %.0f%% confidence, %s", landings, takeoffs, hdg, lengthM, conf, status)),
				lon, lat)
		}
		rows.Close()
		sb.WriteString("</Folder>\n")
	}

	sb.WriteString("</Document>\n</kml>\n")

	w.Header().Set("Content-Type", "application/vnd.google-earth.kml+xml")
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%q", filename))
	w.Write([]byte(sb.String()))
}

func kmlEscape(s string) string {
	r := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;")
	return r.Replace(s)
}

// geojsonToKMLGeometry converts a GeoJSON LineString/Point geometry to a KML geometry fragment.
func geojsonToKMLGeometry(geojsonStr string) string {
	var geom struct {
		Type        string          `json:"type"`
		Coordinates json.RawMessage `json:"coordinates"`
	}
	if err := json.Unmarshal([]byte(geojsonStr), &geom); err != nil {
		return ""
	}
	switch geom.Type {
	case "LineString":
		var coords [][]float64
		if err := json.Unmarshal(geom.Coordinates, &coords); err != nil || len(coords) < 2 {
			return ""
		}
		var sb strings.Builder
		sb.WriteString("<LineString><tessellate>1</tessellate><coordinates>")
		for i, c := range coords {
			if len(c) < 2 {
				continue
			}
			if i > 0 {
				sb.WriteString(" ")
			}
			fmt.Fprintf(&sb, "%f,%f,0", c[0], c[1])
		}
		sb.WriteString("</coordinates></LineString>")
		return sb.String()
	case "Point":
		var c []float64
		if err := json.Unmarshal(geom.Coordinates, &c); err != nil || len(c) < 2 {
			return ""
		}
		return fmt.Sprintf("<Point><coordinates>%f,%f,0</coordinates></Point>", c[0], c[1])
	}
	return ""
}

// learnedCoverageForPark returns the data coverage backing learned features for a park:
// number of GPS points, uploads, and the time range of underlying track data.
func (s *Server) learnedCoverageForPark(parkID string) map[string]interface{} {
	var uploads, points sql.NullInt64
	var minT, maxT sql.NullString
	err := s.DB.QueryRow(`
		SELECT COUNT(DISTINCT l.id), COALESCE(SUM(l.total_points),0),
		       MIN(u.start_time), MAX(u.end_time)
		FROM gpx_upload_logs l
		LEFT JOIN gpx_uploads u ON u.id = l.upload_id
		WHERE l.protected_area_id = ?`, parkID).Scan(&uploads, &points, &minT, &maxT)
	if err != nil {
		return nil
	}
	cov := map[string]interface{}{
		"uploads":     uploads.Int64,
		"data_points": points.Int64,
	}
	if minT.Valid {
		cov["from"] = normalizeDBTime(minT.String)
	}
	if maxT.Valid {
		cov["to"] = normalizeDBTime(maxT.String)
	}
	return cov
}

// normalizeDBTime trims Go time.Time string suffixes like " +0000 UTC" so the
// frontend can parse the value with new Date().
func normalizeDBTime(t string) string {
	if i := strings.Index(t, " +"); i > 0 {
		t = t[:i]
	}
	return strings.TrimSuffix(t, " UTC")
}
