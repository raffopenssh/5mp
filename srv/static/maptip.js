/*
 * MapTip — one hover/tap tooltip and one SELECTION for every interactive map
 * layer (registered MapLibre layers and canvas-drawn "probes" alike).
 *
 * ---------------------------------------------------------------------------
 * THE MODEL: hover previews, a click SELECTS, the selection is a place.
 * ---------------------------------------------------------------------------
 *
 * This is the interaction every map application the user already knows shares,
 * and none of it is ours to invent:
 *
 *   Google/Apple Maps — tapping a POI drops a card that STAYS. It survives
 *   panning and zooming because it is anchored to the ground, not to the
 *   cursor; you dismiss it with its × or by tapping the empty map; tapping the
 *   map where there is no POI still answers, with the area/address you hit.
 *   Nothing about that behaviour differs between their desktop and their phone
 *   builds — the mouse merely adds a hover preview on top of it.
 *
 *   Illustrator / Figma / CAD — clicking a stack of overlapping shapes selects
 *   the topmost, and there is always a way DOWN through the stack (alt-click,
 *   right-click ▸ "Select behind", the layers list). Overlap is never allowed
 *   to make a shape unreachable. We show the stack instead of hiding it behind
 *   a modifier key, because a list is discoverable and a modifier is not.
 *
 * So, for every layer, on every pointer:
 *
 *   HOVER (mouse only)   transient preview at the cursor. Answers "what is
 *                        this?" while you are still looking for it.
 *   CLICK / TAP          SELECTS the feature: the tip pins itself to the
 *                        ground at that point, gains a × and, where the layer
 *                        has something to open, its action button. Hover keeps
 *                        working around it — a selection you cannot look past
 *                        is a modal dialog wearing a tooltip's clothes.
 *   CLICK THE SAME AGAIN unselects. (A selection toggles; nothing else does.)
 *   CLICK THE VOID       clears the selection. But "void" means nothing
 *                        answered — a click inside a big polygon is a click on
 *                        that polygon, and it selects it, which is how you
 *                        reach the AOI or the geology unit under everything.
 *   DOUBLE-CLICK / ⏎     skips the card and opens the real thing (report,
 *                        area popup). The card's action button does the same;
 *                        the double-click is the shortcut for people who
 *                        already know what they clicked.
 *   ESC                  clears the selection.
 *
 * TABS ARE THE STACK, AND THEY ARE NOT MOBILE-ONLY. A point on this map
 * genuinely holds several answers — a fire path, over a protected area, over
 * an area of interest, over a geological unit. They used to be listed only on
 * a coarse pointer, on the theory that a mouse can hover for the others; it
 * cannot, because hovering the same pixel yields the same winner every time.
 * The pinned card lists every answer at the point in priority order with the
 * winner selected, on both pointers. That is the "select behind" affordance,
 * made visible.
 *
 * WHY PINNING DOES NOT REPLACE THE REAL THING. An earlier version made a click
 * open the popup/report directly, because a sticky tip reproducing the first
 * three lines of the AOI popup gave you LESS than a click used to. Both are
 * true at once and the answer is not to choose: the click selects, and the
 * card carries the button that opens the full thing (with a double-click as
 * the express lane). That is exactly the Google Maps shape — the card is not
 * the place page, it is how you get to it — and it is what makes one gesture
 * mean one thing on a mouse and on a finger.
 *
 * THE SELECTION IS PART OF THE VIEW, SO IT IS IN THE SHARE LINK.
 * `?tip=<lng>,<lat>` (plus `tip_layer=` to disambiguate the stack). It is a
 * PLACE, not a feature id: ids are not stable across a rebuild, the layer may
 * be re-fetched at a different level of detail, and "what is at this point"
 * is the question the link is trying to reproduce anyway. Restoring re-asks
 * the map once the layers the link also carries have landed.
 *
 * ---------------------------------------------------------------------------
 * PRECEDENCE — draw order is not intent.
 * ---------------------------------------------------------------------------
 *
 * `priority` (default 0, higher wins, render order breaks ties). Two layers
 * are *backdrops* and declare themselves negative: the AOI polygon (485,000
 * km²) and the geology drape (a whole country). Both sit under the cursor
 * almost everywhere, so whichever happened to be drawn last would win every
 * hit-test and bury the specific thing the user was pointing at.
 *
 * `clickOnly` suppresses only the HOVER tip, for a layer that covers the whole
 * viewport (there is no "off it" to move to, so the tip would follow the
 * cursor forever). It has never meant "do not answer a click" — a click on a
 * backdrop is precisely how you reach it.
 *
 * `MapTip.setBackdropGuard(fn)` — "is there a more specific answer here that
 * MapTip does not own?" (a park polygon, which has its own popup and click
 * handler). Stated once by the app, so every backdrop stands down over a park
 * rather than each re-implementing it (which is how geology, added later,
 * did not).
 *
 * Usage:
 *   MapTip.init(map);
 *   MapTip.register(layerId, {
 *       html: (props, feature, e) => '<b>hi</b>',  // required; falsy = decline
 *       onActivate: (feature, e) => {...},         // optional (button / dblclick)
 *       actionLabel: 'Open park overview',       // string or (feature) => string
 *       tabLabel: 'Fire', tabColor: '#f87171',
 *       priority: -20, clickOnly: true, peers: false,
 *       activateOnClick: true,   // full surface IS the selection (see onClick)
 *   });
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
/* The pinned card is the selection: brighter frame, and it accepts the pointer
   (its tabs, its action button and its × are all real controls). The hover
   preview never does — a tooltip you can hit is a tooltip that steals clicks. */
