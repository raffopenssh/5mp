package srv

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// The demo password must not see geology: an explanatory empty catalogue,
// and 403 on every other geology route. A non-sandbox tenant passes through.
func TestGeoMapGateWithholdsFromSandbox(t *testing.T) {
	sandboxReq := func(path string) *http.Request {
		r := httptest.NewRequest("GET", path, nil)
		r.AddCookie(&http.Cookie{Name: "access_pwd", Value: "test2026"})
		return r
	}
	if tenantForPwd("test2026") != sandboxTenant {
		t.Skip("test2026 is not the sandbox password in this configuration")
	}
	s := &Server{}
	rec := httptest.NewRecorder()
	s.HandleAPIGeoMap(rec, sandboxReq("/api/geomap"))
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"withheld":true`) || !strings.Contains(rec.Body.String(), `"sheets":[]`) {
		t.Fatalf("sandbox catalogue should be empty and say why: %d %s", rec.Code, rec.Body.String())
	}
	called := false
	gated := geoMapGate(func(w http.ResponseWriter, r *http.Request) { called = true })
	rec = httptest.NewRecorder()
	gated(rec, sandboxReq("/api/geomap/car/1/2/3.pbf"))
	if rec.Code != http.StatusForbidden || called {
		t.Fatalf("sandbox tile request should be 403 without reaching the handler: code=%d called=%v", rec.Code, called)
	}
	if cc := rec.Header().Get("Cache-Control"); !strings.Contains(cc, "private") {
		t.Errorf("a withheld answer must not be cacheable publicly: %q", cc)
	}
	// A different tenant passes.
	r := httptest.NewRequest("GET", "/api/geomap/car/1/2/3.pbf", nil)
	r.AddCookie(&http.Cookie{Name: "access_pwd", Value: "not-the-sandbox-password-zz"})
	called = false
	gated(httptest.NewRecorder(), r)
	if geoMapWithheld(r) || !called {
		t.Errorf("non-sandbox tenant must reach the handler (withheld=%v called=%v)", geoMapWithheld(r), called)
	}
}
