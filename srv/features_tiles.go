package srv

// /api/lod-tiles/{z}/{x}/{y}.mvt — the pinned-layer loader's tile tier.
//
// THE PROBLEM IT SOLVES. `XSA_Study_Area` holds 38,789 fire trajectories.
// Pinned, they arrived as one 3.4 MB JSON answer (1.5 MB gzipped) that the
// browser then parsed on the main thread, turned into 38,789 GeoJSON
// objects, structured-cloned into MapLibre's worker, indexed with geojson-vt
// and tessellated — and did all of it again at every zoom, because a zoom
// re-asks. Measured on a server-class core: 73 ms parse + 217 ms clone +
// 448 ms index + ~22 ms per tile cut; a mid-range phone is 4–6x that, on a
// 1.5 MB download, per gesture. That is the freeze.
//
// A vector tile is the answer the renderer actually wants: decoded in the
// worker from a compact delta-coded protobuf, only the tiles in view are
// fetched (a z6 phone view of the AOI is ~6 of them), a pan fetches only the
// new ones, and a zoom-in reuses the parent while its children arrive. The
// server work per tile is a bbox-indexed scan of a few thousand rows, cached.
//
// WHAT A TILE CARRIES. The same features as the JSON tiers, with the same
// row id (`rid`) so a hover fetches /api/feature-detail exactly as a chord or
// a dot does — a tile is not a picture (lod.md "the same feature at every
// zoom"). Below tileFullGeomZoom a feature is its three-point CHORD (first /
// middle / last vertex — the cheap tier of a path is a shorter path, not a
// dot); from that zoom on it is the full geometry, simplified to the tile's
// resolution. `ev`/`eb` ride along because the line is PAINTED from them
// (width grade, direction chevrons; invariant 12).
//
// WHAT IT DOES NOT DO. It does not sample or truncate: a tile holds every
// feature intersecting it. The count the chip prints comes from the JSON
// endpoint's aggregate query, so the two cannot disagree (invariant 7).

import (
	"container/list"
	"context"
	"encoding/json"
	"fmt"
	"hash/fnv"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	// Above this many features in view the JSON answer becomes a tile
	// template (when the client can take one). Same order as the geometry
	// budget: below it a single JSON body is small and carries full
	// properties, which the tiles deliberately do not.
	tileAboveCount = 3000
	// From this zoom a tile carries full geometry rather than chords. At z9
	// one tile is ~0.7 deg; a 150 km trajectory is a route, not a stroke.
	tileFullGeomZoom = 9
	// Tiles past this zoom are overzoomed by MapLibre from the z12 tile —
	// full geometry simplified at z12 is 1/4 px at z14, so nothing is lost.
	tileMaxZoom      = 12
	lodTileLayerName = "features"
	// Fraction of a tile's width fetched beyond its edge, so a stroke that
	// crosses the seam is drawn on both sides of it. MapLibre's own buffer is
	// 128/4096 (3 %); 5 % covers the widest stroke this layer draws.
	tilePad = 0.05
	// A tile holding more than this is logged: with ?area= required and the
	// largest AOI at 38,789 rows in total it cannot happen, and if it does
	// the log line is the evidence rather than a silently slow tile.
	tileWarnRows = 100000
)

// lodTileTemplate is the URL template the JSON endpoint hands back. It
// carries the scope and window (type, area, focus, dates) and nothing else —
// no credential: the browser sends the session cookie with same-origin tile
// requests, and a URL with a password in it is the leak invariant 14 exists
// to stop.
func lodTileTemplate(q url.Values, featureType string) string {
	p := url.Values{}
	p.Set("type", featureType)
	for _, k := range []string{"area", "park", "aoi", "park_focus", "from", "to"} {
		if v := q.Get(k); v != "" {
			p.Set(k, v)
		}
	}
	return "/api/lod-tiles/{z}/{x}/{y}.mvt?" + p.Encode()
}

// ---- cache -----------------------------------------------------------------

type tileCacheEntry struct {
	key  string
	body []byte
	etag string
	at   time.Time
	el   *list.Element
}

type tileCache struct {
	mu    sync.Mutex
	m     map[string]*tileCacheEntry
	lru   *list.List
	bytes int
	maxB  int
	ttl   time.Duration
}

var lodTiles = &tileCache{m: map[string]*tileCacheEntry{}, lru: list.New(), maxB: 64 << 20, ttl: 10 * time.Minute}

