// Map/legend stress soak — OPT-IN ONLY. Not part of run_all.sh / runUITests().
// Run when asked to verify hours-long browsing stability (see
// docs/agents/testing.md "Interactive map stress testing").
//
// Usage: open /?pwd=test2026&test=1&popup=CAF_Chinko , then inject cheaply
// (the server serves this file via a symlink in srv/static/):
//   eval(await (await fetch('/static/map_stress.js')).text());
//   await MapStress.runAll()        // every phase, ~5-8 min
//   await MapStress.phases.geology() // one phase
//   MapStress.report()              // snapshot + errors so far
// Mobile: emulate a phone, open an AOI guest link, run phases.mobileTouch().
// phases.contextLoss() RELOADS THE PAGE and needs MapStress.allowReload=true.
//
// Invariant: after any phase that ends "everything off", __snap() must equal
// the baseline captured at setup (10 layers / 4 sources / 1 image as of
// 2337250) and __errs must stay empty. geomap-structural-* layers persisting
// at line-opacity 0 is by design. ALSO check the browser console: MapLibre
// paint errors (e.g. fill-opacity NaN) bypass window.onerror.

window.MapStress = (() => {
    const s = ms => new Promise(r => setTimeout(r, ms));
    let baseline = null;

    function setup() {
        window.__errs = window.__errs || [];
        if (!window.__errsHooked) {
            addEventListener('error', e => __errs.push('ERR:' + (e.message || '')));
            addEventListener('unhandledrejection',
                e => __errs.push('REJ:' + String(e.reason).slice(0, 150)));
            window.__errsHooked = true;
        }
        window.__snap = () => {
            const st = map.getStyle();
            return { layers: st.layers.length,
                     sources: Object.keys(st.sources).length,
                     images: map.listImages().length, errs: __errs.length };
        };
        baseline = baseline || __snap();
        return baseline;
    }

    // ---- synthetic input on the map canvas (MapTip listens there) ----
    const cv = () => map.getCanvas();
    const rect = () => cv().getBoundingClientRect();
    const mm = (x, y) => cv().dispatchEvent(new MouseEvent('mousemove',
        { bubbles: true, clientX: rect().left + x, clientY: rect().top + y }));
    const click = (x, y) => { for (const t of ['mousedown', 'mouseup', 'click'])
        cv().dispatchEvent(new MouseEvent(t, { bubbles: true,
            clientX: rect().left + x, clientY: rect().top + y, button: 0 })); };
    const esc = () => document.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

    // Aim at REAL features: random pixels miss everything at low zoom.
    function targetsFrom(layers, cap) {
        const r = rect(), out = [];
        for (const id of layers) {
            if (!map.getLayer(id)) continue;
            for (const f of map.queryRenderedFeatures({ layers: [id] }).slice(0, 20)) {
                const g = f.geometry;
                const c = g.type === 'Point' ? g.coordinates
                    : g.type === 'LineString' ? g.coordinates[g.coordinates.length >> 1]
                    : g.type === 'MultiLineString' ? g.coordinates[0][0]
                    : g.type === 'Polygon' ? g.coordinates[0][g.coordinates[0].length >> 1]
                    : g.type === 'MultiPolygon' ? g.coordinates[0][0][0] : null;
                if (!c) continue;
                const p = map.project(c);
                if (p.x > 20 && p.x < r.width - 20 && p.y > 20 && p.y < r.height - 20)
                    out.push([p.x, p.y]);
            }
        }
        return out.slice(0, cap || 60);
    }

    // Widen the time window by dragging the start handle (PointerEvents:
    // down on the handle, move/up on document). ~360px left ≈ 2 years.
    async function widenWindow(px) {
        const h = document.querySelector('.time-slider-handle.start');
        if (!h) return false;
        const r = h.getBoundingClientRect();
        const sx = r.left + r.width / 2, sy = r.top + r.height / 2;
        const pe = (t, x, tgt) => tgt.dispatchEvent(new PointerEvent(t,
            { bubbles: true, clientX: x, clientY: sy, pointerId: 1,
              isPrimary: true, button: 0 }));
        pe('pointerdown', sx, h);
        for (let x = sx; x > sx - px; x -= 40) { pe('pointermove', x, document); await s(40); }
        pe('pointerup', sx - px, document);
        await s(2500);
        return true;
    }

    const phases = {
        // Stats-panel layer toggles, repeated.
        async statsToggles() {
            for (let i = 0; i < 4; i++)
                for (const l of ['fires', 'deforestation', 'settlements', 'pixels']) {
                    toggleViewLayer(l); await s(200);
                }
            await s(1200);
        },

        // Geology: sheets, opacity (incl. garbage), filters, cells, structural.
        async geology() {
            GeoMap.setAll(true); await s(2000);
            // opacity: garbage must be a no-op (the NaN-poison regression)
            GeoMap.setOpacity(NaN); GeoMap.setOpacity(undefined); GeoMap.setOpacity('x');
            GeoMap.setOpacity(0.3); await s(200); GeoMap.setOpacity(0.52); await s(200);
            // commodity rows + weight via legend body (styled divs, not inputs)
            const q = oc => [...document.querySelectorAll('[onclick]')]
                .filter(e => e.offsetParent && (e.getAttribute('onclick') || '').includes(oc));
            const chip = document.querySelector('.ml-chip.geo .ml-chip-main');
            if (chip) { chip.click(); await s(700); }
            const full = GeoMap.drawnUnitCount();
            for (const n of ['gold', 'copper'])
                { q(`geoCommodity('${n}')`)[0]?.click(); await s(350); }
            const filtered = GeoMap.drawnUnitCount();
            for (const w of [3, 2, 1]) { q(`geoMinWeight(${w})`)[0]?.click(); await s(350); }
            for (const n of ['gold', 'copper'])
                { q(`geoCommodity('${n}')`)[0]?.click(); await s(250); }
            if (GeoMap.drawnUnitCount() !== full)
                __errs.push('GEO: drawn count did not restore ('
                    + filtered + ' -> ' + GeoMap.drawnUnitCount() + ' != ' + full + ')');
            // per-cell grid taps, incl. rapid churn on one cell
            const cells = () => q('geoCell').filter(e => e.offsetParent);
            const n0 = cells().length;
            for (let i = 0; i < Math.min(6, n0); i++) { cells()[i]?.click(); await s(250); }
            for (let i = 0; i < Math.min(6, n0); i++) { cells()[i]?.click(); await s(200); }
            for (let i = 0; i < 8; i++) { cells()[0]?.click(); await s(70); }
            await s(500);
            // structural soft-on/off
            GeoMap.setStructural('active_faults', true);
            GeoMap.setStructural('craton_edges', true); await s(1200);
            GeoMap.setStructural('active_faults', false);
            GeoMap.setStructural('craton_edges', false); await s(500);
            esc();
            GeoMap.setAll(false); await s(800);
        },

        // HistMap incl. mid-load toggle-off and opacity garbage.
        async histmap() {
            HistMap.toggle(); await s(300); HistMap.toggle(); await s(800); // mid-load off
            HistMap.toggle(); await s(2000);
            HistMap.setOpacity(NaN); HistMap.setOpacity(0.3); await s(200);
            HistMap.setOpacity(0.85); await s(200);
            HistMap.toggle(); await s(800);
        },

        // Basemap switches with overlays on: auto-opacity must re-pick,
        // a manual value must survive the switch.
        async basemaps() {
            GeoMap.setAll(true); await s(1500);
            MapLegend.pickBasemap('satellite-s2'); await s(3500);
            MapLegend.pickBasemap('dark'); await s(3500);
            GeoMap.setOpacity(0.33); await s(300);
            MapLegend.pickBasemap('satellite-s2'); await s(3500);
            const op = map.getLayer('geomap-fill-car')
                ? map.getPaintProperty('geomap-fill-car', 'fill-opacity') : null;
            if (op !== null && Math.abs(op - 0.33) > 0.01)
                __errs.push('GEO: manual opacity lost across basemap switch: ' + op);
            MapLegend.pickBasemap('dark'); await s(3500);
            GeoMap.setAutoOpacity(true); await s(300);
            GeoMap.setAll(false); await s(800);
        },

        // Park-popup pins at 2-year density + detail modes + hover/click storms.
        // Needs ?popup=CAF_Chinko (or any park with data).
        async pins() {
            await widenWindow(360);
            const icons = () => [...document.querySelectorAll(
                '.maplibregl-popup [onclick*="togglePinFromIcon"]')]
                .filter(e => e.offsetParent);
            const n = icons().length;
            for (let i = 0; i < n; i++) { icons()[i]?.click(); await s(1200); } // re-query: popup re-renders
            map.jumpTo({ center: [23.9, 6.9], zoom: 8.5 }); await s(3000);
            // detail modes on the fire chip
            const q = oc => [...document.querySelectorAll('[onclick]')]
                .filter(e => e.offsetParent && (e.getAttribute('onclick') || '').includes(oc));
            const opt = t => [...document.querySelectorAll('*')].filter(e =>
                e.offsetParent && e.children.length <= 2 &&
                (e.textContent || '').trim().startsWith(t)).pop();
            for (const m of ['Full shapes', 'Fast', 'Automatic']) {
                const b = q("openPinModeMenu(this,'CAF_Chinko_fire')")[0];
                if (!b) break;
                b.click(); await s(600);
                opt(m)?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                await s(2500);
            }
            esc();
            // storms over whatever is pinned
            const ids = map.getStyle().layers.map(l => l.id)
                .filter(id => /^(pinned|lod)-/.test(id) && !/-text$/.test(id));
            const ts = targetsFrom(ids, 50);
            for (const [x, y] of ts) { mm(x, y); await s(20); }        // hover storm
            for (const [x, y] of ts.slice(0, 40)) { click(x, y); await s(55); } // click storm
            await s(800); esc(); await s(400);
            const stuck = [...document.querySelectorAll('.maptip')]
                .filter(t => t.classList.contains('visible')).length;
            if (stuck) __errs.push('TIP: ' + stuck + ' tips still visible after Esc');
            // full unpin must restore baseline
            [...document.querySelectorAll('[onclick*="clearAllPinnedLayers"]')]
                .find(e => e.offsetParent)?.click();
            await s(1500);
        },

        // MOBILE / coarse-pointer soak (found the floatui resize-listener
        // leak + the WebGL context-loss black map, 2026-08). Real
        // TouchEvents: tap features (a tap = click with no prior mousemove,
        // which is how MapTip detects a coarse pointer), pan + pinch with a
        // tip pinned, open/close the popup via the tip's action button, then
        // fire resize — every leaked listener throws here. Works on any
        // view with pinned-* layers (e.g. an AOI share link).
        async mobileTouch() {
            const T = (() => {
                const el = () => cv();
                function touch(type, pts) {
                    const touches = pts.map(([x, y], i) => new Touch({
                        identifier: i, target: el(),
                        clientX: rect().left + x, clientY: rect().top + y }));
                    el().dispatchEvent(new TouchEvent(type, { bubbles: true, cancelable: true,
                        touches: type === 'touchend' ? [] : touches,
                        targetTouches: type === 'touchend' ? [] : touches,
                        changedTouches: touches }));
                }
                async function tap(x, y) {
                    touch('touchstart', [[x, y]]); await s(40); touch('touchend', [[x, y]]);
                    click(x, y);   // browsers fire compat mouse events after a tap
                }
                async function pinch(cx, cy, d1, d2) {
                    const p = d => [[cx - d / 2, cy], [cx + d / 2, cy]];
                    touch('touchstart', p(d1));
                    for (let i = 1; i <= 8; i++) { touch('touchmove', p(d1 + (d2 - d1) * i / 8)); await s(30); }
                    touch('touchend', p(d2));
                }
                return { touch, tap, pinch };
            })();
            const feats = () => targetsFrom(map.getStyle().layers.map(l => l.id)
                .filter(id => /^(pinned|lod)-/.test(id) && !/-text$/.test(id)), 30);
            for (let round = 0; round < 4; round++) {
                const ts = feats();
                if (ts.length) { await T.tap(...ts[round % ts.length]); await s(400); }
                await T.pinch(206, 400, 100, 260); await s(500);
                await T.pinch(206, 400, 260, 100); await s(500);
                for (const [x, y] of feats().slice(0, 10)) { await T.tap(x, y); await s(40); }
                // popup open/close via the tip's action button (this leaked a
                // resize listener per cycle until 2026-08)
                const act = document.querySelector('.maptip.sticky.visible .maptip-action');
                if (act) { act.click(); await s(900);
                    document.querySelector('.maplibregl-popup-close-button')?.click(); }
                esc(); await s(200);
                const errsBefore = __errs.length;
                window.dispatchEvent(new Event('resize')); map.resize(); await s(250);
                if (__errs.length > errsBefore)
                    __errs.push('LEAK: resize after popup cycle threw (round ' + round + ')');
            }
            esc(); await s(300);
        },

        // WebGL context loss drill. Android discards contexts under memory
        // pressure; globe.html's webglcontextrestored handler reloads the
        // page (URL carries the view), so in THIS harness we only verify the
        // handler is wired — run it LAST or standalone: the page navigates.
        // Skipped unless MapStress.allowReload = true.
        async contextLoss() {
            if (!window.MapStress || !MapStress.allowReload) {
                console.log('[MapStress] contextLoss skipped (set MapStress.allowReload = true)');
                return;
            }
            const gl = map.painter.context.gl;
            const ext = gl.getExtension('WEBGL_lose_context');
            if (!ext) { __errs.push('CTX: WEBGL_lose_context unavailable'); return; }
            ext.loseContext(); await s(800); ext.restoreContext();
            // page reloads ≈200ms later; nothing to assert here
        },

        // ANIMATOR toggle soak (2026-09-17). Every chip, every highlight
        // profile, open/close, date-window reopen, pixels toggle from the
        // panel while open — and after EVERY step `animCheck()` asserts the
        // invariants that, when broken, read as "the patrol layer showed":
        //   * live patrol pixels (grid-*) visible  ⇔  viewLayers.pixels && !Animator.animatingPatrol()
        //   * every chip's .on / aria-pressed  ⇔  Animator.isLayerOn(name)
        //   * FireSeason.*On()  ⇔  the season chip's state (one owner)
        //   * lod-view-* hidden only while a profile animates that row
        //   * closed ⇒ no canvas, no chips, season overlay back to what it was
        //     (unless a season chip was clicked by hand)
        async animator() {
            const A = window.Animator;
            if (!A) { __errs.push('ANIM: window.Animator missing'); return; }
            const FS = window.FireSeason;
            const gridVis = () => ['grid-halo', 'grid-glow', 'grid-fill', 'grid-cells']
                .filter(id => map.getLayer(id))
                .map(id => map.getLayoutProperty(id, 'visibility') || 'visible');
            const seasonState = () => FS ? {
                front: !!FS.frontOn(), vanguard: !!FS.vanguardOn(),
                entry: !!(FS.entryOn && FS.entryOn()), speed: !!(FS.speedOn && FS.speedOn()),
                patrolfront: !!(FS.patrolOn && FS.patrolOn()), patrolpressure: !!(FS.pressureOn && FS.pressureOn()) } : {};
            const seasonBefore = seasonState();
            let touchedSeason = false;
            function check(where) {
                const open = A.isOpen();
                const px = !!(window.viewLayers && viewLayers.pixels);
                const wantGrid = (px && !(open && A.animatingPatrol())) ? 'visible' : 'none';
                const gv = gridVis();
                if (gv.some(v => v !== wantGrid))
                    __errs.push(`ANIM[${where}]: patrol pixels ${JSON.stringify(gv)} want ${wantGrid} (pixels=${px} open=${open} animPatrol=${open && A.animatingPatrol()})`);
                const chips = [...document.querySelectorAll('.anim-chip[data-layer]')];
                if (!open && (chips.length || document.querySelector('#anim-canvas, canvas.anim-canvas')))
                    __errs.push(`ANIM[${where}]: closed but UI residue (${chips.length} chips)`);
                if (open && !chips.length) __errs.push(`ANIM[${where}]: open but no chips`);
                const ss = seasonState();
                for (const c of chips) {
                    const n = c.dataset.layer, on = A.isLayerOn(n);
                    if (c.classList.contains('on') !== on)
                        __errs.push(`ANIM[${where}]: chip ${n} .on=${c.classList.contains('on')} state=${on}`);
                    if (c.getAttribute('aria-pressed') !== String(on))
                        __errs.push(`ANIM[${where}]: chip ${n} aria-pressed=${c.getAttribute('aria-pressed')} state=${on}`);
                    if (n in ss && ss[n] !== on)
                        __errs.push(`ANIM[${where}]: season ${n} FireSeason=${ss[n]} chip=${on}`);
                    if (n === 'patrol' && on && c.classList.contains('hidden'))
                        __errs.push(`ANIM[${where}]: patrol chip on but hidden`);
                }
                if (!open && !touchedSeason) {
                    const diff = Object.keys(seasonBefore).filter(k => seasonBefore[k] !== ss[k]);
                    if (diff.length) __errs.push(`ANIM[${where}]: season overlay not restored on close: ${diff.join(',')}`);
                }
                // live LOD rows: hidden only while a profile animates the row
                const rows = { fires: ['fireGrid', 'firePts', 'trajs'], deforest: ['deforest'], settlements: ['settlements'] };
                const hl = open && A.highlight();
                const lyr = open ? A.layers() : [];
                for (const row of Object.keys(rows)) {
                    const hide = !!(hl && rows[row].some(n => lyr.indexOf(n) >= 0));
                    for (const suf of ['fill', 'line', 'point', 'dots', 'arrows']) {
                        const id = `lod-view-${row}-${suf}`;
                        if (!map.getLayer(id)) continue;
                        const v = map.getLayoutProperty(id, 'visibility') || 'visible';
                        if (v !== (hide ? 'none' : 'visible'))
                            __errs.push(`ANIM[${where}]: ${id} ${v} (hl=${hl} layers=${lyr})`);
                    }
                }
            }
            const chipNames = () => [...document.querySelectorAll('.anim-chip[data-layer]')]
                .filter(c => !c.classList.contains('unavailable')).map(c => c.dataset.layer);
            const SEASON = ['front', 'vanguard', 'entry', 'speed', 'patrolfront', 'patrolpressure'];
            const settle = async (ms) => { await s(ms || 700); };

            check('start');
            // 1. plain open (curator 'now'), step every profile twice around
            A.open(); await settle(3500); check('open');
            const profiles = A.highlightProfiles();
            for (let i = 0; i < profiles.length * 2 + 1; i++) {
                await A.setHighlight(); await settle(1800); check('hl-step-' + i);
            }
            for (const p of profiles) { await A.setHighlight(p); await settle(1500); check('hl-' + p); }
            await A.setHighlight(false); await settle(400); check('hl-off');
            // 2. every chip on then off, by click (clicks switch highlight off)
            for (const n of chipNames()) {
                const c = document.querySelector(`.anim-chip[data-layer="${n}"]`);
                if (SEASON.indexOf(n) >= 0) touchedSeason = true;
                c.click(); await settle(1500); check('chip-on-' + n);
                c.click(); await settle(600); check('chip-off-' + n);
            }
            // 3. rapid churn on patrol + pixels toggle from the panel while open
            for (let i = 0; i < 6; i++) { A.setLayer('patrol'); await s(120); }
            await settle(1500); check('patrol-churn');
            toggleViewLayer('pixels'); await settle(400); check('pixels-toggle-1');
            A.setLayer('patrol', true); await settle(1500); check('patrol-on-pixels-off');
            toggleViewLayer('pixels'); await settle(400); check('pixels-toggle-2');
            A.setLayer('patrol', false); await settle(400); check('patrol-off');
            // 4. play / pause / seek
            document.getElementById('anim-play')?.click(); await s(1500);
            document.getElementById('anim-play')?.click(); await s(300); check('play-pause');
            // 5. date-window reopen with a profile active, then close
            await A.setHighlight('now'); await settle(1500);
            await widenWindow(200); await settle(3000); check('reopen-widened');
            A.close(); await settle(600); check('closed-1');
            // 6. reopen/close cycles with a season profile and a chip click in between
            for (let i = 0; i < 3; i++) {
                A.open(); await settle(3000); check('cycle-open-' + i);
                if (profiles.indexOf('season') >= 0) { await A.setHighlight('season'); await settle(1800); check('cycle-season-' + i); }
                if (i === 1) { A.setLayer('deforest'); await settle(1000); check('cycle-chip-' + i); }
                A.close(); await settle(600); check('cycle-closed-' + i);
            }
            // 7. pixels toggle round-trip with the animator closed
            toggleViewLayer('pixels'); await s(300); check('closed-pixels-off');
            // 8. pixels OFF in the panel: no profile may switch patrol on
            //    (the "patrol layer showed" report, 2026-09-17)
            A.open(); await settle(3000); check('px-off-open');
            for (let i = 0; i < profiles.length + 1; i++) {
                await A.setHighlight(); await settle(1200); check('px-off-hl-' + i);
                if (A.layers().some(n => /^effort/.test(n)))
                    __errs.push('ANIM[px-off-hl-' + i + ']: profile ' + A.highlight() + ' animates patrol with pixels off');
                if (A.highlight() === 'patrol')
                    __errs.push('ANIM[px-off-hl-' + i + ']: patrol profile offered with pixels off');
            }
            const pchip = document.querySelector('.anim-chip[data-layer="patrol"]');
            if (pchip && !pchip.classList.contains('hidden') && !A.isLayerOn('patrol'))
                __errs.push('ANIM[px-off]: patrol chip shown with pixels off');
            toggleViewLayer('pixels'); await s(400); check('px-on-while-open');
            if (pchip && pchip.classList.contains('hidden'))
                __errs.push('ANIM[px-on-while-open]: patrol chip still hidden after pixels on');
            A.close(); await settle(600); check('closed-final');
            esc(); await s(300);
        },

        // FIRE VECTOR TILES (2026-09-17). A pinned fire layer over a big area
        // is served as vector tiles (srv/features_tiles.go, lodlayer.js
        // TILE_LAYER): pan/zoom fetch tiles, a date change swaps the tile
        // template, a detail mode swaps the SOURCE KIND (vector <-> geojson),
        // the animator hides/shows the live rows. Soak every transition and
        // assert: tiles actually served, no errors, pinned-* layers present
        // while pinned and gone after unpin, baseline restored.
        // Tiles start above 3,000 trajectories in the pin's window, so the
        // window is widened to ~2 years first; CAF_Chinko then tiles (4,051).
        // Pass an AOI id to soak the AOI path — the session must be able to
        // SEE it (an invisible ?area= is dropped server-side and the pin
        // quietly shows the parks' rows instead).
        async fireTiles(area) {
            area = area || 'CAF_Chinko';
            const L = window.LODLayer;
            if (!L || typeof addPinnedLayer !== 'function') { __errs.push('TILES: LODLayer/addPinnedLayer missing'); return; }
            const key = getPinKey(area, 'fire');
            const tiled = () => { const st = L.state(key); return st ? { tiled: !!st.tiled, count: st.count, render: st.render, url: st.tileURL } : null; };
            // an LOD pin's layers are lod-<key>-{fill,line,arrows,point,dots}
            const pinnedIds = () => map.getStyle().layers.map(l => l.id).filter(id => id.indexOf('lod-' + key) === 0);
            const chk = (w, wantPinned) => {
                const ids = pinnedIds();
                if (wantPinned && !ids.length) __errs.push(`TILES[${w}]: pinned layer missing`);
                if (!wantPinned && ids.length) __errs.push(`TILES[${w}]: pinned residue ${ids}`);
                const st = tiled();
                if (wantPinned && st && st.tiled) {
                    const src = map.getSource('lod-' + key);
                    if (src && src.type !== 'vector') __errs.push(`TILES[${w}]: state says tiled but source is ${src.type}`);
                }
            };
            // wait until LODLayer stops loading for this key
            const settled = async (ms) => { const t0 = Date.now(); await s(600);
                while (Date.now() - t0 < (ms || 15000)) { const st = L.state(key); if (!st || !st.inflightSig) break; await s(200); } await s(300); };
            const views = [[23.9, 6.9, 7.5], [23.0, 7.2, 8], [24.8, 6.5, 9], [23.9, 6.9, 10.5], [23.9, 6.9, 6]];
            map.jumpTo({ center: [23.9, 6.9], zoom: 7.5 }); await s(1500);
            await widenWindow(360);
            await addPinnedLayer(area, area, 'fire'); await settled(); chk('pinned', true);
            const st0 = tiled();
            if (!st0) __errs.push('TILES: no LODLayer state for ' + key);
            let sawTiles = !!(st0 && st0.tiled);
            // pan/zoom soak with hover/click on tile features
            for (let round = 0; round < 2; round++)
                for (const [lng, lat, z] of views) {
                    map.jumpTo({ center: [lng, lat], zoom: z }); await settled(8000);
                    const st = tiled(); sawTiles = sawTiles || !!(st && st.tiled);
                    chk('view-' + z, true);
                    const ts = targetsFrom(pinnedIds().filter(id => !/-text$/.test(id)), 20);
                    for (const [x, y] of ts) { mm(x, y); await s(15); }
                    if (ts.length) { click(...ts[0]); await s(300); esc(); }
                }
            // date-window change swaps the tile template (setTiles)
            await widenWindow(60); await settled(); chk('widened', true);
            // detail modes swap the source kind: tiles -> geojson -> tiles
            for (const m of ['shapes', 'fast', 'auto', 'shapes', 'auto']) {
                L.setDetail(key, m); await settled(12000); chk('detail-' + m, true);
            }
            // animator over a tiled pin: live rows hidden by the curator, pins untouched
            if (window.Animator) {
                Animator.open(); await s(3000); chk('anim-open', true);
                map.jumpTo({ center: [22.0, 7.5], zoom: 7 }); await settled(8000); chk('anim-moved', true);
                Animator.close(); await s(800); chk('anim-closed', true);
            }
            // rapid pin churn: unpin/pin while tiles are in flight
            for (let i = 0; i < 3; i++) {
                removePinnedLayer(key); await s(120);
                await addPinnedLayer(area, area, 'fire'); await s(150);
            }
            await settled(); chk('churn', true);
            removePinnedLayer(key); await s(1200); chk('unpinned', false);
            if (!sawTiles) __errs.push('TILES: the layer was never served as tiles over ' + area + ' — the tile tier was not exercised');
            const srcs = Object.keys(map.getStyle().sources).filter(k => k === 'lod-' + key);
            if (srcs.length) __errs.push('TILES: source residue ' + srcs);
            esc(); await s(300);
        },

        // Pan/zoom + hover/click soak across scales.
        async panZoomSoak() {
            const views = [[23.9, 6.9, 7.5], [30.5, 15.5, 7], [29.5, -0.6, 8.5], [34.9, -2.4, 8]];
            for (const [lng, lat, z] of views) {
                map.jumpTo({ center: [lng, lat], zoom: z }); await s(1400);
                for (let i = 0; i < 12; i++) { mm(80 + (i * 97) % 1100, 60 + (i * 61) % 680); await s(35); }
                click(640, 420); await s(500); esc(); await s(200);
            }
        },
    };

    function report() {
        const now = __snap();
        // geomap-structural-* layers/sources persist at line-opacity 0 by
        // design (soft-off); they are not residue.
        const st = map.getStyle();
        // fireseason-* layers likewise stay at visibility none with an empty
        // source once the Season overlay has been on and off again.
        const softOff = l => /^geomap-structural-/.test(l.id) ||
            (/^fireseason-/.test(l.id) && (l.layout || {}).visibility === 'none');
        const structL = st.layers.filter(softOff).length;
        const structS = Object.keys(st.sources).filter(k => /^geomap-struct-src-/.test(k) || /^fireseason-/.test(k)).length;
        const clean = baseline && (now.layers - structL) === baseline.layers &&
            (now.sources - structS) === baseline.sources && now.images === baseline.images &&
            __errs.length === 0;
        return { baseline, now, structSoftOff: { layers: structL, sources: structS },
                 errs: __errs.slice(0, 10), clean,
                 note: clean ? 'baseline restored, no errors'
                             : 'RESIDUE OR ERRORS - inspect layer ids / __errs / console' };
    }

    async function runAll() {
        setup();
        for (const [name, fn] of Object.entries(phases)) {
            console.log('[MapStress] phase:', name);
            await fn();
            console.log('[MapStress]', name, JSON.stringify(__snap()));
        }
        return report();
    }

    return { setup, phases, runAll, report, targetsFrom, widenWindow };
})();
'MapStress loaded — await MapStress.runAll()';
