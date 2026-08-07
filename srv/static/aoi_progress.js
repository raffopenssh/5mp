// ============================================================
// 5MP AOI progress — the notification card that lives for days.
//
// An AOI's data arrives over days, one cron slice at a time
// (docs/PLAN_AOI_OVERLAY.md §0 rule 3). Everything else in the
// notification panel describes something that already happened;
// this one describes something still happening, and it has to
// survive page reloads, browser restarts and a laptop being shut
// for a week.
//
// So the card is NOT client state. It is one row in `notifications`
// (type 'aoi_progress', park_id = the AOI id) written at create
// time, re-read on every page load, and re-rendered from
// GET /api/aois/{id}/progress. Polling is adaptive: fast while a
// dataset is running, slow while the queue waits for the noon cron,
// stopped entirely once everything is done — a card that polls
// every 5 s for three days is a bug, not a feature.
//
// window.AOIProgress.track(id) starts watching one; refresh() is
// called by the notification loader for each 'aoi_progress' row.
// ============================================================
(function () {
    'use strict';

    // Adaptive poll intervals (ms). The queue only moves when the runner has
    // a lease, so there is nothing to learn from polling a 'queued' AOI often.
    const POLL_RUNNING = 8000;
    const POLL_PARTIAL = 60000;
    const POLL_QUEUED = 300000;

    const watched = new Map();   // aoi_id -> { timer, last }

    function pwd() { return (typeof getPwd === 'function' ? getPwd() : ''); }

    function esc(s) {
        return (typeof escapeHtml === 'function') ? escapeHtml(s)
            : String(s == null ? '' : s).replace(/[&<>"']/g, c =>
                ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    const DS_LABEL = {
        clip: 'preview from nearby parks', fire_gap: 'fire detections',
        fire_v5: 'fire trajectories', gfw: 'forest-loss alerts',
        deforestation: 'deforestation events (2024+)',
        hansen: 'forest loss 2001–2023', ghsl: 'settlements',
        osm: 'roads & places', gsw: 'surface water', hydro: 'rivers & lakes',
        basin: 'watershed'
    };

    async function poll(id) {
        try {
            const r = await fetch(`/api/aois/${encodeURIComponent(id)}/progress?pwd=${encodeURIComponent(pwd())}`,
                                  { cache: 'no-store' });
            if (!r.ok) { stop(id); return null; }
            const d = await r.json();
            const w = watched.get(id);
            if (w) w.last = d;
            paint(id, d);
            schedule(id, d);
            return d;
        } catch (e) {
            schedule(id, null);
            return null;
        }
    }

    function schedule(id, d) {
        const w = watched.get(id);
        if (!w) return;
        clearTimeout(w.timer);
        if (d && d.state === 'ready') {
            // Done. Stop polling forever — the card stays, rendered from the
            // last payload, and a reload re-reads it from the API once.
            announceReady(id, d);
            watched.delete(id);
            return;
        }
        if (d && (d.state === 'cancelled' || d.state === 'superseded')) {
            // Nothing will move until the user says so, so polling is pure
            // waste; Resume re-arms the watcher through post(). 'superseded'
            // never moves at all -- a newer version owns the question.
            watched.delete(id);
            return;
        }
        const ms = !d ? POLL_PARTIAL
                 : d.state === 'running' ? POLL_RUNNING
                 : d.state === 'partial' ? POLL_PARTIAL : POLL_QUEUED;
        w.timer = setTimeout(() => poll(id), ms);
    }

    // Fired once, when the last dataset lands. Deliberately a toast plus a
    // repaint rather than a browser notification: the user may well not be
    // looking, and the card is the durable record.
    function announceReady(id, d) {
        const w = watched.get(id);
        if (!w || w.announced) return;
        w.announced = true;
        if (typeof showToast === 'function') {
            showToast(`“${d.name || id}” is ready — all data layers complete`,
                      'success', { key: 'aoi-ready-' + id });
        }
        if (typeof loadAOIs === 'function') loadAOIs();
        paint(id, d);
    }

    // Render into the card the notification list drew for this AOI. The list
    // owns the DOM; this only fills the live region inside it, so a re-render
    // of the panel cannot fight with a poll in flight.
    function paint(id, d) {
        const el = document.querySelector(`[data-aoi-progress="${CSS.escape(id)}"]`);
        if (!el || !d) return;
        el.innerHTML = bodyHTML(d);
    }

    function bodyHTML(d) {
        const pct = Math.max(0, Math.min(100, d.percent || 0));
        const ready = d.state === 'ready';
        const cancelled = d.state === 'cancelled';
        // An edit forks: v1's queue is disabled, exactly as a Stop would leave
        // it, but the *reason* is different and so is the remedy. Offering
        // Resume here would re-spend days of quota answering a question the
        // user has already replaced -- and the numbers v1 holds are still
        // correct for the window it asked about, so there is nothing to fix.
        const superseded = d.state === 'superseded';
        const colour = ready ? '#22c55e' : (cancelled || superseded) ? '#888'
                     : d.state === 'running' ? '#3b82f6' : '#f59e0b';
        let line;
        if (ready) {
            line = `All ${d.datasets_total} data layers complete`;
        } else if (superseded) {
            line = `Replaced by a newer version · ${d.datasets_done} layer`
                 + `${d.datasets_done === 1 ? '' : 's'} kept`;
        } else if (cancelled) {
            line = `Stopped · ${d.datasets_done} of ${d.datasets_done + (d.datasets_stopped || 0)} layers were fetched`;
        } else if (d.state === 'running') {
            const what = DS_LABEL[d.current] || d.current || 'data';
            line = `Fetching ${what}` + (d.detail ? ` — ${d.detail}` : '');
        } else if (d.state === 'partial') {
            line = `${d.datasets_done} of ${d.datasets_total} layers ready · next batch at 12:00 UTC`;
        } else {
            line = `Queued · first batch at 12:00 UTC`;
        }
        // datasets_total is the *planned* count, so a stopped queue reports 0 --
        // "0/0 layers" beside "0 of 11 were fetched" reads as a bug. Add the
        // stopped ones back for the denominator only.
        const total = d.datasets_total || (d.datasets_done + (d.datasets_stopped || 0));
        const blocked = d.datasets_blocked
            ? `<div class="aoi-prog-dim">${d.datasets_blocked} layer${d.datasets_blocked > 1 ? 's' : ''} not available yet</div>`
            : '';
        // Abort. The card is the only place an owner can see a multi-day ingest
        // in progress, so it is the only honest place to stop it — without it
        // the choices are "wait days for data you no longer want" or "delete",
        // which also throws away what already landed. Owner-only (the endpoint
        // 404s otherwise), and gone once there is nothing left to stop.
        const stoppable = d.is_owner && !ready && !cancelled && !superseded;
        const actions = stoppable
            ? `<div class="aoi-prog-actions"><button class="aoi-prog-abort"
                 onclick="event.stopPropagation();AOIProgress.cancel('${esc(d.aoi_id)}')"
                 title="Stop fetching data for this area. What has already landed is kept and stays queryable; nothing is deleted."
                 >Stop fetching</button></div>`
            : (cancelled && d.is_owner
               ? `<div class="aoi-prog-actions"><button class="aoi-prog-abort resume"
                 onclick="event.stopPropagation();AOIProgress.resume('${esc(d.aoi_id)}')"
                 title="Resume from where each layer stopped — nothing is re-downloaded"
                 >Resume</button></div>`
               : '');
        return `
            <div class="aoi-prog-line">${esc(line)}</div>
            <div class="aoi-prog-bar"><div style="width:${pct}%;background:${colour}"></div></div>
            <div class="aoi-prog-dim">${d.datasets_done}/${total} layers · ${Math.round(pct)}%</div>
            ${blocked}${actions}`;
    }

    // Stop / resume. Both go through the server (which re-checks ownership)
    // and then re-poll rather than optimistically repainting: the runner may
    // hold a lease mid-unit, so the truthful state is whatever the DB says a
    // moment later, not what we just asked for.
    async function cancel(id) {
        if (!confirm('Stop fetching data for this area?\n\nWhat has already been fetched is kept. You can resume later from exactly where each layer stopped — nothing is re-downloaded.')) return;
        await post(id, 'cancel', 'Stopped fetching');
    }

    async function resume(id) {
        // refresh?resume=1, not plain refresh: plain refresh recomputes the
        // derived layers of an *enabled* queue, which after a cancel is the
        // empty set. See HandleAPIAOIRefresh.
        await post(id, 'refresh?resume=1', 'Resuming — the next batch runs at 12:00 UTC');
    }

    async function post(id, action, okMsg) {
        const sep = action.includes('?') ? '&' : '?';
        try {
            const r = await fetch(`/api/aois/${encodeURIComponent(id)}/${action}${sep}pwd=${encodeURIComponent(pwd())}`,
                                  { method: 'POST' });
            if (!r.ok) throw new Error('HTTP ' + r.status);
            if (typeof showToast === 'function') showToast(okMsg, 'success');
            // Re-arm the watcher: a cancelled AOI was probably dropped from
            // `watched` by a 'ready'-like schedule, and a resumed one needs to
            // start polling again.
            watched.delete(id);
            track(id);
        } catch (e) {
            if (typeof showToast === 'function') showToast('That did not work — try again', 'error');
        }
    }

    // The notification list calls this to render one 'aoi_progress' row. It
    // returns immediately with whatever is cached and kicks a poll, so the
    // panel never blocks on the network.
    function cardHTML(notif) {
        const data = notif.reference_data || {};
        const id = data.aoi_id || notif.reference_id || notif.park_id;
        const name = data.aoi_name || notif.title || id;
        const w = watched.get(id);
        const cached = w && w.last;
        const initial = cached ? bodyHTML(cached) : `
            <div class="aoi-prog-line">${esc(data.human || notif.message || 'Queued')}</div>
            <div class="aoi-prog-bar"><div style="width:0%;background:#f59e0b"></div></div>
            <div class="aoi-prog-dim">checking…</div>`;
        track(id);
        return `
            <div class="notification-item clickable" data-type="aoi" data-action="open_aoi" data-aoi-id="${esc(id)}">
                <div class="notification-icon" style="background:rgba(96,165,250,0.15);color:#60a5fa;">
                    <i class="icon-scan-search"></i></div>
                <div class="notification-content">
                    <div class="notification-location">${esc(name)}</div>
                    <div data-aoi-progress="${esc(id)}">${initial}</div>
                    <div class="notification-time">${typeof formatNotificationTime === 'function'
                        ? formatNotificationTime(notif.created_at) : ''}</div>
                </div>
            </div>`;
    }

    function track(id, estimate) {
        if (!id || watched.has(id)) return;
        watched.set(id, { timer: null, last: null, announced: false });
        // First poll immediately: the panel may have been opened days later.
        poll(id);
    }

    function stop(id) {
        const w = watched.get(id);
        if (w) clearTimeout(w.timer);
        watched.delete(id);
    }

    // Polling while the tab is hidden is pure waste — the runner takes a slice
    // a day, so nothing is missed by waiting for focus.
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            watched.forEach(w => clearTimeout(w.timer));
        } else {
            watched.forEach((w, id) => poll(id));
        }
    });

    window.AOIProgress = { track, stop, cardHTML, poll, cancel, resume };
})();
