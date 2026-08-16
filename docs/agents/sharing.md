# Share links: short names and guest capabilities

_Read when working on "Copy link", the admin sharing sheet, or anything that
delegates access._

## What exists (committed)

| Piece | Where |
|---|---|
| The share dialog (the whole UI) | `srv/static/sharelink.js`, `.sl-*` in `globe.css` |
| Scope: what a key may SEE | `db/migrations/053-shortlink-scope.sql`, `srv/guest.go` |
| Dates: WHEN a link is about | `db/migrations/054-shortlink-dates.sql`, `srv/guest.go` |
| Admin → Sharing tab | `loadSharingTab()` in `globe.html`, `.sh-*` in `globe.css` |
| Table + the reasoning | `db/migrations/052-short-links.sql` |
| Create / rename / list / revoke, `/s/{slug}` | `srv/shortlink.go` |
| Guest session, read-only enforcement | `srv/guest.go` |
| Middleware hook, `RequestEnv` fallback | `srv/auth_middleware.go` |
| `RequestPrincipalID`, `visibilityFingerprint` | `srv/aoi.go` |
| Routes, `pageData.IsGuest`, chip label | `srv/server.go` |

API: `POST /api/shortlink {url,title?,kind?,slug?,guest?,days?,patrol?,lock_dates?}` →
`{slug,short,url,guest,expires_at,scope,date_from,date_to,reused}`;
`POST /api/shortlink/{slug}/rename {slug}`; `DELETE /api/shortlink/{slug}`;
`GET /api/shortlinks` → `{groups:[{ref,label,env,mine,links:[…]}],guest_ttl_days}`.

## ⚠️ A link never carries the password (2026-08-12)

`buildShareUrl()` defaulted to `includePwd = true`. So the first thing every
"copy link" in the app put on the clipboard — the long URL, copied *inside* the
click, before the shortener answers — was the shared access password in plain
text. Four copies of the one mistake, each of which alone would have been
enough:

1. `buildShareUrl(includePwd = true)` — the long URL, and the `?pwd=` the
   shortener then stored… no: `normaliseShortTarget()` strips it, so the stored
   row was always clean. The **clipboard** was not.
2. `shortURL()` appended `?pwd=` to the `/s/…` URL whenever the current session
   was authenticated by query param.
3. `HandleShortLink` forwarded a `?pwd=` on the short URL onto the redirect
   target, so a named slug became a bearer token for the shared password.
4. `PasswordMiddleware` only stripped `?pwd=` on the request that had **no
   cookie yet**. Every later arrival carrying the param — most of all the
   redirect in (3) — left it sitting in `location.search`, where (1) copied it
   into the next link the user shared. **The credential leaked one share at a
   time**, from a URL its owner had already authenticated past.

Now: a password rides only to authenticate the request it arrives on. It is
spent into the `access_pwd` cookie (`SetAccessPwdCookie`, one writer for the
attributes) and scrubbed from the URL by a redirect (`urlWithoutPwd`, one writer
for the scrub) on **both** middleware branches. `/s/{slug}?pwd=` still works —
`/s/` sits outside the gate, so nothing else would honour it — but sets the
cookie and redirects to the clean target.

On the client, `absolute()` in `sharelink.js` is a **chokepoint**: every URL
entering the module passes through it, including download hrefs deliberately
built with `?pwd=` so a plain anchor works. Scrub there and the paths nobody
thought about are covered too. `copyStarredLink()` had its own private copy of
the same bug ("always include password if present"); gone.

