# Share links: short names and guest capabilities

_Read when working on "Copy link", the admin sharing sheet, or anything that
delegates access. Server side is done and committed; the UI is not._

## What exists (committed)

| Piece | Where |
|---|---|
| Table + the reasoning | `db/migrations/052-short-links.sql` |
| Create / rename / list / revoke, `/s/{slug}` | `srv/shortlink.go` |
| Guest session, read-only enforcement | `srv/guest.go` |
| Middleware hook, `RequestEnv` fallback | `srv/auth_middleware.go` |
| `RequestPrincipalID`, `visibilityFingerprint` | `srv/aoi.go` |
| Routes, `pageData.IsGuest`, chip label | `srv/server.go` |

API: `POST /api/shortlink {url,title?,kind?,slug?,guest?}` →
`{slug,short,url,guest,expires_at,reused}`;
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

## What is left to build (UI)

1. **One share popup, used by every copy/share element**, modelled on
   `srtm-lidar-at` (see the screenshots in the originating conversation): the
   URL shown with the slug as an inline `contenteditable` in green, `tap green
   name to rename` under it, one **Copy link** button. Blur commits the rename
   and re-copies the fresh URL. On mobile it is the same component — that is
   the point of making it minimal.
2. **Shorten every copyable link**, including downloads: `copyExportLink`,
   `shareCurrentView`, `copyMapSheetLink`, `copyAdminTabLink`, the report and
   feed links (`grep -n "navigator.clipboard" srv/templates/globe.html` — 8
   call sites). One helper `shortenURL(url, {title, kind})` that falls back to
   the long URL if the POST fails; never block the copy on the network.
3. **Admin sheet: a `sharing` tab** next to `access`, rendering
   `GET /api/shortlinks` — groups per password (label is the non-secret
   `pwd_ref` handle, own group first), and per link: slug, target, kind, hits +
   last seen, expiry, a copy button, and Clear. Guest rows need to *look*
   different from named rows; they are keys.
4. **Create-guest affordance** in the share popup: a "share without my
   password" checkbox → `guest:true`, with the resulting link shown once,
   plainly labelled read-only and dated.
5. **`IsGuest` in the UI**: hide draw/edit/upload/admin entry points. The
   server refuses writes already; an editor that 403s on save is worse than one
   never offered. The session chip already shows the link's name.
6. **Tests**: `tests/api_tests.sh` — a guest slug reads a private AOI, is
   refused a POST, is refused `/api/admin/*`, and stops working after
   `DELETE /api/shortlink/{slug}`. Playwright: rename in the popup, copy,
   reopen.
