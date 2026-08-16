package srv

// Short links: the shareable NAME of a view — and, where the sender needs it,
// a read-only capability that opens that view without handing over the
// password.
//
// ---------------------------------------------------------------------------
// PART ONE: THE NAME
// ---------------------------------------------------------------------------
//
// Every "Copy link" in this app produced 300-500 characters of state
// (lat/lng/z, dates, layer set, pinned ids, the selected place, which panel,
// which admin tab). That is a correct link and an unusable one: it cannot be
// read aloud, footnoted in a report, or typed off a second screen, and in a
// phone's clipboard preview it wraps to six lines. /s/{slug} is the same view
// with a name, and the name is editable, because you copy a link while looking
// at the thing and name it a second later while sending it.
//
// A named slug is not a credential: `url` is stored with `pwd` STRIPPED and
// /s/{slug} resolves behind the ordinary password gate. Slugs are meant to be
// guessable ("virunga-fires"), so a named slug that carried a way in would
// make guessing the name a way in.
//
// ---------------------------------------------------------------------------
// PART TWO: SHARING WITHOUT SHARING THE PASSWORD
// ---------------------------------------------------------------------------
//
// The request behind "put the password in the link" is: let a donor, a
// ministry or a colleague see this AOI without becoming us. Sending them the
// access password does the opposite of what is wanted — it is the same secret
// for everyone, it grants writing as well as reading, it cannot be taken back
// from one recipient, and after two forwards nobody can say who holds it.
//
// A six-digit PIN on top of a password-carrying link does not repair any of
// that. The link and the digits travel through the same chat window, six
// digits is a million guesses, and what is behind them is still the password.
// It would add a page and change nothing that matters, so it is not here.
//
// What is here is the thing the principals/aoi_grants tables were built for
// ("a principal is a password today, a user or an NGO tomorrow"): a GUEST
// link, a capability with five properties a shared password cannot have.
//
//	  the slug IS the secret   16 chars of crypto/rand over a 27-symbol
//	                           alphabet (~76 bits). Never renameable: a
//	                           memorable capability is a guessable one.
//	  read only                enforced in PasswordMiddleware, not by
//	                           convention: GET/HEAD only, and no /admin,
//	                           /api/admin, upload, AOI write or link minting.
//	                           A capability that can mint capabilities is a
//	                           password.
//	  scoped                   acts as the creator's principal within the
//	                           creator's tenant, so exactly what they can see
//	                           becomes visible, and nothing else.
//	  expiring                 a default life, because a link nobody revokes
//	                           is a password with extra steps.
//	  revocable + counted      one Clear button per link, plus hits and
//	                           last-seen, so "has anyone opened it" and "stop
//	                           this one" are both answerable.
//
// The guest never learns the password: it is not in this table, not in the
// URL, and the session chip names the link instead of a password.
//
// HONEST LIMIT. A guest slug is a bearer token held in cleartext, because the
// admin sheet must be able to show and re-copy it. A database leak therefore
// leaks these links — but that same database holds the data they grant sight
// of, so the token adds no exposure. What it must never do is grant MORE than
// that, which is why the read-only rule lives in the middleware.
//
// See db/migrations/052-short-links.sql.

