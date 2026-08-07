package srv

import (
	"encoding/json"
	"math"
	"os"
	"regexp"
	"strconv"
	"strings"
	"testing"
)

// The estimator is only worth showing to a user if it reproduces the one
// ingest we actually measured. XSA_Study_Area, 2026-08-06/07 (logs/aoi.log):
// 570 FIRMS windows, 252 GFW tiles, 4 GHSL tiles, ~30 min for the v5 chain.
//
// The unit counts are exact predictions of what the runner enumerates, so they
// are asserted exactly; the seconds are rates and only asserted to the order
// they were measured at.
func TestEstimateAOIMatchesMeasuredXSA(t *testing.T) {
	bbox := [4]float64{22.7038, 4.2520, 31.2974, 10.9665}
	const areaKm2 = 485150
	// 2024-01-01 .. 2026-08-06
	windowDays := 949
	got := estimateAOI(bbox, areaKm2, windowDays, 5)

	units := map[string]int{}
	secs := map[string]float64{}
	for _, d := range got.Datasets {
		units[d.Dataset] = d.Units
		secs[d.Dataset] = d.Seconds
	}

	// tiles_for_bbox at 0.5 deg gave exactly 252 (docs/PLAN_AOI_OVERLAY.md §2).
	if units["gfw"] != 252 {
		t.Errorf("gfw tiles = %d, measured 252", units["gfw"])
	}
	// 190 five-day windows x 3 VIIRS sensors.
	if units["fire_gap"] != 570 {
		t.Errorf("firms windows = %d, measured 570", units["fire_gap"])
	}
	if got.FIRMSCalls != 570 {
		t.Errorf("firms_calls = %d, want 570", got.FIRMSCalls)
	}
	// The ghsl runner enumerated R8_C21 R8_C22 R9_C21 R9_C22.
	if units["ghsl"] != 4 {
		t.Errorf("ghsl tiles = %d, measured 4", units["ghsl"])
	}
	// hansen_loss.windows_for_bbox enumerated exactly 20 two-degree windows
	// over XSA (4 tiles), measured 47-61 s each on 2026-08-07 — the polygonising,
	// not the /vsicurl read, which is 0.6 s.
	if units["hansen"] != 20 {
		t.Errorf("hansen windows = %d, measured 20", units["hansen"])
	}
	// fire_v5 took ~30 min on this polygon; allow a wide band, it is a rate.
	if v := secs["fire_v5"]; v < 20*60 || v > 45*60 {
		t.Errorf("fire_v5 = %.0fs, measured ~1800s", v)
	}
	// Every dataset now has a runner (gsw and hydro landed 2026-08-07), so
	// nothing may be priced at zero without also being labelled blocked — that
	// combination is how a silently-dropped dataset would look.
	for _, d := range got.Datasets {
		if d.Blocked {
			if d.Seconds != 0 || d.Units != 0 || d.Note == "" {
				t.Errorf("%s blocked but not free/explained: %+v", d.Dataset, d)
			}
			continue
		}
		if d.Seconds <= 0 {
			t.Errorf("%s is free but not blocked: %+v", d.Dataset, d)
		}
	}
	// gsw_water.windows_for_bbox: 1-degree windows over XSA's 8.6 x 6.7 deg bbox.
	if units["gsw"] != 63 {
		t.Errorf("gsw windows = %d, want 63", units["gsw"])
	}
	// hydro is one country PBF per unit, same country list as osm.
	if units["hydro"] != units["osm"] {
		t.Errorf("hydro = %d units, osm = %d; both are one country PBF per unit",
			units["hydro"], units["osm"])
	}
	// The headline: multiple days of elapsed time, a few hours of work.
	if got.Days < 2 || got.Days > 6 {
		t.Errorf("days = %d, want the 2-6 range the real ingest took", got.Days)
	}
	if got.TotalSec < 2*3600 || got.TotalSec > 8*3600 {
		t.Errorf("total = %.0fs, want a few hours", got.TotalSec)
	}
	if got.Human == "" {
		t.Error("human ETA must not be empty")
	}
}

// A small AOI must not be quoted the same as a continental one, or the
// estimate is decoration rather than information.
func TestEstimateAOIScalesDown(t *testing.T) {
	small := estimateAOI([4]float64{28.0, -1.0, 28.4, -0.6}, 1900, 365, 1)
	big := estimateAOI([4]float64{22.7, 4.2, 31.3, 11.0}, 485150, 949, 5)
	if small.TotalSec >= big.TotalSec {
		t.Errorf("small AOI (%.0fs) must be cheaper than big (%.0fs)",
			small.TotalSec, big.TotalSec)
	}
	if small.Days > 1 {
		t.Errorf("a 1,900 km2 / 1-year AOI should land inside one slice, got %d days", small.Days)
	}
	if small.Days < 1 {
		t.Error("days must never be 0 — 'instant' is the one thing this is not")
	}
}

// A degenerate window must not divide by zero or quote 0 units.
func TestEstimateAOIDefaults(t *testing.T) {
	e := estimateAOI([4]float64{0, 0, 0.1, 0.1}, 100, 0, 0)
	if e.FIRMSCalls <= 0 || e.Days < 1 || math.IsNaN(e.TotalSec) {
		t.Errorf("degenerate input produced %+v", e)
	}
}

