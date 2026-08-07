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
}

// settlementMetaByPolygon maps feature_id -> the settlement row that lists it.
func (s *Server) settlementMetaByPolygon(parkID string) map[string]settlementMeta {
	out := map[string]settlementMeta{}
	rows, err := s.DB.Query(`
		SELECT polygon_ids, narrative, classification, nearest_place, distance_to_place_km
		FROM park_settlements
		WHERE park_id = ? AND polygon_ids IS NOT NULL AND polygon_ids != ''`, parkID)
	if err != nil {
		return out
	}
	defer rows.Close()
	for rows.Next() {
		var polyIDs string
		var m settlementMeta
		if err := rows.Scan(&polyIDs, &m.narrative, &m.classification, &m.nearestPlace, &m.distanceKm); err != nil {
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
