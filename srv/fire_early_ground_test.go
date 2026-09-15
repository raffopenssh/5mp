package srv

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The early-burn ground exports carry the same cells the map draws, and
// every row names its basis. Live-db test (skips without one).
func TestEarlyGroundExportsNameTheirBasis(t *testing.T) {
	s := openLiveDBReadOnly(t)
	r := s.earlyGround("CAF_Chinko", "")
	if r.Status != "ok" {
		t.Skip("CAF_Chinko early ground not computed:", r.Status)
	}
	if r.SeasonsHeld < 3 || len(r.Cells) == 0 {
		t.Fatalf("Chinko should hold >= 3 seasons with cells; got %d seasons, %d cells", r.SeasonsHeld, len(r.Cells))
	}

	// KML: one placemark per cell, grouped by grade, words per cell.
	var kml strings.Builder
	n := s.writeEarlyGroundKML(&kml, "CAF_Chinko")
	if n != len(r.Cells) {
		t.Fatalf("KML wrote %d of %d cells", n, len(r.Cells))
	}
	out := kml.String()
	if strings.Count(out, "<Placemark>") != n {
		t.Fatalf("KML placemarks %d != cells %d", strings.Count(out, "<Placemark>"), n)
	}
	if !strings.Contains(out, "of 8 seasons") && !strings.Contains(out, "of "+itoa(r.SeasonsHeld)+" seasons") {
		t.Fatalf("KML words do not name the seasons held")
	}
	if !strings.Contains(out, "rule: first burn >= 15 d ahead") {
		t.Fatalf("KML words do not name the rule")
	}

	// GeoPackage: the layer exists with the basis columns filled.
	path := filepath.Join(t.TempDir(), "eg.gpkg")
	w, err := newGPKGWriter(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.gpkgFireEarlyGround(w, gpkgExportOpts{AreaID: "CAF_Chinko"}); err != nil {
		t.Fatal(err)
	}
	if err := w.Finish(); err != nil {
		t.Fatal(err)
	}
	db, err := sql.Open("sqlite", "file:"+path+"?mode=ro")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var cnt, early, held, ahead int
	var rule, grade, basis string
	var share float64
	if err := db.QueryRow(`SELECT COUNT(*) FROM fire_early_ground`).Scan(&cnt); err != nil {
		t.Fatal(err)
	}
	if cnt != len(r.Cells) {
		t.Fatalf("gpkg rows %d != cells %d", cnt, len(r.Cells))
	}
	if err := db.QueryRow(`SELECT rule, seasons_early, seasons_held, share_early, grade, days_ahead_median, basis
		FROM fire_early_ground ORDER BY share_early DESC LIMIT 1`).Scan(&rule, &early, &held, &share, &grade, &ahead, &basis); err != nil {
		t.Fatal(err)
	}
	if rule != "recur" || held != r.SeasonsHeld || early < 2 || early > held || share < r.MinShare || ahead < r.AheadDays || !strings.Contains(basis, "seasons") {
		t.Fatalf("basis row wrong: rule=%s early=%d held=%d share=%.2f grade=%s ahead=%d basis=%q", rule, early, held, share, grade, ahead, basis)
	}
	if grade != "solid" {
		t.Fatalf("highest share should grade solid, got %s (share %.2f)", grade, share)
	}
	os.Remove(path)
}

// An area the rule could not judge is an explicit state in the wire, never an
// empty success (invariant 1); an unknown area is "not yet computed".
func TestEarlyGroundInsufficientIsExplicit(t *testing.T) {
	s := openLiveDBReadOnly(t)
	w := s.earlyGround("NO_SUCH_AREA_XYZ", "").wire("")
	if w["status"] != "not yet computed" || w["cells"] != nil || w["reason"] == nil {
		t.Fatalf("unknown area wire: %+v", w)
	}
	x := s.earlyGround("XSA_Study_Area", "")
	if x.Status == "ok" && x.SeasonsHeld < 2 {
		t.Fatalf("XSA ok with %d seasons held", x.SeasonsHeld)
	}
}
