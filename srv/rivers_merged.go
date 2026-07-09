package srv

// Merged river geometries for exports (KML, Locus).
//
// park_rivers_hydro stores raw HydroRIVERS reach segments: thousands of tiny
// 2–20 vertex stubs per park. Exporting the "top 200 by length" produced
// disconnected dashes that looked empty in Locus/Earth. Here we chain
// segments whose endpoints touch (same 1/240° HydroRIVERS grid) into long
// polylines, grouped by river name (unnamed reaches merge by stream order).

import (
	"database/sql"
	"fmt"
	"math"
	"sort"
)

type mergedRiver struct {
	Name        string // display name, e.g. "Mbomou" or "River (order 5)"
	StreamOrder int
	LengthKm    float64 // summed source segment length
	Path        [][2]float64
}

// endpoint key on the HydroRIVERS grid (1/240 deg ≈ 460 m); rounding to
// 1/480 catches float noise while never fusing distinct grid nodes.
func riverEndpointKey(p [2]float64) [2]int64 {
	return [2]int64{int64(math.Round(p[0] * 480)), int64(math.Round(p[1] * 480))}
}

// mergeRiverPaths greedily chains paths that share endpoints.
func mergeRiverPaths(paths [][][2]float64) [][][2]float64 {
	used := make([]bool, len(paths))
	// endpoint -> list of path indices
	ends := map[[2]int64][]int{}
	for i, p := range paths {
		if len(p) < 2 {
			used[i] = true
			continue
		}
		ends[riverEndpointKey(p[0])] = append(ends[riverEndpointKey(p[0])], i)
		ends[riverEndpointKey(p[len(p)-1])] = append(ends[riverEndpointKey(p[len(p)-1])], i)
	}
	takeAt := func(key [2]int64) int {
		for _, i := range ends[key] {
			if !used[i] {
				return i
			}
		}
		return -1
	}
	reverse := func(p [][2]float64) {
		for a, b := 0, len(p)-1; a < b; a, b = a+1, b-1 {
			p[a], p[b] = p[b], p[a]
		}
	}
	var out [][][2]float64
	for i := range paths {
		if used[i] {
			continue
		}
		used[i] = true
		chain := append([][2]float64{}, paths[i]...)
		// extend forward from tail
		for {
			j := takeAt(riverEndpointKey(chain[len(chain)-1]))
			if j < 0 {
				break
			}
			used[j] = true
			seg := append([][2]float64{}, paths[j]...)
			if riverEndpointKey(seg[len(seg)-1]) == riverEndpointKey(chain[len(chain)-1]) {
				reverse(seg)
			}
			chain = append(chain, seg[1:]...)
		}
		// extend backward from head
		for {
			j := takeAt(riverEndpointKey(chain[0]))
			if j < 0 {
				break
			}
			used[j] = true
			seg := append([][2]float64{}, paths[j]...)
			if riverEndpointKey(seg[0]) == riverEndpointKey(chain[0]) {
				reverse(seg)
			}
			chain = append(append([][2]float64{}, seg[:len(seg)-1]...), chain...)
		}
		out = append(out, chain)
	}
	return out
}

// loadMergedRivers returns chained river polylines for a park, longest first.
// minOrder filters noise (1–2 are tiny headwater reaches); named rivers are
// always included regardless of order.
func (s *Server) loadMergedRivers(parkID string, minOrder, maxRivers int) []mergedRiver {
	rows, err := s.DB.Query(`SELECT COALESCE(name,''), stream_order, length_km, geojson
		FROM park_rivers_hydro
		WHERE park_id = ? AND geojson IS NOT NULL AND (stream_order >= ? OR (name IS NOT NULL AND name != ''))`,
		parkID, minOrder)
	if err != nil {
		return nil
	}
	defer rows.Close()

	type bucket struct {
		name     string
		order    int
		lengthKm float64
		paths    [][][2]float64
	}
	buckets := map[string]*bucket{}
	for rows.Next() {
		var name string
		var order sql.NullInt64
		var lengthKm sql.NullFloat64
		var geojson string
		if rows.Scan(&name, &order, &lengthKm, &geojson) != nil {
			continue
		}
		key := name
		if key == "" {
			key = fmt.Sprintf("\x00order%d", order.Int64) // internal key per order
		}
		b := buckets[key]
		if b == nil {
			b = &bucket{name: name, order: int(order.Int64)}
			buckets[key] = b
		}
		if int(order.Int64) > b.order {
			b.order = int(order.Int64)
		}
		b.lengthKm += lengthKm.Float64
		b.paths = append(b.paths, extractPaths(geojson)...)
	}

	var out []mergedRiver
	for _, b := range buckets {
		for _, chain := range mergeRiverPaths(b.paths) {
			name := b.name
			if name == "" {
				name = fmt.Sprintf("River (order %d)", b.order)
			}
			km := 0.0
			for i := 1; i < len(chain); i++ {
				km += haversineDistanceKm(chain[i-1][1], chain[i-1][0], chain[i][1], chain[i][0])
			}
			out = append(out, mergedRiver{Name: name, StreamOrder: b.order, LengthKm: km, Path: chain})
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].StreamOrder != out[j].StreamOrder {
			return out[i].StreamOrder > out[j].StreamOrder
		}
		return out[i].LengthKm > out[j].LengthKm
	})
	if maxRivers > 0 && len(out) > maxRivers {
		out = out[:maxRivers]
	}
	return out
}
