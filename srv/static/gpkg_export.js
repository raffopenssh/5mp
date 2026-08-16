// ============================================================
// 5MP GeoPackage export — the "GeoPackage" entry in a park's or
// an AOI's download menu.
//
// The file is every layer we hold for the area, whole: for
// XSA_Study_Area that is millions of fire detections and hundreds
// of MB, minutes of work, well past the server's WriteTimeout. So
// it is a job, and the card here is how the user watches it.
//
// Same shape as AOIProgress, and for the same reason: the card is
// NOT client state, it is a `notifications` row the server rewrites
// in place. Close the tab, come back tomorrow, the link is still
// there — the export lives 21 days, so a link mailed to a colleague
// still works next week and asking for the same file twice returns
// the one already built instead of rebuilding it.
//
// The two differences from AOIProgress are deliberate:
//   * polling is seconds, not minutes (a build is minutes, an AOI
//     ingest is days), and
//   * the terminal state carries a download button, because the
//     whole point of the job was to produce a file.
// ============================================================
(function () {
    'use strict';

    const POLL_MS = 2500;
    const watched = new Map();   // job id -> {timer}
    const cache = new Map();     // job id -> last payload
    const TERMINAL = { ready: 1, failed: 1, expired: 1 };

    function pwd() { return (typeof getPwd === 'function' ? getPwd() : ''); }
    function esc(s) {
        return (typeof escapeHtml === 'function') ? escapeHtml(s)
            : String(s == null ? '' : s).replace(/[&<>"']/g, c =>
                ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    // Start (or adopt) an export. `opts.aoi` picks the /api/aois prefix; the
    // handlers are otherwise identical, and the server decides everything else.
    async function start(areaId, areaName, opts) {
        opts = opts || {};
        const base = opts.aoi ? 'aois' : 'parks';
        const qs = new URLSearchParams({ pwd: pwd() });
        if (typeof dateFrom !== 'undefined' && dateFrom) qs.set('from', dateFrom);
        if (typeof dateTo !== 'undefined' && dateTo) qs.set('to', dateTo);
        if (opts.refresh) qs.set('refresh', '1');
        // Absent means "everything". Only the explicit lighter variant sends
        // raw=0, so the parameter never appears in the common case and old
        // links keep meaning what they meant.
        if (opts.raw === false) qs.set('raw', '0');
        let d;
        try {
            const r = await fetch(`/api/${base}/${encodeURIComponent(areaId)}/export.gpkg?${qs}`,
                                  { method: 'POST' });
            if (!r.ok) throw new Error(await r.text());
            d = await r.json();
        } catch (e) {
            if (typeof showToast === 'function') showToast('Could not start the export: ' + e.message, 'error');
            return null;
        }
        cache.set(d.id, d);
        if (d.state === 'ready') {
            // Already built (this is a cache, so asking twice is normal and
            // cheap). Download straight away rather than opening the bell on a
            // card that says "done": the user asked for a file, and making them
            // find a second button to get the thing they just requested is
            // friction with no purpose. The card still exists for later.
            if (typeof showToast === 'function') {
                showToast(`“${areaName || areaId}” GeoPackage is ready — downloading`, 'success',
                          { key: 'gpkg-' + d.id });
            }
            download(d.id);
        } else {
            if (typeof showToast === 'function') {
                showToast(`Building the GeoPackage for “${areaName || areaId}” — watch the bell, `
                        + `the download stays available for 21 days`, 'info', { key: 'gpkg-' + d.id });
            }
            track(d.id);
            if (typeof loadNotifications === 'function') setTimeout(loadNotifications, 700);
        }
        return d;
    }

    // The viewport export: "a GeoPackage of exactly what is on my screen".
    //
    // Same job, same card, same 21-day link, same delete button as the area
    // export — a view export is a different QUESTION, not a different
    // mechanism (srv/gpkg_view.go). The one behavioural difference is that it
    // is usually small enough to be a download rather than a notification, so
    // it asks the server to hold the request briefly (`wait`) and only falls
    // back to the bell if the build is genuinely long. Both outcomes are
    // announced, because "nothing appeared to happen" is the failure this is
    // guarding against in either direction.
    //
    // opts: {bbox:[w,s,e,n], from, to, at, layers:[], aoi, area, label, wait}
    async function startView(opts) {
        opts = opts || {};
        if (!opts.bbox || opts.bbox.length !== 4) return null;
        const qs = new URLSearchParams({ pwd: pwd() });
        qs.set('bbox', opts.bbox.map(v => Number(v).toFixed(4)).join(','));
        if (opts.from) qs.set('from', opts.from);
        if (opts.to) qs.set('to', opts.to);
        if (opts.at) qs.set('at', opts.at);
        if (opts.layers && opts.layers.length) qs.set('layers', opts.layers.join(','));
        if (opts.aoi) qs.set('aoi', opts.aoi);
        if (opts.area) qs.set('area', opts.area);
        if (opts.refresh) qs.set('refresh', '1');
        // Long enough that the common case (one screen, a few MB) lands as a
        // download; short enough that it can never be why a request times out.
        qs.set('wait', String(opts.wait || 8));
        const what = opts.label || 'this view';
        let d;
        try {
            const r = await fetch(`/api/view/export.gpkg?${qs}`, { method: 'POST' });
            if (!r.ok) throw new Error(await r.text());
            d = await r.json();
        } catch (e) {
            if (typeof showToast === 'function') showToast('Could not start the export: ' + e.message, 'error');
            return null;
        }
        cache.set(d.id, d);
        if (d.state === 'ready') {
            if (typeof showToast === 'function') {
                showToast(`GeoPackage of ${what} is ready — downloading`
                        + (d.size_bytes ? ` (${fmtBytes(d.size_bytes)})` : ''), 'success',
                          { key: 'gpkg-' + d.id });
            }
            download(d.id);
            if (typeof loadNotifications === 'function') setTimeout(loadNotifications, 700);
        } else if (d.state === 'failed') {
            if (typeof showToast === 'function') showToast('Export failed: ' + (d.error || 'unknown'), 'error');
        } else {
            if (typeof showToast === 'function') {
                showToast(`This view is a big one — building the GeoPackage in the background. `
                        + `Watch the bell; the download stays available for 21 days, `
                        + `or delete it there when you're done.`, 'info', { key: 'gpkg-' + d.id });
            }
            track(d.id);
            if (typeof loadNotifications === 'function') setTimeout(loadNotifications, 700);
        }
        return d;
    }

    // Is this exact export already a file? Side-effect free (`peek=1`), so it
    // can be asked by a share link that merely *points* at an entry.
    // Returns the job, or null (404 = nothing built for this window).
    async function peek(areaId, opts) {
        opts = opts || {};
        const base = opts.aoi ? 'aois' : 'parks';
        const qs = new URLSearchParams({ pwd: pwd(), peek: '1' });
        if (typeof dateFrom !== 'undefined' && dateFrom) qs.set('from', dateFrom);
        if (typeof dateTo !== 'undefined' && dateTo) qs.set('to', dateTo);
        if (opts.raw === false) qs.set('raw', '0');
        try {
            const r = await fetch(`/api/${base}/${encodeURIComponent(areaId)}/export.gpkg?${qs}`,
                                  { cache: 'no-store' });
            if (!r.ok) return null;
            const d = await r.json();
            cache.set(d.id, d);
            return d;
        } catch (e) {
            return null;
        }
    }

    // A hidden <a download>, not window.open().
    //
    // The common path is: click a menu entry → POST → the server answers "this
    // one is already built" → download. That answer arrives after an await, by
    // which point the browser no longer considers this a user gesture and
    // silently blocks window.open — so the click that was supposed to hand the
    // user a file appeared to do nothing at all. A same-origin anchor click is
    // a navigation, not a popup, and is not subject to that rule; the server's
    // Content-Disposition then keeps the page in place.
    function download(id) {
        const a = document.createElement('a');
        a.href = `/api/geopackage/${encodeURIComponent(id)}/download?pwd=${encodeURIComponent(pwd())}`;
        a.rel = 'noopener';
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        setTimeout(() => a.remove(), 0);
    }

    async function poll(id) {
        try {
            const r = await fetch(`/api/geopackage/${encodeURIComponent(id)}?pwd=${encodeURIComponent(pwd())}`,
                                  { cache: 'no-store' });
            if (r.status === 404) {
                // The job is gone (cancelled elsewhere, swept, or orphaned by a
                // restart). A card that can never resolve must not sit at
                // "checking…" — remove it; the server's sweeper drops the
                // notification row the same way.
                stop(id);
                cache.delete(id);
                const card = document.querySelector(`[data-gpkg-id="${cssEsc(id)}"]`);
                if (card) card.remove();
                return null;
            }
            if (!r.ok) { stop(id); return null; }
            const d = await r.json();
            cache.set(id, d);
            paint(id, d);
            if (TERMINAL[d.state]) {
                stop(id);
                if (d.state === 'ready' && typeof loadNotifications === 'function') loadNotifications();
            } else {
                const w = watched.get(id);
                if (w) w.timer = setTimeout(() => poll(id), POLL_MS);
            }
            return d;
        } catch (e) {
            const w = watched.get(id);
            if (w) w.timer = setTimeout(() => poll(id), POLL_MS * 3);
            return null;
        }
    }

    function track(id) {
        if (!id || watched.has(id)) return;
        const known = cache.get(id);
        if (known && TERMINAL[known.state]) return;
        watched.set(id, { timer: null });
        poll(id);
    }

    function stop(id) {
        const w = watched.get(id);
        if (w) clearTimeout(w.timer);
        watched.delete(id);
    }

    function paint(id, d) {
        const el = document.querySelector(`[data-gpkg-job="${CSS.escape(id)}"]`);
        if (el && d) { el.innerHTML = bodyHTML(d); mountTags(el); }
    }

    // Tag chips are a control, not markup, so any HTML this module injects has
    // to be walked once afterwards. Read-only here: the SHARE LINK owns its
    // tags, and offering to edit them on a file card would put two writers on
    // one fact. Drawn by the shared control (srv/static/tagchips.js) so a tag
    // looks like a tag here, in the share dialog and in Admin alike.
    function mountTags(root) {
        if (typeof TagChips === 'undefined') return;
        (root || document).querySelectorAll('.gpkg-tags:not([data-tc])').forEach((el) => {
            el.dataset.tc = '1';
            TagChips.mount(el, {
                tags: (el.dataset.tags || '').split(',').filter(Boolean),
                editable: false
            });
        });
    }

    function fmtBytes(n) {
        if (!n) return '';
        if (n >= 1 << 30) return (n / (1 << 30)).toFixed(1) + ' GB';
        if (n >= 1 << 20) return Math.round(n / (1 << 20)) + ' MB';
        return Math.max(1, Math.round(n / 1024)) + ' KB';
    }

    function bodyHTML(d) {
        const pct = Math.max(0, Math.min(100, Math.round((d.progress || 0) * 100)));
        // A guest may build and download, but not delete or cancel: the job is
        // a cache shared with the link's owner, and removing the owner's file
        // is a write. Don't draw buttons that would 401.
        const guest = !!window.IS_GUEST;
        const delBtn = guest ? '' : `
                  <button class="gpkg-btn danger" title="Delete this file now — it would otherwise be removed automatically after 21 days"
                    onclick="event.stopPropagation();GeoPackageExport.remove('${esc(d.id)}')"><i class="icon-trash-2"></i></button>`;
        if (d.state === 'ready') {
            const n = (d.layers || []).reduce((a, l) => a + (l.count || 0), 0);
            const until = d.expires_at
                ? new Date(d.expires_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
                : null;
            // Days until the sweeper purges the file — the number the user
            // actually reasons with ("will my link still work next month?").
            const daysLeft = d.expires_at
                ? Math.max(0, Math.ceil((new Date(d.expires_at) - Date.now()) / 86400000))
                : null;
            // The purpose tags of the live guest link that keeps this file
            // alive — the answer to "why is this being retained". Read-only
            // here (the link owns them; editing happens where the link is),
            // but drawn by the SAME control as everywhere else, so a tag looks
            // like a tag in all three places it appears. Several, because a
            // file can be cited by a report and handed out at a workshop.
            const linkTags = d.link_tags || (d.link_tag ? [d.link_tag] : []);
            const tag = linkTags.length
                ? ` · <span class="gpkg-tags" data-tags="${esc(linkTags.join(','))}"></span>`
                : '';
            // The renew control appears only when the file is CLOSE to being
            // swept (≤14 days) — a single calendar-plus icon, same logic as
            // share-key renewal in Admin. An always-on "+30 days" taught
            // people to click it ritually, which is how files live forever.
            const renew = until && !guest && daysLeft !== null && daysLeft <= 14
                ? ` <button class="gpkg-btn gpkg-renew" title="Expires in ${daysLeft} day${daysLeft === 1 ? '' : 's'} — keep the file 30 days longer"
                    onclick="event.stopPropagation();GeoPackageExport.extend('${esc(d.id)}')"><i class="icon-calendar-plus"></i></button>`
                : '';
            // The layer list is the receipt. "Ready, 412 MB" does not tell you
            // whether the fires you wanted are in it; "fire_detections 3.2M"
            // does, and it is the only place that answer exists.
            const layers = (d.layers || []).slice(0, 4)
                .map(l => `${esc(l.name)} ${l.count.toLocaleString()}`).join(' · ');
            const more = (d.layers || []).length > 4 ? ` +${d.layers.length - 4} more` : '';
            return `
                <div class="gpkg-line">${fmtBytes(d.size_bytes)} · ${d.layers.length} layers · ${n.toLocaleString()} features</div>
                <div class="gpkg-dim">${layers}${more}</div>
                <div class="gpkg-actions">
                  <button class="gpkg-btn primary" onclick="event.stopPropagation();GeoPackageExport.download('${esc(d.id)}')">
                    <i class="icon-download"></i> Download .gpkg</button>
                  <button class="gpkg-btn" title="Copy a link that opens this download for anyone with access"
                    onclick="event.stopPropagation();GeoPackageExport.copyLink('${esc(d.id)}')"><i class="icon-link"></i></button>${delBtn}
                </div>
                ${until ? `<div class="gpkg-dim">Styled for QGIS · kept until ${until}${daysLeft !== null && daysLeft <= 14 ? ` <span style="color:#f59e0b">(${daysLeft} day${daysLeft === 1 ? '' : 's'} left)</span>` : ` (${daysLeft} day${daysLeft === 1 ? '' : 's'})`}${tag}${renew}</div>` : ''}`;
        }
        if (d.state === 'failed' || d.state === 'expired') {
            return `
                <div class="gpkg-line">${esc(d.state === 'expired' ? 'Expired' : (d.error || 'Export failed'))}</div>
                <div class="gpkg-actions">
                  <button class="gpkg-btn" onclick="event.stopPropagation();GeoPackageExport.retry('${esc(d.id)}')">Try again</button>${guest ? '' : `
                  <button class="gpkg-btn danger" title="Remove this card"
                    onclick="event.stopPropagation();GeoPackageExport.remove('${esc(d.id)}')"><i class="icon-trash-2"></i></button>`}
                </div>`;
        }
        // The step is a full phrase from the server ("writing fire detections",
        // "waiting for another export"), not a noun the client decorates: only
        // one export builds at a time, so "queued behind something else" and
        // "0% and working" are different answers and the user is owed the
        // difference.
        const step = d.step ? d.step.charAt(0).toUpperCase() + d.step.slice(1) : 'Starting…';
        return `
            <div class="gpkg-line">${esc(step)}</div>
            <div class="gpkg-bar"><div style="width:${pct}%"></div></div>
            <div class="gpkg-dim">${pct}% · you can close this, it keeps running</div>
            ${guest ? '' : `<div class="gpkg-actions">
              <button class="gpkg-btn danger" title="Stop this export and discard what has been built so far"
                onclick="event.stopPropagation();GeoPackageExport.remove('${esc(d.id)}')"><i class="icon-x"></i> Cancel</button>
            </div>`}`;
    }

    // Restart the same question. Uses the job's own parameters rather than the
    // current UI state: the user is retrying *this* export, and the time slider
    // may well have moved since. A view export retries as a view export — its
    // bbox/instant/chips ride on the job row (`view_json`), so a card reloaded
    // tomorrow still retries the right thing rather than quietly falling back
    // to an area export for the same window.
    async function retry(id) {
        const d = cache.get(id);
        if (!d) return;
        if (d.view) {
            const nd = await startView({
                bbox: d.view.bbox, from: d.from_date, to: d.to_date, at: d.view.at,
                layers: d.view.layers, aoi: d.view.aoi,
                area: d.is_aoi ? '' : d.area_id,
                label: d.area_name || 'this view', refresh: true,
            });
            if (nd && typeof loadNotifications === 'function') loadNotifications();
            return;
        }
        const qs = new URLSearchParams({ pwd: pwd(), refresh: '1' });
        if (d.from_date) qs.set('from', d.from_date);
        if (d.to_date) qs.set('to', d.to_date);
        if (d.raw_fire === false) qs.set('raw', '0');
        const base = d.is_aoi ? 'aois' : 'parks';
        const r = await fetch(`/api/${base}/${encodeURIComponent(d.area_id)}/export.gpkg?${qs}`, { method: 'POST' });
        if (r.ok) {
            const nd = await r.json();
            cache.set(nd.id, nd);
            track(nd.id);
            if (typeof loadNotifications === 'function') loadNotifications();
        }
    }

    // Delete the file now rather than waiting out the TTL — or, on a job
    // still building, CANCEL it: the server stops the build at the next row
    // and cleans up the half-written file, the job and the card itself
    // (DELETE answers 202 in that case, and the cleanup follows a beat later).
    //
    // A confirm() would ask the user to predict the result; this shows it and
    // offers no undo *because there is nothing to undo cheaply* — the answer to
    // "I still want it" is the same click that made it the first time, and the
    // export is a pure function of (area, window, options), so re-requesting
    // reproduces exactly the same file. The toast says so.
    //
    // The card is removed optimistically so the click reads as immediate; a
    // failure puts the list back the way the server sees it.
    // Push the file's expiry out 30 days. The server extends from the later
    // of now / current expiry, so clicking twice adds 60.
    async function extend(id) {
        let r;
        try {
            r = await fetch(`/api/geopackage/${encodeURIComponent(id)}/extend?pwd=${encodeURIComponent(pwd())}`,
                            { method: 'POST', headers: { 'Content-Type': 'application/json' },
                              body: JSON.stringify({ days: 30 }) });
        } catch (e) { r = null; }
        if (!r || !r.ok) {
            if (typeof showToast === 'function') showToast('Could not extend the export', 'error');
            return;
        }
        const d = await r.json();
        cache.set(id, d);
        paint(id, d);
        if (typeof showToast === 'function') {
            const until = d.expires_at
                ? new Date(d.expires_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
                : '';
            showToast(`Export kept until ${until}`, 'success');
        }
    }

    async function remove(id) {
        const d = cache.get(id) || {};
        const card = document.querySelector(`[data-gpkg-id="${cssEsc(id)}"]`);
        if (card) card.style.opacity = '0.35';
        stop(id);
        let r;
        try {
            r = await fetch(`/api/geopackage/${encodeURIComponent(id)}?pwd=${encodeURIComponent(pwd())}`,
                            { method: 'DELETE' });
        } catch (e) {
            r = null;
        }
        if (!r || !r.ok) {
            if (card) card.style.opacity = '';
            if (typeof showToast === 'function') showToast('Could not delete the export', 'error');
            track(id);
        // The card's HTML is returned as a string to the notification panel, so
        // the chips inside it can only be mounted after it lands in the DOM.
        setTimeout(mountTags, 0);
            return;
        }
        cache.delete(id);
        if (card) card.remove();
        if (typeof showToast === 'function') {
            if (r.status === 202) {
                showToast('Export cancelled — the partial file is being cleaned up', 'success');
            } else {
                showToast(`Deleted${d.size_bytes ? ' ' + fmtBytes(d.size_bytes) : ''} — `
                        + `ask for the export again any time to rebuild it`, 'success');
            }
        }
        // The server's cleanup deletes the notification row a moment after the
        // 202; refresh after that beat so the panel doesn't repaint the card.
        if (typeof loadNotifications === 'function') setTimeout(loadNotifications, r.status === 202 ? 900 : 0);
    }

    function cssEsc(v) {
        return window.CSS && CSS.escape ? CSS.escape(v) : String(v).replace(/[^\w-]/g, '');
    }

    // A share link, not a raw download URL: it opens the app on the same area
    // with the download menu pointing at this export, so the recipient sees
    // what it is before a few hundred MB start moving.
    function copyLink(id) {
        const d = cache.get(id) || {};
        const u = new URL(window.location.href);
        u.search = '';
        const p = u.searchParams;
        const cur = new URLSearchParams(window.location.search).get('pwd');
        if (cur) p.set('pwd', cur);
        p.set('gpkg', id);
        if (d.area_id) p.set(d.is_aoi ? 'aoi' : 'popup', d.area_id);
        const url = u.toString();
        navigator.clipboard.writeText(url).then(
            () => typeof showToast === 'function' && showToast('Download link copied', 'success'),
            () => prompt('Copy this link:', url));
    }

    // The notification card. `notif` is the server's row; the live body is
    // filled by paint() so a panel re-render cannot fight a poll in flight.
    function cardHTML(notif) {
        const id = notif.reference_id;
        const known = cache.get(id);
        const ready = notif.notification_type === 'geopackage_ready';
        const failed = notif.notification_type === 'geopackage_failed';
        const body = known ? bodyHTML(known) : `
            <div class="gpkg-line">${esc(notif.message || '')}</div>
            ${ready || failed ? '' : '<div class="gpkg-bar"><div style="width:0%"></div></div>'}
            <div class="gpkg-dim">checking…</div>`;
        // A ready card still gets one status fetch: the file may have expired
        // or been swept since the row was written, and offering a download that
        // 410s is worse than saying so.
        track(id);
        const colour = failed ? ['rgba(239,68,68,0.15)', '#ef4444', 'x']
                     : ready ? ['rgba(34,197,94,0.15)', '#22c55e', 'database']
                     : ['rgba(59,130,246,0.15)', '#3b82f6', 'database'];
        return `
            <div class="notification-item" data-type="gpkg" data-gpkg-id="${esc(id)}">
                <div class="notification-icon" style="background:${colour[0]};color:${colour[1]};">
                    <i class="icon-${colour[2]}"></i></div>
                <div class="notification-content">
                    <div class="notification-location">${esc(notif.title || 'GIS export')}</div>
                    <div class="gpkg-card" data-gpkg-job="${esc(id)}">${body}</div>
                    <div class="notification-time">${typeof formatNotificationTime === 'function'
                        ? formatNotificationTime(notif.created_at) : ''}</div>
                </div>
            </div>`;
    }

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            watched.forEach((w) => clearTimeout(w.timer));
        } else {
            watched.forEach((w, id) => poll(id));
        }
    });

    window.GeoPackageExport = { start, startView, peek, track, stop, poll, cardHTML, download, retry, remove, extend, copyLink, mountTags, cache };
})();