import (
	"crypto/rand"
	"database/sql"
	"encoding/json"
	"io"
	"math/big"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

// guestTTL: long enough for a report to be read next week, short enough that
// an unrevoked link does not become a permanent second password.
const guestTTL = 30 * 24 * time.Hour

// guestMaxTTL caps what a caller may ask for. A capability that outlives the
// reason it was issued is a second password with extra steps, and "never
// expires" is the option that turns one careless share into a permanent one --
// so it is not offered, and a request for more than a year is clamped rather
// than refused (refusing would only teach the caller to ask for exactly 365).
const guestMaxTTL = 365 * 24 * time.Hour

// Tags (a link's purpose groups) live in srv/shortlink_tags.go.

// gpkgDownloadTarget extracts the job id from a short-link target that points
// straight at an export file, or "" for any other URL.
func gpkgDownloadTarget(target string) string {
	const pre = "/api/geopackage/"
	if !strings.HasPrefix(target, pre) {
		return ""
	}
	rest := strings.TrimPrefix(target, pre)
	id, tail, ok := strings.Cut(rest, "/")
	if !ok || id == "" {
		return ""
	}
	if tail != "download" && !strings.HasPrefix(tail, "download?") {
		return ""
	}
	return id
}

// retainGeoPackageForLink keeps the invariant "a file a live guest link points
// at outlives the link": the job's expiry is pushed OUT to the link's, never
// pulled in. Called at mint time and again at extend time; the sweeper holds
// the same line for links created before this existed.
func (s *Server) retainGeoPackageForLink(target, linkExpires string) {
	id := gpkgDownloadTarget(target)
	if id == "" || linkExpires == "" {
		return
	}
	s.DB.Exec(`UPDATE geopackage_jobs SET expires_at = ?
		WHERE id = ? AND (expires_at IS NULL OR expires_at = '' OR expires_at < ?)`,
		linkExpires, id, linkExpires)
}

// guestLife turns a requested number of days into a lifetime.
func guestLife(days int) time.Duration {
	if days <= 0 {
		return guestTTL
	}
	d := time.Duration(days) * 24 * time.Hour
	if d > guestMaxTTL {
		return guestMaxTTL
	}
	return d
}

// withScope adds or removes one capability from a scope set, keeping it
// sorted-by-construction so two identical grants produce one string.
func withScope(scope, name string, on bool) string {
	out := []string{}
	for _, known := range []string{ScopePatrol} {
		has := false
		for _, s := range strings.Split(scope, ",") {
			if strings.TrimSpace(s) == known {
				has = true
			}
		}
		if known == name {
			has = on
		}
		if has {
			out = append(out, known)
		}
	}
	return strings.Join(out, ",")
}

// decodeJSONBody reads a small JSON body. Capped: these endpoints are behind
// the password gate but not behind a rate limiter.
func decodeJSONBody(r *http.Request, v interface{}) error {
	return json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(v)
}

// A slug is lowercase because links get typed and said out loud, and case is
// neither audible nor reliably preserved by chat clients. 64 chars fits
// "chinko-fire-season-2025-report" and still stays on one line.
var shortSlugRe = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,63}$`)
var shortNonSlug = regexp.MustCompile(`[^a-z0-9]+`)

// Reserved: a slug must never shadow a real route, or renaming a link could
// take the site's own paths away from it.
var shortSlugReserved = map[string]bool{
	"api": true, "static": true, "admin": true, "login": true, "logout": true,
	"register": true, "upload": true, "s": true, "healthz": true, "new": true,
	"robots.txt": true, "sitemap.xml": true, "impressum": true, "datenschutz": true,
}

// shortSlugify turns whatever a person typed into a legal slug, or "".
// Deliberately lossy and silent: the field shows the result immediately, so
// the rule is demonstrated rather than explained.
func shortSlugify(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	s = shortNonSlug.ReplaceAllString(s, "-")
	s = strings.Trim(s, "-")
	if len(s) > 64 {
		s = strings.Trim(s[:64], "-")
	}
	if !shortSlugRe.MatchString(s) || shortSlugReserved[s] {
		return ""
	}
	return s
}

// The alphabet has no vowels (cannot spell a word by accident) and no 0/o/1/l
// (cannot be misread off a screen by someone on the phone).
const shortAlpha = "23456789bcdfghjkmnpqrstvwxz"

func shortRandom(n int) string {
	out := make([]byte, n)
	max := big.NewInt(int64(len(shortAlpha)))
	for i := range out {
		v, err := rand.Int(rand.Reader, max)
		if err != nil {
			return ""
		}
		out[i] = shortAlpha[v.Int64()]
	}
	return string(out)
}

// A NAME is 7 characters: short, and only ever an alias for something the
// password gate protects anyway.
func shortToken() string { return shortRandom(7) }

// A CAPABILITY is 16: ~76 bits, because here the slug is the whole secret.
// The "g-" prefix is not decoration — it tells an operator reading a log or a
// referrer that this URL is a credential, and it keeps the two kinds visibly
// distinct in the admin sheet.
func guestToken() string { return "g-" + shortRandom(16) }

// shortNormalizeURL accepts what the browser sends (absolute URL or path) and
// returns the path+query we will store, with `pwd` removed.
//
// The pwd strip is the security-relevant line in this file; everything else is
// convenience. An absolute URL pointing anywhere but this host is refused
// rather than silently rewritten — a silently rewritten link goes somewhere
// the sender never looked at.
func shortNormalizeURL(raw string, r *http.Request) (string, bool) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "", false
	}
	u, err := url.Parse(raw)
	if err != nil {
		return "", false
	}
	if u.Host != "" && r != nil && u.Host != r.Host {
		return "", false
	}
	if !strings.HasPrefix(u.Path, "/") || strings.HasPrefix(u.Path, "//") {
		return "", false
	}
	// A short link may not point at another short link: two hops is a loop
	// waiting to happen and buys nothing.
	if u.Path == "/s" || strings.HasPrefix(u.Path, "/s/") {
		return "", false
	}
	q := u.Query()
	q.Del("pwd")
	out := u.Path
	if enc := q.Encode(); enc != "" {
		out += "?" + enc
	}
	if u.Fragment != "" {
		out += "#" + u.Fragment
	}
	if len(out) > 4000 {
		return "", false
	}
	return out, true
}

type shortLink struct {
	Slug        string
	URL         string
	Kind        string
	Title       string
	Env         string
	Scope       string
	DateFrom    string
	DateTo      string
	Guest       bool
	PrincipalID int64
	Expired     bool
	Revoked     bool
}

// loadShortLink follows at most one alias hop.
func (s *Server) loadShortLink(slug string) (*shortLink, bool) {
	get := func(id string) (*shortLink, string, bool) {
		var l shortLink
		var alias, expires, revoked sql.NullString
		var pid sql.NullInt64
		var guest int
		err := s.DB.QueryRow(`SELECT slug, url, COALESCE(alias_of,''), kind, title, env,
			COALESCE(scope,''), COALESCE(date_from,''), COALESCE(date_to,''),
			guest, principal_id, expires_at, revoked_at
			FROM short_links WHERE slug = ?`, id).
			Scan(&l.Slug, &l.URL, &alias, &l.Kind, &l.Title, &l.Env, &l.Scope,
				&l.DateFrom, &l.DateTo, &guest, &pid, &expires, &revoked)
		if err != nil {
			return nil, "", false
		}
		l.Guest = guest == 1
		l.PrincipalID = pid.Int64
		l.Revoked = revoked.Valid && revoked.String != ""
		if expires.Valid && expires.String != "" {
			if t, err := time.Parse(time.RFC3339, expires.String); err == nil {
				l.Expired = time.Now().After(t)
			}
		}
		return &l, alias.String, true
	}
	l, alias, ok := get(slug)
	if !ok {
		return nil, false
	}
	if alias != "" {
		if l2, _, ok2 := get(alias); ok2 {
			l = l2
		} else {
			return nil, false
		}
	}
	if l.URL == "" {
		return nil, false
	}
	return l, true
}

// GuestSession is what a guest slug authenticates as. Deliberately tiny: a
// tenant to read within, a principal whose visibility to borrow, and a name to
// put in the session chip so the guest can see what they are holding.
type GuestSession struct {
	Slug        string
	Title       string
	Env         string
	PrincipalID int64
	// Scope: which restricted layers travel with this link (srv/guest.go).
	// Read-only is not the same as harmless; this is the difference.
	Scope string
	// DateFrom/DateTo: the window this key is confined to, or both empty for
	// a key that merely OPENS on a window and then lets the holder browse.
	// Enforced in one place (clampGuestQuery), never per handler.
	DateFrom string
	DateTo   string
}

// LookupGuest resolves a guest slug for the middleware. Returns nil for
// anything that is not a live capability — expired, revoked, or merely a
// named link, all of which must fall through to the password gate.
func (s *Server) LookupGuest(slug string) *GuestSession {
	if s == nil || s.DB == nil || slug == "" {
		return nil
	}
	l, ok := s.loadShortLink(slug)
	if !ok || !l.Guest || l.Expired || l.Revoked {
		return nil
	}
	return &GuestSession{Slug: l.Slug, Title: l.Title, Env: l.Env,
		PrincipalID: l.PrincipalID, Scope: l.Scope,
		DateFrom: l.DateFrom, DateTo: l.DateTo}
}

// HandleShortLink — GET /s/{slug}.
//
// 302, not 301: a slug can be renamed and a target is not eternal, and a
// permanently-cached redirect in every visitor's browser would outlive both.
func (s *Server) HandleShortLink(w http.ResponseWriter, r *http.Request) {
	slug := strings.ToLower(strings.Trim(r.PathValue("slug"), "/"))
	l, ok := s.loadShortLink(slug)
	if !ok {
		s.shortLinkGone(w, "That short link does not exist",
			"It may have been cleared, or the name mistyped &mdash; they are lowercase letters, digits and hyphens.")
		return
	}
	w.Header().Set("Cache-Control", "private, no-store")

	if l.Guest {
		if l.Revoked {
			s.shortLinkGone(w, "This shared link has been switched off",
				"Whoever sent it revoked access. Ask them for a new link.")
			return
		}
		if l.Expired {
			s.shortLinkGone(w, "This shared link has expired",
				"Shared links are time-limited on purpose. Ask whoever sent it for a fresh one.")
			return
		}
		// The capability becomes a cookie so it survives the dozens of /api
		// requests the page makes next, and so the slug leaves the URL bar —
		// a token in the address bar ends up in screenshots and in the next
		// person's "share this page".
		http.SetCookie(w, &http.Cookie{
			Name: guestCookie, Value: l.Slug, Path: "/",
			MaxAge: int(guestTTL / time.Second), HttpOnly: true, Secure: true,
			SameSite: http.SameSiteLaxMode,
		})
	}

	target := l.URL
	// A `pwd` arriving ON the short link authenticates THIS request — /s/ sits
	// outside PasswordMiddleware, so nothing else would honour it — but it is
	// spent into a cookie and never written into the redirect target.
	//
	// It used to be forwarded, "so a link copied while authenticated by query
	// param still works". That turned every named slug into a bearer token for
	// the shared password and left the password in the recipient's address bar
	// for their next "share this page": the credential leaked one share at a
	// time. A named link is a NAME (see the header of this file); the reader
	// authenticates as themselves.
	if pwd := r.URL.Query().Get("pwd"); pwd != "" && !l.Guest && isValidPassword(pwd) {
		SetAccessPwdCookie(w, pwd)
	}
	go s.bumpShortLinkHit(l.Slug)
	http.Redirect(w, r, target, http.StatusFound)
}

func (s *Server) shortLinkGone(w http.ResponseWriter, head, body string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "private, no-store")
	w.WriteHeader(http.StatusNotFound)
	w.Write([]byte(`<!doctype html><meta charset="utf-8"><title>` + head + `</title>` +
		`<body style="font:15px/1.6 -apple-system,system-ui,sans-serif;background:#0a0a0a;color:#e0e0e0;padding:14vh 8vw">` +
		`<h1 style="font-size:20px;color:#fff">` + head + `</h1>` +
		`<p style="color:#888">` + body + `</p>` +
		`<p><a style="color:#4ade80" href="/">Open the map</a></p></body>`))
}

func (s *Server) bumpShortLinkHit(slug string) {
	defer func() { recover() }()
	s.DB.Exec(`UPDATE short_links SET hits = hits + 1, last_hit_at = ? WHERE slug = ?`,
		time.Now().UTC().Format(time.RFC3339), slug)
}

// ── create / rename / list / revoke ───────────────────────────────────────

type shortLinkResp struct {
	Slug    string `json:"slug"`
	Short   string `json:"short"` // the path: /s/<slug>
	URL     string `json:"url"`   // what it points at (pwd stripped)
	Kind    string `json:"kind,omitempty"`
	Guest   bool   `json:"guest,omitempty"`
	Expires string `json:"expires_at,omitempty"`
	Scope   string `json:"scope,omitempty"` // restricted layers this link carries
	// DateFrom/DateTo: the window this key is CONFINED to, empty when the
	// holder may browse. Echoed so the dialog states what it granted rather
	// than what it asked for -- the server clamps, and a UI that shows its own
	// request would keep showing a denied one as granted.
	DateFrom string `json:"date_from,omitempty"`
	DateTo   string `json:"date_to,omitempty"`
	Reuse    bool   `json:"reused,omitempty"`
	// Tags: the purpose groups actually stored on the row (a SET — a link may
	// be part of a report and of a workshop). `Tag` is the first of them, kept
	// for one-word callers; both are echoed rather than assumed, because the
	// server sanitises, dedupes and caps what was asked for.
	Tags  []string `json:"tags,omitempty"`
	Tag   string   `json:"tag,omitempty"`
	Error string   `json:"error,omitempty"`
}

// HandleAPIShortLinkCreate — POST /api/shortlink
//
//	{url, title?, kind?, slug?, guest?: bool}
//
// Idempotent by URL within a tenant: sharing the same view twice returns the
// same slug rather than minting a second name for one picture. That is what
// makes "shorten every link" affordable — the table grows with distinct views,
// not with clicks. A guest link is never deduped into: it is a credential
// issued to one recipient, not a name for a view, and two recipients must be
// revocable separately.
func (s *Server) HandleAPIShortLinkCreate(w http.ResponseWriter, r *http.Request) {
	var body struct {
		URL   string `json:"url"`
		Title string `json:"title"`
		Kind  string `json:"kind"`
		Slug  string `json:"slug"`
		Guest bool   `json:"guest"`
		// Days: how long a guest link lives. Absent = guestTTLDays.
		Days int `json:"days"`
		// Patrol: whether this key carries patrol effort. A POINTER, because
		// three states matter here and a bool has two: "include", "exclude",
		// and "not stated" -- the last one means "whatever the shared view
		// was showing", which is the default and the common case.
		Patrol *bool `json:"patrol"`
		// LockDates: may the holder look outside the shared window? Only ever
		// asked of a guest link -- a named link is a name, and whoever opens
		// it signs in and sees what their own password sees, dates included.
		LockDates bool `json:"lock_dates"`
		// Tag/Tags: purpose groups (e.g. 'report') so links can be listed and
		// extended together. A set, because one link can serve two purposes;
		// `tag` is the single-word form older callers send.
		Tag  string   `json:"tag"`
		Tags []string `json:"tags"`
	}
	if err := decodeJSONBody(r, &body); err != nil {
		writeJSON(w, http.StatusBadRequest, shortLinkResp{Error: "bad request"})
		return
	}
	target, ok := shortNormalizeURL(body.URL, r)
	if !ok {
		writeJSON(w, http.StatusBadRequest, shortLinkResp{Error: "that URL cannot be shortened"})
		return
	}
	env, pwd := RequestEnv(r), RequestPwd(r)
	ref := ""
	if pwd != "" {
		ref = principalRef(pwd)
	}
	kind := body.Kind
	if kind != "download" && kind != "report" {
		kind = "view"
	}

	var expires string
	principal := s.RequestPrincipalID(r)
	if body.Guest {
		// A guest link delegates the CALLER's sight. A caller who is itself a
		// guest must therefore be refused, or one shared link would breed
		// others that nobody can find and revoke. (The middleware already
		// blocks POST for guests; this is the second lock on the same door,
		// because this is the door that matters.)
		if pwd == "" || GuestFromRequest(r) != nil {
			writeJSON(w, http.StatusForbidden, shortLinkResp{
				Error: "only a signed-in session can create a shared link"})
			return
		}
		expires = time.Now().Add(guestLife(body.Days)).UTC().Format(time.RFC3339)
	}

	// The scope defaults to the VIEW -- a sender grants what they were looking
	// at (srv/guest.go) -- and an explicit `patrol` overrides that, because
	// somebody who is looking at patrol tracks and wants to share the fire
	// scar underneath them should not have to go and toggle a layer off first.
	//
	// The override may only ever REMOVE, never add: the caller cannot ask for
	// a capability their own session does not have, or a guest link would be
	// a way to widen access rather than to delegate it.
	scope := scopeFromURL(target)
	if body.Patrol != nil {
		scope = withScope(scope, ScopePatrol, *body.Patrol && GuestHasScope(r, ScopePatrol))
	}

	// The date lock, same rule: derived from the view, never widened by the
	// caller, and only ever attached to a capability. A locked window on a
	// named link would be decoration -- the recipient signs in, and a signed-in
	// session is not confined by anything in this table.
	//
	// A guest minting is already refused above, so the "clamp downward" case
	// that scope needs has no analogue here: the only session that reaches this
	// line unrestricted is a real one. If that ever changes, intersect with
	// GuestWindow(r) before storing.
	var dFrom, dTo string
	if body.Guest && body.LockDates {
		dFrom, dTo = datesFromURL(target, time.Now())
		if dFrom == "" || dTo == "" {
			// A lock that silently locked nothing would be the worst outcome
			// on offer: the sender is told the key is confined, the key is not,
			// and nothing anywhere records the difference.
			writeJSON(w, http.StatusBadRequest, shortLinkResp{
				Error: "this view has no date range to lock"})
			return
		}
	}

	tags := shortSanitizeTags(append(append([]string{}, body.Tags...), body.Tag))
	tag := ""
	if len(tags) > 0 {
		tag = tags[0]
	}
	insert := func(slug string) error {
		var pid interface{}
		if body.Guest && principal != 0 {
			pid = principal
		}
		var exp interface{}
		if expires != "" {
			exp = expires
		}
		_, err := s.DB.Exec(`INSERT INTO short_links
			(slug, url, kind, title, env, pwd_ref, guest, principal_id, expires_at, created_at,
			 scope, date_from, date_to)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			slug, target, kind, strings.TrimSpace(body.Title), env, ref,
			boolInt(body.Guest), pid, exp, time.Now().UTC().Format(time.RFC3339),
			scope, dFrom, dTo)
		if err == nil && len(tags) > 0 {
			// Tags are rows in short_link_tags, not a column: the mint carries
			// them so a re-minted key stays in its groups (a remint that
			// dropped them would silently exempt the new key from the next
			// "renew #report").
			s.shortSetTags(slug, tags)
		}
		if err == nil && body.Guest {
			// A guest link straight to an export file is a promise the file
			// keeps existing: push the job's expiry out to the link's.
			s.retainGeoPackageForLink(target, expires)
		}
		return err
	}

	if body.Guest {
		for i := 0; i < 6; i++ {
			slug := guestToken()
			if slug == "" || s.shortSlugTaken(slug) {
				continue
			}
			if err := insert(slug); err != nil {
				continue
			}
			writeJSON(w, http.StatusOK, shortLinkResp{Slug: slug, Short: "/s/" + slug,
				URL: target, Kind: kind, Guest: true, Expires: expires, Scope: scope,
				DateFrom: dFrom, DateTo: dTo, Tag: tag, Tags: tags})
			return
		}
		writeJSON(w, http.StatusInternalServerError, shortLinkResp{Error: "could not create a link"})
		return
	}

	// A caller may propose a name (the rename field, before anything is
	// saved). If it is taken, fall through to a token rather than failing:
	// the user gets a link now and can rename it afterwards.
	if want := shortSlugify(body.Slug); want != "" && !s.shortSlugTaken(want) {
		if err := insert(want); err == nil {
			writeJSON(w, http.StatusOK, shortLinkResp{Slug: want, Short: "/s/" + want,
				URL: target, Kind: kind, Tag: tag, Tags: tags})
			return
		}
	}

	var existing string
	if err := s.DB.QueryRow(`SELECT slug FROM short_links
		WHERE url = ? AND env = ? AND pwd_ref = ? AND guest = 0
		  AND (alias_of IS NULL OR alias_of = '')
		ORDER BY created_at DESC LIMIT 1`, target, env, ref).Scan(&existing); err == nil && existing != "" {
		// Dedupe found a row. Since tags are a SET, a requested tag is ADDED to
		// whatever is already there rather than overwriting it or being dropped:
		// the caller is tagging the view they are sharing right now, and the row
		// they landed on may already belong to another purpose. The response
		// echoes the stored set, so the dialog shows the truth either way.
		if len(tags) > 0 {
			s.shortAddTags(existing, tags)
		}
		have := s.shortTagsOf(existing)
		first := ""
		if len(have) > 0 {
			first = have[0]
		}
		writeJSON(w, http.StatusOK, shortLinkResp{Slug: existing, Short: "/s/" + existing,
			URL: target, Kind: kind, Reuse: true, Tag: first, Tags: have})
		return
	}

	for i := 0; i < 6; i++ {
		slug := shortToken()
		if slug == "" || s.shortSlugTaken(slug) {
			continue
		}
		if err := insert(slug); err != nil {
			continue
		}
		writeJSON(w, http.StatusOK, shortLinkResp{Slug: slug, Short: "/s/" + slug,
			URL: target, Kind: kind, Tag: tag, Tags: tags})
		return
	}
	writeJSON(w, http.StatusInternalServerError, shortLinkResp{Error: "could not create a link"})
}

