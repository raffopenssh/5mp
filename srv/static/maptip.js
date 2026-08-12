/*
 * MapTip — one unified hover/tap tooltip for every interactive map layer.
 *
 * Why this exists: each pinned-layer code path used to create its own
 * maplibregl.Popup on 'mouseenter' and remove it on 'mouseleave' with a
 * timeout. With overlapping features (e.g. a park full of fire trajectories)
 * that produced a stack of half-dead popups that never went away, all anchored
 * at stale positions, and nothing at all on touch devices.
 *
 * MapTip instead keeps a SINGLE DOM tooltip, driven by one map-level
 * 'mousemove' that hit-tests all registered layers. Topmost feature wins.
 * On touch devices a tap opens the same tip in "sticky" mode (with a close
 * button and an optional action button).
 *
 * Usage:
 *   MapTip.init(map);
 *   MapTip.register(layerId, {
 *       html: (props, feature, e) => '<b>hi</b>',  // required; falsy = decline
 *       onActivate: (feature, e) => {...},       // optional (click / "Details")
 *       actionLabel: 'Open in report',           // optional button label
 *       priority: -20,                           // optional; see below
 *       clickOnly: true,                         // optional; see below
 *   });
 *   MapTip.unregister(layerId);
 *
 * PRECEDENCE — why render order is not enough.
 *
 * Draw order answers "what is on top", which is not the same question as
 * "what did the user mean". Two layers are *backdrops*: the AOI polygon
 * (485,000 km²) and the geology drape (a whole country). Both sit under the
 * cursor almost everywhere, so whichever happens to be drawn last would win
 * every hit-test and bury the specific thing the user was actually pointing
 * at. `priority` (default 0, higher wins, render order breaks ties) makes
 * that explicit: backdrops declare themselves negative and are only reached
 * when nothing more specific is there.
 *
 * `clickOnly` is the other half of the same idea. A backdrop that covers the
 * whole viewport must not emit a hover tip — there is no "off it" to move to,
 * so the tip would simply follow the cursor forever. Such a layer answers a
 * deliberate click. Use it sparingly: MapTip only ever shows ONE tip, so a
 * backdrop already loses to anything more specific under the cursor, and
 * `clickOnly` is only warranted when the layer owns the click too (the AOI
 * polygon, which opens its own popup). The geology drape does not: it is an
 * ordinary hover tip at priority -30.
 *
 * A CLICK OPENS THE REAL THING, NOT A COPY OF IT.
 *
 * Where a layer has an `onActivate`, a click on a fine pointer runs it — the
 * AOI opens its popup, a fire opens its report. A sticky tip must never stand
 * in for that: it reproduced the first three lines of the AOI popup without
 * the grab bar, the minimise button or the rest of the content, so clicking an
 * area got you less than it used to.
 *
 * ON A COARSE POINTER, THE TIP CARRIES EVERY ANSWER AT THE POINT AS TABS.
 *
 * There is no hover on a finger, so the one tap has to serve both "what is
 * this?" and "open it". The sticky tip answers the first (with the action
 * button for the second), and because a place genuinely holds several answers
 * — a fire path, over an area, over a geological unit — it lists them in
 * priority order with the winner selected. On a mouse those same answers are
 * reachable by moving the cursor, which is why the tabs are not needed there.
 */
