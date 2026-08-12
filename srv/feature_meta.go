package srv

import (
	"database/sql"
	"fmt"
	"strings"
)

// Narrative/classification lookups for the feature endpoints.
//
// Both park_settlements.polygon_ids and deforestation_events.polygon_ids are
// comma-separated lists of feature_geometries.feature_id. Joining on them in
// SQL means `(',' || polygon_ids || ',') LIKE ('%,' || fg.feature_id || ',%')`,
// which is non-sargable in the worst way: SQLite has to pair every polygon with
// every event and run a string search. For a park (hundreds of events, a few
// thousand polygons) that is milliseconds and nobody noticed; for
// XSA_Study_Area it is 1,552 x 74,904 settlements = 29 s and 7,815 x 80,408
// deforestation = 13 s, i.e. pinning "all settlements" from the AOI popup
// looked hung.
//
// One scan of the (small) events table, split in Go, gives the identical
// answer in ~30 ms. Do not reintroduce the LIKE join.

func splitPolygonIDs(s string) []string {
	if s == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	out := parts[:0]
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

type settlementMeta struct {
	narrative, classification, nearestPlace sql.NullString
	distanceKm                              sql.NullFloat64
	// groupID is the park_settlements row this footprint belongs to — the
	// identity of the SETTLEMENT, as opposed to of the polygon. See
	// settlementGroupKey.
	groupID int64
}

// settlementMetaByPolygon maps feature_id -> the settlement row that lists it.
//
// Scanner-injected rows are excluded here as everywhere else
// (srv/mining_flag.go): a footprint must not inherit a narrative, a
// classification or a group identity from retired detector output.
func (s *Server) settlementMetaByPolygon(parkID string) map[string]settlementMeta {
	out := map[string]settlementMeta{}
	rows, err := s.DB.Query(`
		SELECT polygon_ids, narrative, classification, nearest_place, distance_to_place_km, id
		FROM park_settlements
		WHERE park_id = ? AND polygon_ids IS NOT NULL AND polygon_ids != ''`+
		scannerInjectedSQLFilter("narrative"), parkID)
	if err != nil {
		return out
	}
	defer rows.Close()
	for rows.Next() {
		var polyIDs string
		var m settlementMeta
		if err := rows.Scan(&polyIDs, &m.narrative, &m.classification, &m.nearestPlace, &m.distanceKm, &m.groupID); err != nil {
			continue
		}
		for _, id := range splitPolygonIDs(polyIDs) {
			out[id] = m
		}
	}
	return out
}

type deforestMeta struct {
	narrative, classification, patternType sql.NullString
}

// defMetaKey mirrors the old join's ON clause: polygon id AND year.
func defMetaKey(featureID string, year interface{}) string {
	switch v := year.(type) {
	case float64:
		return fmt.Sprintf("%s|%d", featureID, int(v))
	case int:
		return fmt.Sprintf("%s|%d", featureID, v)
	case string:
		return featureID + "|" + v
	}
	return featureID + "|"
}

func (s *Server) deforestMetaByPolygon(parkID string) map[string]deforestMeta {
	out := map[string]deforestMeta{}
	rows, err := s.DB.Query(`
		SELECT polygon_ids, year, narrative, classification, pattern_type
		FROM deforestation_events
		WHERE park_id = ? AND polygon_ids IS NOT NULL AND polygon_ids != ''`, parkID)
	if err != nil {
		return out
	}
	defer rows.Close()
	for rows.Next() {
		var polyIDs string
		var year int
		var m deforestMeta
		if err := rows.Scan(&polyIDs, &year, &m.narrative, &m.classification, &m.patternType); err != nil {
			continue
		}
		for _, id := range splitPolygonIDs(polyIDs) {
			out[fmt.Sprintf("%s|%d", id, year)] = m
		}
	}
	return out
}

// ---- viewport enrichment -------------------------------------------------

// featureMetaCache memoises the per-park meta maps for one request. A viewport
// answer can span several parks (and an AOI), so the lookup is keyed by park,
// but a full scan per feature would be the polygon_ids trap wearing a hat.
type featureMetaCache struct {
	settlements map[string]map[string]settlementMeta
	deforest    map[string]map[string]deforestMeta
}

// enrichFeatureProps adds the narrative/classification a hover tip needs.
//
// The bbox endpoint reads feature_geometries only, whose properties_json for a
// settlement is {area_m2, population_est, lat, lon} and for a deforestation
// polygon {year, area_km2} — nothing to say in a tip. The text lives in
// park_settlements / deforestation_events. Fire trajectories already carry
// their narrative inline, so they need nothing here.
func (s *Server) enrichFeatureProps(featureType, parkID, featureID string,
	props map[string]interface{}, c *featureMetaCache) {
	switch featureType {
	case "settlement":
		if c.settlements == nil {
			c.settlements = map[string]map[string]settlementMeta{}
		}
		m, ok := c.settlements[parkID]
		if !ok {
			m = s.settlementMetaByPolygon(parkID)
			c.settlements[parkID] = m
		}
		e, ok := m[featureID]
		if !ok {
			return
		}
		if e.classification.Valid {
			props["classification"] = publicSettlementClass(e.classification.String)
		}
		if e.narrative.Valid {
			cls := ""
			if e.classification.Valid {
				cls = publicSettlementClass(e.classification.String)
			}
			if n := publicSettlementNarrative(cls, e.narrative.String); n != "" {
				props["narrative"] = n
			}
		}
		if e.nearestPlace.Valid {
			props["nearest_place"] = e.nearestPlace.String
		}
		if e.distanceKm.Valid {
			props["distance_to_place_km"] = e.distanceKm.Float64
		}
	case "deforestation":
		if c.deforest == nil {
			c.deforest = map[string]map[string]deforestMeta{}
		}
		m, ok := c.deforest[parkID]
		if !ok {
			m = s.deforestMetaByPolygon(parkID)
			c.deforest[parkID] = m
		}
		e, ok := m[defMetaKey(featureID, props["year"])]
		if !ok {
			return
		}
		if e.narrative.Valid {
			props["narrative"] = e.narrative.String
		}
		if e.classification.Valid {
			props["classification"] = e.classification.String
		}
		if e.patternType.Valid {
			props["pattern_type"] = e.patternType.String
		}
	}
}

// settlementGroupKey maps one built-up FOOTPRINT to the settlement it belongs
// to, as a stable string ident.
//
// A settlement is a cluster of adjacent GHSL built-up polygons
// (rebuild_events_enhanced.py); feature_geometries holds the polygons and
// park_settlements holds the clusters. Chinko is 35 polygons and 27
// settlements. Counting the polygons and calling them settlements is how the
// viewport readout came to disagree with the panel and the popup by a third.
//
// A footprint no cluster claims is its own group rather than being dropped: it
// is on screen, so it has to be in the number describing the screen, and
// invariant 1 says a lookup that matched nothing must not silently subtract.
// Prefixed so a park_id can never collide with a settlement row id.
func (s *Server) settlementGroupKey(parkID, featureID string, c *featureMetaCache) string {
	if c.settlements == nil {
		c.settlements = map[string]map[string]settlementMeta{}
	}
	m, ok := c.settlements[parkID]
	if !ok {
		m = s.settlementMetaByPolygon(parkID)
		c.settlements[parkID] = m
	}
	if e, ok := m[featureID]; ok && e.groupID != 0 {
		return fmt.Sprintf("g:%s:%d", parkID, e.groupID)
	}
	return "u:" + parkID + ":" + featureID
}

// featureIDsWithClass returns the feature_ids of one area whose classification
// matches, for the viewport endpoint's ?class= filter.
//
// nil means "this feature type has no classification" (fire trajectories), and
// the caller must then serve the UNFILTERED answer rather than an empty one: a
// filter that cannot apply is not a filter that excludes everything. An empty
// (non-nil) map means the class is real and nothing in this area carries it,
// which legitimately draws nothing.
//
// Same rule as everywhere else in this file: one scan of the small events
// table, split in Go. Never the polygon_ids LIKE join.
func (s *Server) featureIDsWithClass(featureType, areaID, class string) map[string]bool {
	out := map[string]bool{}
	switch featureType {
	case "settlement":
		for id, m := range s.settlementMetaByPolygon(areaID) {
			if m.classification.Valid && publicSettlementClass(m.classification.String) == class {
				out[id] = true
			}
		}
	case "deforestation":
		// Keyed by "<feature_id>|<year>" for the tip lookup; the filter is
		// about the polygon, so the year is dropped here.
		for key, m := range s.deforestMetaByPolygon(areaID) {
			if !m.classification.Valid || m.classification.String != class {
				continue
			}
			if i := strings.LastIndexByte(key, '|'); i >= 0 {
				out[key[:i]] = true
			} else {
				out[key] = true
			}
		}
	default:
		return nil
	}
	return out
}