func (s *Server) shortSlugTaken(slug string) bool {
	var one int
	return s.DB.QueryRow(`SELECT 1 FROM short_links WHERE slug = ?`, slug).Scan(&one) == nil
}

// shortCallerRef is the caller's pwd_ref, or "" when the request carries no
// password (a guest, or nothing). Every read and write on short_links is
// scoped by it: a link belongs to the login that minted it, and another
// login must get "no such link" (404, not 403 — an id must not be an oracle,
// AGENTS.md invariant 6). Rows with an empty pwd_ref (pre-scoping legacy)
// are reachable by nobody rather than by everybody.
func shortCallerRef(r *http.Request) string {
	if pwd := RequestPwd(r); pwd != "" {
		return principalRef(pwd)
	}
	return ""
}

// shortLinkOwned reports whether slug exists AND belongs to the caller.
func (s *Server) shortLinkOwned(slug string, r *http.Request) bool {
	ref := shortCallerRef(r)
	if ref == "" {
		return false
	}
	var pr string
	if err := s.DB.QueryRow(`SELECT COALESCE(pwd_ref,'') FROM short_links WHERE slug = ?`,
		slug).Scan(&pr); err != nil {
		return false
	}
	return pr == ref
}

// HandleAPIShortLinkRename — POST /api/shortlink/{slug}/rename {slug}
//
// The old name stays as an alias. Renaming is the normal case (copy first,
// name second), so by then the old link may already have been sent — freeing
// the name would break exactly the link this feature exists to make sendable.
func (s *Server) HandleAPIShortLinkRename(w http.ResponseWriter, r *http.Request) {
	old := strings.ToLower(strings.Trim(r.PathValue("slug"), "/"))
	var body struct {
		Slug string `json:"slug"`
	}
	if err := decodeJSONBody(r, &body); err != nil {
		writeJSON(w, http.StatusBadRequest, shortLinkResp{Error: "bad request"})
		return
	}
	want := shortSlugify(body.Slug)
	if want == "" {
		writeJSON(w, http.StatusBadRequest, shortLinkResp{
			Error: "use lowercase letters, digits and hyphens"})
		return
	}
	var target, kind string
	var guest int
	var alias sql.NullString
	if !s.shortLinkOwned(old, r) {
		// Not yours reads as not there — an id must not be an oracle.
		writeJSON(w, http.StatusNotFound, shortLinkResp{Error: "no such link"})
		return
	}
	if err := s.DB.QueryRow(`SELECT url, kind, guest, alias_of FROM short_links WHERE slug = ?`, old).
		Scan(&target, &kind, &guest, &alias); err != nil {
		writeJSON(w, http.StatusNotFound, shortLinkResp{Error: "no such link"})
		return
	}
	if alias.Valid && alias.String != "" {
		writeJSON(w, http.StatusConflict, shortLinkResp{Error: "that link is already an alias"})
		return
	}
	// A guest slug is the secret itself. A memorable capability is a guessable
	// one, and an alias would leave the old secret live besides.
	if guest == 1 {
		writeJSON(w, http.StatusConflict, shortLinkResp{
			Error: "a shared link keeps its name — the name is the key"})
		return
	}
	if want == old {
		writeJSON(w, http.StatusOK, shortLinkResp{Slug: old, Short: "/s/" + old, URL: target, Kind: kind})
		return
	}
	if s.shortSlugTaken(want) {
		writeJSON(w, http.StatusConflict, shortLinkResp{Error: "that name is taken"})
		return
	}
	tx, err := s.DB.Begin()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, shortLinkResp{Error: "could not rename"})
		return
	}
	defer tx.Rollback()
	if _, err := tx.Exec(`UPDATE short_links SET slug = ? WHERE slug = ?`, want, old); err != nil {
		writeJSON(w, http.StatusConflict, shortLinkResp{Error: "that name is taken"})
		return
	}
	// The alias keeps `url` empty so the dedupe query cannot pair one view
	// with two live names; resolution follows alias_of.
	if _, err := tx.Exec(`INSERT INTO short_links (slug, url, alias_of, kind, env, pwd_ref, created_at)
		VALUES (?, '', ?, ?, ?, ?, ?)`,
		old, want, kind, RequestEnv(r), principalRef(RequestPwd(r)),
		time.Now().UTC().Format(time.RFC3339)); err != nil {
		writeJSON(w, http.StatusInternalServerError, shortLinkResp{Error: "could not rename"})
		return
	}
	// Tags are keyed on the slug, so they must travel with it inside the same
	// transaction — a renamed link that lost its tags would silently drop out
	// of the next "renew #report", which is the one thing tags exist to stop.
	// (The alias gets none: an alias is a ghost of a rename, not a link.)
	if _, err := tx.Exec(`UPDATE short_link_tags SET slug = ? WHERE slug = ?`, want, old); err != nil {
		writeJSON(w, http.StatusInternalServerError, shortLinkResp{Error: "could not rename"})
		return
	}
	if err := tx.Commit(); err != nil {
		writeJSON(w, http.StatusInternalServerError, shortLinkResp{Error: "could not rename"})
		return
	}
	tags := s.shortTagsOf(want)
	first := ""
	if len(tags) > 0 {
		first = tags[0]
	}
	writeJSON(w, http.StatusOK, shortLinkResp{Slug: want, Short: "/s/" + want, URL: target,
		Kind: kind, Tags: tags, Tag: first})
}

