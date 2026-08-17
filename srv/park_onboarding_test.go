package srv

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"srv.exe.dev/srv/areas"
)

// Onboarding requests are scoped to the login that made them: another tenant
// must not see them in the list and must get 404 (not 403) on cancel — an id
// must not be an oracle. The WORK is shared: a second tenant requesting the
// same park joins the existing request instead of spawning a duplicate
// multi-hour ingest, and the first tenant leaving must not cancel it.
func TestOnboardingScopedToCaller(t *testing.T) {
	resetTenants(t, "pw-alpha:prod,pw-beta:beta")
	oldPw := validPasswords
	validPasswords = []string{"pw-alpha", "pw-beta"}
	t.Cleanup(func() { validPasswords = oldPw })

	tempDB := filepath.Join(t.TempDir(), "onboard.sqlite3")
	server, err := New(tempDB, "test-host")
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	server.WDPAIndex = &areas.WDPAIndex{ByID: map[int]*areas.WDPAIndexEntry{
		99001: {WDPAID: 99001, Name: "Testland", Country: "Testonia", CountryCode: "TST"},
	}}

	call := func(h http.HandlerFunc, method, url string) (int, map[string]any) {
		t.Helper()
		r := httptest.NewRequest(method, url, nil)
		w := httptest.NewRecorder()
		h(w, r)
		var m map[string]any
		json.Unmarshal(w.Body.Bytes(), &m)
		return w.Code, m
	}
	list := func(pwd string) []map[string]any {
		t.Helper()
		r := httptest.NewRequest("GET", "/api/onboarding?pwd="+pwd, nil)
		w := httptest.NewRecorder()
		server.HandleAPIOnboardingStatus(w, r)
		var out []map[string]any
		json.Unmarshal(w.Body.Bytes(), &out)
		return out
	}

	// Unauthenticated caller cannot request.
	if code, _ := call(server.HandleAPIRequestOnboard, "POST", "/api/onboarding/request?wdpa_id=99001"); code != http.StatusForbidden {
		t.Fatalf("unauthenticated request: code=%d, want 403", code)
	}

	// Alpha requests the park.
	if code, m := call(server.HandleAPIRequestOnboard, "POST", "/api/onboarding/request?wdpa_id=99001&pwd=pw-alpha"); code != 200 || m["status"] != "requested" {
		t.Fatalf("alpha request: code=%d resp=%v", code, m)
	}
	if n := len(list("pw-alpha")); n != 1 {
		t.Fatalf("alpha list: %d rows, want 1", n)
	}
	// Beta sees nothing and cannot cancel.
	if n := len(list("pw-beta")); n != 0 {
		t.Fatalf("beta list leaked alpha's request: %d rows", n)
	}
	if code, _ := call(server.HandleAPICancelOnboard, "POST", "/api/onboarding/cancel?wdpa_id=99001&pwd=pw-beta"); code != http.StatusNotFound {
		t.Fatalf("beta cancel of alpha's request: code=%d, want 404", code)
	}

	// Beta requests the same park -> joins the shared row, no duplicate.
	if code, m := call(server.HandleAPIRequestOnboard, "POST", "/api/onboarding/request?wdpa_id=99001&pwd=pw-beta"); code != 200 || m["status"] != "already_requested" {
		t.Fatalf("beta request: code=%d resp=%v", code, m)
	}
	var nreq int
	server.DB.QueryRow(`SELECT COUNT(*) FROM park_onboarding_requests WHERE wdpa_id=99001`).Scan(&nreq)
	if nreq != 1 {
		t.Fatalf("request rows = %d, want 1 (shared)", nreq)
	}
	if n := len(list("pw-beta")); n != 1 {
		t.Fatalf("beta list after subscribing: %d rows, want 1", n)
	}

	// Alpha cancels: the request must SURVIVE for beta.
	if code, m := call(server.HandleAPICancelOnboard, "POST", "/api/onboarding/cancel?wdpa_id=99001&pwd=pw-alpha"); code != 200 || m["status"] != "cancelled" {
		t.Fatalf("alpha cancel: code=%d resp=%v", code, m)
	}
	server.DB.QueryRow(`SELECT COUNT(*) FROM park_onboarding_requests WHERE wdpa_id=99001`).Scan(&nreq)
	if nreq != 1 {
		t.Fatalf("alpha's cancel deleted beta's request")
	}
	if n := len(list("pw-alpha")); n != 0 {
		t.Fatalf("alpha still sees the request after cancel")
	}

	// Beta cancels too: last subscriber out deletes the pending row.
	if code, _ := call(server.HandleAPICancelOnboard, "POST", "/api/onboarding/cancel?wdpa_id=99001&pwd=pw-beta"); code != 200 {
		t.Fatalf("beta cancel failed")
	}
	server.DB.QueryRow(`SELECT COUNT(*) FROM park_onboarding_requests WHERE wdpa_id=99001`).Scan(&nreq)
	if nreq != 0 {
		t.Fatalf("pending row survived last subscriber's cancel")
	}
}

