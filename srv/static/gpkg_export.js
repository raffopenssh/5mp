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
        if (el && d) el.innerHTML = bodyHTML(d);
    }

    function fmtBytes(n) {
        if (!n) return '';
        if (n >= 1 << 30) return (n / (1 << 30)).toFixed(1) + ' GB';
        if (n >= 1 << 20) return Math.round(n / (1 << 20)) + ' MB';
        return Math.max(1, Math.round(n / 1024)) + ' KB';
    }

    function bodyHTML(d) {
        const pct = Math.max(0, Math.min(100, Math.round((d.progress || 0) * 100)));
        if (d.state === 'ready') {
            const n = (d.layers || []).reduce((a, l) => a + (l.count || 0), 0);
            const until = d.expires_at
                ? new Date(d.expires_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
                : null;
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
                    onclick="event.stopPropagation();GeoPackageExport.copyLink('${esc(d.id)}')"><i class="icon-link"></i></button>
                </div>
                ${until ? `<div class="gpkg-dim">Styled for QGIS · link valid until ${until}</div>` : ''}`;
        }
        if (d.state === 'failed' || d.state === 'expired') {
            return `
                <div class="gpkg-line">${esc(d.state === 'expired' ? 'Expired' : (d.error || 'Export failed'))}</div>
                <div class="gpkg-actions">
                  <button class="gpkg-btn" onclick="event.stopPropagation();GeoPackageExport.retry('${esc(d.id)}')">Try again</button>
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
            <div class="gpkg-dim">${pct}% · you can close this, it keeps running</div>`;
    }

    // Restart the same question. Uses the job's own parameters rather than the
    // current UI state: the user is retrying *this* export, and the time slider
    // may well have moved since.
    async function retry(id) {
        const d = cache.get(id);
        if (!d) return;
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

    window.GeoPackageExport = { start, track, stop, poll, cardHTML, download, retry, copyLink, cache };
})();
