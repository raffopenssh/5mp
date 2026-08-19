# Testing & share-link params

_Split out of AGENTS.md. Read when working on this area._

## Test Helpers (test=1 mode)

Add `?test=1` to URL to enable advanced testing tools:

**Entry ID Badges:** Blue numbered badges (0, 1, 2...) on fire/deforestation entries

**Key TEST Functions:**
```javascript
// Navigate & inspect
TEST.scrollToEntry('deforestation', 50)  // Scroll to entry by ID
TEST.scrollToText('fire', 'safari')      // Find by content
TEST.inspectEntry('fire', 10)            // Show full details
TEST.findBrokenEntries('deforestation')  // Scan for issues

// Manipulate UI
TEST.expandAll('CAF_Chinko')             // Expand all accordions
TEST.setPopupHeight(2000)                // Resize popup
TEST.triggerLoadMore('deforestation', 'CAF_Chinko')  // Click load more

// Shortcuts
TEST.testDeforest('CAF_Chinko', 100)    // Scroll+inspect+click entry
TEST.getEntryCount('fire')               // Count entries
```

**Benefits:** 50%+ token reduction in debugging (direct access replaces manual scrolling/clicking)

**Docs:** `docs/TEST_HELPERS.md`, `docs/TEST_HELPERS_QUICK_REF.md`

---

## Testing

### Run Tests

```bash
# Run all tests
./tests/run_all.sh

# Run specific test suites
./tests/run_all.sh db    # Database tests (37 tests)
./tests/run_all.sh api   # API tests (31 tests)
./tests/run_all.sh ui    # UI URL tests (20 tests)
```

### Browser Test Mode

Add `?test=1` to URL to enable `window.TEST` helper:

**Browser Setup:**
- Resize browser to **1280x1400** (or taller) to see full popup content
- This allows testing of all accordion sections without scrolling

```javascript
// Navigate to: http://localhost:8000/?pwd=test2026&test=1
// In browser console:
TEST.assertExists('#map', 'Map exists');
TEST.assertVisible('.stats-panel', 'Stats visible');
TEST.isPanelOpen('admin');  // Returns true/false
TEST.isPopupOpen('CAF_Chinko');
TEST.done();  // Print results
```

### Share Link Testing

URL params encode full UI state for reproducible tests:

| Param | Example | Description |
|-------|---------|-------------|
| `test` | `1` | Enable TEST helper |
| `panel` | `filter,star,admin,upload` | Open panel |
| `admin_tab` | `learning,features` | Admin tab |
| `popup` | `CAF_Chinko` | Open park popup |
| `sections` | `fire,deforestation` | Open accordions |
| `pinned` | `CAF_Chinko:fire_trajectory` | Pin layers |
| `tip` | `22.62154,6.61277` | The selected feature, as a **place**. Restoring re-asks the live map |
| `tip_layer` | `geomap-fill-car` | Which answer at that place — picks within the stack, never invents one |
| `detail` | `major` \| `main` \| `all` | Geography detail tier (omitted at the `main` default) |
| `geomap` | `sudan,car` | Show these geology sheets |
| `geomap_only` | `car:GC2/GO\|S` | Isolate these classes (unknown codes dropped) |
| `geomap_hide` | `car:Zeta` | Hide these classes |
| `geomap_opacity` | `car:80` | Per-sheet opacity % (omitted at the 55 default) |
| `map_sheet` | `car`, `sudan`, `histmap` | With `panel=admin&admin_tab=map-settings`: flash that sheet's card. Points at the downloads and provenance, not at the map — composes with `geomap=` |
| `aoi_menu` | `XSA_Study_Area` | Open that area's download menu |
| `aoi_menu_item` | `gpkg`, `gpkg_light`, `kml`… | Highlight one entry in it. A **built** GeoPackage downloads immediately; an unbuilt one only explains itself — a link never starts a build |
| `gpkg` | `<job id>` | Open the bell on that GeoPackage export's card |
| `starred_parks` | `CAF_Chinko,COD_Virunga` | Star parks |
| `notif` | `1` | Open notification dropdown |
| `notif_fire` | `CAF_Chinko:2026_grp_2caaa51b` | Zoom to fire + pin (see below) |
| `notif_upload` | `10.52,18.19` | Zoom to patrol location |
| `notif_pub` | `CAF_Chinko` | Open popup with research |
| `notif_download` | `123` | Download MBTiles file |

#### Fire Notification Share Links

Format: `?notif_fire=PARK_ID:YEAR_grp_HASH`

Example: `?notif_fire=CAF_Chinko:2026_grp_2caaa51b`

This will:
1. Open the notification dropdown
2. Expand the fire notification group for that park
3. Load the fire trajectory from features API
4. Look up friendly name from fire-realtime API (e.g., "Alpha-2")
5. Display trajectory on map and zoom to it
6. Pin the fire layer with friendly name

**To get the correct format:**
```bash
# Query notifications table
sqlite3 db.sqlite3 "SELECT park_id, reference_id FROM notifications WHERE notification_type = 'fire_alert' LIMIT 5"
# Returns: CAF_Chinko|CAF_Chinko_2026_grp_2caaa51b

# Format for share link: parkId:year_grp_hash
# Remove park prefix: CAF_Chinko:2026_grp_2caaa51b
```

### Playwright (Full UI)

```bash
npm install -D @playwright/test
npx playwright test tests/playwright/
```

---

## Interactive map stress testing (browser-tool sessions)

**Opt-in only.** This is NOT part of `run_all.sh` or `runUITests()` and must
not be added to them — it takes 5–8 minutes, mutates UI state (time window,
basemap, pins), and belongs to explicit "stress test the map" asks, not to
the per-change test loop.

