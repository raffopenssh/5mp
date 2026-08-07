package srv

import "testing"

func TestGeoFeatureLimit(t *testing.T) {
	// The UI's "give me everything" idiom must not be read as a cap, or the
	// cached path is never taken and 5,000 of 19,000 rivers ship.
	for _, s := range []string{"", "5000", "10000", "0", "junk"} {
		if l, limited := geoFeatureLimit(s); limited || l != geoFeatureAll {
			t.Errorf("limit %q: got (%d, %v), want whole layer", s, l, limited)
		}
	}
	if l, limited := geoFeatureLimit("10"); !limited || l != 10 {
		t.Errorf("limit 10: got (%d, %v), want (10, true)", l, limited)
	}
}

func TestGeoFeatureSourcesCoverPlaceSuppression(t *testing.T) {
	// buildPlaceFeatures drops a place whose name a river or road already
	// carries, so those two tables are part of the places answer and must be
	// part of its cache revision.
	want := map[string]bool{"osm_places": true, "park_rivers_hydro": true, "roads_heigit": true}
	got := map[string]bool{}
	for _, tbl := range geoFeatureSources["place"] {
		got[tbl] = true
	}
	for tbl := range want {
		if !got[tbl] {
			t.Errorf("place cache revision does not fingerprint %s", tbl)
		}
	}
}
