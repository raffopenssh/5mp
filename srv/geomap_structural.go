package srv

// Continental structural context: the two JRC Africa Knowledge Platform
// layers the eval said to ingest (docs/agents/overlays.md "Other geology
// data, weighed", docs/PLAN_NEW_DATA_LAYERS.md WP2).
//
//   - active_faults: 406 fault traces (Macgregor 2014)
//   - craton_edges: the BOUNDARIES of the 9 AKP craton polygons
//
// WHY LINEWORK AND NOT A FOURTH SHEET. A sheet is a vectorized scan with
// classes, legend colours and commodity columns; these are continental lines
// with neither. They are served whole (both files together are ~116 KB
// gzipped ~35 KB) as plain GeoJSON, not tiles: tiling 415 features would be
// engineering against no cost.
//
// WHY THE CRATON EDGE AND NEVER THE POLYGON. 60% of the measured hull is
// inside a craton, so "on a craton" scores ~1.0 and means nothing; the MARGIN
// concentrates DRC gold visits 2.9x. scripts/geomaps/fetch_akp.py therefore
// ships boundary linework only, and this server has no way to draw the fill.
//
// THE SKILL IS MEASURED OR THE WORD IS "unmeasured" (invariant 12 shape).
// geoStructuralSkill in geomap_scores_table.go is GENERATED from the eval's
// --continental section; a layer with no row there ships skill: null and the
// UI prints the word. The measurement's ground (IPIS DRC visits) overlaps no
// sheet we serve, and geoStructuralSkillScope says so on every surface that
// quotes a lift.
//
// UNGRADED CONTEXT, NOT A GRADE. These lines carry no commodity weight and
// must never wear the contact amber ramp: a fault drawn in the graded ink
// would claim a grade nobody computed.

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// geoStructuralLift is one measured proximity lift from the continental eval.
// Referenced by the generated geomap_scores_table.go.
type geoStructuralLift struct {
	Near     float64 `json:"near"`      // share of sites within the threshold
	MedianKM float64 `json:"median_km"` // median distance site -> nearest line
	Lift     float64 `json:"lift"`      // Near / random baseline's Near
	N        int     `json:"n"`         // sites measured
}

// geoStructuralLayers: served id -> (file, key in geoStructuralSkill). The
// skill key differs for the cratons because the eval scored the EDGE and
// named it that; keeping the eval's own word here means the generated table
// and this map can only agree or visibly miss, never silently alias.
var geoStructuralLayers = map[string]struct {
	File     string
	SkillKey string
	Label    string
}{
	"active_faults": {"active_faults.geojson", "active_faults", "Active faults"},
	"craton_edges":  {"craton_edges.geojson", "craton_edge", "Craton margins"},
}

// geoStructuralDir is a var, not a const, only so tests can point it at a
// fixture; production never reassigns it.
var geoStructuralDir = "data/akp"

type geoStructuralLayer struct {
	gz  []byte // the file, gzipped once at load
	n   int    // feature count, derived from the file (invariant 2)
	rev string // mtime+size, same scheme as the tiles
	// R7 attribution, read from the artefact itself, never typed here.
	Source   string `json:"source"`
	Citation string `json:"citation"`
	Terms    string `json:"terms"`
	Accessed string `json:"accessed"`
	Notice   string `json:"notice"`
	err      error
}

var (
	geoStructuralOnce sync.Once
	geoStructural     map[string]*geoStructuralLayer
)

func geoStructuralPath(file string) string {
	return filepath.Join(geoStructuralDir, file)
}

func loadGeoStructural() map[string]*geoStructuralLayer {
	geoStructuralOnce.Do(func() {
		geoStructural = map[string]*geoStructuralLayer{}
		for id, spec := range geoStructuralLayers {
			geoStructural[id] = loadGeoStructuralLayer(geoStructuralPath(spec.File))
		}
	})
	return geoStructural
}

