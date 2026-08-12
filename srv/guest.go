package srv

// The guest session: what a capability short link authenticates as.
//
// Read srv/shortlink.go first — it explains WHY this exists (sharing an AOI
// without handing over the access password) and why a PIN on a
// password-carrying link was rejected instead. This file is the enforcement,
// and it is deliberately the only place a guest can be admitted.
//
// TWO INVARIANTS, BOTH MECHANICAL RATHER THAN BY CONVENTION.
//
//  1. READ ONLY. GET/HEAD only, and never the admin surface, the upload
//     surface, or the endpoint that mints links. A capability that can mint
//     capabilities is a password with extra steps, and a delegated read that
//     can write is not a delegated read.
//
//  2. NO ESCALATION OF SIGHT. A guest borrows the creator's principal and
//     tenant and nothing else. It never becomes RequestPwd(), so every
//     existing `env = RequestEnv(r)` filter, every aoiGate, and every
//     visibilityFingerprint keeps working unchanged — which is the whole
//     reason this is a principal rather than a new axis of permission.

import (
	"context"
	"net/http"
	"net/url"
	"strings"
)

const guestCookie = "guest_link"

type guestCtxKey struct{}

// GuestFromRequest returns the guest capability this request is riding on, or
// nil for an ordinary password session. Handlers use it to answer "may I write
// / may I show the admin panel", and the page uses it to label the session
// chip with the link's name instead of a password.
func GuestFromRequest(r *http.Request) *GuestSession {
	if r == nil {
		return nil
	}
	g, _ := r.Context().Value(guestCtxKey{}).(*GuestSession)
	return g
}

func withGuest(r *http.Request, g *GuestSession) *http.Request {
	return r.WithContext(context.WithValue(r.Context(), guestCtxKey{}, g))
}

// guestMayRead decides what a capability link is allowed to reach.
//
// A DENY-LIST WOULD BE WRONG HERE and it is worth saying why, because it is
// the obvious first draft: this app grows endpoints weekly, and a deny-list
// silently admits every new one. The rule is therefore stated as a method
// check plus a small set of prefixes that are categorically off-limits, and
// anything genuinely new that must be closed has to be closed here, in one
// place a reviewer can read in ten seconds.
func guestMayRead(r *http.Request) bool {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		return false
	}
	p := r.URL.Path
	switch {
	case strings.HasPrefix(p, "/admin"),
		strings.HasPrefix(p, "/api/admin"),
		strings.HasPrefix(p, "/upload"),
		strings.HasPrefix(p, "/api/upload"),
		strings.HasPrefix(p, "/api/shortlink"),
		strings.HasPrefix(p, "/api/shortlinks"),
		strings.HasPrefix(p, "/api/aois/estimate"),
		p == "/register", p == "/login":
		return false
	}
	return true
}

// guestAuth is consulted by PasswordMiddleware before it shows the password
// form: a live capability admits the request, a dead or misused one falls
// through to the ordinary gate rather than erroring, because "your link
// expired, here is the password box" is a better dead end than a 403.
func (s *Server) guestAuth(r *http.Request) (*http.Request, bool) {
	slug := ""
	if c, err := r.Cookie(guestCookie); err == nil {
		slug = c.Value
	}
	if slug == "" {
		return r, false
	}
	g := s.LookupGuest(slug)
	if g == nil || !guestMayRead(r) {
		return r, false
	}
	return withGuest(r, g), true
}

// ── WHAT A GUEST MAY SEE (as opposed to what it may do) ────────────────────
//
// guestMayRead above answers "may this request happen at all". This answers a
// different question: of the things the creator can see, which ones travel
// with the link. Read-only is not the same as harmless — a capability borrows
// the creator's principal, so without a scope a link shared to show a fire
// scar also shows the recipient every patrol track the account owns. The
// sender never chose that and had no way to notice it.
//
// Almost nothing belongs on this list. Fires, deforestation, settlements,
// geology, the basemap and the historical sheets are the same public data for
// everybody, and a guest is welcome to toggle them, zoom, and play — an
// interactive map that punishes exploration is a screenshot with extra steps.
// A layer earns an entry here only when it is somebody's people or somebody's
// private geometry.
//
// The set is an ALLOW-LIST for the reason stated in the migration: a deny-list
// would retroactively widen every link ever minted the day a new sensitive
// layer ships.
const (
	// ScopePatrol — patrol effort: grid pixels, the animator's effort frames,
	// distances, and the patrol columns of an export. Ranger movement,
	// uploaded by one account and scoped to it (srv/tenant.go).
	ScopePatrol = "patrol"
)

// scopedLayers maps a scope name to the SHARE-URL LAYER that turns it on. The
// scope of a new link is derived from the view being shared, so that a sender
// grants what they were looking at and nothing else — there is no separate
// switch to forget, and no way for the permission to drift from the picture.
//
// A layer named here must be one that buildShareUrl() puts in `layers=`.
var scopedLayers = map[string]string{
	ScopePatrol: "pixels",
}

// defaultOnLayers: layers that are ON when `layers=` is absent. buildShareUrl()
// omits the parameter while the toggles sit at their defaults, so "no layers
// param" is a positive statement about the view, not missing information.
//
// If this ever disagrees with the frontend the failure must be the quiet
// direction — a guest seeing less — so the parse below treats an unreadable
// URL as an empty scope.
var defaultOnLayers = map[string]bool{"pixels": true}

// scopeFromURL derives the scope set for a link from the view it points at.
func scopeFromURL(raw string) string {
	u, err := url.Parse(raw)
	if err != nil {
		return ""
	}
	vals, present := u.Query()["layers"]
	on := func(layer string) bool {
		if !present || len(vals) == 0 {
			return defaultOnLayers[layer]
		}
		for _, n := range strings.Split(vals[0], ",") {
			if strings.TrimSpace(n) == layer {
				return true
			}
		}
		return false
	}
	out := []string{}
	// Sorted by construction (iterate the constant list, not the map) so the
	// stored value is stable and two identical views produce one string.
	for _, name := range []string{ScopePatrol} {
		if layer, ok := scopedLayers[name]; ok && on(layer) {
			out = append(out, name)
		}
	}
	return strings.Join(out, ",")
}

// GuestHasScope reports whether this request's guest link carries a capability.
// An ordinary (non-guest) session has every scope: it is the account itself.
func GuestHasScope(r *http.Request, scope string) bool {
	g := GuestFromRequest(r)
	if g == nil {
		return true
	}
	for _, s := range strings.Split(g.Scope, ",") {
		if strings.TrimSpace(s) == scope {
			return true
		}
	}
	return false
}

// noSuchTenant is an env value no row has ever been written under.
//
// This is how a scope is ENFORCED WITHOUT TOUCHING FORTY CALL SITES. Patrol
// data is already filtered by `e.env = ?` everywhere it is read — that is the
// tenant mechanism from srv/tenant.go. Handing a scope-less guest a tenant name
// that owns nothing makes every one of those queries answer empty, correctly,
// without a single query learning what a guest is. A new restricted layer that
// is likewise env-scoped costs one wrapper like PatrolEnv below.
const noSuchTenant = "guest_scope_denied"

// PatrolEnv — use INSTEAD OF RequestEnv in any query that reads patrol effort
// (effort_data, subcell_visits, gpx_uploads, track_points).
//
// grep -n 'effort_data' srv/*.go — every one of those should be reading this.
func PatrolEnv(r *http.Request) string {
	if !GuestHasScope(r, ScopePatrol) {
		return noSuchTenant
	}
	return RequestEnv(r)
}
