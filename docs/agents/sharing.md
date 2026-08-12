# Share links: short names and guest capabilities

_Read when working on "Copy link", the admin sharing sheet, or anything that
delegates access._

## What exists (committed)

| Piece | Where |
|---|---|
| The share dialog (the whole UI) | `srv/static/sharelink.js`, `.sl-*` in `globe.css` |
| Scope: what a key may SEE | `db/migrations/053-shortlink-scope.sql`, `srv/guest.go` |
| Admin → Sharing tab | `loadSharingTab()` in `globe.html`, `.sh-*` in `globe.css` |
| Table + the reasoning | `db/migrations/052-short-links.sql` |
| Create / rename / list / revoke, `/s/{slug}` | `srv/shortlink.go` |
| Guest session, read-only enforcement | `srv/guest.go` |
| Middleware hook, `RequestEnv` fallback | `srv/auth_middleware.go` |
| `RequestPrincipalID`, `visibilityFingerprint` | `srv/aoi.go` |
| Routes, `pageData.IsGuest`, chip label | `srv/server.go` |

API: `POST /api/shortlink {url,title?,kind?,slug?,guest?,days?,patrol?}` →
`{slug,short,url,guest,expires_at,scope,reused}`;
`POST /api/shortlink/{slug}/rename {slug}`; `DELETE /api/shortlink/{slug}`;
`GET /api/shortlinks` → `{groups:[{ref,label,env,mine,links:[…]}],guest_ttl_days}`.

## The two kinds, and why they are different objects

**A named link** is a *name*. `pwd` is stripped from the stored URL, it
resolves behind the ordinary password gate, and the slug is meant to be
guessable (`virunga-fires`) — which is safe precisely because guessing it wins
nothing. Renaming leaves the old slug as an alias (`alias_of`, empty `url`),
because by the time you rename, the old link may already have been sent.
Creation is idempotent by (url, env): shortening *everything* then costs one
row per distinct view, not one per click.

**A guest link** is a *capability*. The slug **is** the secret (16 chars of
crypto/rand over a 27-symbol vowel-free alphabet, ~76 bits, `g-` prefixed so an
operator reading a log can see it is a credential). It is therefore **never
renameable** — a memorable capability is a guessable one — and never deduped
into, because two recipients must be revocable separately.

## Why not "password in the link + PIN"

It was proposed and rejected. The link and the digits travel through the same
chat window; six digits is a million guesses; and what is behind them is still
the one shared password — same secret for everyone, grants writing, cannot be
withdrawn from one recipient, unattributable after two forwards. A PIN adds a
page and fixes none of those five things. A capability fixes all five: its own
secret, read-only, scoped, expiring (30 d), revocable with a hit count.

Honest limit, stated in the migration: a guest slug is a bearer token stored in
cleartext (the admin sheet must be able to re-copy it). A DB leak leaks these
links — but the same DB holds the data they grant sight of, so the token adds
no exposure. What it must never do is grant *more*, which is why read-only
lives in the middleware.

## Invariants a change here can break

1. **A guest is not anonymous.** `visibilityFingerprint` gives it its own slot
   (`g<slug>`). Filing a private-AOI body under `anon` puts it in the slot
   every unauthenticated reader draws from.
2. **Read-only is a method check plus a prefix list** (`guestMayRead`), not a
   deny-list of known-dangerous endpoints — this app grows endpoints weekly and
   a deny-list admits every new one silently. New write surface must be closed
   *there*.
3. **A guest may not mint links.** Blocked twice on purpose: in `guestMayRead`
   and again in the create handler. A capability that mints capabilities is a
   password.
4. **The strongest credential present wins.** `guestAuth` is consulted after
   both password checks, so a signed-in user holding a stale guest cookie stays
   themselves; and `RequestEnv`'s guest branch is likewise last.
5. **`/s/` is not in `isPublicPath`.** It is allowed through
   `PasswordMiddleware` explicitly, because the arriving guest has no cookie
   yet — that request is how they get one. It answers `no-store`.
6. **Guests are revoked, not deleted** (`revoked_at`): the recipient gets "this
   was switched off" rather than "no such link", and the sheet keeps the
   evidence that it was opened four times first. Named links are deleted
   outright, with their aliases (an alias outliving its target is a 404 with a
   name, which reads as a bug).

## The scope column: what a key may SEE

Read-only is not the same as harmless. A capability borrows the creator's
principal, so before `scope` a link shared to show a fire scar also showed the
recipient every patrol track the account owned — the sender never chose that
and had no way to notice.