.maptip.sticky {
    pointer-events: auto; z-index: 401;
    border-color: rgba(255,255,255,0.30);
    box-shadow: 0 12px 40px rgba(0,0,0,0.7), 0 0 0 1px rgba(255,255,255,0.05);
}
/* A hover preview standing next to a pinned card must read as the lesser of
   the two, or the user cannot tell which one they have selected. */
.maptip.hover { opacity: 0; }
.maptip.hover.visible { opacity: .97; }
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
/* The anchor: a selection pinned to the ground has to SHOW the ground it is
   pinned to, or panning turns it into a card floating over nothing. Same
   reason Maps drops a marker. Not a control — it never takes a click. */
.maptip-anchor {
    position: absolute; z-index: 399; pointer-events: none;
    width: 13px; height: 13px; margin: -7px 0 0 -7px;
    border-radius: 50%; border: 2px solid rgba(255,255,255,0.9);
    background: rgba(255,255,255,0.18);
    box-shadow: 0 0 0 1px rgba(0,0,0,0.55), 0 2px 8px rgba(0,0,0,0.6);
    opacity: 0; transition: opacity .12s ease;
}
.maptip-anchor.visible { opacity: 1; }
/* Tabs: every answer at this point, in priority order. Only on the pinned
   card — a hover has one moving question and one answer. */
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
/* Export row: the pinned card is the mobile equivalent of the popup header's
   button strip, and on a phone it is often the only thing the user opens.
   Hidden on the hover preview, which has pointer-events:none, so we never
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
/* On a phone the pinned card DOCKS to the bottom instead of covering the thing
   it describes — the finger is already there, the card is up to 340px wide and
   the tapped feature is under it. The anchor dot stays on the feature, so the
   link between the two is still visible. This is the bottom sheet every mobile
   map uses, and it is why the same card can safely be bigger here than the
   hover preview ever is. */