type shortLinkRow struct {
	Slug    string `json:"slug"`
	URL     string `json:"url"`
	AliasOf string `json:"alias_of,omitempty"`
	Kind    string `json:"kind"`
	Title   string `json:"title,omitempty"`
	Hits    int    `json:"hits"`
	LastHit string `json:"last_hit_at,omitempty"`
	Created string `json:"created_at"`
	Guest   bool   `json:"guest"`
	Expires string `json:"expires_at,omitempty"`
	Scope   string `json:"scope,omitempty"` // restricted layers this link carries
	// Tags: purpose groups, e.g. ['report','workshop'] — a SET, alphabetical.
	// `Tag` is the first of them, kept so a one-word reader stays correct.
	Tags []string `json:"tags,omitempty"`
	Tag  string   `json:"tag,omitempty"`
	// The window this key is confined to, if any. The sheet must show it:
	// "read-only, expiring, and only May-August" is a different thing to have
	// handed out than "read-only and expiring", and an operator deciding
	// whether to revoke needs to see which one they issued.
	DateFrom string `json:"date_from,omitempty"`
	DateTo   string `json:"date_to,omitempty"`
	Expired  bool   `json:"expired,omitempty"`
	Revoked  bool   `json:"revoked,omitempty"`
	Mine     bool   `json:"mine"`
}