`short_links.scope` is a **comma-separated allow-list of capability names**, not
a `patrol` boolean, so the next restricted layer is one constant in Go and no
migration. It is an allow-list on purpose: a deny-list would retroactively widen
every link ever minted the day a new sensitive layer ships, and the failure mode
of forgetting to update it must be a guest who sees *less*.

Today there is exactly one entry, `ScopePatrol`. Fires, deforestation,
settlements, geology, basemaps and the historical sheets are the same public
data for everybody and a guest may toggle and explore them freely — an
interactive map that punishes exploration is a screenshot with extra steps. A
layer earns an entry only when it is somebody's *people* or private geometry.

**Enforcement is one line per layer, not forty.** Patrol data is already
filtered by `e.env = ?` everywhere it is read (the tenant mechanism,
`srv/tenant.go`). `PatrolEnv(r)` returns a tenant that owns nothing when the
scope is absent, so every one of those queries answers empty without learning
what a guest is:

```bash
grep -n 'effort_data' srv/*.go     # every read should use PatrolEnv, not RequestEnv
```

`isTestEnv()` reads `PatrolEnv` too, which closes learned roads/airstrips/camps
and the patrol MCP in the same stroke — they are derived from the same GPX, and
a link that does not carry patrol must not show it under another name.

The scope is decided **once, at creation, from the view being shared** (a sender
grants what they were looking at), and an explicit `patrol:` in the POST
overrides that — but only ever downward: `GuestHasScope` clamps it, so a caller
can never ask for a capability their own session lacks.

Two things a change here must not break: the GeoPackage cache key includes the
patrol tenant (`gpkgKeyFor`), or a scope-restricted export would be served the
account's own cached file; and `visibilityFingerprint` already gives a guest its
own response-cache slot keyed on slug, which is what makes the above safe.

## The dialog

One centred modal for every copyable link — it replaced a tooltip anchored to
whichever button was clicked, which had to dodge the time slider (it grows when
the animator runs), the toolbar and the screen edge, and still landed somewhere
different every time. Centring deletes all that geometry; the same component is
the phone layout.

* **The copy has already happened** by the time it is on screen, and with the
  *long* URL, inside the click that asked for it — the shortener's answer
  arrives after the gesture is over, when Safari refuses clipboard writes. The
  short URL then replaces it silently. Never block a copy on the network.
* **No caption explains the rename.** The slug is the only green, boxed,
  pencil-bearing thing on the line. A control that needs a label saying it is a
  control is not finished. (A "tap green name to rename" caption was there; it
  is gone on purpose.)
* **Named vs guest is a two-way switch, not a checkbox** — "unchecked" is not a
  description of anything. Each mode carries its consequence in its second line.
  Flipping mints (or recalls) the *other* link: a key's scope and lifetime are
  fixed at creation, because a link whose permissions can be edited afterwards
  means the copy in somebody's inbox no longer describes what they hold.
  Flipping back and forth must not leave orphan keys, hence `namedLink` /
  `guestLink` are remembered.
* **Guest options unfold, they do not appear.** `display:none` toggling jumped
  the card 90px, which reads as a reload. The grid `0fr→1fr` transition animates
  to the panel's own height with nothing measured or hardcoded.
* **The patrol row is absent, not disabled,** when the account owns no patrol or
  the page is itself a guest: a control that could only ever be off is worse
  than none, because it implies the data exists.
* Lifetime is capped (`guestMaxTTL`, 1 year) and clamped rather than refused;
  "never expires" is not offered, because that is a second password.

## Guest UI

`window.IS_GUEST` + `body.is-guest`. Upload is **disabled, not removed** — it is
the primary action, and deleting it would silently change the shape of the app;
dimmed with a tooltip it reads as "not yours to do". Write-only admin tabs *are*
removed (nobody misses a tab they cannot use), leaving Map Settings, and the
toolbar pencil becomes a map icon because a pencil promises editing.

## Admin → Sharing

Grouped by password (`pwd_ref`, own group first). Guest rows are amber and
badged; "never opened" is written out rather than left blank, because a blank
cell reads as "no data" and an unopened key is the opposite of no data. Aliases
are shown — an old link still resolves through one, and hiding it makes "why
does this URL still work" unanswerable.

## Tests

`tests/api_tests.sh`, six checks: a guest reads, is refused writes/admin/
minting, is withheld patrol pixels (**both halves asserted** — "0 and 0" would
pass a one-sided check while meaning the feature is broken), honours an explicit
scope, expires and revokes, and a named link is not a way in. The block **mints
and then destroys** every slug: a test that leaves live capabilities behind
manufactures the exact thing this feature exists to keep countable. The ledger
is a *file* — `mint()` runs inside `$(...)`, and a bash array appended in that
subshell is discarded, which "cleaned up" one slug of eleven and reported
success.
