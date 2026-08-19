// FloatUI — makes floating panels (pinned-layers box, park popup) draggable,
// collapsible and dockable to the map toolbar. Desktop + mobile (pointer events).
// State for the pinned indicator persists in localStorage; scroll positions are
// remembered across collapse/expand.
(function () {
    'use strict';

    const LS_PREFIX = 'fui.';
    const DRAG_THRESHOLD = 6; // px before a press becomes a drag
    const MARGIN = 6;         // min distance from viewport edge

    const store = {
        get(k, d) { try { const v = localStorage.getItem(LS_PREFIX + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
        set(k, v) { try { localStorage.setItem(LS_PREFIX + k, JSON.stringify(v)); } catch (e) { } },
        del(k) { try { localStorage.removeItem(LS_PREFIX + k); } catch (e) { } }
    };

    const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

    // ---------------------------------------------------------------
    // Generic pointer-based drag with tap detection
    // ---------------------------------------------------------------
    function enableDrag(handle, opts) {
        let sx = 0, sy = 0, ox = 0, oy = 0, active = false, dragging = false;
        handle.style.touchAction = 'none';
        handle.classList.add('fui-handle');

        handle.addEventListener('pointerdown', (e) => {
            if (e.button !== undefined && e.button !== 0) return;
            // Don't start drags from interactive elements
            if (e.target.closest('button, a, input, select, .star-btn, .pa-export-btn, .fui-bar-btn, .pinned-indicator-close, .maplibregl-popup-close-button, .pinned-layer-chip')) return;
            active = true; dragging = false;
            sx = e.clientX; sy = e.clientY;
            const p = opts.getPos();
            ox = p.x; oy = p.y;
            try { handle.setPointerCapture(e.pointerId); } catch (err) { }
        });

        handle.addEventListener('pointermove', (e) => {
            if (!active) return;
            const dx = e.clientX - sx, dy = e.clientY - sy;
            if (!dragging) {
                if (Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
                if (opts.onDragStart && opts.onDragStart() === false) { active = false; return; }
                dragging = true;
                handle.classList.add('fui-dragging');
                document.body.classList.add('fui-no-select');
            }
            opts.setPos(ox + dx, oy + dy);
            e.preventDefault();
        });

        const end = (e) => {
            if (!active) return;
            active = false;
            handle.classList.remove('fui-dragging');
            document.body.classList.remove('fui-no-select');
            if (dragging) { opts.onDragEnd && opts.onDragEnd(); }
            else if (opts.onTap) { opts.onTap(e); }
            dragging = false;
        };
        handle.addEventListener('pointerup', end);
        handle.addEventListener('pointercancel', end);
    }

    // ---------------------------------------------------------------
    // Dock (chips inside the map toolbar)
    // ---------------------------------------------------------------
    function getDock() {
        let dock = document.getElementById('float-dock');
        if (!dock) {
            dock = document.createElement('div');
            dock.id = 'float-dock';
            dock.className = 'float-dock';
            const toolbar = document.getElementById('map-toolbar');
            if (toolbar) toolbar.appendChild(dock); else document.body.appendChild(dock);
        }
        return dock;
    }

    function addDockChip(id, opts) {
        removeDockChip(id);
        const btn = document.createElement('button');
        btn.className = 'toolbar-btn float-dock-btn ' + (opts.cls || '');
        btn.id = 'fui-chip-' + id;
        btn.title = opts.title || '';
        btn.setAttribute('aria-label', opts.title || 'Restore panel');
        btn.innerHTML = opts.svg + '<span class="fui-badge"></span>';
        btn.addEventListener('click', opts.onClick);
        getDock().appendChild(btn);
        if (opts.badge !== undefined) setDockBadge(id, opts.badge);
        return btn;
    }

    function setDockBadge(id, text) {
        const chip = document.getElementById('fui-chip-' + id);
        if (!chip) return;
        const b = chip.querySelector('.fui-badge');
        if (!b) return;
        if (text === null || text === '' || text === 0) { b.style.display = 'none'; }
        else { b.style.display = 'flex'; b.textContent = String(text); }
    }

    function removeDockChip(id) {
        const chip = document.getElementById('fui-chip-' + id);
        if (chip) chip.remove();
    }

    // Slim grab-bar shared by all floating panels: centered grabber pill,
    // tiny chevron (collapse) and dock icons on the right. Visually distinct
    // from action/export buttons.
    function makeBar(label) {
        const bar = document.createElement('div');
        bar.className = 'fui-bar';
        bar.setAttribute('role', 'toolbar');
        bar.setAttribute('aria-label', label + ' — drag to move, tap to collapse');
        bar.innerHTML =
            '<span class="fui-grabber" aria-hidden="true"></span>' +
            '<span class="fui-bar-btns">' +
            '<button class="fui-bar-btn" data-act="collapse" title="Collapse / expand" aria-label="Collapse ' + label + '"><i class="icon-chevron-up"></i></button>' +
            '<button class="fui-bar-btn" data-act="dock" title="Minimize to toolbar" aria-label="Minimize ' + label + ' to toolbar"><i class="icon-minimize-2"></i></button>' +
            '</span>';
        return bar;
    }

    // ---------------------------------------------------------------
    // Mobile sheet stack (≤640px — the breakpoint maptip already docks at)
    //
    // Every persistent floating card (park/AOI popup, pinned map-tip card,
    // pinned-layers indicator) registers here on a phone and becomes a
    // full-width bottom sheet. Sheets stack upward from the measured bottom
    // chrome (toolbar + time slider — measured, never assumed: both change
    // height on a narrow screen; see bottomChromePx in maptip.js for why
    // offsetParent cannot be the visibility guard). As many sheets stay
    // EXPANDED as the vertical budget allows, most-recently-used first;
    // the rest auto-collapse to their bar. Tapping a collapsed sheet
    // expands it (and may auto-collapse another). A user's own collapse
    // always wins over auto-expansion. Desktop is untouched.
    // ---------------------------------------------------------------
    const SHEET_BP = 640, SHEET_GAP = 8;
    const sheets = new Map();   // id -> {api, el, entered, userCollapsed, autoCollapsed, lastActive, expH, colH}
    let layoutQueued = false;
    let applyingLayout = false; // suppresses noteSheetToggle from layout's own setCollapsed calls

    // Portrait phone by width; landscape phone by height + coarse pointer
    // (a desktop window dragged short must not become a sheet).
    function isSheetMode() {
        return window.innerWidth <= SHEET_BP ||
            (window.innerHeight <= 480 && matchMedia('(pointer: coarse)').matches);
    }

    function sheetBottomPx() {
        let top = window.innerHeight;
        ['#map-toolbar', '.map-toolbar', '#time-slider-container'].forEach((sel) => {
            const n = document.querySelector(sel);
            if (!n || getComputedStyle(n).display === 'none') return;
            const r = n.getBoundingClientRect();
            // Bottom chrome STARTS in the lower half. In landscape the toolbar
            // is a full-height column on the left — its bottom edge is low but
            // it is not something to stack above.
            if (!r.height || r.top < window.innerHeight * 0.5) return;
            top = Math.min(top, r.top);
        });
        return Math.max(8, window.innerHeight - top + SHEET_GAP);
    }

    // The stats grid pins to the top of a phone screen; sheets must not
    // climb into it.
    function sheetTopPx() {
        const n = document.querySelector('.stats-panel');
        if (n && getComputedStyle(n).display !== 'none') {
            const r = n.getBoundingClientRect();
            if (r.height && r.top < window.innerHeight * 0.4) return r.bottom + SHEET_GAP;
        }
        return 8;
    }

    function sheetVisible(el) {
        return el.isConnected && getComputedStyle(el).display !== 'none';
    }

    function queueSheetLayout() {
        if (layoutQueued) return;
        layoutQueued = true;
        requestAnimationFrame(() => { layoutQueued = false; layoutSheets(); });
    }

    function layoutSheets() {
        // Published even with no sheets: the transient menus (geology mixer,
        // export menus) anchor to it via CSS.
        const bottom = sheetBottomPx();
        document.documentElement.style.setProperty('--fui-bottom', bottom + 'px');
        const mode = isSheetMode();
        const live = [];
        sheets.forEach((s) => {
            if (!s.el.isConnected) return;
            if (mode && !s.entered) {
                s.entered = true;
                try { s.api.onEnterSheet && s.api.onEnterSheet(); } catch (e) { }
                s.el.classList.add('fui-sheet');
            } else if (!mode && s.entered) {
                s.entered = false;
                s.el.classList.remove('fui-sheet');
                s.el.style.bottom = '';
                if (s.autoCollapsed) { s.autoCollapsed = false; try { s.api.setCollapsed(false); } catch (e) { } }
                try { s.api.onExitSheet && s.api.onExitSheet(); } catch (e) { }
            }
            if (mode && s.entered && sheetVisible(s.el)) live.push(s);
        });
        if (!mode || !live.length) return;

        const budget = Math.max(120, window.innerHeight - sheetTopPx() - bottom);
        const maxExp = Math.round(window.innerHeight * 0.45); // matches the sheets' CSS max-height
        const order = live.slice().sort((a, b) => b.lastActive - a.lastActive);

        // Pass 1: who stays expanded. Greedy by recency — the newest always
        // does; each older one does if its (remembered) expanded height still
        // fits the remaining budget.
        let used = 0;
        applyingLayout = true;
        order.forEach((s, i) => {
            const colH = s.colH || 44;
            const expH = Math.min(s.expH || maxExp, maxExp);
            const expand = !s.userCollapsed && (i === 0 || used + expH + SHEET_GAP <= budget);
            s.autoCollapsed = !expand && !s.userCollapsed;
            const collapsed = s.userCollapsed || s.autoCollapsed;
            try { if (s.api.isCollapsed() !== collapsed) s.api.setCollapsed(collapsed); } catch (e) { }
            used += (collapsed ? colH : expH) + SHEET_GAP;
        });
        applyingLayout = false;

        // Pass 2: position — most recent nearest the thumb (bottom).
        let y = bottom;
        order.forEach((s) => {
            s.el.style.bottom = y + 'px';
            const h = s.el.offsetHeight || 44;
            let collapsed = false;
            try { collapsed = s.api.isCollapsed(); } catch (e) { }
            if (collapsed) s.colH = h; else s.expH = h;   // remember real heights for next pass 1
            y += h + SHEET_GAP;
        });
    }

    function registerSheet(id, api) {
        unregisterSheet(id);
        const s = { api, el: api.el, entered: false, userCollapsed: false, autoCollapsed: false, lastActive: Date.now(), expH: 0, colH: 0 };
        // Tapping a collapsed sheet expands it. Capture-phase so it wins over
        // the card's own handlers — except the close controls, which must
        // still close a collapsed sheet.
        s.expandTap = (e) => {
            if (!s.entered || !(s.userCollapsed || s.autoCollapsed)) return;
            if (e.target.closest('[data-act=close], .maptip-close, .pinned-indicator-close, .maplibregl-popup-close-button')) return;
            e.stopPropagation(); e.preventDefault();
            s.userCollapsed = false; s.autoCollapsed = false; s.lastActive = Date.now();
            queueSheetLayout();
        };
        s.el.addEventListener('click', s.expandTap, true);
        // Any interaction bumps recency (used at the NEXT layout — no churn now).
        s.touch = () => { s.lastActive = Date.now(); };
        s.el.addEventListener('pointerdown', s.touch);
        // Async content grows a sheet after layout (popup sections load) —
        // restack so sheets never overlap.
        if (window.ResizeObserver) {
            s.ro = new ResizeObserver(() => { if (s.entered) queueSheetLayout(); });
            s.ro.observe(s.el);
        }
        sheets.set(id, s);
        queueSheetLayout();
        return s;
    }

    function unregisterSheet(id) {
        const s = sheets.get(id);
        if (!s) return;
        if (s.ro) s.ro.disconnect();
        s.el.removeEventListener('click', s.expandTap, true);
        s.el.removeEventListener('pointerdown', s.touch);
        if (s.entered) { s.el.classList.remove('fui-sheet'); s.el.style.bottom = ''; }
        sheets.delete(id);
        queueSheetLayout();
    }

    // The widgets' own collapse controls report user intent through this, so
    // auto-expansion never overrides a fold the user chose.
    function noteSheetToggle(id, collapsed) {
        if (applyingLayout) return;
        const s = sheets.get(id);
        if (!s) return;
        s.userCollapsed = collapsed;
        s.autoCollapsed = false;
        s.lastActive = Date.now();
        queueSheetLayout();
    }

    function sheetActive(id) {
        const s = sheets.get(id);
        return !!(s && s.entered);
    }

    window.addEventListener('resize', queueSheetLayout);
    (function watchChrome() {
        const attach = () => {
            const n = document.getElementById('time-slider-container');
            if (!n || !window.ResizeObserver) return false;
            new ResizeObserver(queueSheetLayout).observe(n);
            return true;
        };
        if (!attach()) document.addEventListener('DOMContentLoaded', attach);
        document.addEventListener('DOMContentLoaded', queueSheetLayout);
    })();

    const PIN_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1z"/></svg>';
    const POPUP_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>';

    // ---------------------------------------------------------------
    // Pinned Layers indicator
    // ---------------------------------------------------------------
    function setupPinnedIndicator() {
        const el = document.getElementById('pinned-indicator');
        const items = document.getElementById('pinned-items');
        if (!el || !items || el.dataset.fui) return;
        el.dataset.fui = '1';

        const header = el.querySelector('.pinned-indicator-header');
        const title = el.querySelector('.pinned-indicator-title');

        // Mini count shown when collapsed
        const mini = document.createElement('span');
        mini.className = 'fui-mini-count';
        title.appendChild(mini);

        // Slim grab-bar: drag to move, tap to collapse, tiny dock control.
        // The old header row is merged into the bar to save vertical space:
        // title goes left, clear-all × joins the bar controls, header hidden.
        const bar = makeBar('Pinned layers');
        el.insertBefore(bar, header);
        el.classList.add('fui-compact');
        bar.insertBefore(title, bar.firstChild);
        const closeBtn = header.querySelector('.pinned-indicator-close');
        if (closeBtn) bar.querySelector('.fui-bar-btns').appendChild(closeBtn);
        header.style.display = 'none';

        let savedScroll = 0;

        function setCollapsed(on, persist) {
            if (on) { savedScroll = items.scrollTop; el.classList.add('fui-collapsed'); }
            else {
                el.classList.remove('fui-collapsed');
                requestAnimationFrame(() => { items.scrollTop = savedScroll; });
            }
            if (persist !== false) store.set('pinned.collapsed', on);
        }

        function setDocked(on, persist) {
            if (on) {
                el.classList.add('fui-docked');
                if (el.classList.contains('active')) {
                    addDockChip('pinned', {
                        cls: 'pinned-dock', title: 'Show pinned layers',
                        svg: PIN_SVG, badge: chipCount(),
                        onClick: () => setDocked(false)
                    });
                }
            } else {
                el.classList.remove('fui-docked');
                removeDockChip('pinned');
            }
            if (persist !== false) store.set('pinned.docked', on);
        }

        function chipCount() {
            return items.querySelectorAll('.pinned-layer-chip').length;
        }

        function clampIntoViewport() {
            if (!el.classList.contains('fui-moved') || !el.classList.contains('active')) return;
            const r = el.getBoundingClientRect();
            const x = clamp(r.left, MARGIN, window.innerWidth - r.width - MARGIN);
            const y = clamp(r.top, MARGIN, window.innerHeight - r.height - MARGIN);
            el.style.left = x + 'px';
            el.style.top = y + 'px';
        }

        bar.querySelector('[data-act=collapse]').addEventListener('click', (e) => {
            e.stopPropagation();
            const on = !el.classList.contains('fui-collapsed');
            setCollapsed(on);
            noteSheetToggle('pinned', on);
        });
        bar.querySelector('[data-act=dock]').addEventListener('click', (e) => {
            e.stopPropagation();
            setDocked(true);
        });

        // Drag by bar (or header); tap toggles collapse. In sheet mode the
        // stack owns the position, so a drag never converts to fui-moved.
        const dragOpts = {
            getPos() { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top }; },
            onDragStart() {
                if (el.classList.contains('fui-sheet')) return false;
                // Switch from centered transform positioning to explicit left/top
                const r = el.getBoundingClientRect();
                el.classList.add('fui-moved');
                el.style.left = r.left + 'px';
                el.style.top = r.top + 'px';
            },
            setPos(x, y) {
                const r = el.getBoundingClientRect();
                el.style.left = clamp(x, MARGIN, window.innerWidth - r.width - MARGIN) + 'px';
                el.style.top = clamp(y, MARGIN, window.innerHeight - r.height - MARGIN) + 'px';
            },
            onDragEnd() {
                const r = el.getBoundingClientRect();
                store.set('pinned.pos', { x: r.left, y: r.top });
            },
            onTap() {
                const on = !el.classList.contains('fui-collapsed');
                setCollapsed(on);
                noteSheetToggle('pinned', on);
            }
        };
        enableDrag(bar, dragOpts);
        enableDrag(header, dragOpts);

        // On a phone the pinned-layers box joins the sheet stack.
        registerSheet('pinned', {
            el,
            isCollapsed: () => el.classList.contains('fui-collapsed'),
            setCollapsed: (on) => setCollapsed(on, false)   // auto-folds never persist
        });

        // Restore persisted state
        const pos = store.get('pinned.pos', null);
        if (pos && typeof pos.x === 'number') {
            el.classList.add('fui-moved');
            el.style.left = clamp(pos.x, MARGIN, Math.max(MARGIN, window.innerWidth - 220)) + 'px';
            el.style.top = clamp(pos.y, MARGIN, Math.max(MARGIN, window.innerHeight - 100)) + 'px';
        }
        if (store.get('pinned.collapsed', false)) setCollapsed(true, false);
        if (store.get('pinned.docked', false)) setDocked(true, false);

        // Keep dock chip badge + mini count in sync with pin contents
        function sync() {
            const n = chipCount();
            mini.textContent = n > 0 ? n : '';
            if (el.classList.contains('fui-docked')) {
                if (el.classList.contains('active') && n > 0) {
                    if (!document.getElementById('fui-chip-pinned')) {
                        addDockChip('pinned', {
                            cls: 'pinned-dock', title: 'Show pinned layers',
                            svg: PIN_SVG, badge: n,
                            onClick: () => setDocked(false)
                        });
                    } else { setDockBadge('pinned', n); }
                } else {
                    removeDockChip('pinned');
                }
            }
            clampIntoViewport();
            queueSheetLayout();   // becoming .active / chip changes affect the sheet stack
        }
        new MutationObserver(sync).observe(items, { childList: true, subtree: true });
        new MutationObserver(sync).observe(el, { attributes: true, attributeFilter: ['class'] });

        window.addEventListener('resize', clampIntoViewport);
    }

    // ---------------------------------------------------------------
    // Stats panel (desktop): standard window furniture + time-based rest
    //
    // The panel is the app's legend. On desktop it wore none of the app's
    // furniture, so nothing said it could get out of the way — and its only
    // × was the focus scope's "leave focus", which read as "close panel".
    // Now it wears the same .fui-bar every floating surface wears (grabber,
    // chevron; drag to move, tap to collapse), and it borrows the map
    // strip's rest behaviour (maplegend.js): a few seconds untouched and it
    // folds to the bar + the focus line + the map chip; any attention
    // unfolds it. Manual collapse persists; rest never does — rest is the
    // panel getting out of the way, not the user's choice.
    //
    // The bar is desktop-only (CSS ≤768px hides it): on a phone the panel is
    // a top-pinned grid with its own compaction, and the map strip already
    // rests itself there.
    // ---------------------------------------------------------------
    // ---------------------------------------------------------------
    // Stats panel (desktop): standard window furniture, manual compaction
    //
    // The panel is the app's legend. On desktop it wore none of the app's
    // furniture, so nothing said it could get out of the way — and its only
    // × was the focus scope's "leave focus", which read as "close panel".
    // Now it wears the same .fui-bar every floating surface wears (grabber,
    // chevron; drag to move, tap to collapse). Collapse COMPACTS, it does
    // not empty: users still need the legend, so every row keeps its label,
    // value, colour accent and click target — it just drops the section
    // headers, dividers and LOD sub-lines and tightens the spacing. The
    // fold is the USER's choice only (persists in localStorage); there is
    // deliberately no timer — an auto-fold took the legend away mid-read.
    //
    // The bar is desktop-only (CSS ≤768px hides it): on a phone the panel is
    // a top-pinned grid with its own compaction.
    // ---------------------------------------------------------------
    function setupStatsPanel() {
        const el = document.querySelector('.stats-panel');
        if (!el || el.dataset.fui) return;
        el.dataset.fui = '1';

        const bar = makeBar('Statistics');
        // No dock: docking would hide the legend entirely; compacting is
        // as far as it folds.
        const dockBtn = bar.querySelector('[data-act=dock]');
        if (dockBtn) dockBtn.remove();
        // "Snap home": after a drag, one tap returns the panel to its CSS
        // place (top-right). Only visible while the panel is displaced
        // (.fui-moved) — a home button on a panel that is home is noise.
        const homeBtn = document.createElement('button');
        homeBtn.className = 'fui-bar-btn';
        homeBtn.setAttribute('data-act', 'home');
        homeBtn.title = 'Snap back to its usual corner';
        homeBtn.setAttribute('aria-label', 'Snap statistics panel back to its usual corner');
        homeBtn.innerHTML = '<i class="icon-house"></i>';
        const btns = bar.querySelector('.fui-bar-btns');
        btns.insertBefore(homeBtn, btns.firstChild);
        homeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            el.classList.remove('fui-moved');
            el.style.left = '';
            el.style.top = '';
            store.del('stats.pos');
        });
        el.insertBefore(bar, el.firstChild);

        function desktop() { return window.innerWidth > 768; }

        function setCollapsed(on, persist) {
            el.classList.toggle('fui-collapsed', on);
            const b = bar.querySelector('[data-act=collapse]');
            if (b) b.setAttribute('aria-expanded', String(!on));
            if (persist !== false) store.set('stats.collapsed', on);
        }

        bar.querySelector('[data-act=collapse]').addEventListener('click', (e) => {
            e.stopPropagation();
            setCollapsed(!el.classList.contains('fui-collapsed'));
        });

        // Drag by the bar; tap toggles collapse. Same gestures as every
        // other floating surface.
        enableDrag(bar, {
            getPos() { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top }; },
            onDragStart() {
                if (!desktop()) return false;
                const r = el.getBoundingClientRect();
                el.classList.add('fui-moved');
                el.style.left = r.left + 'px';
                el.style.top = r.top + 'px';
            },
            setPos(x, y) {
                const r = el.getBoundingClientRect();
                el.style.left = clamp(x, MARGIN, window.innerWidth - r.width - MARGIN) + 'px';
                el.style.top = clamp(y, MARGIN, window.innerHeight - r.height - MARGIN) + 'px';
            },
            onDragEnd() {
                const r = el.getBoundingClientRect();
                store.set('stats.pos', { x: r.left, y: r.top });
            },
            onTap() { setCollapsed(!el.classList.contains('fui-collapsed')); }
        });

        // Restore persisted state (desktop only — the phone layout owns itself)
        if (desktop()) {
            const pos = store.get('stats.pos', null);
            if (pos && typeof pos.x === 'number') {
                el.classList.add('fui-moved');
                el.style.left = clamp(pos.x, MARGIN, Math.max(MARGIN, window.innerWidth - 180)) + 'px';
                el.style.top = clamp(pos.y, MARGIN, Math.max(MARGIN, window.innerHeight - 100)) + 'px';
            }
            if (store.get('stats.collapsed', false)) setCollapsed(true, false);
        }

        statsWidthGovernor(el);
    }

    // ── WIDTH IS GOVERNED, NOT FOLLOWED ────────────────────────────────
    //
    // The stats panel is shrink-to-fit over content that changes whenever the
    // map moves: "1,771,204" becomes "0" one zoom step later, the in-view
    // readout goes from "30,000 of 38,725 in view" to "12 in view", the LOD
    // pill's word changes length. Pinned to the RIGHT edge, every one of those
    // moves the panel's LEFT edge and every label with it — so a zoom sequence
    // made the whole panel jitter horizontally under the cursor. That is the
    // complaint this exists to fix, and the two halves of the fix are
    // independent:
    //
    //   1. ANIMATE the change (CSS `transition: width`) so a resize is a
    //      movement the eye can follow rather than a teleport; and
    //   2. RESIST it — asymmetric hysteresis, the standard treatment for a
    //      value that is noisy in one direction. GROW IMMEDIATELY (the content
    //      does not fit; withholding space would clip a number, and a clipped
    //      number is a wrong number) but SHRINK ONLY AFTER THE CONTENT HAS
    //      BEEN STILL for QUIET_MS. A zoom-in/zoom-out that returns to the
    //      same numbers therefore costs zero width changes, and a long zoom
    //      sequence costs one settle at the end instead of one per step.
    //
    // Shrinking also ignores changes under SHRINK_EPS px: a digit's worth of
    // width is not worth an animation.
    //
    // Measurement is the subtle part — with an explicit width applied, the
    // element's own box no longer tells you what the content wants. So the
    // measurement removes the width for one synchronous read (one forced
    // layout, only when the content actually changed, never during the
    // transition) and puts it straight back.
    function statsWidthGovernor(el) {
        const QUIET_MS = 700;      // content must be still this long before shrinking
        const SHRINK_EPS = 6;      // px; below this a shrink is not worth animating
        const GROW_EPS = 1;
        let applied = null, shrinkTimer = null, raf = null, animTimer = null;

        const enabled = () => window.innerWidth > 768
            && !window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        function natural() {
            const prev = el.style.width;
            el.style.width = 'auto';
            const w = el.getBoundingClientRect().width;
            el.style.width = prev;
            return Math.ceil(w);
        }

        function apply(w) {
            if (applied === w) return;
            applied = w;
            el.style.width = w + 'px';
            // Clip only WHILE the width animates: a shrink would otherwise
            // show the content overflowing its own border for 280 ms. Outside
            // the animation the panel must not clip, or a menu opened from a
            // row would be cut off by its own container.
            el.classList.add('w-anim');
            clearTimeout(animTimer);
            animTimer = setTimeout(() => el.classList.remove('w-anim'), 320);
        }

        function measure() {
            raf = null;
            if (!enabled()) { el.style.width = ''; applied = null; return; }
            const want = natural();
            if (applied === null) { applied = want; el.style.width = want + 'px'; return; }
            if (want > applied + GROW_EPS) {          // grow now
                clearTimeout(shrinkTimer); shrinkTimer = null;
                apply(want);
                return;
            }
            if (want < applied - SHRINK_EPS) {        // shrink later, if it lasts
                clearTimeout(shrinkTimer);
                shrinkTimer = setTimeout(() => {
                    shrinkTimer = null;
                    if (!enabled()) return;
                    const w = natural();
                    if (w < applied - SHRINK_EPS) apply(w);
                }, QUIET_MS);
            }
        }

        function schedule() { if (raf === null) raf = requestAnimationFrame(measure); }

        // Content changes are the trigger. Two things the observer must NOT
        // react to, both of which this function itself causes: the panel's
        // `style` (we write the width) and the `w-anim` class (we toggle it
        // for the duration of the transition). Reacting to the latter
        // re-baselined the governor a third of a second after every change and
        // left the panel unmanaged again — a feedback loop that looks exactly
        // like "it works, then stops working". Only a change of the COLLAPSED
        // state is a deliberate resize by the user, and only that re-baselines.
        let wasCollapsed = el.classList.contains('fui-collapsed');
        new MutationObserver((muts) => {
            let relevant = false;
            for (const m of muts) {
                if (m.type === 'attributes' && m.target === el) {
                    if (m.attributeName === 'style') continue;      // written by us
                    const now = el.classList.contains('fui-collapsed');
                    if (now === wasCollapsed) continue;             // w-anim, awaiting, …
                    wasCollapsed = now;
                    applied = null; el.style.width = '';
                }
                relevant = true;
            }
            if (relevant) schedule();
        }).observe(el, {
            childList: true, subtree: true, characterData: true,
            attributes: true, attributeFilter: ['class', 'hidden', 'style']
        });
        window.addEventListener('resize', () => { el.style.width = ''; applied = null; schedule(); });
        // Fonts land after first paint and change every measurement.
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);
        schedule();
    }

    // ---------------------------------------------------------------
    // Park popup (MapLibre popup with .pa-popup content)
    // ---------------------------------------------------------------
    function decoratePAPopup(popup) {
        if (!popup) return;
        let container;
        try { container = popup.getElement(); } catch (e) { return; }
        if (!container || container.dataset.fui) return;
        container.dataset.fui = '1';

        const content = container.querySelector('.pa-popup');
        const header = container.querySelector('.pa-popup-header');
        // `> span` only, not `> span:last-child`: the AOI popup puts a rename
        // pencil after the name, so the name span stopped being the last child
        // and this guard silently returned — taking the grab bar, the minimise
        // button and MapLibre's × with it. The guard only ever meant “does this
        // look like a PA popup”.
        const nameRight = container.querySelector('.pa-popup-name > span');
        if (!content || !header || !nameRight) return;

        const parkName = (container.querySelector('.pa-popup-name span')?.textContent || 'Park').trim();
        let savedScroll = 0;
        let detached = false;
        const rawUpdate = popup._update; // MapLibre's bound update fn (registered on map 'move')

        // --- slim grab-bar above the header (distinct from export buttons) ---
        const bar = makeBar(parkName);
        content.insertBefore(bar, content.firstChild);
        // THE CLOSE BUTTON IS OURS, NOT A RELOCATED ONE.
        //
        // MapLibre's \u00d7 used to be *moved* into the bar and left to its own
        // internal listener. Two things then had to hold that we do not
        // control: that the listener survives re-parenting, and that the
        // browser still resolves a click on it to it \u2014 the bar is a drag
        // handle that calls setPointerCapture, and a captured pointer is
        // exactly where browsers disagree about the click target (Safari
        // charges it to the capturing element, so the \u00d7 did nothing and the
        // card could not be dismissed). MapLibre's \u00d7 is hidden and the bar
        // gets a real button of its own, next to collapse and minimise, closed
        // by the same code path the app already calls (`popup.remove()`).
        const mlClose = container.querySelector('.maplibregl-popup-close-button');
        if (mlClose) mlClose.style.display = 'none';
        const closeBtn = document.createElement('button');
        closeBtn.className = 'fui-bar-btn';
        closeBtn.setAttribute('data-act', 'close');
        closeBtn.title = 'Close';
        closeBtn.setAttribute('aria-label', 'Close ' + parkName);
        closeBtn.innerHTML = '<i class="icon-x"></i>';
        bar.querySelector('.fui-bar-btns').appendChild(closeBtn);
        closeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            e.preventDefault();
            try { popup.remove(); } catch (err) { }
        });

        function setCollapsed(on) {
            if (on) { savedScroll = content.scrollTop; container.classList.add('fui-collapsed'); }
            else {
                container.classList.remove('fui-collapsed');
                requestAnimationFrame(() => { content.scrollTop = savedScroll; });
            }
            // Re-anchor cleanly if still attached to the map point (never in
            // sheet mode, where the stack owns the popup's position)
            if (!detached && !container.classList.contains('fui-sheet') && rawUpdate) { try { rawUpdate.call(popup); } catch (e) { } }
        }

        function detach() {
            if (detached) return;
            detached = true;
            const r = container.getBoundingClientRect();
            const mapEl = container.offsetParent || container.parentElement;
            const mr = mapEl.getBoundingClientRect();
            // Stop MapLibre repositioning the popup on map move: unhook its
            // bound _update from map events, then neuter the method.
            try {
                if (popup._map && popup._update) {
                    popup._map.off('move', popup._update);
                    popup._map.off('moveend', popup._update);
                }
            } catch (e) { }
            popup._update = function () { };
            container.classList.add('fui-detached');
            container.style.transform = 'none';
            container.style.left = (r.left - mr.left) + 'px';
            container.style.top = (r.top - mr.top) + 'px';
            container.style.maxWidth = r.width + 'px';
        }

        function setDocked(on) {
            if (on) {
                detach(); // keep position stable when restored
                container.classList.add('fui-docked');
                const initials = parkName.split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
                addDockChip('popup', {
                    cls: 'popup-dock', title: 'Show ' + parkName + ' details',
                    svg: POPUP_SVG, badge: initials,
                    onClick: () => setDocked(false)
                });
            } else {
                container.classList.remove('fui-docked');
                removeDockChip('popup');
                clampIntoView();
            }
        }

        function clampIntoView() {
            if (!detached) return;
            const mapEl = container.offsetParent || container.parentElement;
            const mr = mapEl.getBoundingClientRect();
            const r = container.getBoundingClientRect();
            const x = clamp(r.left - mr.left, MARGIN, Math.max(MARGIN, mr.width - r.width - MARGIN));
            const y = clamp(r.top - mr.top, MARGIN, Math.max(MARGIN, mr.height - 60));
            container.style.left = x + 'px';
            container.style.top = y + 'px';
        }

        bar.querySelector('[data-act=collapse]').addEventListener('click', (e) => {
            e.stopPropagation();
            const on = !container.classList.contains('fui-collapsed');
            setCollapsed(on);
            noteSheetToggle('popup', on);
        });
        bar.querySelector('[data-act=dock]').addEventListener('click', (e) => {
            e.stopPropagation();
            setDocked(true);
        });

        // Drag by bar or header; tap toggles collapse. In sheet mode (phone)
        // the stack owns the popup's position, so a drag never detaches it.
        const dragOpts = {
            getPos() { const r = container.getBoundingClientRect(); return { x: r.left, y: r.top }; },
            onDragStart() { if (container.classList.contains('fui-sheet')) return false; detach(); },
            setPos(x, y) {
                const mapEl = container.offsetParent || container.parentElement;
                const mr = mapEl.getBoundingClientRect();
                const r = container.getBoundingClientRect();
                container.style.left = clamp(x - mr.left, MARGIN, Math.max(MARGIN, mr.width - r.width - MARGIN)) + 'px';
                container.style.top = clamp(y - mr.top, MARGIN, Math.max(MARGIN, mr.height - 60)) + 'px';
            },
            onTap() {
                const on = !container.classList.contains('fui-collapsed');
                setCollapsed(on);
                noteSheetToggle('popup', on);
            }
        };
        enableDrag(bar, dragOpts);
        enableDrag(header, dragOpts);

        // FREEZE ON MAP INTERACTION (desktop).
        //
        // A card this size following the ground is not "anchored", it is a
        // panel that leaps across the screen every time you drag the map —
        // and it is the panel you are reading. So the anchor is only used to
        // PLACE it: the first user-driven map move hands the position over to
        // the screen (detach), where it stays until the user moves it, resizes
        // the window, or opens a new card. Programmatic moves (the fly-to that
        // accompanies opening the popup, a share-link restore) have no
        // originalEvent and keep the popup anchored, so it still lands on its
        // area.
        if (popup._map) {
            const freeze = (e) => {
                if (detached) return;
                if (!e || !e.originalEvent) return;          // programmatic move: stay anchored
                if (container.classList.contains('fui-sheet')) return;   // phone: stack owns it
                detach();
                // The anchor may have carried the card partly off-screen (a
                // tall AOI popup anchored near the top edge shows only its
                // lowest accordion row). Freezing it THERE strands the grab
                // bar outside the viewport with no gesture that can bring it
                // back — so the hand-over to the screen always ends with the
                // bar reachable.
                clampIntoView();
            };
            ['movestart', 'dragstart', 'zoomstart', 'rotatestart', 'pitchstart', 'wheel'].forEach((ev) => {
                try { popup._map.on(ev, freeze); } catch (err) { }
            });
            popup.on('close', () => {
                ['movestart', 'dragstart', 'zoomstart', 'rotatestart', 'pitchstart', 'wheel'].forEach((ev) => {
                    try { popup._map.off(ev, freeze); } catch (err) { }
                });
            });
        }

        // ENSURE THE GRAB BAR IS ON SCREEN AFTER PLACEMENT.
        //
        // Staying anchored through programmatic moves (above) assumes the
        // anchor point puts the card somewhere usable. A share-link restore
        // opens the popup at the area's centroid with the link's own lat/lng/z,
        // and for a large AOI the centroid can sit above the viewport — the
        // card renders with its top (grab bar, name, ×) off-screen, and
        // nothing brings it back until the first user gesture happens to
        // detach it. So once the map settles, if the bar is not visible,
        // hand the card to the screen and clamp it into view.
        function ensureVisible() {
            if (detached || container.classList.contains('fui-sheet') ||
                container.classList.contains('fui-docked')) return;
            const mapEl = container.offsetParent || container.parentElement;
            if (!mapEl) return;
            const mr = mapEl.getBoundingClientRect();
            const r = container.getBoundingClientRect();
            if (r.top < mr.top + 2 || r.top > mr.bottom - 60 ||
                r.right < mr.left + 60 || r.left > mr.right - 60) {
                detach();
                clampIntoView();
            }
        }
        if (popup._map) {
            try { popup._map.once('idle', ensureVisible); } catch (err) { }
        }
        requestAnimationFrame(ensureVisible);

        // The popup's sections load asynchronously (narratives, versions,
        // accordions), so the card can GROW past the top edge long after the
        // one-time ensureVisible passed — an anchored card grows upward from
        // its anchor. Watch the content's size: detached cards re-clamp,
        // anchored ones re-check visibility (but never mid-flight — a fly-to
        // in progress puts the card anywhere for a frame).
        if (window.ResizeObserver) {
            const ro = new ResizeObserver(() => {
                if (container.classList.contains('fui-sheet') ||
                    container.classList.contains('fui-docked')) return;
                if (detached) { clampIntoView(); return; }
                try { if (popup._map && popup._map.isMoving()) return; } catch (err) { }
                ensureVisible();
            });
            ro.observe(content);
            popup.on('close', () => { try { ro.disconnect(); } catch (err) { } });
        }

        // On a phone the popup joins the sheet stack: full-width above the
        // bottom chrome, stacked with the pinned card / pinned layers. The
        // .fui-sheet CSS out-!importants MapLibre's inline transform, so its
        // _update can keep running and re-anchoring stays intact on exit.
        registerSheet('popup', {
            el: container,
            isCollapsed: () => container.classList.contains('fui-collapsed'),
            setCollapsed: setCollapsed
        });

        // Cleanup dock chip + sheet slot when popup closes / is replaced
        popup.on('close', () => { removeDockChip('popup'); unregisterSheet('popup'); });

        window.addEventListener('resize', clampIntoView);
    }

    // ---------------------------------------------------------------
    // Init
    // ---------------------------------------------------------------
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => { setupPinnedIndicator(); setupStatsPanel(); });
    } else {
        setupPinnedIndicator();
        setupStatsPanel();
    }

    window.FloatUI = { decoratePAPopup, enableDrag, addDockChip, removeDockChip, setDockBadge,
        registerSheet, unregisterSheet, noteSheetToggle, sheetActive,
        sheetLayout: queueSheetLayout, isSheetMode, sheetBottomPx };
})();
