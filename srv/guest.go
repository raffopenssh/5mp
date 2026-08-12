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
	"time"
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
	// The date clamp happens HERE, on the way in, so no handler has to know
	// that a restricted window exists (see GuestWindow below).
	return withGuest(clampGuestQuery(r, g), g), true
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

// ── WHEN A GUEST MAY LOOK AT (as opposed to what it opens on) ─────────────
//
// A share URL always carries a time window, but carrying it is not the same as
// RESTRICTING it: the recipient of "the Chinko fire season, May to August" can
// drag the slider to last week the moment the map loads, and nothing in the
// link ever suggested they could not. Usually that is right — an interactive
// map that punishes exploration is a screenshot with extra steps (the same
// argument that keeps fires and geology out of the scope list). Sometimes it
// is exactly wrong: a link minted for one incident, one season, or one
// reporting period is ABOUT those dates, and the sender means the key to open
// nothing else.
//
// So the window is a property of the capability, stored on the row, decided
// once at creation from the view being shared, and enforced HERE — once, in
// the middleware — rather than at the forty-odd handlers that read `from`/`to`:
//
//	grep -c 'Query().Get("from")' srv/*.go
//
// Rewriting the query is legitimate precisely because it is the same shape of
// answer: every one of those handlers already accepts any window the caller
// asks for, so a narrowed window is an ordinary request, not a special case.
// The alternative — a check per handler — is invariant 5's mistake in a new
// costume: the one handler that forgets is the one that leaks, and nothing
// says which one that is.
//
// IT CLAMPS, IT DOES NOT REFUSE. A guest who drags the slider outside the
// window gets a map with less on it, not a screenful of failed panels: an
// error would be a worse experience AND a worse secret, since "403 for June"
// tells the holder there is a June worth having. Out-of-window entirely
// collapses to a zero-length range, which every query answers empty.

// ── DERIVING THE WINDOW FROM THE VIEW ───────────────────────────────
//
// Like scope, a locked window is taken from the picture being shared rather
// than typed into a second form: the sender grants what they were looking at.
//
// A share URL says WHEN in one of two ways, and the difference is the whole
// subject of this feature. `from=&to=` is a fact and always shows the same
// map. `date_preset=90d` is a rule, resolved against whoever's clock opens it,
// so it shows a different map next month — which is the right thing for a
// standing bookmark and the wrong thing for a citation.
//
// A LOCK IS ONLY MEANINGFUL ON A FACT. The dialog therefore freezes the URL
// before asking to lock it, and this function resolves a preset anyway rather
// than storing an empty window: an empty window means "unrestricted", so
// failing to parse would silently grant MORE than was asked for — the failure
// direction 053 spent a paragraph forbidding.
var presetDays = map[string]int{"td": 1, "3d": 3, "7d": 7, "14d": 14, "30d": 30, "90d": 90}

func datesFromURL(raw string, now time.Time) (string, string) {
	u, err := url.Parse(raw)
	if err != nil {
		return "", ""
	}
	q := u.Query()
	iso := func(t time.Time) string { return t.Format("2006-01-02") }
	valid := func(v string) string {
		if _, err := time.Parse("2006-01-02", v); err != nil {
			return ""
		}
		return v
	}
	from, to := valid(q.Get("from")), valid(q.Get("to"))
	if from != "" || to != "" {
		if from == "" {
			from = "2020-01-01" // the slider's own floor (globe.html)
		}
		if to == "" {
			to = iso(now)
		}
		if from > to {
			from, to = to, from
		}
		return from, to
	}
	switch p := q.Get("date_preset"); {
	case p == "cmo":
		return iso(time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, now.Location())), iso(now)
	case presetDays[p] > 0:
		return iso(now.AddDate(0, 0, -(presetDays[p] - 1))), iso(now)
	}
	return "", ""
}

// GuestWindow returns the dates this request's capability is confined to.
// ok is false for an ordinary session and for a key that was not date-locked —
// both of which may look at anything the account can see.
func GuestWindow(r *http.Request) (from, to string, ok bool) {
	g := GuestFromRequest(r)
	if g == nil || g.DateFrom == "" || g.DateTo == "" {
		return "", "", false
	}
	return g.DateFrom, g.DateTo, true
}

// clampISO narrows one requested ISO date into [lo, hi]. An absent request
// means "the whole window", which is the widest thing a locked key may ask
// for, not the widest thing the account can see.
func clampISO(v, lo, hi string) string {
	if v == "" {
		return ""
	}
	if len(v) > 10 {
		v = v[:10]
	}
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// clampGuestQuery rewrites the date parameters of a request riding on a
// date-locked key. Returns the request unchanged when there is nothing to do,
// and never mutates the caller's URL in place — a middleware that edits the
// request it was handed makes every later reader of r.URL a liar.
func clampGuestQuery(r *http.Request, g *GuestSession) *http.Request {
	if g == nil || g.DateFrom == "" || g.DateTo == "" {
		return r
	}
	q := r.URL.Query()
	from, to := q.Get("from"), q.Get("to")
	// An unstated bound becomes the window's own edge: a handler defaulting
	// "no from" to 2020 would walk straight out of the window, and the default
	// is written in the handler, not here.
	if from == "" {
		from = g.DateFrom
	}
	if to == "" {
		to = g.DateTo
	}
	from = clampISO(from, g.DateFrom, g.DateTo)
	to = clampISO(to, g.DateFrom, g.DateTo)
	if from > to {
		from = to
	}
	if from == q.Get("from") && to == q.Get("to") {
		return r
	}
	q.Set("from", from)
	q.Set("to", to)
	r2 := r.Clone(r.Context())
	u := *r.URL
	u.RawQuery = q.Encode()
	r2.URL = &u
	return r2
}

// ClampGuestDates narrows a window a handler computed for itself, for the
// handlers whose time range is NOT a `from`/`to` pair the middleware can see —
// today that is the NRT fire endpoint, whose `days=28` means "ending now".
// "Now" is the one thing a locked key must not be able to reach past.
func ClampGuestDates(r *http.Request, start, end time.Time) (time.Time, time.Time) {
	from, to, ok := GuestWindow(r)
	if !ok {
		return start, end
	}
	lo, err1 := time.Parse("2006-01-02", from)
	hi, err2 := time.Parse("2006-01-02", to)
	if err1 != nil || err2 != nil {
		return start, end
	}
	hi = hi.Add(24*time.Hour - time.Second)
	if start.Before(lo) {
		start = lo
	}
	if end.After(hi) {
		end = hi
	}
	if start.After(end) {
		start = end
	}
	return start, end
}
