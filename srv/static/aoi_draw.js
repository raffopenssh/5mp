// ============================================================
// 5MP AOI editor — draw an area of interest, and be honest about
// what asking for it costs.
//
// Why this exists as its own mode rather than a variant of the
// existing "Select Area" bbox tool:
//
//   The bbox tool is a *filter*. It is instant, it is disposable,
//   and it narrows data we already hold. An AOI is the opposite:
//   an arbitrary polygon, an owner, and a queue of downloads that
//   will spend real FIRMS quota and hours of CPU spread over days
//   (docs/PLAN_AOI_OVERLAY.md §0 rule 3). Presenting the two as
//   the same gesture would be a lie about the second one.
//
// So the flow is deliberately slower than a drag:
//   1. click to place vertices, drag them to adjust, close the ring
//   2. a live estimate updates on every edit — area, FIRMS calls,
//      "about 3 days"
//   3. the analysis window is READ FROM THE TIME SLIDER, not from
//      a date picker in this dialog. There is one place in this app
//      where a user says "which dates", and it is the slider; a
//      second one would immediately disagree with it. Changing the
//      slider while the editor is open re-prices the AOI live.
//   4. confirm names it and queues it; a progress notification
//      then tracks it to completion over the following days.
//
// Entry point: window.AOIDraw.start() / .cancel() / .isActive()
// ============================================================
(function () {
    'use strict';

    const SRC = 'aoi-draft';
    const MIN_VERTICES = 3;
    const HANDLE_HIT_PX = 14;

    let S = null;   // active editor state, or null

    // The MapLibre instance is a top-level `const map` in globe.html. That is
    // a global *lexical* binding, so a bare reference from another classic
    // script resolves to it — which is what anim.js does too.
    //
    // What must NOT be used is `window.map`: named access on window resolves
    // that to the <div id="map"> ELEMENT, which has no getSource(), so the
    // failure is a confusing TypeError rather than an obvious undefined.
    // Hence the accessor is called theMap(), not map(): a local function named
    // `map` would shadow the very binding it is trying to read.
    function theMap() { return typeof map !== 'undefined' ? map : null; }
    function pwd() { return (typeof getPwd === 'function' ? getPwd() : ''); }

    // ---------------------------------------------------------- geometry

    // The draft ring, closed. MapLibre wants a closed ring; the estimate
    // endpoint insists on one (>= 4 positions).
    function ring() {
        if (!S || S.pts.length < 3) return null;
        const r = S.pts.map(p => [p[0], p[1]]);
        r.push([S.pts[0][0], S.pts[0][1]]);
        return r;
    }

    function geometry() {
        // A locked shape (holes, or a multipolygon) is carried through
        // untouched: the editor can only express a single outer ring, and
        // silently flattening a donut into its outer ring would change what
        // the AOI *means* while looking like a date-only edit.
        if (S && S.ringLocked && S.originalGeom) return S.originalGeom;
        const r = ring();
        return r ? { type: 'Polygon', coordinates: [r] } : null;
    }

    // Draft rendering is three layers on one source: the filled polygon,
    // the outline, and the vertex handles. Kept separate from the 'aois'
    // source so an in-progress draft can never be mistaken for a saved AOI
    // (and so cancelling is a single setData).
    function ensureLayers() {
        const m = theMap();
        if (m.getSource(SRC)) return;
        m.addSource(SRC, { type: 'geojson', data: empty() });
        m.addLayer({
            id: 'aoi-draft-fill', type: 'fill', source: SRC,
            filter: ['==', '$type', 'Polygon'],
            paint: { 'fill-color': '#60a5fa', 'fill-opacity': 0.12 }
        });
        m.addLayer({
            id: 'aoi-draft-line', type: 'line', source: SRC,
            filter: ['==', '$type', 'LineString'],
            paint: { 'line-color': '#60a5fa', 'line-width': 2, 'line-dasharray': [2, 1.5] }
        });
        m.addLayer({
            id: 'aoi-draft-pts', type: 'circle', source: SRC,
            filter: ['==', '$type', 'Point'],
            paint: {
                'circle-radius': ['case', ['==', ['get', 'i'], 0], 7, 5],
                'circle-color': ['case', ['==', ['get', 'i'], 0], '#fbbf24', '#fff'],
                'circle-stroke-color': '#1e3a5f', 'circle-stroke-width': 2
            }
        });
    }

    function empty() { return { type: 'FeatureCollection', features: [] }; }

    function redraw() {
        const m = theMap();
        if (!m || !m.getSource(SRC)) return;
        const f = [];
        if (S && S.ringLocked && S.originalGeom) {
            // Not editable, but it must still be visible: the user is about to
            // re-date exactly this shape.
            f.push({ type: 'Feature', geometry: S.originalGeom, properties: {} });
            m.getSource(SRC).setData({ type: 'FeatureCollection', features: f });
            return;
        }
        if (S && S.pts.length >= 3) {
            f.push({ type: 'Feature', geometry: geometry(), properties: {} });
        }
        if (S && S.pts.length >= 2) {
            // While drawing, the line follows the cursor so the user can see
            // the edge they are about to commit.
            const line = S.pts.map(p => [p[0], p[1]]);
            if (S.hover && !S.closed) line.push(S.hover);
            if (S.closed) line.push([S.pts[0][0], S.pts[0][1]]);
            f.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: line }, properties: {} });
        }
        (S ? S.pts : []).forEach((p, i) => f.push({
            type: 'Feature', geometry: { type: 'Point', coordinates: [p[0], p[1]] },
            properties: { i }
        }));
        m.getSource(SRC).setData({ type: 'FeatureCollection', features: f });
    }

    // ---------------------------------------------------------- estimate

    // Debounced because it fires on every vertex drag. 300 ms is below the
    // threshold where the number feels stale and well above the drag rate.
    let estTimer = null;
    function scheduleEstimate() {
        clearTimeout(estTimer);
        estTimer = setTimeout(fetchEstimate, 300);
    }

    // The analysis window is whatever the time slider currently says. This is
    // the single source of truth for "which dates" in the whole app
    // (window.dateFrom / window.dateTo, moved only by applyPreciseDateFilter).
    function currentWindow() {
        const f = window.dateFrom || '';
        const t = window.dateTo || '';
        return { from: f, to: t };
    }

    async function fetchEstimate() {
        const g = geometry();
        const box = document.getElementById('aoi-draw-estimate');
        if (!g || !box) return;
        const w = currentWindow();
        try {
            const r = await fetch(`/api/aois/estimate?pwd=${encodeURIComponent(pwd())}`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ geometry: g, from_date: w.from, to_date: w.to })
            });
            if (!r.ok) throw new Error('HTTP ' + r.status);
            S.estimate = await r.json();
            renderEstimate();
        } catch (e) {
            box.innerHTML = '<div class="aoi-draw-dim">Could not estimate cost.</div>';
        }
    }

    const DS_LABEL = {
        clip: 'Preview from nearby parks', fire_gap: 'Fire detections (FIRMS)',
        fire_v5: 'Fire trajectories', gfw: 'Forest-loss alerts',
        deforestation: 'Deforestation events (2024+)',
        hansen: 'Forest loss 2001–2023 (Hansen)', ghsl: 'Settlements',
        osm: 'Roads & places', gsw: 'Surface water', hydro: 'Rivers & lakes',
        basin: 'Watershed'
    };

    function fmtNum(n) {
        return (typeof formatNumber === 'function') ? formatNumber(n) : String(n);
    }

    function renderEstimate() {
        const box = document.getElementById('aoi-draw-estimate');
        if (!box || !S || !S.estimate) return;
        const e = S.estimate;
        const w = currentWindow();
        const rows = e.datasets.map(d => {
            const label = DS_LABEL[d.dataset] || d.dataset;
            if (d.blocked) {
                return `<div class="aoi-est-row blocked"><span>${label}</span>
                        <span class="aoi-est-note">not available yet</span></div>`;
            }
            const mins = d.seconds < 60 ? '<1 min' : Math.round(d.seconds / 60) + ' min';
            const units = d.units > 1 ? `${fmtNum(d.units)} × ` : '';
            return `<div class="aoi-est-row"><span>${label}</span>
                    <span class="aoi-est-note">${units}${mins}</span></div>`;
        }).join('');
        // The headline is the calendar time, not the CPU time: that is what
        // the user experiences, because the runner takes one slice a day.
        box.innerHTML = `
            <div class="aoi-est-head">
                <div class="aoi-est-eta">${escHtml(e.human || '')}</div>
                <div class="aoi-draw-dim">${fmtNum(Math.round(e.area_km2))} km² ·
                    ${fmtNum(e.firms_calls)} satellite requests ·
                    window ${escHtml(w.from || '…')} → ${escHtml(w.to || 'now')}</div>
            </div>
            <details class="aoi-est-details"><summary>What gets fetched</summary>
                ${rows}
                <div class="aoi-draw-dim" style="margin-top:6px">
                    The preview from neighbouring parks is ready almost immediately.
                    Everything else arrives layer by layer — the area stays usable
                    while it fills in.
                </div>
            </details>`;
    }

    function escHtml(s) {
        return (typeof escapeHtml === 'function') ? escapeHtml(s)
             : String(s == null ? '' : s).replace(/[&<>"']/g, c =>
                 ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    // ---------------------------------------------------------- interaction

    function nearestHandle(e) {
        if (!S) return -1;
        const m = theMap();
        for (let i = 0; i < S.pts.length; i++) {
            const p = m.project(S.pts[i]);
            if (Math.hypot(p.x - e.point.x, p.y - e.point.y) <= HANDLE_HIT_PX) return i;
        }
        return -1;
    }

    function onClick(e) {
        if (!S || S.dragging !== -1 || S.ringLocked) return;
        const hit = nearestHandle(e);
        // Clicking the first vertex closes the ring — the standard gesture,
        // and the only one that works on touch without a keyboard.
        if (hit === 0 && S.pts.length >= MIN_VERTICES) {
            S.closed = true;
            redraw();
            scheduleEstimate();
            updateHint();
            return;
        }
        if (hit > 0) return;   // a click on an existing handle is a no-op
        if (S.closed) return;
        S.pts.push([e.lngLat.lng, e.lngLat.lat]);
        redraw();
        if (S.pts.length >= MIN_VERTICES) scheduleEstimate();
        updateHint();
    }

    function onMouseMove(e) {
        if (!S || S.ringLocked) return;
        if (S.dragging !== -1) {
            S.pts[S.dragging] = [e.lngLat.lng, e.lngLat.lat];
            redraw();
            scheduleEstimate();
            return;
        }
        S.hover = [e.lngLat.lng, e.lngLat.lat];
        const over = nearestHandle(e) !== -1;
        theMap().getCanvas().style.cursor = over ? 'move' : 'crosshair';
        if (!S.closed && S.pts.length) redraw();
    }

    function onDown(e) {
        if (!S || S.ringLocked) return;
        const hit = nearestHandle(e);
        if (hit === -1) return;
        // Dragging a vertex must not also pan the map.
        S.dragging = hit;
        theMap().dragPan.disable();
        e.preventDefault && e.preventDefault();
    }

    function onUp() {
        if (!S || S.dragging === -1) return;
        S.dragging = -1;
        theMap().dragPan.enable();
        scheduleEstimate();
    }

    function onKey(ev) {
        if (!S) return;
        if (ev.key === 'Escape') { cancel(); }
        else if (ev.key === 'Enter' && S.pts.length >= MIN_VERTICES) {
            S.closed = true; redraw(); scheduleEstimate(); updateHint();
        } else if ((ev.key === 'Backspace' || ev.key === 'Delete') && S.pts.length) {
            if (ev.target && /input|textarea/i.test(ev.target.tagName)) return;
            ev.preventDefault();
            S.pts.pop(); S.closed = false; redraw(); scheduleEstimate(); updateHint();
        }
    }

    // The slider is the window. If the user moves it while the editor is
    // open, the price changes — so re-fetch rather than quoting a stale one.
    function onWindowChanged() {
        if (S && (S.pts.length >= MIN_VERTICES || S.ringLocked)) scheduleEstimate();
    }

    // ---------------------------------------------------------- panel

    function updateHint() {
        const el = document.getElementById('aoi-draw-hint-text');
        if (!el || !S) return;
        const editing = !!S.editID;
        if (S.ringLocked) el.textContent = 'This area has a complex shape. Move the time slider to change its dates, then save — the shape is kept exactly as it is.';
        else if (!S.pts.length) el.textContent = 'Click on the map to place the first corner.';
        else if (S.pts.length < MIN_VERTICES) el.textContent = `${S.pts.length} corner${S.pts.length > 1 ? 's' : ''} — at least ${MIN_VERTICES} needed.`;
        else if (!S.closed) el.textContent = 'Keep clicking, or click the amber first corner to close the shape.';
        else if (editing) el.textContent = 'Drag any corner to adjust, and move the time slider for a different window. Saving creates a new version and keeps the current one as history.';
        else el.textContent = 'Drag any corner to adjust. Name it below when you are happy.';
        const save = document.getElementById('aoi-draw-save');
        if (save) save.disabled = !(S.ringLocked || (S.closed && S.pts.length >= MIN_VERTICES));
        const est = document.getElementById('aoi-draw-estimate');
        if (est && S.pts.length < MIN_VERTICES && !S.ringLocked) {
            est.innerHTML = '<div class="aoi-draw-dim">Draw at least three corners to see how long this will take.</div>';
        }
    }

    function panelHTML() {
        const editing = !!(S && S.editID);
        return `
        <div class="aoi-draw-panel" id="aoi-draw-panel">
          <div class="aoi-draw-head">
            <span><i class="icon-pencil-ruler"></i> ${editing ? 'Edit area' : 'New area of interest'}</span>
            <button class="aoi-draw-x" onclick="AOIDraw.cancel()" aria-label="Cancel">×</button>
          </div>
          <div class="aoi-draw-hint" id="aoi-draw-hint-text">Click on the map to place the first corner.</div>
          <div class="aoi-draw-estimate" id="aoi-draw-estimate">
            <div class="aoi-draw-dim">Draw at least three corners to see how long this will take.</div>
          </div>
          <input class="aoi-draw-input" id="aoi-draw-name" maxlength="80"
                 value="${editing ? escHtml(S.editName) : ''}"
                 placeholder="Name this area (e.g. Chinko northern buffer)">
          <div class="aoi-draw-actions">
            <button class="aoi-draw-btn ghost" onclick="AOIDraw.undo()">Undo corner</button>
            <button class="aoi-draw-btn primary" id="aoi-draw-save" disabled
                    onclick="AOIDraw.save()">${editing ? 'Save as new version' : 'Create area'}</button>
          </div>
          <div class="aoi-draw-dim" style="margin-top:6px">
            ${editing
              ? 'Editing never overwrites: the current version is archived and stays reachable, with the data it already holds.'
              : 'The analysis window comes from the time slider. Move it to change which dates this area covers.'}
          </div>
        </div>`;
    }

    // ---------------------------------------------------------- lifecycle

    function start() {
        if (S) return;
        const m = theMap();
        if (!m) return;
        ensureLayers();
        S = { pts: [], closed: false, dragging: -1, hover: null, estimate: null };
        attach();
    }

    // Editing an existing AOI. Deliberately the *same* editor, prefilled: the
    // server turns this into a fork (a new version, the old one archived), so
    // from the user's side it is "draw the question again, slightly differently"
    // and it must feel like the create flow, including re-pricing live and
    // taking its window from the time slider.
    //
    // Only the outer ring is editable. A hole or a multipolygon would need a
    // ring selector that nothing in this app has ever needed; such an AOI can
    // still be edited window-only (leave the shape alone and press Save).
    function startEdit(id, name, geometry) {
        if (S) return;
        const m = theMap();
        if (!m) return;
        ensureLayers();
        let pts = [];
        let ringLocked = false;
        const g = geometry && (typeof geometry === 'string' ? JSON.parse(geometry) : geometry);
        if (g && g.type === 'Polygon' && g.coordinates && g.coordinates[0]) {
            pts = g.coordinates[0].slice(0, -1).map(c => [c[0], c[1]]);
            if (g.coordinates.length > 1) ringLocked = true;   // has holes
        } else if (g && g.type === 'MultiPolygon') {
            ringLocked = true;
        }
        S = { pts, closed: pts.length >= MIN_VERTICES, dragging: -1, hover: null,
              estimate: null, editID: id, editName: name || id,
              originalGeom: g, ringLocked };
        attach();
        if (S.pts.length >= MIN_VERTICES || S.ringLocked) scheduleEstimate();
        // Frame the shape being edited: an edit often starts from a share link
        // or a notification, with the map somewhere else entirely.
        if (pts.length) {
            const lons = pts.map(p => p[0]), lats = pts.map(p => p[1]);
            m.fitBounds([[Math.min(...lons), Math.min(...lats)],
                         [Math.max(...lons), Math.max(...lats)]],
                        { padding: 80, duration: 600 });
        }
    }

    // Shared tail of start()/startEdit(): panel, handlers, first paint.
    function attach() {
        const m = theMap();
        const host = document.createElement('div');
        host.innerHTML = panelHTML();
        document.body.appendChild(host.firstElementChild);

        m.getCanvas().style.cursor = 'crosshair';
        m.on('click', onClick);
        m.on('mousemove', onMouseMove);
        m.on('mousedown', onDown);
        m.on('mouseup', onUp);
        document.addEventListener('keydown', onKey);
        window.addEventListener('5mp:date-window-changed', onWindowChanged);
        redraw();
        updateHint();
    }

    function teardown() {
        const m = theMap();
        if (m) {
            m.off('click', onClick);
            m.off('mousemove', onMouseMove);
            m.off('mousedown', onDown);
            m.off('mouseup', onUp);
            m.getCanvas().style.cursor = '';
            m.dragPan.enable();
            if (m.getSource(SRC)) m.getSource(SRC).setData(empty());
        }
        document.removeEventListener('keydown', onKey);
        window.removeEventListener('5mp:date-window-changed', onWindowChanged);
        const p = document.getElementById('aoi-draw-panel');
        if (p) p.remove();
        clearTimeout(estTimer);
        S = null;
    }

    function cancel() { teardown(); }

    function undo() {
        if (!S || !S.pts.length || S.ringLocked) return;
        S.pts.pop(); S.closed = false; redraw(); scheduleEstimate(); updateHint();
    }

    async function save() {
        if (!S || !(S.closed || S.ringLocked)) return;
        const nameEl = document.getElementById('aoi-draw-name');
        const name = (nameEl && nameEl.value.trim()) || '';
        if (!name) { nameEl && nameEl.focus(); return; }
        const btn = document.getElementById('aoi-draw-save');
        const editing = !!S.editID;
        if (btn) { btn.disabled = true; btn.textContent = editing ? 'Saving…' : 'Creating…'; }
        const w = currentWindow();
        // An edit that did not move a vertex sends no geometry at all: the
        // server carries the old one forward, and "same shape, new dates" is
        // the common case because the window comes from the slider.
        const geomChanged = editing && !S.ringLocked && geometryDiffers();
        const url = editing
            ? `/api/aois/${encodeURIComponent(S.editID)}/edit?pwd=${encodeURIComponent(pwd())}`
            : `/api/aois?pwd=${encodeURIComponent(pwd())}`;
        const body = editing
            ? { name, from_date: w.from, to_date: w.to,
                ...(geomChanged ? { geometry: geometry() } : {}) }
            : { name, geometry: geometry(), from_date: w.from, to_date: w.to,
                visibility: 'private' };
        try {
            const r = await fetch(url, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            if (!r.ok) {
                const msg = await r.text();
                throw new Error(msg || ('HTTP ' + r.status));
            }
            const d = await r.json();
            teardown();
            // The AOI now exists but holds nothing yet. Reload the layer so the
            // polygon appears, and hand the user to the progress notification —
            // that card, not this dialog, is where the next few days live.
            if (typeof loadAOIs === 'function') await loadAOIs();
            if (d.unchanged) {
                if (typeof showToast === 'function') showToast('Nothing changed — no new version created', 'info');
                return;
            }
            if (window.AOIProgress && d.aoi) AOIProgress.track(d.aoi.id, d.estimate);
            if (typeof loadNotifications === 'function') loadNotifications();
            if (typeof showToast === 'function') {
                const eta = d.estimate && d.estimate.human || 'processing';
                showToast(editing ? `New version of “${name}” queued — ${eta}`
                                  : `“${name}” queued — ${eta}`, 'success');
            }
        } catch (e) {
            if (btn) { btn.disabled = false; btn.textContent = editing ? 'Save as new version' : 'Create area'; }
            const box = document.getElementById('aoi-draw-estimate');
            if (box) box.innerHTML = `<div class="aoi-draw-err">${escHtml(String(e.message || e))}</div>`;
        }
    }

    // Cheap structural comparison against the polygon we started the edit
    // with. Exact equality is the right test here: the coordinates came from
    // the server unmodified unless a handle was actually dragged.
    function geometryDiffers() {
        const before = S.originalGeom && S.originalGeom.coordinates &&
                       S.originalGeom.coordinates[0];
        if (!before) return true;
        const after = ring();
        if (!after || after.length !== before.length) return true;
        for (let i = 0; i < after.length; i++) {
            if (after[i][0] !== before[i][0] || after[i][1] !== before[i][1]) return true;
        }
        return false;
    }

    window.AOIDraw = { start, startEdit, cancel, undo, save, isActive: () => !!S };
})();