`buildShareUrl(includePwd)` keeps its parameter, defaulting to `false` and with
no caller passing `true`. Deleting it would have made a future need silent;
leaving it makes that need state itself.

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
   *there*. The one carve-out is **reads wearing POSTs**, enumerated one by one
   in the same function: the geology filtered export and the GeoPackage
   builders (`/api/{parks|aois}/{id}/export.gpkg`, `/api/view/export.gpkg`).
   A builder is admissible because everything in the file is data the guest
   can already GET — dates are clamped on the way in, patrol rides on
   `PatrolEnv` (and the job cache key carries `|np`, so a scope-restricted
   export is a different file, never the owner's cached copy), and AOI ids
   still pass `aoiGate`. What stays refused: `DELETE /api/geopackage/{id}`
   (cancelling/removing the owner's files is a write) and
   `POST …/mbtiles` (publishes to the owner's Zenodo account — an external
   write). The guest UI matches: no Tiles menu entry, no delete/cancel
   buttons on export cards (`gpkg_export.js`, `exportMenuItems`). Pinned by
   `guest_may_build_exports_but_not_delete` in `tests/api_tests.sh`.
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

## The date columns: WHEN a link is about

A share URL always carried a time window, but in one of two ways that look
identical in the address bar and mean opposite things:

* `date_preset=90d` is a **rule**, resolved against whoever's clock opens it —
  right for a standing bookmark, so the map moves with time;
* `from=&to=` is a **fact** — right for a report footnote, an incident, a
  season, and the same picture forever.

Sending the wrong one produces either a dashboard that quietly goes stale or a
citation that no longer says what it said, and the sender finds out from the
recipient months later. So when the shared view is on a preset, the dialog
asks: **Always up to date** vs **These exact dates** (blue, one row above the
WHO switch — not green/amber, because this is not an access decision). The two
options are two *different URLs*, so choosing re-mints; `sl-dates` hides itself
entirely when the view is already pinned, because two buttons with one possible
answer invent a decision rather than offering one.

The frozen dates are **read off the slider** (`window.dateFrom/dateTo`), never
recomputed — a second implementation of "what does 90d mean" is a second answer
waiting to disagree with the map on screen. `datesFromURL` in Go resolves a
preset too, but only as the last resort at mint time.

### The second question, and why it needs a column

A frozen URL says what a link **opens at**, not what the holder may look at:
the recipient of "the Chinko fire season" drags the slider to last week the
moment the map loads, and nothing in the link ever suggested they could not.
Usually that is right. Sometimes one season *is* the grant — hence
`short_links.date_from/date_to`, the **Other dates** switch, and:

1. **It clamps, it does not refuse** (`clampGuestQuery`, on the way in through
   `guestAuth`). A guest who drags outside the window gets a map with less on
   it, not a screenful of failed panels — and a 403 for June would tell the
   holder there is a June worth having. Wholly-outside collapses to a
   zero-length range, which every query answers empty.
2. **One place, not forty.** `grep -c 'Query().Get("from")' srv/*.go` — a check
   per handler is invariant 5's mistake in a new costume. Rewriting the query
   is legitimate because every one of those handlers already accepts any window
   a caller asks for.
3. **The exception is a window with no dates in it.** `/fire-realtime?days=28`
   means "ending now", and "now" is the one thing a locked key must not reach
   past — hence `ClampGuestDates` there, plus an upper bound on the
   `start_date` query (clamping only the *claimed* period would be a no-op
   reading as an answer).
4. **A lock that locks nothing is refused**, not stored empty: empty means
   unrestricted, so a silently-empty lock tells the sender the key is confined
   while it is not. Same reason a named link never gets one — the recipient
   signs in, and a signed-in session is not confined by this table.
5. Locking a *rolling* link is incoherent (a window that moves under the
   holder), so asking for the lock takes the frozen URL with it rather than
   refusing the click; un-freezing drops the lock.

The dialog's note states the grant in the recipient's terms before it is sent
("Only Jul 14 – Aug 12 — other dates come back empty"), and the admin sheet
badges it in blue, distinct from the red `patrol tracks`: a narrowed window is a
restriction the sender chose, not a risk they may have missed.

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
* **Tags & extension** (2026-08-16). `short_links.tag` (migration 060) groups
  links issued for one purpose (e.g. every link a report cites → `report`).
  Set at mint time (`tag` in the POST body, sanitised to `[a-z0-9_-]{≤32}`).
  `POST /api/shortlink/{slug}/extend {days}` pushes one guest link's expiry
  out (from the later of now/current, capped at `guestMaxTTL` from now);
  `POST /api/shortlinks/extend {tag, days}` does every live key with that tag
  in one call. Both refuse guests. The Sharing sheet shows `#tag` badges,
  days-remaining, a per-key `+30d` button and a `+30d all '<tag>'` button per
  group.
* **A guest link to an export file retains the file.** A key pointing at
  `/api/geopackage/{id}/download` promises the bytes exist as long as the key
  lives: `retainGeoPackageForLink` pushes the job's `expires_at` out to the
  link's (never in) at mint time and on every extend, and the hourly gpkg
  sweeper re-asserts it for links created before the rule existed. The bell
  card shows the purge date, days left, the link's tag (`link_tag`, computed
  on read from `short_links`, never stored) and a `+30 days` button
  (`POST /api/geopackage/{id}/extend`, owner only — guests 401).

## Guest UI

`window.IS_GUEST` + `body.is-guest`. Upload is **disabled, not removed** — it is
the primary action, and deleting it would silently change the shape of the app;
dimmed with a tooltip it reads as "not yours to do". Write-only admin tabs *are*
removed (nobody misses a tab they cannot use), leaving Map Settings, and the
toolbar pencil becomes a map icon because a pencil promises editing.

## Admin → Access & Sharing (merged 2026-08-16)

The Access and Sharing tabs are ONE tab (`data-tab="access"`; share links
first, then AOI/ingest, then principals). `switchAdminTab('sharing')` redirects
to it so old deep links keep working.

**Scoped to the caller (2026-08-16).** The first cut listed *every* login's
links grouped by `pwd_ref` — one tenant could read another's link targets
(which name their private AOIs and export files), copy their live guest keys,
and revoke/retag/extend/rename them; the group label was even `pwd[:3]+"…"`,
three characters of every other password. Now `GET /api/shortlinks` returns
only rows `WHERE pwd_ref = <caller>` (empty ref → empty list, so a legacy row
with no `pwd_ref` is reachable by *nobody*), and every mutation
(delete/rename/extend/retag, the by-tag bulk verbs, and the tag vocabulary)
is scoped through `shortLinkOwned`/`shortCallerRef` in `srv/shortlink.go`.
Not-yours answers **404, not 403** (invariant 6). Create's dedupe also matches
only the caller's own rows — it used to return, and adopt tags onto, another
login's slug. `/s/{slug}` resolution is unchanged: a named link resolving for
anyone holding a valid password is the feature. Pinned by
`shortlinks_scoped_to_their_login` in `tests/api_tests.sh`.

The intro text is a one-line scope statement plus a `.sh-legend` definition
list (globe.css) that collapses to one column under 640px — the old 7-line
paragraph read as a wall on desktop and wrapped badly on phones.

Guest rows are amber and badged; "never opened" is written out rather
than left blank, because a blank cell reads as "no data" and an unopened key is
the opposite of no data. Aliases are shown — an old link still resolves through
one, and hiding it makes "why does this URL still work" unanswerable.

**Tags.** A purpose tag is bookkeeping, not a grant: setting one never remints.
Chip UI (`.sh-tag` in admin, `.sl-tag` in the share dialog — blue on purpose,
not the key's amber): click the name to rename the tag **everywhere**
(`POST /api/shortlinks/retag {tag,new_tag}` — renaming one row would fork the
group out of the next "renew all"), × to remove it from one link
(`POST /api/shortlink/{slug}/retag {tag}`, empty = clear). The share dialog
offers a subtle "# add tag" with datalist autocomplete over
`GET /api/shortlink-tags` (names only, most recent first) — the point of a tag
is to be the *same word* as last time. On create, dedupe adopts a requested tag
onto an untagged existing row but never overwrites one already there; the
response echoes what is stored.

**Renewal appears only near death.** The `+30d` buttons became a single amber
calendar-plus icon (`.sh-btn-renew`, and `.gpkg-renew` on export cards) shown
only when ≤14 days remain (`SHARING_RENEW_DAYS`); the group-level
"renew #tag" likewise only when a member is close. An always-on extend button
taught people to click it ritually, which is how keys live forever.

## Tests

`tests/api_tests.sh`, eight checks: a guest reads, is refused writes/admin/
minting, is withheld patrol pixels (**both halves asserted** — "0 and 0" would
pass a one-sided check while meaning the feature is broken), honours an explicit
scope, expires and revokes, is **confined by a date lock** (again both halves:
the unlocked key must reach *before* the window, or an empty table would pass),
is **refused a lock it cannot enforce**, and a named link is not a way in. The block **mints
and then destroys** every slug: a test that leaves live capabilities behind
manufactures the exact thing this feature exists to keep countable. The ledger
is a *file* — `mint()` runs inside `$(...)`, and a bash array appended in that
subshell is discarded, which "cleaned up" one slug of eleven and reported
success.
