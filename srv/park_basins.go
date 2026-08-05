package srv

import (
	"encoding/json"
	"net/http"
)

// Park basin layer: the contributing (upstream) watershed and the downstream
// river trace for each park, fetched by scripts/fetch_park_basins.py from the
// mghydro Global Watersheds API and the global-river-runner pygeoapi.
//
// Why this exists: mining pressure on a park is a *watershed* phenomenon. The
// eight field-confirmed artisanal pits in the Chinko headwaters are 123 km
// OUTSIDE CAF_Chinko but inside its contributing basin, and every scanner in
// this repo used to be park-bbox-scoped (docs/MINING_FINDINGS_2026-08.md §1).
// With this layer, "upstream mining pressure on park X" is expressible.

type basinRow struct {
	Kind      string          `json:"kind"`
	Source    string          `json:"source"`
	OutletLat float64         `json:"outlet_lat"`
	OutletLon float64         `json:"outlet_lon"`
	AreaKm2   *float64        `json:"area_km2,omitempty"`
	LengthKm  *float64        `json:"length_km,omitempty"`
	Meta      json.RawMessage `json:"meta,omitempty"`
	FetchedAt string          `json:"fetched_at"`
	Geometry  json.RawMessage `json:"geometry,omitempty"`
}

func (s *Server) loadBasins(parkID string, withGeometry bool) ([]basinRow, error) {
	rows, err := s.DB.Query(`
		SELECT kind, source, outlet_lat, outlet_lon, area_km2, length_km,
		       COALESCE(meta,''), COALESCE(fetched_at,''), geojson
		FROM park_basins WHERE park_id = ? ORDER BY kind`, parkID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []basinRow
	for rows.Next() {
		var b basinRow
		var meta, geo string
		if err := rows.Scan(&b.Kind, &b.Source, &b.OutletLat, &b.OutletLon,
			&b.AreaKm2, &b.LengthKm, &meta, &b.FetchedAt, &geo); err != nil {
			return nil, err
		}
		if meta != "" {
			b.Meta = json.RawMessage(meta)
		}
		if withGeometry {
			b.Geometry = json.RawMessage(geo)
		}
		out = append(out, b)
	}
	return out, rows.Err()
}

// HandleAPIParkBasin returns basin metadata (no geometry) for a park.
// GET /api/parks/{id}/basin
func (s *Server) HandleAPIParkBasin(w http.ResponseWriter, r *http.Request) {
	parkID := r.PathValue("id")
	if s.AreaStore != nil {
		for _, area := range s.AreaStore.Areas {
			if area.WDPAID == parkID {
				parkID = area.ID
				break
			}
		}
	}
	basins, err := s.loadBasins(parkID, r.URL.Query().Get("geometry") == "1")
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	resp := map[string]any{
		"park_id": parkID,
		"basins":  basins,
		"attribution": "Upstream watersheds: mghydro Global Watersheds (Heberger 2022, " +
			"MERIT-Hydro/MERIT-Basins, CC-BY-NC). Downstream traces: global-river-runner " +
			"(Internet of Water / USGS, MERIT-Basins).",
	}
	for _, b := range basins {
		if b.Kind == "upstream" && b.AreaKm2 != nil {
			resp["upstream_area_km2"] = *b.AreaKm2
		}
		if b.Kind == "downstream" && b.LengthKm != nil {
			resp["downstream_length_km"] = *b.LengthKm
		}
	}
	writeJSON(w, http.StatusOK, resp)
}

// handleBasinFeatures serves the basin as GeoJSON for the map pin layer.
// GET /api/parks/{id}/features?type=basin[&kind=upstream|downstream]
func (s *Server) handleBasinFeatures(w http.ResponseWriter, parkID, kind string) {
	basins, err := s.loadBasins(parkID, true)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	feats := []map[string]any{}
	for _, b := range basins {
		if kind != "" && kind != b.Kind {
			continue
		}
		props := map[string]any{
			"feature_type": "basin_" + b.Kind,
			"feature_id":   parkID + "_basin_" + b.Kind,
			"kind":         b.Kind,
			"source":       b.Source,
			"outlet_lat":   b.OutletLat,
			"outlet_lon":   b.OutletLon,
			"fetched_at":   b.FetchedAt,
		}
		if b.AreaKm2 != nil {
			props["area_km2"] = *b.AreaKm2
			props["name"] = "Contributing basin"
		}
		if b.LengthKm != nil {
			props["length_km"] = *b.LengthKm
			props["name"] = "Downstream trace"
		}
		if b.Meta != nil {
			var m map[string]any
			if json.Unmarshal(b.Meta, &m) == nil {
				if o, ok := m["outlets"]; ok {
					props["outlets"] = o
				}
				if n, ok := m["river_names"]; ok {
					props["river_names"] = n
				}
			}
		}
		feats = append(feats, map[string]any{
			"type": "Feature", "geometry": b.Geometry, "properties": props,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"type": "FeatureCollection", "features": feats})
}
