package srv

import (
	"encoding/json"
	"fmt"
	"math"
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
	// only set for rows from park_basin_parts (one per outlet watershed)
	Index int    `json:"idx,omitempty"`
	River string `json:"river,omitempty"`
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

// loadBasinParts returns ONE ROW PER OUTLET WATERSHED (migration 044).
//
// park_basins is keyed (park_id, kind), so it can only ever hold the *union* of
// an area's watersheds. Most areas have several genuinely separate ones
// (CAF_Chinko drains via both the Chinko and the Mbari; the XSA AOI by two
// dozen rivers), and the union throws away which outlet each lobe belongs to —
// so the map could draw one amorphous MultiPolygon and nothing else, and a lobe
// could not be attributed to the river that carries it.
//
// Empty for anything fetched before 044 existed; callers fall back to the
// merged row, which is why this is additive rather than a rewrite of the table.
func (s *Server) loadBasinParts(parkID, kind string, withGeometry bool) ([]basinRow, error) {
	q := `SELECT kind, source, outlet_lat, outlet_lon, area_km2, length_km,
	             COALESCE(river,''), COALESCE(meta,''), COALESCE(fetched_at,''),
	             geojson, idx
	      FROM park_basin_parts WHERE park_id = ?`
	args := []any{parkID}
	if kind != "" {
		q += ` AND kind = ?`
		args = append(args, kind)
	}
	q += ` ORDER BY kind, idx`
	rows, err := s.DB.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []basinRow
	for rows.Next() {
		var b basinRow
		var river, meta, geo string
		if err := rows.Scan(&b.Kind, &b.Source, &b.OutletLat, &b.OutletLon,
			&b.AreaKm2, &b.LengthKm, &river, &meta, &b.FetchedAt, &geo,
			&b.Index); err != nil {
			return nil, err
		}
		b.River = river
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
	// Per-outlet watersheds. `basins` stays the merged summary (it is what the
	// area/length numbers are computed from); `parts` is how many separate
	// watersheds this area actually drains by, with the river that carries each.
	// A park on a divide has one; the XSA AOI has two dozen. Without this the
	// UI could only ever say "the watershed", singular, which for most areas is
	// wrong.
	parts, perr := s.loadBasinParts(parkID, "", r.URL.Query().Get("geometry") == "1")
	if perr == nil && len(parts) > 0 {
		resp["parts"] = parts
		up, down := 0, 0
		names := []string{}
		seen := map[string]bool{}
		for _, p := range parts {
			switch p.Kind {
			case "upstream":
				up++
				// Named rivers each watershed drains through, biggest first:
				// the human-readable form of "all watersheds".
				if p.River != "" && !seen[p.River] {
					seen[p.River] = true
					names = append(names, p.River)
				}
			case "downstream":
				down++
			}
		}
		resp["upstream_count"] = up
		resp["downstream_count"] = down
		if len(names) > 0 {
			resp["upstream_rivers"] = names
		}
	}
	var nReach, nReach3 int
	var reachKm, reachKm3 float64
	_ = s.DB.QueryRow(`SELECT COUNT(*),
		COALESCE(SUM(CASE WHEN stream_order>=3 THEN 1 ELSE 0 END),0),
		COALESCE(SUM(length_km),0),
		COALESCE(SUM(CASE WHEN stream_order>=3 THEN length_km ELSE 0 END),0)
		FROM park_basin_rivers WHERE park_id=?`,
		parkID).Scan(&nReach, &nReach3, &reachKm, &reachKm3)
	if nReach > 0 {
		resp["upstream_reaches"] = nReach
		// only these are usable for optical turbidity work
		resp["upstream_reaches_order3plus"] = nReach3
		// river km is the figure worth stating out loud ("8,000 km of upstream
		// reaches drain through this park"); reach counts are an artefact of
		// how MERIT-Basins happens to split lines.
		resp["upstream_river_km"] = math.Round(reachKm)
		resp["upstream_river_km_order3plus"] = math.Round(reachKm3)
	}
	// Named rivers along the downstream trace: the human-readable answer to
	// "where does this park's water actually go". river_names comes from the
	// river-runner response and is empty for every park at present (the API
	// stopped returning reach names), so fall back to the outlet's own river
	// name from the mghydro snap, which is populated.
	for _, b := range basins {
		if b.Kind != "downstream" || b.Meta == nil {
			continue
		}
		var m map[string]any
		if json.Unmarshal(b.Meta, &m) != nil {
			continue
		}
		names := []string{}
		if n, ok := m["river_names"].([]any); ok {
			for _, x := range n {
				if s, ok := x.(string); ok && s != "" {
					names = append(names, s)
				}
			}
		}
		if len(names) == 0 {
			if o, ok := m["outlet"].(map[string]any); ok {
				if s, ok := o["river"].(string); ok && s != "" {
					names = append(names, s)
				}
			}
		}
		resp["downstream_rivers"] = names
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

// handleBasinRiverFeatures serves the upstream river network with Strahler
// order. `min_order` matters: Sentinel-2 cannot see the water surface of 1st-2nd
// order streams under canopy, so turbidity/plume work is only valid on >=3rd
// order reaches (docs/MINING_FINDINGS_2026-08.md 4).
// GET /api/parks/{id}/features?type=basin_rivers[&min_order=3]
func (s *Server) handleBasinRiverFeatures(w http.ResponseWriter, parkID string, minOrder int) {
	rows, err := s.DB.Query(`
		SELECT comid, COALESCE(stream_order,0), COALESCE(length_km,0), geojson
		FROM park_basin_rivers
		WHERE park_id = ? AND COALESCE(stream_order,0) >= ?
		ORDER BY stream_order DESC`, parkID, minOrder)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	feats := []map[string]any{}
	for rows.Next() {
		var comid, order int
		var lengthKm float64
		var geo string
		if err := rows.Scan(&comid, &order, &lengthKm, &geo); err != nil {
			continue
		}
		feats = append(feats, map[string]any{
			"type":     "Feature",
			"geometry": json.RawMessage(geo),
			"properties": map[string]any{
				"feature_type":  "basin_river",
				"feature_id":    fmt.Sprintf("%s_reach_%d", parkID, comid),
				"comid":         comid,
				"stream_order":  order,
				"length_km":     lengthKm,
				"s2_observable": order >= 3,
			},
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"type": "FeatureCollection", "features": feats})
}

// handleBasinFeatures serves the basin as GeoJSON for the map pin layer.
// GET /api/parks/{id}/features?type=basin[&kind=upstream|downstream][&merged=1]
//
// **All** of the area's watersheds, one feature per outlet, from
// park_basin_parts. Serving only the merged union (which is what this did until
// 2026-08-07) meant the map drew a single amorphous polygon for an area that
// drains by several separate rivers, with no way to tell which lobe was which.
// `merged=1` asks for the old union explicitly; anything fetched before
// migration 044 has no parts and falls back to it.
func (s *Server) handleBasinFeatures(w http.ResponseWriter, parkID, kind string) {
	s.handleBasinFeaturesOpt(w, parkID, kind, false)
}

func (s *Server) handleBasinFeaturesOpt(w http.ResponseWriter, parkID, kind string, merged bool) {
	var basins []basinRow
	var err error
	split := false
	if !merged {
		basins, err = s.loadBasinParts(parkID, kind, true)
		split = len(basins) > 0
	}
	if !split {
		basins, err = s.loadBasins(parkID, true)
	}
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	feats := []map[string]any{}
	for _, b := range basins {
		if kind != "" && kind != b.Kind {
			continue
		}
		// feature_id must stay unique per feature or pinning one lobe selects
		// another: the merged row keeps the historical id, the parts suffix the
		// outlet index.
		fid := parkID + "_basin_" + b.Kind
		if split {
			fid = fmt.Sprintf("%s_%d", fid, b.Index)
		}
		props := map[string]any{
			"feature_type": "basin_" + b.Kind,
			"feature_id":   fid,
			"kind":         b.Kind,
			"source":       b.Source,
			"outlet_lat":   b.OutletLat,
			"outlet_lon":   b.OutletLon,
			"fetched_at":   b.FetchedAt,
			"merged":       !split,
		}
		if split {
			props["idx"] = b.Index
			if b.River != "" {
				props["river"] = b.River
			}
		}
		if b.AreaKm2 != nil {
			props["area_km2"] = *b.AreaKm2
			props["name"] = "Contributing basin"
			if b.River != "" {
				props["name"] = b.River + " basin"
			}
		}
		if b.LengthKm != nil {
			props["length_km"] = *b.LengthKm
			props["name"] = "Downstream trace"
			if b.River != "" {
				props["name"] = b.River + " downstream"
			}
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
