package srv

// The reference workings: the independently-published mine sites this map's
// affinity model was scored against.
//
// WHY THEY SHIP WITH THE GEOLOGY AND NOT AS A LAYER OF THEIR OWN
//
// The geology overlay states an INFERENCE ("this rock can host gold"). Its
// credibility rests entirely on having been measured against ground somebody
// else walked, and until now that evidence existed only as a lift printed in a
// tooltip. A number in a tooltip is not checkable. These points are: every
// working we scored against, each carrying the id its ORIGINAL publisher uses
// and the URL where that id resolves, so a reader can open IPIS's or OSM's own
// record and see that we did not invent it.
//
// FIVE FIELDS, AND THE OMISSIONS ARE THE POINT. Worker counts, armed-actor
// flags, environmental scores and conflict scalars exist in
// data/eval/mining_reference.json and are NOT here: they are the source's
// research, not our anchor. What is left is a citation, which is why every
// list ships including the ones that granted no licence — `terms` says which
// on every row. The one exclusion is ACLED, which was never a mining list and
// whose terms forbid rows outright; it is NAMED in `withheld` rather than
// omitted. Single writer: scripts/mining_anchors.py.
//
// THEY ARE NEVER FILTERED BY THE READER'S SELECTION. Cutting the anchors down
// to the commodity the chooser is showing would produce a file in which every
// anchor agrees with the layer, which is a picture of our own filter and reads
// as a prediction that came true. The whole set ships every time; `resource`
// is a column, so anyone who wants gold-only can ask for it themselves and
// know that they did the asking.

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
)

const geoAnchorPath = "data/geology_truth/mining_anchors.geojson"

type geoAnchorProps struct {
	Source    string `json:"source"`
	SourceID  string `json:"source_id"`
	SourceURL string `json:"source_url"`
	ISO3      string `json:"iso3"`
	Year      *int   `json:"year"`
	Resource  string `json:"resource"`
	Observed  string `json:"observed"`
	Licence   string `json:"licence"`
	// Terms rides on EVERY ROW, not only in the header: a reader who selects
	// one source out of this layer and passes it on must carry the fact that
	// its publisher granted no licence. "open" | "unstated".
	Terms       string `json:"terms"`
	Attribution string `json:"attribution"`
}

type geoAnchorFeature struct {
	Geometry json.RawMessage `json:"geometry"`
	Props    geoAnchorProps  `json:"properties"`
}

// geoAnchorSource is one list, as the file's own header describes it.
type geoAnchorSource struct {
	Source      string `json:"source"`
	Label       string `json:"label"`
	Attribution string `json:"attribution"`
	Licence     string `json:"licence"`
	Terms       string `json:"terms"`
	Landing     string `json:"landing"`
	Observed    string `json:"observed"`
	Note        string `json:"note,omitempty"`
	Sites       int    `json:"sites"`
}

// geoAnchorWithheld is a list that was SCORED but may not be redistributed.
// It ships as prose, never as rows — and it ships, rather than being omitted,
// because "we did not check there" and "we checked and may not show you" are
// different statements and an absence blurs them.
type geoAnchorWithheld struct {
	Source  string `json:"source"`
	Label   string `json:"label"`
	Terms   string `json:"terms"`
	Why     string `json:"why"`
	Landing string `json:"landing"`
	Sites   *int   `json:"sites"`
}

type geoAnchorDoc struct {
	Notice   string              `json:"notice"`
	Purpose  string              `json:"purpose"`
	Sources  []geoAnchorSource   `json:"sources"`
	Withheld []geoAnchorWithheld `json:"withheld"`
	Features []geoAnchorFeature  `json:"features"`
}

var (
	geoAnchorOnce sync.Once
	geoAnchors    *geoAnchorDoc
	geoAnchorErr  error
)

func geoAnchorFile() string {
	return filepath.Join(filepath.Dir(geoMaps.dir), "geology_truth", "mining_anchors.geojson")
}

// loadGeoAnchors reads the committed anchor file once. Its ABSENCE is not an
// error the panel should hide: a server without it simply cannot offer the
// evidence, and the export must say so rather than shipping a geology file
// that looks like it never had any.
func loadGeoAnchors() (*geoAnchorDoc, error) {
	geoAnchorOnce.Do(func() {
		blob, err := os.ReadFile(geoAnchorFile())
		if err != nil {
			geoAnchorErr = fmt.Errorf("mining anchors not installed: %w", err)
			return
		}
		var d geoAnchorDoc
		if err := json.Unmarshal(blob, &d); err != nil {
			geoAnchorErr = fmt.Errorf("%s: %w", geoAnchorFile(), err)
			return
		}
		// Invariant 1: a file that parsed but holds nothing is a broken build,
		// not an empty world. Offering it as an evidence layer would be a
		// no-op that reads as an answer.
		if len(d.Features) == 0 {
			geoAnchorErr = fmt.Errorf("%s holds no anchors; rebuild with scripts/mining_anchors.py", geoAnchorFile())
			return
		}
		geoAnchors = &d
	})
	return geoAnchors, geoAnchorErr
}

// geoAnchorSummary is what the catalogue tells the client: enough to say "2,660
// workings from 4 lists" beside the download, and never the points themselves
// (they are export payload, not map state — the panel has no anchor layer).
func geoAnchorSummary() map[string]any {
	d, err := loadGeoAnchors()
	if err != nil {
		return map[string]any{"available": false, "reason": err.Error()}
	}
	out := map[string]any{
		"available": true,
		"n":         len(d.Features),
		"sources":   d.Sources,
		"withheld":  d.Withheld,
		"notice":    d.Notice,
	}
	return out
}
