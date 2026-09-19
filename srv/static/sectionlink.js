// SectionLink — a share link on every heading of a surface.
//
// WHY. The things people quote from this app are sections: "how do you build
// a fire line?", "where is the Shared files list?". A heading you can link to
// is the standard answer (docs sites, READMEs, MDN): a link affordance appears
// on hover/focus beside the heading, one click copies, and the URL opens the
// app WITH that section on screen, highlighted once.
//
// ONE COMPONENT, MANY SURFACES. A mount describes a surface: which root to
// watch, which headings count, how a heading becomes a slug, what URL that slug
// makes, and how to bring the surface on screen. Everything else — the button,
// idempotent decoration under re-render, the reveal with highlight and focus,
// the address-bar mirror, the URL restore — is shared. Mounts live at the end
// of this file; adding a surface is one object.
//
// THE LINK IS ABOUT THE SECTION, NOT THE VIEW. A methods link carries no
// lat/lng/layers/dates: the recipient's own map opens beneath the text, and
// the link survives changes to the view-state format.
//
// AUTH. Every link goes through ShareLink.open(), the chokepoint that strips
// `pwd` (docs/agents/sharing.md): a section link is a NAME behind the password
// gate; the reader authenticates as themselves. A guest on a capability cannot
// mint, so ShareLink hands them their own held key with the section query
// carried along (/s/{slug} forwards non-credential params) — a guest can point
// a colleague at a section without gaining or granting anything.
//
// ADDRESS BAR. Mirrors the section while the surface is open (replaceState,
// never pushState — Back must not unwind headings) and is cleaned when the
// surface closes so a reload does not reopen it. buildShareUrl() composes its
// own params from state, so the mirror never leaks into other links.
(function () {
    'use strict';

    var mounts = [];
    var REVEAL_TRIES = 30, REVEAL_EVERY_MS = 100; // 3 s for content rendered from a fetch

    function text(h) {
        var c = h.cloneNode(true);
        Array.prototype.forEach.call(c.querySelectorAll('.seclink'), function (b) { b.remove(); });
        return (c.textContent || '').replace(/\s+/g, ' ').trim();
    }
    function absolute(u) { try { return new URL(u, window.location.href).toString(); } catch (e) { return u; } }

    function setAddress(m, slug) {
        try {
            var u = new URL(window.location.href);
            var before = u.toString();
            m.params(slug).forEach(function (kv) { if (kv[1] == null) u.searchParams.delete(kv[0]); else u.searchParams.set(kv[0], kv[1]); });
            u.searchParams.delete('pwd');
            if (u.toString() !== before) history.replaceState(history.state, '', u.toString());
        } catch (e) { /* the address bar is a nicety */ }
    }

    function share(m, h, ev) {
        var slug = m.slugOf(h);
        if (!slug) return false;
        var url = absolute(m.urlFor(slug));
        setAddress(m, slug);
        if (window.ShareLink && ShareLink.open) {
            // Propose a readable name (methods-fire, admin-files): a section
            // link is quoted and typed, so it should read as what it is.
            return ShareLink.open(url, { title: m.title(h), kind: 'view', slug: m.nameFor(slug),
                anchor: ev && ev.currentTarget, event: ev });
        }
        try { navigator.clipboard.writeText(url); } catch (e) { /* no share component and no clipboard: nothing to do */ }
        return false;
    }

    function decorate(m) {
        var root = m.rootEl();
        if (!root) return;
        Array.prototype.forEach.call(root.querySelectorAll(m.headings), function (h) {
            if (h.dataset.seclink) return;
            var slug = m.slugOf(h);
            if (!slug) return;
            h.dataset.seclink = slug;
            if (!h.hasAttribute('tabindex')) h.setAttribute('tabindex', '-1'); // focus target on arrival
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'seclink';
            b.setAttribute('aria-label', 'Copy link to “' + text(h) + '”');
            b.title = 'Copy link to this section';
            b.innerHTML = '<i class="icon-link" aria-hidden="true"></i>';
            b.addEventListener('click', function (ev) { ev.preventDefault(); ev.stopPropagation(); share(m, h, ev); });
            h.appendChild(b);
        });
    }

    // nearestScroller — the first ancestor that actually scrolls (computed
    // overflow), so a heading inside a panel body scrolls the body, not the
    // page. Surfaces need not declare it.
    function nearestScroller(h) {
        var e = h.parentElement;
        while (e && e !== document.body) {
            var o = getComputedStyle(e).overflowY;
            if ((o === 'auto' || o === 'scroll') && e.scrollHeight > e.clientHeight + 1) return e;
            e = e.parentElement;
        }
        return window;
    }

    // offsetOf — the heading's scroll-margin-top, so a surface with a sticky
    // bar inside its scroller (the About TOC chips) declares the clearance.
    function offsetOf(h) { return parseFloat(getComputedStyle(h).scrollMarginTop) || 16; }
    function align(h, sc) {
        if (sc && sc !== window && sc !== document.body) {
            var top = h.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop - offsetOf(h);
            sc.scrollTo({ top: Math.max(0, top), behavior: 'auto' });
        } else h.scrollIntoView({ block: 'start' });
    }
    // settle — scroll now, then re-align for a short while as content above
    // the heading arrives from fetches and pushes it down (the admin tabs and
    // the licences list both render late). The user's first scroll gesture
    // ends the settling: their hand wins.
    function settle(h, m) {
        var sc = m.scroller(h), stop = false, ends = [250, 600, 1200, 2000];
        var off = function () { stop = true; ['wheel', 'touchstart', 'pointerdown', 'keydown'].forEach(function (t) { window.removeEventListener(t, off, true); }); };
        ['wheel', 'touchstart', 'pointerdown', 'keydown'].forEach(function (t) { window.addEventListener(t, off, true); });
        align(h, sc);
        ends.forEach(function (ms) {
            setTimeout(function () {
                if (stop || !document.contains(h)) return;
                var ref = (sc && sc !== window) ? sc.getBoundingClientRect().top : 0;
                if (Math.abs(h.getBoundingClientRect().top - ref - offsetOf(h)) > 8) align(h, m.scroller(h));
                if (ms === ends[ends.length - 1]) off();
            }, ms);
        });
    }

    function highlight(h) {
        h.classList.remove('seclink-target');
        void h.offsetWidth;
        h.classList.add('seclink-target');
        try { h.focus({ preventScroll: true }); } catch (e) { h.focus(); }
        setTimeout(function () { h.classList.remove('seclink-target'); }, 2400);
    }

    // reveal — open the surface, wait for the heading to exist (it may render
    // from a fetch), scroll it into view inside its own scroller, highlight.
    function reveal(m, slug) {
        return new Promise(function (resolve) {
            try { m.open(slug); } catch (e) { /* surface unavailable */ }
            var tries = 0;
            (function tick() {
                var h = m.find(slug);
                if (h && h.offsetParent !== null) {
                    decorate(m);
                    settle(h, m);
                    highlight(h);
                    return resolve(true);
                }
                if (++tries >= REVEAL_TRIES) return resolve(false);
                setTimeout(tick, REVEAL_EVERY_MS);
            })();
        });
    }

    function watch(m) {
        var root = m.rootEl();
        if (!root || m._watching) return;
        m._watching = true;
        var queued = false, wasOpen = m.isOpen();
        var mo = new MutationObserver(function () {
            if (queued) return;
            queued = true;
            requestAnimationFrame(function () {
                queued = false;
                var open = m.isOpen();
                if (open) decorate(m);
                // Only the CLOSE transition cleans the address bar: a link
                // arriving on a closed surface must keep its param until the
                // restore pass has read it.
                else if (wasOpen) setAddress(m, null);
                wasOpen = open;
            });
        });
        mo.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style'] });
        decorate(m);
    }

    // mount(spec) — see the mounts below for the shape. Every field but
    // `name`, `root`, `headings`, `urlFor` has a sensible default.
    function mount(spec) {
        var m = {
            name: spec.name,
            rootEl: function () { return typeof spec.root === 'string' ? document.querySelector(spec.root) : spec.root; },
            headings: spec.headings,
            slugOf: spec.slugOf || function (h) { return h.id || ''; },
            find: spec.find || function (slug) {
                var root = m.rootEl(); if (!root) return null;
                var all = root.querySelectorAll(m.headings);
                for (var i = 0; i < all.length; i++) if (m.slugOf(all[i]) === slug) return all[i];
                return null;
            },
            urlFor: spec.urlFor,
            // params(slug) — the address-bar keys this mount owns; null value = delete.
            params: spec.params || function (slug) { return [[spec.param || spec.name, slug]]; },
            fromURL: spec.fromURL || function (p) { return p.get(spec.param || spec.name) || ''; },
            title: spec.title || function (h) { return (spec.titlePrefix || '') + text(h); },
            nameFor: spec.nameFor || function (slug) { return (spec.slugPrefix || spec.name + '-') + slug; },
            open: spec.open || function () {},
            isOpen: spec.isOpen || function () { return true; },
            scroller: spec.scroller || nearestScroller
        };
        mounts.push(m);
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { watch(m); });
        else watch(m);
        return {
            decorate: function () { decorate(m); },
            reveal: function (slug) { return reveal(m, slug); },
            urlFor: function (slug) { return absolute(m.urlFor(slug)); }
        };
    }

    // openFromURL — the page's URL-restore pass calls this once. The first
    // mount whose param is present claims the request.
    function openFromURL(params) {
        params = params || new URLSearchParams(window.location.search);
        for (var i = 0; i < mounts.length; i++) {
            var slug = mounts[i].fromURL(params);
            if (slug) return reveal(mounts[i], slug);
        }
        return Promise.resolve(false);
    }

    window.SectionLink = { mount: mount, openFromURL: openFromURL };

    // ── mounts ──────────────────────────────────────────────────────────

    // "How this map works" — the methods text. Headings carry id="methods-…".
    mount({
        name: 'methods', param: 'methods',
        root: '#modal-manifest', headings: '.methods-h',
        slugOf: function (h) { return (h.id || '').replace(/^methods-/, ''); },
        urlFor: function (slug) { return '/?methods=' + encodeURIComponent(slug); },
        titlePrefix: 'How this map works · ',
        open: function () { if (typeof showModal === 'function') showModal('manifest'); },
        isOpen: function () { var el = document.getElementById('modal-manifest'); return !!(el && el.classList.contains('active')); }
    });

    // Admin panel sub-sections — headings carry id="adm-…" inside a tab. The
    // URL is the tab link the panel already understands plus `section=`.
    mount({
        name: 'admin-section', param: 'section',
        root: '#admin-panel', headings: '.admin-tab-content .adm-h',
        slugOf: function (h) { return (h.id || '').replace(/^adm-/, ''); },
        urlFor: function (slug) {
            var el = document.getElementById('adm-' + slug);
            var tab = el ? el.closest('.admin-tab-content') : null;
            var tabName = tab ? tab.id.replace(/^tab-/, '') : '';
            return '/?panel=admin&admin_tab=' + encodeURIComponent(tabName) + '&section=' + encodeURIComponent(slug);
        },
        titlePrefix: 'Admin · ', slugPrefix: 'admin-',
        params: function (slug) { return [['section', slug]]; },
        // Only claimed when the admin panel is the surface being restored —
        // the panel/admin_tab restore runs first and switches the tab.
        fromURL: function (p) { return p.get('panel') === 'admin' ? (p.get('section') || '') : ''; },
        open: function () {},
        isOpen: function () { var el = document.getElementById('admin-panel'); return !!(el && el.classList.contains('active')); }
    });
})();
