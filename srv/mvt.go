package srv

// A minimal Mapbox Vector Tile (MVT v2) encoder.
//
// Hand-rolled protobuf rather than a dependency: the tile layer we emit is
// one layer, four property keys and three geometry types, and the whole
// spec fits in this file. See https://github.com/mapbox/vector-tile-spec/tree/master/2.1
//
// Why tiles at all: a pinned AOI fire layer used to arrive as one 3.4 MB JSON
// answer that the browser parsed on the main thread, cloned into MapLibre's
// worker, indexed with geojson-vt and re-fetched on every zoom. On a phone
// that is several seconds frozen per gesture. A vector tile is decoded in the
// worker, only the tiles in view are fetched, and a pan fetches only the new
// ones (srv/features_tiles.go).

import (
	"math"
	"strconv"
)

const mvtExtent = 4096

// mvtValue is one property value. Only the kinds we use.
type mvtValue struct {
	kind byte // 's' string, 'd' double, 'i' int64
	s    string
	f    float64
	i    int64
}

type mvtFeature struct {
	id    uint64
	geom  byte         // 1 point, 2 linestring, 3 polygon
	parts [][][2]int32 // rings / lines / points, in tile coordinates
	tags  []mvtTag
}

type mvtTag struct {
	key string
	val mvtValue
}

// mvtLayer accumulates features and interns keys/values as the spec requires.
type mvtLayer struct {
	name     string
	keys     []string
	keyIdx   map[string]uint32
	vals     []mvtValue
	valIdx   map[string]uint32
	features [][]byte // encoded Feature messages
}

func newMVTLayer(name string) *mvtLayer {
	return &mvtLayer{name: name, keyIdx: map[string]uint32{}, valIdx: map[string]uint32{}}
}

func (l *mvtLayer) key(k string) uint32 {
	if i, ok := l.keyIdx[k]; ok {
		return i
	}
	i := uint32(len(l.keys))
	l.keys = append(l.keys, k)
	l.keyIdx[k] = i
	return i
}

func (l *mvtLayer) val(v mvtValue) uint32 {
	var sig string
	switch v.kind {
	case 's':
		sig = "s" + v.s
	case 'd':
		sig = "d" + formatFloat(v.f)
	default:
		sig = "i" + formatInt(v.i)
	}
	if i, ok := l.valIdx[sig]; ok {
		return i
	}
	i := uint32(len(l.vals))
	l.vals = append(l.vals, v)
	l.valIdx[sig] = i
	return i
}

func formatFloat(f float64) string { return strconv.FormatFloat(f, 'g', -1, 64) }
func formatInt(i int64) string     { return strconv.FormatInt(i, 10) }

// add encodes one feature. Parts with fewer than the minimum distinct
// vertices for their type are dropped; a feature with nothing left is
// skipped and add returns false.
func (l *mvtLayer) add(f mvtFeature) bool {
	var geom []uint32
	var cx, cy int32
	cmd := func(id, n uint32) { geom = append(geom, (id&0x7)|(n<<3)) }
	param := func(x, y int32) {
		geom = append(geom, zigzag(x-cx), zigzag(y-cy))
		cx, cy = x, y
	}
	nParts := 0
	switch f.geom {
	case 1: // points: one MoveTo with n params
		var pts [][2]int32
		for _, p := range f.parts {
			pts = append(pts, p...)
		}
		if len(pts) == 0 {
			return false
		}
		cmd(1, uint32(len(pts)))
		for _, p := range pts {
			param(p[0], p[1])
		}
		nParts = 1
	case 2:
		for _, line := range f.parts {
			line = dedupPts(line, false)
			if len(line) < 2 {
				continue
			}
			cmd(1, 1)
			param(line[0][0], line[0][1])
			cmd(2, uint32(len(line)-1))
			for _, p := range line[1:] {
				param(p[0], p[1])
			}
			nParts++
		}
	case 3:
		for ri, ring := range f.parts {
			ring = dedupPts(ring, true)
			if len(ring) < 3 {
				continue
			}
			// MVT v2: exterior rings clockwise, interior anticlockwise, in
			// tile space (y down). Shoelace sign in y-down space: negative
			// area == clockwise on screen.
			if (ri == 0) != (shoelace(ring) < 0) {
				for i, j := 0, len(ring)-1; i < j; i, j = i+1, j-1 {
					ring[i], ring[j] = ring[j], ring[i]
				}
			}
			cmd(1, 1)
			param(ring[0][0], ring[0][1])
			cmd(2, uint32(len(ring)-1))
			for _, p := range ring[1:] {
				param(p[0], p[1])
			}
			cmd(7, 1)
			nParts++
		}
	default:
		return false
	}
	if nParts == 0 {
		return false
	}

	var b []byte
	if f.id != 0 {
		b = pbTag(b, 1, 0)
		b = appendUvarint(b, f.id)
	}
	if len(f.tags) > 0 {
		var tags []byte
		for _, t := range f.tags {
			tags = appendUvarint(tags, uint64(l.key(t.key)))
			tags = appendUvarint(tags, uint64(l.val(t.val)))
		}
		b = pbBytes(b, 2, tags)
	}
	b = pbTag(b, 3, 0)
	b = appendUvarint(b, uint64(f.geom))
	var g []byte
	for _, v := range geom {
		g = appendUvarint(g, uint64(v))
	}
	b = pbBytes(b, 4, g)
	l.features = append(l.features, b)
	return true
}

