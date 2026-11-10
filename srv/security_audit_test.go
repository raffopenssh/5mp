package srv

import (
	"net/http/httptest"
	"testing"
)

// Regressions from the 2026-11-10 security audit. Each case was a working
// request against the live server before the fix.

func TestPublicPathIsOnlyStaticAssets(t *testing.T) {
	for p, want := range map[string]bool{
		"/static/anim.js":                true,
		"/static/vendor/x.css":           true,
		"/api/legal/CAF/x.js":            false, // gate bypass: wildcard last segment
		"/api/legal/pa/CAF_Chinko/x.css": false,
		"/api/aois/aoi_1.svg":            false,
		"/park/1.js":                     false,
		"/static/../api/version.js":      false,
		"/healthz":                       true,
	} {
		if got := isPublicPath(p); got != want {
			t.Errorf("isPublicPath(%q) = %v, want %v", p, got, want)
		}
	}
}

func TestLocalPathCannotLeaveTheSite(t *testing.T) {
	for in, want := range map[string]string{
		"//evil.example/":   "/evil.example/",
		`/\evil.example/`:   "/evil.example/",
		`\\evil.example`:    "/evil.example",
		"/":                 "/",
		"/park/COD_Virunga": "/park/COD_Virunga",
	} {
		if got := localPath(in); got != want {
			t.Errorf("localPath(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestShortLinkRefusesOffSiteTargets(t *testing.T) {
	req := httptest.NewRequest("GET", "http://five.example/", nil)
	for _, raw := range []string{`/\example.org/x`, "//example.org/x", "/%5Cexample.org/x", "/x\r\nSet-Cookie: a=b"} {
		if out, ok := shortNormalizeURL(raw, req); ok {
			t.Errorf("shortNormalizeURL(%q) accepted as %q", raw, out)
		}
	}
	if _, ok := shortNormalizeURL("/?park=CAF_Chinko", nil); !ok {
		t.Error("an ordinary in-site target was refused")
	}
}

func TestGuestClampCoversDateAliases(t *testing.T) {
	g := &GuestSession{DateFrom: "2025-01-01", DateTo: "2025-01-31"}
	for _, q := range []string{"year=2026", "start=2026-01-01&end=2026-10-31", "from_year=2020&to_year=2026", ""} {
		r := clampGuestQuery(httptest.NewRequest("GET", "/api/parks/CAF_Chinko/export.kml?"+q, nil), g)
		got := r.URL.Query()
		if got.Get("from") != "2025-01-01" && q != "start=2026-01-01&end=2026-10-31" {
			t.Errorf("%q: from = %q", q, got.Get("from"))
		}
		if f, to := got.Get("from"), got.Get("to"); f < g.DateFrom || to > g.DateTo || f == "" || to == "" {
			t.Errorf("%q: window %q..%q escapes the lock", q, f, to)
		}
		for _, k := range []string{"year", "start", "end", "from_year", "to_year"} {
			if got.Has(k) {
				t.Errorf("%q: %s survived the clamp", q, k)
			}
		}
	}
}

func TestPublicHandleIsNotTheHashPrefix(t *testing.T) {
	ref := principalRef("test2026")
	if h := publicHandle(ref); h != "" && h == ref[:8] {
		t.Fatal("handle is the raw sha256 prefix of the password")
	}
}
