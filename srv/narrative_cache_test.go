package srv

import (
	"database/sql"
	"math/rand"
	"os"
	"testing"

	_ "modernc.org/sqlite"
)

// openLiveDBReadOnly opens the production database read-only. The memo is a
// query-equivalence claim about real data distributions (a park with 3 rivers
// and an AOI with 11,370 behave differently), so a synthetic fixture would test
// the wrong thing. Skips when the db is absent, e.g. in CI.
func openLiveDBReadOnly(t *testing.T) *Server {
	t.Helper()
	if _, err := os.Stat("../db.sqlite3"); err != nil {
		t.Skip("no live db")
	}
	db, err := sql.Open("sqlite", "file:../db.sqlite3?mode=ro&_busy_timeout=5000")
	if err != nil {
		t.Skip("cannot open db read-only:", err)
	}
	t.Cleanup(func() { db.Close() })
	return &Server{DB: db}
}

// The geoMemo exists only to avoid re-running the same bbox query thousands of
// times; it must therefore return exactly what the direct per-point query
// returned, or it is silently rewriting narratives.
func TestGeoMemoMatchesDirectQueries(t *testing.T) {
	s := openLiveDBReadOnly(t)
	parks := []string{"CAF_Chinko", "COD_Virunga", "SSD_Zeraf", "XSA_Study_Area"}
	types := []string{"village", "hamlet", "town", "city"}
	checked := 0

	for _, park := range parks {
		var latMin, latMax, lonMin, lonMax sql.NullFloat64
		err := s.DB.QueryRow(
			`SELECT MIN(lat),MAX(lat),MIN(lon),MAX(lon) FROM deforestation_events WHERE park_id=?`,
			park).Scan(&latMin, &latMax, &lonMin, &lonMax)
		if err != nil || !latMin.Valid {
			continue
		}
		memo := newGeoMemo(s, park)
		rng := rand.New(rand.NewSource(42))
		for i := 0; i < 50; i++ {
			lat := latMin.Float64 + rng.Float64()*(latMax.Float64-latMin.Float64)
			lon := lonMin.Float64 + rng.Float64()*(lonMax.Float64-lonMin.Float64)

			want, _ := s.findNearestPlaces(park, lat, lon, 3, types)
			got := memo.nearestPlaces(lat, lon, 3, types, "settle")
			if len(want) != len(got) {
				t.Fatalf("%s places: len %d != %d at %.4f,%.4f", park, len(want), len(got), lat, lon)
			}
			for j := range want {
				if want[j].Name != got[j].Name || want[j].Distance != got[j].Distance {
					t.Fatalf("%s place[%d]: %q/%.6f != %q/%.6f", park, j,
						want[j].Name, want[j].Distance, got[j].Name, got[j].Distance)
				}
			}

			wantR, _ := s.findNearestRiverToPoint(park, lat, lon, 3)
			gotR, ok := memo.nearestRivers(lat, lon, 3)
			if !ok {
				continue // memo declined; the handler falls back to the direct query
			}
			if len(wantR) != len(gotR) {
				t.Fatalf("%s rivers: len %d != %d at %.4f,%.4f", park, len(wantR), len(gotR), lat, lon)
			}
			for j := range wantR {
				if wantR[j].Name != gotR[j].Name {
					t.Fatalf("%s river[%d]: %q != %q", park, j, wantR[j].Name, gotR[j].Name)
				}
			}
			checked++
		}
	}
	if checked == 0 {
		t.Skip("no park data to compare")
	}
	t.Logf("compared %d points", checked)
}

// source_rev must change when the underlying rows change, or a stale narrative
// is served forever; and must NOT change otherwise, or the cache never hits.
func TestNarrativeSourceRevStability(t *testing.T) {
	s := openLiveDBReadOnly(t)
	a := s.narrativeSourceRev("deforestation_events", "CAF_Chinko")
	b := s.narrativeSourceRev("deforestation_events", "CAF_Chinko")
	if a != b {
		t.Fatalf("rev not stable: %q != %q", a, b)
	}
	if c := s.narrativeSourceRev("deforestation_events", "COD_Virunga"); c == a {
		t.Fatalf("different parks share a rev: %q", c)
	}
}