type shortLinkGroup struct {
	Ref   string         `json:"ref"`   // non-secret sha256 handle
	Label string         `json:"label,omitempty"` // unused since scoping; kept for shape stability
	Env   string         `json:"env"`
	Mine  bool           `json:"mine"`
	Links []shortLinkRow `json:"links"`
}

// HandleAPIShortLinkList — GET /api/shortlinks. The admin sheet.
//
// SCOPED TO THE CALLER. You see and manage the links your login minted, and
// only those — another tenant's share links describe their views and their
// grants, which is exactly the class of thing the AOI Access tab already
// refuses to show globally (srv/aoi_admin.go, decision 1). The response keeps
// the grouped shape (one group: yours) so the sheet needs no second format.
// The password itself is never in the response — a group is identified by
// `pwd_ref`, the same non-secret handle the Access tab already prints.
func (s *Server) HandleAPIShortLinkList(w http.ResponseWriter, r *http.Request) {
	mine := shortCallerRef(r)
	if mine == "" {
		// No password, no links: a guest or an unauthenticated caller owns
		// nothing here. Empty, not an error — the sheet renders "no links".
		writeJSON(w, http.StatusOK, map[string]interface{}{"groups": []*shortLinkGroup{},
			"guest_ttl_days": int(guestTTL / (24 * time.Hour)),
			"guest_max_days": int(guestMaxTTL / (24 * time.Hour))})
		return
	}
	rows, err := s.DB.Query(`SELECT slug, url, COALESCE(alias_of,''), kind, title, env,
		COALESCE(pwd_ref,''), guest, COALESCE(expires_at,''), COALESCE(revoked_at,''),
		hits, COALESCE(last_hit_at,''), created_at, COALESCE(scope,''),
		COALESCE(date_from,''), COALESCE(date_to,'')
		FROM short_links WHERE pwd_ref = ? ORDER BY created_at DESC LIMIT 1000`, mine)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "could not list links"})
		return
	}
	defer rows.Close()
	// One query for every (slug → tags) pair, not one per row: the sheet lists
	// up to 1000 links and N+1 here is how a sheet becomes a spinner.
	tagsBySlug := s.shortTagsByOwner(mine)
	g := &shortLinkGroup{Ref: mine, Mine: true, Links: []shortLinkRow{}}
	now := time.Now()
	for rows.Next() {
		var l shortLinkRow
		var env, ref, revoked string
		var guest int
		if err := rows.Scan(&l.Slug, &l.URL, &l.AliasOf, &l.Kind, &l.Title, &env, &ref,
			&guest, &l.Expires, &revoked, &l.Hits, &l.LastHit, &l.Created, &l.Scope,
			&l.DateFrom, &l.DateTo); err != nil {
			continue
		}
		l.Tags = tagsBySlug[l.Slug]
		if len(l.Tags) > 0 {
			l.Tag = l.Tags[0]
		}
		l.Guest = guest == 1
		l.Revoked = revoked != ""
		if l.Expires != "" {
			if t, err := time.Parse(time.RFC3339, l.Expires); err == nil {
				l.Expired = now.After(t)
			}
		}
		l.Mine = true
		g.Env = env
		g.Links = append(g.Links, l)
	}
	groups := []*shortLinkGroup{}
	if len(g.Links) > 0 {
		groups = append(groups, g)
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"groups": groups,
		"guest_ttl_days": int(guestTTL / (24 * time.Hour)),
		"guest_max_days": int(guestMaxTTL / (24 * time.Hour))})
}