The phases are packaged in **`tests/stress/map_stress.js`**, which the server
also serves at `/static/map_stress.js` (symlink in `srv/static/`), so a
browser-tool session injects it in one cheap eval instead of pasting 16 KB:

```javascript
eval(await (await fetch('/static/map_stress.js')).text());
MapStress.setup();
await MapStress.runAll();          // or one phase, e.g. MapStress.phases.geology()
MapStress.report();                // did the style return to baseline?
```

Desktop phases want `/?pwd=test2026&test=1&popup=CAF_Chinko`. Two phases
added after the 2026-08 mobile black-map session:

- **`mobileTouch`** — coarse-pointer soak (real TouchEvents: taps on real
  features, pinches, popup open/close via the maptip action, resize after
  every cycle). Run it under mobile emulation (`emulate_device pixel_7`) on
  any view with `pinned-*` layers, e.g. an AOI guest share link. This is the
  phase that found the floatui.js per-popup `resize`-listener leak.
- **`contextLoss`** — WebGL context-loss drill. **Skipped unless you set
  `MapStress.allowReload = true`, and it navigates the page**: globe.html's
  `webglcontextrestored` handler recovers by freezing the view into the URL
  (`buildShareUrl()`) and reloading, because MapLibre 4.1 comes back from a
  restored context with a stuck render queue and corrupted tile/glyph
  textures (black or garbled map — the mobile screenshot bug). Run it last
  or standalone; after the reload, assert the URL carries lat/lng/z/pins and
  the map renders. Never put it inside `runAll()` unattended.

How the 2026-08 stress sessions were run (found the metaPromise retry bug,
the setOpacity(NaN) poison, and the pin tabLabel fix — commits bb30e33,
2337250). Reuse this recipe when asked to "make sure hours-long browsing
stays stable".

**Setup** — navigate to `/?pwd=test2026&test=1`, then instrument *before*
interacting (on `/s/` guest links: instrument immediately, restore is async):

```javascript
window.__errs = [];
addEventListener('error', e => __errs.push('ERR:' + (e.message || '')));
addEventListener('unhandledrejection', e => __errs.push('REJ:' + String(e.reason).slice(0, 150)));
window.__snap = () => { const st = map.getStyle(); return {
    layers: st.layers.length, sources: Object.keys(st.sources).length,
    images: map.listImages().length, errs: __errs.length }; };
```

Baseline is **10 layers / 4 sources / 1 image**. The invariant: after every
churn phase that ends "everything off", `__snap()` must equal baseline and
`__errs` must be empty. Residue in `layers`/`images` = a leak (grep the ids:
`geomap-*`, `geopat-*`, `histmap-*`, `pinned-*`, `lod-*`). Exception:
`geomap-structural-*` layers persist at `line-opacity: 0` by design (soft-off).

**Also check the console**: `__errs` misses `console.error` — MapLibre paint
errors (e.g. the NaN opacity spam) only show in browser console_logs.

**Synthetic input** — dispatch on `map.getCanvas()`; MapTip listens there:

```javascript
const cv = map.getCanvas(), rect = cv.getBoundingClientRect();
const mm = (x, y) => cv.dispatchEvent(new MouseEvent('mousemove',
    { bubbles: true, clientX: rect.left + x, clientY: rect.top + y }));
const click = (x, y) => { for (const t of ['mousedown', 'mouseup', 'click'])
    cv.dispatchEvent(new MouseEvent(t, { bubbles: true,
        clientX: rect.left + x, clientY: rect.top + y, button: 0 })); };
// Time-slider drags need PointerEvents on .time-slider-handle.start/.end
// (pointerdown on handle, pointermove/up on document).
```

Aim storms at *real* features, not random pixels: collect targets via
`map.queryRenderedFeatures({layers:[...]})`, project midpoints with
`map.project()`, then hover at 15–25 ms cadence / click at 50–60 ms.
After a click storm: Esc must clear every `.maptip.visible`. Beware: at a
2-year window fires cover everything, so a "void" click may legitimately pin.

**Phases that earned their keep** (each ends with a baseline check):
1. Stats-panel `toggleViewLayer('fires'|'deforestation'|'settlements'|'pixels')` ×4+.
2. Geology on/off incl. **mid-load toggle-off**; opacity slider incl. garbage
   values (`slider.value=''` + input event); color modes; lith/age chips;
   commodity rows (`MapLegend.geoCommodity`), min-weight 1/2/3, per-cell grid
   (`MapLegend.geoCell`) — drawn count must go filtered→back (104→22→104);
   Rocks/Junctions tabs; structural on/off.
3. HistMap toggle incl. mid-load; opacity drags.
4. Legend menu (`MapLegend.menu` button) + basemap switches with overlays on:
   auto-opacity must re-pick (0.52 dark / 0.72 sat), a manual value must survive.
5. Park popup pins: `?popup=CAF_Chinko`, click the
   `togglePinFromIcon` icons next to accordion titles; **widen to 2 years
   first** (drag start handle) so pins carry thousands of shapes; fly-to,
   MAP DETAIL modes (openPinModeMenu → Automatic/Full/Fast), clearAllPinnedLayers.
6. Hover/click storms over pinned + lod layers (targets from
   queryRenderedFeatures). Tabs on multi-answer cards must show type names
   ("River", "Fire"), never layer ids.
7. Guest share restore: open an `/s/` slug, storm *during* restore, verify
   popup + geology + pins all survive and reload cleanly.

**Gotchas** — DOM refs go stale after popup re-renders (re-query, don't cache
button handles); many "checkboxes" are styled divs with `onclick`, so find by
`[onclick*="geoCommodity"]` etc., not `input[type=checkbox]`; `performance.memory`
is a cheap heap sanity check between phases.