// A ready park is shared data: one subscriber leaving must only unsubscribe;
// the LAST subscriber leaving schedules removal, and a returning requester
// on a remove_requested row revives it.
func TestOnboardingReadySharedRemoval(t *testing.T) {
	resetTenants(t, "pw-alpha:prod,pw-beta:beta")
	oldPw := validPasswords
	validPasswords = []string{"pw-alpha", "pw-beta"}
	t.Cleanup(func() { validPasswords = oldPw })

	tempDB := filepath.Join(t.TempDir(), "onboard2.sqlite3")
	server, err := New(tempDB, "test-host")
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	// Seed a ready request with two subscribers.
	res, err := server.DB.Exec(`INSERT INTO park_onboarding_requests
		(wdpa_id, name, country, country_code, env, status, park_id)
		VALUES (99002, 'Sharedland', 'Testonia', 'TST', 'prod', 'ready', 'TST_Sharedland')`)
	if err != nil {
		t.Fatalf("seed: %v", err)
	}
	reqID, _ := res.LastInsertId()
	for _, p := range []string{"pw-alpha", "pw-beta"} {
		server.DB.Exec(`INSERT INTO park_onboarding_subscribers (request_id, pwd_ref, env) VALUES (?,?,?)`,
			reqID, principalRef(p), "prod")
	}

	cancel := func(pwd string) (int, map[string]any) {
		t.Helper()
		r := httptest.NewRequest("POST", "/api/onboarding/cancel?wdpa_id=99002&pwd="+pwd, nil)
		w := httptest.NewRecorder()
		server.HandleAPICancelOnboard(w, r)
		var m map[string]any
		json.Unmarshal(w.Body.Bytes(), &m)
		return w.Code, m
	}
	status := func() string {
		var s string
		server.DB.QueryRow(`SELECT status FROM park_onboarding_requests WHERE id=?`, reqID).Scan(&s)
		return s
	}

	if code, m := cancel("pw-alpha"); code != 200 || m["status"] != "unsubscribed" {
		t.Fatalf("alpha cancel of shared ready park: code=%d resp=%v (park must stay)", code, m)
	}
	if s := status(); s != "ready" {
		t.Fatalf("status after first unsubscribe = %q, want ready", s)
	}
	if code, m := cancel("pw-beta"); code != 200 || m["status"] != "remove_scheduled" {
		t.Fatalf("last subscriber cancel: code=%d resp=%v", code, m)
	}
	if s := status(); s != "remove_requested" {
		t.Fatalf("status = %q, want remove_requested", s)
	}

	// Beta reconsiders: cancel again toggles back to ready and re-subscribes.
	if code, m := cancel("pw-beta"); code != 200 || m["status"] != "remove_cancelled" {
		t.Fatalf("toggle-back: code=%d resp=%v", code, m)
	}
	if s := status(); s != "ready" {
		t.Fatalf("status after toggle-back = %q, want ready", s)
	}
	var nsub int
	server.DB.QueryRow(`SELECT COUNT(*) FROM park_onboarding_subscribers WHERE request_id=?`, reqID).Scan(&nsub)
	if nsub != 1 {
		t.Fatalf("subscribers after toggle-back = %d, want 1 (beta re-subscribed)", nsub)
	}
}