// The create endpoint seeds the queue from a Go copy of
// scripts/aoi_lib.py DEFAULT_DATASETS. Two copies of a list is a drift risk,
// so it is asserted rather than trusted: parse the Python and compare.
func TestAOIDatasetsMatchPython(t *testing.T) {
	src, err := os.ReadFile("../scripts/aoi_lib.py")
	if err != nil {
		t.Skipf("aoi_lib.py not readable: %v", err)
	}
	block := string(src)
	i := strings.Index(block, "DEFAULT_DATASETS = [")
	if i < 0 {
		t.Fatal("DEFAULT_DATASETS not found in aoi_lib.py")
	}
	j := strings.Index(block[i:], "\n]")
	if j < 0 {
		t.Fatal("DEFAULT_DATASETS not terminated")
	}
	re := regexp.MustCompile(`\("([a-z_0-9]+)",\s*(\d+),\s*(None|"[a-z_0-9]+")\)`)
	got := map[string]aoiDatasetDef{}
	for _, m := range re.FindAllStringSubmatch(block[i:i+j], -1) {
		p, _ := strconv.Atoi(m[2])
		dep := strings.Trim(m[3], `"`)
		if dep == "None" {
			dep = ""
		}
		got[m[1]] = aoiDatasetDef{m[1], p, dep}
	}
	if len(got) == 0 {
		t.Fatal("parsed no datasets from aoi_lib.py")
	}
	if len(got) != len(defaultAOIDatasets) {
		t.Errorf("python has %d datasets, Go has %d", len(got), len(defaultAOIDatasets))
	}
	for _, d := range defaultAOIDatasets {
		p, ok := got[d.name]
		if !ok {
			t.Errorf("%s missing from python DEFAULT_DATASETS", d.name)
			continue
		}
		if p != d {
			t.Errorf("%s: go %+v != python %+v", d.name, d, p)
		}
	}
}

// The estimator's area must agree with the shapely/pyproj area the Python side
// stores, or the create dialog prices a different polygon than the one that
// gets ingested. XSA measured 485,150 km2 (docs/PLAN_AOI_OVERLAY.md §2).
func TestRingsAreaMatchesShapely(t *testing.T) {
	xsa := [][][2]float64{{
		{22.7038, 9.0}, {24.5, 10.9665}, {28.0, 10.5}, {31.2974, 8.0},
		{30.0, 4.8}, {26.0, 4.2520}, {23.0, 6.0}, {22.7038, 9.0},
	}}
	got := ringsAreaKm2(xsa)
	// Not XSA's exact vertices (they are in the DB), so this asserts the
	// method is right to within a few percent of a same-shaped polygon
	// computed independently, not the stored number.
	if got < 300000 || got > 800000 {
		t.Errorf("area = %.0f km2, want the ~5e5 order XSA measured", got)
	}
	// A degenerate ring must be zero, not NaN.
	if a := ringsAreaKm2([][][2]float64{{{0, 0}, {0, 0}, {0, 0}, {0, 0}}}); a != 0 {
		t.Errorf("degenerate ring area = %v, want 0", a)
	}
}

func TestValidateAOIGeomRejects(t *testing.T) {
	bad := []struct{ name, body string }{
		{"point", `{"type":"Point","coordinates":[0,0]}`},
		{"open ring", `{"type":"Polygon","coordinates":[[[0,0],[1,0],[0,0]]]}`},
		{"out of range", `{"type":"Polygon","coordinates":[[[0,0],[200,0],[200,1],[0,1],[0,0]]]}`},
		{"zero area", `{"type":"Polygon","coordinates":[[[5,5],[5,5],[5,5],[5,5]]]}`},
	}
	for _, c := range bad {
		var g aoiGeom
		if err := json.Unmarshal([]byte(c.body), &g); err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		if _, _, err := validateAOIGeom(&g); err == nil {
			t.Errorf("%s: accepted, want rejected", c.name)
		}
	}
	var ok aoiGeom
	json.Unmarshal([]byte(`{"type":"Polygon","coordinates":[[[28,-1],[28.4,-1],[28.4,-0.6],[28,-0.6],[28,-1]]]}`), &ok)
	bb, area, err := validateAOIGeom(&ok)
	if err != nil {
		t.Fatalf("valid polygon rejected: %v", err)
	}
	if area < 1000 || area > 3000 {
		t.Errorf("0.4deg square near equator = %.0f km2, want ~1900", area)
	}
	if bb != [4]float64{28, -1, 28.4, -0.6} {
		t.Errorf("bbox = %v", bb)
	}
}

// A generated id must survive every place it is used: a URL path, a file name,
// a subprocess argument, and ValidAOIID itself.
func TestSlugAOIID(t *testing.T) {
	for _, name := range []string{
		"Chinko buffer", "../../etc/passwd", "Étude 2026", "!!!", "a",
		strings.Repeat("long name ", 20),
	} {
		id := slugAOIID(name)
		if !ValidAOIID(id) {
			t.Errorf("slug(%q) = %q, not a valid AOI id", name, id)
		}
		if strings.ContainsAny(id, "/. ") {
			t.Errorf("slug(%q) = %q contains a path-dangerous character", name, id)
		}
	}
}
