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
