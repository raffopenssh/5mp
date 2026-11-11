package srv

// Where a learned feature IS, in the words the rest of the map uses: the
// nearest OSM village and the nearest named river.
//
// Computed when /api/learned-map is served, never stored. The gazetteer
// (osm_places) and the river names (park_rivers_hydro) are rewritten by the
// nightly Geofabrik rotation (scripts/osm_pbf.py); a copy kept on the learned
// row would be a second answer that drifts from the first, and would cost
// disk to hold. The learner's own evidence_json keeps only what its SCORE
// reads (near_osm_place within 500 m).
//
// Villages are looked up across every park's rows (idx_op_latlon), because a
// learned feature is credited to the park its tracker works in and can lie
// outside that park's own extract. Rivers are looked up in the feature's park.

import (
	"context"
	"encoding/json"
	"math"
)

const (
	learnedCtxVillageKm = 10.0   // beyond this a village does not locate anything
	learnedCtxRiverM    = 1500.0 // "at the river", not "in its basin"
)

type learnedContext struct {
	Village     string
	VillageType string
	VillageKm   float64
	VillageDir  string
	River       string
	RiverM      float64
}

// apply writes the context into a feature's properties. Absent keys mean
// "nothing within range", which the tip simply does not print.
func (c learnedContext) apply(p map[string]any) {
	if c.Village != "" {
		p["near_village"] = c.Village
		p["near_village_type"] = c.VillageType
		p["near_village_km"] = math.Round(c.VillageKm*10) / 10
		p["near_village_dir"] = c.VillageDir
	}
	if c.River != "" {
		p["near_river"] = c.River
		p["near_river_m"] = math.Round(c.RiverM/50) * 50
	}
}

func bearing8(fromLat, fromLon, toLat, toLon float64) string {
	dy := toLat - fromLat
	dx := (toLon - fromLon) * math.Cos(fromLat*math.Pi/180)
	deg := math.Mod(math.Atan2(dx, dy)*180/math.Pi+360, 360)
	return []string{"N", "NE", "E", "SE", "S", "SW", "W", "NW"}[int(math.Mod(deg+22.5, 360)/45)]
}

func (s *Server) learnedContextAt(ctx context.Context, parkID string, lat, lon float64) learnedContext {
	var c learnedContext
	dLat := learnedCtxVillageKm / 111.0
	dLon := dLat / math.Max(0.2, math.Cos(lat*math.Pi/180))
	if rows, err := s.DB.QueryContext(ctx, `SELECT name, place_type, lat, lon FROM osm_places
		WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
		  AND place_type IN ('city','town','village','hamlet')`,
		lat-dLat, lat+dLat, lon-dLon, lon+dLon); err == nil {
		best := learnedCtxVillageKm
		for rows.Next() {
			var name, typ string
			var la, lo float64
			if rows.Scan(&name, &typ, &la, &lo) != nil || name == "" {
				continue
			}
			if d := haversineDistance(lat, lon, la, lo); d <= best {
				best = d
				c.Village, c.VillageType, c.VillageKm = name, typ, d
				c.VillageDir = bearing8(la, lo, lat, lon) // the feature, seen from the village
			}
		}
		rows.Close()
	}

	// Candidates by reach centroid, then the real distance to the line: a
	// reach is a few km long and its centroid is not where the bank is.
	const dR = 0.06
	if rows, err := s.DB.QueryContext(ctx, `SELECT name, geojson FROM park_rivers_hydro
		WHERE park_id = ? AND name IS NOT NULL AND name != '' AND geojson IS NOT NULL
		  AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?`,
		parkID, lat-dR, lat+dR, lon-dR, lon+dR); err == nil {
		best := learnedCtxRiverM
		pt := []float64{lon, lat}
		for rows.Next() {
			var name, gj string
			if rows.Scan(&name, &gj) != nil {
				continue
			}
			var g struct {
				Type        string          `json:"type"`
				Coordinates json.RawMessage `json:"coordinates"`
			}
			if json.Unmarshal([]byte(gj), &g) != nil {
				continue
			}
			var lines [][][]float64
			if g.Type == "MultiLineString" {
				json.Unmarshal(g.Coordinates, &lines)
			} else {
				var l [][]float64
				if json.Unmarshal(g.Coordinates, &l) == nil {
					lines = [][][]float64{l}
				}
			}
			for _, l := range lines {
				for i := 0; i+1 < len(l); i++ {
					if len(l[i]) < 2 || len(l[i+1]) < 2 {
						continue
					}
					if d := pointSegDistMeters(pt, l[i], l[i+1]); d <= best {
						best, c.River, c.RiverM = d, name, d
					}
				}
			}
		}
		rows.Close()
	}
	return c
}