// shortRefLabels is gone: the list is scoped to the caller, so no label
// (which was pwd[:3]+"…", three characters of a credential) is ever needed
// or served.

// shortExtend pushes a guest link's expiry out by n days from now or from its
// current expiry, whichever is later, clamped to guestMaxTTL from now. Returns
// the new expiry, or "" if the row is not an extendable (live guest) link.
func (s *Server) shortExtend(slug string, days int) string {
	if days <= 0 {
		days = 30
	}
	var target, expires, revoked string
	var guest int
	err := s.DB.QueryRow(`SELECT url, guest, COALESCE(expires_at,''), COALESCE(revoked_at,'')
		FROM short_links WHERE slug = ?`, slug).Scan(&target, &guest, &expires, &revoked)
	if err != nil || guest != 1 || revoked != "" {
		return ""
	}
	now := time.Now().UTC()
	base := now
	if t, err := time.Parse(time.RFC3339, expires); err == nil && t.After(base) {
		base = t
	}
	newExp := base.Add(time.Duration(days) * 24 * time.Hour)
	if hi := now.Add(guestMaxTTL); newExp.After(hi) {
		newExp = hi
	}
	exp := newExp.Format(time.RFC3339)
	if _, err := s.DB.Exec(`UPDATE short_links SET expires_at = ? WHERE slug = ?`, exp, slug); err != nil {
		return ""
	}
	// The file the link names must live at least as long as the link.
	s.retainGeoPackageForLink(target, exp)
	return exp
}

