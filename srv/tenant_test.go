package srv

import (
	"net/http"
	"net/http/httptest"
	"os"
	"sync"
	"testing"
)

// resetTenants lets a test reconfigure PASSWORD_ENVS despite the sync.Once.
func resetTenants(t *testing.T, value string) {
	t.Helper()
	old, had := os.LookupEnv("PASSWORD_ENVS")
	t.Cleanup(func() {
		if had {
			os.Setenv("PASSWORD_ENVS", old)
		} else {
			os.Unsetenv("PASSWORD_ENVS")
		}
		tenantOnce = sync.Once{}
		tenantMap, tenantSet = nil, false
	})
	os.Setenv("PASSWORD_ENVS", value)
	tenantOnce = sync.Once{}
	tenantMap, tenantSet = nil, false
}

// A password that is not listed must NOT land in the client tenant: the whole
// point is that adding an access password cannot silently hand it somebody
// else's patrol pixels.
func TestTenantUnlistedPasswordIsIsolated(t *testing.T) {
	resetTenants(t, "client-a:prod,client-c:prod,client-b:prod,test2026:test")
	for _, p := range []string{"client-a", "client-c", "client-b"} {
		if got := tenantForPwd(p); got != clientTenant {
			t.Errorf("tenantForPwd(%q) = %q, want %q", p, got, clientTenant)
		}
	}
	if got := tenantForPwd("test2026"); got != sandboxTenant {
		t.Errorf("sandbox tenant = %q, want %q", got, sandboxTenant)
	}
	other := tenantForPwd("unlisted-pwd")
	if other == clientTenant || other == sandboxTenant {
		t.Fatalf("unlisted password got shared tenant %q", other)
	}
	if got := tenantForPwd("unlisted-pwd"); got != other {
		t.Errorf("tenant not stable: %q vs %q", got, other)
	}
	if tenantForPwd("someone-else") == other {
		t.Error("two different unlisted passwords share a tenant")
	}
}

// Without PASSWORD_ENVS the historical single-tenant behaviour must hold, or a
// fresh checkout (and the test suite) sees an empty map.
func TestTenantLegacyFallback(t *testing.T) {
	resetTenants(t, "")
	os.Unsetenv("PASSWORD_ENVS")
	if got := tenantForPwd("anything"); got != clientTenant {
		t.Errorf("legacy tenant = %q, want %q", got, clientTenant)
	}
	if got := tenantForPwd("test2026"); got != sandboxTenant {
		t.Errorf("legacy test tenant = %q, want %q", got, sandboxTenant)
	}
}

// isTestEnv means "outside the client tenant", not "is test2026".
func TestRequestEnvAndIsTestEnv(t *testing.T) {
	resetTenants(t, "client-a:prod,test2026:test")
	oldPw := validPasswords
	validPasswords = []string{"client-a", "test2026", "unlisted-pwd"}
	t.Cleanup(func() { validPasswords = oldPw })

	req := func(pwd string) *http.Request {
		r := httptest.NewRequest("GET", "/api/grid?pwd="+pwd, nil)
		return r
	}
	if RequestEnv(req("client-a")) != clientTenant {
		t.Error("client password not in client tenant")
	}
	if isTestEnv(req("client-a")) {
		t.Error("client password treated as non-client")
	}
	if !isTestEnv(req("test2026")) || !isTestEnv(req("unlisted-pwd")) {
		t.Error("non-client tenants must be excluded from client-derived data")
	}
	// A cookie is equivalent to the query param.
	r := httptest.NewRequest("GET", "/api/grid", nil)
	r.AddCookie(&http.Cookie{Name: "access_pwd", Value: "client-a"})
	if RequestEnv(r) != clientTenant {
		t.Error("cookie auth did not resolve the tenant")
	}
}