(function () {
    'use strict';

    var CSS = `
.maptip {
    position: absolute; z-index: 400; pointer-events: none;
    max-width: 300px; min-width: 150px;
    background: rgba(16,16,16,0.96);
    border: 1px solid rgba(255,255,255,0.14);
    border-radius: 9px;
    box-shadow: 0 10px 34px rgba(0,0,0,0.6);
    padding: 9px 11px;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 12px; line-height: 1.5; color: #e5e7eb;
    opacity: 0; transform: translateY(3px);
    transition: opacity .12s ease, transform .12s ease;
    overflow-wrap: break-word;
}
.maptip.visible { opacity: 1; transform: translateY(0); }
.maptip.sticky { pointer-events: auto; border-color: rgba(255,255,255,0.24); }
.maptip-label {
    font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
    color: #9ca3af; margin-bottom: 6px; padding-right: 16px;
}
.maptip-title { font-weight: 600; font-size: 13px; color: #f3f4f6; margin-bottom: 4px; }
.maptip-body { color: #d1d5db; }
.maptip-meta { color: #9ca3af; font-size: 11px; margin-top: 4px; }
.maptip-dim { color: #6b7280; font-size: 10px; margin-top: 4px; }
.maptip-hot { color: #f87171; }
.maptip-warn { color: #fbbf24; }
.maptip-cool { color: #60a5fa; }
.maptip-more {
    margin-top: 6px; padding-top: 5px; border-top: 1px solid rgba(255,255,255,0.08);
    color: #6b7280; font-size: 10px;
}
/* Tabs: every answer at this point, in priority order. Only ever on a sticky
   (clicked/tapped) tip — a hover has one moving question and one answer. */
.maptip-tabs {
    display: none; gap: 4px; margin: -2px -3px 7px -3px; padding-bottom: 6px;
    border-bottom: 1px solid rgba(255,255,255,0.08);
    overflow-x: auto; scrollbar-width: none;
}
.maptip-tabs::-webkit-scrollbar { display: none; }
.maptip.sticky .maptip-tabs.available { display: flex; }
.maptip-tab {
    flex: 0 0 auto; display: flex; align-items: center; gap: 5px;
    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.10);
    color: #9ca3af; font: inherit; font-size: 10.5px; font-weight: 600;
    letter-spacing: .02em; padding: 4px 8px; border-radius: 999px; cursor: pointer;
    white-space: nowrap;
}
.maptip-tab:hover { color: #e5e7eb; background: rgba(255,255,255,0.10); }
.maptip-tab.active {
    color: #f3f4f6; background: rgba(255,255,255,0.16);
    border-color: rgba(255,255,255,0.28);
}
.maptip-tab .maptip-tab-dot {
    width: 7px; height: 7px; border-radius: 2px; flex: none;
    background: currentColor; opacity: .8;
}
.maptip-hint { color: #6b7280; font-size: 10px; margin-top: 6px; }
.maptip-close {
    position: absolute; top: 3px; right: 4px; width: 22px; height: 22px;
    display: none; align-items: center; justify-content: center;
    background: transparent; border: 0; color: #9ca3af; font-size: 16px;
    line-height: 1; cursor: pointer; border-radius: 4px;
}
.maptip.sticky .maptip-close { display: flex; }
.maptip-close:hover { color: #fff; background: rgba(255,255,255,0.1); }
.maptip-action {
    display: none; margin-top: 8px; width: 100%;
    background: rgba(34,197,94,0.14); border: 1px solid rgba(34,197,94,0.45);
    color: #86efac; font-size: 11px; font-weight: 600; padding: 6px 8px;
    border-radius: 6px; cursor: pointer;
}
.maptip.sticky .maptip-action.available { display: block; }
.maptip-action:active { background: rgba(34,197,94,0.28); }
/* Export row: a tapped (sticky) tip is the mobile equivalent of the popup
   header's button strip, and on a phone it is often the only thing the user
   opens. Hidden on hover tips, which have pointer-events:none, so we never
   show a button that cannot be clicked. */
.maptip-exports {
    display: none; gap: 6px; margin-top: 8px; padding-top: 7px;
    flex-wrap: wrap; border-top: 1px solid rgba(255,255,255,0.08);
}
.maptip.sticky .maptip-exports { display: flex; }
/* A .star-btn brings its own (deliberately unframed) styling — same control,
   same look as on a park popup. Everything else is a framed icon button. */
.maptip-exports > *:not(.star-btn) {
    flex: 1; display: flex; align-items: center; justify-content: center;
    min-height: 34px; background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.14); border-radius: 6px;
    color: #d1d5db; font-size: 14px; cursor: pointer; text-decoration: none;
}
.maptip-exports > *:active { background: rgba(255,255,255,0.16); }
.maptip-exports .star-btn { margin-left: auto; font-size: 16px; padding: 4px 2px; }
/* Legacy inline-styled tip bodies (turbidity etc.) were written for a white
   popup; remap their light-theme greys so they stay readable on dark. */
.maptip [style*="#374151"] { color: #d1d5db !important; }
.maptip [style*="#1f2937"] { color: #f3f4f6 !important; }
.maptip [style*="#6b7280"] { color: #9ca3af !important; }
.maptip [style*="#9ca3af"] { color: #8b8f96 !important; }
.maptip [style*="#2563eb"] { color: #60a5fa !important; }
.maptip [style*="#dc2626"] { color: #f87171 !important; }
.maptip [style*="#d97706"] { color: #fbbf24 !important; }
@media (max-width: 640px), (hover: none) {
    .maptip { max-width: min(84vw, 340px); font-size: 13px; padding: 11px 13px; }
    .maptip-close { width: 30px; height: 30px; font-size: 19px; }
    /* A tab is a tap target, not a label. */
    .maptip-tab { font-size: 11.5px; padding: 7px 11px; }
    .maptip-tabs { gap: 6px; padding-bottom: 8px; }
}
`;

    var map = null;
    var el = null, closeBtn = null, actionBtn = null, bodyEl = null, tabsEl = null;
    var registry = new Map();      // layerId -> opts
    // Probes are the same idea for things that are NOT MapLibre layers: the
    // animator draws to its own canvas, so `queryRenderedFeatures` cannot see
    // a single one of its trajectories. A probe answers "is there a feature at
    // this point?" from whatever arrays it already keeps, and its result then
    // competes in exactly the same priority ordering as a real layer.
    //
    // Deliberately not a second tooltip: a paused animation that grew its own
    // popup would be the pile-up this file was written to end, and it would
    // not know to stand down over a pinned fire or a park polygon.
    var probes = new Map();        // probeId -> {probe(e) -> {html, ...}|null, priority}
    // A predicate set by the app: "is there a more specific answer here that
    // MapTip does not own?" (a park polygon, which has its own popup and its
    // own click handler). Backdrop layers — the AOI, the geology drape — stand
    // down where it is true, so a click reaches the park handler underneath
    // instead of being swallowed by a country-sized fill. Each backdrop used to
    // implement this itself, which is why geology, added later, did not.
    var backdropGuard = null;
    var sticky = false;            // tap-opened tip that stays until dismissed
    var anchor = null;             // {lng, lat} used while sticky / after pan
    var current = null;            // {layerId, feature, opts}
    var hideTimer = null;
    var lastPoint = null;

    // Media queries lie in some environments (headless Chrome reports
    // `hover: none`), so don't gate hover on a static capability check.
    // Instead: mice generate mousemove -> transient tip; a click with no recent
    // mousemove is a tap -> sticky tip with a close button and action button.
    var lastMoveAt = 0;
    var TAP_WINDOW_MS = 400;
    function pointerIsCoarse(e) {
        var oe = e && e.originalEvent;
        if (oe && oe.pointerType) return oe.pointerType !== 'mouse';
        if (oe && oe.type && oe.type.indexOf('touch') === 0) return true;
        return (Date.now() - lastMoveAt) > TAP_WINDOW_MS;
    }

    function injectCSS() {
        if (document.getElementById('maptip-css')) return;
        var s = document.createElement('style');
        s.id = 'maptip-css';
        s.textContent = CSS;
        document.head.appendChild(s);
    }

    function build() {
        injectCSS();
        el = document.createElement('div');
        el.className = 'maptip';
        el.innerHTML =
            '<button class="maptip-close" aria-label="Close">&times;</button>' +
            '<div class="maptip-tabs"></div>' +
            '<div class="maptip-body"></div>' +
            '<button class="maptip-action"></button>';
        closeBtn = el.querySelector('.maptip-close');
        tabsEl = el.querySelector('.maptip-tabs');
        bodyEl = el.querySelector('.maptip-body');
        actionBtn = el.querySelector('.maptip-action');
        tabsEl.addEventListener('click', function (ev) {
            var b = ev.target.closest ? ev.target.closest('.maptip-tab') : null;
            if (!b) return;
            ev.stopPropagation();
            selectTab(parseInt(b.getAttribute('data-i'), 10) || 0);
        });
        closeBtn.addEventListener('click', function (ev) { ev.stopPropagation(); hide(true); });
        actionBtn.addEventListener('click', function (ev) {
            ev.stopPropagation();
            var c = current;
            hide(true);
            if (c && c.opts && typeof c.opts.onActivate === 'function') {
                try { c.opts.onActivate(c.feature, { lngLat: anchor }); } catch (e) { console.error(e); }
            }
        });
        // Keep wheel/drag over a sticky tip from being eaten silently
        el.addEventListener('touchstart', function (ev) { ev.stopPropagation(); }, { passive: true });
        (map.getContainer() || document.body).appendChild(el);
    }

    /** Layers that are registered AND currently on the map. */
    function liveLayers() {
        var out = [];
        registry.forEach(function (opts, id) {
            if (map.getLayer(id)) out.push(id);
        });
        return out;
    }

    function position(point) {
        if (!el) return;
        var pad = 12, gap = 14;
        var cw = map.getContainer().clientWidth;
        var ch = map.getContainer().clientHeight;
        var w = el.offsetWidth, h = el.offsetHeight;
        var x = point.x + gap, y = point.y + gap;
        if (x + w + pad > cw) x = point.x - w - gap;
        if (x < pad) x = Math.min(pad, Math.max(0, cw - w - pad));
        if (y + h + pad > ch) y = point.y - h - gap;
        if (y < pad) y = pad;
        el.style.left = Math.round(x) + 'px';
        el.style.top = Math.round(y) + 'px';
    }

    // The answers at the anchored point, and which one is being read. Only
    // meaningful while sticky.
    var stack = null, stackIdx = 0;

    function renderTabs() {
        if (!tabsEl) return;
        if (!sticky || !stack || stack.length < 2) {
            tabsEl.classList.remove('available');
            tabsEl.innerHTML = '';
            return;
        }
        tabsEl.innerHTML = stack.map(function (t, i) {
            return '<button class="maptip-tab' + (i === stackIdx ? ' active' : '') +
                   '" data-i="' + i + '">' +
                   (t.opts && t.opts.tabColor
                        ? '<span class="maptip-tab-dot" style="background:' + t.opts.tabColor + '"></span>'
                        : '') +
                   escapeTab(t.tab) + '</button>';
        }).join('');
        tabsEl.classList.add('available');
    }

    function escapeTab(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
        });
    }

    function selectTab(i) {
        if (!stack || !stack[i]) return;
        stackIdx = i;
        var t = stack[i];
        show(t.html, lastPoint, anchor, t.extra, t.opts, t.feature, t.layerId);
    }

    function show(html, point, lngLat, extra, opts, feature, layerId) {
        if (!el) build();
        if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
        current = { layerId: layerId, feature: feature, opts: opts, extra: extra };
        anchor = lngLat ? { lng: lngLat.lng, lat: lngLat.lat } : anchor;
        lastPoint = point;
        var more = extra > 0
            ? '<div class="maptip-more">+' + extra + ' more feature' + (extra > 1 ? 's' : '') + ' here — zoom in to separate</div>'
            : '';
        bodyEl.innerHTML = html + more;
        renderTabs();
        var label = opts && opts.actionLabel ? opts.actionLabel : 'Open in report';
        if (opts && typeof opts.onActivate === 'function') {
            actionBtn.textContent = label;
            actionBtn.classList.add('available');
        } else {
            actionBtn.classList.remove('available');
        }
        el.classList.add('visible');
        position(point);
    }

    function hide(immediate) {
        if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
        sticky = false;
        current = null;
        stack = null; stackIdx = 0;
        if (!el) return;
        el.classList.remove('sticky');
        if (tabsEl) { tabsEl.classList.remove('available'); tabsEl.innerHTML = ''; }
        if (immediate) {
            el.classList.remove('visible');
        } else {
            hideTimer = setTimeout(function () {
                el.classList.remove('visible');
                hideTimer = null;
            }, 70);
        }
    }

    /**
     * Every answer at this point, best first.
     *
     * `all` = false stops at the winner (a hover asks one question). `all` =
     * true keeps going, which is what a click gets: see the header note — a
     * place holds several answers and losing the priority contest must not
     * mean being unreachable.
     */
    function tipsFor(e, forClick, all) {
        var cand = [];
        var layers = liveLayers().filter(function (id) {
            var o = registry.get(id);
            return forClick || !(o && o.clickOnly);
        });
        if (layers.length) {
            var feats;
            try {
                feats = map.queryRenderedFeatures(e.point, { layers: layers });
            } catch (err) { feats = null; }
            // Topmost-first, but backdrops last regardless of where they are
            // drawn. Stable sort on (priority desc), so within one priority the
            // map's own render order still decides.
            (feats || []).forEach(function (f, i) {
                var o = registry.get(f.layer && f.layer.id);
                cand.push({ f: f, opts: o, i: i,
                            pri: (o && typeof o.priority === 'number') ? o.priority : 0 });
            });
        }
        // Canvas-drawn features join the same ordering. They come after the
        // real layers at equal priority, which is right: a pinned vector the
        // user deliberately put there outranks an animation frame.
        probes.forEach(function (p, id) {
            if (!forClick && p.clickOnly) return;
            var hit;
            try { hit = p.probe(e); } catch (err) { hit = null; }
            if (!hit) return;
            cand.push({
                f: hit.feature || { properties: hit.properties || {}, layer: { id: id } },
                opts: { html: function () { return hit.html; },
                        onActivate: hit.onActivate || p.onActivate,
                        actionLabel: hit.actionLabel || p.actionLabel,
                        priority: p.priority, clickOnly: p.clickOnly },
                i: cand.length,
                pri: (typeof p.priority === 'number') ? p.priority : 0,
                probeId: id
            });
        });
        cand = cand.filter(function (c) { return c.opts && typeof c.opts.html === 'function'; });
        if (!cand.length) return null;
        cand.sort(function (a, b) { return (b.pri - a.pri) || (a.i - b.i); });
        var guardBlocked = null;   // evaluated at most once per event
        var out = [];
        var seen = {};             // one row per layer: 12 fire paths are one answer
        for (var i = 0; i < cand.length; i++) {
            var f = cand[i].f, opts = cand[i].opts;
            if (cand[i].pri < 0 && backdropGuard) {
                if (guardBlocked === null) {
                    try { guardBlocked = !!backdropGuard(e); } catch (err) { guardBlocked = false; }
                }
                if (guardBlocked) continue;
            }
            var layerId = cand[i].probeId || (f.layer && f.layer.id);
            if (all && seen[layerId]) continue;
            var html;
            try { html = opts.html(f.properties || {}, f, e); } catch (err) { html = null; }
            // A registration may decline a feature by returning falsy — the
            // loop then falls through to whatever is underneath, and if
            // nothing renders there is no tip AND no click interception. The
            // AOI layer uses this to stand down over a park polygon, which is
            // the same precedence rule the park click handler applies.
            if (!html) continue;
            seen[layerId] = true;
            // "+N more here" means "there are other features of THIS KIND
            // under the cursor that you would have to zoom in to tell apart".
            //
            // Three things it must not count. Other LAYERS — a geology unit
            // under a fire trajectory is a different question, and it is now a
            // tab, i.e. already reachable, so advising a zoom would be wrong
            // twice over. Other PARTS of the same feature: MapLibre returns
            // one result per tile per part, so a multipolygon AOI reported
            // "+3 more features here" about itself and a geology unit — one
            // dissolved multipart per class — reported "+96". And repeats of
            // an identical feature across tile seams.
            //
            // Identity is the feature id when there is one, and otherwise the
            // properties themselves: a vector tile carrying no id is exactly
            // the case where two results with identical attributes ARE one
            // thing (that is how the source dissolved them).
            var extra = 0, mine = {};
            mine[featureKey(f)] = true;
            // A backdrop opts out entirely (`peers: false`). "Zoom in to
            // separate" is advice for picking between things you were trying
            // to click; nobody clicks a country-sized drape to choose one of
            // its 17 units, and at low zoom the count is an artefact of tile
            // simplification (overlapping simplified rings) rather than of the
            // map. The tab already says the layer is there.
            if (opts.peers !== false) {
                for (var j = 0; j < cand.length; j++) {
                    if (j === i) continue;
                    var of = cand[j].f;
                    if ((cand[j].probeId || (of.layer && of.layer.id)) !== layerId) continue;
                    var k = featureKey(of);
                    if (mine[k]) continue;
                    mine[k] = true;
                    extra++;
                }
            }
            out.push({ html: html, opts: opts, feature: f,
                       layerId: layerId, extra: extra,
                       tab: tabLabelFor(opts, f, layerId) });
            if (!all) break;
        }
        return out.length ? out : null;
    }

    // See the "+N more here" note in tipsFor().
    function featureKey(f) {
        if (!f) return '?';
        var p = f.properties || {};
        if (f.id != null) return 'i:' + f.id;
        if (p.rid != null) return 'r:' + p.rid;
        if (p.feature_id != null) return 'f:' + p.feature_id;
        if (p.id != null) return 'p:' + p.id;
        try { return 'j:' + JSON.stringify(p); } catch (err) { return 'x:' + Math.random(); }
    }

    function tipFor(e, forClick) {
        var t = tipsFor(e, forClick, false);
        return t ? t[0] : null;
    }

    // What a tab is called. A registration says so (`tabLabel`, string or
    // function); otherwise fall back to something derived from the layer id,
    // because an unnamed tab is worse than no tab.
    function tabLabelFor(opts, f, layerId) {
        var l = opts && opts.tabLabel;
        if (typeof l === 'function') {
            try { l = l((f && f.properties) || {}, f); } catch (err) { l = null; }
        }
        if (l) return String(l);
        var id = String(layerId || '');
        if (id.indexOf('geomap-') === 0) return 'Geology';
        if (id.indexOf('aois') === 0) return 'Area';
        return id.replace(/^lod-|-(fill|line|circle|point|arrow|front|glow)$/g, '')
                 .replace(/[-_:]+/g, ' ').trim() || 'Feature';
    }

    function onMouseMove(e) {
        lastMoveAt = Date.now();
        if (sticky) return;
        var t = tipFor(e);
        if (!t) {
            if (el && el.classList.contains('visible')) hide(false);
            map.getCanvas().style.cursor = '';
            return;
        }
        map.getCanvas().style.cursor = 'pointer';
        show(t.html, e.point, e.lngLat, t.extra, t.opts, t.feature, t.layerId);
    }

    function onClick(e) {
        // Only a coarse pointer needs every answer: a mouse can hover for
        // them, and a click there means "open this".
        var coarse = pointerIsCoarse(e);
        var all = tipsFor(e, true, coarse);
        if (!all) {
            if (sticky) hide(true);
            return;
        }
        var t = all[0];
        if (e.originalEvent) e.originalEvent.stopPropagation();
        // Tell the park-polygon click handler to stand down: a tap on a pinned
        // feature must not also open the park report popup underneath.
        window._mapTipClicked = true;
        setTimeout(function () { window._mapTipClicked = false; }, 60);
        // A click on a fine pointer does the DIRECT thing whenever the layer
        // has one, backdrop or not. That is what a click on an area has always
        // meant: it opens the area's popup — with its grab bar, its minimise
        // button and its full content — not a card that reproduces the first
        // three lines of it. `clickOnly` is about not emitting a HOVER tip; it
        // was never meant to turn a click into a tooltip.
        //
        // The tabbed sticky tip is for a coarse pointer, where there is no
        // hover at all and a tap is the only way to reach anything underneath.
        if (!coarse && typeof t.opts.onActivate === 'function') {
            hide(true);
            try { t.opts.onActivate(t.feature, e); } catch (err) { console.error(err); }
            return;
        }
        // A mouse click on a layer with nothing to open (geology, and any
        // other purely informative layer) leaves the HOVER tip exactly as it
        // is. Freezing it into a sticky card with a close button would demand
        // a dismissal for something the user dismisses by moving the mouse —
        // and it is how a click on the rock map ended up feeling heavier than
        // a click on a fire.
        if (!coarse) {
            // ...and if a previous tap left one on screen, this click dismisses
            // it, so the sticky state cannot outlive the thing it described.
            if (sticky) { sticky = false; el.classList.remove('sticky'); renderTabs(); }
            return;
        }
        anchor = e.lngLat ? { lng: e.lngLat.lng, lat: e.lngLat.lat } : anchor;
        lastPoint = e.point;
        if (!el) build();
        sticky = true;
        stack = all; stackIdx = 0;
        el.classList.add('sticky');
        show(t.html, e.point, e.lngLat, t.extra, t.opts, t.feature, t.layerId);
        position(e.point);
    }

    function onMapMove() {
        if (!el || !el.classList.contains('visible')) return;
        if (sticky && anchor) {
            position(map.project([anchor.lng, anchor.lat]));
        } else {
            hide(true);   // cursor-anchored tips are meaningless once the map moves
        }
    }

    var MapTip = {
        init: function (m) {
            if (map) return;
            map = m;
            build();
            map.on('mousemove', onMouseMove);
            map.on('click', onClick);
            map.on('move', onMapMove);
            map.on('mouseout', function () { if (!sticky) hide(false); });
            document.addEventListener('keydown', function (ev) {
                if (ev.key === 'Escape') hide(true);
            });
        },
        register: function (layerId, opts) {
            if (!layerId || !opts || typeof opts.html !== 'function') return;
            registry.set(layerId, opts);
        },
        unregister: function (layerId) {
            registry.delete(layerId);
            if (current && current.layerId === layerId) hide(true);
        },
        unregisterPrefix: function (prefix) {
            Array.from(registry.keys()).forEach(function (id) {
                if (id.indexOf(prefix) === 0) MapTip.unregister(id);
            });
        },
        /**
         * A non-MapLibre source of features (the animator's canvas). `probe(e)`
         * returns {html, properties?, onActivate?} or null; the result joins
         * the same priority arbitration as a registered layer.
         */
        registerProbe: function (id, opts) {
            if (!id || !opts || typeof opts.probe !== 'function') return;
            probes.set(id, opts);
        },
        unregisterProbe: function (id) {
            probes.delete(id);
            if (current && current.layerId === id) hide(true);
        },
        setBackdropGuard: function (fn) { backdropGuard = fn; },
        hide: function () { hide(true); },
        isSticky: function () { return !!sticky; },
        /** Re-render the tip currently on screen (an async detail arrived). */
        refresh: function (layerId) {
            if (!current || !el || !el.classList.contains('visible')) return;
            if (layerId && current.layerId !== layerId) return;
            var html;
            try {
                html = current.opts.html(current.feature.properties || {}, current.feature,
                                         { point: lastPoint, lngLat: anchor });
            } catch (err) { return; }
            if (!html) return;
            // Keep the stack in step, or switching away and back shows the
            // stale placeholder the async detail just replaced.
            if (stack && stack[stackIdx]) stack[stackIdx].html = html;
            show(html, lastPoint, anchor, current.extra, current.opts,
                 current.feature, current.layerId);
        },
        isTouch: function () { return (Date.now() - lastMoveAt) > TAP_WINDOW_MS; },
        count: function () { return registry.size; }
    };

    window.MapTip = MapTip;
})();
