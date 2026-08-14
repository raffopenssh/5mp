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
            setCollapsed(!el.classList.contains('fui-collapsed'));
        });
        bar.querySelector('[data-act=dock]').addEventListener('click', (e) => {
            e.stopPropagation();
            setDocked(true);
        });

        // Drag by bar (or header); tap toggles collapse
        const dragOpts = {
            getPos() { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top }; },
            onDragStart() {
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
            onTap() { setCollapsed(!el.classList.contains('fui-collapsed')); }
        };
        enableDrag(bar, dragOpts);
        enableDrag(header, dragOpts);

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
            // Re-anchor cleanly if still attached to the map point
            if (!detached && rawUpdate) { try { rawUpdate.call(popup); } catch (e) { } }
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
            setCollapsed(!container.classList.contains('fui-collapsed'));
        });
        bar.querySelector('[data-act=dock]').addEventListener('click', (e) => {
            e.stopPropagation();
            setDocked(true);
        });

        // Drag by bar or header; tap toggles collapse
        const dragOpts = {
            getPos() { const r = container.getBoundingClientRect(); return { x: r.left, y: r.top }; },
            onDragStart() { detach(); },
            setPos(x, y) {
                const mapEl = container.offsetParent || container.parentElement;
                const mr = mapEl.getBoundingClientRect();
                const r = container.getBoundingClientRect();
                container.style.left = clamp(x - mr.left, MARGIN, Math.max(MARGIN, mr.width - r.width - MARGIN)) + 'px';
                container.style.top = clamp(y - mr.top, MARGIN, Math.max(MARGIN, mr.height - 60)) + 'px';
            },
            onTap() { setCollapsed(!container.classList.contains('fui-collapsed')); }
        };
        enableDrag(bar, dragOpts);
        enableDrag(header, dragOpts);

        // Cleanup dock chip when popup closes / is replaced
        popup.on('close', () => removeDockChip('popup'));

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

    window.FloatUI = { decoratePAPopup, addDockChip, removeDockChip, setDockBadge };
})();
