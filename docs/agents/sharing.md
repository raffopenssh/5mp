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
* **Tags & extension** (2026-08-16). Tags group links issued for one purpose
  (e.g. every link a report cites → `report`) and live in
  `short_link_tags(slug, tag)` — migration **061**, which dropped the single
  `short_links.tag` column of 060; see "Tags" below for why a set.
  Set at mint time (`tags:[…]` or `tag` in the POST body, sanitised to
  `[a-z0-9_-]{≤32}`). `POST /api/shortlink/{slug}/extend {days}` pushes one
  guest link's expiry out (from the later of now/current, capped at
  `guestMaxTTL` from now); `POST /api/shortlinks/extend {tag, days}` does every
  live key with that tag in one call. Both refuse guests. The Sharing sheet
  shows tag chips, days-remaining and a renew icon per key and per group.
* **A guest link to an export file retains the file.** A key pointing at
  `/api/geopackage/{id}/download` promises the bytes exist as long as the key
  lives: `retainGeoPackageForLink` pushes the job's `expires_at` out to the
  link's (never in) at mint time and on every extend, and the hourly gpkg
  sweeper re-asserts it for links created before the rule existed. The bell
  card shows the purge date, days left, the link's tag (`link_tag`, computed
  on read from `short_links`, never stored) and a `+30 days` button
  (`POST /api/geopackage/{id}/extend`, owner only — guests 401).

## Shared files (2026-08-16)

Replaces the ad-hoc `busybox httpd` on :8099 that served `/tmp/gpkg_share`
unauthenticated and unexpiring. `srv/shared_files.go`, table `shared_files`
(migration 062), bytes under `data/shared_files/{id}/{name}`.

* **A file share IS a guest link** whose target is `/api/files/{id}/download`
  — minted through the ordinary `ShareLink` dialog, so revoke/expiry/tags/
  renewal/the Sharing sheet all come from the existing machinery. One
  credential system, not two.
* **The file adopts the key's lifetime.** Default TTL 21 days
  (`sharedFileTTL`); `retainSharedFileForLink` pushes `expires_at` out (never
  in) at mint, extend, and hourly sweep — a 90-day key keeps the file 90
  days. The sweeper (`sweepSharedFiles`, called from `sweepGeoPackages`)
  deletes bytes and row together, plus hour-old orphan dirs.
* **Scope:** owner = `shortCallerRef` (`pwd_ref`); list/extend/delete are
  owner-only, another login gets **404** (invariant 6). Download is open to
  any authenticated session or guest — the id is a random token and being
  fetched via a link is the point (gpkg precedent). Anonymous = 401.
* **Upload refused** for guests and for the sandbox tenant (test2026): a demo
  password must not fill the disk or serve files under this domain's name.
  1 GB cap (`http.MaxBytesReader`), filename sanitised (`sharedFileName`),
  a partial write is deleted, never kept under the advertised name.
* **Delete switches keys off**: `DELETE /api/files/{id}` removes bytes+row
  and revokes every live guest link targeting the download in the same
  stroke — a key whose target is gone must read "switched off", not 410.
* **Scope side effect guarded:** `scopeFromURL` returns empty for any
  `/api/…` target, otherwise the default-on layer set would have granted
  every file key the patrol scope.
* **UI:** "Shared files" section in Admin → Access & Sharing
  (`loadFilesSection` in globe.html): upload (control absent, not disabled,
  where refused), rows with size/downloads/kept-until, read-only link-tag
  chips (computed on read via `linkTagsForTarget`), Share → ShareLink dialog,
  renew (+30d) shown only ≤14 d out, delete with a confirm that names the
  consequence (keys switched off).
* **Upload progress lives across the button** (2026-08-16). The first version
  used a hidden `<input>` + `fetch()`, which has **no upload progress events**:
  a 118 MB zip on a domestic uplink was minutes of a button that looked dead,
  reported as "nothing happens" (the server was fine — 120 MB in 0.5 s over
  loopback). Now `filesUploadSend` uses **XHR** for `upload.onprogress` and
  renders four states through one function, `uplRender` (`.upl*` CSS in
  `globe.css`):
  - *sending* — determinate fill across the button + `MB / MB · rate · ETA` +
    `%` + a cancel `✕` (which must `preventDefault`, or the surrounding
    `<label>` reopens the file picker); rate is EWMA-smoothed, a raw
    per-event rate flickers and reads as a broken meter.
  - *processing* — bytes acknowledged, server still storing: an indeterminate
    sweep, **not** 100%. Any number here would be invented (invariant 1: an
    unfinished unit must say so).
  - *done* / *error* — the failure names its reason **in the control** with a
    Retry that reuses the already-picked `File`; a toast alone scrolls away.
    Status codes map to plain titles, and 413/403 are `canRetry: false`
    (retrying cannot help). Oversize is refused **client-side** against
    `FILES_MAX_UPLOAD` — sending 3 GB for four minutes to be told "too large"
    is the worst of both.
  `loadFilesSection` re-renders the control **only when it is idle**, or the
  reload after an upload would erase the running bar / the error being read.
  `xhr.timeout = 0` on purpose: the server's own idle deadline (`longUpload`,
  10 min of *no bytes*) governs, a wall-clock cap would kill a slow but
  healthy 1 GB upload.

## A `?pwd=` for another login switches the session (2026-08-16)

