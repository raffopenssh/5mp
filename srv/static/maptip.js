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
 * deliberate click, and answers it as a sticky tip (with a close button) even
 * for a mouse.
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
}
`;

    var map = null;
    var el = null, closeBtn = null, actionBtn = null, bodyEl = null;
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
            '<div class="maptip-body"></div>' +
            '<button class="maptip-action"></button>';
        closeBtn = el.querySelector('.maptip-close');
        bodyEl = el.querySelector('.maptip-body');
        actionBtn = el.querySelector('.maptip-action');
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
        if (!el) return;
        el.classList.remove('sticky');
        if (immediate) {
            el.classList.remove('visible');
        } else {
            hideTimer = setTimeout(function () {
                el.classList.remove('visible');
                hideTimer = null;
            }, 70);
        }
    }

    function tipFor(e, forClick) {
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
        for (var i = 0; i < cand.length; i++) {
            var f = cand[i].f, opts = cand[i].opts;
            if (cand[i].pri < 0 && backdropGuard) {
                var blocked = false;
                try { blocked = !!backdropGuard(e); } catch (err) { blocked = false; }
                if (blocked) continue;
            }
            var html;
            try { html = opts.html(f.properties || {}, f, e); } catch (err) { html = null; }
            // A registration may decline a feature by returning falsy — the
            // loop then falls through to whatever is underneath, and if
            // nothing renders there is no tip AND no click interception. The
            // AOI layer uses this to stand down over a park polygon, which is
            // the same precedence rule the park click handler applies.
            if (!html) continue;
            // "+N more here" counts only peers, not the backdrop the feature
            // happens to sit on: an AOI or a geology unit under a fire
            // trajectory is context, not a second thing to zoom in and separate.
            var extra = 0;
            for (var j = 0; j < cand.length; j++) {
                if (j !== i && cand[j].pri >= cand[i].pri) extra++;
            }
            return { html: html, opts: opts, feature: f,
                     layerId: cand[i].probeId || f.layer.id, extra: extra };
        }
        return null;
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
        var t = tipFor(e, true);
        if (!t) {
            if (sticky) hide(true);
            return;
        }
        if (e.originalEvent) e.originalEvent.stopPropagation();
        // Tell the park-polygon click handler to stand down: a tap on a pinned
        // feature must not also open the park report popup underneath.
        window._mapTipClicked = true;
        setTimeout(function () { window._mapTipClicked = false; }, 60);
        if (t.opts.clickOnly || pointerIsCoarse(e)) {
            // Tap = show the tip; the tip itself carries the "open report" action.
            show(t.html, e.point, e.lngLat, t.extra, t.opts, t.feature, t.layerId);
            sticky = true;
            el.classList.add('sticky');
            position(e.point);
        } else if (typeof t.opts.onActivate === 'function') {
            hide(true);
            try { t.opts.onActivate(t.feature, e); } catch (err) { console.error(err); }
        }
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
            if (html) show(html, lastPoint, anchor, current.extra, current.opts,
                           current.feature, current.layerId);
        },
        isTouch: function () { return (Date.now() - lastMoveAt) > TAP_WINDOW_MS; },
        count: function () { return registry.size; }
    };

    window.MapTip = MapTip;
})();