// encodeTile wraps the layer in a Tile message.
func (l *mvtLayer) encodeTile() []byte {
	var layer []byte
	layer = pbTag(layer, 15, 0)
	layer = appendUvarint(layer, 2) // version
	layer = pbBytes(layer, 1, []byte(l.name))
	for _, f := range l.features {
		layer = pbBytes(layer, 2, f)
	}
	for _, k := range l.keys {
		layer = pbBytes(layer, 3, []byte(k))
	}
	for _, v := range l.vals {
		var vb []byte
		switch v.kind {
		case 's':
			vb = pbBytes(vb, 1, []byte(v.s))
		case 'd':
			vb = pbTag(vb, 3, 1)
			vb = appendFixed64(vb, math.Float64bits(v.f))
		default:
			vb = pbTag(vb, 6, 0)
			vb = appendUvarint(vb, uint64(zigzag64(v.i)))
		}
		layer = pbBytes(layer, 4, vb)
	}
	layer = pbTag(layer, 5, 0)
	layer = appendUvarint(layer, mvtExtent)
	var tile []byte
	tile = pbBytes(tile, 3, layer)
	return tile
}

// ---- projection --------------------------------------------------------

// tileProjector maps lon/lat to integer tile coordinates for tile z/x/y.
type tileProjector struct {
	z, x, y int
	scale   float64
}

func newTileProjector(z, x, y int) tileProjector {
	return tileProjector{z: z, x: x, y: y, scale: float64(uint64(1)<<uint(z)) * mvtExtent}
}

func (p tileProjector) project(lon, lat float64) [2]int32 {
	if lat > 85.05112878 {
		lat = 85.05112878
	} else if lat < -85.05112878 {
		lat = -85.05112878
	}
	x := (lon+180)/360*p.scale - float64(p.x)*mvtExtent
	sin := math.Sin(lat * math.Pi / 180)
	y := (0.5-0.25*math.Log((1+sin)/(1-sin))/math.Pi)*p.scale - float64(p.y)*mvtExtent
	return [2]int32{int32(math.Round(x)), int32(math.Round(y))}
}

// tileBBox is the lon/lat bounds of tile z/x/y, padded by `pad` (fraction of
// the tile width) on every side.
func tileBBox(z, x, y int, pad float64) [4]float64 {
	n := float64(uint64(1) << uint(z))
	lon := func(px float64) float64 { return px/n*360 - 180 }
	lat := func(py float64) float64 {
		t := math.Pi * (1 - 2*py/n)
		return math.Atan(math.Sinh(t)) * 180 / math.Pi
	}
	fx, fy := float64(x), float64(y)
	return [4]float64{lon(fx - pad), lat(fy + 1 + pad), lon(fx + 1 + pad), lat(fy - pad)}
}

// ---- helpers -------------------------------------------------------------

func dedupPts(pts [][2]int32, ring bool) [][2]int32 {
	out := pts[:0:0]
	for i, p := range pts {
		if i > 0 && p == pts[i-1] {
			continue
		}
		out = append(out, p)
	}
	// A ring's closing vertex is implied by ClosePath.
	if ring && len(out) > 1 && out[0] == out[len(out)-1] {
		out = out[:len(out)-1]
	}
	return out
}

func shoelace(r [][2]int32) float64 {
	var a float64
	for i := range r {
		j := (i + 1) % len(r)
		a += float64(r[i][0])*float64(r[j][1]) - float64(r[j][0])*float64(r[i][1])
	}
	return a / 2
}

func zigzag(v int32) uint32   { return uint32((v << 1) ^ (v >> 31)) }
func zigzag64(v int64) uint64 { return uint64((v << 1) ^ (v >> 63)) }

func appendUvarint(b []byte, v uint64) []byte {
	for v >= 0x80 {
		b = append(b, byte(v)|0x80)
		v >>= 7
	}
	return append(b, byte(v))
}

func appendFixed64(b []byte, v uint64) []byte {
	for i := 0; i < 8; i++ {
		b = append(b, byte(v>>(8*uint(i))))
	}
	return b
}

// pbTag writes a field header: (field << 3) | wireType.
func pbTag(b []byte, field, wire uint64) []byte { return appendUvarint(b, field<<3|wire) }

func pbBytes(b []byte, field uint64, payload []byte) []byte {
	b = pbTag(b, field, 2)
	b = appendUvarint(b, uint64(len(payload)))
	return append(b, payload...)
}
