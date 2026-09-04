package srv

import (
	"net/http/httptest"
	"os"
	"regexp"
	"strings"
	"testing"
)

// Every entry must be complete enough to be an attribution, and nothing may
// claim "open" without a URL where the licence can be read.
func TestLicenseRegisterComplete(t *testing.T) {
	seen := map[string]bool{}
	for _, e := range Licenses {
		if seen[e.ID] {
			t.Errorf("duplicate licence id %q", e.ID)
		}
		seen[e.ID] = true
		for k, v := range map[string]string{"name": e.Name, "publisher": e.Publisher, "use": e.Use, "licence": e.Licence, "attribution": e.Attribution, "url": e.URL} {
			if strings.TrimSpace(v) == "" {
				t.Errorf("%s: empty %s", e.ID, k)
			}
		}
		switch e.Terms {
		case TermsOpen:
			if e.LicenceURL == "" {
				t.Errorf("%s: terms=open without a licence_url", e.ID)
			}
		case TermsRestricted:
			if e.Notes == "" && e.LicenceURL == "" {
				t.Errorf("%s: terms=restricted must state the condition (Notes) or link the terms", e.ID)
			}
		case TermsUnstated:
		default:
			t.Errorf("%s: unknown terms %q", e.ID, e.Terms)
		}
		switch e.Category {
		case "imagery", "data", "software":
		default:
			t.Errorf("%s: unknown category %q", e.ID, e.Category)
		}
	}
}

// Every external tile/asset host the frontend reaches must be declared in the
// register, so a basemap cannot be added without stating whose it is and on
// what terms. Private sources a user adds at runtime (srv/tile_sources.go) are
// not in this file and not this server's to license — the test pins only what
// ships.
func TestFrontendTileHostsAreLicensed(t *testing.T) {
	html, err := os.ReadFile("templates/globe.html")
	if err != nil {
		t.Skip("template not available:", err)
	}
	re := regexp.MustCompile(`(?:tiles:\s*\[\s*'|glyphs:\s*'|<script src="|<link href=")https://([a-z0-9.-]+)/`)
	for _, m := range re.FindAllStringSubmatch(string(html), -1) {
		host := m[1]
		if LicenseForHost(host) != nil {
			continue
		}
		t.Errorf("frontend loads from %s but no licence entry declares that host", host)
	}
	// The retired providers must not come back.
	for _, banned := range []string{"mt1.google.com", "mt{s}.google.com", "virtualearth.net", "arcgisonline.com"} {
		if strings.Contains(string(html), banned) {
			t.Errorf("globe.html references %s, whose terms forbid direct tile access", banned)
		}
	}
}

// The offline builder may only copy imagery whose licence permits copying,
// and every source must carry the attribution it writes into the file.
func TestMBTilesSourcesAreRedistributable(t *testing.T) {
	for name, src := range TileSources {
		if src.Attribution == "" || src.Licence == "" || src.LicenceURL == "" {
			t.Errorf("tile source %s lacks attribution/licence", name)
		}
		for _, banned := range []string{"google.com", "virtualearth.net", "arcgisonline.com"} {
			if strings.Contains(src.URLFormat, banned) {
				t.Errorf("tile source %s copies from %s, whose terms forbid bulk download", name, banned)
			}
		}
		host := strings.TrimPrefix(src.URLFormat, "https://")
		host = host[:strings.Index(host, "/")]
		if LicenseForHost(host) == nil {
			t.Errorf("tile source %s host %s not in the licence register", name, host)
		}
	}
	if _, ok := TileSources[defaultTileSource]; !ok {
		t.Errorf("defaultTileSource %q not in TileSources", defaultTileSource)
	}
}

func TestLicensesEndpoints(t *testing.T) {
	s := &Server{}
	rec := httptest.NewRecorder()
	s.HandleAPILicenses(rec, httptest.NewRequest("GET", "/api/licenses", nil))
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"eox-s2cloudless"`) {
		t.Fatalf("/api/licenses: %d %s", rec.Code, rec.Body.String()[:200])
	}
	rec = httptest.NewRecorder()
	s.HandleLicensesPage(rec, httptest.NewRequest("GET", "/licenses", nil))
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), "Sentinel-2 cloudless") {
		t.Fatalf("/licenses: %d", rec.Code)
	}
	if !isPublicPath("/licenses") || !isPublicPath("/api/licenses") {
		t.Error("licence pages must be public: an attribution behind a password is not an attribution")
	}
}
