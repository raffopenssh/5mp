// About / "How this map works" modal (#modal-manifest) — reading behaviour.
//
// The markup is in globe.html, the layout in globe.css (.about-*). This file
// adds what a long text needs to be read rather than skimmed: a table of
// contents derived from the headings (never typed — invariant 2), the active
// section tracked while scrolling, a reading-progress bar, a read time
// derived from the word count, scroll position remembered across opens,
// focus handed in and back out, and on touch a swipe-down on the header to
// dismiss. Headings stay `.methods-h` + id="methods-…" so SectionLink
// (sectionlink.js) keeps working: it finds the scroller by computed overflow
// and honours scroll-margin-top, so the sticky chip row on mobile is respected.
(function () {
    'use strict';

    var overlay, scroller, toc, article, readtime, head, sheet, steps, nowBtn, nowIdx, nowTitle, drop;
    var segs = [], fills = [];
    var built = false, opener = null, raf = 0, links = [], heads = [];
    var mobileMQ = matchMedia('(max-width: 860px)');
    var SCROLL_KEY = 'about.scrollTop';

    function $(id) { return document.getElementById(id); }

    function headingText(h) {
        var c = h.cloneNode(true);
        Array.prototype.forEach.call(c.querySelectorAll('.seclink'), function (b) { b.remove(); });
        return (c.textContent || '').replace(/\s+/g, ' ').trim();
    }

    function topOffset(h) { return parseFloat(getComputedStyle(h).scrollMarginTop) || 16; }

    function scrollToHeading(h) {
        var top = h.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - topOffset(h);
        scroller.scrollTo({ top: Math.max(0, top), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    }

    function buildToc() {
        if (built) return;
        heads = Array.prototype.slice.call(article.querySelectorAll('h3.methods-h, h4.methods-h'));
        if (!heads.length) return;
        var frag = document.createDocumentFragment();
        var label = document.createElement('div');
        label.className = 'about-toc-label';
        label.textContent = 'Contents';
        frag.appendChild(label);
        links = heads.map(function (h) {
            var a = document.createElement('a');
            a.href = '#' + h.id;
            a.className = h.tagName === 'H4' ? 'sub' : '';
            a.textContent = headingText(h);
            a.addEventListener('click', function (ev) {
                ev.preventDefault();
                setTocOpen(false);
                scrollToHeading(h);
                try { h.focus({ preventScroll: true }); } catch (e) { /* focus is a nicety */ }
            });
            frag.appendChild(a);
            return a;
        });
        toc.appendChild(frag);
        buildSteps();
        // Read time from the words actually on the page, ~220 wpm.
        var words = (article.textContent || '').trim().split(/\s+/).length;
        if (readtime) readtime.textContent = Math.max(1, Math.round(words / 220)) + ' min read';
        built = true;
    }

    // buildSteps — one segment per heading, width proportional to the text
    // it heads (the reader sees how long each part is before entering it).
    // Each fills left→right as its section is read; the whole track is the
    // progress bar. Click/tap jumps. Section lengths are measured from the
    // DOM, never typed (invariant 2).
    function sectionLengths() {
        var tops = heads.map(function (h) { return h.offsetTop; });
        tops.push(article.scrollHeight);
        return heads.map(function (h, i) { return Math.max(1, tops[i + 1] - tops[i]); });
    }
    function buildSteps() {
        if (!steps) return;
        steps.innerHTML = '';
        segs = []; fills = [];
        var len = sectionLengths(), total = len.reduce(function (a, b) { return a + b; }, 0);
        heads.forEach(function (h, i) {
            var b = document.createElement('button');
            b.type = 'button'; b.className = 'about-step';
            // sqrt of the share: long parts read long, short parts stay tappable
            b.style.flexGrow = String(Math.sqrt(len[i] / total * heads.length));
            b.setAttribute('aria-label', (i + 1) + ' of ' + heads.length + ': ' + headingText(h));
            var f = document.createElement('span'); f.className = 'about-step-fill';
            var tip = document.createElement('span'); tip.className = 'about-step-tip'; tip.textContent = headingText(h);
            b.appendChild(f); b.appendChild(tip);
            b.addEventListener('click', function () { scrollToHeading(h); try { h.focus({ preventScroll: true }); } catch (e) { /* nicety */ } });
            steps.appendChild(b); segs.push(b); fills.push(f);
        });
    }
    function setNow(i) {
        if (!nowBtn || !heads[i]) return;
        var h = heads[i], isSub = h.tagName === 'H4';
        nowIdx.textContent = (i + 1) + '/' + heads.length;
        var parent = null;
        if (isSub) for (var k = i - 1; k >= 0; k--) if (heads[k].tagName === 'H3') { parent = heads[k]; break; }
        nowTitle.innerHTML = '';
        if (parent) { var sp = document.createElement('span'); sp.className = 'sub'; sp.textContent = headingText(parent) + ' › '; nowTitle.appendChild(sp); }
        nowTitle.appendChild(document.createTextNode(headingText(h)));
    }
    function setTocOpen(open) {
        if (!sheet || !nowBtn) return;
        sheet.classList.toggle('toc-open', open);
        nowBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    // placeToc — the same <nav> lives beside the article on wide screens and
    // inside the header dropdown on narrow ones; move it rather than clone it
    // so there is one set of links and one active state.
    function placeToc() {
        if (!toc || !drop) return;
        var grid = article.parentElement;
        if (mobileMQ.matches) { if (toc.parentElement !== drop) drop.appendChild(toc); }
        else { if (toc.parentElement !== grid) grid.insertBefore(toc, article); setTocOpen(false); }
    }

    function onScroll() {
        if (raf) return;
        raf = requestAnimationFrame(function () {
            raf = 0;
            var max = scroller.scrollHeight - scroller.clientHeight;
            if (fills.length) {
                // Fill by reading line: the segment holding the line is partly
                // full, earlier ones full, later ones empty. At the end, all full.
                var lineY = scroller.scrollTop + 72, atEnd = max > 0 && scroller.scrollTop >= max - 2;
                var tops = heads.map(function (h) { return h.offsetTop; }); tops.push(article.scrollHeight);
                for (var j = 0; j < fills.length; j++) {
                    var p = atEnd ? 1 : Math.min(1, Math.max(0, (lineY - tops[j]) / Math.max(1, tops[j + 1] - tops[j])));
                    fills[j].style.width = (p * 100) + '%';
                }
            }
            // Remember here, not on close: once the overlay is display:none
            // the scroller reads 0.
            if (overlay.classList.contains('active')) {
                try { sessionStorage.setItem(SCROLL_KEY, String(scroller.scrollTop)); } catch (e) { /* private mode */ }
            }
            if (!heads.length) return;
            // Active = last heading whose top has passed the reading line;
            // at the very end, the last heading wins even if short.
            var line = scroller.getBoundingClientRect().top + 72, active = 0;
            for (var i = 0; i < heads.length; i++) {
                if (heads[i].getBoundingClientRect().top <= line) active = i; else break;
            }
            if (max > 0 && scroller.scrollTop >= max - 2) active = heads.length - 1;
            links.forEach(function (a, i) {
                var on = i === active;
                if (on !== a.classList.contains('active')) {
                    a.classList.toggle('active', on);
                    if (on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
                    if (on) setNow(i);
                }
                if (segs[i]) segs[i].classList.toggle('active', on);
            });
            if (nowBtn && !nowTitle.textContent) setNow(active);
        });
    }

    function onOpen() {
        buildToc();
        placeToc();
        if (steps && heads.length) buildSteps(); // heights may have changed since (licences)
        opener = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
        var wantsSection = /[?&]methods=/.test(location.search);
        if (!wantsSection) {
            var saved = 0;
            try { saved = parseInt(sessionStorage.getItem(SCROLL_KEY) || '0', 10) || 0; } catch (e) { /* private mode */ }
            scroller.style.scrollBehavior = 'auto';
            scroller.scrollTop = saved;
            scroller.style.scrollBehavior = '';
        }
        onScroll();
        // Focus the scroller so arrow keys/space read; the dialog is labelled
        // by its title.
        scroller.setAttribute('tabindex', '-1');
        try { scroller.focus({ preventScroll: true }); } catch (e) { scroller.focus(); }
    }

    function onClose() {
        if (opener && document.contains(opener) && typeof opener.focus === 'function') {
            try { opener.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
        }
        opener = null;
        setTocOpen(false);
        if (sheet) sheet.style.transform = '';
    }

    // Swipe the header down to dismiss (touch only). Only the header is a
    // handle, so the article can scroll freely; the sheet follows the finger
    // and snaps back or closes past a third of its height / a quick flick.
    function bindSwipe() {
        var y0 = 0, t0 = 0, dy = 0, active = false;
        head.addEventListener('touchstart', function (e) {
            if (e.touches.length !== 1) return;
            y0 = e.touches[0].clientY; t0 = Date.now(); dy = 0; active = true;
            sheet.classList.add('dragging'); sheet.classList.remove('snapping');
        }, { passive: true });
        head.addEventListener('touchmove', function (e) {
            if (!active) return;
            dy = Math.max(0, e.touches[0].clientY - y0);
            sheet.style.transform = dy ? 'translateY(' + dy + 'px)' : '';
        }, { passive: true });
        function end() {
            if (!active) return;
            active = false;
            sheet.classList.remove('dragging');
            var v = dy / Math.max(1, Date.now() - t0); // px per ms
            if (dy > sheet.clientHeight / 3 || (dy > 40 && v > 0.6)) {
                sheet.classList.add('snapping');
                sheet.style.transform = 'translateY(100%)';
                setTimeout(function () {
                    sheet.classList.remove('snapping');
                    if (typeof closeModal === 'function') closeModal('manifest'); else overlay.classList.remove('active');
                }, 200);
            } else {
                sheet.classList.add('snapping');
                sheet.style.transform = '';
                setTimeout(function () { sheet.classList.remove('snapping'); }, 240);
            }
        }
        head.addEventListener('touchend', end);
        head.addEventListener('touchcancel', end);
    }

    function init() {
        overlay = $('modal-manifest');
        if (!overlay) return;
        scroller = $('about-scroll'); toc = $('about-toc'); article = $('about-article');
        readtime = $('about-readtime'); steps = $('about-steps');
        nowBtn = $('about-now'); nowIdx = $('about-now-idx'); nowTitle = $('about-now-title'); drop = $('about-drop');
        head = overlay.querySelector('.about-head'); sheet = overlay.querySelector('.about-modal');
        if (!scroller || !toc || !article) return;
        scroller.addEventListener('scroll', onScroll, { passive: true });
        window.addEventListener('resize', function () { placeToc(); if (built) buildSteps(); onScroll(); });
        if (mobileMQ.addEventListener) mobileMQ.addEventListener('change', placeToc);
        if (nowBtn) nowBtn.addEventListener('click', function (e) { e.stopPropagation(); setTocOpen(!sheet.classList.contains('toc-open')); });
        overlay.addEventListener('click', function (e) { if (sheet.classList.contains('toc-open') && !e.target.closest('.about-drop') && !e.target.closest('.about-now')) setTocOpen(false); });
        overlay.addEventListener('keydown', function (e) { if (e.key === 'Escape' && sheet.classList.contains('toc-open')) { e.stopPropagation(); setTocOpen(false); } });
        if (head && sheet && 'ontouchstart' in window) bindSwipe();
        var wasOpen = overlay.classList.contains('active');
        new MutationObserver(function () {
            var open = overlay.classList.contains('active');
            if (open === wasOpen) return;
            wasOpen = open;
            if (open) onOpen(); else onClose();
        }).observe(overlay, { attributes: true, attributeFilter: ['class'] });
        if (wasOpen) onOpen();
        // late-rendered content (licences) changes heights: refresh the bar
        var rebuild = 0;
        new MutationObserver(function () { clearTimeout(rebuild); rebuild = setTimeout(function () { if (built) buildSteps(); onScroll(); }, 120); }).observe(article, { childList: true, subtree: true });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
