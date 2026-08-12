# Authentication & tenancy

_Split out of AGENTS.md. Read when working on this area._

## Authentication

### Password-Protected Endpoints

Most endpoints require password via:
- Cookie: `access_pwd=test2026`
- Query param: `?pwd=test2026`

Valid passwords: loaded from `ACCESS_PASSWORDS` env var or `secrets.env` (fallback: `test2026`)

### ⚠️ Patrol data is scoped to a TENANT, not shared across passwords

Patrol effort belongs to the rangers who uploaded it, not to everyone holding
an alpha password. The scoping key is the `env` column that already existed on
`effort_data`, `subcell_visits`, `gpx_uploads`, `track_points`, `upload_queue`,
`notifications` and now `autofetch_sources` — `srv/tenant.go` turns it from
"prod vs the test sandbox" into a real per-password tenant:

    PASSWORD_ENVS=<client-pwd-a>:prod,<client-pwd-b>:prod,<client-pwd-c>:prod,test2026:test

**An access password that is not listed gets its own empty tenant**
(`pw_<ref8>`). That direction is deliberate: adding a password must never
silently widen access, so the failure mode is "I see no patrol data", not
"I see yours". Omit `PASSWORD_ENVS` entirely and the historical single-tenant
behaviour applies (`test2026` → test, everything else → prod), which is what a
fresh checkout and `tests/` expect.

Nothing new was invented for the read path: **every query that already filtered
`env = RequestEnv(r)` became tenant-correct for free**, and `cacheKey` already
carried `RequestEnv`. What changed is only what `RequestEnv` *means*.

* `isTestEnv(r)` now means **"outside the client tenant"**, not "is test2026".
  Its callers all ask one question — may this request see client-derived data —
  and that has exactly one answer per tenant. `clientTenant`/`sandboxTenant`
  constants; never compare against the string `"test"` again.
* A server-side job that acts through our own HTTP API must pick a password for
  the *tenant it is working for* (`pwdForTenant`), and **refuse** when none
  maps. `runAutofetchSource` used `validPasswords[0]`, which files a client's
  EarthRanger tracks under whichever password happens to sort first.
* `autofetch_sources` is per tenant (migration **049**): a subscription names
  the client's tracking server, their username and the parks they operate in,
  and it feeds their pixels. Every by-id write is `WHERE id = ? AND env = ?`,
  and `run` checks ownership **in the handler**, not in the worker (the worker
  is also the scheduler's entry point and must not need a request).
* Onboarding is *not* tenant-scoped — a park is global data. `onboard_park.py`
  now skips only `env='test'` instead of requiring `env='prod'`, or a
  non-default tenant's request would never be processed.

### ⚠️ An authenticated response must not be `Cache-Control: public`

The first version of the tenant split was correct on the server and still
showed the wrong answer in a browser: after visiting as one account, switching
to another served the **first account's body from the browser's own HTTP
cache**, which is keyed on the URL and ignores the cookie. Symptom: "the pixels
are gone" (or worse, another tenant's pixels), while `curl` returns the right
thing — so it reads as a server bug and is not one.

`PrivateCacheMiddleware` (`srv/security.go`) downgrades `public` → `private`
and adds `Vary: Cookie` on everything behind the password gate. It downgrades
rather than deletes, so handlers keep the revalidation they asked for.
`isPublicPath()` is the **single** list shared with `PasswordMiddleware` — two
copies would drift, and the drift is invisible in both directions (a leak, or a
needlessly uncacheable asset).

UI: `window.HAS_PATROL` (from `pageData.HasPatrol`) says whether this tenant
owns any effort at all. When it does not, the Active Pixels / Total Distance
rows are dimmed and inert and the animator's patrol chips are `unavailable` —
including when a **share link** names them, which is dropped rather than
switched on. An empty layer reads as a broken feature; a dimmed one reads as
"not yours".

Pinned by `go test ./srv/ -run 'Tenant|RequestEnv'` and the
"Patrol data tenants" block in `tests/api_tests.sh`. Note the assertion there
is *"does not see THESE pixels"*, not "sees nothing": a tenant with its own
patrols is the normal case.

### Unauthenticated Endpoints

These paths bypass password check (see `srv/auth_middleware.go`):
- `/static/downloads/*` - Downloadable files
- `/robots.txt` - SEO
- `/sitemap.xml` - SEO
- `/static/robots.txt`
- `/static/sitemap.xml`

### Admin-Only Endpoints

Require admin login (see `srv/server.go` lines with `RequireAdmin`):
- `POST /admin/approve`, `/admin/reject`
- `POST /admin/upload/fire`, `/admin/upload/ghsl`
- `POST /api/admin/approve-feature`, `/api/admin/reject-feature`
- `POST /api/admin/bulk-approve`, `/api/admin/bulk-reject`
- `POST /api/admin/delete-upload`, `/api/admin/hide-notification`

---
