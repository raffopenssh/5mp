// admin.js -- the Activity and Learner tabs of the admin panel, and the fetch
// log on an automated-fetch card.
//
// WHY THESE TWO TABS WERE REWRITTEN (after e83b392, the event learner).
//
// The old "Upload logs" table assumed a row was a ranger's afternoon: a
// filename, one park, seven km columns. A fetched file is a WEEK of a fleet --
// 60,000 points, 1,300 segments, four parks -- arriving twenty at a time under
// the name "autofetch-20261105.gpx", stamped with the minute a backfill ran
// rather than the week it covers. So a row here leads with the period its
// tracks cover and where it came from, draws the km as one bar instead of
// seven columns, and says what the learner made of it.
//
// The old "Pending approvals" and "Learned features" tabs showed a
// `confidence %` and offered "Approve all > 75%". Neither survived the model
// change: a score is now the calibrated probability that a feature is used
// again within the horizon, each family has its own approval line (airfield
// 0.77, place 0.88, road 0.94), and a feature below its line is undecided ON
// PURPOSE. One button at 75 would have put several hundred of those on the
// map. What a reviewer needs instead is the evidence the score was computed
// from, the line it is measured against, and who decided -- so that is a row.
//
// Rules this file keeps:
//   * A count names what it counts and whether it is the whole list
//     ("Showing 50 of 146") -- AGENTS.md invariant 8.
//   * A score that was never computed prints "not scored", never 0 %.
//   * Nothing destructive hides behind confirm(): a decision is applied and
//     offered back as Undo; a delete arms on the first press.
//   * Everything is reachable by keyboard and by a thumb (44 px targets under
//     900 px, where rows become cards and the bulk bar moves to the bottom).
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const esc = (v) => (typeof escapeHtml === 'function' ? escapeHtml(String(v == null ? '' : v)) : String(v == null ? '' : v));
    const toast = (m, t, o) => { if (typeof showToast === 'function') return showToast(m, t || 'info', o || {}); };
    const int = (n) => Math.round(n || 0).toLocaleString('en-GB');
    const plural = (n, one, many) => `${int(n)} ${n === 1 ? one : (many || one + 's')}`;
    const km = (v) => v >= 100 ? int(v) + ' km' : v >= 1 ? v.toFixed(1) + ' km' : v > 0 ? Math.round(v * 1000) + ' m' : '0 km';
    const pct = (p) => p == null ? '' : (p >= 0.995 ? '>99' : p < 0.005 ? '<1' : Math.round(p * 100)) + '%';
    const parkName = (id) => String(id || '').replace(/^[A-Z]{3}_/, '').replace(/_/g, ' ');

    const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    function parseDay(v) { if (!v) return null; const d = new Date(v.length === 10 ? v + 'T00:00:00Z' : v); return isNaN(d) ? null : d; }
    // Dates are printed in UTC: a week of tracks is "5-11 Nov" for every
    // reviewer, wherever they read it.
    function day(v, withYear) {
        const d = parseDay(v); if (!d) return '';
        return `${d.getUTCDate()} ${MON[d.getUTCMonth()]}` + (withYear === false ? '' : ` ${d.getUTCFullYear()}`);
    }
    function range(a, b) {
        const x = parseDay(a), y = parseDay(b);
        if (!x || !y) return day(a) || day(b);
        if (x.getUTCFullYear() !== y.getUTCFullYear()) return `${day(a)} \u2013 ${day(b)}`;
        if (x.getUTCMonth() !== y.getUTCMonth()) return `${day(a, false)} \u2013 ${day(b)}`;
        if (x.getUTCDate() === y.getUTCDate()) return day(b);
        return `${x.getUTCDate()}\u2013${day(b)}`;
    }
    function ago(v) {
        const d = parseDay(v); if (!d) return '';
        const m = Math.round((Date.now() - d.getTime()) / 60000);
        if (m < 1) return 'just now';
        if (m < 60) return `${m} min ago`;
        const h = Math.round(m / 60);
        if (h < 36) return `${h} h ago`;
        const dd = Math.round(h / 24);
        return dd < 45 ? `${dd} days ago` : day(v);
    }
    const bytes = (n) => n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n >= 1024 ? Math.round(n / 1024) + ' kB' : n + ' B';

    async function getJSON(url) {
        const r = await fetch(url, { credentials: 'same-origin' });
        if (!r.ok) throw new Error((await r.text()).slice(0, 200) || `HTTP ${r.status}`);
        return r.json();
    }
    async function postJSON(url, body) {
        const r = await fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const text = await r.text();
        let d = {}; try { d = JSON.parse(text); } catch (_) { }
        if (!r.ok || d.success === false) throw new Error(d.error || text.slice(0, 200) || `HTTP ${r.status}`);
        return d;
    }
    const skeleton = (n) => Array.from({ length: n }, () => '<div class="adm-skel"></div>').join('');
    const empty = (icon, title, body) => `<div class="adm-empty"><i class="icon-${icon}" aria-hidden="true"></i><b>${title}</b>${body ? `<span>${body}</span>` : ''}</div>`;
    const DEMO = 'Patrol tracks and what is learned from them belong to an account login. They are not part of the public demo or of a shared link.';

    function segmented(id, onPick) {
        const host = $(id); if (!host || host._wired) return; host._wired = true;
        host.addEventListener('click', (e) => {
            const b = e.target.closest('button[data-v]'); if (!b) return;
            setSeg(id, b.dataset.v); onPick(b.dataset.v);
        });
    }
    function setSeg(id, v) {
        const host = $(id); if (!host) return;
        host.querySelectorAll('button[data-v]').forEach(x => { const on = x.dataset.v === v; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on); });
    }

    // arm(): a destructive button asks once, in place. The first press turns
    // it into its own confirmation for a few seconds; the second does it.
    function arm(btn, label, fn) {
        if (btn._armed) { clearTimeout(btn._armed); btn._armed = null; btn.classList.remove('armed'); btn.innerHTML = btn._label; fn(); return; }
        btn._label = btn.innerHTML;
        btn.classList.add('armed');
        btn.textContent = label;
        btn._armed = setTimeout(() => { btn._armed = null; btn.classList.remove('armed'); btn.innerHTML = btn._label; }, 5000);
    }

    // ── Movement modes: one vocabulary for the totals bar, a row and a detail ──
    const MODES = [
        { k: 'foot', label: 'On foot', c: '#22c55e' },
        { k: 'vehicle', label: 'Vehicle', c: '#3b82f6' },
        { k: 'boat', label: 'Boat', c: '#22d3ee' },
        { k: 'fixed_wing', label: 'Plane', c: '#f8fafc' },
        { k: 'rotor_wing', label: 'Helicopter', c: '#f0abfc' },
        // Aircraft whose airframe was not measured is its own colour: folding
        // it into either type would be a guess drawn as a measurement.
        { k: 'aircraft_unknown', label: 'Aircraft, type not measured', c: '#94a3b8' },
    ];
    const kmTotal = (m) => MODES.reduce((s, x) => s + (m[x.k] || 0), 0);
    function modeBar(m, cls) {
        const tot = kmTotal(m);
        if (tot <= 0) return '';
        const label = MODES.filter(x => m[x.k] > 0).map(x => `${x.label} ${km(m[x.k])}`).join(', ');
        return `<div class="adm-modebar ${cls || ''}" role="img" aria-label="${esc(label)}" title="${esc(label)}">` +
            MODES.filter(x => m[x.k] > 0).map(x => `<i style="flex:${(m[x.k] / tot).toFixed(4)};background:${x.c}"></i>`).join('') + '</div>';
    }
    const modeLegend = (m) => '<ul class="adm-legend">' + MODES.filter(x => m[x.k] > 0)
        .map(x => `<li><i style="background:${x.c}"></i>${x.label} <b>${km(m[x.k])}</b></li>`).join('') + '</ul>';

    // ════════════════════════════ Selection + bulk bar ════════════════════════
    const sel = { kind: '', items: new Map() };
    function bulkBar() {
        let bar = $('admin-bulk-bar');
        if (!sel.items.size) { if (bar) bar.remove(); return; }
        if (!bar) {
            bar = document.createElement('div'); bar.id = 'admin-bulk-bar'; bar.className = 'adm-bulk'; bar.setAttribute('role', 'region'); bar.setAttribute('aria-label', 'Selection');
            ($('admin-panel') || document.body).appendChild(bar);
        }
        const n = sel.items.size;
        bar.innerHTML = `<span class="adm-bulk-n" aria-live="polite"><b>${n}</b> selected</span>` + (sel.kind === 'learner'
            ? `<button class="sh-btn adm-ok" data-bulk="approve"><i class="icon-check" aria-hidden="true"></i> Approve</button>
               <button class="sh-btn sh-btn-off" data-bulk="reject"><i class="icon-x" aria-hidden="true"></i> Reject</button>`
            : `<button class="sh-btn sh-btn-off" data-bulk="delete"><i class="icon-trash-2" aria-hidden="true"></i> Delete</button>`)
            + `<button class="sh-btn adm-bulk-clear" data-bulk="clear">Clear</button>`;
        bar.onclick = (e) => {
            const b = e.target.closest('[data-bulk]'); if (!b) return;
            const what = b.dataset.bulk;
            if (what === 'clear') return clearSelection();
            if (what === 'delete') return arm(b, `Delete ${plural(n, 'file')}?`, () => activity.removeMany([...sel.items.keys()]));
            learner.decideMany(what, [...sel.items.values()]);
        };
    }
    function clearSelection() {
        sel.items.clear();
        document.querySelectorAll('#admin-panel .adm-row.selected').forEach(r => r.classList.remove('selected'));
        document.querySelectorAll('#admin-panel .adm-check:checked').forEach(c => { c.checked = false; });
        bulkBar();
    }
    function toggleSel(kind, key, val, on, rowEl) {
        if (sel.kind !== kind) { sel.items.clear(); sel.kind = kind; }
        if (on) sel.items.set(key, val); else sel.items.delete(key);
        if (rowEl) rowEl.classList.toggle('selected', on);
        bulkBar();
    }

    // ══════════════════════════════════ Activity ══════════════════════════════
    const activity = {
        source: '', sourceId: 0, sourceName: '', rows: [], total: 0, seq: 0,

        open(opts) {
            segmented('act-source', (v) => { this.source = v; this.sourceId = 0; this.load(); });
            if (opts && opts.source_id) { this.sourceId = opts.source_id; this.sourceName = opts.source_name || ''; this.source = 'autofetch'; setSeg('act-source', 'autofetch'); }
            this.load();
        },
        url(limit, offset) {
            const q = new URLSearchParams({ limit, offset });
            if (this.source) q.set('source', this.source);
            if (this.sourceId) q.set('source_id', this.sourceId);
            return '/api/admin/activity?' + q;
        },
        async load(keep) {
            const list = $('act-list'); if (!list) return;
            const seq = ++this.seq;
            const want = keep ? Math.max(30, this.rows.length) : 30;
            if (!keep) { list.innerHTML = skeleton(4); $('act-more').innerHTML = ''; }
            try {
                const d = await getJSON(this.url(want, 0));
                if (seq !== this.seq) return;
                if (d.unavailable) { $('act-totals').innerHTML = ''; list.innerHTML = empty('lock', 'Not available on this login', DEMO); return; }
                this.rows = d.rows || []; this.total = d.total || 0;
                this.renderTotals(d.totals || {});
                this.render();
            } catch (e) {
                if (seq !== this.seq) return;
                list.innerHTML = empty('triangle-alert', 'Could not load activity', esc(e.message));
            }
        },
        async more() {
            const btn = $('act-more').querySelector('button'); if (btn) { btn.disabled = true; btn.textContent = 'Loading\u2026'; }
            try {
                const d = await getJSON(this.url(30, this.rows.length));
                this.rows = this.rows.concat(d.rows || []); this.total = d.total || this.total;
                this.render();
            } catch (e) { toast(e.message, 'error'); if (btn) { btn.disabled = false; btn.textContent = 'Load more'; } }
        },
        renderTotals(t) {
            const host = $('act-totals'); if (!host) return;
            if (!t.files) { host.innerHTML = ''; return; }
            const m = t.km || {};
            const scope = this.sourceId ? `<button class="adm-chip-x" onclick="AdminUI.activity.clearSource()" title="Show every source">
                <i class="icon-cloud-download" aria-hidden="true"></i> ${esc(this.sourceName || 'One source')} <i class="icon-x" aria-hidden="true"></i></button>` : '';
            host.innerHTML = `<section class="adm-card adm-totals" aria-label="Totals for this filter">
                ${scope}
                <dl class="adm-figs">
                    <div><dt>Files</dt><dd>${int(t.files)}</dd></div>
                    <div><dt>GPS points</dt><dd>${int(t.points)}</dd></div>
                    <div><dt>Distance</dt><dd>${int(kmTotal(m))} <small>km</small></dd></div>
                    <div class="wide"><dt>Tracks cover</dt><dd class="txt">${esc(range(t.from, t.to)) || '\u2014'}</dd></div>
                </dl>
                ${modeBar(m, 'big')}${modeLegend(m)}
            </section>`;
        },
        clearSource() { this.sourceId = 0; this.source = ''; setSeg('act-source', ''); this.load(); },
        render() {
            const list = $('act-list');
            if (!this.rows.length) {
                list.innerHTML = this.source === 'upload'
                    ? empty('upload', 'No hand-uploaded tracks', 'Drop a GPX file on the map to add one.')
                    : this.source === 'autofetch'
                        ? empty('cloud-download', 'Nothing fetched yet', 'Connect a tracking server under <a href="#" onclick="switchAdminTab(\'autofetch\');return false">Automated fetch</a>.')
                        : empty('route', 'No patrol tracks yet', 'Drop a GPX file on the map, or connect a tracking server under <a href="#" onclick="switchAdminTab(\'autofetch\');return false">Automated fetch</a>.');
                $('act-more').innerHTML = ''; return;
            }
            list.innerHTML = this.rows.map(r => this.rowHTML(r)).join('');
            // Truncation is said, not implied (invariant 8).
            $('act-more').innerHTML = this.rows.length < this.total
                ? `<span>Showing ${int(this.rows.length)} of ${int(this.total)} files</span><button class="sh-btn" onclick="AdminUI.activity.more()">Load more</button>`
                : `<span>All ${plural(this.total, 'file')} shown</span>`;
        },
        learnerPill(r) {
            if (r.learner === 'running') return '<span class="ts-badge wait">Learner running</span>';
            if (r.learner === 'queued') return '<span class="ts-badge wait">Waiting for the learner</span>';
            if (r.learner === 'failed') return '<span class="ts-badge err">Learner failed</span>';
            if (r.learner !== 'learned') return '';
            const s = (r.learned || []).reduce((a, l) => { a.r += l.roads; a.p += l.places; a.a += l.airfields; return a; }, { r: 0, p: 0, a: 0 });
            const parts = [s.a && plural(s.a, 'airfield'), s.p && plural(s.p, 'place'), s.r && plural(s.r, 'track')].filter(Boolean);
            return parts.length
                ? `<span class="ts-badge ok" title="Features that reached the map when this file was learned">+ ${parts.join(', ')} on the map</span>`
                : '<span class="ts-badge off" title="The learner read this file; no feature crossed its approval line because of it">Learned \u00b7 nothing new on the map</span>';
        },
        rowHTML(r) {
            const fetched = r.source === 'autofetch';
            const covers = range(r.from, r.to);
            const title = fetched ? (covers || r.filename) : r.filename;
            const parks = (r.learned && r.learned.length) ? r.learned.map(l => parkName(l.park_id)) : (r.park ? [r.park] : []);
            const sub = [
                fetched ? `<span><i class="icon-cloud-download" aria-hidden="true"></i> ${esc(r.source_name || 'Fetched')}</span>` : `<span><i class="icon-upload" aria-hidden="true"></i> Uploaded</span>`,
                !fetched && covers ? `<span>covers ${esc(covers)}</span>` : '',
                `<span title="${esc(r.received)}">${fetched ? 'fetched' : 'received'} ${esc(ago(r.received))}</span>`,
            ].filter(Boolean).join('');
            const st = r.status === 'rejected' ? `<span class="ts-badge err" title="${esc(r.rejection_reason || '')}">Rejected</span>`
                : r.status === 'completed' ? '' : `<span class="ts-badge wait">${esc(r.status || 'processing')}</span>`;
            const total = kmTotal(r.km);
            return `<article class="adm-row act-row" data-id="${r.id}">
                ${r.can_edit ? `<label class="adm-checkwrap"><input type="checkbox" class="adm-check" aria-label="Select ${esc(title)}" ${sel.kind === 'activity' && sel.items.has(r.id) ? 'checked' : ''}
                    onchange="AdminUI.activity.pick(${r.id}, this)"></label>` : '<span class="adm-checkwrap"></span>'}
                <div class="adm-row-main">
                    <button class="adm-row-title" aria-expanded="false" aria-controls="act-d-${r.id}" onclick="AdminUI.activity.toggle(${r.id}, this)">
                        <span>${esc(title)}</span><i class="icon-chevron-down" aria-hidden="true"></i>
                    </button>
                    <div class="adm-row-sub">${sub}</div>
                    ${r.status === 'rejected' && r.rejection_reason ? `<div class="adm-row-err">${esc(r.rejection_reason)}</div>` : ''}
                    <div class="adm-row-pills">${st}${this.learnerPill(r)}${parks.map(p => `<span class="af-park">${esc(p)}</span>`).join('')}</div>
                </div>
                <div class="adm-row-side">
                    <div class="adm-row-nums"><b>${km(total)}</b><span>${plural(r.points, 'point')} \u00b7 ${plural(r.segments, 'segment')}</span></div>
                    ${modeBar(r.km)}
                </div>
                <div class="adm-row-detail" id="act-d-${r.id}" hidden></div>
            </article>`;
        },
        pick(id, box) { toggleSel('activity', id, id, box.checked, box.closest('.adm-row')); },
        async toggle(id, btn) {
            const host = $('act-d-' + id); if (!host) return;
            const open = host.hidden;
            host.hidden = !open; btn.setAttribute('aria-expanded', open); btn.closest('.adm-row').classList.toggle('open', open);
            if (!open || host._loaded) return;
            host.innerHTML = skeleton(1);
            try {
                const d = await getJSON('/api/admin/activity/detail?id=' + id);
                host._loaded = true;
                host.innerHTML = this.detailHTML(this.rows.find(x => x.id === id) || {}, d);
            } catch (e) { host.innerHTML = `<div class="adm-row-err">${esc(e.message)}</div>`; }
        },
        detailHTML(r, d) {
            const mv = (g) => g.movement === 'aircraft' ? (g.subtype === 'fixed_wing' ? 'Plane' : g.subtype === 'rotor_wing' ? 'Helicopter' : 'Aircraft, type not measured')
                : g.movement ? g.movement[0].toUpperCase() + g.movement.slice(1) : 'Unclassified';
            const cls = (g) => g.in_effort ? '' : `<span class="ts-badge off" title="Not counted as patrol effort">${esc(g.classification || 'excluded')}</span>`;
            const groups = (d.groups || []).map(g => `<tr><th scope="row">${esc(mv(g))} ${cls(g)}</th><td>${int(g.segments)}</td><td>${km(g.km)}</td><td>${g.hours >= 1 ? int(g.hours) + ' h' : Math.round(g.hours * 60) + ' min'}</td></tr>`).join('');
            const legs = (d.longest || []).map(l => `<li><b>${km(l.km)}</b> ${esc(mv(l))} \u00b7 ${esc(l.duration || '')}${l.kmh ? ` \u00b7 ${Math.round(l.kmh)} km/h` : ''}${l.reason ? `<span>${esc(l.reason)}</span>` : ''}</li>`).join('');
            const learned = (r.learned || []).map(l => `<li><b>${esc(parkName(l.park_id))}</b> ${[l.airfields && plural(l.airfields, 'airfield'), l.places && plural(l.places, 'place'),
                l.roads && `${plural(l.roads, 'track')} (${km(l.roads_km)})`].filter(Boolean).join(', ') || 'nothing new on the map'}</li>`).join('');
            const notes = [].concat((d.errors || []).map(x => `<li class="err">${esc(x)}</li>`), (d.warnings || []).map(x => `<li>${esc(x)}</li>`)).join('');
            const facts = [
                d.trackers ? plural(d.trackers, 'tracker') : '',
                d.patrol_km ? `${km(d.patrol_km)} counted as patrol effort` : '',
                d.boundary_km ? `${km(d.boundary_km)} on the boundary` : '',
                d.excluded_km ? `${km(d.excluded_km)} excluded` : '',
                `file <code>${esc(r.filename || '')}</code>`,
            ].filter(Boolean).join('<i class="af-dot"></i>');
            return `<div class="adm-detail-grid">
                <div><h5>How it moved</h5>
                    ${groups ? `<table class="adm-mini"><thead><tr><th scope="col">Movement</th><th scope="col">Segments</th><th scope="col">Distance</th><th scope="col">Time</th></tr></thead><tbody>${groups}</tbody></table>` : '<p class="adm-muted">No classified segments on record.</p>'}
                </div>
                <div><h5>Longest legs${d.longest_of > (d.longest || []).length ? ` <small>${(d.longest || []).length} of ${int(d.longest_of)} segments</small>` : ''}</h5>
                    ${legs ? `<ol class="adm-legs">${legs}</ol>` : '<p class="adm-muted">\u2014</p>'}
                </div>
                ${learned ? `<div><h5>What the learner added</h5><ul class="adm-plain">${learned}</ul></div>` : ''}
                ${notes ? `<div><h5>Validation</h5><ul class="adm-plain">${notes}</ul></div>` : ''}
            </div>
            <div class="adm-detail-foot"><div class="af-stats">${facts}</div>
                ${r.can_edit ? `<button class="sh-btn sh-btn-off" onclick="AdminUI.arm(this, 'Delete this file and its effort?', () => AdminUI.activity.removeMany([${r.id}]))"><i class="icon-trash-2" aria-hidden="true"></i> Delete</button>` : ''}
            </div>`;
        },
        async removeMany(ids) {
            try {
                if (ids.length === 1) await postJSON('/api/admin/delete-upload', { id: ids[0] });
                else await postJSON('/api/admin/bulk-delete-uploads', { ids });
                toast(`${plural(ids.length, 'file')} deleted`, 'success');
                clearSelection(); this.load(true);
            } catch (e) { toast('Delete failed: ' + e.message, 'error'); }
        },
    };

    // ══════════════════════════════════ Learner ═══════════════════════════════
    // Wording and colours mirror the map layer (learned.js) so a row here and a
    // tip there describe one feature in one vocabulary.
    const KIND = {
        airstrip: ['Airstrip', 'plane', '#f8fafc'], landing_zone: ['Helicopter landing zone', 'circle-dot', '#f0abfc'], airfield: ['Landing site', 'plane-landing', '#cbd5e1'],
        base: ['Base', 'house', '#5eead4'], outpost: ['Outpost', 'tent', '#5eead4'], camp: ['Camp', 'tent', '#5eead4'], stop: ['Regular stop', 'map-pin', '#5eead4'],
        road: ['Road / track', 'route', '#fde68a'],
    };
    const FAMILY = {
        airfield: { label: 'Airfields', one: 'airfield', icon: 'plane', c: '#f8fafc' },
        place: { label: 'Places', one: 'place', icon: 'tent', c: '#5eead4' },
        road: { label: 'Roads & tracks', one: 'track', icon: 'route', c: '#fde68a' },
    };
    const STATE_WORD = { map: 'on the map', pending: 'undecided', rejected: 'rejected', all: 'in all' };

    const learner = {
        park: '', family: '', state: 'map', sort: 'score', rows: [], total: 0, data: null, seq: 0, wired: false,

        open(opts) {
            if (!this.wired) {
                this.wired = true;
                segmented('lrn-state', (v) => { this.state = v; this.load(); });
                $('lrn-park').addEventListener('change', (e) => { this.park = e.target.value; this.load(); });
                $('lrn-sort').addEventListener('change', (e) => { this.sort = e.target.value; this.load(); });
            }
            if (opts && opts.state) { this.state = opts.state; setSeg('lrn-state', this.state); }
            this.load();
        },
        url(limit, offset) {
            const q = new URLSearchParams({ state: this.state, sort: this.sort, limit, offset });
            if (this.park) q.set('park_id', this.park);
            if (this.family) q.set('family', this.family);
            return '/api/admin/learner?' + q;
        },
        async load(keep) {
            const list = $('lrn-list'); if (!list) return;
            const seq = ++this.seq;
            const want = keep ? Math.min(200, Math.max(50, this.rows.length)) : 50;
            if (!keep) { clearSelection(); list.innerHTML = skeleton(5); $('lrn-more').innerHTML = ''; }
            try {
                const d = await getJSON(this.url(want, 0));
                if (seq !== this.seq) return;
                if (d.unavailable) {
                    ['lrn-summary', 'lrn-queue', 'lrn-count', 'lrn-more'].forEach(i => { $(i).innerHTML = ''; });
                    $('lrn-toolbar').hidden = true;
                    list.innerHTML = empty('lock', 'Not available on this login', DEMO); return;
                }
                $('lrn-toolbar').hidden = false;
                this.data = d; this.rows = d.features || []; this.total = d.total || 0;
                this.renderChrome(); this.render();
            } catch (e) {
                if (seq !== this.seq) return;
                list.innerHTML = empty('triangle-alert', 'Could not load the learner', esc(e.message));
            }
        },
        async more() {
            const btn = $('lrn-more').querySelector('button'); if (btn) { btn.disabled = true; btn.textContent = 'Loading\u2026'; }
            try {
                const d = await getJSON(this.url(50, this.rows.length));
                this.rows = this.rows.concat(d.features || []); this.total = d.total; this.render();
            } catch (e) { toast(e.message, 'error'); if (btn) { btn.disabled = false; btn.textContent = 'Load more'; } }
        },
        renderChrome() {
            const d = this.data, parks = d.parks || [];
            // Park filter: only parks the learner has something for.
            const selEl = $('lrn-park'), cur = this.park;
            selEl.innerHTML = `<option value="">All parks (${int(parks.reduce((s, p) => s + p.total, 0))})</option>` +
                parks.map(p => `<option value="${esc(p.park_id)}"${p.park_id === cur ? ' selected' : ''}>${esc(parkName(p.park_id))} (${int(p.total)})</option>`).join('');
            $('lrn-sort').value = this.sort;

            const scope = cur ? parks.filter(p => p.park_id === cur) : parks;
            const sum = (fam, k) => scope.reduce((s, p) => s + (((p.counts || {})[fam] || {})[k] || 0), 0);
            const model = d.model || {};
            $('lrn-summary').innerHTML = Object.keys(FAMILY).map(f => {
                const F = FAMILY[f], auto = sum(f, 'auto'), rev = sum(f, 'reviewer'), pend = sum(f, 'pending'), rej = sum(f, 'rejected');
                const line = model[f] && model[f].approve;
                const on = this.family === f;
                return `<button class="adm-card lrn-fam${on ? ' on' : ''}" aria-pressed="${on}" onclick="AdminUI.learner.pickFamily('${f}')" title="${on ? 'Show every family' : 'Show only ' + F.label.toLowerCase()}">
                    <span class="lrn-fam-h"><i class="icon-${F.icon}" style="color:${F.c}" aria-hidden="true"></i>${F.label}</span>
                    <span class="lrn-fam-n">${int(auto + rev)}<small>on the map</small></span>
                    <span class="lrn-fam-split">${int(auto)} by the learner${rev ? ` \u00b7 ${int(rev)} by a reviewer` : ''}</span>
                    <span class="lrn-fam-rest"><b>${int(pend)}</b> undecided<i class="af-dot"></i><b>${int(rej)}</b> rejected</span>
                    <span class="lrn-fam-line">${line ? `goes on the map at \u2265 ${pct(line)}` : 'approval line not measured'}</span>
                </button>`;
            }).join('');

            if (d.horizon_days) $('lrn-intro').innerHTML = `Airstrips, camps and tracks inferred from how patrols move. Each carries a score &mdash; the probability it is used again within ${d.horizon_days} days &mdash; and goes on the map by itself once the score clears its family&rsquo;s line. Your decision always overrides the learner&rsquo;s.`;

            // Queue: say when it last ran, and say so when a job never finished.
            const q = d.queue || {}, bits = [];
            if (q.last_completed_at) bits.push(`Last learned ${esc(ago(q.last_completed_at))}`);
            if (q.completed) bits.push(`${plural(q.completed, 'file')} read`);
            const ev = scope.reduce((s, p) => s + (p.events || 0), 0);
            if (ev) bits.push(`${int(ev)} events on record${scope.length ? ' ' + esc(range(scope.map(p => p.from).filter(Boolean).sort()[0], scope.map(p => p.to).filter(Boolean).sort().pop())) : ''}`);
            const live = (q.pending || 0) + Math.max(0, (q.processing || 0) - (q.stuck || 0));
            const warn = [];
            if (live) warn.push(`<span class="ts-badge wait">${plural(live, 'file')} waiting to be learned</span>`);
            if (q.stuck) warn.push(`<span class="ts-badge wait" title="Marked as processing for over an hour; nothing is working on it">${plural(q.stuck, 'job')} started and never finished</span>`);
            if (q.failed) warn.push(`<span class="ts-badge err">${plural(q.failed, 'job')} failed</span>`);
            $('lrn-queue').innerHTML = (bits.length || warn.length) ? `<div class="lrn-queue"><span class="af-stats">${bits.map(b => `<span>${b}</span>`).join('<i class="af-dot"></i>')}</span>${warn.join('')}</div>` : '';
            const badge = $('learner-queue-badge');
            if (badge) { const n = live + (q.failed || 0); badge.hidden = !n; badge.textContent = n ? int(n) : ''; badge.title = n ? 'Files waiting for, or failed in, the learner' : ''; }
        },
        pickFamily(f) { this.family = this.family === f ? '' : f; this.load(); },
        render() {
            const list = $('lrn-list'), n = this.rows.length;
            const what = this.family ? FAMILY[this.family].label.toLowerCase() : 'features';
            const where = this.park ? ` in ${esc(parkName(this.park))}` : '';
            let kml = '';
            if (this.park) kml = `<a class="adm-link" href="/api/admin/learned-features-kml?park_id=${encodeURIComponent(this.park)}" download title="On the map and undecided, for this park"><i class="icon-download" aria-hidden="true"></i> KML</a>`;
            else if (this.state === 'pending') kml = `<a class="adm-link" href="/api/admin/learned-features-kml?scope=pending" download title="Every undecided feature"><i class="icon-download" aria-hidden="true"></i> KML</a>`;
            $('lrn-count').innerHTML = n ? `<span>${n < this.total ? `Showing ${int(n)} of ${int(this.total)}` : int(this.total)} ${what} ${STATE_WORD[this.state]}${where}</span>
                <label class="adm-selall"><input type="checkbox" onchange="AdminUI.learner.pickAll(this.checked)"> Select shown</label>${kml}` : '';
            if (!n) {
                list.innerHTML = this.state === 'pending' ? empty('circle-check', `Nothing undecided${where}`, 'Every feature the learner has seen is either on the map or rejected.')
                    : this.state === 'rejected' ? empty('circle-check', `Nothing rejected${where}`, '')
                        : empty('brain', `Nothing ${STATE_WORD[this.state]}${where} yet`, 'Features appear once patrol tracks repeat a landing, a stop or a route often enough to clear the approval line.');
                $('lrn-more').innerHTML = ''; return;
            }
            list.innerHTML = this.rows.map(f => this.rowHTML(f)).join('');
            $('lrn-more').innerHTML = n < this.total
                ? `<span>Showing ${int(n)} of ${int(this.total)}</span><button class="sh-btn" onclick="AdminUI.learner.more()">Load more</button>`
                : `<span>All ${int(this.total)} shown</span>`;
        },
        title(f) {
            const K = KIND[f.kind] || KIND[f.family === 'road' ? 'road' : f.kind] || [f.kind || f.family, 'map-pin', '#94a3b8'];
            const ev = f.evidence || {};
            let extra = f.name ? ` \u00b7 ${f.name}` : '';
            // A runway is named by its two headings, as on a chart -- only
            // when the axis was measured from take-off and landing rolls.
            if (f.family === 'airfield' && f.kind !== 'landing_zone' && ev.axis_deg != null && ev.axis_n >= 3) {
                const a = ((Math.round(ev.axis_deg / 10) + 35) % 36) + 1, b = ((a + 17) % 36) + 1;
                const p = (x) => String(x).padStart(2, '0');
                extra = ` ${p(Math.min(a, b))}/${p(Math.max(a, b))}` + extra;
            }
            return { K, text: K[0] + extra };
        },
        whereLine(f) {
            const w = f.where || {}, out = [];
            if (w.near_village) out.push(w.near_village_km != null && w.near_village_km >= 0.3
                ? `${w.near_village_km < 10 ? w.near_village_km.toFixed(1) : Math.round(w.near_village_km)} km ${w.near_village_dir || ''} of ${w.near_village}`.replace('  ', ' ')
                : `at ${w.near_village}`);
            if (w.near_river) out.push(`${w.near_river_m >= 1000 ? (w.near_river_m / 1000).toFixed(1) + ' km' : Math.round(w.near_river_m / 10) * 10 + ' m'} from the ${w.near_river}`);
            return out.join(' \u00b7 ');
        },
        chips(f) {
            const e = f.evidence || {}, c = [];
            const add = (t, title, cls) => c.push(`<span class="lrn-chip${cls ? ' ' + cls : ''}"${title ? ` title="${esc(title)}"` : ''}>${t}</span>`);
            if (!Object.keys(e).length) { add('No event evidence', 'Recorded before the event learner; kept because a reviewer decided it', 'dim'); return c.join(''); }
            if (f.family === 'airfield') {
                if (e.journeys) add(plural(e.journeys, 'flight'), 'Separate journeys that touched down or took off here');
                if (e.landings || e.takeoffs) add(`${int(e.landings)} landings \u00b7 ${int(e.takeoffs)} take-offs`);
                if (e.fixed_wing_journeys || e.rotor_wing_journeys) add([e.fixed_wing_journeys && `${int(e.fixed_wing_journeys)} plane`, e.rotor_wing_journeys && `${int(e.rotor_wing_journeys)} helicopter`].filter(Boolean).join(' \u00b7 '), 'Journeys whose airframe was measured');
                else add('airframe not measured', 'Plane or helicopter unknown', 'dim');
                if (e.length_m) add(`at least ${int(e.length_m)} m`, 'Longest ground roll seen; the strip is at least this long');
                if (e.known_airfield) add('known airfield' + (e.known_airfield_m != null ? ` ${int(e.known_airfield_m)} m away` : ''), 'Matches a mapped airfield', 'good');
            } else if (f.family === 'place') {
                if (e.events) add(plural(e.events, 'visit'));
                if (e.dwell_median_min) add(`stays ~${e.dwell_median_min >= 90 ? (e.dwell_median_min / 60).toFixed(1) + ' h' : Math.round(e.dwell_median_min) + ' min'}`, 'Median time stopped here');
                if (e.near_osm_place) add('mapped place nearby', e.near_osm_place_m != null ? `${int(e.near_osm_place_m)} m from a mapped place` : '', 'good');
                if (e.on_known_road) add('on a mapped road', '', 'good');
            } else {
                if (e.events) add(plural(e.events, 'pass', 'passes'));
                const len = e.road_length_m || e.length_m || f.length_m;
                if (len) add(km(len / 1000));
                if (e.connected_ends != null) add(e.connected_ends >= 2 ? 'joins the network at both ends' : e.connected_ends === 1 ? 'joins the network at one end' : 'not joined to the network', '', e.connected_ends >= 2 ? 'good' : e.connected_ends ? '' : 'dim');
                if (e.outside_reference) add('not on any mapped road', 'No reference road follows this track', 'new');
            }
            if (e.days) add(plural(e.days, 'day'), 'Distinct days it was used');
            if (e.trackers) add(plural(e.trackers, 'tracker'), 'Distinct devices that used it');
            return c.join('');
        },
        scoreHTML(f) {
            if (f.score == null) return `<div class="lrn-score none" title="Recorded before the event learner">not scored</div>`;
            const s = Math.max(0, Math.min(1, f.score)), line = f.approve_at;
            const over = line != null && s >= line;
            return `<div class="lrn-score" title="Probability it is used again${line != null ? `; goes on the map at ${pct(line)}` : ''}">
                <div class="lrn-score-n${over ? ' over' : ''}">${pct(s)}</div>
                <div class="lrn-meter" role="img" aria-label="Score ${pct(s)}${line != null ? `, approval line ${pct(line)}` : ''}">
                    <i style="width:${(s * 100).toFixed(1)}%" class="${over ? 'over' : ''}"></i>${line != null ? `<b style="left:${(line * 100).toFixed(1)}%"></b>` : ''}</div>
                ${line != null ? `<div class="lrn-score-l">line ${pct(line)}</div>` : ''}
            </div>`;
        },
        statusHTML(f) {
            const who = f.decided === 'reviewer' ? 'reviewer' : 'learner';
            const m = { auto_approved: ['ok', 'On the map'], approved: ['ok', 'On the map'], pending: ['wait', 'Undecided'], rejected: ['off', 'Rejected'] }[f.status] || ['off', f.status];
            const by = f.status === 'pending' ? '' : ` <span class="lrn-by">by ${f.decided === 'reviewer' ? 'a reviewer' : 'the learner'}</span>`;
            const reason = f.reason && f.reason !== 'evidence' && f.reason !== 'reviewer decision' ? `<span class="lrn-reason">${esc(f.reason)}</span>` : '';
            return `<div class="lrn-status" data-who="${who}"><span class="ts-badge ${m[0]}">${m[1]}</span>${by}${reason}</div>`;
        },
        key: (f) => f.type + ':' + f.id,
        rowHTML(f) {
            const t = this.title(f), e = f.evidence || {}, k = this.key(f);
            const where = this.whereLine(f);
            const onMap = f.status === 'approved' || f.status === 'auto_approved';
            const seen = [e.last_seen || f.last_seen ? `last seen ${esc(day(e.last_seen || f.last_seen))}` : '', e.first_seen ? `first ${esc(day(e.first_seen))}` : '',
            e.idle_active_days > 3 ? `<span class="lrn-idle" title="Days the park was patrolled elsewhere since this was last used">unused for ${int(e.idle_active_days)} patrol days</span>` : ''].filter(Boolean).join('<i class="af-dot"></i>');
            const args = `'${f.type}', ${f.id}`;
            return `<article class="adm-row lrn-row${sel.kind === 'learner' && sel.items.has(k) ? ' selected' : ''}" data-key="${k}">
                <label class="adm-checkwrap"><input type="checkbox" class="adm-check" aria-label="Select ${esc(t.text)}" ${sel.kind === 'learner' && sel.items.has(k) ? 'checked' : ''}
                    onchange="AdminUI.learner.pick('${k}', this)"></label>
                <div class="adm-row-main">
                    <div class="lrn-title"><i class="icon-${t.K[1]}" style="color:${t.K[2]}" aria-hidden="true"></i><span>${esc(t.text)}</span>${this.park ? '' : `<span class="af-park">${esc(parkName(f.park_id))}</span>`}</div>
                    ${where ? `<div class="adm-row-sub"><span><i class="icon-map-pin" aria-hidden="true"></i> ${esc(where)}</span></div>` : ''}
                    <div class="lrn-chips">${this.chips(f)}</div>
                    ${seen ? `<div class="adm-row-sub lrn-seen">${seen}</div>` : ''}
                </div>
                <div class="adm-row-side lrn-side">
                    ${this.scoreHTML(f)}
                    ${this.statusHTML(f)}
                </div>
                <div class="adm-row-acts">
                    <button class="sh-btn" onclick="AdminUI.learner.show(${args})" title="Close this panel and fly to it"><i class="icon-locate-fixed" aria-hidden="true"></i> Show</button>
                    ${f.status !== 'approved' ? `<button class="sh-btn adm-ok" onclick="AdminUI.learner.decide('approve', ${args})" title="${onMap ? 'Keep it on the map whatever the learner later decides' : 'Put it on the map'}"><i class="icon-check" aria-hidden="true"></i> ${onMap ? 'Confirm' : 'Approve'}</button>` : ''}
                    ${f.status !== 'rejected' ? `<button class="sh-btn sh-btn-off" onclick="AdminUI.learner.decide('reject', ${args})" title="Take it off the map and keep it off"><i class="icon-x" aria-hidden="true"></i> Reject</button>` : ''}
                    ${f.decided === 'reviewer' ? `<button class="sh-btn" onclick="AdminUI.learner.decide('release', ${args})" title="Drop the reviewer decision and let the learner decide from the evidence"><i class="icon-rotate-ccw" aria-hidden="true"></i> Hand back</button>` : ''}
                </div>
            </article>`;
        },
        find(type, id) { return this.rows.find(x => x.type === type && x.id === id); },
        pick(k, box) { const f = this.rows.find(x => this.key(x) === k); toggleSel('learner', k, f, box.checked, box.closest('.adm-row')); },
        pickAll(on) {
            if (!on) return clearSelection();
            if (sel.kind !== 'learner') { sel.items.clear(); sel.kind = 'learner'; }
            this.rows.forEach(f => sel.items.set(this.key(f), f));
            document.querySelectorAll('#lrn-list .adm-row').forEach(r => { r.classList.add('selected'); const c = r.querySelector('.adm-check'); if (c) c.checked = true; });
            bulkBar();
        },
        show(type, id) {
            const f = this.find(type, id); if (!f || f.lat == null) return;
            if (typeof toggleAdminPanel === 'function' && $('admin-panel').classList.contains('active')) toggleAdminPanel();
            try { map.flyTo({ center: [f.lon, f.lat], zoom: f.family === 'road' ? 13 : 14.5, pitch: 0, bearing: 0 }); } catch (_) { }
            const onMap = f.status === 'approved' || f.status === 'auto_approved';
            toast(onMap ? this.title(f).text : `${this.title(f).text} \u2014 ${f.status}, so it is not drawn on the map`, 'info',
                { action: { label: 'Back to the learner', onClick: () => openAdminPanel('learning') } });
        },
        endpoint: { approve: '/api/admin/approve-feature', reject: '/api/admin/reject-feature', release: '/api/admin/learner/release' },
        async decide(action, type, id, quiet) {
            const f = this.find(type, id), prev = f ? { status: f.status, decided: f.decided } : null;
            const row = document.querySelector(`#lrn-list [data-key="${type}:${id}"]`);
            if (row) row.classList.add('busy');
            try {
                const d = await postJSON(this.endpoint[action], { type, id });
                if (quiet) return d;
                const name = f ? this.title(f).text : 'Feature';
                const msg = action === 'approve' ? `${name} approved \u2014 on the map`
                    : action === 'reject' ? `${name} rejected \u2014 off the map`
                        : `${name} handed back \u2014 the learner says: ${String(d.status || '').replace('auto_approved', 'on the map').replace('pending', 'undecided')}`;
                // Undo restores WHO decided as well as what: a learner decision
                // is handed back, a reviewer decision is re-applied.
                const undo = !prev ? null : prev.decided === 'reviewer' && action !== 'release'
                    ? (prev.status === 'rejected' ? 'reject' : 'approve')
                    : action === 'release' ? (prev.status === 'rejected' ? 'reject' : 'approve') : 'release';
                toast(msg, 'success', undo ? { action: { label: 'Undo', onClick: () => this.decide(undo, type, id, true).then(() => { toast('Undone', 'info'); this.refresh(); }).catch(e => toast(e.message, 'error')) } } : {});
                this.refresh();
            } catch (e) {
                if (row) row.classList.remove('busy');
                if (quiet) throw e;
                toast(e.message, 'error');
            }
        },
        async decideMany(action, feats) {
            const items = feats.filter(Boolean).map(f => ({ type: f.type, id: f.id }));
            if (!items.length) return;
            try {
                await postJSON(action === 'approve' ? '/api/admin/bulk-approve' : '/api/admin/bulk-reject', { items });
                toast(`${plural(items.length, 'feature')} ${action === 'approve' ? 'approved' : 'rejected'}`, 'success', {
                    action: {
                        label: 'Undo', onClick: async () => {
                            // Each goes back to who had decided it before.
                            for (const f of feats) {
                                const a = f.decided === 'reviewer' ? (f.status === 'rejected' ? 'reject' : 'approve') : 'release';
                                try { await this.decide(a, f.type, f.id, true); } catch (_) { }
                            }
                            toast('Undone', 'info'); this.refresh();
                        }
                    }
                });
                clearSelection(); this.refresh();
            } catch (e) { toast(e.message, 'error'); }
        },
        refresh() {
            this.load(true);
            // The map layer draws approved features: keep it in step.
            try { if (window.LearnedMap && LearnedMap.reload) LearnedMap.reload(); } catch (_) { }
        },
    };

    // ══════════════════════ Fetch log on an automated-fetch card ══════════════
    // One bar per fetched file, placed by the period it COVERS. A backfill
    // arrives in an afternoon and describes five months; a log ordered by
    // arrival would draw that as one spike.
    function afLogHTML(s) {
        const lg = s.log; if (!lg) return '';
        const f = lg.fetches || [];
        if (!lg.fetches_total) return `<div class="af-log"><div class="af-log-h">Fetch log</div><p class="adm-muted">Nothing fetched yet${s.enabled ? ' \u2014 the first run is scheduled.' : '.'}</p></div>`;
        const max = Math.max(1, ...f.map(x => x.points));
        const bars = f.map(x => {
            const bad = x.status && x.status !== 'completed';
            const tip = `${range(x.from, x.to) || 'period unknown'}: ${int(x.points)} points, ${km(x.km)}${bad ? ' \u2014 ' + x.status : ''}`;
            return `<i class="${bad ? 'bad' : ''}${x.points ? '' : ' nil'}" style="height:${Math.max(6, Math.round(100 * x.points / max))}%" title="${esc(tip)}"></i>`;
        }).join('');
        const shown = f.length < lg.fetches_total ? `last ${f.length} of ${int(lg.fetches_total)} fetches` : plural(lg.fetches_total, 'fetch', 'fetches');
        const b = lg.backfill;
        const done = b ? (b.by_state.done || 0) : 0;
        const rows = [
            `<div><dt>Covers</dt><dd>${esc(range(lg.cover_from, lg.cover_to)) || '\u2014'}</dd></div>`,
            `<div><dt>Delivered</dt><dd>${plural(s.uploads || lg.fetches_total, 'file')} \u00b7 ${int(s.total_km)} km</dd></div>`,
            lg.archive_files ? `<div title="The GPX as it was fetched, kept on the server so it can be re-read without asking the tracking server again"><dt>Raw GPX kept</dt><dd>${plural(lg.archive_files, 'file')} \u00b7 ${bytes(lg.archive_bytes)}</dd></div>`
                : `<div><dt>Raw GPX kept</dt><dd class="adm-muted">none</dd></div>`,
            b ? `<div title="${esc((b.errors || []).join('\n'))}"><dt>History backfill</dt><dd>${done === b.windows
                ? `<span class="ts-badge ok">complete</span> ${int(b.windows)} weeks from ${esc(day(b.from))}`
                : `<span class="ts-badge warn">${int(done)} of ${int(b.windows)} weeks</span>${b.by_state.failed ? ` ${int(b.by_state.failed)} failed` : ''}`}${b.at ? ` <span class="adm-muted">${esc(ago(b.at))}</span>` : ''}</dd></div>` : '',
        ].join('');
        return `<div class="af-log">
            <div class="af-log-h">Fetch log <small>${shown}, by the period each covers</small>
                <button class="adm-link" onclick="switchAdminTab('uploads', {source_id:${s.id}, source_name:'${typeof jsq === 'function' ? jsq(s.title || String(s.service_url || '').replace(/^https?:\/\//, '').replace(/\/.*$/, '')) : ''}'})">Open in Activity <i class="icon-arrow-right" aria-hidden="true"></i></button></div>
            <div class="af-log-bars" role="img" aria-label="${esc(`${shown}: points per fetch, ${range(f[0] && f[0].from, f.length && f[f.length - 1].to)}`)}">${bars}</div>
            <div class="af-log-axis"><span>${esc(day(f[0] && f[0].from))}</span><span>${esc(day(f.length && f[f.length - 1].to))}</span></div>
            <dl class="af-log-facts">${rows}</dl>
        </div>`;
    }

    // ══════════════════════════════ Panel keyboard ════════════════════════════
    document.addEventListener('keydown', (e) => {
        const panel = $('admin-panel');
        if (!panel || !panel.classList.contains('active')) return;
        if (e.key === 'Escape' && !e.defaultPrevented) {
            // A selection is dismissed first; then the panel.
            if (sel.items.size) { clearSelection(); return; }
            const t = e.target;
            if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) && t.type !== 'checkbox') { t.blur(); return; }
            if (document.querySelector('.share-popup, .app-modal.active, dialog[open]')) return;
            toggleAdminPanel();
            return;
        }
        const tab = e.target.closest && e.target.closest('.admin-tab');
        if (!tab) return;
        const tabs = [...panel.querySelectorAll('.admin-tab')].filter(t => t.offsetParent !== null);
        const i = tabs.indexOf(tab);
        const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
        let next = null;
        if (step) next = tabs[(i + step + tabs.length) % tabs.length];
        else if (e.key === 'Home') next = tabs[0];
        else if (e.key === 'End') next = tabs[tabs.length - 1];
        if (next) { e.preventDefault(); next.focus(); switchAdminTab(next.dataset.tab); }
    });

    window.AdminUI = {
        activity, learner, afLogHTML, arm, clearSelection,
        closed() { clearSelection(); },
    };
    // A deep link (?admin_tab=learning) can open the panel before this
    // deferred script has run: fill whichever of our tabs is showing.
    const p = $('admin-panel');
    if (p && p.classList.contains('active')) {
        const on = p.querySelector('.admin-tab.active');
        if (on && on.dataset.tab === 'uploads') activity.open();
        if (on && on.dataset.tab === 'learning') learner.open();
    }
})();