func loadGeoStructuralLayer(path string) *geoStructuralLayer {
	l := &geoStructuralLayer{}
	blob, err := os.ReadFile(path)
	if err != nil {
		l.err = fmt.Errorf("not installed: run scripts/geomaps/fetch_akp.py (%w)", err)
		return l
	}
	var doc struct {
		Source   string          `json:"source"`
		Citation string          `json:"citation"`
		Terms    string          `json:"terms"`
		Accessed string          `json:"accessed"`
		Notice   string          `json:"notice"`
		Features json.RawMessage `json:"features"`
	}
	if err := json.Unmarshal(blob, &doc); err != nil {
		l.err = fmt.Errorf("%s: %w", path, err)
		return l
	}
	var feats []json.RawMessage
	if err := json.Unmarshal(doc.Features, &feats); err != nil {
		l.err = fmt.Errorf("%s features: %w", path, err)
		return l
	}
	// Invariant 1: a file that parsed and holds nothing is a broken fetch,
	// not a continent without faults. Refusing to serve it is what makes the
	// caller re-run the fetch instead of reading an empty map as an answer.
	if len(feats) == 0 {
		l.err = fmt.Errorf("%s holds no features; re-run scripts/geomaps/fetch_akp.py", path)
		return l
	}
	l.n = len(feats)
	l.Source, l.Citation, l.Terms = doc.Source, doc.Citation, doc.Terms
	l.Accessed, l.Notice = doc.Accessed, doc.Notice
	l.rev = geoMapRev(path)
	var buf bytes.Buffer
	zw, _ := gzip.NewWriterLevel(&buf, gzip.BestCompression)
	zw.Write(blob)
	zw.Close()
	l.gz = buf.Bytes()
	return l
}

// geoStructuralSummary is what the catalogue (/api/geomap) reports: enough
// for the panel to offer the toggles and print each layer's measured skill —
// or the word "unmeasured" — without a second request. An absent or broken
// file reports itself (`available: false` + reason) rather than vanishing:
// a missing key would read as "there is no such layer", which is a claim
// about Africa, not about this server.
func geoStructuralSummary() map[string]any {
	out := map[string]any{}
	for id, spec := range geoStructuralLayers {
		l := loadGeoStructural()[id]
		if l.err != nil {
			out[id] = map[string]any{"available": false, "label": spec.Label,
				"reason": l.err.Error()}
			continue
		}
		e := map[string]any{
			"available": true,
			"label":     spec.Label,
			"n":         l.n,
			"url":       "/api/geomap-structural/" + id + "?v=" + l.rev,
			"source":    l.Source,
			"citation":  l.Citation,
			"terms":     l.Terms,
			"accessed":  l.Accessed,
			"notice":    l.Notice,
		}
		// Measured or absent, never a placeholder number. `skill: null` is the
		// UI's instruction to print "unmeasured" (R4): the absence of a
		// measurement must reach the reader as a word, not as a blank.
		if per, ok := geoStructuralSkill[spec.SkillKey]; ok && len(per) > 0 {
			e["skill"] = map[string]any{"lifts": per, "scope": geoStructuralSkillScope}
		}
		out[id] = e
	}
	return out
}

// HandleAPIGeoMapStructural serves one structural layer whole, as GeoJSON.
// Immutable + ?v= like the tiles: the URL the catalogue hands out embeds the
// file's revision, so a re-fetch changes the URL rather than fighting caches.
func (s *Server) HandleAPIGeoMapStructural(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("layer")
	spec, ok := geoStructuralLayers[id]
	if !ok {
		http.Error(w, "unknown structural layer", http.StatusNotFound)
		return
	}
	l := loadGeoStructural()[id]
	if l.err != nil {
		http.Error(w, spec.Label+" "+l.err.Error(), http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/geo+json")
	w.Header().Set("Cache-Control", "public, max-age=604800, immutable")
	if strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
		w.Header().Set("Content-Encoding", "gzip")
		w.Write(l.gz)
		return
	}
	zr, err := gzip.NewReader(bytes.NewReader(l.gz))
	if err != nil {
		http.Error(w, "decode failed", http.StatusInternalServerError)
		return
	}
	defer zr.Close()
	var buf bytes.Buffer
	buf.ReadFrom(zr)
	w.Write(buf.Bytes())
}
