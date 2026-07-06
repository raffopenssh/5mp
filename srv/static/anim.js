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
//     deforestation    : accumulates, new events flash
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
    const POINTS_ZOOM = 6.5;       // default to real points at/above this zoom
    const POINTS_MAX_AREA = 40;    // deg², matches server cap

    let A = null; // active animator state

    const LAYERS = {
        fireGrid:    { label: 'fire grid',     color: '#ef4444', title: 'Aggregated fire heatmap (0.1° grid)' },
        firePts:     { label: 'fire points',   color: '#ff7043', title: 'Individual VIIRS detections (high zoom)' },
        trajs:       { label: 'fire paths',    color: '#fda668', title: 'Fire movement trajectories (build up, then ashen out)' },
        effortGrid:  { label: 'patrol grid',   color: '#4ade80', title: 'Aggregated patrol effort (0.1° grid pixels)' },
        effortPts:   { label: 'patrol circles', color: '#86efac', title: 'Patrol effort circles (like the live map) — age and ashen over 90d' },
        deforest:    { label: 'deforest',      color: '#a855f7', title: 'Deforestation events (accumulate)' },
        settlements: { label: 'settlements',   color: '#fbbf24', title: 'Settlements (static context)' },
        turb:        { label: 'turbidity',     color: '#eab308', title: 'Turbidity plume + mining sites' },
        infra:       { label: 'infra',         color: '#60a5fa', title: 'Pinned roads/rivers/places (static)' }
    };
    const LAYER_ORDER = ['fireGrid', 'firePts', 'trajs', 'effortGrid', 'effortPts', 'deforest', 'settlements', 'turb', 'infra'];

    function getPwdSafe() { return (typeof getPwd === 'function' ? getPwd() : '') || ''; }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type || 'info'); }
    function fmtDate(ms) { return new Date(ms).toISOString().slice(0, 10); }
    function fmtDateHuman(ms) {
        const d = new Date(ms);
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        return d.getUTCDate() + ' ' + months[d.getUTCMonth()] + ' ' + d.getUTCFullYear();
    }
    function parseD(s) { return Date.parse(s + 'T00:00:00Z'); }

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
        .time-slider-container.animating .time-slider-date-tags { display: none; }
        #anim-date-lbl { font-size: 10px; min-width: 68px; }
        #anim-inline > #anim-date-lbl.visible { min-width: 68px; }
        #anim-speed-lbl { min-width: 40px; font-size: 9px; }
        #anim-inline > #anim-speed-lbl.visible { min-width: 40px; }
        .anim-chip { font-size: 8px; }
        .anim-chip.visible { max-width: 100px; padding: 1px 5px 1px 4px; }
        #anim-chips { gap: 2px; }
        #anim-gif { display: none; }
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
    function chooseRes(bbox) {
        const w = Math.abs(bbox[2] - bbox[0]);
        if (w > 30) return 0.25;
        if (w > 10) return 0.1;
        return 0.1;
    }
    function bucketMs(step) { return step === 'day' ? DAY : step === 'week' ? 7 * DAY : 30 * DAY; }

    async function fetchJSON(url) {
        const r = await fetch(url);
        if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url.split('?')[0]);
        return r.json();
    }

    // ---------- per-layer lazy loaders ----------
    async function loadLayer(name) {
        const pwd = encodeURIComponent(getPwdSafe());
        const bb = A.fetchBbox.map(v => v.toFixed(4)).join(',');
        const fromISO = A.fromISO, toISO = A.toISO;
        const spanDays = (A.t1 - A.t0) / DAY;
        const step = chooseStep(spanDays);
        const res = chooseRes(A.fetchBbox);
        const D = A.data;
        switch (name) {
            case 'fireGrid': {
                const j = await fetchJSON(`/api/fire-frames?bbox=${bb}&from=${fromISO}&to=${toISO}&step=${step}&res=${res}&pwd=${pwd}`);
                D.fireGrid = { frames: (j.frames || []).map(f => ({ t: parseD(f.d), pts: f.p })), res: j.res || res, step: j.step || step };
                break;
            }
            case 'firePts': {
                if (bboxArea(A.fetchBbox) > POINTS_MAX_AREA) { D.firePts = null; throw new Error('zoom in for fire points'); }
                const j = await fetchJSON(`/api/fire-frames?mode=points&bbox=${bb}&from=${fromISO}&to=${toISO}&step=day&pwd=${pwd}`);
                if (j.mode !== 'points') { D.firePts = null; throw new Error('too many fires here — use fire grid'); }
                const f0 = parseD(j.from);
                D.firePts = (j.points || []).map(p => [p[0], p[1], f0 + p[2] * DAY, p[3]]);
                break;
            }
            case 'trajs': {
                const j = await fetchJSON(`/api/fire-anim-trajectories?bbox=${bb}&from=${fromISO}&to=${toISO}&limit=800&pwd=${pwd}`);
                D.trajs = (j.groups || []).map(g => {
                    const pts = g.pts.map(p => [p[0], p[1], parseD(p[2])]).sort((a, b) => a[2] - b[2]);
                    return { pts, t0: pts[0][2], t1: pts[pts.length - 1][2], type: g.type, kmd: g.kmd };
                }).filter(g => g.pts.length >= 2);
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
                const j = await fetchJSON(`/api/features-in-bbox?type=deforestation&bbox=${bb}&from=${fromISO}&to=${toISO}&limit=1500&pwd=${pwd}`);
                D.deforest = (j.features || []).map(f => {
                    const p = f.properties || {};
                    return { lon: p.lon, lat: p.lat, t: parseD(p.start_date || (p.year + '-06-15')), area: p.area_km2 || 0.1 };
                }).filter(d => d.lon != null && !isNaN(d.t)).sort((a, b) => a.t - b.t);
                break;
            }
            case 'settlements': {
                const j = await fetchJSON(`/api/features-in-bbox?type=settlement&bbox=${bb}&limit=1500&pwd=${pwd}`);
                D.settlements = (j.features || []).map(f => {
                    const p = f.properties || {};
                    return { lon: p.lon, lat: p.lat };
                }).filter(d => d.lon != null);
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

        // clip to fixed bbox selection if present
        let clipped = false;
        if (A.bboxFixed) {
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
        if (on.settlements && D.settlements) {
            ctx.fillStyle = 'rgba(251,191,36,0.45)';
            for (const s of D.settlements) {
                const p = proj(s.lon, s.lat);
                if (p.x < -10 || p.y < -10 || p.x > w + 10 || p.y > h + 10) continue;
                ctx.beginPath(); ctx.arc(p.x, p.y, 1.6, 0, 6.283); ctx.fill();
            }
        }

        // --- deforestation (accumulate + flash) ---
        if (on.deforest && D.deforest) {
            for (const d of D.deforest) {
                if (d.t > t) break;
                const p = proj(d.lon, d.lat);
                if (p.x < -10 || p.y < -10 || p.x > w + 10 || p.y > h + 10) continue;
                const age = (t - d.t) / DAY;
                const flash = Math.max(0, 1 - age / DEFOREST_FLASH_DAYS);
                const r = Math.max(1.5, Math.min(8, Math.sqrt(d.area) * (zoom / 3))) * (1 + flash * 0.8);
                ctx.fillStyle = `rgba(168,85,247,${0.35 + flash * 0.55})`;
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

        // --- patrol circles: fire-style pulse in the live pixel look ---
        // Each patrolled cell lights up when visited (halo + fill + ring, like the
        // live grid-halo/grid-fill/grid-cells layers) then fades out — same temporal
        // language as the fire grid, so the animation reads "patrol intensity over time".
        ctx.globalCompositeOperation = 'lighter';
        if (on.effortPts && D.effortPts) {
            const eres = D.effortPts.res;
            const eoff = D.effortPts.off || 0;
            const fadeMs = Math.max(bMs * 3, DAY * 10); // linger a touch longer than fires
            for (const f of D.effortPts.frames) {
                if (f.t > t) break;
                const age = t - f.t;
                if (age > fadeMs) continue;
                const k = 1 - age / fadeMs;                 // 1 fresh → 0 faded
                const flash = Math.max(0, 1 - age / (fadeMs * 0.25)); // bright pop on arrival
                for (const pt of f.pts) {
                    const lon = (pt[0] + eoff) * eres, lat = (pt[1] + eoff) * eres;
                    const p = proj(lon, lat);
                    if (p.x < -25 || p.y < -25 || p.x > w + 25 || p.y > h + 25) continue;
                    const km = pt[2];
                    const inten = Math.min(1, Math.log2(1 + km) / 7); // effort intensity 0..1
                    const cellPx = Math.abs(proj(lon + eres, lat).x - p.x);
                    const rCell = Math.max(2.5, Math.min(cellPx * 0.5, 22));
                    // halo glow — like grid-halo (#22c55e, heavy blur)
                    const rHalo = rCell * (1.1 + 0.6 * inten) * (1 + flash * 0.35);
                    const haloA = (0.10 + 0.30 * inten + 0.25 * flash) * k;
                    const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, rHalo);
                    g.addColorStop(0, `rgba(34,197,94,${Math.min(0.85, haloA)})`);
                    g.addColorStop(0.55, `rgba(34,197,94,${Math.min(0.6, haloA * 0.55)})`);
                    g.addColorStop(1, 'rgba(34,197,94,0)');
                    ctx.fillStyle = g;
                    ctx.beginPath(); ctx.arc(p.x, p.y, rHalo, 0, 6.283); ctx.fill();
                    // inner fill — like grid-fill (#4ade80), sized by intensity
                    const rFill = rCell * (0.25 + 0.5 * inten);
                    ctx.fillStyle = `rgba(74,222,128,${(0.25 + 0.45 * inten) * k})`;
                    ctx.beginPath(); ctx.arc(p.x, p.y, rFill, 0, 6.283); ctx.fill();
                    // outline ring — like grid-cells stroke, fades with age
                    ctx.strokeStyle = `rgba(74,222,128,${(0.20 + 0.55 * k) * k})`;
                    ctx.lineWidth = 1;
                    ctx.beginPath(); ctx.arc(p.x, p.y, rCell, 0, 6.283); ctx.stroke();
                }
            }
        }

        // --- fire grid: flash + afterglow ---
        if (on.fireGrid && D.fireGrid) {
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
                    const r = Math.max(2.5, Math.min(22, 2 + Math.log2(1 + n) * 2.4)) * Math.max(0.5, zoom / 5);
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
        if (on.firePts && D.firePts) {
            const fadeMs = DAY * 3;
            for (const pt of D.firePts) {
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
        ctx.globalCompositeOperation = 'source-over';

        // --- fire trajectories: build up one-by-one, then ashen out (red → grey → gone) ---
        if (on.trajs && D.trajs) {
            for (const g of D.trajs) {
                if (g.t0 > t) continue;
                let alpha, ash;
                if (t <= g.t1) {
                    alpha = 0.95; ash = 0;
                } else {
                    const fade = (t - g.t1) / (TRAJ_FADE_DAYS * DAY);
                    if (fade >= 1) continue;               // fully gone
                    ash = Math.min(1, fade * 1.6);         // grey out first…
                    alpha = 0.95 * (1 - fade);             // …then vanish
                }
                ctx.strokeStyle = ashColor(ash, alpha);
                ctx.lineWidth = t <= g.t1 ? 2.5 : 1.5;
                ctx.lineJoin = 'round'; ctx.lineCap = 'round';
                ctx.beginPath();
                let started = false, headX = null, headY = null;
                for (let i = 0; i < g.pts.length; i++) {
                    const pt = g.pts[i];
                    if (pt[2] <= t) {
                        const p = proj(pt[0], pt[1]);
                        if (!started) { ctx.moveTo(p.x, p.y); started = true; }
                        else ctx.lineTo(p.x, p.y);
                        headX = p.x; headY = p.y;
                    } else {
                        if (started && i > 0) {
                            const prev = g.pts[i - 1];
                            const span = pt[2] - prev[2];
                            if (span > 0) {
                                const frac = (t - prev[2]) / span;
                                if (frac > 0) {
                                    const lon = prev[0] + (pt[0] - prev[0]) * frac;
                                    const lat = prev[1] + (pt[1] - prev[1]) * frac;
                                    const p = proj(lon, lat);
                                    ctx.lineTo(p.x, p.y);
                                    headX = p.x; headY = p.y;
                                }
                            }
                        }
                        break;
                    }
                }
                if (started) ctx.stroke();
                if (headX !== null && t <= g.t1 + 2 * DAY) {
                    const gr = ctx.createRadialGradient(headX, headY, 0, headX, headY, 7);
                    gr.addColorStop(0, 'rgba(255,220,120,0.95)');
                    gr.addColorStop(0.4, 'rgba(255,120,50,0.7)');
                    gr.addColorStop(1, 'rgba(255,120,50,0)');
                    ctx.fillStyle = gr;
                    ctx.beginPath(); ctx.arc(headX, headY, 7, 0, 6.283); ctx.fill();
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
        if (A.t >= A.t1) { A.t = A.t1; A.playing = false; updatePlayBtn(); }
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

    function play() {
        if (!A) return;
        if (A.t >= A.t1 - 1) A.t = A.t0;
        A.playing = true; A.lastNow = null;
        updatePlayBtn();
        A.raf = requestAnimationFrame(loop);
    }
    function pause() { if (A) { A.playing = false; updatePlayBtn(); if (A.raf) cancelAnimationFrame(A.raf); } }

    // ---------- GIF export ----------
    async function exportGIF() {
        if (!A || A.recording) return;
        A.recording = true;
        pause();
        const btn = document.getElementById('anim-gif');
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
            const frames = 80, delayMs = 100;
            const enc = GIFEncoder();
            const span = A.t1 - A.t0;
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
            toast('GIF downloaded (' + Math.round(blob.size / 1024) + ' KB)', 'success');
        } catch (e) {
            console.error('GIF export failed:', e);
            toast('GIF export failed: ' + e.message, 'error');
        } finally {
            A.recording = false;
            btn.textContent = 'GIF';
            drawAndSync();
        }
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
            <button id="anim-gif" class="anim-btn" title="Download animated GIF">GIF</button>
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
            // (unless a share link explicitly enabled them)
            if ((name === 'effortGrid' || name === 'effortPts') &&
                !(window.viewLayers && window.viewLayers.pixels) && !A.on[name]) {
                chip.classList.add('hidden');
            }
            chips.appendChild(chip);
        }
        header.appendChild(chips);
        // staggered reveal (same behaviour as date preset tags)
        Array.from(chips.children).filter(c => !c.classList.contains('hidden')).forEach((chip, i) => {
            setTimeout(() => chip.classList.add('visible'), 60 + i * 45);
        });
        updateChips();

        // playhead + progress in slider track
        const track = document.getElementById('time-slider-track');
        const prog = document.createElement('div');
        prog.id = 'anim-progress';
        track.appendChild(prog);
        const ph = document.createElement('div');
        ph.id = 'anim-playhead';
        track.appendChild(ph);

        // playhead drag = scrub (works during playback; pauses while dragging)
        let wasPlaying = false;
        const scrubMove = (e) => {
            const rect = track.getBoundingClientRect();
            const x = (e.touches ? e.touches[0].clientX : e.clientX);
            const pct = Math.max(0, Math.min(100, (x - rect.left) / rect.width * 100));
            const [s, ep] = sliderRangePcts();
            const frac = Math.max(0, Math.min(1, (pct - s) / Math.max(0.001, ep - s)));
            A.t = A.t0 + (A.t1 - A.t0) * frac;
            drawAndSync();
            e.preventDefault();
        };
        const scrubEnd = () => {
            document.removeEventListener('pointermove', scrubMove);
            document.removeEventListener('pointerup', scrubEnd);
            if (wasPlaying) play();
        };
        ph.addEventListener('pointerdown', (e) => {
            e.preventDefault(); e.stopPropagation();
            wasPlaying = A.playing;
            pause();
            document.addEventListener('pointermove', scrubMove);
            document.addEventListener('pointerup', scrubEnd);
        });

        // controls
        document.getElementById('anim-play').onclick = () => A.playing ? pause() : play();
        document.getElementById('anim-close').onclick = () => window.Animator.close();
        document.getElementById('anim-gif').onclick = exportGIF;

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
        if (viewportInside(A.fetchBbox)) return;
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
            const { bbox, fixed } = activeBbox();

            const { canvas, resize } = makeCanvas();
            A = {
                canvas, ctx: canvas.getContext('2d'), resize,
                data: {}, loading: {}, on: {},
                fromISO, toISO, fetchBbox: bbox, bboxFixed: fixed,
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
                initial = opts.layers.filter(n => LAYERS[n]);
            } else {
                initial = [];
                const v = window.viewLayers || {};
                const pins = pinnedTypes();
                const hiZoom = map.getZoom() >= POINTS_ZOOM && bboxArea(bbox) <= POINTS_MAX_AREA;
                if (v.fires || pins.has('fires')) { initial.push('trajs', hiZoom ? 'firePts' : 'fireGrid'); }
                if (v.pixels) initial.push(hiZoom ? 'effortPts' : 'effortGrid');
                if (v.deforest || pins.has('deforest')) initial.push('deforest');
                if (v.settlements || pins.has('settlements')) initial.push('settlements');
                const snap = snapshotPinned();
                A.snapPreview = snap;
                if (snap.turb.plume.length || snap.turb.mines.length) initial.push('turb');
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
                (A.data[n].frames ? A.data[n].frames.length : true)));
            if (!any) toast('No animatable data in view for this window — toggle layers or adjust dates', 'warning');

            A.mapHandler = () => { if (A) draw(A.t); };
            A.moveEndHandler = onMoveEnd;
            A.resizeHandler = () => { if (A) { A.resize(); draw(A.t); } };
            map.on('move', A.mapHandler);
            map.on('moveend', A.moveEndHandler);
            window.addEventListener('resize', A.resizeHandler);

            drawAndSync();
            if (opts.paused) { pause(); drawAndSync(); } else play();
            if (typeof updateShareURL === 'function') updateShareURL();
        },
        close() {
            if (!A) return;
            pause();
            map.off('move', A.mapHandler);
            map.off('moveend', A.moveEndHandler);
            window.removeEventListener('resize', A.resizeHandler);
            document.removeEventListener('keydown', A.keyHandler);
            clearTimeout(A._refetchTimer);
            if (A.canvas) A.canvas.remove();
            teardownUI();
            hideLoading();
            A = null;
            syncBaseEffortVisibility(); // restore live patrol pixels per viewLayers.pixels
            if (typeof updateShareURL === 'function') updateShareURL();
        },
        isOpen() { return !!A; },
        toggle() { A ? this.close() : this.open(); },
        getState() {
            if (!A) return null;
            return {
                layers: LAYER_ORDER.filter(n => A.on[n]),
                speed: A.speed,
                tISO: fmtDate(A.t),
                playing: A.playing
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
