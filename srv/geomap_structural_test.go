package srv

import (
	"compress/gzip"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// useTestStructural points the structural loader at a fixture dir and resets
// its load-once cache, so each test sees its own files.
func useTestStructural(t *testing.T, dir string) {
	t.Helper()
	old := geoStructuralDir
	geoStructuralDir = dir
	geoStructuralOnce = sync.Once{}
	geoStructural = nil
	t.Cleanup(func() {
		geoStructuralDir = old
		geoStructuralOnce = sync.Once{}
		geoStructural = nil
	})
}

func writeTestStructural(t *testing.T, dir string, nFaults, nCratons int) {
	t.Helper()
	mk := func(file string, n int, props string) {
		feats := ""
		for i := 0; i < n; i++ {
			if i > 0 {
				feats += ","
			}
			feats += fmt.Sprintf(`{"type":"Feature","properties":%s,"geometry":{"type":"MultiLineString","coordinates":[[[%d,0],[%d,1]]]}}`, props, i, i)
		}
		doc := `{"type":"FeatureCollection","source":"test","citation":"test cite","terms":"test terms","accessed":"2026-08-13","notice":"test notice","features":[` + feats + `]}`
		if err := os.WriteFile(filepath.Join(dir, file), []byte(doc), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	mk("active_faults.geojson", nFaults, `{"type":"extensional","reference":"Macgregor (2014)"}`)
	mk("craton_edges.geojson", nCratons, `{"name":"Testland","source":"Macgregor, 2020"}`)
}

// The served layer must be the file, whole: a truncated answer is
// indistinguishable from a complete one (cross-cutting invariant 8). Runs
// against the REAL fetched files when they are installed, so the count the
// handler serves is compared to the count fetch_akp.py wrote — never typed.
func TestGeoStructuralServesEveryFeatureInTheFile(t *testing.T) {
	dir := "../data/akp"
	if _, err := os.Stat(filepath.Join(dir, "active_faults.geojson")); err != nil {
		t.Skipf("%s absent — run scripts/geomaps/fetch_akp.py", dir)
	}
	useTestStructural(t, dir)
	s := &Server{}
	for id, spec := range geoStructuralLayers {
		blob, err := os.ReadFile(filepath.Join(dir, spec.File))
		if err != nil {
			t.Fatal(err)
		}
		var file struct {
			Features []json.RawMessage `json:"features"`
		}
		if err := json.Unmarshal(blob, &file); err != nil {
			t.Fatal(err)
		}

		req := httptest.NewRequest("GET", "/api/geomap/structural/"+id, nil)
		req.SetPathValue("layer", id)
		req.Header.Set("Accept-Encoding", "gzip")
		rec := httptest.NewRecorder()
		s.HandleAPIGeoMapStructural(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("%s: status %d", id, rec.Code)
		}
		if rec.Header().Get("Content-Encoding") != "gzip" {
			t.Errorf("%s: not gzipped for a gzip-accepting client", id)
		}
		zr, err := gzip.NewReader(rec.Body)
		if err != nil {
			t.Fatal(err)
		}
		body, err := io.ReadAll(zr)
		if err != nil {
			t.Fatal(err)
		}
		var served struct {
			Features []json.RawMessage `json:"features"`
			Source   string            `json:"source"`
			Citation string            `json:"citation"`
			Terms    string            `json:"terms"`
			Accessed string            `json:"accessed"`
		}
		if err := json.Unmarshal(body, &served); err != nil {
			t.Fatal(err)
		}
		if len(served.Features) != len(file.Features) {
			t.Errorf("%s: served %d features, file holds %d", id, len(served.Features), len(file.Features))
		}
		// R7: the attribution rides INSIDE the artefact the reader saves.
		for k, v := range map[string]string{"source": served.Source, "citation": served.Citation,
			"terms": served.Terms, "accessed": served.Accessed} {
			if v == "" {
				t.Errorf("%s: served file lost its %q attribution", id, k)
			}
		}
	}
}

// Invariant 1: a file that parsed and holds nothing is a broken fetch, not a
// continent without faults. It must refuse to serve, and the catalogue must
// name the layer as unavailable rather than omit it.
func TestGeoStructuralRefusesAnEmptyFile(t *testing.T) {
	dir := t.TempDir()
	useTestStructural(t, dir)
	writeTestStructural(t, dir, 0, 3)

	s := &Server{}
	req := httptest.NewRequest("GET", "/api/geomap/structural/active_faults", nil)
	req.SetPathValue("layer", "active_faults")
	rec := httptest.NewRecorder()
	s.HandleAPIGeoMapStructural(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Errorf("empty faults file served with status %d; an empty layer reads as "+
			"'no faults in Africa', which nobody measured", rec.Code)
	}

	sum := geoStructuralSummary()
	f, ok := sum["active_faults"].(map[string]any)
	if !ok {
		t.Fatal("broken layer omitted from the catalogue; absence must be named, not implied")
	}
	if av, _ := f["available"].(bool); av {
		t.Error("empty faults file reported available")
	}
	if f["reason"] == "" || f["reason"] == nil {
		t.Error("unavailable layer must say why")
	}
	// The other layer is fine and must not be dragged down with it.
	c, _ := sum["craton_edges"].(map[string]any)
	if av, _ := c["available"].(bool); !av {
		t.Error("healthy craton layer reported unavailable beside a broken fault file")
	}
	if n, _ := c["n"].(int); n != 3 {
		t.Errorf("craton count %v, want 3 (derived from the file, invariant 2)", c["n"])
	}
}

// An unknown id is 404, and a served id embeds its revision in the URL the
// catalogue hands out — the immutable cache contract.
func TestGeoStructuralCatalogueURLsCarryARevision(t *testing.T) {
	dir := t.TempDir()
	useTestStructural(t, dir)
	writeTestStructural(t, dir, 2, 2)

	sum := geoStructuralSummary()
	for id, v := range sum {
		e := v.(map[string]any)
		url, _ := e["url"].(string)
		if url == "" || !strings.Contains(url, "?v=") {
			t.Errorf("%s: url %q has no ?v= revision; immutable caching would pin a stale file", id, url)
		}
	}

	s := &Server{}
	req := httptest.NewRequest("GET", "/api/geomap/structural/nope", nil)
	req.SetPathValue("layer", "nope")
	rec := httptest.NewRecorder()
	s.HandleAPIGeoMapStructural(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Errorf("unknown layer id: status %d, want 404", rec.Code)
	}
}

// The GPKG stamp must cover the structural inputs: a re-fetched fault file
// must stale the cached package exactly like a re-derived contact file does
// (TestGeoMapGPKGStampCoversContactsAndAnchors is the model).
func TestGeoMapGPKGStampCoversStructuralInputs(t *testing.T) {
	dir := t.TempDir()
	useTestSheets(t, dir, "tst")
	useTestAnchors(t, dir, 3)
	useTestStructural(t, dir)
	writeTestSheet(t, dir)
	writeTestStructural(t, dir, 4, 2)

	if err := buildGeoMapGeoPackage(geoMapGPKGPath(), []string{"tst"}); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady(); !ok {
		t.Fatal("freshly built package must be ready")
	}
	future := time.Now().Add(2 * time.Hour)
	if err := os.Chtimes(filepath.Join(dir, "active_faults.geojson"), future, future); err != nil {
		t.Fatal(err)
	}
	if _, ok := geoMapGPKGReady(); ok {
		t.Error("a re-fetched fault file must invalidate the package: the units in " +
			"it would still be right, which is what makes this invisible")
	}
}

// A filtered view export honours the reader's switch (off means off), and the
// whole-catalogue export always ships the structural context — like anchors.
func TestGeoMapViewExportStructuralFollowsTheSwitch(t *testing.T) {
	dir := t.TempDir()
	useTestSheets(t, dir, "tst")
	useTestStructural(t, dir)
	writeTestSheet(t, dir)
	writeTestStructural(t, dir, 4, 2)

	tables := func(sel *geoMapSelection) map[string]bool {
		path := filepath.Join(t.TempDir(), "out.gpkg")
		var err error
		if sel == nil {
			err = buildGeoMapGeoPackage(path, []string{"tst"})
		} else {
			err = buildGeoMapGeoPackageSel(path, []string{"tst"}, sel)
		}
		if err != nil {
			t.Fatal(err)
		}
		return gpkgTables(t, path)
	}

	whole := tables(nil)
	if !whole["structural_active_faults"] || !whole["structural_craton_edges"] {
		t.Error("whole-catalogue export must ship the structural context, like the anchors")
	}

	off := tables(&geoMapSelection{Units: []string{"tst:Au/Bx"}})
	if off["structural_active_faults"] || off["structural_craton_edges"] {
		t.Error("a view with structural layers OFF must not ship them: the download " +
			"would be a picture of our defaults, not of the reader's view")
	}

	on := tables(&geoMapSelection{Units: []string{"tst:Au/Bx"}, Structural: []string{"craton_edges"}})
	if on["structural_active_faults"] || !on["structural_craton_edges"] {
		t.Errorf("a view with cratons drawn must ship exactly them: got faults=%v cratons=%v",
			on["structural_active_faults"], on["structural_craton_edges"])
	}
}

func gpkgTables(t *testing.T, path string) map[string]bool {
	t.Helper()
	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	rows, err := db.Query(`SELECT table_name FROM gpkg_contents`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	out := map[string]bool{}
	for rows.Next() {
		var n string
		if err := rows.Scan(&n); err != nil {
			t.Fatal(err)
		}
		out[n] = true
	}
	return out
}