// HandleAPIShortLinkExtend — POST /api/shortlink/{slug}/extend {days:30}.
// Guest links only: a named link does not expire, so there is nothing to
// extend. Never callable by a guest (the middleware blocks guest POSTs, and a
// capability that can lengthen its own life is not a capability).
func (s *Server) HandleAPIShortLinkExtend(w http.ResponseWriter, r *http.Request) {
	slug := strings.ToLower(strings.Trim(r.PathValue("slug"), "/"))
	if !s.shortLinkOwned(slug, r) {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "no such link"})
		return
	}
	var body struct {
		Days int `json:"days"`
	}
	decodeJSONBody(r, &body)
	exp := s.shortExtend(slug, body.Days)
	if exp == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "not an extendable link"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"slug": slug, "expires_at": exp})
}

// HandleAPIShortLinkExtendTag — POST /api/shortlinks/extend {tag, days:30}.
// The reason tags exist: every link a report cites is extended in one call.
func (s *Server) HandleAPIShortLinkExtendTag(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Tag  string `json:"tag"`
		Days int    `json:"days"`
	}
	if err := decodeJSONBody(r, &body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "bad request"})
		return
	}
	tag := shortSanitizeTag(body.Tag)
	if tag == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "no tag"})
		return
	}
	slugs := s.shortSlugsWithTag(tag, shortCallerRef(r))
	extended := 0
	last := ""
	for _, sl := range slugs {
		if exp := s.shortExtend(sl, body.Days); exp != "" {
			extended++
			last = exp
		}
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"tag": tag, "extended": extended, "expires_at": last})
}

