package srv

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

// The tile route carries three things in one path segment -- y, the retina
// marker and the extension -- and getting the split wrong is silent: "16@2x"
// parses as neither an int nor an error unless we look. It also decides the
// cache key, so a lost "@2x" would serve 256px tiles to a retina client from
// the same file.
func TestBasemapTileCoordinateParsing(t *testing.T) {
	dir := t.TempDir()
	wd, _ := os.Getwd()
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	defer os.Chdir(wd)

	// A pre-seeded cache file means no request reaches CARTO: this test is
	// about the parse, and a unit test must not spend the month's quota.
	seed := func(styleDir string, z, x, y int, body string) {
		p := filepath.Join(cartoCacheDir, styleDir, strconv.Itoa(z), strconv.Itoa(x), strconv.Itoa(y)+".png")
		os.MkdirAll(filepath.Dir(p), 0o755)
		os.WriteFile(p, []byte(body), 0o644)
	}
	seed("dark_all@2x", 5, 17, 16, "retina-tile")
	seed("dark_all", 5, 17, 16, "plain-tile")

	s := &Server{}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/basemap/{style}/{z}/{x}/{y}", s.HandleAPIBasemapTile)

	cases := []struct {
		path   string
		status int
		body   string
	}{
		{"/api/basemap/dark_all/5/17/16@2x.png", 200, "retina-tile"},
		{"/api/basemap/dark_all/5/17/16.png", 200, "plain-tile"},
		{"/api/basemap/dark_all/5/17/16", 200, "plain-tile"},
		// Allowlist, not passthrough.
		{"/api/basemap/evil/5/17/16.png", 404, ""},
		{"/api/basemap/dark_all/30/1/1.png", 400, ""},
		// x,y must be inside the pyramid for the zoom: at z5 the grid is 32x32.
		{"/api/basemap/dark_all/5/32/1.png", 400, ""},
		{"/api/basemap/dark_all/5/-1/1.png", 400, ""},
		{"/api/basemap/dark_all/5/abc/1.png", 400, ""},
	}
	for _, c := range cases {
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, httptest.NewRequest("GET", c.path, nil))
		if rec.Code != c.status {
			t.Errorf("%s: status %d, want %d", c.path, rec.Code, c.status)
			continue
		}
		if c.body != "" && rec.Body.String() != c.body {
			t.Errorf("%s: served %q, want %q (wrong cache key)", c.path, rec.Body.String(), c.body)
		}
	}
}

// A hit must not be recorded as quota, or the fair-use figure reported to the
// admin panel describes page views rather than upstream fetches.
func TestBasemapCacheHitCostsNoQuota(t *testing.T) {
	dir := t.TempDir()
	wd, _ := os.Getwd()
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	defer os.Chdir(wd)

	p := filepath.Join(cartoCacheDir, "dark_all@2x", "3", "4", "3.png")
	os.MkdirAll(filepath.Dir(p), 0o755)
	os.WriteFile(p, []byte("tile"), 0o644)

	_, before := CartoQuotaThisMonth()
	s := &Server{}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/basemap/{style}/{z}/{x}/{y}", s.HandleAPIBasemapTile)
	for i := 0; i < 5; i++ {
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, httptest.NewRequest("GET", "/api/basemap/dark_all/3/4/3@2x.png", nil))
		if rec.Code != 200 {
			t.Fatalf("hit %d: status %d", i, rec.Code)
		}
		if got := rec.Header().Get("X-Tile-Cache"); got != "hit" {
			t.Fatalf("hit %d: X-Tile-Cache=%q", i, got)
		}
	}
	if _, after := CartoQuotaThisMonth(); after != before {
		t.Errorf("cache hits charged quota: %d -> %d", before, after)
	}
}
