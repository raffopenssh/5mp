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
    /* A disabled chip must still be HOVERABLE. pointer-events:none made it
       invisible to the cursor, so the one thing it has to do -- explain why it
       is off -- could not happen, and it read as broken rather than as
       refused. Clicks are refused in toggleChip() instead, where the reason is
       known and can be said out loud. */
    .anim-chip.unavailable { opacity: .38; cursor: not-allowed; }
    .anim-chip.unavailable:hover { border-color: rgba(255,255,255,0.15); color: #999; }
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
    // A truncated answer is a SAMPLE of the box it was asked for, so it is
    // also a standing invitation to re-ask once the view shrinks (onMoveEnd
    // reason 3). Record it per layer; the toast is the same as before.
    function truncNote(label, name, shown, total) {
        if (A) { A.trunc = A.trunc || {}; A.trunc[name] = true; }
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
                // The feasibility probe (refreshFirePtsFeasibility) may
                // already know the answer is no — in which case do not spend a
                // request proving it again. Unknown still asks: a missing
                // probe must never cost the user the better rendering.
                if (wantPoints(A.fetchBbox, res) && !(A.ptsFeas && A.ptsFeas.ok === false)) {
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
                if (j.truncated) truncNote('fire paths', 'trajs', j.count, j.total);
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
                // The row id rides along so a dot stays a FEATURE: a paused
                // frame's hover asks /api/feature-detail for the same narrative
                // a pinned layer would show, through the same cache
                // (LODLayer.loadDetail). Without it the animation is a picture.
                const dIds = j.ids || [];
                D.deforest = (j.points || []).map((p, i) => ({
                    lon: p[0], lat: p[1],
                    t: p[2] >= 0 ? base + p[2] * DAY : NaN,
                    area: p[3] || 0.1,
                    rid: dIds[i]
                })).filter(d => d.lon != null && !isNaN(d.t)).sort((a, b) => a.t - b.t);
                if (j.truncated) truncNote('deforestation', 'deforest', j.count, j.total);
                break;
            }
            case 'settlements': {
                const j = await fetchJSON(`/api/features-in-bbox?type=settlement&mode=points&bbox=${bb}&limit=${FEATURE_POINT_LIMIT}&pwd=${pwd}${aoiQ}`);
                const sIds = j.ids || [];
                D.settlements = (j.points || []).map((p, i) => ({ lon: p[0], lat: p[1], rid: sIds[i] }));
                if (j.truncated) truncNote('settlements', 'settlements', j.count, j.total);
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

    // The view transform, as a string. Everything that caches screen-space
    // work (projected coordinates, sprites, the hit index) keys on this one
    // value, so "the map moved" is stated once.
    function viewKey(w, h) {
        const c = map.getCenter();
        return [w, h, c.lng.toFixed(5), c.lat.toFixed(5), map.getZoom().toFixed(3),
                map.getBearing().toFixed(1), map.getPitch().toFixed(1)].join('|');
    }

    // ---------- screen-space point index -----------------------------------
    //
    // Both the sprites and the hover probe want the same thing: every point of
    // a layer in screen coordinates. The probe additionally wants "which of
    // them is under this pixel", and it is asked that on every mousemove.
    //
    // Naively that is one map.project() per point per mousemove — 12,000
    // settlements + 12,000 clearings + up to 60,000 detections, each a matrix
    // multiply, at pointer rate. So project once per view transform (shared
    // with the sprites, which no longer project anything themselves) and hang
    // a uniform 32 px bucket grid off it: a hover then touches ~9 cells.
    //
    // Off-screen points are indexed as bucket -1 and cost nothing.
    const IDX_CELL = 32;
    const _idx = new Map();     // layer name -> {key, src, xy, grid…}

    function screenIndex(name, arr, lonOf, latOf, proj, w, h) {
        const key = viewKey(w, h) + '|' + arr.length;
        let e = _idx.get(name);
        if (e && e.key === key && e.src === arr) return e;
        const n = arr.length;
        const xy = (e && e.xy && e.xy.length === n * 2) ? e.xy : new Float32Array(n * 2);
        const nx = Math.max(1, Math.ceil((w + 128) / IDX_CELL));
        const ny = Math.max(1, Math.ceil((h + 128) / IDX_CELL));
        const heads = (e && e.heads && e.heads.length === nx * ny) ? e.heads : new Int32Array(nx * ny);
        heads.fill(-1);
        const next = (e && e.next && e.next.length === n) ? e.next : new Int32Array(n);
        for (let i = 0; i < n; i++) {
            const o = arr[i];
            const p = proj(lonOf(o), latOf(o));
            xy[i * 2] = p.x; xy[i * 2 + 1] = p.y;
            const cx = Math.floor((p.x + 64) / IDX_CELL), cy = Math.floor((p.y + 64) / IDX_CELL);
            if (cx < 0 || cy < 0 || cx >= nx || cy >= ny) { next[i] = -1; continue; }
            const b = cy * nx + cx;
            next[i] = heads[b];
            heads[b] = i;
        }
        e = { key, src: arr, n, xy, nx, ny, heads, next };
        _idx.set(name, e);
        return e;
    }

    // Visit every point within `rad` px of (px, py). `fn(i, dist)`.
    function idxNear(e, px, py, rad, fn) {
        if (!e || !e.n) return;
        const c0 = Math.floor((px + 64 - rad) / IDX_CELL), c1 = Math.floor((px + 64 + rad) / IDX_CELL);
        const r0 = Math.floor((py + 64 - rad) / IDX_CELL), r1 = Math.floor((py + 64 + rad) / IDX_CELL);
        const xy = e.xy;
        for (let cy = Math.max(0, r0); cy <= Math.min(e.ny - 1, r1); cy++) {
            for (let cx = Math.max(0, c0); cx <= Math.min(e.nx - 1, c1); cx++) {
                for (let i = e.heads[cy * e.nx + cx]; i !== -1; i = e.next[i]) {
                    const dx = xy[i * 2] - px, dy = xy[i * 2 + 1] - py;
                    const d = Math.hypot(dx, dy);
                    if (d <= rad) fn(i, d);
                }
            }
        }
    }

    // Trajectory geometry is fixed; only the visible prefix moves with t.
    // Cache screen coords per group, keyed on the view transform.
    let _trajKey = '';
    let _trajIdx = null;    // vertex bucket grid, for the hover probe
    function projectTrajs(groups, proj, w, h) {
        const key = viewKey(w, h);
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
        _trajIdx = null;   // rebuilt lazily, and only if something hovers
    }

    // A bucket grid over every ON-SCREEN trajectory vertex. Built lazily (only
    // a hover needs it) and once per view transform: 6,000 groups x ~30 points
    // is ~180k distance tests per mousemove without it, which is exactly the
    // kind of per-pointer-event work that makes a paused map feel stuck.
    function trajIndex(groups, w, h) {
        if (_trajIdx && _trajIdx.key === _trajKey) return _trajIdx;
        const nx = Math.max(1, Math.ceil((w + 128) / IDX_CELL));
        const ny = Math.max(1, Math.ceil((h + 128) / IDX_CELL));
        const gi = [], pi = [], next = [];
        const heads = new Int32Array(nx * ny).fill(-1);
        for (let k = 0; k < groups.length; k++) {
            const g = groups[k];
            if (g._off || !g._scr) continue;
            for (let i = 0; i < g.pts.length; i++) {
                const x = g._scr[i * 2], y = g._scr[i * 2 + 1];
                const cx = Math.floor((x + 64) / IDX_CELL), cy = Math.floor((y + 64) / IDX_CELL);
                if (cx < 0 || cy < 0 || cx >= nx || cy >= ny) continue;
                const b = cy * nx + cx, id = gi.length;
                gi.push(k); pi.push(i); next.push(heads[b]); heads[b] = id;
            }
        }
        _trajIdx = { key: _trajKey, nx, ny, heads,
                     next: Int32Array.from(next), gi: Int32Array.from(gi), pi: Int32Array.from(pi) };
        return _trajIdx;
    }

    function invalidateSprites() { _defSprite = null; _settSprite = null; _trajKey = ''; _idx.clear(); }

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
        const key = [n, band, viewKey(w, h)].join('|');
        if (_defSprite && _defSprite.key === key) return _defSprite.canvas;
        const e = screenIndex('deforest', arr, d => d.lon, d => d.lat, proj, w, h);
        const cv = (_defSprite && _defSprite.canvas) || document.createElement('canvas');
        cv.width = w; cv.height = h;
        const g = cv.getContext('2d');
        g.clearRect(0, 0, w, h);
        // Same halo rule as the settlement sprite: the heat field is drawn
        // under these, and purple on amber is barely a colour difference.
        // Aged clearings need it most — they are the faintest thing here and
        // also the only permanent one.
        const shown = [];
        for (let i = 0; i < n; i++) {
            const x = e.xy[i * 2], y = e.xy[i * 2 + 1];
            if (x < -10 || y < -10 || x > w + 10 || y > h + 10) continue;
            const k = deforestAge(tq, arr[i].t);
            shown.push([x, y, k, deforestRadius(arr[i], zoom) * deforestAgeScale(k)]);
        }
        g.fillStyle = 'rgba(16,6,26,0.5)';
        for (const s of shown) { g.beginPath(); g.arc(s[0], s[1], s[3] + 1.4, 0, 6.283); g.fill(); }
        for (const s of shown) {
            g.fillStyle = deforestColor(s[2], 0.7 - 0.25 * s[2]);
            g.beginPath(); g.arc(s[0], s[1], s[3], 0, 6.283); g.fill();
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
        const key = [pts.length, viewKey(w, h)].join('|');
        if (_settSprite && _settSprite.key === key) return _settSprite.canvas;
        const e = screenIndex('settlements', pts, s => s.lon, s => s.lat, proj, w, h);
        const cv = (_settSprite && _settSprite.canvas) || document.createElement('canvas');
        cv.width = w; cv.height = h;
        const g = cv.getContext('2d');
        g.clearRect(0, 0, w, h);
        // Each dot carries its own dark halo. Settlement yellow (#fbbf24) is
        // the same hue as the top of the fire ramp, and the heat field is now
        // drawn UNDERNEATH these dots — so over a burning dry season a bare
        // yellow dot on amber is invisible exactly where a settlement matters
        // most. The halo is cheap here (this bitmap is built once per view,
        // not per frame) and it makes the layer independent of whatever is
        // behind it, dark basemap or white-hot fire alike.
        const dots = [];
        for (let i = 0; i < pts.length; i++) {
            const x = e.xy[i * 2], y = e.xy[i * 2 + 1];
            if (x < -10 || y < -10 || x > w + 10 || y > h + 10) continue;
            dots.push([x, y]);
        }
        g.fillStyle = 'rgba(20,12,4,0.55)';
        for (const p of dots) { g.beginPath(); g.arc(p[0], p[1], 3.0, 0, 6.283); g.fill(); }
        g.fillStyle = 'rgba(253,224,71,0.95)';
        for (const p of dots) { g.beginPath(); g.arc(p[0], p[1], 1.6, 0, 6.283); g.fill(); }
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
            // Same thermal ramp as the grid: a detection and the cell that
            // aggregates it must not be two different colours, or crossing the
            // points/grid threshold reads as a change in the data.
            const rgb = heatRGB(Math.min(1, Math.log2(1 + frp) / 7) * (0.5 + 0.5 * k));
            const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
            g.addColorStop(0, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${0.6 * k + 0.1})`);
            g.addColorStop(0.5, `rgba(239,68,68,${0.35 * k})`);
            g.addColorStop(1, 'rgba(239,68,68,0)');
            ctx.fillStyle = g;
            ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 6.283); ctx.fill();
        }
    }

    // A real heat ramp, rather than one channel swept between two numbers.
    //
    // The old ramp was `rgb(255, 90..200, 40)`: at low intensity that is a
    // dull yellow-green which, at 20% alpha over a near-black basemap, comes
    // out OLIVE. A field of olive squares beside blue lakes reads as land
    // cover, not as fire.
    //
    // The replacement (dark red → orange → amber → near-white) fixed the
    // colour and then failed the same way for a different reason: over a
    // continental dry season MOST cells land mid-ramp, and a whole country of
    // mid-ramp orange at 30% alpha is again a brown smear that reads as land
    // cover. A heat scale used as a FIELD has to spend most of its length in
    // the deep reds and keep orange and white rare enough to mean something —
    // which is what the scientific inferno/magma ramps do, and why they are
    // the convention for exactly this kind of surface.
    //
    // THE THIRD VERSION, AND WHY IT IS RED AGAIN. The inferno ramp below
    // started at a near-black PLUM and ran through a brick more brown than
    // red. Curved (cGamma) so the ordinary cell lands low on the ramp, a whole
    // AOI then sits in that plum-brick band — over a dark basemap exactly the
    // brown smear the ramp was chosen to avoid, and, worse, it stops reading
    // as FIRE. The layer's job is to be recognised at a glance, and this app
    // says fire in #ef4444 everywhere else: alerts, trajectories, detections.
    //
    // So the ramp is anchored on that same red at its MIDDLE — the ordinary
    // burning cell — with the low end a dark ember of the SAME hue rather than
    // a different colour, and orange/amber/white kept rare for the top decile.
    // Hue is near-constant across the bottom two thirds and only lightness
    // moves, so a quiet month reads as "a little fire", not as another layer.
    const HEAT = [
        [ 74,   8,  10],   // barely anything burnt here — dark ember red
        [110,  14,  16],
        [150,  24,  24],
        [190,  38,  38],
        [222,  54,  54],
        [239,  68,  68],   // ordinary savanna burning — the app's fire red
        [246,  92,  56],
        [251, 140,  50],
        [253, 186,  70],   // a serious front
        [255, 226, 140],
        [255, 250, 225]    // the hot core, rare on purpose
    ];
    // Patrol effort is the same kind of surface and is drawn by the same
    // field code, in the patrol green (#4ade80) it wears on the live map —
    // and with the same weighting, so most of the ramp is green and white is
    // reserved for the few cells that are patrolled far more than the rest.
    const EFFORT_HEAT = [
        [  8,  38,  24],
        [ 12,  56,  34],
        [ 20,  84,  50],
        [ 32, 122,  72],
        [ 50, 168,  96],
        [ 74, 222, 128],   // the live pixel colour
        [120, 234, 155],
        [160, 243, 185],
        [198, 250, 215],
        [228, 253, 238],
        [245, 255, 250]
    ];
    function rampRGB(ramp, v) {
        const x = Math.max(0, Math.min(0.999, v)) * (ramp.length - 1);
        const i = Math.floor(x), f = x - i;
        const a = ramp[i], b = ramp[i + 1] || a;
        return [Math.round(a[0] + (b[0] - a[0]) * f),
                Math.round(a[1] + (b[1] - a[1]) * f),
                Math.round(a[2] + (b[2] - a[2]) * f)];
    }
    function heatRGB(v) { return rampRGB(HEAT, v); }

    // A scratch canvas for the blurred-field rendering, kept across frames.
    // Allocating a viewport-sized canvas 60 times a second is the one way to
    // make this cost anything.
    let gridScratch = null;
    function scratchFor(w, h) {
        if (!gridScratch) gridScratch = document.createElement('canvas');
        if (gridScratch.width !== w || gridScratch.height !== h) {
            gridScratch.width = w; gridScratch.height = h;
        }
        return gridScratch;
    }

    // ---------- the fire heat field ----------
    //
    // WHY THIS IS NOT A PULSE ANY MORE.
    //
    // The old rendering drew every frame still inside a fade window straight
    // onto the map with `globalCompositeOperation = 'lighter'`. Two things
    // followed, and both are visible in a 2.5-year AOI animation:
    //
    //  1. ALPHA STACKED INSTEAD OF INTENSITY ADDING. Each bucket painted its
    //     own translucent layer, so three quiet months on top of each other
    //     came out brighter than one busy one, and over 485,000 km² of dry
    //     season the whole polygon washed to a flat pink sheet with the
    //     structure — where it is actually burning — buried in it.
    //  2. EVERY CELL STROBED ONCE PER BUCKET. A bucket is an impulse: the cell
    //     flashed on its bucket boundary and decayed, so ground that burns
    //     continuously all season flickered monthly. That is a picture of our
    //     bucketing, not of the fire — the same class of mistake as the
    //     checkerboard the blur was added to hide.
    //
    // So the field is computed as a NUMBER per cell and coloured once:
    //
    //     heat(cell, t) = Σ_bucket  n · ramp(t) · exp(−age / τ)
    //
    //   * `ramp` rises across the bucket's own duration instead of landing all
    //     at once. We do not know when inside a month a detection burnt, so
    //     spreading it across the month is the least-wrong statement — and it
    //     is what removes the strobe: a continuously-burning cell now holds
    //     steady instead of sawtoothing.
    //   * `exp(−age/τ)`, τ ≈ 1.2 buckets, is the cooling memory: a front
    //     leaves a trail that fades, so movement reads as movement.
    //   * decay reaches zero. Deliberately NO permanent burn scar: over a
    //     multi-year window every cell in a savanna AOI has burnt at some
    //     point, so a scar layer is a solid rectangle that says nothing. The
    //     persistent record of loss is the deforestation layer, in purple.
    //
    // And it is rasterised in CELL SPACE — one texel per 0.1° cell — then
    // upscaled by the GPU with bilinear smoothing. That is what makes it fast
    // *and* continuous: the cost is (cells in view) writes plus a handful of
    // drawImage calls, regardless of how many buckets are alive, instead of
    // one fillRect per cell per bucket plus a full-screen CSS blur.
    //
    // IT IS NOT ONLY THE FIRE GRID. Patrol effort is the same kind of thing —
    // an aggregated quantity per cell per bucket, arriving from the same
    // endpoint in the same wire format — and it used to be drawn as one
    // `fillRect` per cell per live bucket, i.e. the rendering this replaced,
    // with its checkerboard, its stacked alpha and its per-bucket strobe. So
    // the field is parameterised by a ramp and a cooling constant, and both
    // layers go through it. The animator is the tool for vast data; two
    // renderings of the same shape of data, one good and one not, is not an
    // acceptable answer.
    const HEAT_TAU_BUCKETS = 1.2;    // cooling time constant, in buckets
    const HEAT_WINDOW_TAUS = 3.0;    // how far back a bucket still contributes
    const HEAT_MAX_CELLS = 1200000;  // sanity ceiling on the texel buffer
    const HEAT_ALPHA_FLOOR = 0.30;   // a cell at the bottom of the ramp is still fire

    // A layer may fix its cooling in real time rather than in buckets: patrol
    // effort ages over EFFORT_FADE_DAYS whatever the bucket size, because that
    // horizon is a statement about patrolling, not about our binning.
    function heatTau(G, bMs) { return G.tauMs || HEAT_TAU_BUCKETS * bMs; }

    function heatIndex(G) {
        if (G._idx) return G._idx;
        let xi0 = Infinity, xi1 = -Infinity, yi0 = Infinity, yi1 = -Infinity;
        for (const f of G.frames) {
            for (const pt of f.pts) {
                if (pt[0] < xi0) xi0 = pt[0];
                if (pt[0] > xi1) xi1 = pt[0];
                if (pt[1] < yi0) yi0 = pt[1];
                if (pt[1] > yi1) yi1 = pt[1];
            }
        }
        if (!isFinite(xi0)) return (G._idx = { empty: true });
        const nx = xi1 - xi0 + 1, ny = yi1 - yi0 + 1;
        if (nx * ny > HEAT_MAX_CELLS) return (G._idx = { empty: true });
        G._idx = {
            xi0, yi0, xi1, yi1, nx, ny,
            acc: new Float32Array(nx * ny),
            canvas: null, img: null,
            ramp: G.ramp || HEAT,
            off: G.off || 0,
            alphaCap: G.alphaCap || 0.82,
            accKey: '',
            // frame start times, so the contributing window is a binary search
            // rather than a scan of every bucket in the animation.
            times: G.frames.map(f => f.t)
        };
        measureHeatScale(G, G._idx, null);
        return G._idx;
    }

    // THE SCALE IS MEASURED ON THE QUANTITY ACTUALLY DRAWN, AND ON THE PART OF
    // IT THAT IS ON SCREEN.
    //
    // A fixed "N detections = white" cannot work across this range: in a
    // Sahelian dry season the average 0.1° cell holds ~40 detections a month,
    // so a constant that makes one park's fires visible paints half a
    // continent white. A per-FRAME normalisation is worse still — the picture
    // would brighten as the season ends, i.e. the colour would stop meaning
    // anything over time. So the scale is measured once by running the real
    // accumulation at a handful of sample times and reading two quantiles off
    // the BUSIEST of them (the frame at risk of saturating), and it then holds
    // for the whole playback.
    //
    // TWO ANCHORS, NOT ONE. It used to be `hot` alone (the 98th percentile)
    // with a log from zero. In a January dry season the median cell holds ~40%
    // of the 98th percentile, and log2(1+0.4h)/log2(1+h) is ~0.9 for any large
    // h — so the ORDINARY cell landed at nine tenths of the ramp and the whole
    // AOI came out amber-white with the fronts invisible inside it. Exactly
    // the failure the ramp exists to prevent, arriving through the
    // normalisation instead of through the colours. `cool` (the 35th
    // percentile of the burning cells) is the bottom of the ramp: "an ordinary
    // cell in a busy month" has to read as deep red for anything above it to
    // read as more.
    //
    // AND IT IS RE-MEASURED WHEN THE VIEW CHANGES (`win`, a cell-space
    // rectangle). A scale measured over 485,000 km² is the wrong scale for one
    // district inside it: zoomed into a quiet corner every cell sits below
    // `cool` and the layer goes flat dark; zoomed into the hot corner every
    // cell is above `hot` and it goes flat white. Both read as "the layer
    // broke on zoom", and both are the same mistake as judging density from a
    // zoom number instead of from the data — the contrast that matters is the
    // contrast among the cells you can see. Recomputed on view change only
    // (a handful of passes over the grid), never per frame.
    function measureHeatScale(G, ix, win) {
        if (ix.empty) return;
        const nx = ix.nx, ny = ix.ny;
        const x0 = win ? Math.max(0, win.x0) : 0, x1 = win ? Math.min(nx - 1, win.x1) : nx - 1;
        const y0 = win ? Math.max(0, win.y0) : 0, y1 = win ? Math.min(ny - 1, win.y1) : ny - 1;
        if (x1 < x0 || y1 < y0) return;
        const cellsInWin = (x1 - x0 + 1) * (y1 - y0 + 1);
        let hot = 0, cool = 0, busiestVis = 0;
        const SAMPLES = 11;
        const ft0 = G.frames[0].t, ft1 = G.frames[G.frames.length - 1].t;
        const sBMs = G.frames.length > 1
            ? Math.max(DAY, (ft1 - ft0) / (G.frames.length - 1))
            : DAY;
        const step = Math.max(1, Math.floor(Math.sqrt(cellsInWin / 4000)));
        const sx = Math.ceil((x1 - x0 + 1) / step), sy = Math.ceil((y1 - y0 + 1) / step);
        const sampled = Math.max(1, sx * sy);
        const vals = [];
        for (let s = 0; s < SAMPLES; s++) {
            const st = ft0 + (ft1 - ft0) * (s + 0.5) / SAMPLES + sBMs;
            if (!accumulate(ix, G, st, sBMs)) continue;
            const acc = ix.acc;
            vals.length = 0;
            for (let y = y0; y <= y1; y += step) {
                const base = y * nx;
                for (let x = x0; x <= x1; x += step) {
                    const v = acc[base + x];
                    if (v > 0.02) vals.push(v);
                }
            }
            if (!vals.length) continue;
            vals.sort((a, b) => a - b);
            const q = p => vals[Math.min(vals.length - 1, Math.floor(p * vals.length))];
            // Both anchors come from the SAME frame. Taking the top from one
            // and the bottom from another compares two distributions and can
            // invert them.
            const score = q(0.98);
            if (score > hot) {
                hot = score;
                cool = q(0.35);
                busiestVis = vals.length / sampled;
            }
        }
        ix.hot = Math.max(3, hot || 8);
        // Never let the anchors collapse: a uniform field would divide by ~0
        // and every cell would be white.
        ix.cool = Math.min(cool || 0, ix.hot * 0.6);

        // HOW HARD TO CURVE THE INK IS A QUESTION ABOUT COVERAGE, not about a
        // zoom level — the same rule as densityPaint() in lodlayer.js. A
        // continental dry season has fire in nearly every cell and needs a
        // hard curve so only the top decile reads; one park in the same season
        // has a few hot cells in an empty grid, where that curve under-inks
        // the very thing the user zoomed in to see. So measure it: what
        // fraction of the VISIBLE cells carry fire in the busiest frame?
        const lerp = Math.max(0, Math.min(1, (busiestVis - 0.02) / 0.58));
        // 2% of cells -> bold; 60%+ -> hard. Linear between. The colour gamma
        // stays above 1 even in a sparse view: the ramp is deliberately
        // red-heavy (see HEAT) and the ordinary cell must land inside that red
        // half, not above it.
        ix.aGamma = 1.15 + 0.75 * lerp;
        ix.cGamma = 1.15 + 0.95 * lerp;
    }

    // The visible cell-space rectangle, or null when the whole grid is in
    // view. Padded a little so a scale is not re-measured for a one-pixel pan.
    function heatWindow(ix, G) {
        if (ix.empty) return null;
        const res = G.res, off = ix.off;
        const b = map.getBounds();
        const x0 = Math.floor((b.getWest() / res) - off) - ix.xi0 - 1;
        const x1 = Math.ceil((b.getEast() / res) - off) - ix.xi0 + 1;
        const yTop = ix.yi1 - Math.ceil((b.getNorth() / res) - off) - 1;
        const yBot = ix.yi1 - Math.floor((b.getSouth() / res) - off) + 1;
        if (x0 <= 0 && yTop <= 0 && x1 >= ix.nx - 1 && yBot >= ix.ny - 1) return null;
        return { x0, x1, y0: yTop, y1: yBot };
    }

    // heat(cell, t) = Σ_bucket n · ramp · exp(−age/τ), into ix.acc. Returns the
    // peak, or 0 when nothing is alive at t.
    //
    // Memoised on (t, bucket size): a PAUSED animation redraws on every map
    // `move` event — i.e. once per frame of a pinch-zoom — and recomputing an
    // identical field while dragging is the difference between a fluid zoom
    // and a sticky one.
    function accumulate(ix, G, t, bMs) {
        const frames = G.frames;
        const key = t + '|' + bMs;
        if (ix.accKey === key) return ix.accPeak;
        const tau = heatTau(G, bMs);
        const acc = ix.acc;
        acc.fill(0);
        const lo = lowerBoundNum(ix.times, t - HEAT_WINDOW_TAUS * tau - bMs);
        let peak = 0;
        for (let fi = lo; fi < frames.length; fi++) {
            const f = frames[fi];
            if (f.t > t) break;
            // ramp-in across the bucket's own duration, then exponential
            // cooling: we do not know WHEN inside a month a detection burnt,
            // so spreading it over the month is the least-wrong statement —
            // and it is what removes the strobe.
            const since = t - f.t;
            const ramp = Math.min(1, since / bMs);
            const age = Math.max(0, since - bMs);
            const wgt = ramp * Math.exp(-age / tau);
            if (wgt < 0.004) continue;
            for (const pt of f.pts) {
                const i = (ix.yi1 - pt[1]) * ix.nx + (pt[0] - ix.xi0);
                if (i < 0 || i >= acc.length) continue;
                const v = acc[i] + pt[2] * wgt;
                acc[i] = v;
                if (v > peak) peak = v;
            }
        }
        ix.accKey = key;
        ix.accPeak = peak;
        return peak;
    }

    // first index with times[i] >= tt
    function lowerBoundNum(times, tt) {
        let lo = 0, hi = times.length;
        while (lo < hi) { const m = (lo + hi) >> 1; if (times[m] < tt) lo = m + 1; else hi = m; }
        return lo;
    }

    // Paint the heat numbers into the cell-space texel buffer. Returns the
    // buffer's canvas, or null when the frame is empty.
    function heatBuffer(G, t, bMs) {
        const ix = heatIndex(G);
        if (ix.empty) return null;
        const acc = ix.acc;
        // Re-measure the scale for what is on screen when the view has
        // meaningfully changed (see measureHeatScale). Keyed coarsely on the
        // visible cell rectangle, so panning within a view costs nothing and
        // a zoom costs a few passes — not a measurement per frame.
        const win = heatWindow(ix, G);
        const wKey = win ? [win.x0 >> 1, win.x1 >> 1, win.y0 >> 1, win.y1 >> 1].join(',') : 'all';
        if (ix.winKey !== wKey) {
            ix.winKey = wKey;
            measureHeatScale(G, ix, win);
            ix.accKey = '';   // measurement left `acc` at a sample time
        }
        if (!accumulate(ix, G, t, bMs)) return null;

        // THE BUFFER'S ROWS ARE MERCATOR, NOT LATITUDE.
        //
        // Cell rows are equally spaced in *latitude*; screen rows are equally
        // spaced in *mercator y*. Blitting one as the other puts the field
        // visibly north of its fires over a continental span, and stitching it
        // back in horizontal bands (the first attempt) drew every band edge
        // twice through a translucent alpha, i.e. bright horizontal seams
        // across the map. Resampling here instead makes the draw ONE exact
        // `drawImage` again, with the GPU's bilinear filter doing the
        // smoothing it is for.
        // x3 rows: enough that the mercator row lookup never skips a cell row
        // (the worst-case latitude stretch across an area this size is well
        // under 2), and small enough that the whole buffer stays a few tens of
        // thousands of texels — this loop runs every frame. Sub-cell smoothing
        // is the GPU's job on the way out, not this buffer's.
        const my = Math.min(1024, Math.max(ix.ny, ix.ny * 3));
        if (!ix.canvas || ix.canvas.height !== my) {
            ix.canvas = ix.canvas || document.createElement('canvas');
            ix.canvas.width = ix.nx; ix.canvas.height = my;
            ix.ctx = ix.canvas.getContext('2d');
            ix.img = ix.ctx.createImageData(ix.nx, my);
            ix.my = my;
            // Row lookup is fixed for a given grid: cache it.
            ix.rowOf = new Int32Array(my);
            const latN = (ix.yi1 + ix.off + 0.5) * G.res, latS = (ix.yi0 + ix.off - 0.5) * G.res;
            const mY = lat => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
            const yN = mY(latN), yS = mY(latS);
            for (let j = 0; j < my; j++) {
                const ym = yN + (yS - yN) * (j + 0.5) / my;
                const lat = (Math.atan(Math.exp(ym)) - Math.PI / 4) * 360 / Math.PI;
                let r = ix.yi1 - Math.round(lat / G.res - ix.off);
                if (r < 0) r = 0; else if (r >= ix.ny) r = ix.ny - 1;
                ix.rowOf[j] = r;
            }
        }
        const data = ix.img.data;
        // One scale for the whole animation, from the data's own distribution
        // (see heatIndex). log2 so a single detection stays visible beside a
        // 500-detection front.
        // One scale for the whole animation, from the data's own distribution
        // (see heatIndex), and BETWEEN two measured anchors rather than from
        // zero: `cool` (an ordinary burning cell in the busiest month) is the
        // bottom of the ramp, `hot` (its 98th percentile) the top. log2 in
        // between, so a single detection still separates from a 500-detection
        // front. Normalising from zero instead put the median cell at 0.9 of
        // the ramp and painted the whole AOI amber.
        const lo = Math.log2(1 + ix.cool);
        const denom = Math.max(0.35, Math.log2(1 + ix.hot) - lo);
        for (let j = 0, o = 0; j < my; j++) {
            const base = ix.rowOf[j] * ix.nx;
            for (let i = 0; i < ix.nx; i++, o += 4) {
                const v = acc[base + i];
                if (v <= 0.02) { data[o + 3] = 0; continue; }
                const raw = Math.max(0, Math.min(1, (Math.log2(1 + v) - lo) / denom));
                // COLOUR AND ALPHA GAMMA, on top of the log, and both keyed on
                // how much of the view burns (see heatIndex). Curving keeps
                // the ordinary cell in the deep reds and orange/white for the
                // cells that earn it — which is also what keeps the yellow
                // settlement dots and purple clearings legible ON TOP of this.
                // Over a sparse park the curve flattens towards linear,
                // because there the hot cells are the point and nothing is
                // crowding them out.
                const inten = Math.pow(raw, ix.cGamma);
                const rgb = rampRGB(ix.ramp, inten);
                data[o] = rgb[0]; data[o + 1] = rgb[1]; data[o + 2] = rgb[2];
                // ALPHA HAS A FLOOR, because the ramp now starts at an
                // ORDINARY cell rather than at zero: below `cool` raw is 0,
                // and without the floor half of a burning dry season would be
                // fully transparent — "a bit of fire" would render as "no
                // fire". The floor is what makes the bottom of the ramp a
                // dark ember rather than nothing.
                const a = HEAT_ALPHA_FLOOR + (1 - HEAT_ALPHA_FLOOR) * Math.pow(raw, ix.aGamma);
                data[o + 3] = Math.round(255 * ix.alphaCap * a);
            }
        }
        ix.ctx.putImageData(ix.img, 0, 0);
        return ix.canvas;
    }

    /**
     * The aggregated fire grid, drawn as a continuous heat FIELD.
     *
     * `heatBuffer` has already reduced every live bucket to one number per
     * 0.1° cell and coloured it, in cell space. All that is left is to put
     * that little image on the map — one `drawImage`, bilinearly upscaled by
     * the GPU, instead of a fillRect per cell per bucket plus a full-screen
     * CSS blur. For the reference view (XSA, 2.5 years, 32 monthly buckets,
     * 127k cell-buckets) that is ~4k texel writes and one blit per frame.
     *
     * Three details that are not decoration:
     *
     *  * WEB MERCATOR IS NOT LINEAR IN LATITUDE. The buffer is resampled onto
     *    mercator rows in `heatBuffer`, so the blit is exact and stays a
     *    single `drawImage`. Stitching bands instead (the first attempt) drew
     *    each band edge twice through a translucent alpha and left bright
     *    horizontal seams across the map.
     *  * UNDER ROTATION OR PITCH an axis-aligned blit is simply wrong, so that
     *    case falls back to projecting each cell. Same numbers, same colours,
     *    just slower — a wrong picture is not an acceptable fast path.
     *  * SMOOTHING IS THE POINT. The 0.1° grid is our binning, not the fire's
     *    edge; bilinear upscaling plus a light blur (a quarter of a cell)
     *    turns each intensity step into a gradient. Missing `ctx.filter`
     *    support degrades to hard-edged cells, never to a missing layer.
     */
    function drawHeatField(ctx, G, t, proj, w, h, bMs) {
        const res = G.res;
        const ix = heatIndex(G);
        if (ix.empty) return;
        const buf = heatBuffer(G, t, bMs);
        if (!buf) return;
        const prevOp = ctx.globalCompositeOperation;
        ctx.globalCompositeOperation = 'source-over';

        const c0 = proj(0, 0), c1 = proj(res, 0);
        const cellPx = Math.abs(c1.x - c0.x);

        const bearing = (map.getBearing && map.getBearing()) || 0;
        const pitch = (map.getPitch && map.getPitch()) || 0;
        if (Math.abs(bearing) > 0.5 || pitch > 0.5) {
            drawHeatCells(ctx, ix, res, proj, w, h, cellPx);
            ctx.globalCompositeOperation = prevOp;
            return;
        }

        const lonW = (ix.xi0 + ix.off - 0.5) * res, lonE = (ix.xi1 + ix.off + 0.5) * res;
        const latN = (ix.yi1 + ix.off + 0.5) * res, latS = (ix.yi0 + ix.off - 0.5) * res;
        const pNW = proj(lonW, latN), pSE = proj(lonE, latS);
        const dx = pNW.x, dw = pSE.x - pNW.x;
        if (!(dw > 0)) { ctx.globalCompositeOperation = prevOp; return; }
        // Nothing on screen: skip the blit entirely (a panned-away AOI).
        if (pSE.x < -8 || dx > w + 8) { ctx.globalCompositeOperation = prevOp; return; }

        const prevSmooth = ctx.imageSmoothingEnabled;
        ctx.imageSmoothingEnabled = true;
        try { ctx.imageSmoothingQuality = 'high'; } catch (e) { /* not everywhere */ }
        const prevFilter = ctx.filter;
        if (cellPx > 3) {
            try { ctx.filter = 'blur(' + Math.min(10, cellPx * 0.26).toFixed(1) + 'px)'; }
            catch (e) { /* hard edges, still the right shape */ }
        }

        // One exact blit. The buffer's rows are already mercator-spaced
        // (heatBuffer), and screen y is linear in mercator y whenever the map
        // is neither rotated nor pitched -- which is the case guarded above.
        ctx.drawImage(buf, 0, 0, buf.width, buf.height, dx, pNW.y, dw, pSE.y - pNW.y);
        try { ctx.filter = prevFilter || 'none'; } catch (e) { /* ignore */ }
        ctx.imageSmoothingEnabled = prevSmooth;
        ctx.globalCompositeOperation = prevOp;
    }

    // Rotated/pitched fallback: the same accumulated heat, projected per cell.
    function drawHeatCells(ctx, ix, res, proj, w, h, cellPx) {
        const acc = ix.acc;
        const scratch = scratchFor(Math.max(1, Math.round(w)), Math.max(1, Math.round(h)));
        const g = scratch.getContext('2d');
        g.clearRect(0, 0, scratch.width, scratch.height);
        for (let i = 0; i < acc.length; i++) {
            const v = acc[i];
            if (v <= 0.02) continue;
            const cx = i % ix.nx, cy = (i / ix.nx) | 0;
            const lon = (ix.xi0 + cx + ix.off) * res, lat = (ix.yi1 - cy + ix.off) * res;
            const p = proj(lon, lat);
            if (p.x < -cellPx - 20 || p.y < -cellPx - 20 ||
                p.x > w + cellPx + 20 || p.y > h + cellPx + 20) continue;
            const inten = Math.min(1, Math.log2(1 + v) / Math.log2(1 + ix.hot));
            const rgb = rampRGB(ix.ramp, Math.pow(inten, ix.cGamma));
            const s = Math.max(1.5, cellPx * 1.2);
            g.fillStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${Math.min(ix.alphaCap, Math.pow(inten, ix.aGamma))})`;
            g.fillRect(p.x - s / 2, p.y - s / 2, s, s);
        }
        const prev = ctx.filter;
        try { ctx.filter = 'blur(' + Math.min(12, Math.max(1, cellPx * 0.3)).toFixed(1) + 'px)'; }
        catch (e) { /* ignore */ }
        ctx.drawImage(scratch, 0, 0, scratch.width, scratch.height, 0, 0, w, h);
        try { ctx.filter = prev || 'none'; } catch (e) { /* ignore */ }
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
            const bb = A.selBbox || A.fetchBbox;
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

        // --- fire heat field (backdrop) ---
        //
        // ORDER IS THE POINT. The heat field is a SURFACE — it covers ground
        // rather than marking a thing — so it belongs under the discrete
        // layers, exactly like the geology drape under the map tips. Drawn
        // above them (which it was) a busy dry season painted a sheet over
        // every settlement and clearing in the AOI, and the user's answer to
        // "where has it been cleared" became "somewhere under the pink".
        //
        // It also composites `source-over`, not `lighter`: the buckets were
        // already summed as NUMBERS in heatBuffer, so adding the finished
        // pixels a second time would double-count them and re-create the
        // washed-out sheet in a different way. Individual detections and
        // trajectories stay additive — they are sparse, and that is what makes
        // a cluster of them glow.
        if (on.fireGrid && D.fireGrid && !D.fireGrid.asPoints) {
            drawHeatField(ctx, D.fireGrid, t, proj, w, h, bMs);
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
                ctx.fillStyle = 'rgba(16,6,26,0.5)';   // halo, as in the sprite
                ctx.beginPath(); ctx.arc(p.x, p.y, r + 1.4, 0, 6.283); ctx.fill();
                ctx.fillStyle = deforestColor(0, 0.5 + flash * 0.5);
                ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 6.283); ctx.fill();
            }
        }

        // --- patrol grid: the same heat FIELD as the fires, in green ---
        //
        // This used to be a fillRect per cell per live bucket: the exact
        // rendering the fire grid was rewritten to escape, with the same three
        // faults (a visible lattice of our 0.1° binning, alpha stacking across
        // overlapping buckets, and a per-bucket strobe on ground that is
        // patrolled continuously). Patrol effort is the same kind of quantity
        // in the same wire format, so it is the same field — only the ramp
        // and the cooling horizon differ.
        //
        // tauMs is fixed in REAL time (a third of EFFORT_FADE_DAYS, so the
        // tail dies at roughly the 90 days the layer has always claimed),
        // rather than in buckets like the fire grid: "a patrol goes cold after
        // three months" is a statement about patrolling and must not change
        // when the window crosses a day/week/month bucketing threshold.
        if (on.effortGrid && D.effortGrid) {
            const E = D.effortGrid;
            if (!E.ramp) {
                E.ramp = EFFORT_HEAT;
                E.tauMs = EFFORT_FADE_DAYS / 3 * DAY;
                E.alphaCap = 0.78;
            }
            drawHeatField(ctx, E, t, proj, w, h, bMs);
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

        // --- fire detections (additive) ---
        //
        // `fireGrid` swaps itself for real detections when the view is zoomed
        // in far enough (`asPoints`, decided by the server from the true count
        // in view) — one rendering shared with the `firePts` chip, so the two
        // cannot drift into disagreeing about what a fire looks like. The
        // aggregated FIELD form of the same layer was drawn earlier, as a
        // backdrop; see there for why.
        if (on.fireGrid && D.fireGrid && D.fireGrid.asPoints) {
            drawFirePoints(ctx, D.fireGrid.points, t, proj, w, h, zoom);
        }
        if (on.firePts && D.firePts) drawFirePoints(ctx, D.firePts, t, proj, w, h, zoom);
        ctx.globalCompositeOperation = 'source-over';

        // --- fire trajectories: build up one-by-one, then ashen out (red → grey → gone) ---
        if (on.trajs && D.trajs) {
            // Project each group's points once per view transform, not once per
            // frame: at 800 groups x ~30 points that was ~24k projections at
            // 60 fps, and the geometry does not depend on t — only how much of
            // it is drawn does. Also skip groups whose extent is off screen.
            projectTrajs(D.trajs, proj, w, h);
            // DENSITY-AWARE INK, KEYED ON WHAT IS ON SCREEN *AT t*.
            //
            // The map draws every feature in the view at once, so its ramp
            // keys on the loaded count. An ANIMATION does not: a group is
            // drawn only between its own t0 and its ash-out, so a window
            // holding 6,000 trajectories typically shows a few hundred in
            // parallel. Keying the ramp on D.trajs.length therefore thinned
            // and de-opacified a frame that was nearly empty — the original
            // thick, opaque strokes were simply better, and that is why.
            //
            // So count the live ones (cheap: one pass over already-projected
            // groups, and off-screen ones are pre-flagged) and only start
            // thinning when a FRAME really is a red sheet. Same rule as
            // densityPaint() in lodlayer.js, applied to the right number.
            let nLive = 0;
            for (const g of D.trajs) {
                if (g._off || g.t0 > t) continue;
                if (t > g.t1 && (t - g.t1) >= TRAJ_FADE_DAYS * DAY) continue;
                nLive++;
            }
            const inkW = nLive > 4000 ? 1.0 : nLive > 2000 ? 1.4 : nLive > 800 ? 1.9 : 2.5;
            const inkA = nLive > 4000 ? 0.45 : nLive > 2000 ? 0.6 : nLive > 800 ? 0.8 : 0.95;
            const headR = nLive > 2000 ? 4 : 7;
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

    // A dot the animator drew and a dot a pinned layer drew are the SAME ROW.
    // So the tip must be the same tip — narrative, classification, area and
    // all — and it must be reached the same way: /api/feature-detail through
    // LODLayer's cache, so hovering the animation and hovering the map never
    // disagree and never ask twice. Until it lands the tip says what it knows
    // (kind, date, size) and marks the rest as loading; MapTip.refresh()
    // re-renders in place when it does.
    function areaTipRender(kind, tipType, extra, probeKey) {
        // Returned to MapTip as `render`, so it is re-run on refresh.
        return function (props) {
            props = props || {};
            const L = window.LODLayer;
            const full = (L && props.rid != null) ? L.detailFor(props.rid) : null;
            if (full && L && L.tipFor) {
                return L.tipFor(full, tipType, full.park_name || full.park_id || '');
            }
            if (props.rid != null && L && L.loadDetail) L.loadDetail(props.rid, PROBE_ID + ':' + probeKey);
            return '<div class="maptip-label">' + kind + '</div>' +
                   extra.filter(Boolean).map(l => '<div class="maptip-meta">' + l + '</div>').join('') +
                   (props.rid != null ? '<div class="maptip-dim">loading details…</div>' : '');
        };
    }

    // Where the card's button goes. The area is only known once the detail has
    // landed, so both are functions of the feature and both degrade quietly.
    function areaOfFeature(f) {
        const p = (f && f.properties) || {};
        const L = window.LODLayer;
        const full = (L && p.rid != null) ? L.detailFor(p.rid) : null;
        return (full && full.park_id) || A.aoiID || null;
    }
    function areaAction(tipType) {
        return {
            actionLabel: function (f) {
                const a = areaOfFeature(f);
                return (a && typeof areaOverviewLabel === 'function')
                    ? areaOverviewLabel(a) : 'Open overview';
            },
            onActivate: function (f) {
                const a = areaOfFeature(f);
                const p = (f && f.properties) || {};
                const L = window.LODLayer;
                const full = (L && p.rid != null) ? L.detailFor(p.rid) : null;
                if (a && typeof openAreaOverview === 'function') {
                    openAreaOverview(a, tipType, (full && full.feature_id) || null);
                }
            }
        };
    }

    function probeFrame(e) {
        if (!A || A.playing) return null;
        const pt = e && e.point;
        if (!pt) return null;
        const t = A.t, D = A.data, on = A.on;
        const w = A.canvas.clientWidth, h = A.canvas.clientHeight;
        const proj = (lon, lat) => map.project([lon, lat]);
        // ONE BEST ANSWER PER KIND, not one overall. Three layers drawn in the
        // same place are three different questions; collapsing them to the
        // nearest dot made a settlement under a fire path unreachable, which
        // is exactly the "select behind" failure the card's tabs exist to fix
        // for registered layers. MapTip turns the list into tabs.
        const best = new Map();     // kind -> {dist, make}
        function considerIn(kind, dist, make) {
            if (dist > PROBE_PX) return;
            const b = best.get(kind);
            if (b && b.dist <= dist) return;
            best.set(kind, { dist, make });
        }

        // Trajectories: nearest vertex of the part that has happened by t.
        // The vertices are already projected (projectTrajs) and bucketed
        // (trajIndex), so a hover touches ~9 cells rather than every group.
        if (on.trajs && D.trajs) {
            projectTrajs(D.trajs, proj, w, h);
            const ti = trajIndex(D.trajs, w, h);
            const c0 = Math.floor((pt.x + 64 - PROBE_PX) / IDX_CELL), c1 = Math.floor((pt.x + 64 + PROBE_PX) / IDX_CELL);
            const r0 = Math.floor((pt.y + 64 - PROBE_PX) / IDX_CELL), r1 = Math.floor((pt.y + 64 + PROBE_PX) / IDX_CELL);
            for (let cy = Math.max(0, r0); cy <= Math.min(ti.ny - 1, r1); cy++) {
                for (let cx = Math.max(0, c0); cx <= Math.min(ti.nx - 1, c1); cx++) {
                    for (let id = ti.heads[cy * ti.nx + cx]; id !== -1; id = ti.next[id]) {
                        const g = D.trajs[ti.gi[id]], i = ti.pi[id];
                        if (g.t0 > t || g.pts[i][2] > t) continue;
                        if (t > g.t1 + TRAJ_FADE_DAYS * DAY) continue;   // ashed away
                        const dx = g._scr[i * 2] - pt.x, dy = g._scr[i * 2 + 1] - pt.y;
                        considerIn('trajs', Math.hypot(dx, dy), () => ({
                            key: 'trajs',
                            html: trajTip(g, t).html,
                            tabLabel: 'Fire path',
                            // The group is a real feature with a real id, so
                            // the card's button opens its area's overview
                            // scrolled to it — exactly like a pinned fire.
                            actionLabel: (g.park && typeof areaOverviewLabel === 'function')
                                ? areaOverviewLabel(g.park) : null,
                            onActivate: g.park && typeof openAreaOverview === 'function'
                                ? () => openAreaOverview(g.park, 'fire', g.id) : null
                        }));
                    }
                }
            }
        }
        // Deforestation and settlements are the SAME ROWS a pinned layer would
        // serve, so they get the same tip and the same "open the area" button
        // — via the row id the points endpoint already ships (`ids`).
        if (on.deforest && D.deforest && D.deforest.length) {
            const ix = screenIndex('deforest', D.deforest, d => d.lon, d => d.lat, proj, w, h);
            idxNear(ix, pt.x, pt.y, PROBE_PX, (i, dist) => {
                const d = D.deforest[i];
                if (d.t > t) return;
                considerIn('deforest', dist, () => ({
                    key: 'deforest',
                    properties: { rid: d.rid },
                    tabLabel: 'Deforestation',
                    render: areaTipRender('Deforestation · ' + fmtDateHuman(d.t), 'deforestation',
                                          [d.area ? d.area.toFixed(2) + ' km² cleared' : null], 'deforest'),
                    ...areaAction('deforestation')
                }));
            });
        }
        if (on.settlements && D.settlements && D.settlements.length) {
            const ix = screenIndex('settlements', D.settlements, s => s.lon, s => s.lat, proj, w, h);
            idxNear(ix, pt.x, pt.y, PROBE_PX, (i, dist) => {
                const s = D.settlements[i];
                considerIn('settlements', dist, () => ({
                    key: 'settlements',
                    properties: { rid: s.rid },
                    tabLabel: 'Settlement',
                    render: areaTipRender('Settlement', 'settlement',
                                          [s.lat.toFixed(4) + ', ' + s.lon.toFixed(4)], 'settlements'),
                    ...areaAction('settlement')
                }));
            });
        }
        const firePts = (on.firePts && D.firePts) ||
                        (on.fireGrid && D.fireGrid && D.fireGrid.asPoints && D.fireGrid.points);
        if (firePts && firePts.length) {
            const ix = screenIndex('firePts', firePts, q => q[0], q => q[1], proj, w, h);
            idxNear(ix, pt.x, pt.y, PROBE_PX, (i, dist) => {
                const q = firePts[i];
                if (q[2] > t || t - q[2] > DAY * 3) return;    // outside the frame
                considerIn('firePts', dist, () => ({
                    key: 'firePts',
                    label: 'Fire detection',
                    lines: [fmtDateHuman(q[2]),
                            q[3] ? 'Intensity ' + Math.round(q[3]) + ' MW' : null]
                }));
            });
        }
        // The heat field, last and only when nothing discrete answered. It is
        // a SURFACE: every pixel of it is a hit, so letting it compete on
        // distance would give it 0 and it would win over the trajectory the
        // user is pointing at. Same rule as the geology drape's priority -30.
        if (!best.size && on.fireGrid && D.fireGrid && !D.fireGrid.asPoints && D.fireGrid._idx) {
            const ix = D.fireGrid._idx, res = D.fireGrid.res;
            if (!ix.empty && map.unproject) {
                const ll = map.unproject([pt.x, pt.y]);
                const cx = Math.round(ll.lng / res) - ix.xi0;
                const cy = ix.yi1 - Math.round(ll.lat / res);
                if (cx >= 0 && cy >= 0 && cx < ix.nx && cy < ix.ny) {
                    const v = ix.acc[cy * ix.nx + cx];
                    if (v > 0.02) {
                        const stepLbl = D.fireGrid.step === 'day' ? 'day'
                                      : D.fireGrid.step === 'week' ? 'week' : 'month';
                        best.set('fireGrid', { dist: PROBE_PX, make: () => ({
                            key: 'fireGrid',
                            label: 'Fire activity',
                            peers: false,
                            // "≈" because this is the decayed sum the pixel is
                            // drawn from, not a count of anything: it mixes the
                            // current bucket with what is still cooling from
                            // the previous ones. An exact-looking number here
                            // would be precision the picture does not have.
                            lines: ['≈' + fmtCount(Math.round(v)) + ' detection'
                                        + (Math.round(v) === 1 ? '' : 's') + ' burning here',
                                    'around ' + fmtDateHuman(t) + ' (' + stepLbl + ' buckets)',
                                    Math.round(res * 111) + ' km cell']
                        }) });
                    }
                }
            }
        }
        if (!best.size) return null;
        // Nearest first, so the winner is still the thing the cursor is on and
        // the rest are tabs behind it.
        const hits = Array.from(best.values()).sort((a, b) => a.dist - b.dist).map(b => {
            const v = b.make();
            // A hit may be handed back as-is (it already carries `render`,
            // `properties` and an action), or as the simple {label, lines}
            // shape for the answers with nothing behind them to open.
            if (v.render || v.html) return v;
            return {
                key: v.key,
                html: '<div class="maptip-label">' + v.label + ' · ' + fmtDateHuman(t) + '</div>' +
                      v.lines.filter(Boolean).map(l => '<div class="maptip-meta">' + l + '</div>').join(''),
                tabLabel: v.label, peers: v.peers
            };
        });
        return hits;
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
    // ---------- can we draw individual detections here? ----------
    //
    // The `fire points` chip can only ever be honoured when the number of
    // detections in the window is under the server's ceiling. Offering it
    // regardless meant a user clicked it, waited for a request, and was told
    // "1.4M detections in view — showing the density grid instead": an offer
    // the app already knew was refused, and a spinner spent proving it.
    //
    // `mode=estimate` is ~10 ms over fire_grid_day, so the answer is fetched
    // WITH the layers and refreshed whenever the view or the window changes.
    // The chip is then disabled *with the number in its hover hint* — a
    // refusal that says how much too much it is, which is the difference
    // between "broken" and "zoom in and it will work".
    //
    // Unknown is never a refusal: if the probe fails, the chip stays live and
    // the old ask-then-fall-back path answers.
    async function refreshFirePtsFeasibility() {
        if (!A) return;
        const bb = A.fetchBbox.map(v => v.toFixed(4)).join(',');
        const key = bb + '|' + A.fromISO + '|' + A.toISO;
        if (A.ptsFeas && A.ptsFeas.key === key) return;
        const my = (A._feasSeq = (A._feasSeq || 0) + 1);
        try {
            const j = await fetchJSON(`/api/fire-frames?mode=estimate&bbox=${bb}&from=${A.fromISO}&to=${A.toISO}`
                + `&pwd=${encodeURIComponent(getPwdSafe())}`);
            if (!A || my !== A._feasSeq) return;
            A.ptsFeas = { key, ok: !!j.points_ok, estimate: j.estimate || 0, max: j.max || 0 };
        } catch (e) {
            if (!A || my !== A._feasSeq) return;
            A.ptsFeas = { key, ok: true, unknown: true };   // never refuse on a failed probe
        }
        updateChips();
    }

    function firePtsRefusal() {
        const f = A && A.ptsFeas;
        if (!f || f.ok) return null;
        if (!f.estimate) return 'No fire detections in this view and window.';
        return fmtCount(f.estimate) + ' detections in this view — too many to draw one by one '
             + '(limit ' + fmtCount(f.max) + '). Zoom in, or shorten the window; '
             + 'the fire grid shows all of them as a heat field.';
    }

    function updateChips() {
        if (!A) return;
        const refusal = firePtsRefusal();
        document.querySelectorAll('.anim-chip').forEach(chip => {
            const name = chip.dataset.layer;
            chip.classList.toggle('on', !!A.on[name]);
            const dot = chip.querySelector('i');
            if (dot) dot.style.background = A.on[name] ? LAYERS[name].color : '#555';
            if (A.on[name]) chip.style.color = '';
            if (name === 'firePts') {
                chip.classList.toggle('unavailable', !!refusal && !A.on[name]);
                chip.title = refusal || LAYERS.firePts.title;
            }
        });
        announceLayers();
    }

    // The stats panel's layer rows are the map's legend, and while the
    // animator is open the animation IS what those rows describe (see
    // ANIM_ROW_CHIPS in globe.html). One announcement, so the legend can never
    // disagree with the chips about what is on screen — the same shape as the
    // `lod:state` event a pinned layer emits for a mirrored row.
    function announceLayers() {
        try {
            window.dispatchEvent(new CustomEvent('anim:layers', {
                detail: {
                    open: !!A,
                    layers: A ? LAYER_ORDER.filter(n => A.on[n]) : [],
                    unavailable: A ? LAYER_ORDER.filter(n => chipUnavailable(n)) : []
                }
            }));
        } catch (e) {}
    }
    function chipUnavailable(name) {
        const chip = document.querySelector(`.anim-chip[data-layer="${name}"]`);
        return !!(chip && chip.classList.contains('unavailable'));
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

    async function toggleChip(name, want) {
        if (!A) return;
        if (want !== undefined && !!A.on[name] === !!want) return;
        // A refused layer answers the click with its reason instead of doing
        // nothing: the chip is dimmed, so a click on it is a question.
        if (!A.on[name]) {
            const chip = document.querySelector(`.anim-chip[data-layer="${name}"]`);
            if (chip && chip.classList.contains('unavailable')) {
                toast(chip.title, 'info', { key: 'anim-chip-' + name });
                return;
            }
        }
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

    // Refetch when the viewport leaves the fetched bbox, or when the view now
    // deserves a different answer — playing or paused, fixed bbox or not.
    function onMoveEnd() {
        if (!A) return;
        // THREE reasons to refetch; originally only the first existed:
        //
        //  1. the viewport has left the padded box we fetched, so there is
        //     data on screen we simply do not have;
        //  2. the LEVEL OF DETAIL the view now deserves has changed — zooming
        //     from a continent to a district stays well inside the fetched
        //     box, but a 0.1° grid there is a lattice of squares where real
        //     detections would fit. Without this the layer froze at whatever
        //     detail the view had when the animator opened, which is exactly
        //     the "a cheap rendering quietly becomes a picture" failure this
        //     work exists to remove;
        //  3. THE VIEW HAS SHRUNK INTO A TRUNCATED ANSWER. Every point layer
        //     is a bounded sample SPREAD over the box it was asked for, so
        //     zooming from a 485,000 km² AOI into one district keeps ~2% of
        //     that sample on screen: settlements and clearings thin out and
        //     then look absent, which reads as "the data ends here". The fix
        //     is not a bigger budget, it is asking again for the smaller box —
        //     the same 12,000 then buys ~50x the density. Only when the answer
        //     WAS truncated: an untruncated layer already holds every feature
        //     in view, so re-asking would return the same rows.
        //
        // A FIXED BBOX (an AOI, or a drawn selection) is NOT exempt from 2 and
        // 3. Fixed means the *clip* is fixed; the picture inside it still has
        // to answer the zoom. Returning early here is why zooming into an AOI
        // animation faded its layers out instead of resolving them.
        const vb = map.getBounds();
        const viewBox = [vb.getWest(), vb.getSouth(), vb.getEast(), vb.getNorth()];
        const lodChanged = A.on.fireGrid && A.data.fireGrid !== undefined &&
            wantPoints(viewBox, chooseRes(viewBox)) !== !!A.data.fireGrid.asPoints;
        // "Meaningfully smaller": a 2x linear zoom is a 4x density gain and is
        // worth a request; a nudge is not.
        const shrunk = bboxArea(A.fetchBbox) > bboxArea(viewBox) * 3.5;
        const resample = shrunk && LAYER_ORDER.some(n => A.on[n] && A.trunc && A.trunc[n]);
        const outside = !A.bboxFixed && !viewportInside(A.fetchBbox);
        if (!outside && !lodChanged && !resample) return;
        clearTimeout(A._refetchTimer);
        A._refetchTimer = setTimeout(() => {
            if (!A) return;
            // With a fixed selection the FETCH box follows the viewport,
            // clipped to the selection: the clip is unchanged, but the budget
            // is spent on what is on screen.
            A.fetchBbox = A.bboxFixed
                ? intersectBbox(A.selBbox || activeBbox().bbox, viewportFetchBbox())
                : activeBbox().bbox;
            A.trunc = {};
            refreshFirePtsFeasibility();
            for (const name of LAYER_ORDER) {
                if (A.on[name] && A.data[name] !== undefined && name !== 'turb' && name !== 'infra') {
                    delete A.data[name];
                    ensureLayer(name);
                }
            }
        }, 350);
    }

    // The viewport padded 30% — activeBbox() returns the *selection* when one
    // is fixed, and a zoomed-in frame needs the view too.
    function viewportFetchBbox() {
        const b = map.getBounds();
        const w = b.getEast() - b.getWest(), h = b.getNorth() - b.getSouth();
        return [b.getWest() - w * 0.3, b.getSouth() - h * 0.3,
                b.getEast() + w * 0.3, b.getNorth() + h * 0.3];
    }
    // Intersection, but never empty: a viewport panned entirely off the
    // selection keeps the selection rather than asking for a zero-area box.
    function intersectBbox(sel, view) {
        const out = [Math.max(sel[0], view[0]), Math.max(sel[1], view[1]),
                     Math.min(sel[2], view[2]), Math.min(sel[3], view[3])];
        if (!(out[2] > out[0]) || !(out[3] > out[1])) return sel.slice();
        return out;
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
                // The SELECTION is what clips the picture and never moves;
                // fetchBbox follows the viewport inside it (onMoveEnd), so the
                // two must not be the same field. They were, which is why the
                // clip rectangle used to be redrawn from whatever had last
                // been fetched.
                selBbox: bbox.slice(),
                trunc: {},
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
            //
            // The feasibility probe runs FIRST and is awaited: it costs ~10 ms
            // (one SUM over fire_grid_day) and it decides two things the
            // loaders would otherwise get wrong — whether `fireGrid` should
            // even try for individual detections, and whether a share link
            // naming `firePts` is asking for something this view cannot have.
            // A link is not an override: it can carry a viewport its author
            // never had, so an impossible layer is dropped WITH its reason
            // rather than switched on to draw nothing.
            await refreshFirePtsFeasibility();
            if (A.on.firePts && A.ptsFeas && A.ptsFeas.ok === false) {
                A.on.firePts = false;
                initial = initial.filter(n => n !== 'firePts');
                updateChips();
                toast(firePtsRefusal(), 'info', { key: 'anim-chip-firePts' });
            }
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
            announceLayers();           // the legend must not keep offering animation modes
            if (typeof updateShareURL === 'function') updateShareURL();
        },
        isOpen() { return !!A; },
        /** Which animation renderings are on right now. */
        layers() { return A ? LAYER_ORDER.filter(n => A.on[n]) : []; },
        isLayerOn(name) { return !!(A && A.on[name]); },
        /** Why a rendering is refused here, or null. Used by the legend menu. */
        layerRefusal(name) {
            if (!A) return null;
            if (name === 'firePts') return firePtsRefusal();
            const chip = document.querySelector(`.anim-chip[data-layer="${name}"]`);
            if (chip && chip.classList.contains('unavailable')) return chip.title || 'Not available here';
            return null;
        },
        /**
         * The same switch the chip is, reachable from the stats-panel legend:
         * one layer, two places it can be reached, exactly like the LOD detail
         * control. `on` omitted toggles.
         */
        setLayer(name, on) { return toggleChip(name, on); },
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