func (c *tileCache) get(key string) *tileCacheEntry {
	c.mu.Lock()
	defer c.mu.Unlock()
	e := c.m[key]
	if e == nil {
		return nil
	}
	if time.Since(e.at) > c.ttl {
		c.evict(e)
		return nil
	}
	c.lru.MoveToFront(e.el)
	return e
}

func (c *tileCache) put(key string, body []byte, etag string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if old := c.m[key]; old != nil {
		c.evict(old)
	}
	e := &tileCacheEntry{key: key, body: body, etag: etag, at: time.Now()}
	e.el = c.lru.PushFront(e)
	c.m[key] = e
	c.bytes += len(body)
	for c.bytes > c.maxB && c.lru.Len() > 1 {
		c.evict(c.lru.Back().Value.(*tileCacheEntry))
	}
}

func (c *tileCache) evict(e *tileCacheEntry) {
	c.lru.Remove(e.el)
	delete(c.m, e.key)
	c.bytes -= len(e.body)
}

// ---- handler ---------------------------------------------------------------

// HandleAPILODTile — GET /api/lod-tiles/{z}/{x}/{y}.mvt?type=&area=&from=&to=
func (s *Server) HandleAPILODTile(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	featureType := q.Get("type")
	if !lineLikeFeature(featureType) {
		http.Error(w, "tiles are served for line layers (type=fire_trajectory)", http.StatusBadRequest)
		return
	}
	z, errZ := strconv.Atoi(r.PathValue("z"))
	x, errX := strconv.Atoi(r.PathValue("x"))
	yStr := strings.TrimSuffix(r.PathValue("y"), ".mvt")
	y, errY := strconv.Atoi(yStr)
	if errZ != nil || errX != nil || errY != nil || z < 0 || z > tileMaxZoom || x < 0 || y < 0 || x >= 1<<uint(z) || y >= 1<<uint(z) {
		http.Error(w, "invalid tile", http.StatusBadRequest)
		return
	}
	// A tile is a statement about ONE area's rows (see the JSON endpoint:
	// only a pinned layer is offered tiles). Without it a z3 tile could hold
	// 300k continental rows.
	if q.Get("area") == "" && q.Get("park") == "" {
		http.Error(w, "area required", http.StatusBadRequest)
		return
	}

	bbox := tileBBox(z, x, y, tilePad)
	sc := s.bboxScope(r, featureType, bbox)
	if sc.area == "" {
		// An invisible or unknown area: an empty tile, not an error — an id
		// must not be an oracle (invariant 6).
		w.Header().Set("Content-Type", "application/vnd.mapbox-vector-tile")
		w.Write(newMVTLayer(lodTileLayerName).encodeTile())
		return
	}

	// Cache on the RESOLVED question (where + args), not the raw query: two
	// principals asking the same URL resolve to the same rows only if both
	// may see the area, and the resolution above has already decided that.
	key := fmt.Sprintf("%d/%d/%d|%s|%v", z, x, y, sc.where, sc.args)
	if e := lodTiles.get(key); e != nil {
		serveLODTile(w, r, e.body, e.etag)
		return
	}

	body, n, err := s.buildLODTile(r.Context(), z, x, y, sc)
	if err != nil {
		internalError(w, "tile failed", err)
		return
	}
	if n > tileWarnRows {
		log.Printf("lod-tiles: %d/%d/%d for %s holds %d rows", z, x, y, sc.area, n)
	}
	h := fnv.New64a()
	h.Write(body)
	etag := fmt.Sprintf(`"%x"`, h.Sum64())
	lodTiles.put(key, body, etag)
	serveLODTile(w, r, body, etag)
}

func serveLODTile(w http.ResponseWriter, r *http.Request, body []byte, etag string) {
	w.Header().Set("ETag", etag)
	if r.Header.Get("If-None-Match") == etag {
		w.WriteHeader(http.StatusNotModified)
		return
	}
	w.Header().Set("Content-Type", "application/vnd.mapbox-vector-tile")
	w.Write(body)
}

