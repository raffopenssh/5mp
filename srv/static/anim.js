// ============================================================
// 5MP Time Animator
// Animates currently-visible/toggled/pinned data over the
// selected time window on a canvas overlay above the MapLibre map.
//
// Temporal semantics (deliberate, per layer):
//   fires (heatmap)   : transient — flash + afterglow fading over ~2 buckets
//   fire trajectories : build up point-by-point at true dated speed
//                       (people/fire-front movement), bright head marker,
//                       then fade to a dim residual trace after group ends
//   patrol pixels     : fade over 90 days (coverage goes stale — recency)
//   deforestation     : accumulates permanently, new events flash brighter
//   settlements       : static context (no reliable event dates)
//   turbidity plume   : dated sample pixels accumulate (sediment stays in
//                       the record), new ones flash; mining sites static
//   pinned park infra : roads/rivers/places/water/airstrips — static context
//                       snapshotted from whatever is pinned on the map
// ============================================================
(function () {
    'use strict';

    const DAY = 86400000;
    const EFFORT_FADE_DAYS = 90;
    const TRAJ_RESIDUAL_ALPHA = 0.18;
    const TRAJ_FADE_DAYS = 30;      // head→residual fade after group end
    const DEFOREST_FLASH_DAYS = 45; // new deforestation flashes bright

    let A = null; // active animator state

    function getPwdSafe() { return (typeof getPwd === 'function' ? getPwd() : '') || ''; }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type || 'info'); }

    function fmtDate(ms) {
        const d = new Date(ms);
        return d.toISOString().slice(0, 10);
    }
    function fmtDateHuman(ms) {
        const d = new Date(ms);
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        return d.getUTCDate() + ' ' + months[d.getUTCMonth()] + ' ' + d.getUTCFullYear();
    }
    function parseD(s) { return Date.parse(s + 'T00:00:00Z'); }

    // ---------- UI ----------
    const CSS = `
    #anim-bar { position: fixed; left: 50%; transform: translateX(-50%); bottom: 84px; z-index: 650;
        background: rgba(12,14,16,0.94); border: 1px solid rgba(255,255,255,0.12); border-radius: 14px;
        padding: 10px 14px; display: flex; flex-direction: column; gap: 6px; min-width: 340px; max-width: 94vw;
        box-shadow: 0 8px 32px rgba(0,0,0,0.6); font-family: inherit; color: #ddd; }
    #anim-bar .anim-row { display: flex; align-items: center; gap: 10px; }
    #anim-bar button { background: rgba(255,255,255,0.08); color: #ddd; border: 1px solid rgba(255,255,255,0.12);
        border-radius: 8px; padding: 5px 10px; cursor: pointer; font-size: 13px; line-height: 1; }
    #anim-bar button:hover { background: rgba(255,255,255,0.16); }
    #anim-bar button.primary { background: #16a34a; border-color: #16a34a; color: #fff; font-weight: 600; }
    #anim-bar button.primary:hover { background: #15803d; }
    #anim-bar .anim-date { font-variant-numeric: tabular-nums; font-weight: 700; font-size: 15px; color: #fff; min-width: 110px; text-align: center; }
    #anim-scrub { flex: 1; min-width: 120px; accent-color: #22c55e; }
    #anim-bar .anim-speed { font-size: 11px; color: #999; min-width: 64px; text-align: center; font-variant-numeric: tabular-nums; }
    #anim-bar .anim-legend { display: flex; gap: 8px; flex-wrap: wrap; font-size: 10px; color: #999; }
    #anim-bar .anim-legend span { display: inline-flex; align-items: center; gap: 3px; }
    #anim-bar .anim-legend i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
    #anim-bar .anim-close { margin-left: auto; }
    #anim-loading { position: fixed; left: 50%; top: 50%; transform: translate(-50%,-50%); z-index: 700;
        background: rgba(12,14,16,0.95); border: 1px solid rgba(255,255,255,0.12); border-radius: 16px;
        padding: 26px 34px; text-align: center; color: #ddd; box-shadow: 0 8px 40px rgba(0,0,0,0.7); }
    #anim-loading .flame { font-size: 30px; animation: animFlicker 0.9s infinite alternate; }
    @keyframes animFlicker { 0% { transform: scale(1) rotate(-3deg); opacity: .8; } 100% { transform: scale(1.15) rotate(3deg); opacity: 1; } }
    #anim-loading .bar { width: 220px; height: 5px; background: rgba(255,255,255,0.1); border-radius: 3px; margin: 12px auto 4px; overflow: hidden; }
    #anim-loading .bar > div { height: 100%; width: 0%; background: linear-gradient(90deg,#f59e0b,#ef4444); border-radius: 3px; transition: width .3s; }
    #anim-loading .sub { font-size: 11px; color: #888; margin-top: 4px; }
    #anim-canvas { position: absolute; inset: 0; pointer-events: none; z-index: 5; }
    @media (max-width: 640px) { #anim-bar { bottom: 70px; padding: 8px 10px; } #anim-bar .anim-date { font-size: 13px; min-width: 88px; } }
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
            el.innerHTML = '<div class="flame">🔥</div><div class="msg" style="margin-top:8px;font-size:13px;"></div><div class="bar"><div></div></div><div class="sub">preparing animation…</div>';
            document.body.appendChild(el);
        }
        el.querySelector('.msg').textContent = msg;
        el.querySelector('.bar > div').style.width = (pct || 0) + '%';
    }
    function hideLoading() { const el = document.getElementById('anim-loading'); if (el) el.remove(); }

    // ---------- data loading ----------
    function activeBbox() {
        if (typeof currentBbox !== 'undefined' && currentBbox && currentBbox.length === 4) return currentBbox.slice();
        const b = map.getBounds();
        return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
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
        } catch (e) { /* pinnedLayers not in scope */ }
        return out;
    }

    // Snapshot GeoJSON data of everything currently pinned on the map
    // (park layers + single-feature pins + realtime trajectories).
    // Splits into: static infrastructure features and dated turbidity features.
    const STATIC_COLORS = { road: '#60a5fa', river: '#3b82f6', water: '#3b82f6', place: '#c084fc',
        infrastructure: '#22c55e', airstrip: '#22c55e', learned: '#22c55e' };
    function snapshotPinned() {
        const statics = [];   // { geom, color, kind }
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
                // dated turbidity features get animated
                if (ft === 'turbid_water') { turb.plume.push({ lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], t: p.date ? parseD(p.date) : null }); continue; }
                if (ft === 'turbidity_alert') { turb.alerts.push({ lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], t: p.date ? parseD(p.date) : null }); continue; }
                if (ft === 'mining_site') { turb.mines.push({ lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] }); continue; }
                // animated elsewhere — skip (fires/deforest/settlements come from API)
                if (ft.includes('fire') || ft.includes('deforest') || ft === 'settlement') continue;
                // everything else pinned = static infrastructure context
                let color = '#9ca3af';
                for (const k of Object.keys(STATIC_COLORS)) { if (ft.includes(k)) { color = STATIC_COLORS[k]; break; } }
                statics.push({ geom: f.geometry, color });
            }
        }
        return { statics, turb };
    }

    function wantedLayers() {
        const v = window.viewLayers || {};
        const pins = pinnedTypes();
        return {
            pixels: !!v.pixels,
            fires: !!v.fires || pins.has('fires') || true, // fire heatmap always as background context
            trajectories: !!v.fires || pins.has('fires'),
            deforest: !!v.deforest || pins.has('deforest'),
            settlements: !!v.settlements || pins.has('settlements')
        };
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
        return 0.05;
    }

    async function fetchJSON(url) {
        const r = await fetch(url);
        if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url.split('?')[0]);
        return r.json();
    }

    async function loadData(bbox, fromISO, toISO, layers) {
        const pwd = encodeURIComponent(getPwdSafe());
        const bb = bbox.map(v => v.toFixed(4)).join(',');
        const spanDays = (parseD(toISO) - parseD(fromISO)) / DAY;
        const step = chooseStep(spanDays);
        const res = chooseRes(bbox);
        const data = { step, res, fireFrames: [], effortFrames: [], trajs: [], deforest: [], settlements: [] };
        const tasks = [];
        let done = 0, total = 0;
        const tick = (label) => { done++; showLoading(label, Math.round(done / total * 100)); };

        if (layers.fires) {
            total++;
            tasks.push(fetchJSON(`/api/fire-frames?bbox=${bb}&from=${fromISO}&to=${toISO}&step=${step}&res=${res}&pwd=${pwd}`).then(j => {
                data.fireFrames = (j.frames || []).map(f => ({ t: parseD(f.d), pts: f.p }));
                data.res = j.res || res;
                tick('Fire heatmap loaded');
            }));
        }
        if (layers.trajectories) {
            total++;
            tasks.push(fetchJSON(`/api/fire-anim-trajectories?bbox=${bb}&from=${fromISO}&to=${toISO}&limit=800&pwd=${pwd}`).then(j => {
                data.trajs = (j.groups || []).map(g => {
                    const pts = g.pts.map(p => [p[0], p[1], parseD(p[2])]).sort((a, b) => a[2] - b[2]);
                    return { pts, t0: pts[0][2], t1: pts[pts.length - 1][2], type: g.type, kmd: g.kmd };
                }).filter(g => g.pts.length >= 2);
                tick('Fire trajectories loaded');
            }));
        }
        if (layers.pixels) {
            total++;
            tasks.push(fetchJSON(`/api/fire-frames?layer=effort&bbox=${bb}&from=${fromISO}&to=${toISO}&step=${step}&pwd=${pwd}`).then(j => {
                data.effortFrames = (j.frames || []).map(f => ({ t: parseD(f.d), pts: f.p }));
                tick('Patrol effort loaded');
            }).catch(() => tick('Patrol effort skipped')));
        }
        if (layers.deforest) {
            total++;
            tasks.push(fetchJSON(`/api/features-in-bbox?type=deforestation&bbox=${bb}&from=${fromISO}&to=${toISO}&limit=1500&pwd=${pwd}`).then(j => {
                data.deforest = (j.features || []).map(f => {
                    const p = f.properties || {};
                    return { lon: p.lon, lat: p.lat, t: parseD(p.start_date || (p.year + '-06-15')), area: p.area_km2 || 0.1 };
                }).filter(d => d.lon != null && !isNaN(d.t)).sort((a, b) => a.t - b.t);
                tick('Deforestation loaded');
            }).catch(() => tick('Deforestation skipped')));
        }
        if (layers.settlements) {
            total++;
            tasks.push(fetchJSON(`/api/features-in-bbox?type=settlement&bbox=${bb}&limit=1500&pwd=${pwd}`).then(j => {
                data.settlements = (j.features || []).map(f => {
                    const p = f.properties || {};
                    return { lon: p.lon, lat: p.lat };
                }).filter(d => d.lon != null);
                tick('Settlements loaded');
            }).catch(() => tick('Settlements skipped')));
        }
        showLoading('Fetching data…', 2);
        await Promise.all(tasks);
        return data;
    }

    // ---------- rendering ----------
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

    function bucketMs(step) { return step === 'day' ? DAY : step === 'week' ? 7 * DAY : 30 * DAY; }

    // Draw any GeoJSON geometry as static context (thin, semi-transparent).
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
                if (p.x >= -10 && p.y >= -10 && p.x <= w + 10 && p.y <= h + 10) {
                    ctx.beginPath(); ctx.arc(p.x, p.y, 2.2, 0, 6.283); ctx.fill();
                }
                break;
            }
            case 'MultiPoint':
                for (const c of geom.coordinates) {
                    const p = proj(c[0], c[1]);
                    if (p.x >= -10 && p.y >= -10 && p.x <= w + 10 && p.y <= h + 10) {
                        ctx.beginPath(); ctx.arc(p.x, p.y, 2.2, 0, 6.283); ctx.fill();
                    }
                }
                break;
            case 'LineString': line(geom.coordinates); break;
            case 'MultiLineString': for (const l of geom.coordinates) line(l); break;
            case 'Polygon': for (const r of geom.coordinates) line(r); break;
            case 'MultiPolygon': for (const poly of geom.coordinates) for (const r of poly) line(r); break;
        }
        ctx.globalAlpha = 1;
    }

    function draw(t) {
        const ctx = A.ctx;
        const w = A.canvas.clientWidth, h = A.canvas.clientHeight;
        ctx.clearRect(0, 0, w, h);
        const proj = (lon, lat) => map.project([lon, lat]);
        const zoom = map.getZoom();
        const bMs = bucketMs(A.data.step);

        // --- pinned static infrastructure: roads/rivers/places/etc ---
        if (A.data.statics && A.data.statics.length) {
            for (const s of A.data.statics) {
                drawGeom(ctx, s.geom, s.color, proj, w, h);
            }
        }

        // --- turbidity: mining sites static; plume pixels accumulate + flash ---
        if (A.data.turb) {
            const tb = A.data.turb;
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
                if (pt.t && pt.t > t) continue;   // appears at its scan date, then stays
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

        // --- settlements: static context, dim yellow ---
        if (A.data.settlements.length) {
            ctx.fillStyle = 'rgba(251,191,36,0.45)';
            for (const s of A.data.settlements) {
                const p = proj(s.lon, s.lat);
                if (p.x < -10 || p.y < -10 || p.x > w + 10 || p.y > h + 10) continue;
                ctx.beginPath(); ctx.arc(p.x, p.y, 1.6, 0, 6.283); ctx.fill();
            }
        }

        // --- deforestation: accumulate; recent flash brighter ---
        if (A.data.deforest.length) {
            for (const d of A.data.deforest) {
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

        // --- patrol effort: green pixels fading over 90 days ---
        if (A.data.effortFrames.length) {
            for (const f of A.data.effortFrames) {
                if (f.t > t) break;
                const ageD = (t - f.t) / DAY;
                if (ageD > EFFORT_FADE_DAYS) continue;
                const alpha = 0.75 * (1 - ageD / EFFORT_FADE_DAYS);
                for (const pt of f.pts) {
                    const p = proj(pt[0], pt[1]);
                    if (p.x < -10 || p.y < -10 || p.x > w + 10 || p.y > h + 10) continue;
                    const r = Math.max(2, Math.min(10, 1.5 + Math.log2(1 + pt[2]) )) * Math.max(0.6, zoom / 6);
                    ctx.fillStyle = `rgba(74,222,128,${alpha})`;
                    ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 6.283); ctx.fill();
                }
            }
        }

        // --- fire heatmap: flash + afterglow (additive) ---
        if (A.data.fireFrames.length) {
            ctx.globalCompositeOperation = 'lighter';
            const fadeMs = bMs * 2.2;
            const res = A.data.res;
            for (const f of A.data.fireFrames) {
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
            ctx.globalCompositeOperation = 'source-over';
        }

        // --- fire trajectories: build up at true speed, head marker, residual trace ---
        if (A.data.trajs.length) {
            for (const g of A.data.trajs) {
                if (g.t0 > t) continue;
                let alpha;
                if (t <= g.t1) {
                    alpha = 0.95;
                } else {
                    const fade = (t - g.t1) / (TRAJ_FADE_DAYS * DAY);
                    alpha = Math.max(TRAJ_RESIDUAL_ALPHA, 0.95 - fade * (0.95 - TRAJ_RESIDUAL_ALPHA));
                }
                ctx.strokeStyle = `rgba(239,68,68,${alpha})`;
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
                        // interpolate partial segment toward next point (true speed)
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
                // head marker while active
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

    function drawAndSync() {
        draw(A.t);
        const el = document.getElementById('anim-date');
        if (el) el.textContent = fmtDateHuman(A.t);
        const sc = document.getElementById('anim-scrub');
        if (sc && !A.scrubbing) sc.value = Math.round((A.t - A.t0) / (A.t1 - A.t0) * 1000);
        const sp = document.getElementById('anim-speed-lbl');
        if (sp) sp.textContent = fmtSpeed();
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
            btn.textContent = '⏳ 0%';
            const gifenc = await import('https://unpkg.com/gifenc@1.0.3/dist/gifenc.esm.js');
            const { GIFEncoder, quantize, applyPalette } = gifenc;

            const mapCanvas = map.getCanvas();
            const srcW = A.canvas.clientWidth, srcH = A.canvas.clientHeight;
            const outW = Math.min(720, srcW);
            const outH = Math.round(srcH * outW / srcW);
            const off = document.createElement('canvas');
            off.width = outW; off.height = outH;
            const octx = off.getContext('2d', { willReadFrequently: true });

            const frames = 80;                       // total gif frames
            const delayMs = 100;                     // 10 fps
            const enc = GIFEncoder();
            const span = A.t1 - A.t0;

            for (let i = 0; i < frames; i++) {
                const t = A.t0 + span * i / (frames - 1);
                draw(t);
                octx.fillStyle = '#0a0a0a';
                octx.fillRect(0, 0, outW, outH);
                octx.drawImage(mapCanvas, 0, 0, outW, outH);
                octx.drawImage(A.canvas, 0, 0, outW, outH);
                // date stamp
                octx.font = 'bold 16px sans-serif';
                octx.fillStyle = 'rgba(0,0,0,0.55)';
                octx.fillRect(8, outH - 30, 150, 22);
                octx.fillStyle = '#fff';
                octx.fillText(fmtDateHuman(t), 14, outH - 14);

                const { data } = octx.getImageData(0, 0, outW, outH);
                const palette = quantize(data, 256);
                const index = applyPalette(data, palette);
                enc.writeFrame(index, outW, outH, { palette, delay: delayMs });
                btn.textContent = '⏳ ' + Math.round((i + 1) / frames * 100) + '%';
                if (i % 8 === 0) await new Promise(r => setTimeout(r, 0)); // keep UI alive
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
            btn.textContent = '⬇ GIF';
            drawAndSync();
        }
    }

    // ---------- control bar ----------
    function buildBar(layers) {
        const bar = document.createElement('div');
        bar.id = 'anim-bar';
        const chips = [];
        if (layers.fires) chips.push('<span><i style="background:#ef4444"></i>fires</span>');
        if (layers.trajectories) chips.push('<span><i style="background:#ff7832"></i>fire movement</span>');
        if (layers.pixels) chips.push('<span><i style="background:#4ade80"></i>patrols (fade 90d)</span>');
        if (layers.deforest) chips.push('<span><i style="background:#a855f7"></i>deforestation (accumulates)</span>');
        if (layers.settlements) chips.push('<span><i style="background:#fbbf24"></i>settlements</span>');
        if (A.data.turb && (A.data.turb.plume.length || A.data.turb.mines.length)) chips.push('<span><i style="background:#eab308"></i>turbidity (accumulates)</span>');
        if (A.data.statics && A.data.statics.length) chips.push('<span><i style="background:#60a5fa"></i>pinned infrastructure</span>');
        bar.innerHTML = `
            <div class="anim-row">
                <button id="anim-play" class="primary" title="Play/Pause (space)">▶</button>
                <span class="anim-date" id="anim-date"></span>
                <input type="range" id="anim-scrub" min="0" max="1000" value="0">
                <button id="anim-slower" title="Slower">−</button>
                <span class="anim-speed" id="anim-speed-lbl"></span>
                <button id="anim-faster" title="Faster">+</button>
                <button id="anim-gif" title="Download animated GIF">⬇ GIF</button>
                <button id="anim-close" class="anim-close" title="Close animator">✕</button>
            </div>
            <div class="anim-legend">${chips.join('')}</div>`;
        document.body.appendChild(bar);

        bar.querySelector('#anim-play').onclick = () => A.playing ? pause() : play();
        bar.querySelector('#anim-close').onclick = () => window.Animator.close();
        bar.querySelector('#anim-gif').onclick = exportGIF;
        bar.querySelector('#anim-slower').onclick = () => { A.speed = Math.max(0.25, A.speed / 1.6); drawAndSync(); };
        bar.querySelector('#anim-faster').onclick = () => { A.speed = Math.min(365, A.speed * 1.6); drawAndSync(); };
        const sc = bar.querySelector('#anim-scrub');
        sc.oninput = () => {
            A.scrubbing = true;
            A.t = A.t0 + (A.t1 - A.t0) * sc.value / 1000;
            draw(A.t);
            document.getElementById('anim-date').textContent = fmtDateHuman(A.t);
        };
        sc.onchange = () => { A.scrubbing = false; };

        A.keyHandler = (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
            if (e.code === 'Space') { e.preventDefault(); A.playing ? pause() : play(); }
            else if (e.code === 'ArrowRight') { A.t = Math.min(A.t1, A.t + bucketMs(A.data.step)); drawAndSync(); }
            else if (e.code === 'ArrowLeft') { A.t = Math.max(A.t0, A.t - bucketMs(A.data.step)); drawAndSync(); }
            else if (e.code === 'Escape') { window.Animator.close(); }
        };
        document.addEventListener('keydown', A.keyHandler);
    }

    // ---------- public API ----------
    window.Animator = {
        async open() {
            if (A) { this.close(); }
            injectCSS();
            const fromISO = (typeof dateFrom !== 'undefined' && dateFrom) ? dateFrom : '2023-01-01';
            const toISO = (typeof dateTo !== 'undefined' && dateTo) ? dateTo : fmtDate(Date.now());
            const bbox = activeBbox();
            const layers = wantedLayers();

            showLoading('Fetching data…', 0);
            let data;
            try {
                data = await loadData(bbox, fromISO, toISO, layers);
            } catch (e) {
                hideLoading();
                toast('Animation data load failed: ' + e.message, 'error');
                return;
            }
            hideLoading();

            // Snapshot pinned park layers: static infrastructure + turbidity
            const snap = snapshotPinned();
            data.statics = snap.statics;
            data.turb = snap.turb;

            const nFire = data.fireFrames.reduce((s, f) => s + f.pts.length, 0);
            if (!nFire && !data.trajs.length && !data.deforest.length && !data.effortFrames.length) {
                toast('No animatable data in view for this time window', 'warning');
                return;
            }

            const { canvas, resize } = makeCanvas();
            A = {
                canvas, ctx: canvas.getContext('2d'), resize, data, layers,
                t0: parseD(fromISO), t1: parseD(toISO) + DAY - 1,
                playing: false, speed: 1, raf: null, scrubbing: false, recording: false
            };
            A.t = A.t0;
            const spanDays = (A.t1 - A.t0) / DAY;
            A.speed = Math.max(0.5, spanDays / 20); // full span in ~20s by default

            buildBar(layers);
            A.mapHandler = () => { if (A) draw(A.t); };
            A.resizeHandler = () => { if (A) { A.resize(); draw(A.t); } };
            map.on('move', A.mapHandler);
            window.addEventListener('resize', A.resizeHandler);

            drawAndSync();
            play();
            toast(`Animating ${fmtDateHuman(A.t0)} → ${fmtDateHuman(A.t1)}` + (data.step !== 'day' ? ` (${data.step}ly steps)` : ''), 'info');
        },
        close() {
            if (!A) return;
            pause();
            map.off('move', A.mapHandler);
            window.removeEventListener('resize', A.resizeHandler);
            document.removeEventListener('keydown', A.keyHandler);
            if (A.canvas) A.canvas.remove();
            const bar = document.getElementById('anim-bar');
            if (bar) bar.remove();
            hideLoading();
            A = null;
        },
        isOpen() { return !!A; },
        toggle() { A ? this.close() : this.open(); }
    };
})();