`PasswordMiddleware` checked the cookie **first** and, finding a valid one,
stripped the query param and served on. That half-applied a switch:
`RequestPwd`/`RequestEnv` prefer the param, so one page load was two
identities — a shell rendered as the new login, filled by XHRs that were still
the old one. Opening a colleague's `?pwd=` link showed *your* tenant's links,
AOIs and patrol data under their name, with the mismatch visible nowhere.

The URL is the newer statement of intent, so it wins: a `pwd=` that differs from
the cookie is adopted into the cookie, then scrubbed by the usual redirect (an
`/api/` path is answered as that login on the same request, since a download
must arrive as the response to the request that asked for it). The *same*
password does not re-issue the cookie — a `Set-Cookie` on every page view is a
session that resets. A guest capability never reaches this branch: it arrives on
`/s/`, resolved above the gate, and never as a `pwd=`. Pinned by
`pwd_in_url_switches_the_session` in `tests/api_tests.sh`, which asserts on the
tenant's own autofetch list (0 → ≥1) rather than a link count, because a count
that is 0 for both logins would pass while nothing switched.

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

**Tags.** A purpose tag is bookkeeping, not a grant: setting, renaming or
clearing one never remints, never changes what a key shows and never changes
when it dies. Server: `srv/shortlink_tags.go`. Frontend: **one** control,
`srv/static/tagchips.js` (`TagChips.mount`), used by the share dialog, the
admin sheet and the gpkg export card.

*A link carries a SET of tags.* 060's single column made the two truthful
answers exclusive — a link cited by a report **and** handed out at a workshop
had to pick, and picking took it out of the next "renew #report", the exact
accident tags exist to prevent. 061 moved the pairs to `short_link_tags` and
**dropped** the old column rather than keeping it in sync (a duplicated fact
with two writers drifts; a reader selecting the dead column would see the first
tag as if it were the only one). `tags` is served sorted; `tag` is still in
every response as the *first* of them, for one-word readers. Tag rows travel
with the slug on a rename and are deleted with the link — a pair pointing at a
dead slug keeps the tag in the vocabulary, describing nothing.

`POST /api/shortlink/{slug}/retag` takes `{tags:[…]}` (replace), `{add}`,
`{remove}` or legacy `{tag}`; a word that sanitises to nothing is a **400**, not
a silent no-op. `POST /api/shortlinks/retag {tag,new_tag}` renames **everywhere**
(renaming one row would fork the group), merges by `INSERT OR IGNORE` — an
`UPDATE` aborted the whole rename when one link already carried the target — and
`{delete:true}` clears a tag from every link. `GET /api/shortlink-tags` returns
`tags` (names, most recent first) **and** `detail` with per-tag link/live counts:
a chooser that cannot tell "report (12 links)" from a typo made once spreads the
typo. Every verb is scoped through `shortLinkOwned`/`shortCallerRef`.

*Chip UI.* Blue, never the key's amber. A **soft 6px rectangle with a dashed
outline**, not a pill: this app spends pills on status ("key", "expired",
"patrol tracks"), so a tag — an editable label — must not borrow that shape, and
dashed matches the "# add tag" slot beside it, making chip and empty slot one
control in two states. Editing happens **inside the chip's own box** with a
borderless input: the previous version nested a bordered pill in a bordered
chip, so a rename drew *two* outlines around one word. The add slot stays
visible while tags exist, because an "add" that vanishes after the first one
teaches that the set holds one. `mount` retires any previous controller on the
element (`el.__tc`) — two live controllers on one node meant a × repainted from
the stale tag array and appeared to do nothing.

*Autocomplete is inline, not a dropdown.* A `<datalist>` gets a browser-drawn
arrow inside the chip and a floating menu over the dialog — a second boundary
around one word. Instead the remainder of the best match is its own
`.tc-ghost` span after the caret. It is an **element, not selected text**:
selected-text-in-an-input is not tappable and a touch keyboard has no Tab, so
the address-bar trick left the suggestion visible and unacceptable on a phone.
Accepted by tap (`mousedown`/`touchstart`, never `click` — blur would commit
first), Enter, Tab, → or End; one Escape dismisses the suggestion, a second
cancels the edit. It never regrows over a Backspace. Ranking is prefix match
then link count, excluding tags the link already carries (a suggestion whose
acceptance is a no-op reads as broken). The input is sized in **px from a
measured mirror on `<body>`**, never in `ch`: `ch` is the width of "0", so a
proportional font left a gap and "rep|ort" read as two words — and a mirror
appended *inside* the flex chip measures 0.

On create, dedupe **adds** requested tags to the row it lands on rather than
overwriting or dropping them (it is a set, and that row may already serve
another purpose); the response echoes what is stored.

**Renewal appears only near death.** The `+30d` buttons became a single amber
calendar-plus icon (`.sh-btn-renew`, and `.gpkg-renew` on export cards) shown
only when ≤14 days remain (`SHARING_RENEW_DAYS`); the group-level
"renew #tag" likewise only when a member is close. An always-on extend button
taught people to click it ritually, which is how keys live forever.

## Tests

`tests/api_tests.sh`, eight guest checks plus four on tags and the session
switch (`tags_are_a_set_not_one_word`, `tags_survive_slug_rename_and_merge`,
`tag_vocabulary_carries_counts`, `pwd_in_url_switches_the_session`); the fixture
sweeper deletes `short_link_tags` rows too, or a test's tags outlive it in the
vocabulary. The guest checks: a guest reads, is refused writes/admin/
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
