// ============================================================
// 5MP Time Animator v2 — integrated into the time slider.
//
// - Controls live inside the time-slider header; progress playhead
//   rendered inside the slider track (draggable to scrub).
// - Layer chips (styled like date preset tags) let the user toggle
//   exactly what animates: fire grid / fire points / fire paths /
//   patrol grid / patrol points / deforestation / settlements /
//   turbidity / pinned infrastructure. Lazy-loaded on first enable.
// - Grid aggregates for wide views, real per-detection points for
//   high zoom (server falls back to grid if the bbox is too big).
// - Map stays fully interactive while animating (canvas is
//   pointer-events:none); layer chips can be toggled mid-play.
// - Speed: +/- click, press-and-hold to ramp (mobile friendly).
// - Temporal semantics:
//     fire grid/points : flash + afterglow (additive glow)
//     fire paths       : build point-by-point at true dated speed,
//                        then "ashen out" — red → grey → gone
//     patrol effort    : same glow language in green; ages to ash
//                        over 90d so recency/refresh is visible
//     deforestation    : accumulates; new clearings flash, old ones ash over years
//     settlements      : static context
//     turbidity        : accumulates + flash; mines static
//     pinned infra     : static context lines
// - Share links: anim=<layers>&anim_speed&anim_t&anim_paused.
// ============================================================
(function () {
    'use strict';

    const DAY = 86400000;
    const EFFORT_FADE_DAYS = 90;   // effort ash-out horizon
    const TRAJ_FADE_DAYS = 21;     // trajectory ashening after group end
    const DEFOREST_FLASH_DAYS = 45;
    // Deforestation ages on a different clock from fire. A fire front is an
    // event that ends; canopy loss is a state that persists, so a patch greys
    // out slowly and — unlike a trajectory — never disappears: it settles to a
    // dim ash dot, because the trees are still gone.
    //
    // The horizon is the animation window itself (floor 90 days), not a fixed
    // number of years. The loader only fetches events inside the window, so a
    // fixed 10-year ramp greys nothing at all in a 7-month window — every
    // event would sit in the first 6% of the ramp and the layer would read as
    // one flat purple mass again, which is the thing the ageing is for. Over a
    // multi-year window this is exactly "greys out over years"; over a short
    // one it still separates the start of the window from its end.
    const DEFOREST_AGE_MIN_MS = 90 * DAY;
    // Age is quantised into bands so the settled-prefix bitmap stays valid for
    // a whole band instead of being redrawn every frame (see deforestSprite);
    // the band scales with the horizon so a short window still ages smoothly.
    const DEFOREST_BANDS = 24;
    const POINTS_ZOOM = 6.5;       // default to real points at/above this zoom
    const POINTS_MAX_AREA = 40;    // deg², matches server cap
    const GIF_MAX_FRAMES = 160;    // size ceiling; duration is preserved by delay

    let A = null; // active animator state

    const LAYERS = {
        fireGrid:    { label: 'fire grid',     color: '#ef4444', title: 'Aggregated fire heatmap (0.1° grid)' },
        firePts:     { label: 'fire points',   color: '#ff7043', title: 'Individual VIIRS detections (high zoom)' },
        trajs:       { label: 'fire paths',    color: '#fda668', title: 'Fire movement trajectories (build up, then ashen out)' },
        effortGrid:  { label: 'patrol grid',   color: '#4ade80', title: 'Aggregated patrol effort (0.1° grid pixels)' },
        effortPts:   { label: 'patrol circles', color: '#86efac', title: 'Patrol effort circles (like the live map) — age and ashen over 90d' },
        deforest:    { label: 'deforest',      color: '#a855f7', title: 'Deforestation \u2014 new clearings flash purple, older ones grey out over years (never vanish)' },
        settlements: { label: 'settlements',   color: '#fbbf24', title: 'Settlements (static context)' },
        infra:       { label: 'infra',         color: '#60a5fa', title: 'Pinned roads/rivers/places (static)' }
    };
    // 'turb' (turbidity plume + mining sites) removed 2026-08-06 --
    // docs/MINING_FINDINGS_2026-08.md §10. The turbidity endpoint is disabled, so
    // there is nothing to animate. Remaining turb branches below are inert.
    const LAYER_ORDER = ['fireGrid', 'firePts', 'trajs', 'effortGrid', 'effortPts', 'deforest', 'settlements', 'infra'];

    function getPwdSafe() { return (typeof getPwd === 'function' ? getPwd() : '') || ''; }
    function toast(msg, type, opts) { if (typeof showToast === 'function') showToast(msg, type || 'info', opts); }
    function fmtDate(ms) { return new Date(ms).toISOString().slice(0, 10); }
    function fmtDateHuman(ms) {
        const d = new Date(ms);
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        return d.getUTCDate() + ' ' + months[d.getUTCMonth()] + ' ' + d.getUTCFullYear();
    }
    function parseD(s) { return Date.parse(s + 'T00:00:00Z'); }
    function fmtCount(n) {
        n = Number(n) || 0;
        if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
        if (n >= 1e4) return Math.round(n / 1e3) + 'k';
        return n.toLocaleString();
    }

    // ---------- CSS ----------
    const CSS = `
    #anim-canvas { position: absolute; inset: 0; pointer-events: none; z-index: 5; }

    /* open chip (idle state) — same badge geometry as the date preset tags (td/3d/…) */
    #anim-open-btn { font-size: 9px; font-weight: 600; color: #22c55e; background: rgba(34,197,94,0.10);
        border: 1px solid rgba(34,197,94,0.35); border-radius: 3px; padding: 1px 5px; margin-left: 8px;
        cursor: pointer; letter-spacing: 0.3px; line-height: 1.3; white-space: nowrap; user-select: none;
        vertical-align: middle; transition: background .15s, border-color .15s; font-family: inherit; }
    #anim-open-btn:hover { background: rgba(34,197,94,0.22); border-color: #22c55e; }
    .time-slider-container.animating #anim-open-btn { display: none; }

    /* inline controls in the slider header — same badge height as the date tags */
    #anim-inline { display: inline-flex; align-items: center; gap: 4px; margin-left: 8px; }
    .anim-btn { background: rgba(255,255,255,0.04); color: #aaa; border: 1px solid rgba(255,255,255,0.12);
        border-radius: 3px; padding: 1px 6px; cursor: pointer; font-size: 10px; font-weight: 600; line-height: 1.3;
        letter-spacing: 0.3px; font-family: inherit; user-select: none; -webkit-user-select: none; touch-action: manipulation;
        transition: background .15s, border-color .15s, color .15s; }
    .anim-btn:hover { background: rgba(34,197,94,0.12); border-color: rgba(34,197,94,0.35); color: #ccc; }
    .anim-btn.primary { background: rgba(34,197,94,0.18); border-color: rgba(34,197,94,0.5); color: #22c55e; font-weight: 700; min-width: 26px; text-align: center; }
    .anim-btn.primary:hover { background: rgba(34,197,94,0.3); }
    #anim-date-lbl { font-variant-numeric: tabular-nums; font-weight: 700; font-size: 11px; color: #fff;
        min-width: 80px; text-align: center; line-height: 1.3; }
    #anim-speed-lbl { font-size: 9px; color: #999; min-width: 46px; text-align: center; font-variant-numeric: tabular-nums; line-height: 1.3; }

    /* staggered reveal (same expansion behaviour as the date preset tags) */
    #anim-inline > * { max-width: 0; opacity: 0; padding-left: 0; padding-right: 0; margin: 0; border-width: 0;
        overflow: hidden; min-width: 0;
        transition: max-width .25s cubic-bezier(.4,0,.2,1), opacity .2s ease, padding .25s cubic-bezier(.4,0,.2,1),
            background .15s, border-color .15s, color .15s; }
    #anim-inline > .visible { max-width: 90px; opacity: 1; }
    #anim-inline > .anim-btn.visible { padding: 1px 6px; border-width: 1px; }
    #anim-inline > .anim-btn.primary.visible { min-width: 26px; }
    #anim-inline > #anim-date-lbl.visible { min-width: 80px; }
    #anim-inline > #anim-speed-lbl.visible { min-width: 46px; }

    /* layer chips row — same badge geometry as the date tags */
    #anim-chips { display: flex; flex-wrap: wrap; gap: 3px; align-items: center; margin: 3px 0 4px; min-height: 16px; }
    .anim-chip { font-size: 9px; font-weight: 600; color: #888; background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.12); border-radius: 3px; padding: 1px 5px; cursor: pointer;
        letter-spacing: 0.3px; line-height: 1.3; white-space: nowrap; user-select: none; -webkit-user-select: none;
        display: inline-flex; align-items: center; gap: 4px; max-width: 0; opacity: 0; padding-left: 0; padding-right: 0;
        border-width: 0; overflow: hidden;
        transition: max-width .25s cubic-bezier(.4,0,.2,1), opacity .2s ease, padding .25s cubic-bezier(.4,0,.2,1),
            background .15s, border-color .15s, color .15s;
        font-family: inherit; }
    .anim-chip.visible { max-width: 120px; opacity: 1; padding: 1px 6px 1px 5px; border-width: 1px; }
    .anim-chip i { width: 6px; height: 6px; border-radius: 50%; display: inline-block; flex: none;
        background: #555; transition: background .15s, box-shadow .15s; }
    .anim-chip.on { color: #ddd; background: rgba(255,255,255,0.09); border-color: rgba(255,255,255,0.25); }
    .anim-chip.on i { box-shadow: 0 0 5px currentColor; }
    .anim-chip.loading i { animation: animChipPulse 0.8s infinite alternate; }
    @keyframes animChipPulse { 0% { opacity: .3; } 100% { opacity: 1; } }
    .anim-chip:hover { border-color: rgba(34,197,94,0.35); color: #ccc; }
    .anim-chip.unavailable { opacity: .35; pointer-events: none; }
    .anim-chip.hidden { display: none; }

    /* playhead + progress inside the slider track */
    #anim-progress { position: absolute; height: 100%; background: rgba(255,255,255,0.35); border-radius: 3px;
        pointer-events: none; z-index: 1; mix-blend-mode: overlay; }
    #anim-playhead { position: absolute; top: 50%; width: 4px; height: 18px; background: #fff; border-radius: 2px;
        transform: translate(-50%, -50%); z-index: 3; cursor: ew-resize; box-shadow: 0 0 6px rgba(255,255,255,0.7);
        touch-action: none; }
    #anim-playhead::after { content: ''; position: absolute; inset: -8px -10px; }

    .time-slider-container.animating { height: auto; min-height: 92px;
        background: linear-gradient(to top, rgba(10,10,10,0.96) 0%, rgba(10,10,10,0.92) 80%, rgba(10,10,10,0.55) 100%); }
    .time-slider-container.animating .time-slider-header { margin-bottom: 2px; }

    #anim-loading { position: fixed; left: 50%; top: 50%; transform: translate(-50%,-50%); z-index: 700;
        background: rgba(12,14,16,0.95); border: 1px solid rgba(255,255,255,0.12); border-radius: 16px;
        padding: 22px 30px; text-align: center; color: #ddd; box-shadow: 0 8px 40px rgba(0,0,0,0.7); }
    #anim-loading .flame { font-size: 26px; animation: animFlicker 0.9s infinite alternate; }
    @keyframes animFlicker { 0% { transform: scale(1) rotate(-3deg); opacity: .8; } 100% { transform: scale(1.15) rotate(3deg); opacity: 1; } }
    #anim-loading .bar { width: 200px; height: 4px; background: rgba(255,255,255,0.1); border-radius: 3px; margin: 10px auto 4px; overflow: hidden; }
    #anim-loading .bar > div { height: 100%; width: 0%; background: linear-gradient(90deg,#f59e0b,#ef4444); border-radius: 3px; transition: width .3s; }
    #anim-loading .sub { font-size: 11px; color: #888; margin-top: 4px; }

    .time-slider-container.animating .time-slider-date { flex-wrap: wrap; row-gap: 2px; }
    .time-slider-container.animating .time-slider-date-part { white-space: nowrap; }
    @media (max-width: 768px) {
        #anim-inline { gap: 3px; margin-left: 0; width: 100%; justify-content: flex-start; }
        /* keep date preset tags tappable while animating — header wraps */
        #anim-date-lbl { font-size: 10px; min-width: 68px; }
        #anim-inline > #anim-date-lbl.visible { min-width: 68px; }
        #anim-speed-lbl { min-width: 40px; font-size: 9px; }
        #anim-inline > #anim-speed-lbl.visible { min-width: 40px; }
        .anim-chip { font-size: 8px; }
        .anim-chip.visible { max-width: 100px; padding: 1px 5px 1px 4px; }
        #anim-chips { gap: 2px; }
        /* GIF encoding a phone's viewport is minutes of CPU; the row stays,
           the GIF entry hides itself (see exportMenuHTML). */
        /* bigger touch targets: keep badge look, extend invisible hit area */
        .anim-btn { padding: 3px 8px; font-size: 11px; }
        #anim-inline > .anim-btn.visible { padding: 3px 8px; max-width: 60px;
            overflow: visible; position: relative; }
        #anim-inline > .anim-btn.visible::after { content: ''; position: absolute; inset: -8px -6px; }
    }
    `;

    function injectCSS() {
        if (document.getElementById('anim-css')) return;
        const st = document.createElement('style');
        st.id = 'anim-css';
        st.textContent = CSS;
        document.head.appendChild(st);
    }

    function showLoading(msg, pct) {
        let el = document.getElementById('anim-loading');
        if (!el) {
            el = document.createElement('div');
            el.id = 'anim-loading';
            el.innerHTML = '<div class="flame">🔥</div><div class="msg" style="margin-top:6px;font-size:13px;"></div><div class="bar"><div></div></div><div class="sub">preparing animation…</div>';
            document.body.appendChild(el);
        }
        el.querySelector('.msg').textContent = msg;
        el.querySelector('.bar > div').style.width = (pct || 0) + '%';
    }
    function hideLoading() { const el = document.getElementById('anim-loading'); if (el) el.remove(); }

    // ---------- geometry helpers ----------
    function activeBbox() {
        if (typeof currentBbox !== 'undefined' && currentBbox && currentBbox.length === 4) return { bbox: currentBbox.slice(), fixed: true };
        const b = map.getBounds();
        // pad viewport 30% each side so panning a bit doesn't leave the data
        const w = b.getEast() - b.getWest(), h = b.getNorth() - b.getSouth();
        return { bbox: [b.getWest() - w * 0.3, b.getSouth() - h * 0.3, b.getEast() + w * 0.3, b.getNorth() + h * 0.3], fixed: false };
    }
    function bboxArea(bb) { return Math.abs((bb[2] - bb[0]) * (bb[3] - bb[1])); }

    // Trace a GeoJSON Polygon/MultiPolygon as one canvas path (all rings,
    // including holes -- fill/clip with 'evenodd').
    function tracePolygon(ctx, geom, proj) {
        if (!geom) return;
        const polys = geom.type === 'MultiPolygon' ? geom.coordinates
                    : geom.type === 'Polygon' ? [geom.coordinates] : [];
        ctx.beginPath();
        for (const poly of polys) {
            for (const ring of poly) {
                for (let i = 0; i < ring.length; i++) {
                    const p = proj(ring[i][0], ring[i][1]);
                    if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
                }
                ctx.closePath();
            }
        }
    }

    function geomBbox(geom) {
        const polys = geom && geom.type === 'MultiPolygon' ? geom.coordinates
                    : geom && geom.type === 'Polygon' ? [geom.coordinates] : [];
        let x0 = 180, y0 = 90, x1 = -180, y1 = -90, seen = false;
        for (const poly of polys) for (const ring of poly) for (const c of ring) {
            seen = true;
            if (c[0] < x0) x0 = c[0];
            if (c[0] > x1) x1 = c[0];
            if (c[1] < y0) y0 = c[1];
            if (c[1] > y1) y1 = c[1];
        }
        return seen ? [x0, y0, x1, y1] : null;
    }
    function viewportInside(bb) {
        const b = map.getBounds();
        return b.getWest() >= bb[0] && b.getSouth() >= bb[1] && b.getEast() <= bb[2] && b.getNorth() <= bb[3];
    }

    function pinnedTypes() {
        const out = new Set();
        try {
            Object.values(pinnedLayers || {}).forEach(l => {
                const t = (l.type || '').toLowerCase();
                if (t.includes('fire')) out.add('fires');
                else if (t.includes('deforest')) out.add('deforest');
                else if (t.includes('settlement')) out.add('settlements');
            });
        } catch (e) {}
        return out;
    }

    const STATIC_COLORS = { road: '#60a5fa', river: '#3b82f6', water: '#3b82f6', place: '#c084fc',
        infrastructure: '#22c55e', airstrip: '#22c55e', learned: '#22c55e' };
    function snapshotPinned() {
        const statics = [];
        const turb = { plume: [], alerts: [], mines: [] };
        const seen = new Set();
        const style = map.getStyle();
        if (!style || !style.sources) return { statics, turb };
        for (const srcId of Object.keys(style.sources)) {
            if (!/^(pinned-source-|pinned-single-source-|rt-traj-)/.test(srcId)) continue;
            const src = map.getSource(srcId);
            const data = src && src._data;
            if (!data || !data.features && data.type !== 'Feature') continue;
            const feats = data.type === 'Feature' ? [data] : (data.features || []);
            for (const f of feats) {
                if (!f.geometry) continue;
                const p = f.properties || {};
                const ft = (p.feature_type || '').toLowerCase();
                const key = ft + ':' + (p.feature_id || JSON.stringify(f.geometry.coordinates && f.geometry.coordinates[0]));
                if (seen.has(key)) continue;
                seen.add(key);
                if (ft === 'turbid_water') { turb.plume.push({ lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], t: p.date ? parseD(p.date) : null }); continue; }
                if (ft === 'turbidity_alert') { turb.alerts.push({ lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], t: p.date ? parseD(p.date) : null }); continue; }
                if (ft === 'mining_site') { turb.mines.push({ lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] }); continue; }
                if (ft.includes('fire') || ft.includes('deforest') || ft === 'settlement') continue;
                let color = '#9ca3af';
                for (const k of Object.keys(STATIC_COLORS)) { if (ft.includes(k)) { color = STATIC_COLORS[k]; break; } }
                statics.push({ geom: f.geometry, color });
            }
        }
        return { statics, turb };
    }

    function chooseStep(spanDays) {
        if (spanDays <= 92) return 'day';
        if (spanDays <= 800) return 'week';
        return 'month';
    }
    // The grid resolution has to keep getting finer as the user zooms in, or
    // the picture stops meaning anything: below ~10° of width it used to clamp
    // at 0.1°, which over a 1:250k historical sheet is a fat blob per ~11 km
    // cell. 0.1° is the pre-agg base (fire_grid_day) and the server clamps
    // below it, so this asks for the base as soon as the view is small enough
    // to want it — and at that point `wantPoints()` usually takes over anyway.
    function chooseRes(bbox) {
        const w = Math.abs(bbox[2] - bbox[0]);
        if (w > 30) return 0.25;
        return 0.1;
    }

    // When is the grid the wrong answer? When a cell has grown big enough on
    // screen that the user is looking at something FINER than it — at which
    // point the lattice of blobs is a picture of our binning, not of the
    // fires. That is a question about screen pixels per cell, not about a zoom
    // level or a bbox area, because it depends on the viewport too.
    //
    // Asking is free-ish and never wrong: the server's own gate is the
    // estimated number of detections in view (~10 ms over fire_grid_day), so a
    // wide-but-quiet view gets its points and a wide-and-burning one is told
    // no and falls back here. The client must not pre-judge that — 40 deg² of
    // Namibian desert and 40 deg² of an Angolan dry season differ by three
    // orders of magnitude.
    const GRID_CELL_PX_MAX = 10;
    function wantPoints(bbox, res) {
        const w = Math.abs(bbox[2] - bbox[0]);
        if (!(w > 0)) return false;
        const vw = (map.getCanvas && map.getCanvas().clientWidth) || 1280;
        return (vw * (res || 0.1) / w) > GRID_CELL_PX_MAX;
    }
    function bucketMs(step) { return step === 'day' ? DAY : step === 'week' ? 7 * DAY : 30 * DAY; }

    async function fetchJSON(url) {
        const r = await fetch(url);
        if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url.split('?')[0]);
        return r.json();
    }

    // Without geometry a feature costs ~26 bytes instead of ~1.6 KB, so the
    // animator can afford a real sample of a continental AOI rather than the
    // 1,500 it used to get. The server spreads a truncated answer over the
    // bbox (see srv/features_bbox.go spreadSelect).
    const FEATURE_POINT_LIMIT = 12000;
    // Fire paths the browser draws per frame. The endpoint is now a single
    // indexed query (0.5 s for a park window, 1.7 s continental), so this is a
    // rendering budget, not a database one — projectTrajs caches screen coords
    // per view transform, which is what makes 6,000 of them affordable.
    const TRAJ_LIMIT = 6000;
    function truncNote(label, shown, total) {
        toast('Showing ' + shown.toLocaleString() + ' of ' + total.toLocaleString() + ' ' + label +
              ' spread across the view — zoom in for all of them', 'info', { key: 'anim-trunc-' + label });
    }

    // ---------- per-layer lazy loaders ----------
    //
    // aoiQ: the raw-geography endpoints exclude every AOI's rows by default
    // (srv/aoi.go aoiExcludeSQL) — for privacy, and so an AOI does not
    // double-count the parks it overlaps in the global counters. That default
    // also hid the AOI from its own animation: the trajectory and
    // deforestation/settlement layers came back with the concave gap empty.
    // ?aoi= lets exactly this AOI back in, and only if the server agrees the
    // caller may see it (aoiScopeParam re-checks visibility; it never trusts
    // this parameter).
    async function loadLayer(name) {
        const pwd = encodeURIComponent(getPwdSafe());
        const aoiQ = A.aoiID ? '&aoi=' + encodeURIComponent(A.aoiID) : '';
        const bb = A.fetchBbox.map(v => v.toFixed(4)).join(',');
        const fromISO = A.fromISO, toISO = A.toISO;
        const spanDays = (A.t1 - A.t0) / DAY;
        const step = chooseStep(spanDays);
        const res = chooseRes(A.fetchBbox);
        const D = A.data;
        switch (name) {
            case 'fireGrid': {
                // Level of detail, decided from the data and not from a zoom
                // number: zoomed in far enough that a 0.1° cell is bigger than
                // what is being looked at, ask for the detections themselves.
                // The server refuses (and says how many there are) when that
                // would be too much, and we fall through to the grid — so the
                // layer means the same thing at every zoom and only its
                // rendering changes.
                if (wantPoints(A.fetchBbox, res)) {
                    try {
                        const p = await fetchJSON(`/api/fire-frames?mode=points&bbox=${bb}&from=${fromISO}&to=${toISO}&step=day&pwd=${pwd}`);
                        if (p.mode === 'points') {
                            const p0 = parseD(p.from);
                            D.fireGrid = { asPoints: true, res: 0, step: 'day',
                                points: (p.points || []).map(q => [q[0], q[1], p0 + q[2] * DAY, q[3]]) };
                            break;
                        }
                    } catch (e) { /* fall through to the grid */ }
                }
                const j = await fetchJSON(`/api/fire-frames?bbox=${bb}&from=${fromISO}&to=${toISO}&step=${step}&res=${res}&pwd=${pwd}`);
                D.fireGrid = { frames: (j.frames || []).map(f => ({ t: parseD(f.d), pts: f.p })), res: j.res || res, step: j.step || step };
                break;
            }
            case 'firePts': {
                // No local bbox-area refusal any more. The server's gate is an
                // ESTIMATE of the detections in view (it sums fire_grid_day in
                // ~10 ms), which is the number that actually matters — 40 deg²
                // of Namibian desert and 40 deg² of an Angolan dry season are
                // three orders of magnitude apart. Ask, then say what it said.
                const j = await fetchJSON(`/api/fire-frames?mode=points&bbox=${bb}&from=${fromISO}&to=${toISO}&step=day&pwd=${pwd}`);
                if (j.mode !== 'points') {
                    D.firePts = null;
                    const u = j.points_unavailable || {};
                    throw new Error(u.estimate
                        ? `${fmtCount(u.estimate)} detections in view — showing the density grid instead`
                        : 'too many fires here — use the fire grid');
                }
                const f0 = parseD(j.from);
                D.firePts = (j.points || []).map(p => [p[0], p[1], f0 + p[2] * DAY, p[3]]);
                break;
            }
            case 'trajs': {
                // Wire format (2026-08-12): pts are [lon, lat, dayOffset] against
                // the group's own t0, and the endpoint reads feature_geometries
                // + traj_days instead of parsing data/fire_groups_v5/*.json.
                // A continental view went from >120 s to 1.7 s, so the limit is
                // no longer a apology for the endpoint: TRAJ_LIMIT of them is
                // what the browser can draw at 60 fps, and the server spreads
                // the selection across the view so a truncated answer is still
                // a picture of the whole area rather than of its hottest corner.
                const j = await fetchJSON(`/api/fire-anim-trajectories?bbox=${bb}&from=${fromISO}&to=${toISO}&limit=${TRAJ_LIMIT}&pwd=${pwd}${aoiQ}`);
                D.trajs = (j.groups || []).map(g => {
                    const base = parseD(g.t0 || g.start);
                    const pts = g.pts.map(p => [p[0], p[1], base + p[2] * DAY]);
                    // fires/days/frp/narrative are carried so a PAUSED frame
                    // can answer a hover without a second request (probeFrame).
                    return { pts, t0: pts[0][2], t1: pts[pts.length - 1][2], type: g.type, kmd: g.kmd,
                             id: g.id, park: g.park, km: g.km,
                             fires: g.fires, days: g.days, frp: g.frp, narrative: g.narrative };
                }).filter(g => g.pts.length >= 2);
                if (j.truncated) truncNote('fire paths', j.count, j.total);
                break;
            }
            case 'effortGrid': {
                const j = await fetchJSON(`/api/fire-frames?layer=effort&bbox=${bb}&from=${fromISO}&to=${toISO}&step=${step}&res=${res}&pwd=${pwd}`);
                D.effortGrid = { frames: (j.frames || []).map(f => ({ t: parseD(f.d), pts: f.p })), res: j.res || res, step: j.step || step, off: j.align === 'center' ? 0.5 : 0 };
                break;
            }
            case 'effortPts': {
                // same aggregated effort frames as effortGrid, rendered as circles
                const j = await fetchJSON(`/api/fire-frames?layer=effort&bbox=${bb}&from=${fromISO}&to=${toISO}&step=${step}&res=${res}&pwd=${pwd}`);
                D.effortPts = { frames: (j.frames || []).map(f => ({ t: parseD(f.d), pts: f.p })), res: j.res || res, step: j.step || step, off: j.align === 'center' ? 0.5 : 0 };
                if (D.effortGrid === undefined) D.effortGrid = D.effortPts; // share
                break;
            }
            case 'deforest': {
                // mode=points: the animator draws a dot per event, so asking for
                // polygon rings meant parsing ~1 MB of geometry to recover
                // 1,500 centroids. Points come back as [lon, lat, dayOffset,
                // value] against j.from.
                const j = await fetchJSON(`/api/features-in-bbox?type=deforestation&mode=points&bbox=${bb}&from=${fromISO}&to=${toISO}&limit=${FEATURE_POINT_LIMIT}&pwd=${pwd}${aoiQ}`);
                const base = parseD(j.from || fromISO);
                D.deforest = (j.points || []).map(p => ({
                    lon: p[0], lat: p[1],
                    t: p[2] >= 0 ? base + p[2] * DAY : NaN,
                    area: p[3] || 0.1
                })).filter(d => d.lon != null && !isNaN(d.t)).sort((a, b) => a.t - b.t);
                if (j.truncated) truncNote('deforestation', j.count, j.total);
                break;
            }
            case 'settlements': {
                const j = await fetchJSON(`/api/features-in-bbox?type=settlement&mode=points&bbox=${bb}&limit=${FEATURE_POINT_LIMIT}&pwd=${pwd}${aoiQ}`);
                D.settlements = (j.points || []).map(p => ({ lon: p[0], lat: p[1] }));
                if (j.truncated) truncNote('settlements', j.count, j.total);
                break;
            }
            case 'turb': case 'infra': {
                const snap = snapshotPinned();
                D.turb = snap.turb;
                D.infra = snap.statics;
                break;
            }
        }
    }

    function ensureLayer(name) {
        if (A.data[name] !== undefined) return Promise.resolve();
        if (A.loading[name]) return A.loading[name];
        const chip = document.querySelector(`.anim-chip[data-layer="${name}"]`);
        if (chip) chip.classList.add('loading');
        A.loading[name] = loadLayer(name).catch(e => {
            A.on[name] = false;
            toast(e.message, 'warning');
        }).finally(() => {
            delete A.loading[name];
            // A refetch can return the same number of points for a different
            // area, and the sprite keys count position — drop them explicitly.
            invalidateSprites();
            if (chip) chip.classList.remove('loading');
            updateChips();
            if (A) draw(A.t);
        });
        return A.loading[name];
    }

    // ---------- canvas ----------
    function makeCanvas() {
        const mapEl = map.getContainer();
        const c = document.createElement('canvas');
        c.id = 'anim-canvas';
        mapEl.appendChild(c);
        const resize = () => {
            const dpr = Math.min(window.devicePixelRatio || 1, 2);
            c.width = mapEl.clientWidth * dpr;
            c.height = mapEl.clientHeight * dpr;
            c.style.width = mapEl.clientWidth + 'px';
            c.style.height = mapEl.clientHeight + 'px';
            c.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
        };
        resize();
        return { canvas: c, resize };
    }

    // Trajectory geometry is fixed; only the visible prefix moves with t.
    // Cache screen coords per group, keyed on the view transform.
    let _trajKey = '';
    function projectTrajs(groups, proj, w, h) {
        const c = map.getCenter();
        const key = [w, h, c.lng.toFixed(5), c.lat.toFixed(5), map.getZoom().toFixed(3),
                     map.getBearing().toFixed(1), map.getPitch().toFixed(1)].join('|');
        if (key === _trajKey && groups.length && groups[0]._scr) return;
        _trajKey = key;
        for (const g of groups) {
            const n = g.pts.length;
            const scr = g._scr && g._scr.length === n * 2 ? g._scr : new Float32Array(n * 2);
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            for (let i = 0; i < n; i++) {
                const p = proj(g.pts[i][0], g.pts[i][1]);
                scr[i * 2] = p.x; scr[i * 2 + 1] = p.y;
                if (p.x < minX) minX = p.x;
                if (p.x > maxX) maxX = p.x;
                if (p.y < minY) minY = p.y;
                if (p.y > maxY) maxY = p.y;
            }
            g._scr = scr;
            g._off = maxX < -20 || maxY < -20 || minX > w + 20 || minY > h + 20;
        }
    }

    function invalidateSprites() { _defSprite = null; _settSprite = null; _trajKey = ''; }

    function deforestRadius(d, zoom) {
        return Math.max(1.5, Math.min(8, Math.sqrt(d.area) * (zoom / 3)));
    }

    // purple → ash-grey for deforestation aging.
    // k: 0 = fresh clearing, 1 = DEFOREST_AGE_YEARS old or more. The floor of
    // 0.22 alpha is the point: an old clearing is faint, not absent — it is
    // still deforested. Old patches also shrink (deforestAgeScale) so a fresh
    // one next to them reads immediately.
    function deforestColor(k, alpha) {
        const r = Math.round(168 + (128 - 168) * k);
        const g = Math.round(85 + (128 - 85) * k);
        const b = Math.round(247 + (128 - 247) * k);
        return `rgba(${r},${g},${b},${alpha})`;
    }
    function deforestAgeMs() {
        const span = A ? (A.t1 - A.t0) : DEFOREST_AGE_MIN_MS;
        return Math.max(DEFOREST_AGE_MIN_MS, span);
    }
    function deforestAge(t, tEvent) {
        return Math.max(0, Math.min(1, (t - tEvent) / deforestAgeMs()));
    }
    function deforestAgeScale(k) { return 1 - 0.4 * k; }

    // index of the first element with t > tt (arr sorted by t)
    function upperBound(arr, tt) {
        let lo = 0, hi = arr.length;
        while (lo < hi) {
            const mid = (lo + hi) >> 1;
            if (arr[mid].t <= tt) lo = mid + 1; else hi = mid;
        }
        return lo;
    }

    // The settled (no longer flashing) deforestation prefix, rasterised.
    // Scrubbing backwards shrinks the prefix, which invalidates the key — the
    // bitmap is redrawn, never reused for a larger t than it was built for.
    //
    // Settled patches now age too, so the picture is no longer constant for a
    // fixed prefix. It is quantised instead: age is evaluated at a band-aligned
    // time (1/24th of the ageing horizon), so one bitmap serves ~4% of the
    // window's playback rather than a single frame — tens of redraws over a
    // whole play-through instead of 60 per second.
    let _defSprite = null;
    function deforestSprite(arr, n, t, proj, w, h, zoom) {
        if (n <= 0 || w <= 0 || h <= 0) return null;
        const bandMs = deforestAgeMs() / DEFOREST_BANDS;
        const band = Math.floor(t / bandMs);
        const tq = band * bandMs;
        const c = map.getCenter();
        const key = [n, band, w, h, c.lng.toFixed(5), c.lat.toFixed(5),
                     map.getZoom().toFixed(3), map.getBearing().toFixed(1), map.getPitch().toFixed(1)].join('|');
        if (_defSprite && _defSprite.key === key) return _defSprite.canvas;
        const cv = (_defSprite && _defSprite.canvas) || document.createElement('canvas');
        cv.width = w; cv.height = h;
        const g = cv.getContext('2d');
        g.clearRect(0, 0, w, h);
        for (let i = 0; i < n; i++) {
            const d = arr[i];
            const p = proj(d.lon, d.lat);
            if (p.x < -10 || p.y < -10 || p.x > w + 10 || p.y > h + 10) continue;
            const k = deforestAge(tq, d.t);
            g.fillStyle = deforestColor(k, 0.35 - 0.13 * k);
            g.beginPath();
            g.arc(p.x, p.y, deforestRadius(d, zoom) * deforestAgeScale(k), 0, 6.283);
            g.fill();
        }
        _defSprite = { key, canvas: cv };
        return cv;
    }

    // A static point layer is the same picture every frame; only the view
    // transform can change it. Rasterise once into an offscreen canvas keyed on
    // the map's centre/zoom/bearing so playback composites a single bitmap
    // instead of re-stroking N arcs at 60 fps. Sized to the visible canvas, so
    // memory is one screen regardless of the point count.
    let _settSprite = null;
    function settlementSprite(pts, proj, w, h) {
        if (!pts.length || w <= 0 || h <= 0) return null;
        const c = map.getCenter();
        const key = [pts.length, w, h, c.lng.toFixed(5), c.lat.toFixed(5),
                     map.getZoom().toFixed(3), map.getBearing().toFixed(1), map.getPitch().toFixed(1)].join('|');
        if (_settSprite && _settSprite.key === key) return _settSprite.canvas;
        const cv = (_settSprite && _settSprite.canvas) || document.createElement('canvas');
        cv.width = w; cv.height = h;
        const g = cv.getContext('2d');
        g.clearRect(0, 0, w, h);
        g.fillStyle = 'rgba(251,191,36,0.45)';
        for (const s of pts) {
            const p = proj(s.lon, s.lat);
            if (p.x < -10 || p.y < -10 || p.x > w + 10 || p.y > h + 10) continue;
            g.beginPath(); g.arc(p.x, p.y, 1.6, 0, 6.283); g.fill();
        }
        _settSprite = { key, canvas: cv };
        return cv;
    }

    function drawGeom(ctx, geom, color, proj, w, h) {
        const line = (coords) => {
            ctx.beginPath();
            let started = false;
            for (const c of coords) {
                const p = proj(c[0], c[1]);
                if (!started) { ctx.moveTo(p.x, p.y); started = true; } else ctx.lineTo(p.x, p.y);
            }
            ctx.stroke();
        };
        ctx.strokeStyle = color; ctx.globalAlpha = 0.5; ctx.lineWidth = 1.2;
        ctx.fillStyle = color;
        switch (geom.type) {
            case 'Point': {
                const p = proj(geom.coordinates[0], geom.coordinates[1]);
                if (p.x >= -10 && p.y >= -10 && p.x <= w + 10 && p.y <= h + 10) { ctx.beginPath(); ctx.arc(p.x, p.y, 2.2, 0, 6.283); ctx.fill(); }
                break;
            }
            case 'MultiPoint':
                for (const c of geom.coordinates) {
                    const p = proj(c[0], c[1]);
                    if (p.x >= -10 && p.y >= -10 && p.x <= w + 10 && p.y <= h + 10) { ctx.beginPath(); ctx.arc(p.x, p.y, 2.2, 0, 6.283); ctx.fill(); }
                }
                break;
            case 'LineString': line(geom.coordinates); break;
            case 'MultiLineString': for (const l of geom.coordinates) line(l); break;
            case 'Polygon': for (const r of geom.coordinates) line(r); break;
            case 'MultiPolygon': for (const poly of geom.coordinates) for (const r of poly) line(r); break;
        }
        ctx.globalAlpha = 1;
    }

    // red → ash-grey interpolation for trajectory fade-out
    function ashColor(k, alpha) {
        // k: 0 = fresh red, 1 = fully ash
        const r = Math.round(239 + (140 - 239) * k);
        const g = Math.round(68 + (140 - 68) * k);
        const b = Math.round(68 + (140 - 68) * k);
        return `rgba(${r},${g},${b},${alpha})`;
    }
    // green → ash for effort aging
    function effortAsh(k, alpha) {
        const r = Math.round(74 + (130 - 74) * k);
        const g = Math.round(222 + (135 - 222) * k);
        const b = Math.round(128 + (130 - 128) * k);
        return `rgba(${r},${g},${b},${alpha})`;
    }

    // Individual VIIRS detections, flashing then fading. Shared by the
    // `firePts` chip and by `fireGrid` once it is zoomed in far enough to have
    // swapped itself for real detections — one rendering, so the two chips
    // cannot drift into disagreeing about what a fire looks like.
    function drawFirePoints(ctx, pts, t, proj, w, h, zoom) {
        if (!pts || !pts.length) return;
        const fadeMs = DAY * 3;
        for (const pt of pts) {
            if (pt[2] > t) break;
            const age = t - pt[2];
            if (age > fadeMs) continue;
            const k = 1 - age / fadeMs;
            const p = proj(pt[0], pt[1]);
            if (p.x < -15 || p.y < -15 || p.x > w + 15 || p.y > h + 15) continue;
            const frp = pt[3] || 1;
            const r = (2 + Math.min(6, Math.log2(1 + frp))) * Math.max(0.6, zoom / 7);
            const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
            g.addColorStop(0, `rgba(255,${Math.round(150 + 80 * k)},50,${0.6 * k + 0.1})`);
            g.addColorStop(0.5, `rgba(239,68,68,${0.35 * k})`);
            g.addColorStop(1, 'rgba(239,68,68,0)');
            ctx.fillStyle = g;
            ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 6.283); ctx.fill();
        }
    }

    // ---------- draw ----------
    function draw(t) {
        if (!A) return;
        const ctx = A.ctx;
        const w = A.canvas.clientWidth, h = A.canvas.clientHeight;
        ctx.clearRect(0, 0, w, h);
        const proj = (lon, lat) => map.project([lon, lat]);
        const zoom = map.getZoom();
        const D = A.data, on = A.on;
        const step = (D.fireGrid && D.fireGrid.step) || chooseStep((A.t1 - A.t0) / DAY);
        const bMs = bucketMs(step);

        // Clip to the selection, if there is one. Two shapes:
        //   * a drawn bbox  -> rectangle (what this always did)
        //   * an AOI        -> its actual polygon. Every frames endpoint is
        //     bbox-scoped, so without this an AOI animation spills fires into
        //     the bbox corners *outside* the polygon and reads as data the
        //     AOI does not have (docs/PLAN_AOI_OVERLAY.md §3d).
        let clipped = false;
        if (A.clipGeom) {
            ctx.save();
            tracePolygon(ctx, A.clipGeom, proj);
            ctx.strokeStyle = 'rgba(255,255,255,0.25)';
            ctx.lineWidth = 1;
            ctx.stroke();
            ctx.clip('evenodd');   // even-odd so holes stay holes
            clipped = true;
        } else if (A.bboxFixed) {
            const bb = A.fetchBbox;
            const p0 = proj(bb[0], bb[3]), p1 = proj(bb[2], bb[1]);
            ctx.save();
            ctx.strokeStyle = 'rgba(255,255,255,0.25)';
            ctx.lineWidth = 1;
            ctx.strokeRect(p0.x, p0.y, p1.x - p0.x, p1.y - p0.y);
            ctx.beginPath(); ctx.rect(p0.x, p0.y, p1.x - p0.x, p1.y - p0.y); ctx.clip();
            clipped = true;
        }

        // --- infra (static) ---
        if (on.infra && D.infra) for (const s of D.infra) drawGeom(ctx, s.geom, s.color, proj, w, h);

        // --- turbidity ---
        if (on.turb && D.turb) {
            const tb = D.turb;
            if (tb.mines.length) {
                ctx.fillStyle = 'rgba(239,68,68,0.8)';
                ctx.strokeStyle = 'rgba(255,255,255,0.6)';
                ctx.lineWidth = 1;
                for (const m of tb.mines) {
                    const p = proj(m.lon, m.lat);
                    if (p.x < -10 || p.y < -10 || p.x > w + 10 || p.y > h + 10) continue;
                    ctx.beginPath(); ctx.arc(p.x, p.y, 4.5, 0, 6.283); ctx.fill(); ctx.stroke();
                }
            }
            for (const pt of tb.plume) {
                if (pt.t && pt.t > t) continue;
                const p = proj(pt.lon, pt.lat);
                if (p.x < -10 || p.y < -10 || p.x > w + 10 || p.y > h + 10) continue;
                const age = pt.t ? (t - pt.t) / DAY : 999;
                const flash = Math.max(0, 1 - age / 21);
                ctx.fillStyle = `rgba(234,179,8,${0.55 + flash * 0.45})`;
                ctx.beginPath(); ctx.arc(p.x, p.y, 1.8 + flash * 2, 0, 6.283); ctx.fill();
            }
            for (const a of tb.alerts) {
                if (a.t && a.t > t) continue;
                const p = proj(a.lon, a.lat);
                if (p.x < -10 || p.y < -10 || p.x > w + 10 || p.y > h + 10) continue;
                const age = a.t ? (t - a.t) / DAY : 999;
                const flash = Math.max(0, 1 - age / 30);
                ctx.strokeStyle = `rgba(234,179,8,${0.7 + flash * 0.3})`;
                ctx.lineWidth = 1.5 + flash;
                ctx.beginPath(); ctx.arc(p.x, p.y, 5 + flash * 4, 0, 6.283); ctx.stroke();
            }
        }

        // --- settlements (static) ---
        //
        // Settlements never change with t, so at 12,000 points they were
        // 12,000 arcs per frame to redraw an identical picture. Cached into an
        // offscreen canvas keyed on the view transform; the key changes only
        // when the map moves, so a play-through composites one bitmap.
        if (on.settlements && D.settlements) {
            const sprite = settlementSprite(D.settlements, proj, w, h);
            if (sprite) ctx.drawImage(sprite, 0, 0, w, h);
        }

        // --- deforestation (accumulate + flash) ---
        //
        // Everything older than DEFOREST_FLASH_DAYS draws with a constant
        // style, so it is the same picture until the next event settles: cache
        // that prefix as a bitmap keyed on (transform, settled count) and
        // stroke only the handful still flashing. D.deforest is sorted by t, so
        // the settled set is always a prefix.
        if (on.deforest && D.deforest) {
            const arr = D.deforest;
            const nowIdx = upperBound(arr, t);
            const settledIdx = upperBound(arr, t - DEFOREST_FLASH_DAYS * DAY);
            const sprite = deforestSprite(arr, settledIdx, t, proj, w, h, zoom);
            if (sprite) ctx.drawImage(sprite, 0, 0, w, h);
            for (let i = settledIdx; i < nowIdx; i++) {
                const d = arr[i];
                const p = proj(d.lon, d.lat);
                if (p.x < -10 || p.y < -10 || p.x > w + 10 || p.y > h + 10) continue;
                const age = (t - d.t) / DAY;
                const flash = Math.max(0, 1 - age / DEFOREST_FLASH_DAYS);
                const r = deforestRadius(d, zoom) * (1 + flash * 0.8);
                ctx.fillStyle = deforestColor(0, 0.35 + flash * 0.55);
                ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 6.283); ctx.fill();
            }
        }

        // --- patrol grid: flat grid-aligned pixels, ash-out over 90d ---
        if (on.effortGrid && D.effortGrid) {
            const eres = D.effortGrid.res;
            const eoff = D.effortGrid.off || 0;
            for (const f of D.effortGrid.frames) {
                if (f.t > t) break;
                const ageD = (t - f.t) / DAY;
                if (ageD > EFFORT_FADE_DAYS) continue;
                const life = 1 - ageD / EFFORT_FADE_DAYS;
                for (const pt of f.pts) {
                    const lon = (pt[0] + eoff) * eres, lat = (pt[1] + eoff) * eres;
                    const p0 = proj(lon - eres / 2, lat + eres / 2);
                    const p1 = proj(lon + eres / 2, lat - eres / 2);
                    if (p1.x < -10 || p1.y < -10 || p0.x > w + 10 || p0.y > h + 10) continue;
                    const km = pt[2];
                    const inten = Math.min(1, Math.log2(1 + km) / 7);
                    const alpha = (0.2 + 0.55 * life) * (0.35 + 0.65 * inten);
                    ctx.fillStyle = effortAsh(1 - life, alpha);
                    // ceil the size so float rounding can't leave 1px seams between cells
                    ctx.fillRect(p0.x, p0.y, Math.max(1.5, Math.ceil(p1.x - p0.x)), Math.max(1.5, Math.ceil(p1.y - p0.y)));
                }
            }
        }

        // --- patrol circles: exact replica of the live pixel stack, animated ---
        // Reproduces the static map's 4-layer look (grid-halo / grid-glow /
        // grid-fill / grid-cells) with the same size ratios and opacity ramps,
        // driven by km-effort as intensity and fade-age as recency. Cells light
        // up when visited then fade — patrol intensity over time.
        //
        // Static layer geometry, measured against cellPx (screen px per 0.1° cell):
        //   ring   = cellPx * 0.25            (gridCellRadius across all zooms)
        //   halo   = cellPx * 0.44 * (1..2)   (intensity factor)
        //   glow   = cellPx * 0.33 * (1..1.3)
        //   fill   = cellPx * 0.22 * fillMetric
        ctx.globalCompositeOperation = 'source-over'; // static map stacks alpha, not additive
        if (on.effortPts && D.effortPts) {
            const eres = D.effortPts.res;
            const eoff = D.effortPts.off || 0;
            const fadeMs = Math.max(bMs * 3, DAY * 10); // linger a touch longer than fires
            const pw = (x, s) => { // piecewise-linear, mirrors MapLibre interpolate
                if (x <= s[0][0]) return s[0][1];
                for (let i = 1; i < s.length; i++) if (x <= s[i][0]) {
                    const [x0, y0] = s[i - 1], [x1, y1] = s[i];
                    return y0 + (y1 - y0) * (x - x0) / (x1 - x0);
                }
                return s[s.length - 1][1];
            };
            for (const f of D.effortPts.frames) {
                if (f.t > t) break;
                const age = t - f.t;
                if (age > fadeMs) continue;
                const k = 1 - age / fadeMs;                 // recency: 1 fresh → 0 faded
                const flash = Math.max(0, 1 - age / (fadeMs * 0.25)); // bright pop on arrival
                // static recency multipliers
                const recHalo = pw(k, [[0, 0.3], [0.5, 0.6], [1, 1]]);
                const recGlow = pw(k, [[0, 0.25], [0.5, 0.55], [1, 1]]);
                const recRing = pw(k, [[0, 0.3], [0.5, 0.6], [1, 1]]);
                for (const pt of f.pts) {
                    const lon = (pt[0] + eoff) * eres, lat = (pt[1] + eoff) * eres;
                    const p = proj(lon, lat);
                    if (p.x < -30 || p.y < -30 || p.x > w + 30 || p.y > h + 30) continue;
                    const km = pt[2];
                    const inten = Math.min(1.5, Math.log2(1 + km) / 4.5); // → static 0..1.5 scale
                    const cellPx = Math.abs(proj(lon + eres, lat).x - p.x);
                    const rRing = Math.max(2, cellPx * 0.25);
                    // halo — grid-halo: #22c55e, blur 1.0 (pure gradient falloff)
                    const intenF = pw(inten, [[0, 1], [0.5, 1.2], [1, 1.5], [1.5, 2]]);
                    const rHalo = cellPx * 0.44 * intenF * (1 + flash * 0.3);
                    const haloA = pw(inten, [[0, 0.1], [0.5, 0.2], [1, 0.35], [1.5, 0.5]]) * recHalo * k + flash * 0.15;
                    let g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, rHalo);
                    g.addColorStop(0, `rgba(34,197,94,${Math.min(0.7, haloA)})`);
                    g.addColorStop(0.6, `rgba(34,197,94,${Math.min(0.5, haloA * 0.45)})`);
                    g.addColorStop(1, 'rgba(34,197,94,0)');
                    ctx.fillStyle = g;
                    ctx.beginPath(); ctx.arc(p.x, p.y, rHalo, 0, 6.283); ctx.fill();
                    // glow — grid-glow: #22c55e, blur 0.7 (tighter core)
                    const rGlow = cellPx * 0.33 * pw(inten, [[0, 1], [1, 1.3]]);
                    const glowA = pw(inten, [[0, 0.1], [0.5, 0.25], [1, 0.4]]) * recGlow * k;
                    g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, rGlow);
                    g.addColorStop(0, `rgba(34,197,94,${Math.min(0.7, glowA)})`);
                    g.addColorStop(0.5, `rgba(34,197,94,${Math.min(0.5, glowA * 0.7)})`);
                    g.addColorStop(1, 'rgba(34,197,94,0)');
                    ctx.fillStyle = g;
                    ctx.beginPath(); ctx.arc(p.x, p.y, rGlow, 0, 6.283); ctx.fill();
                    // fill — grid-fill: #4ade80, crisp, sized by fill metric
                    const fillMetric = pw(inten, [[0, 0], [0.3, 0.3], [0.7, 0.6], [1, 0.8], [1.5, 1]]);
                    const rFill = Math.max(1, cellPx * 0.22 * fillMetric);
                    const fillA = pw(inten, [[0, 0], [0.1, 0.35], [0.3, 0.55], [0.7, 0.75], [1, 0.9]]) * k;
                    if (fillA > 0.01) {
                        ctx.fillStyle = `rgba(74,222,128,${fillA})`;
                        ctx.beginPath(); ctx.arc(p.x, p.y, rFill, 0, 6.283); ctx.fill();
                    }
                    // ring — grid-cells: #4ade80 stroke, opacity from recency
                    ctx.strokeStyle = `rgba(74,222,128,${recRing * Math.min(1, k * 2)})`;
                    ctx.lineWidth = Math.max(0.5, Math.min(2, cellPx * 0.035));
                    ctx.beginPath(); ctx.arc(p.x, p.y, rRing, 0, 6.283); ctx.stroke();
                }
            }
        }
        ctx.globalCompositeOperation = 'lighter';

        // --- fire grid ---
        //
        // Three renderings of one layer, and which one you get is decided by
        // the cell's size in SCREEN PIXELS — never by a zoom number, because
        // the same zoom over a different viewport is a different picture:
        //
        //   small cell   -> a soft blob (the classic heat look)
        //   large cell   -> the cell itself, like the patrol grid, because a
        //                   blob at the centre of a visibly large cell claims
        //                   a peak the pre-agg tables never asserted
        //   zoomed right in -> `loadLayer` has already swapped the whole layer
        //                   for real detections (`asPoints`)
        //
        // The old code sized the blob from `zoom/5`, so blobs grew faster than
        // the ground they cover and the layer got *less* informative exactly
        // when the user zoomed in for more. The effort layer has always done
        // this from `cellPx`; this did not, and that was the visible bug over
        // a 1:250k historical sheet.
        if (on.fireGrid && D.fireGrid && D.fireGrid.asPoints) {
            drawFirePoints(ctx, D.fireGrid.points, t, proj, w, h, zoom);
        } else if (on.fireGrid && D.fireGrid) {
            const fadeMs = bMs * 2.2;
            const res = D.fireGrid.res;
            for (const f of D.fireGrid.frames) {
                if (f.t > t) break;
                const age = t - f.t;
                if (age > fadeMs) continue;
                const k = 1 - age / fadeMs;
                for (const pt of f.pts) {
                    const lon = pt[0] * res, lat = pt[1] * res;
                    const p = proj(lon, lat);
                    if (p.x < -20 || p.y < -20 || p.x > w + 20 || p.y > h + 20) continue;
                    const n = pt[2];
                    const cellPx = Math.abs(proj(lon + res, lat).x - p.x);
                    const inten = Math.min(1, Math.log2(1 + n) / 6);
                    if (cellPx > GRID_CELL_PX_MAX) {
                        // The cell is a visible AREA now, so draw the area.
                        // We only reach this instead of real points when the
                        // server refused points for this view — i.e. when there
                        // are genuinely too many detections to ship. Ceil the
                        // size so float rounding cannot leave 1px seams.
                        const p1 = proj(lon + res, lat - res);
                        const x0 = p.x - (p1.x - p.x) / 2, y0 = p.y - (p1.y - p.y) / 2;
                        ctx.fillStyle = `rgba(255,${Math.round(90 + 110 * (1 - inten))},40,${(0.18 + 0.5 * inten) * k})`;
                        ctx.fillRect(x0, y0, Math.max(1.5, Math.ceil(p1.x - p.x)),
                                             Math.max(1.5, Math.ceil(p1.y - p.y)));
                        continue;
                    }
                    // Blob radius must at least COVER its cell, or the layer
                    // draws a halftone lattice of separated dots — a picture of
                    // our 0.1° binning rather than of the fires. Overlapping
                    // neighbours is the point: where fire is continuous the
                    // blobs merge into a front, and additive blending then
                    // makes the hot core brighter on its own.
                    const r = Math.max(3, Math.min(60, cellPx * (0.8 + 0.6 * inten)));
                    const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
                    g.addColorStop(0, `rgba(255,${Math.round(140 + 90 * k)},40,${0.55 * k + 0.1})`);
                    g.addColorStop(0.5, `rgba(239,68,68,${0.3 * k})`);
                    g.addColorStop(1, 'rgba(239,68,68,0)');
                    ctx.fillStyle = g;
                    ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 6.283); ctx.fill();
                }
            }
        }
        // --- fire points: individual detections flash + afterglow ---
        if (on.firePts && D.firePts) drawFirePoints(ctx, D.firePts, t, proj, w, h, zoom);
        ctx.globalCompositeOperation = 'source-over';

        // --- fire trajectories: build up one-by-one, then ashen out (red → grey → gone) ---
        if (on.trajs && D.trajs) {
            // Project each group's points once per view transform, not once per
            // frame: at 800 groups x ~30 points that was ~24k projections at
            // 60 fps, and the geometry does not depend on t — only how much of
            // it is drawn does. Also skip groups whose extent is off screen.
            projectTrajs(D.trajs, proj, w, h);
            // DENSITY-AWARE INK. The limit is now 6,000 paths rather than 800,
            // and 6,000 opaque 2.5px strokes is a red sheet: every path present,
            // none legible. Thin and translucent at density, the overlaps
            // accumulate (the canvas is in 'lighter' composite here) so
            // corridors and repeatedly-burnt ground glow by themselves, while a
            // sparse view stays bold. Same rule, and the same reason, as
            // densityPaint() in lodlayer.js.
            const nT = D.trajs.length;
            const inkW = nT > 3000 ? 0.7 : nT > 1200 ? 1.0 : nT > 400 ? 1.6 : 2.5;
            const inkA = nT > 3000 ? 0.30 : nT > 1200 ? 0.45 : nT > 400 ? 0.7 : 0.95;
            const headR = nT > 1200 ? 4 : 7;
            for (const g of D.trajs) {
                if (g.t0 > t) continue;
                if (g._off) continue;
                let alpha, ash;
                if (t <= g.t1) {
                    alpha = inkA; ash = 0;
                } else {
                    const fade = (t - g.t1) / (TRAJ_FADE_DAYS * DAY);
                    if (fade >= 1) continue;               // fully gone
                    ash = Math.min(1, fade * 1.6);         // grey out first…
                    alpha = inkA * (1 - fade);             // …then vanish
                }
                ctx.strokeStyle = ashColor(ash, alpha);
                ctx.lineWidth = t <= g.t1 ? inkW : inkW * 0.6;
                ctx.lineJoin = 'round'; ctx.lineCap = 'round';
                ctx.beginPath();
                let started = false, headX = null, headY = null;
                const scr = g._scr;
                for (let i = 0; i < g.pts.length; i++) {
                    const pt = g.pts[i];
                    if (pt[2] <= t) {
                        const x = scr[i * 2], y = scr[i * 2 + 1];
                        if (!started) { ctx.moveTo(x, y); started = true; }
                        else ctx.lineTo(x, y);
                        headX = x; headY = y;
                    } else {
                        if (started && i > 0) {
                            const prev = g.pts[i - 1];
                            const span = pt[2] - prev[2];
                            if (span > 0) {
                                const frac = (t - prev[2]) / span;
                                if (frac > 0) {
                                    // interpolate in screen space: the segment
                                    // is a few km, so it is the same line.
                                    const x = scr[(i - 1) * 2] + (scr[i * 2] - scr[(i - 1) * 2]) * frac;
                                    const y = scr[(i - 1) * 2 + 1] + (scr[i * 2 + 1] - scr[(i - 1) * 2 + 1]) * frac;
                                    ctx.lineTo(x, y);
                                    headX = x; headY = y;
                                }
                            }
                        }
                        break;
                    }
                }
                if (started) ctx.stroke();
                if (headX !== null && t <= g.t1 + 2 * DAY) {
                    const gr = ctx.createRadialGradient(headX, headY, 0, headX, headY, headR);
                    gr.addColorStop(0, 'rgba(255,220,120,0.95)');
                    gr.addColorStop(0.4, 'rgba(255,120,50,0.7)');
                    gr.addColorStop(1, 'rgba(255,120,50,0)');
                    ctx.fillStyle = gr;
                    ctx.beginPath(); ctx.arc(headX, headY, headR, 0, 6.283); ctx.fill();
                }
            }
        }

        if (clipped) ctx.restore();
    }

    // ---------- playback ----------
    function loop(now) {
        if (!A || !A.playing) return;
        const dt = Math.min(100, now - (A.lastNow || now));
        A.lastNow = now;
        A.t += A.speed * DAY * dt / 1000;
        // Reaching the end IS a pause, so it must get the hover probe too —
        // otherwise a run that finished on its own is silently uninspectable
        // while one the user stopped by hand is not.
        if (A.t >= A.t1) { A.t = A.t1; A.playing = false; updatePlayBtn(); syncProbe(); }
        drawAndSync();
        if (A.playing) A.raf = requestAnimationFrame(loop);
    }

    function sliderRangePcts() {
        const sh = document.getElementById('time-slider-start');
        const eh = document.getElementById('time-slider-end');
        const sp = sh ? parseFloat(sh.style.left) || 0 : 0;
        const ep = eh ? parseFloat(eh.style.left) || 100 : 100;
        return [sp, ep];
    }

    // Is there anything to draw at time t? A time animation legitimately
    // starts empty — at t0 no trajectory has begun and no fire has been
    // detected yet — but a share link that opens *paused* at t0 then shows a
    // blank map, which reads as "the layer is broken", not as "the film has
    // not started". Used only to word a hint; drawing is unaffected.
    function frameHasContent(t) {
        const D = A.data, on = A.on;
        const has = arr => Array.isArray(arr) && arr.length;
        if (on.trajs && has(D.trajs) && D.trajs.some(g => g.t0 <= t)) return true;
        if (on.deforest && has(D.deforest) && D.deforest.some(d => d.t <= t)) return true;
        if (on.settlements && has(D.settlements)) return true;
        if (on.infra && has(D.infra)) return true;
        if (on.firePts && has(D.firePts) && D.firePts.some(p => p[2] <= t)) return true;
        // fireGrid can be serving real detections at high zoom (asPoints).
        if (on.fireGrid && D.fireGrid && has(D.fireGrid.points) &&
            D.fireGrid.points.some(p => p[2] <= t)) return true;
        for (const n of ['fireGrid', 'effortGrid', 'effortPts']) {
            const g = on[n] && D[n];
            if (g && g.frames && g.frames.some(f => f.t <= t)) return true;
        }
        return false;
    }

    function drawAndSync() {
        draw(A.t);
        const el = document.getElementById('anim-date-lbl');
        if (el) el.textContent = fmtDateHuman(A.t);
        const sp = document.getElementById('anim-speed-lbl');
        if (sp) sp.textContent = fmtSpeed();
        // playhead + progress in slider track
        const [s, e] = sliderRangePcts();
        const frac = Math.max(0, Math.min(1, (A.t - A.t0) / (A.t1 - A.t0)));
        const pct = s + (e - s) * frac;
        const ph = document.getElementById('anim-playhead');
        if (ph) ph.style.left = pct + '%';
        const pr = document.getElementById('anim-progress');
        if (pr) { pr.style.left = s + '%'; pr.style.width = (pct - s) + '%'; }
        if (typeof window._animShareSync === 'function') window._animShareSync();
    }

    function fmtSpeed() {
        const s = A.speed;
        if (s >= 27) return (s / 30).toFixed(1) + ' mo/s';
        if (s >= 6) return (s / 7).toFixed(1) + ' wk/s';
        return s.toFixed(1) + ' d/s';
    }

    function updatePlayBtn() {
        const b = document.getElementById('anim-play');
        if (b) b.textContent = A.playing ? '⏸' : '▶';
    }

    // ---------- interaction while paused ----------
    //
    // A paused animation is a map, and a map you cannot ask questions of is a
    // screenshot. The canvas is `pointer-events: none` (so pan and zoom keep
    // working), and none of what it draws is a MapLibre feature, so the shared
    // tooltip cannot see it — it hit-tests rendered layers. `registerProbe`
    // lets the animator answer for its own arrays instead, and the answer then
    // competes in the same priority ordering as everything else: a pinned
    // vector the user deliberately put there still wins.
    //
    // Only while PAUSED. A tip that follows a moving animation is noise: the
    // thing under the cursor is gone by the time it is read.
    const PROBE_ID = 'animator-frame';
    const PROBE_PX = 9;             // hit radius in screen pixels

    function probeFrame(e) {
        if (!A || A.playing) return null;
        const pt = e && e.point;
        if (!pt) return null;
        const t = A.t, D = A.data, on = A.on;
        const proj = (lon, lat) => map.project([lon, lat]);
        let best = null;

        function consider(dist, make) {
            if (dist > PROBE_PX) return;
            if (best && best.dist <= dist) return;
            best = { dist, make };
        }

        // Trajectories: nearest point of the part that has happened by t.
        // _scr is already the projected geometry (projectTrajs), so this costs
        // nothing beyond the walk.
        if (on.trajs && D.trajs) {
            projectTrajs(D.trajs, proj, A.canvas.clientWidth, A.canvas.clientHeight);
            for (const g of D.trajs) {
                if (g.t0 > t || g._off || !g._scr) continue;
                if (t > g.t1 + TRAJ_FADE_DAYS * DAY) continue;   // ashed away
                for (let i = 0; i < g.pts.length; i++) {
                    if (g.pts[i][2] > t) break;
                    const dx = g._scr[i * 2] - pt.x, dy = g._scr[i * 2 + 1] - pt.y;
                    consider(Math.hypot(dx, dy), () => trajTip(g, t));
                }
            }
        }
        if (on.deforest && D.deforest) {
            for (const d of D.deforest) {
                if (d.t > t) break;
                const p = proj(d.lon, d.lat);
                consider(Math.hypot(p.x - pt.x, p.y - pt.y), () => ({
                    label: 'Deforestation',
                    lines: [fmtDateHuman(d.t),
                            d.area ? d.area.toFixed(2) + ' km² cleared' : null]
                }));
            }
        }
        if (on.settlements && D.settlements) {
            for (const s of D.settlements) {
                const p = proj(s.lon, s.lat);
                consider(Math.hypot(p.x - pt.x, p.y - pt.y), () => ({
                    label: 'Settlement',
                    lines: [s.lat.toFixed(4) + ', ' + s.lon.toFixed(4)]
                }));
            }
        }
        const firePts = (on.firePts && D.firePts) ||
                        (on.fireGrid && D.fireGrid && D.fireGrid.asPoints && D.fireGrid.points);
        if (firePts) {
            for (const q of firePts) {
                if (q[2] > t) break;
                if (t - q[2] > DAY * 3) continue;    // faded out of the frame
                const p = proj(q[0], q[1]);
                consider(Math.hypot(p.x - pt.x, p.y - pt.y), () => ({
                    label: 'Fire detection',
                    lines: [fmtDateHuman(q[2]),
                            q[3] ? 'Intensity ' + Math.round(q[3]) + ' MW' : null]
                }));
            }
        }
        if (!best) return null;
        const v = best.make();
        if (v.html) return { html: v.html };
        return {
            html: '<div class="maptip-label">' + v.label + ' · ' + fmtDateHuman(t) + '</div>' +
                  v.lines.filter(Boolean).map(l => '<div class="maptip-meta">' + l + '</div>').join('')
        };
    }

    function trajTip(g, t) {
        const active = t <= g.t1;
        const bits = [
            fmtDateHuman(g.t0) + ' – ' + fmtDateHuman(g.t1),
            g.fires ? g.fires + ' detections' : null,
            g.days ? g.days + ' days' : null,
            g.frp ? 'Intensity ' + Math.round(g.frp) + ' MW' : null,
            g.kmd ? g.kmd.toFixed(1) + ' km/day' : null
        ];
        return {
            html: '<div class="maptip-label">Fire path · ' +
                      (active ? '<span class="maptip-hot">burning</span>' : 'ended') +
                      ' at ' + fmtDateHuman(t) + '</div>' +
                  (g.narrative ? '<div class="maptip-body">' + g.narrative + '</div>' : '') +
                  '<div class="maptip-meta">' + bits.filter(Boolean).join(' · ') + '</div>'
        };
    }

    // Registered on pause, dropped on play — so the probe is never asked to
    // hit-test a frame that has already moved on.
    function syncProbe() {
        if (!window.MapTip || !window.MapTip.registerProbe) return;
        if (A && !A.playing) {
            window.MapTip.registerProbe(PROBE_ID, { probe: probeFrame, priority: 0 });
        } else {
            window.MapTip.unregisterProbe(PROBE_ID);
        }
    }

    function play() {
        if (!A) return;
        if (A.t >= A.t1 - 1) A.t = A.t0;
        A.playing = true; A.lastNow = null;
        updatePlayBtn();
        syncProbe();
        A.raf = requestAnimationFrame(loop);
    }
    function pause() {
        if (!A) return;
        A.playing = false;
        updatePlayBtn();
        if (A.raf) cancelAnimationFrame(A.raf);
        syncProbe();
    }

    // ---------- GIF export ----------
    async function exportGIF() {
        if (!A || A.recording) return;
        A.recording = true;
        pause();
        const btn = document.getElementById('anim-export');
        const label = btn && btn.textContent;
        try {
            btn.textContent = '⏳0%';
            const gifenc = await import('https://unpkg.com/gifenc@1.0.3/dist/gifenc.esm.js');
            const { GIFEncoder, quantize, applyPalette } = gifenc;
            const mapCanvas = map.getCanvas();
            const srcW = A.canvas.clientWidth, srcH = A.canvas.clientHeight;
            const outW = Math.min(720, srcW);
            const outH = Math.round(srcH * outW / srcW);
            const off = document.createElement('canvas');
            off.width = outW; off.height = outH;
            const octx = off.getContext('2d', { willReadFrequently: true });
            // The GIF must play back at the speed set on screen: A.speed is
            // days of simulated time per second of wall clock, so the whole
            // window lasts spanDays / speed seconds. Frame count and delay are
            // derived from that duration, never fixed — a fixed 80×100 ms made
            // every export an 8 s clip regardless of the speed control, which
            // is the one thing the user had just chosen.
            const enc = GIFEncoder();
            const span = A.t1 - A.t0;
            const durSec = Math.max(1, (span / DAY) / Math.max(0.25, A.speed));
            // 10 fps is the target; clamp frame count for size, then stretch
            // the delay so the *duration* stays right (choppier, not faster).
            const frames = Math.max(8, Math.min(GIF_MAX_FRAMES, Math.round(durSec * 10)));
            const delayMs = Math.max(20, Math.min(500, Math.round(durSec * 1000 / frames)));
            for (let i = 0; i < frames; i++) {
                const t = A.t0 + span * i / (frames - 1);
                draw(t);
                octx.fillStyle = '#0a0a0a';
                octx.fillRect(0, 0, outW, outH);
                octx.drawImage(mapCanvas, 0, 0, outW, outH);
                octx.drawImage(A.canvas, 0, 0, outW, outH);
                octx.font = 'bold 16px sans-serif';
                octx.fillStyle = 'rgba(0,0,0,0.55)';
                octx.fillRect(8, outH - 30, 150, 22);
                octx.fillStyle = '#fff';
                octx.fillText(fmtDateHuman(t), 14, outH - 14);
                const { data } = octx.getImageData(0, 0, outW, outH);
                const palette = quantize(data, 256);
                const index = applyPalette(data, palette);
                enc.writeFrame(index, outW, outH, { palette, delay: delayMs });
                btn.textContent = '⏳' + Math.round((i + 1) / frames * 100) + '%';
                if (i % 8 === 0) await new Promise(r => setTimeout(r, 0));
            }
            enc.finish();
            const blob = new Blob([enc.bytes()], { type: 'image/gif' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = `5mp_animation_${fmtDate(A.t0)}_${fmtDate(A.t1)}.gif`;
            a.click();
            setTimeout(() => URL.revokeObjectURL(a.href), 30000);
            toast('GIF downloaded — ' + frames + ' frames, ' + durSec.toFixed(0) + 's at '
                + fmtSpeed() + ' (' + Math.round(blob.size / 1024) + ' KB)', 'success');
        } catch (e) {
            console.error('GIF export failed:', e);
            toast('GIF export failed: ' + e.message, 'error');
        } finally {
            A.recording = false;
            if (btn) btn.textContent = label;
            drawAndSync();
        }
    }

    // ---------- GeoPackage of the paused frame ----------
    //
    // Beside the GIF, and for the same reason the GIF exists: pausing an
    // animation produces a specific, hard-won picture — this window, this
    // viewport, these layers, this instant — and until now the only way to keep
    // it was as pixels. This hands over the same thing as DATA.
    //
    // Three things it must get right:
    //
    //  * PAUSE FIRST. The instant is the question; it must not move between
    //    the click and the request (the GIF does the same).
    //  * It exports only WHAT IS ON SCREEN — the fetch bbox, the chips that
    //    are on, the window up to the playhead — which is the whole difference
    //    from the area/AOI download menu, where the file is everything we hold
    //    for that area. Two different questions, deliberately two buttons.
    //  * The instant goes in the label, or two cards in the bell are
    //    indistinguishable. The server puts it in the title and the filename;
    //    this only has to name it in the toast.
    //
    // Small views come back as an immediate download; big ones become a
    // notification card with a progress bar, a delete button and a 21-day
    // link. GPKGExport owns all of that — there is exactly one job watcher.
    async function exportGPKG() {
        if (!A || A.exporting) return;
        if (!window.GeoPackageExport) { toast('Export module not loaded', 'error'); return; }
        pause();
        const btn = document.getElementById('anim-export');
        const on = LAYER_ORDER.filter(n => A.on[n]);
        if (!on.length) {
            toast('No layers are switched on — turn on at least one chip to export it', 'warning');
            return;
        }
        A.exporting = true;
        const label = btn && btn.textContent;
        if (btn) { btn.textContent = '⏳'; btn.disabled = true; }
        try {
            await window.GeoPackageExport.startView({
                bbox: A.fetchBbox,
                from: A.fromISO,
                // The paused frame shows what had happened BY the playhead, so
                // the instant is the upper bound of the window, not a label on
                // top of the full range. The server enforces this too; sending
                // both keeps the cache key honest about which frame it was.
                to: A.toISO,
                at: fmtDate(A.t),
                layers: on,
                aoi: A.aoiID || '',
                label: 'this view at ' + fmtDateHuman(A.t),
            });
        } catch (e) {
            console.error('GPKG export failed:', e);
            toast('GeoPackage export failed: ' + e.message, 'error');
        } finally {
            A.exporting = false;
            if (btn) { btn.textContent = label; btn.disabled = false; }
        }
    }

    // ---------- the download menu ----------
    //
    // The GIF and the GeoPackage used to be two bare buttons in the slider
    // header, and neither could be handed to anyone: the whole point of both is
    // that you have found a picture worth keeping, and "open the animator, set
    // these layers, scrub to this date, press GPKG" is a paragraph where a URL
    // would do.
    //
    // So they are one ⬇ that opens the SAME menu component the park and AOI
    // downloads use (.aoi-menu / .aoi-menu-row / copyExportLink in globe.html):
    // one row per download, each with a ⧉ that copies a link which reproduces
    // this exact animation — window, viewport, layers, speed, playhead — and
    // points at that row. One menu pattern for every download in the app,
    // including its Safari copy-link workaround and its mobile sizing.
    //
    // A highlighted row is a HINT, not an action, for the same reason as the
    // area exports: opening a link must not spend minutes of CPU or hundreds of
    // MB. The recipient lands on the frame with the row pointed at, and clicks.
    const EXPORT_ITEMS = {
        gif:  { icon: 'icon-film',     label: 'GIF',        note: 'the animation', run: () => exportGIF() },
        gpkg: { icon: 'icon-database', label: 'GeoPackage', note: 'this frame, QGIS', run: () => exportGPKG() }
    };

    // The link that reproduces this animation and points at one download.
    // buildShareUrl() already writes anim/anim_speed/anim_t/anim_aoi and the
    // viewport; this only adds which entry to point at, and forces the paused
    // flag, because a download is about a frame.
    function exportShareLink(item) {
        let url;
        try {
            url = typeof buildShareUrl === 'function' ? buildShareUrl() : window.location.href;
        } catch (e) { url = window.location.href; }
        const u = new URL(url, window.location.href);
        u.searchParams.set('anim_paused', '1');
        u.searchParams.set('anim_export', item);
        return u.toString();
    }

    function exportMenuHTML() {
        const mobile = window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
        return Object.keys(EXPORT_ITEMS).map(k => {
            // Encoding a GIF is tens of seconds of main-thread canvas work per
            // export; on a phone it is minutes and a hot battery. The row is
            // hidden there rather than offered and then apologised for.
            if (k === 'gif' && mobile) return '';
            const it = EXPORT_ITEMS[k];
            const link = exportShareLink(k);
            return `<div class="aoi-menu-row">`
                 + `<button class="aoi-menu-item" data-item="${k}" data-anim-export="${k}">`
                 + `<i class="${it.icon}"></i><span>${it.label}</span><em>${it.note}</em></button>`
                 + `<button class="aoi-menu-copy" title="Copy a link to this frame and this download"`
                 + ` aria-label="Copy a link to this frame and this download"`
                 + ` data-url="${link.replace(/"/g, '&quot;')}"><i class="icon-copy"></i></button>`
                 + `</div>`;
        }).join('');
    }

    function closeExportMenu() {
        const el = document.getElementById('anim-export-menu');
        if (el) el.remove();
    }

    function toggleExportMenu(btn, highlight) {
        const open = document.getElementById('anim-export-menu');
        closeExportMenu();
        if (open && !highlight) return;
        if (!A) return;
        pause();     // the frame is the question; it must not move under the menu
        const el = document.createElement('div');
        el.id = 'anim-export-menu';
        el.className = 'aoi-menu';
        el.innerHTML = exportMenuHTML();
        document.body.appendChild(el);
        el.querySelectorAll('[data-anim-export]').forEach(b => {
            b.onclick = (e) => {
                e.stopPropagation();
                closeExportMenu();
                const it = EXPORT_ITEMS[b.dataset.animExport];
                if (it) it.run();
            };
        });
        el.querySelectorAll('.aoi-menu-copy').forEach(b => {
            b.onclick = (e) => (typeof copyExportLink === 'function')
                ? copyExportLink(e, b)
                : (e.preventDefault(), prompt('Copy this link:', b.dataset.url), false);
        });
        // Above the slider, not below it: the button lives in the bottom
        // furniture, so a menu dropped downwards would be off screen.
        const r = btn.getBoundingClientRect();
        const w = el.offsetWidth, h = el.offsetHeight;
        el.style.left = Math.max(6, Math.min(window.innerWidth - w - 6, r.left - w / 2 + r.width / 2)) + 'px';
        el.style.top = (r.top - h - 6 > 6 ? r.top - h - 6 : r.bottom + 6) + 'px';
        if (highlight) {
            const item = el.querySelector(`.aoi-menu-item[data-item="${highlight}"]`);
            if (item) {
                item.classList.add('highlight');
                item.setAttribute('tabindex', '0');
                item.focus({ preventScroll: true });
            }
        }
        setTimeout(() => document.addEventListener('click', closeExportMenu, { once: true }),
                   highlight ? 400 : 0);
    }

    // ---------- chips ----------
    function updateChips() {
        if (!A) return;
        document.querySelectorAll('.anim-chip').forEach(chip => {
            const name = chip.dataset.layer;
            chip.classList.toggle('on', !!A.on[name]);
            const dot = chip.querySelector('i');
            if (dot) dot.style.background = A.on[name] ? LAYERS[name].color : '#555';
            if (A.on[name]) chip.style.color = '';
        });
    }

    // Hide the live map's patrol pixel layers while the animator renders effort
    // (they'd overlap the animated circles/grid); restore per viewLayers.pixels.
    const BASE_EFFORT_LAYERS = ['grid-halo', 'grid-glow', 'grid-fill', 'grid-cells'];
    function syncBaseEffortVisibility() {
        if (typeof map === 'undefined' || !map || !map.getLayer) return;
        const animatingEffort = !!(A && (A.on.effortGrid || A.on.effortPts));
        const vis = (animatingEffort || !(window.viewLayers && window.viewLayers.pixels)) ? 'none' : 'visible';
        BASE_EFFORT_LAYERS.forEach(id => {
            try { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', vis); } catch (e) {}
        });
    }

    async function toggleChip(name) {
        if (!A) return;
        A.on[name] = !A.on[name];
        updateChips();
        syncBaseEffortVisibility();
        if (A.on[name]) await ensureLayer(name);
        draw(A.t);
        if (typeof updateShareURL === 'function') updateShareURL();
    }

    // ---------- UI build / teardown ----------
    function buildUI() {
        const container = document.getElementById('time-slider-container');
        const header = container.querySelector('.time-slider-header');
        const dateRow = document.getElementById('time-slider-date');
        container.classList.add('animating');

        // inline controls in the date row
        const inline = document.createElement('span');
        inline.id = 'anim-inline';
        inline.innerHTML = `
            <button id="anim-play" class="anim-btn primary" title="Play/Pause (space)">⏸</button>
            <span id="anim-date-lbl"></span>
            <button id="anim-slower" class="anim-btn" title="Slower (hold to ramp)">−</button>
            <span id="anim-speed-lbl"></span>
            <button id="anim-faster" class="anim-btn" title="Faster (hold to ramp)">+</button>
            <button id="anim-export" class="anim-btn" title="Download this animation — GIF, or a GeoPackage of this frame">⬇</button>
            <button id="anim-close" class="anim-btn" title="Close animator (Esc)">✕</button>`;
        dateRow.appendChild(inline);
        // staggered expansion, same behaviour as the date preset tags
        Array.from(inline.children).forEach((el, i) => {
            setTimeout(() => el.classList.add('visible'), 40 + i * 45);
        });

        // chips row
        const chips = document.createElement('div');
        chips.id = 'anim-chips';
        for (const name of LAYER_ORDER) {
            const def = LAYERS[name];
            const chip = document.createElement('span');
            chip.className = 'anim-chip';
            chip.dataset.layer = name;
            chip.title = def.title;
            chip.innerHTML = `<i></i>${def.label}`;
            chip.onclick = () => toggleChip(name);
            // hide chips with no possible data
            if ((name === 'turb' || name === 'infra')) {
                const snap = A.snapPreview || (A.snapPreview = snapshotPinned());
                if (name === 'turb' && !(snap.turb.plume.length || snap.turb.mines.length)) chip.classList.add('unavailable');
                if (name === 'infra' && !snap.statics.length) chip.classList.add('unavailable');
            }
            // patrol layers: hidden entirely when the pixels toggle is off
            // (unless a share link explicitly enabled them). When the account
            // owns no patrol data at all they are shown but inert -- patrol
            // effort is scoped to the account it was uploaded in
            // (srv/tenant.go), so animating it here would only ever play an
            // empty layer, and a silently empty layer reads as a broken
            // feature rather than as "not yours".
            if (name === 'effortGrid' || name === 'effortPts') {
                if (window.HAS_PATROL === false) {
                    chip.classList.add('unavailable');
                    chip.title = 'No patrol tracks in this account — patrol effort is only visible to the account it was uploaded in';
                } else if (!(window.viewLayers && window.viewLayers.pixels) && !A.on[name]) {
                    chip.classList.add('hidden');
                }
            }
            chips.appendChild(chip);
        }
        header.appendChild(chips);
        // staggered reveal (same behaviour as date preset tags)
        Array.from(chips.children).filter(c => !c.classList.contains('hidden')).forEach((chip, i) => {
            setTimeout(() => chip.classList.add('visible'), 60 + i * 45);
        });
        updateChips();

        // playhead + progress in slider track — position BEFORE appending so
        // they don't flash at the track's left edge and jump on first draw
        const track = document.getElementById('time-slider-track');
        const [s0, e0] = sliderRangePcts();
        const frac0 = Math.max(0, Math.min(1, (A.t - A.t0) / (A.t1 - A.t0)));
        const pct0 = s0 + (e0 - s0) * frac0;
        const prog = document.createElement('div');
        prog.id = 'anim-progress';
        prog.style.left = s0 + '%';
        prog.style.width = (pct0 - s0) + '%';
        track.appendChild(prog);
        const ph = document.createElement('div');
        ph.id = 'anim-playhead';
        ph.style.left = pct0 + '%';
        track.appendChild(ph);

        // playhead drag = scrub (works during playback; pauses while dragging)
        // Pulling the playhead PAST the left range handle and holding it there
        // extends the time window backwards (gravity: a brief return-to-start
        // does nothing; a sustained pull steadily drags the start date earlier).
        let wasPlaying = false;
        let pull = null; // {t0Ext, raf, last, over, heldSince}
        const PULL_DELAY_MS = 350;  // sustained-pull threshold before extending
        const startHandleEl = () => document.getElementById('time-slider-start');
        function pullLoop(now) {
            if (!pull) return;
            if (now - pull.heldSince >= PULL_DELAY_MS) {
                const dt = Math.min(100, now - (pull.last || now)) / 1000;
                // rate ∝ how far past the handle; grows the extension exponentially
                const pressure = Math.min(1.5, 0.25 + pull.over / 70);
                const span = A.t1 - pull.t0Ext;
                pull.t0Ext -= span * 0.9 * pressure * dt;
                const min = parseD('2020-01-01');
                if (pull.t0Ext < min) pull.t0Ext = min;
                A.t = pull.t0Ext; // playhead pinned at the (moving) start
                // preview: move start handle + progress to the extended position
                const sh = startHandleEl();
                const range = document.getElementById('time-slider-range');
                const totalSpan = parseD(fmtDate(Date.now())) - min;
                const pctS = Math.max(0, (pull.t0Ext - min) / totalSpan * 100);
                if (sh) { sh.style.left = pctS + '%'; sh.classList.add('fine-squeeze'); }
                const [, ep] = [null, parseFloat(document.getElementById('time-slider-end').style.left) || 100];
                if (range) { range.style.left = pctS + '%'; range.style.width = (ep - pctS) + '%'; }
                const ph2 = document.getElementById('anim-playhead');
                if (ph2) ph2.style.left = pctS + '%';
                const prg = document.getElementById('anim-progress');
                if (prg) { prg.style.left = pctS + '%'; prg.style.width = '0%'; }
                const lbl = document.getElementById('anim-date-lbl');
                if (lbl) lbl.textContent = fmtDateHuman(pull.t0Ext);
                const sLbl = document.getElementById('time-slider-date-start');
                if (sLbl) sLbl.textContent = fmtDateHuman(pull.t0Ext);
            }
            pull.last = now;
            pull.raf = requestAnimationFrame(pullLoop);
        }
        const scrubMove = (e) => {
            const rect = track.getBoundingClientRect();
            const x = (e.touches ? e.touches[0].clientX : e.clientX);
            const pct = Math.max(0, Math.min(100, (x - rect.left) / rect.width * 100));
            const [s, ep] = sliderRangePcts();
            // Measure the pull against the ORIGINAL handle position: the
            // extension preview moves the live handle toward the finger, which
            // would otherwise cancel the pull as soon as it catches up.
            const sOrig = pull ? pull.sPct : s;
            const sX = rect.left + sOrig / 100 * rect.width;
            const over = sX - x; // px past the left handle
            if (over > 8 && !A.recording) {
                if (!pull) {
                    pull = { t0Ext: A.t0, raf: 0, last: null, over, heldSince: performance.now(), sPct: s };
                    pull.raf = requestAnimationFrame(pullLoop);
                }
                pull.over = over;
                e.preventDefault();
                return;
            }
            if (pull) { // pulled back inside → cancel extension preview
                cancelAnimationFrame(pull.raf); pull = null;
                const sh = startHandleEl(); if (sh) sh.classList.remove('fine-squeeze');
                if (typeof updateSliderDisplay === 'function') updateSliderDisplay();
            }
            const frac = Math.max(0, Math.min(1, (pct - s) / Math.max(0.001, ep - s)));
            A.t = A.t0 + (A.t1 - A.t0) * frac;
            drawAndSync();
            e.preventDefault();
        };
        const scrubEnd = () => {
            document.removeEventListener('pointermove', scrubMove);
            document.removeEventListener('pointerup', scrubEnd);
            document.removeEventListener('pointercancel', scrubEnd);
            if (pull) {
                cancelAnimationFrame(pull.raf);
                const extended = pull.t0Ext < A.t0 - DAY / 2;
                const newFrom = fmtDate(pull.t0Ext);
                pull = null;
                const sh = startHandleEl(); if (sh) sh.classList.remove('fine-squeeze');
                if (extended && typeof window.setTimeSliderRange === 'function') {
                    // Applies the widened window; onDateRangeChanged reopens the
                    // animation over it (keeping layers), starting from the left.
                    window.setTimeSliderRange(newFrom, A.toISO);
                    return;
                }
                if (typeof updateSliderDisplay === 'function') updateSliderDisplay();
            }
            if (wasPlaying) play();
        };
        const beginScrub = (e) => {
            e.preventDefault(); e.stopPropagation();
            wasPlaying = A.playing;
            pause();
            document.addEventListener('pointermove', scrubMove);
            document.addEventListener('pointerup', scrubEnd);
            document.addEventListener('pointercancel', scrubEnd);
        };
        ph.addEventListener('pointerdown', beginScrub);
        // Exposed so the time-slider's handle arbitration (globe.html) can hand
        // an ambiguous grab (playhead overlapping a range handle) to the scrub.
        window._animBeginScrub = (e) => { beginScrub(e); scrubMove(e); };

        // controls
        document.getElementById('anim-play').onclick = () => A.playing ? pause() : play();
        document.getElementById('anim-close').onclick = () => window.Animator.close();
        document.getElementById('anim-export').onclick = (e) => {
            e.stopPropagation();
            toggleExportMenu(e.currentTarget);
        };

        // speed: click steps, press-and-hold ramps (mobile friendly)
        const speedBtn = (id, factor) => {
            const btn = document.getElementById(id);
            let timer = null, held = false;
            const bump = () => { A.speed = Math.max(0.25, Math.min(365, A.speed * factor)); drawAndSync(); };
            btn.addEventListener('click', (e) => { if (!held) bump(); held = false; });
            btn.addEventListener('pointerdown', () => {
                timer = setTimeout(function ramp() {
                    held = true; bump();
                    timer = setTimeout(ramp, 130);
                }, 350);
            });
            const stop = () => { if (timer) { clearTimeout(timer); timer = null; } };
            btn.addEventListener('pointerup', stop);
            btn.addEventListener('pointerleave', stop);
            btn.addEventListener('pointercancel', stop);
        };
        speedBtn('anim-slower', 1 / 1.35);
        speedBtn('anim-faster', 1.35);

        // keyboard
        A.keyHandler = (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
            if (e.code === 'Space') { e.preventDefault(); A.playing ? pause() : play(); }
            else if (e.code === 'ArrowRight') { A.t = Math.min(A.t1, A.t + bucketMs(chooseStep((A.t1 - A.t0) / DAY))); drawAndSync(); }
            else if (e.code === 'ArrowLeft') { A.t = Math.max(A.t0, A.t - bucketMs(chooseStep((A.t1 - A.t0) / DAY))); drawAndSync(); }
            else if (e.code === 'Escape') { window.Animator.close(); }
        };
        document.addEventListener('keydown', A.keyHandler);
    }

    function teardownUI() {
        const container = document.getElementById('time-slider-container');
        if (container) container.classList.remove('animating');
        ['anim-inline', 'anim-chips', 'anim-playhead', 'anim-progress'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.remove();
        });
    }

    // refetch point layers when the viewport leaves the fetched bbox (pan/zoom during play)
    function onMoveEnd() {
        if (!A || A.bboxFixed) return;
        // Two reasons to refetch, and only the first one used to exist:
        //
        //  1. the viewport has left the padded box we fetched, so there is
        //     data on screen we simply do not have; or
        //  2. the LEVEL OF DETAIL the view now deserves has changed — zooming
        //     from a continent to a district stays well inside the fetched
        //     box, but a 0.1° grid there is a lattice of squares where real
        //     detections would fit. Without this the layer froze at whatever
        //     detail the view had when the animator opened, which is exactly
        //     the "a cheap rendering quietly becomes a picture" failure this
        //     work exists to remove.
        const lodChanged = A.on.fireGrid && A.data.fireGrid !== undefined &&
            wantPoints(activeBbox().bbox, chooseRes(A.fetchBbox)) !== !!A.data.fireGrid.asPoints;
        if (viewportInside(A.fetchBbox) && !lodChanged) return;
        clearTimeout(A._refetchTimer);
        A._refetchTimer = setTimeout(() => {
            if (!A) return;
            const { bbox } = activeBbox();
            A.fetchBbox = bbox;
            for (const name of LAYER_ORDER) {
                if (A.on[name] && A.data[name] !== undefined && name !== 'turb' && name !== 'infra') {
                    delete A.data[name];
                    ensureLayer(name);
                }
            }
        }, 500);
    }

    // ---------- public API ----------
    window.Animator = {
        async open(opts) {
            opts = opts || {};
            if (A) this.close();
            injectCSS();
            const fromISO = (typeof dateFrom !== 'undefined' && dateFrom) ? dateFrom : '2023-01-01';
            const toISO = (typeof dateTo !== 'undefined' && dateTo) ? dateTo : fmtDate(Date.now());
            let { bbox, fixed } = activeBbox();

            // An AOI animates as its polygon, not as its bounding box: the
            // fetch stays bbox-scoped (every frames endpoint is), but draw()
            // clips to the ring so nothing appears in the corners the AOI
            // does not cover. opts.aoi survives share links via getState().
            //
            // Focus mode is inherited when the caller did not say otherwise:
            // if the user has declared an AOI to be the subject, an animation
            // opened from the time slider must animate that subject, or the
            // ▶ chip and the focus banner disagree about what is on screen.
            let clipGeom = null, aoiID = null;
            const wantAOI = opts.aoi !== undefined ? opts.aoi : (window.aoiFocusID || null);
            if (wantAOI) {
                // window._aois is filled by an async loadAOIs(); a share link
                // that opens the animator can win that race, and then the AOI
                // is silently *not* passed as &aoi=, so aoiExcludeSQL() hides
                // the AOI's own trajectories and the animation plays empty.
                // A missing geometry is a not-loaded-yet, never an answer:
                // fetch it rather than degrade to a bbox animation.
                let a = window._aois && window._aois[wantAOI];
                if (!a || !a.geometry) {
                    try {
                        const r = await fetch(`/api/aois/${encodeURIComponent(wantAOI)}?geometry=1&pwd=`
                            + encodeURIComponent(getPwdSafe()));
                        if (r.ok) { const j = await r.json(); a = j.aoi || j; }
                    } catch (e) { /* fall through: animate as bbox */ }
                }
                if (a && a.geometry) {
                    clipGeom = a.geometry;
                    aoiID = wantAOI;
                    const gb = geomBbox(a.geometry) || a.bbox;
                    if (gb) { bbox = gb.slice(); fixed = true; }
                }
            }

            const { canvas, resize } = makeCanvas();
            A = {
                canvas, ctx: canvas.getContext('2d'), resize,
                data: {}, loading: {}, on: {},
                fromISO, toISO, fetchBbox: bbox, bboxFixed: fixed,
                clipGeom, aoiID,
                t0: parseD(fromISO), t1: parseD(toISO) + DAY - 1,
                playing: false, speed: 1, raf: null, recording: false
            };
            A.t = A.t0;
            const spanDays = (A.t1 - A.t0) / DAY;
            A.speed = opts.speed || Math.max(0.5, spanDays / 20);
            if (opts.t) { const tv = parseD(opts.t); if (tv >= A.t0 && tv <= A.t1) A.t = tv; }

            // default layer set: current toggles + pins, grid vs points by zoom
            let initial;
            if (opts.layers && opts.layers.length) {
                // A share link can name patrol layers the receiving account
                // cannot see (patrol effort is scoped to the account it was
                // uploaded in, srv/tenant.go). Drop them rather than switching
                // them on: an "on" chip that draws nothing is the same wrong
                // answer as an empty layer.
                initial = opts.layers.filter(n => LAYERS[n] &&
                    !(window.HAS_PATROL === false && (n === 'effortGrid' || n === 'effortPts')));
            } else {
                initial = [];
                const v = window.viewLayers || {};
                const pins = pinnedTypes();
                const hiZoom = map.getZoom() >= POINTS_ZOOM && bboxArea(bbox) <= POINTS_MAX_AREA;
                if (v.fires || pins.has('fires')) { initial.push('trajs', hiZoom ? 'firePts' : 'fireGrid'); }
                if (v.pixels && window.HAS_PATROL !== false) initial.push(hiZoom ? 'effortPts' : 'effortGrid');
                if (v.deforest || pins.has('deforest')) initial.push('deforest');
                if (v.settlements || pins.has('settlements')) initial.push('settlements');
                const snap = snapshotPinned();
                A.snapPreview = snap;
                if (snap.statics.length) initial.push('infra');
                if (!initial.length) initial = ['fireGrid', 'trajs'];
            }
            initial.forEach(n => { A.on[n] = true; });

            buildUI();
            syncBaseEffortVisibility();

            // load initial layers with progress modal
            const active = initial.slice();
            let done = 0;
            showLoading('Loading ' + active.length + ' layers…', 5);
            await Promise.all(active.map(n => ensureLayer(n).then(() => {
                done++;
                showLoading('Loaded ' + done + '/' + active.length, Math.round(done / active.length * 100));
            })));
            hideLoading();

            const any = LAYER_ORDER.some(n => A.on[n] && A.data[n] && (
                Array.isArray(A.data[n]) ? A.data[n].length :
                (A.data[n].frames ? A.data[n].frames.length :
                 (A.data[n].points ? A.data[n].points.length : true))));
            if (!any) toast('No animatable data in view for this window — toggle layers or adjust dates', 'warning');

            A.mapHandler = () => { if (A) draw(A.t); };
            A.moveEndHandler = onMoveEnd;
            A.resizeHandler = () => { if (A) { A.resize(); draw(A.t); } };
            map.on('move', A.mapHandler);
            map.on('moveend', A.moveEndHandler);
            window.addEventListener('resize', A.resizeHandler);

            drawAndSync();
            if (opts.paused) { pause(); drawAndSync(); } else play();
            // Opened paused on an empty first frame: say why rather than let
            // the user conclude the layer failed to load.
            if (any && opts.paused && !frameHasContent(A.t)) {
                toast('Paused at ' + fmtDateHuman(A.t) + ' — nothing has happened yet in this window. Press ▶ to play.',
                      'info', { key: 'anim-empty-start' });
            }
            if (typeof updateShareURL === 'function') updateShareURL();

            // ?anim_export= — the shared frame points at a download. It opens
            // the menu with that row highlighted; it never starts the export.
            // Same rule, and the same reason, as ?aoi_menu_item=: a link whose
            // only outcome is minutes of encoding (or a few hundred MB) must be
            // a click the recipient makes, not a consequence of opening a URL.
            if (opts.exportItem && EXPORT_ITEMS[opts.exportItem]) {
                setTimeout(() => {
                    const btn = document.getElementById('anim-export');
                    if (btn) toggleExportMenu(btn, opts.exportItem);
                }, 450);
            }
        },
        close() {
            if (!A) return;
            closeExportMenu();
            pause();
            map.off('move', A.mapHandler);
            map.off('moveend', A.moveEndHandler);
            window.removeEventListener('resize', A.resizeHandler);
            document.removeEventListener('keydown', A.keyHandler);
            clearTimeout(A._refetchTimer);
            if (A.canvas) A.canvas.remove();
            window._animBeginScrub = null;
            teardownUI();
            hideLoading();
            invalidateSprites();
            A = null;
            if (window.MapTip && window.MapTip.unregisterProbe) window.MapTip.unregisterProbe(PROBE_ID);
            syncBaseEffortVisibility(); // restore live patrol pixels per viewLayers.pixels
            if (typeof updateShareURL === 'function') updateShareURL();
        },
        isOpen() { return !!A; },
        toggle() { A ? this.close() : this.open(); },
        // Called by the time slider whenever the date window changes
        // (preset tap like td/90d, slider drag, or precise date edit).
        // Reopens the animation over the new window, keeping layer choices.
        onDateRangeChanged() {
            if (!A || A.recording) return;
            const fromISO = (typeof dateFrom !== 'undefined' && dateFrom) ? dateFrom : null;
            const toISO = (typeof dateTo !== 'undefined' && dateTo) ? dateTo : null;
            if (!fromISO || !toISO) return;
            if (fromISO === A.fromISO && toISO === A.toISO) return;
            clearTimeout(this._rangeTimer);
            this._rangeTimer = setTimeout(() => {
                if (!A) return;
                const layers = LAYER_ORDER.filter(n => A.on[n]);
                const paused = !A.playing;
                const aoi = A.aoiID || null;
                this.close();
                // speed intentionally recomputed for the new span
                this.open({ layers, paused, aoi });
            }, 350);
        },
        getState() {
            if (!A) return null;
            return {
                layers: LAYER_ORDER.filter(n => A.on[n]),
                speed: A.speed,
                tISO: fmtDate(A.t),
                playing: A.playing,
                aoi: A.aoiID || null,
                // Which download the open menu is pointing at, so the share
                // link reproduces what is on screen — an open menu IS on
                // screen, and it is the one piece of UI whose whole purpose is
                // the next click.
                exportItem: (function () {
                    const el = document.getElementById('anim-export-menu');
                    if (!el) return null;
                    const hi = el.querySelector('.aoi-menu-item.highlight');
                    return hi ? hi.dataset.item : 'open';
                })()
            };
        }
    };

    // inject CSS immediately so the open chip is styled from load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', injectCSS);
    } else injectCSS();

    // auto-open from share link (?anim=...) once the map is ready
    let pendingTries = 0;
    function tryPendingAnim() {
        const pending = window._pendingAnim;
        // restoreStateFromURL() sets _pendingAnim on map 'load' — keep polling,
        // since map load usually finishes well after our first check (~60s cap).
        if (!pending || typeof map === 'undefined' || !map || !map.loaded || !map.loaded()) {
            if (++pendingTries < 120) setTimeout(tryPendingAnim, 500);
            return;
        }
        window._pendingAnim = null;
        setTimeout(() => window.Animator.open(pending), 800);
    }
    setTimeout(tryPendingAnim, 1000);
})();