// HandleAPIShortLinkDelete — DELETE /api/shortlink/{slug}
//
// Revocation, and the reason the list exists.
//
// A GUEST link is revoked, not deleted: the row stays with `revoked_at` set,
// so the recipient gets "this was switched off" instead of "no such link", and
// so the sheet can still show that it was opened four times before you pulled
// it. A NAMED link has no such history worth keeping and is deleted outright,
// together with the aliases pointing at it — an alias outliving its target is
// a 404 with a name, which reads as a bug rather than as a revoked link.
func (s *Server) HandleAPIShortLinkDelete(w http.ResponseWriter, r *http.Request) {
	slug := strings.ToLower(strings.Trim(r.PathValue("slug"), "/"))
	if slug == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "no slug"})
		return
	}
	var guest int
	if !s.shortLinkOwned(slug, r) {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "no such link"})
		return
	}
	if err := s.DB.QueryRow(`SELECT guest FROM short_links WHERE slug = ?`, slug).Scan(&guest); err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "no such link"})
		return
	}
	if guest == 1 {
		if _, err := s.DB.Exec(`UPDATE short_links SET revoked_at = ? WHERE slug = ?`,
			time.Now().UTC().Format(time.RFC3339), slug); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "could not revoke"})
			return
		}
		writeJSON(w, http.StatusOK, map[string]interface{}{"revoked": slug})
		return
	}
	// The tag rows go with the link: a pair pointing at a deleted slug would
	// keep the tag in the vocabulary and in every count, describing nothing.
	s.DB.Exec(`DELETE FROM short_link_tags WHERE slug IN
		(SELECT slug FROM short_links WHERE slug = ? OR alias_of = ?)`, slug, slug)
	res, err := s.DB.Exec(`DELETE FROM short_links WHERE slug = ? OR alias_of = ?`, slug, slug)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "could not clear link"})
		return
	}
	n, _ := res.RowsAffected()
	writeJSON(w, http.StatusOK, map[string]interface{}{"deleted": n})
}
