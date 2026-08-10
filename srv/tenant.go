package srv

import (
	"os"
	"strings"
	"sync"
)

// Tenants: who may see patrol data.
//
// Patrol effort (effort_data, subcell_visits, gpx_uploads, track_points and
// everything learned from them) is *client* data: it belongs to the rangers who
// uploaded it, not to everyone holding an alpha password. The scoping key is
// the `env` column that already exists on every one of those tables — this file
// turns it from "prod vs the test sandbox" into a real per-password tenant.
//
// Nothing new is invented for the read path: every query that already filtered
// `env = RequestEnv(r)` becomes tenant-correct for free, and the response cache
// key (cacheKey) already carries RequestEnv, so no cross-tenant body can be
// served from it.
//
// The mapping lives in PASSWORD_ENVS (env var or secrets.env):
//
//	PASSWORD_ENVS=client-a:prod,client-c:prod,client-b:prod,test2026:test
//
// A password that is NOT listed gets its own private tenant (`pw_<ref8>`), so
// adding an access password can never silently widen access to somebody else's
// patrols — the failure mode is "I see no patrol data", not "I see yours".
//
// Legacy/dev fallback: when PASSWORD_ENVS is absent entirely, the historical
// behaviour applies (test2026 -> "test", anything else -> "prod"), which is
// what a fresh checkout and the test suite expect.
const (
	// clientTenant is the env value the existing 28k effort_data /
	// gpx_uploads rows were written under. It is a tenant name like any
	// other; it is spelled "prod" only because that is what is on disk.
	clientTenant = "prod"
	// sandboxTenant is the shared demo password's tenant (test2026).
	sandboxTenant = "test"
)

var (
	tenantOnce sync.Once
	tenantMap  map[string]string // password -> env
	tenantSet  bool              // PASSWORD_ENVS was configured
)

func loadTenants() {
	tenantOnce.Do(func() {
		raw := os.Getenv("PASSWORD_ENVS")
		if raw == "" {
			raw = secretsEnv("PASSWORD_ENVS")
		}
		if raw == "" {
			return
		}
		tenantSet = true
		tenantMap = map[string]string{}
		for _, pair := range strings.Split(raw, ",") {
			pwd, env, ok := strings.Cut(strings.TrimSpace(pair), ":")
			if !ok {
				continue
			}
			pwd, env = strings.TrimSpace(pwd), strings.TrimSpace(env)
			if pwd != "" && env != "" {
				tenantMap[pwd] = env
			}
		}
	})
}

// tenantForPwd maps an access password to its env tenant.
func tenantForPwd(pwd string) string {
	if pwd == "" {
		return clientTenant
	}
	loadTenants()
	if !tenantSet {
		// Legacy single-tenant install.
		if pwd == "test2026" {
			return sandboxTenant
		}
		return clientTenant
	}
	if env, ok := tenantMap[pwd]; ok {
		return env
	}
	// Fail closed: unknown password -> its own tenant, which holds nothing.
	return "pw_" + principalRef(pwd)[:8]
}

// tenantHasPatrol reports whether an env tenant owns any patrol effort. Cached
// per tenant for the process lifetime is deliberately NOT done: a first upload
// must light the UI up without a restart, and the query is one indexed EXISTS.
func (s *Server) tenantHasPatrol(env string) bool {
	if s == nil || s.DB == nil {
		return false
	}
	var one int
	err := s.DB.QueryRow(`SELECT 1 FROM effort_data WHERE env = ? LIMIT 1`, env).Scan(&one)
	return err == nil
}

// pwdForTenant returns a configured access password belonging to env, so a
// server-side job (autofetch) can act *as* that tenant against its own HTTP
// API. Returns "" when no configured password maps to env -- callers must then
// refuse, because filing a client's tracks under another tenant is worse than
// not fetching them.
func pwdForTenant(env string) string {
	if env == "" {
		env = clientTenant
	}
	for _, p := range validPasswords {
		p = strings.TrimSpace(p)
		if p != "" && tenantForPwd(p) == env {
			return p
		}
	}
	return ""
}