// buildLODTile reads every row intersecting the (padded) tile and encodes it.
// Returns the tile bytes and the row count.
func (s *Server) buildLODTile(ctx context.Context, z, x, y int, sc bboxScope) ([]byte, int, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT id, geojson, properties_json`+sc.where, sc.args...)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	proj := newTileProjector(z, x, y)
	layer := newMVTLayer(lodTileLayerName)
	full := z >= tileFullGeomZoom
	// Simplification tolerance in degrees: ~1.5 tile units at this zoom.
	tileWidthDeg := 360 / float64(uint64(1)<<uint(z))
	tol := tileWidthDeg / mvtExtent * 1.5

	buf := make([][2]float64, 0, 256)
	n := 0
	for rows.Next() {
		var id int64
		var gj string
		var props *string
		if err := rows.Scan(&id, &gj, &props); err != nil {
			continue
		}
		n++
		// The row id travels as the `rid` tag (what the tip reads), not as the
		// MVT feature id as well: 6 bytes a feature, 15 % of a dense tile.
		f := mvtFeature{}
		f.tags = append(f.tags, mvtTag{"rid", mvtValue{kind: 'i', i: id}})
		if props != nil {
			if ev := evidenceTier(*props); ev != "" {
				f.tags = append(f.tags, mvtTag{"ev", mvtValue{kind: 's', s: ev}})
			}
			if eb, ok := evidenceBits(*props); ok {
				f.tags = append(f.tags, mvtTag{"eb", mvtValue{kind: 'd', f: eb}})
			}
		}
		if full {
			if !tileFullGeometry(&f, gj, tol, proj) {
				continue
			}
		} else {
			pts := scanCoordPairs(gj, buf[:0])
			if len(pts) == 0 {
				continue
			}
			// The middle point is the actual middle VERTEX, not the average
			// of the ends (fetchChords): a fire that doubled back must not
			// draw through ground it never touched.
			a, m, b := pts[0], pts[len(pts)/2], pts[len(pts)-1]
			line := [][2]int32{proj.project(a[0], a[1]), proj.project(m[0], m[1]), proj.project(b[0], b[1])}
			line = dedupPts(line, false)
			if len(line) < 2 {
				// A stationary fire is a Point, not a zero-length line that
				// MapLibre would draw as nothing.
				f.geom = 1
				f.parts = [][][2]int32{{line[0]}}
			} else {
				f.geom = 2
				f.parts = [][][2]int32{line}
			}
		}
		layer.add(f)
	}
	return layer.encodeTile(), n, rows.Err()
}

// tileFullGeometry fills f with the row's stored geometry, simplified and
// projected. Returns false for a geometry it cannot draw.
func tileFullGeometry(f *mvtFeature, gj string, tol float64, proj tileProjector) bool {
	var g struct {
		Type   string          `json:"type"`
		Coords json.RawMessage `json:"coordinates"`
	}
	if err := json.Unmarshal([]byte(gj), &g); err != nil {
		return false
	}
	projectLine := func(pts [][]float64, ring bool) [][2]int32 {
		pts = simplifyLine(pts, tol, ring)
		out := make([][2]int32, 0, len(pts))
		for _, p := range pts {
			if len(p) >= 2 {
				out = append(out, proj.project(p[0], p[1]))
			}
		}
		return out
	}
	switch g.Type {
	case "Point":
		var p []float64
		if json.Unmarshal(g.Coords, &p) != nil || len(p) < 2 {
			return false
		}
		f.geom = 1
		f.parts = [][][2]int32{{proj.project(p[0], p[1])}}
	case "LineString":
		var l [][]float64
		if json.Unmarshal(g.Coords, &l) != nil {
			return false
		}
		line := dedupPts(projectLine(l, false), false)
		if len(line) < 2 {
			if len(line) == 0 {
				return false
			}
			f.geom = 1
			f.parts = [][][2]int32{{line[0]}}
			return true
		}
		f.geom = 2
		f.parts = [][][2]int32{line}
	case "MultiLineString":
		var ml [][][]float64
		if json.Unmarshal(g.Coords, &ml) != nil {
			return false
		}
		f.geom = 2
		for _, l := range ml {
			f.parts = append(f.parts, projectLine(l, false))
		}
	case "Polygon":
		var rings [][][]float64
		if json.Unmarshal(g.Coords, &rings) != nil {
			return false
		}
		f.geom = 3
		for _, rg := range rings {
			f.parts = append(f.parts, projectLine(rg, true))
		}
	case "MultiPolygon":
		// One MVT polygon feature may hold several exterior rings; ring
		// winding tells them apart, and add() enforces it per ring index —
		// so flatten with each polygon's exterior first.
		var polys [][][][]float64
		if json.Unmarshal(g.Coords, &polys) != nil {
			return false
		}
		f.geom = 3
		for _, rings := range polys {
			for _, rg := range rings {
				f.parts = append(f.parts, projectLine(rg, true))
			}
		}
	default:
		return false
	}
	return true
}