@media (max-width: 640px) {
    .maptip.sticky.docked {
        left: 8px !important; right: 8px !important;
        top: auto !important; max-width: none; width: auto;
        /* bottom is set from bottomChromePx() -- the toolbar and the time
           slider are measured, because both change height on a narrow screen. */
        max-height: 46vh; overflow-y: auto;
    }
}
`;

    var map = null;
    var pinEl = null, hovEl = null, anchorEl = null;
    var closeBtn = null, actionBtn = null, pinBody = null, tabsEl = null, hovBody = null;
    var registry = new Map();      // layerId -> opts
    // Probes are the same idea for things that are NOT MapLibre layers: the
    // animator draws to its own canvas, so `queryRenderedFeatures` cannot see
    // a single one of its trajectories. A probe answers "is there a feature at
    // this point?" from whatever arrays it already keeps, and its result then
    // competes in exactly the same priority ordering as a real layer.
    var probes = new Map();        // probeId -> {probe(e) -> {html, ...}|null, priority}
    var backdropGuard = null;

    // The two states, deliberately separate objects rather than one tip that
    // changes mode. A selection that is erased by moving the mouse is not a
    // selection, and a preview that has to be dismissed is not a preview.
    var pin = null;    // {stack, idx, anchor:{lng,lat}, key}
    var hov = null;    // {tip, key}
    var hoverHideTimer = null;

    // Media queries lie in some environments (headless Chrome reports
    // `hover: none`), so don't gate behaviour on a static capability check.
    // Mice generate mousemove; a click with no recent mousemove is a tap.
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
        var host = map.getContainer() || document.body;

        anchorEl = document.createElement('div');
        anchorEl.className = 'maptip-anchor';
        host.appendChild(anchorEl);

        pinEl = document.createElement('div');
        pinEl.className = 'maptip sticky';
        pinEl.innerHTML =
            '<button class="maptip-close" aria-label="Close">&times;</button>' +
            '<div class="maptip-tabs"></div>' +
            '<div class="maptip-body"></div>' +
            '<button class="maptip-action"></button>';
        closeBtn = pinEl.querySelector('.maptip-close');
        tabsEl = pinEl.querySelector('.maptip-tabs');
        pinBody = pinEl.querySelector('.maptip-body');
        actionBtn = pinEl.querySelector('.maptip-action');
        host.appendChild(pinEl);

        hovEl = document.createElement('div');
        hovEl.className = 'maptip hover';
        hovEl.innerHTML = '<div class="maptip-body"></div>';
        hovBody = hovEl.querySelector('.maptip-body');
        host.appendChild(hovEl);

        tabsEl.addEventListener('click', function (ev) {
            var b = ev.target.closest ? ev.target.closest('.maptip-tab') : null;
            if (!b) return;
            ev.stopPropagation();
            selectTab(parseInt(b.getAttribute('data-i'), 10) || 0);
        });
        closeBtn.addEventListener('click', function (ev) { ev.stopPropagation(); unpin(); });
        actionBtn.addEventListener('click', function (ev) {
            ev.stopPropagation();
            activateCurrent();
        });
        // A click anywhere on the card must not fall through to the map and
        // clear the very selection it belongs to.
        pinEl.addEventListener('click', function (ev) { ev.stopPropagation(); });
        pinEl.addEventListener('touchstart', function (ev) { ev.stopPropagation(); }, { passive: true });
    }

    /** Layers that are registered AND currently on the map. */
    function liveLayers() {
        var out = [];
        registry.forEach(function (opts, id) {
            if (map.getLayer(id)) out.push(id);
        });
        return out;
    }

    // What the docked card must sit above. The map's bottom-left toolbar and
    // the time slider are permanent chrome on a phone, so a card docked at a
    // hard-coded `bottom: 10px` lands ON the filter/search buttons and the
    // slider's play control — measured, not assumed, because both grow (the
    // slider's preset tags wrap on a narrow screen).
    function bottomChromePx() {
        var host = map.getContainer();
        var hh = host.clientHeight, hr = host.getBoundingClientRect();
        var top = hh;
        ['#map-toolbar', '.map-toolbar', '#time-slider-container'].forEach(function (sel) {
            var n = document.querySelector(sel);
            // NB: not `offsetParent` — it is null for a POSITION:FIXED element,
            // which is exactly what the toolbar and the slider are, so the
            // guard silently skipped both and the card docked onto them.
            if (!n || getComputedStyle(n).display === 'none') return;
            var r = n.getBoundingClientRect();
            if (!r.height || r.bottom < hr.top + hh * 0.55) return;   // not bottom chrome
            top = Math.min(top, r.top - hr.top);
        });
        return Math.max(10, hh - top + 10);
    }

    function position(el, point, docked) {
        if (!el) return;
        if (docked && el.classList.contains('docked')) {
            el.style.left = el.style.top = '';
            el.style.bottom = bottomChromePx() + 'px';
            return;
        }
        el.style.bottom = '';
        var pad = 12, gap = 14;
        var cw = map.getContainer().clientWidth;
        var ch = map.getContainer().clientHeight;
        var w = el.offsetWidth, h = el.offsetHeight;
        var x = point.x + gap, y = point.y + gap;
        if (x + w + pad > cw) x = point.x - w - gap;
        if (x < pad) x = Math.min(pad, Math.max(0, cw - w - pad));
        if (y + h + pad > ch) y = point.y - h - gap;
        if (y < pad) y = pad;
        // A preview drawn over the selection reads as one broken card with two
        // sets of text through each other, not as two things. The selection is
        // the fixed one (the user put it there), so the preview moves: try the
        // other side of the cursor, then above/below, and only overlap if the
        // container has nowhere left.
        if (el === hovEl && pin && pinEl && pinEl.classList.contains('visible')) {
            var m = 8;   // breathing room; two cards edge-to-edge read as one
            var pr = { x: pinEl.offsetLeft - m, y: pinEl.offsetTop - m,
                       w: pinEl.offsetWidth + 2 * m, h: pinEl.offsetHeight + 2 * m };
            var hits = function (ax, ay) {
                return ax < pr.x + pr.w && ax + w > pr.x && ay < pr.y + pr.h && ay + h > pr.y;
            };
            if (hits(x, y)) {
                var alts = [
                    [point.x - w - gap, y], [x, point.y - h - gap],
                    [point.x - w - gap, point.y - h - gap],
                    [pr.x - w - gap, y], [pr.x + pr.w + gap, y],
                    [x, pr.y + pr.h + gap], [x, pr.y - h - gap]
                ];
                for (var i = 0; i < alts.length; i++) {
                    var ax = alts[i][0], ay = alts[i][1];
                    if (ax < pad || ay < pad || ax + w + pad > cw || ay + h + pad > ch) continue;
                    if (hits(ax, ay)) continue;
                    x = ax; y = ay;
                    break;
                }
            }
        }
        el.style.left = Math.round(x) + 'px';
        el.style.top = Math.round(y) + 'px';
    }

    // ---- the pinned selection -------------------------------------------

    function renderTabs() {
        if (!tabsEl) return;
        if (!pin || !pin.stack || pin.stack.length < 2) {
            tabsEl.classList.remove('available');
            tabsEl.innerHTML = '';
            return;
        }
        tabsEl.innerHTML = pin.stack.map(function (t, i) {
            return '<button class="maptip-tab' + (i === pin.idx ? ' active' : '') +
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
        if (!pin || !pin.stack[i]) return;
        pin.idx = i;
        drawPin();
    }

    function moreHTML(extra) {
        return extra > 0
            ? '<div class="maptip-more">+' + extra + ' more feature' + (extra > 1 ? 's' : '') +
              ' here — zoom in to separate</div>'
            : '';
    }

    function drawPin() {
        if (!pin) return;
        var t = pin.stack[pin.idx];
        pinBody.innerHTML = t.html + moreHTML(t.extra);
        renderTabs();
        // The button names its DESTINATION, and a layer may only know which
        // one that is per feature (a park's overview or an area's), so the
        // label may be a function. Never a generic verb: "Open in report"
        // pointed at a popup that is not the report.
        var label = t.opts && t.opts.actionLabel;
        if (typeof label === 'function') {
            try { label = label(t.feature, (t.feature && t.feature.properties) || {}); }
            catch (e) { label = null; }
        }
        label = label || 'Open overview';
        if (t.opts && typeof t.opts.onActivate === 'function') {
            actionBtn.textContent = label;
            actionBtn.classList.add('available');
        } else {
            actionBtn.classList.remove('available');
        }
        pinEl.classList.add('visible');
        anchorEl.classList.add('visible');
        placePin();
    }

    function placePin() {
        if (!pin || !pinEl.classList.contains('visible')) return;
        var p = map.project([pin.anchor.lng, pin.anchor.lat]);
        anchorEl.style.left = Math.round(p.x) + 'px';
        anchorEl.style.top = Math.round(p.y) + 'px';
        // Docked only where the card would otherwise sit on the feature: a
        // narrow screen. The class is toggled (not media-query-only) so the
        // inline left/top written for a wide screen is not left fighting it.
        var docked = map.getContainer().clientWidth <= 640;
        pinEl.classList.toggle('docked', docked);
        position(pinEl, p, docked);
    }

    function pinAt(lngLat, point, stack, idx) {
        if (!pinEl) build();
        pin = {
            stack: stack, idx: idx || 0,
            anchor: { lng: lngLat.lng, lat: lngLat.lat },
            key: featureKey(stack[idx || 0].feature) + '@' + stack[idx || 0].layerId
        };
        hideHover(true);
        drawPin();
    }

    function unpin() {
        if (!pin) return;
        pin = null;
        if (pinEl) { pinEl.classList.remove('visible'); pinEl.classList.remove('docked'); }
        if (anchorEl) anchorEl.classList.remove('visible');
        if (tabsEl) { tabsEl.classList.remove('available'); tabsEl.innerHTML = ''; }
    }

    function activateCurrent() {
        if (!pin) return;
        var t = pin.stack[pin.idx];
        var at = { lngLat: pin.anchor };
        unpin();
        if (t && t.opts && typeof t.opts.onActivate === 'function') {
            try { t.opts.onActivate(t.feature, at); } catch (e) { console.error(e); }
        }
    }

    // ---- the hover preview -----------------------------------------------

    function showHover(tip, point) {
        if (!hovEl) build();
        if (hoverHideTimer) { clearTimeout(hoverHideTimer); hoverHideTimer = null; }
        var key = featureKey(tip.feature) + '@' + tip.layerId;
        // Moving the cursor ACROSS one feature is not a new answer. Re-writing
        // innerHTML on every mousemove reflows the tip 60-120 times a second
        // for an identical string, which is most of the cost of hovering a
        // dense layer; only the position actually changed.
        if (hov && hov.key === key && hov.tip.html === tip.html &&
            hov.tip.extra === tip.extra) {
            hov.tip = tip;
            position(hovEl, point);
            return;
        }
        hov = { tip: tip, key: key };
        hovBody.innerHTML = tip.html + moreHTML(tip.extra);
        hovEl.classList.add('visible');
        position(hovEl, point);
    }

    function hideHover(immediate) {
        if (hoverHideTimer) { clearTimeout(hoverHideTimer); hoverHideTimer = null; }
        hov = null;
        if (!hovEl) return;
        if (immediate) {
            hovEl.classList.remove('visible');
        } else {
            hoverHideTimer = setTimeout(function () {
                hovEl.classList.remove('visible');
                hoverHideTimer = null;
            }, 70);
        }
    }

    /**
     * HOW CLOSE COUNTS AS ON IT.
     *
     * MapLibre hit-tests the drawn pixel. That is the right answer for a
     * country-sized polygon and the wrong one for everything this map is made
     * of: a fire trajectory is drawn at line-width 0.7-2.4 px (densityPaint),
     * a detection dot at radius 1.1-5. Human pointing error is a few px with a
     * mouse and, by both platform guidelines, ~9-11 mm - about 24 CSS px -
     * with a finger, whose contact patch also HIDES the target it is aiming
     * at. A 1 px line is therefore not a small target, it is not a target at
     * all, and "tap exactly on the line" is not a skill anyone has.
     *
     * So: a slop ring around the point, sized to the POINTER rather than to
     * the feature. This is Leaflet's `tolerance` and OpenLayers'
     * `hitTolerance`, and it changes nothing about what is DRAWN - only about
     * what answers.
     */
    var PAD_MOUSE = 6, PAD_TOUCH = 18, PAD_CLICK_BONUS = 4;
    function hitPad(e, forClick) {
        var p = pointerIsCoarse(e) ? PAD_TOUCH : PAD_MOUSE;
        // A click is a commitment and gets a little more room than a hover: a
        // preview that grabs early is noise, a click that misses is a dead end.
        return forClick ? p + PAD_CLICK_BONUS : p;
    }

    /** queryRenderedFeatures over a box of radius `r` px (r = 0 -> the point). */
    function qrf(point, r, layers) {
        if (!point) return [];
        var geom = r > 0
            ? [[point.x - r, point.y - r], [point.x + r, point.y + r]]
            : point;
        try { return map.queryRenderedFeatures(geom, { layers: layers }) || []; }
        catch (err) { return []; }
    }

    /**
     * Distance in screen px from `point` to a feature's geometry.
     *
     * The box query is a BOX: at pad 22 its corners are 31 px away, so a
     * feature merely clipping a corner would otherwise beat one 5 px from the
     * cursor. Measuring turns the box back into a disc, and gives the ordering
     * that decides which tab is selected - nearest first, which is what "the
     * thing I was pointing at" means.
     *
     * A polygon containing the point is distance 0 (it IS under the finger).
     * Anything unprojectable declines rather than guessing.
     */
    function screenDist(f, point) {
        var g = f && f.geometry;
        if (!g || !point) return null;
        var best = Infinity, px = point.x, py = point.y;
        function proj(c) {
            try { return map.project(c); } catch (err) { return null; }
        }
        function seg(a, b) {
            var vx = b.x - a.x, vy = b.y - a.y;
            var L = vx * vx + vy * vy;
            var t = L ? Math.max(0, Math.min(1, ((px - a.x) * vx + (py - a.y) * vy) / L)) : 0;
            var dx = a.x + t * vx - px, dy = a.y + t * vy - py;
            return Math.sqrt(dx * dx + dy * dy);
        }
        function line(coords) {
            var prev = null;
            for (var i = 0; i < coords.length; i++) {
                var p = proj(coords[i]);
                if (!p) continue;
                if (prev) best = Math.min(best, seg(prev, p));
                else best = Math.min(best, Math.sqrt((p.x - px) * (p.x - px) + (p.y - py) * (p.y - py)));
                prev = p;
            }
        }
        function ring(coords) {
            var pts = [], i, j;
            for (i = 0; i < coords.length; i++) {
                var p = proj(coords[i]);
                if (p) pts.push(p);
            }
            var inside = false;
            for (i = 0, j = pts.length - 1; i < pts.length; j = i++) {
                if ((pts[i].y > py) !== (pts[j].y > py) &&
                    px < (pts[j].x - pts[i].x) * (py - pts[i].y) / (pts[j].y - pts[i].y) + pts[i].x) {
                    inside = !inside;
                }
            }
            if (inside) { best = 0; return; }
            for (i = 0, j = pts.length - 1; i < pts.length; j = i++) {
                best = Math.min(best, seg(pts[j], pts[i]));
            }
        }
        var c = g.coordinates;
        try {
            switch (g.type) {
                case 'Point': {
                    var p0 = proj(c);
                    if (p0) best = Math.sqrt((p0.x - px) * (p0.x - px) + (p0.y - py) * (p0.y - py));
                    break;
                }
                case 'MultiPoint':
                    c.forEach(function (q) {
                        var p = proj(q);
                        if (p) best = Math.min(best, Math.sqrt((p.x - px) * (p.x - px) + (p.y - py) * (p.y - py)));
                    });
                    break;
                case 'LineString': line(c); break;
                case 'MultiLineString': c.forEach(line); break;
                case 'Polygon': c.forEach(ring); break;
                case 'MultiPolygon': c.forEach(function (poly) { poly.forEach(ring); }); break;
                default: return null;
            }
        } catch (err) { return null; }
        return isFinite(best) ? best : null;
    }

    /**
     * Every answer at this point, best first.
     *
     * `all` = false stops at the winner (a hover asks one question). `all` =
     * true keeps going, which is what a click gets: a place holds several
     * answers and losing the priority contest must not mean being unreachable.
     */
    function tipsFor(e, forClick, all) {
        var cand = [];
        var pad = hitPad(e, forClick);
        var layers = liveLayers().filter(function (id) {
            var o = registry.get(id);
            return forClick || !(o && o.clickOnly);
        });
        if (layers.length) {
            var feats = qrf(e.point, 0, layers), seenExact = {};
            // Topmost-first, but backdrops last regardless of where they are
            // drawn. Stable sort on (priority desc, distance asc), so within
            // one priority the nearest wins and the map's own render order
            // breaks the remaining ties.
            feats.forEach(function (f, i) {
                var o = registry.get(f.layer && f.layer.id);
                seenExact[(f.layer && f.layer.id) + '/' + featureKey(f)] = true;
                cand.push({ f: f, opts: o, i: i, dist: 0,
                            pri: (o && typeof o.priority === 'number') ? o.priority : 0 });
            });
            // THE SLOP RING. Everything above is what MapLibre calls a hit,
            // which is the rendered pixel and nothing else — and a fire
            // trajectory at line-width 0.7 (densityPaint's floor) is a target
            // you cannot reliably put a mouse on and cannot put a finger on at
            // all. So ask again over a box around the point and keep whatever
            // lands within `pad` of it, measured properly in screen space.
            // Exact hits stay at distance 0, so widening the target can only
            // ADD answers behind the one already under the cursor.
            if (pad > 0) {
                qrf(e.point, pad, layers).forEach(function (f, i) {
                    var lid = f.layer && f.layer.id;
                    if (seenExact[lid + '/' + featureKey(f)]) return;
                    var d = screenDist(f, e.point);
                    if (d == null || d > pad) return;
                    var o = registry.get(lid);
                    cand.push({ f: f, opts: o, i: feats.length + i, dist: d,
                                pri: (o && typeof o.priority === 'number') ? o.priority : 0 });
                });
            }
        }
        // Canvas-drawn features join the same ordering. They come after the
        // real layers at equal priority, which is right: a pinned vector the
        // user deliberately put there outranks an animation frame.
        probes.forEach(function (p, id) {
            if (!forClick && p.clickOnly) return;
            var hit;
            // The probe is told how much slop the pointer is entitled to, so a
            // canvas layer's hit radius is the same physical size as a vector
            // layer's rather than a number it invented for a mouse.
            try { hit = p.probe({ point: e.point, lngLat: e.lngLat,
                                  originalEvent: e.originalEvent, hitPad: pad }); }
            catch (err) { hit = null; }
            if (!hit) return;
            // A probe may answer with SEVERAL features at the point (an
            // animation frame draws a settlement, a clearing and a fire path
            // in the same place). Each becomes its own candidate under a
            // sub-id, so the card shows them as tabs instead of the nearest
            // one silently winning — the same rule as registered layers.
            (Array.isArray(hit) ? hit : [hit]).forEach(function (h, k) {
                if (!h) return;
                var sub = h.key ? (id + ':' + h.key) : (k ? id + ':' + k : id);
                cand.push({
                    f: h.feature || { properties: h.properties || {}, layer: { id: sub } },
                    // A probe may return `render(props)` instead of a fixed
                    // `html` string. That is what makes MapTip.refresh() work
                    // for canvas features: when an async detail lands the
                    // stored properties are re-rendered, rather than a frozen
                    // string being redrawn.
                    opts: { html: (typeof h.render === 'function') ? h.render
                                                                  : function () { return h.html; },
                            onActivate: h.onActivate || p.onActivate,
                            actionLabel: h.actionLabel || p.actionLabel,
                            tabLabel: h.tabLabel || p.tabLabel,
                            tabColor: h.tabColor || p.tabColor,
                            peers: (h.peers !== undefined) ? h.peers : p.peers,
                            priority: p.priority, clickOnly: p.clickOnly },
                    i: cand.length,
                    dist: (typeof h.dist === 'number') ? h.dist : 0,
                    pri: (typeof p.priority === 'number') ? p.priority : 0,
                    probeId: sub
                });
            });
        });
        cand = cand.filter(function (c) { return c.opts && typeof c.opts.html === 'function'; });
        if (!cand.length) return null;
        // Priority first (a backdrop never buries a feature), then DISTANCE:
        // inside the slop ring several things answer, and "the one I was
        // pointing at" is the nearest, not the one the renderer happened to
        // draw last. Render order only breaks the remaining ties.
        cand.sort(function (a, b) {
            return (b.pri - a.pri) || ((a.dist || 0) - (b.dist || 0)) || (a.i - b.i);
        });
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
            // nothing renders there is no tip AND no click interception.
            if (!html) continue;
            seen[layerId] = true;
            // "+N more here" means "there are other features of THIS KIND
            // under the cursor that you would have to zoom in to tell apart".
            //
            // Three things it must not count. Other LAYERS — a geology unit
            // under a fire trajectory is a different question, and it is a tab,
            // i.e. already reachable. Other PARTS of the same feature:
            // MapLibre returns one result per tile per part, so a multipolygon
            // AOI reported "+3 more features here" about itself and a
            // dissolved geology class "+96". And repeats across tile seams.
            var extra = 0, mine = {};
            mine[featureKey(f)] = true;
            // A backdrop opts out entirely (`peers: false`). Nobody clicks a
            // country-sized drape to choose one of its 17 units.
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
        return id.replace(/^lod-|-(fill|line|circle|point|arrow|front|glow|dots)$/g, '')
                 .replace(/[-_:]+/g, ' ').trim() || 'Feature';
    }

    function onMouseMove(e) {
        lastMoveAt = Date.now();
        var t = tipFor(e);
        if (!t) {
            if (hov) hideHover(false);
            map.getCanvas().style.cursor = '';
            return;
        }
        map.getCanvas().style.cursor = 'pointer';
        // Don't shadow the selection with a second copy of itself. Hovering
        // anything ELSE still previews, which is the whole point of keeping
        // the two apart.
        if (pin && (featureKey(t.feature) + '@' + t.layerId) === pin.key) {
            hideHover(true);
            return;
        }
        showHover(t, e.point);
    }

    function onClick(e) {
        // Every answer at the point, on both pointers: the pinned card lists
        // them as tabs, so a mouse user can reach what is underneath without
        // knowing a modifier key.
        var all = tipsFor(e, true, true);
        if (!all) {
            // The void. Nothing answered here, so the selection is over —
            // exactly as tapping empty map clears a Maps card. A click inside
            // a big polygon is NOT the void: that polygon answered, and it
            // gets selected like anything else.
            unpin();
            return;
        }
        if (e.originalEvent) e.originalEvent.stopPropagation();
        // Tell the park-polygon click handler to stand down: a tap on a pinned
        // feature must not also open the park report popup underneath.
        window._mapTipClicked = true;
        setTimeout(function () { window._mapTipClicked = false; }, 60);

        var t = all[0];
        // A LAYER WHOSE FULL SURFACE *IS* THE SELECTION OPENS IT DIRECTLY.
        //
        // An area's popup is already everything this card is — anchored to the
        // ground, dismissable, in the share link — and more. Pinning a
        // three-line copy of it first would be an extra click for a worse
        // answer (`activateOnClick`). But that only holds when the click is
        // UNAMBIGUOUS: where several things answer at the point, the click has
        // to ask which one, and the card with its tabs is that question. The
        // area's tab then carries its own 'Open area' button, so nothing
        // becomes unreachable and the rule is the same on a mouse and a
        // finger.
        if (all.length === 1 && t.opts.activateOnClick &&
            typeof t.opts.onActivate === 'function') {
            unpin();
            try { t.opts.onActivate(t.feature, e); } catch (err) { console.error(err); }
            return;
        }
        var key = featureKey(t.feature) + '@' + t.layerId;
        // Clicking the selection again clears it. A selection is the one thing
        // on a map that toggles, and it saves aiming at a 22px ×.
        if (pin && pin.key === key) { unpin(); return; }
        pinAt(e.lngLat, e.point, all, 0);
    }

    // The express lane: someone who already knows what they clicked should not
    // have to read a card about it first. Same destination as the card's
    // action button, so nothing is reachable only this way.
    function onDblClick(e) {
        var t = tipFor(e, true);
        if (!t || typeof t.opts.onActivate !== 'function') return;
        if (e.originalEvent) {
            e.originalEvent.stopPropagation();
            e.originalEvent.preventDefault();
        }
        if (e.preventDefault) e.preventDefault();   // stop MapLibre's zoom
        window._mapTipClicked = true;
        setTimeout(function () { window._mapTipClicked = false; }, 60);
        unpin();
        try { t.opts.onActivate(t.feature, e); } catch (err) { console.error(err); }
    }

    function onMapMove() {
        // The selection is anchored to the ground and rides the map. The hover
        // preview is anchored to the cursor and is meaningless once the map
        // moves under it.
        placePin();
        if (hov) hideHover(true);
    }

    var MapTip = {
        init: function (m) {
            if (map) return;
            map = m;
            build();
            map.on('mousemove', onMouseMove);
            map.on('click', onClick);
            map.on('dblclick', onDblClick);
            map.on('move', onMapMove);
            map.on('mouseout', function () { hideHover(false); });
            document.addEventListener('keydown', function (ev) {
                if (ev.key === 'Escape') { hideHover(true); unpin(); }
                // ⏎ opens what is selected — the keyboard equivalent of the
                // double-click, and the only way to reach it without a mouse.
                if (ev.key === 'Enter' && pin &&
                    !/^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || '')) {
                    activateCurrent();
                }
            });
        },
        register: function (layerId, opts) {
            if (!layerId || !opts || typeof opts.html !== 'function') return;
            registry.set(layerId, opts);
        },
        unregister: function (layerId) {
            registry.delete(layerId);
            // A selection whose layer has gone is a card describing something
            // no longer on the map.
            if (pin && pin.stack[pin.idx] && pin.stack[pin.idx].layerId === layerId) unpin();
            if (hov && hov.tip.layerId === layerId) hideHover(true);
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
            if (pin && pin.stack[pin.idx] && pin.stack[pin.idx].layerId === id) unpin();
        },
        setBackdropGuard: function (fn) { backdropGuard = fn; },
        hide: function () { hideHover(true); unpin(); },
        /** Only the transient preview — used before opening a popup. */
        hideHover: function () { hideHover(true); },
        isSticky: function () { return !!pin; },
        isPinned: function () { return !!pin; },

        /**
         * The selection as share-link state: a PLACE plus which answer at it
         * was being read. Not a feature id — see the header.
         */
        getState: function () {
            if (!pin) return null;
            var t = pin.stack[pin.idx];
            return {
                lng: +pin.anchor.lng.toFixed(5),
                lat: +pin.anchor.lat.toFixed(5),
                layer: t ? t.layerId : null
            };
        },

        /**
         * Re-ask the map at a point and select what is there — the restore
         * half of getState(). Returns true if something answered.
         *
         * `layer` only picks WITHIN the stack found at the point; it never
         * invents an answer. A link whose layers have not loaded yet finds
         * nothing and the caller retries — a stale link finds the wrong thing
         * or nothing at all, and either is honest, whereas re-rendering a
         * remembered card would be a copy of an answer nobody re-asked.
         */
        pinAt: function (lngLat, layerId) {
            if (!map) return false;
            var ll = { lng: +lngLat.lng, lat: +lngLat.lat };
            if (isNaN(ll.lng) || isNaN(ll.lat)) return false;
            var pt = map.project([ll.lng, ll.lat]);
            var all = tipsFor({ point: pt, lngLat: ll }, true, true);
            if (!all) return false;
            var idx = 0;
            if (layerId) {
                for (var i = 0; i < all.length; i++) {
                    if (all[i].layerId === layerId) { idx = i; break; }
                }
            }
            pinAt(ll, pt, all, idx);
            return true;
        },

        /** Re-render whatever is on screen for this layer (async detail landed). */
        refresh: function (layerId) {
            function re(t) {
                if (layerId && t.layerId !== layerId) return false;
                var html;
                try {
                    html = t.opts.html(t.feature.properties || {}, t.feature,
                                       { point: null, lngLat: pin ? pin.anchor : null });
                } catch (err) { return false; }
                if (!html) return false;
                t.html = html;
                return true;
            }
            if (pin && pin.stack[pin.idx]) {
                // Keep the whole stack in step, or switching tabs away and
                // back shows the placeholder the detail just replaced.
                var touched = false;
                pin.stack.forEach(function (t) { if (re(t)) touched = true; });
                if (touched) drawPin();
            }
            if (hov && re(hov.tip)) {
                hovBody.innerHTML = hov.tip.html + moreHTML(hov.tip.extra);
            }
        },
        isTouch: function () { return (Date.now() - lastMoveAt) > TAP_WINDOW_MS; },
        count: function () { return registry.size; }
    };

    window.MapTip = MapTip;
})();
