// ShareLink — the one popup every "copy link" in this app opens.
//
// WHY ONE COMPONENT. Before this there were eight clipboard call sites, each
// with its own toast, each producing 300-500 characters of view state. That is
// a correct link and an unusable one: it cannot be read aloud, footnoted, or
// typed off a second screen. srv/shortlink.go turns any of them into /s/{slug};
// this file is the whole of its user interface, and it is deliberately ONE
// LINE, because the line already says everything a hint would:
//
//     https://host/s/[ virunga-fires ]   ( Copy link )
//
// The slug is the only green, editable-looking thing on the line, with a
// dashed underline and a caret on tap. An explicit "tap the green name to
// rename" caption was in the design this replaces; it is gone on purpose —
// a control that needs a caption saying it is a control is not finished.
//
// INVARIANTS (see docs/agents/sharing.md):
//   * Never block the copy on the network. The long URL is put on the
//     clipboard inside the user's gesture; the short one replaces it when it
//     arrives, and if the POST fails the user still has a working link.
//   * A named slug is a NAME (renameable, guessable, behind the password
//     gate). A guest slug is a KEY (never renameable, read-only, expiring).
//     The popup must never let the two look alike.
//   * A guest may not mint links — the server refuses twice, and this file
//     does not offer the affordance at all when window.IS_GUEST.
(function () {
    'use strict';

    var POPUP_ID = 'sharelink-pop';
    // state: the link currently on screen, plus both flavours of it once they
    // have been minted (namedLink / guestLink), so flipping the switch back
    // and forth recalls rather than re-mints — an orphan capability per
    // indecisive click is exactly the kind of key nobody remembers to revoke.
    var state = null;
    var lastFocus = null; // returned to on close, so the keyboard does not jump

    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) {
        if (typeof showToast === 'function') showToast(msg, type || 'info');
    }
    function pwd() { return (typeof getPwd === 'function' ? getPwd() : '') || ''; }
    function isGuest() { return !!window.IS_GUEST; }

    // ── clipboard ────────────────────────────────────────────────────────
    // writeText outside a user gesture is refused by Safari and, on http
    // origins, missing entirely. Both failures are silent-by-design here: the
    // popup is showing the URL, so a failed background re-copy costs the user
    // one tap on a button that is already in front of them.
    function copy(text, loud) {
        var done = function () { if (loud) flashCopied(); };
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                return navigator.clipboard.writeText(text).then(done, function () {
                    if (loud) legacyCopy(text);
                });
            }
        } catch (e) { /* fall through */ }
        if (loud) legacyCopy(text);
        return Promise.resolve();
    }
    function legacyCopy(text) {
        try {
            var ta = document.createElement('textarea');
            ta.value = text;
            ta.setAttribute('readonly', '');
            ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
            document.body.appendChild(ta);
            ta.select();
            var ok = document.execCommand('copy');
            ta.remove();
            if (ok) { flashCopied(); return; }
        } catch (e) { /* fall through */ }
        window.prompt('Copy this link:', text);
    }
    function flashCopied() {
        var btn = document.querySelector('#' + POPUP_ID + ' .sl-copy');
        if (!btn) return;
        btn.classList.add('is-copied');
        setTimeout(function () { btn.classList.remove('is-copied'); }, 1400);
    }

    // ── server ───────────────────────────────────────────────────────────
    //
    // stripPwd is the CHOKEPOINT for the app's one credential. Every URL that
    // enters this module — the long URL copied inside the click, a download
    // href built with `?pwd=` so the anchor works, anything a caller hands to
    // open() — passes through absolute(), so scrubbing here covers the paths
    // no one thought about. A share link is a name; the reader authenticates
    // as themselves (docs/agents/sharing.md).
    function stripPwd(u) {
        try {
            var url = new URL(u, window.location.href);
            url.searchParams.delete('pwd');
            return url.toString();
        } catch (e) { return u; }
    }
    function absolute(u) {
        try { return stripPwd(new URL(u, window.location.href).toString()); } catch (e) { return u; }
    }
    // shortURL builds what the user sees and copies. It never carries `pwd`:
    // it used to, for a session authenticated by query param, on the theory
    // that the recipient might need it — which is precisely backwards. The
    // recipient's own cookie, password form or guest key answers for them, and
    // a password in a URL is exactly what the guest link exists to avoid.
    function shortURL(slug, guest) {
        return window.location.origin + '/s/' + slug;
    }

    function create(url, opts) {
        opts = opts || {};
        return fetch('/api/shortlink' + (pwd() ? '?pwd=' + encodeURIComponent(pwd()) : ''), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: absolute(url), title: opts.title || '',
                kind: opts.kind || 'view', guest: !!opts.guest,
                days: opts.days || 0,
                // lock_dates: confine the KEY to the window it opens on. Only
                // ever true for a guest link and only when that window is a
                // fact; the server refuses to lock a view with no dates rather
                // than storing a lock that locks nothing.
                lock_dates: !!opts.lockDates,
                // undefined (not false) when unstated: the server reads three
                // states here, and "unstated" means "whatever the shared view
                // was showing", which is the default worth keeping.
                patrol: opts.patrol,
                // The purpose tags ride on the mint so a re-minted key stays
                // in its groups — tagging afterwards via /retag also works, but
                // a remint that dropped them would silently exempt the new key
                // from the next "renew #report". A SET: one link can belong to
                // a report and to a workshop.
                tags: opts.tags || (opts.tag ? [opts.tag] : [])
            })
        }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (res) {
                if (!res.ok || !res.d || !res.d.slug) throw new Error((res.d && res.d.error) || 'no slug');
                return res.d;
            });
    }

    // shortenURL — the helper other modules use when they want a short link
    // without a popup. Falls back to the long URL rather than failing: a link
    // that is ugly still works, a link that is missing does not.
    function shortenURL(url, opts) {
        return create(url, opts).then(function (d) {
            return shortURL(d.slug, d.guest);
        }, function () { return absolute(url); });
    }

    // ── the dialog ────────────────────────────────────────────────────────
    //
    // CENTRED AND MODAL, not a tooltip pinned to the button. The old version
    // floated next to whatever was clicked, which meant it had to dodge the
    // time slider, the toolbar and the screen edge, and it still landed
    // somewhere different every time. A share link is a small decision but a
    // deliberate one — you read a URL, maybe rename it, maybe decide who may
    // open it — so it gets the middle of the screen, one place, every time,
    // with everything else dimmed behind it. The same component is the phone
    // layout; there is no second design to keep in sync.
    //
    // The copy has ALREADY HAPPENED by the time this is on screen. The dialog
    // is not a prompt asking permission to copy; it is a receipt you can edit.
    function build() {
        var el = document.getElementById(POPUP_ID);
        if (el) return el;
        el = document.createElement('div');
        el.id = POPUP_ID;
        el.className = 'sl-scrim';
        el.innerHTML =
            '<div class="sl-card" role="dialog" aria-modal="true" aria-labelledby="sl-title">' +
            '  <div class="sl-head">' +
            '    <span class="sl-title" id="sl-title">Link copied</span>' +
            '    <button class="sl-close" id="sl-close" type="button" aria-label="Close">' +
            '      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            '           stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg></button>' +
            '  </div>' +
            '  <div class="sl-url" id="sl-url">' +
            '    <span class="sl-prefix" id="sl-prefix"></span>' +
            '    <span class="sl-slug" id="sl-slug" spellcheck="false" autocapitalize="off" ' +
            '          autocorrect="off" role="textbox" aria-label="Link name — edit to rename"></span>' +
            '    <button class="sl-pen" id="sl-pen" type="button" tabindex="-1" aria-hidden="true">' +
            '      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            '           stroke-linecap="round" stroke-linejoin="round">' +
            '        <path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg></button>' +
            '  </div>' +
            '  <div class="sl-msg" id="sl-msg"></div>' +
            // WHEN the link is about. Present only when there is a genuine
            // choice to make (see dateChoice()): the view is showing a rolling
            // preset, so the same URL can mean either "the last 90 days,
            // whenever you open it" or "15 May to 12 August". Those are
            // different links, not a formatting detail, and the sender is the
            // only person who knows which one they meant.
            '  <div class="sl-modes sl-dates" id="sl-dates" role="radiogroup" aria-label="Which dates this link shows">' +
            '    <button class="sl-mode" id="sl-date-rolling" type="button" role="radio" data-fixed="0">' +
            '      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            '           stroke-linecap="round"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/></svg>' +
            '      <span class="sl-mode-name">Always up to date</span>' +
            '      <span class="sl-mode-sub" id="sl-date-rolling-sub"></span></button>' +
            '    <button class="sl-mode" id="sl-date-fixed" type="button" role="radio" data-fixed="1">' +
            '      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            '           stroke-linecap="round"><rect x="3" y="4" width="18" height="17" rx="2"/>' +
            '        <path d="M8 2v4M16 2v4M3 10h18"/></svg>' +
            '      <span class="sl-mode-name">These exact dates</span>' +
            '      <span class="sl-mode-sub" id="sl-date-fixed-sub"></span></button>' +
            '  </div>' +
            // The choice, as a two-way switch rather than a checkbox: both
            // options are named, so neither is the unlabelled absence of the
            // other, and what each one means is written under it as it is
            // selected — not in a tooltip nobody opens.
            '  <div class="sl-modes" id="sl-modes" role="radiogroup" aria-label="Who can open this link">' +
            '    <button class="sl-mode" id="sl-mode-normal" type="button" role="radio" data-guest="0">' +
            '      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            '           stroke-linecap="round"><rect x="3" y="11" width="18" height="11" rx="2"/>' +
            '        <path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>' +
            '      <span class="sl-mode-name">Needs the password</span>' +
            '      <span class="sl-mode-sub">for people who already sign in here</span></button>' +
            '    <button class="sl-mode" id="sl-mode-guest" type="button" role="radio" data-guest="1">' +
            '      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            '           stroke-linecap="round"><circle cx="12" cy="8" r="4"/>' +
            '        <path d="M4 21a8 8 0 0 1 16 0"/></svg>' +
            '      <span class="sl-mode-name">Opens for anyone</span>' +
            '      <span class="sl-mode-sub" id="sl-guest-sub">read-only · expires</span></button>' +
            '  </div>' +
            // Only shown once "opens for anyone" is chosen: these are the
            // properties of a KEY (how long it lives, what it unlocks), and
            // they are meaningless for a link that still asks for a password.
            '  <div class="sl-opts" id="sl-opts">' +
            '   <div class="sl-opts-inner">' +
            '    <div class="sl-opt">' +
            '      <span class="sl-opt-label">Stops working</span>' +
            '      <div class="sl-days" id="sl-days" role="radiogroup" aria-label="Link lifetime"></div>' +
            '    </div>' +
            '    <div class="sl-opt" id="sl-opt-patrol">' +
            '      <span class="sl-opt-label">Patrol tracks</span>' +
            '      <button class="sl-sw" id="sl-patrol" type="button" role="switch">' +
            '        <span class="sl-sw-track"><span class="sl-sw-knob"></span></span>' +
            '        <span class="sl-sw-text" id="sl-patrol-text"></span></button>' +
            '    </div>' +
            '    <div class="sl-opt" id="sl-opt-datelock">' +
            '      <span class="sl-opt-label">Other dates</span>' +
            '      <button class="sl-sw" id="sl-datelock" type="button" role="switch">' +
            '        <span class="sl-sw-track"><span class="sl-sw-knob"></span></span>' +
            '        <span class="sl-sw-text" id="sl-datelock-text"></span></button>' +
            '    </div>' +
            '   </div>' +
            '  </div>' +
            '  <div class="sl-note" id="sl-note"></div>' +
            // The purpose tag — one quiet line. A tag groups every link made
            // for one purpose ("report") so Admin can renew or audit them
            // together; most links need none, so this is a whisper, not a form.
            '  <div class="sl-tagrow" id="sl-tagrow"></div>' +
            '  <button class="sl-copy" id="sl-copy" type="button">' +
            '    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            '         stroke-linecap="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>' +
            '    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>' +
            '    <span class="sl-copy-label">Copy again</span></button>' +
            '</div>';
        document.body.appendChild(el);

        el.querySelector('#sl-copy').addEventListener('click', function () { copy(current(), true); });
        el.querySelector('#sl-close').addEventListener('click', close);
        // Click-outside closes; a click inside must not bubble out and do it.
        el.addEventListener('click', function (e) { if (e.target === el) close(); });
        el.querySelector('.sl-card').addEventListener('click', function (e) { e.stopPropagation(); });

        el.querySelector('#sl-mode-normal').addEventListener('click', function () { setGuest(false); });
        el.querySelector('#sl-mode-guest').addEventListener('click', function () { setGuest(true); });
        el.querySelector('#sl-dates').addEventListener('click', function (e) {
            var b = e.target.closest('[data-fixed]');
            if (b) setFixedDates(b.dataset.fixed === '1');
        });
        el.querySelector('#sl-datelock').addEventListener('click', function () {
            if (!state || !state.guest) return;
            setDateLock(!state.lockDates);
        });
        el.querySelector('#sl-pen').addEventListener('click', function () { focusSlug(); });
        el.querySelector('#sl-patrol').addEventListener('click', function () {
            if (!state || !state.guest) return;
            setPatrol(!hasPatrolScope());
        });
        el.querySelector('#sl-days').addEventListener('click', function (e) {
            var b = e.target.closest('[data-days]');
            if (b) setDays(parseInt(b.dataset.days, 10));
        });

        var slug = el.querySelector('#sl-slug');
        slug.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); slug.blur(); }
            else if (e.key === 'Escape') { e.stopPropagation(); renderSlug(); slug.blur(); }
        });
        // Paste as plain text: markup from a rich source would be silently
        // eaten by shortSlugify, and the field is meant to show the rule.
        slug.addEventListener('paste', function (e) {
            e.preventDefault();
            var t = (e.clipboardData || window.clipboardData).getData('text') || '';
            document.execCommand('insertText', false, t.trim());
        });
        slug.addEventListener('focus', function () {
            if (state && state.guest) { slug.blur(); return; }
            var el2 = document.getElementById(POPUP_ID);
            if (el2) el2.classList.add('is-editing');
        });
        slug.addEventListener('blur', function () {
            var el2 = document.getElementById(POPUP_ID);
            if (el2) el2.classList.remove('is-editing');
            commitRename();
        });
        return el;
    }

    function focusSlug() {
        var slug = document.getElementById('sl-slug');
        if (!slug || !state || state.guest || !state.slug) return;
        slug.focus();
        try {
            var rng = document.createRange();
            rng.selectNodeContents(slug);
            var sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(rng);
        } catch (e) { /* selection is a nicety */ }
    }

    function current() {
        if (!state) return '';
        return state.slug ? shortURL(state.slug, state.guest) : absolute(state.long);
    }

    // renderSlug — crossfades when the NAME actually changes.
    //
    // Switching between a name and a key swaps a 7-character word for a
    // 18-character token AND its colour. Done bluntly that is a flicker that
    // reads as a page reload; faded through half-opacity it reads as the same
    // line changing its mind, which is what happened. Unchanged text is left
    // strictly alone, so typing in the field is never interrupted.
    function renderSlug() {
        var el = document.getElementById('sl-slug');
        if (!el || !state) return;
        var next = state.slug || '';
        if (el.textContent === next) return;
        if (document.activeElement === el) { el.textContent = next; return; }
        el.classList.add('is-swapping');
        setTimeout(function () {
            el.textContent = next;
            el.classList.remove('is-swapping');
        }, 110);
    }

    function render() {
        var el = build();
        var guest = !!(state && state.guest);
        var ready = !!(state && state.slug);

        el.querySelector('#sl-prefix').textContent = window.location.host + '/s/';
        renderSlug();
        el.classList.toggle('is-guest', guest);
        el.classList.toggle('is-pending', !ready);

        // A guest slug IS the secret, so it must not look editable: renaming
        // it is refused by the server ("a shared link keeps its name — the
        // name is the key") and a memorable capability is a guessable one.
        var slug = el.querySelector('#sl-slug');
        slug.setAttribute('contenteditable', ready && !guest ? 'true' : 'false');

        el.querySelector('#sl-title').textContent =
            state && state.copied === false ? 'Share this view' : 'Link copied';

        el.querySelector('#sl-mode-normal').setAttribute('aria-checked', String(!guest));
        el.querySelector('#sl-mode-guest').setAttribute('aria-checked', String(guest));
        // Minting a guest link requires a password session; a guest holding a
        // link may not make more of them (the server refuses twice).
        el.querySelector('#sl-modes').classList.toggle('no-guest', isGuest());

        var sub = el.querySelector('#sl-guest-sub');
        if (sub) {
            sub.textContent = 'read-only' +
                (state && state.expires ? ' · until ' + dateStr(state.expires) : ' · expires in 30 days');
        }

        renderOptions();
        renderDates();
        renderTag();
        el.querySelector('#sl-msg').innerHTML = msgHTML();
        el.querySelector('#sl-note').innerHTML = noteHTML();
    }

    // ── the purpose tags ─────────────────────────────────────────
    //
    // A row of chips: “#report” “#workshop” plus a ghosted “# add tag”. The
    // control is TagChips (srv/static/tagchips.js), the same one Admin and the
    // export card use — a tag that looked different in each place taught the
    // user they were different things.
    //
    // SEVERAL, always. One link can be cited by a report AND handed out at a
    // workshop; forcing it to pick would silently drop it out of the next
    // "renew #report", which is the accident tags exist to prevent.
    var tagCtl = null;
    function renderTag() {
        var box = document.getElementById('sl-tagrow');
        if (!box || !state) return;
        if (!tagCtl) {
            tagCtl = TagChips.mount(box, {
                tags: state.tags || (state.tag ? [state.tag] : []),
                size: 'md',
                // A guest cannot write, so its chips are read-only rather than
                // absent: a link shown to a guest still says which purpose it
                // belongs to, and a control that would 401 is not drawn.
                editable: !isGuest(),
                onAdd: function (t) { return pushTags(addTo(state.tags, t)); },
                onRemove: function (t) { return pushTags(removeFrom(state.tags, t)); },
                // Renaming from here renames the tag on EVERY link carrying it,
                // exactly as in Admin: a tag is one name for one purpose, and a
                // per-row rename would fork the group.
                onRename: function (oldT, next) { return renameEverywhere(oldT, next); }
            });
        } else {
            tagCtl.setTags(state.tags || []);
        }
    }

    function addTo(list, t) {
        list = (list || []).slice();
        if (list.indexOf(t) < 0) list.push(t);
        return list;
    }
    function removeFrom(list, t) {
        return (list || []).filter(function (x) { return x !== t; });
    }

    // pushTags — tags the LINK ON SCREEN in place. A tag is bookkeeping, not a
    // grant, so unlike days/scope it does NOT remint; and the set the server
    // stores is what we draw, never the set we asked for.
    function pushTags(next) {
        if (!state) return Promise.resolve([]);
        var prev = (state.tags || []).slice();
        state.tags = next.slice();
        state.tag = next[0] || '';
        if (!state.slug) return Promise.resolve(state.tags);
        return fetch('/api/shortlink/' + encodeURIComponent(state.slug) + '/retag' +
            (pwd() ? '?pwd=' + encodeURIComponent(pwd()) : ''), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tags: next })
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (d) {
                if (!r.ok) throw new Error(d.error || 'could not change the tag');
                state.tags = d.tags || [];
                state.tag = state.tags[0] || '';
                return state.tags;
            });
        }, function () {
            state.tags = prev; state.tag = prev[0] || '';
            throw new Error('could not change the tag');
        });
    }

    function renameEverywhere(oldT, next) {
        if (!state) return Promise.resolve([]);
        return fetch('/api/shortlinks/retag' + (pwd() ? '?pwd=' + encodeURIComponent(pwd()) : ''), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tag: oldT, new_tag: next })
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (d) {
                if (!r.ok) throw new Error(d.error || 'could not rename the tag');
                // Say what actually moved: a rename that touched one link when
                // the user expected twelve must not read the same as success.
                toast('Tag renamed on ' + d.renamed + ' link' + (d.renamed === 1 ? '' : 's'), 'success');
                // Sorted, like every other surface's set: two views of one
                // link must not order its tags two ways (AGENTS.md inv. 7).
                state.tags = addTo(removeFrom(state.tags, oldT), next).sort();
                state.tag = state.tags[0] || '';
                return state.tags;
            });
        });
    }

    var DAY_CHOICES = [
        { d: 1, label: 'tomorrow' },
        { d: 7, label: 'a week' },
        { d: 30, label: 'a month' },
        { d: 90, label: '3 months' },
        { d: 365, label: 'a year' }
    ];

    function hasPatrolScope() {
        return !!state && (state.scope || '').split(',').indexOf('patrol') >= 0;
    }
    // Can this session share patrol data at all? If the account owns none, or
    // this page is itself a guest without the capability, then the toggle is
    // not a choice — it is a control that could only ever be off, and one of
    // those is worse than no control, because it implies the data exists.
    function canSharePatrol() {
        return window.HAS_PATROL !== false && !isGuest();
    }

    function renderOptions() {
        var el = document.getElementById(POPUP_ID);
        if (!el) return;
        var guest = !!(state && state.guest);
        var opts = el.querySelector('#sl-opts');
        opts.classList.toggle('is-on', guest);
        // inert while collapsed: a zero-height panel is still tabbable, and a
        // focus ring landing on something nobody can see is how a smooth
        // dialog starts feeling haunted.
        opts.querySelectorAll('button').forEach(function (b) { b.tabIndex = guest ? 0 : -1; });
        if (!guest) return;

        // Built ONCE. Re-writing innerHTML on every render would restart every
        // transition mid-flight and blur whatever the user was on, which is
        // precisely the "it reloaded" feeling this dialog must not have.
        var box = el.querySelector('#sl-days');
        if (!box.firstChild) {
            box.innerHTML = DAY_CHOICES.map(function (c) {
                return '<button class="sl-day" type="button" role="radio" data-days="' +
                    c.d + '">' + c.label + '</button>';
            }).join('');
        }
        var days = (state && state.days) || 30;
        box.querySelectorAll('.sl-day').forEach(function (b) {
            b.setAttribute('aria-checked', String(parseInt(b.dataset.days, 10) === days));
        });

        var pat = el.querySelector('#sl-opt-patrol');
        pat.style.display = canSharePatrol() ? '' : 'none';
        var on = hasPatrolScope();
        var sw = el.querySelector('#sl-patrol');
        sw.setAttribute('aria-checked', String(on));
        sw.classList.toggle('is-on', on);
        // The label states the CONSEQUENCE, not the position of the switch:
        // "on/off" makes the reader work out what is on, and the thing being
        // decided is whether a stranger sees where rangers walked.
        el.querySelector('#sl-patrol-text').textContent = on
            ? 'visible to whoever opens this'
            : 'hidden';

        // The date lock. Absent, not disabled, when there is no window to
        // lock: a switch that could only ever be off implies a restriction
        // exists to be made, and here none does.
        var dl = el.querySelector('#sl-opt-datelock');
        var lockable = !!(state && (state.window || urlDates(state.long).from));
        dl.style.display = lockable ? '' : 'none';
        var locked = !!(state && state.lockDates);
        var sw2 = el.querySelector('#sl-datelock');
        sw2.setAttribute('aria-checked', String(locked));
        sw2.classList.toggle('is-on', locked);
        // Stated as what the reader may do, because that is the decision —
        // "date lock: on" makes the reader work out who is locked out of what.
        el.querySelector('#sl-datelock-text').textContent = locked
            ? 'blocked — this window only'
            : 'they can browse any dates';
    }

    // One line under the URL, and usually empty. There is no "tap the name to
    // rename it" caption any more: the name is the only green, boxed,
    // pencil-bearing thing on the line, which says it already. A caption
    // explaining an affordance is an admission the affordance failed.
    function msgHTML() {
        if (!state) return '';
        if (state.error) return '<span class="sl-warn">' + esc(state.error) + '</span>';
        if (!state.slug) return '<span class="sl-dim">shortening…</span>';
        if (state.guest) return '<span class="sl-dim">this name is the key — it cannot be changed</span>';
        return '';
    }

    // What a key exposes, stated where the decision is made rather than found
    // out later. Everything else on this map is public geography a guest may
    // freely explore; patrol tracks are where named people walked, so that is
    // the one line worth spending here (srv/guest.go).
    function noteHTML() {
        if (!state || !state.guest) return '';
        var out = [];
        if (hasPatrolScope()) {
            out.push('<span class="sl-note-warn">Whoever opens this can see your patrol tracks.</span>');
        } else if (canSharePatrol()) {
            out.push('<span class="sl-dim">Fires, forest loss and settlements only — patrol tracks stay private.</span>');
        }
        // The window is stated once it is a GRANT rather than a starting
        // point: the sender should read the sentence the recipient will live
        // inside before they send it, not learn it from a complaint later.
        if (state.lockDates && state.dateFrom && state.dateTo) {
            out.push('<span class="sl-note-warn">Only ' +
                esc(rangeStr({ from: state.dateFrom, to: state.dateTo })) +
                ' — other dates come back empty.</span>');
        }
        return out.join('<br>');
    }

    // ── WHEN ────────────────────────────────────────────────────────────
    //
    // Every share URL carries a time window, and it carries it in one of two
    // ways that look identical in the address bar and mean opposite things.
    //
    //   date_preset=90d   a RULE. Resolved against whoever's clock opens it,
    //                     so the map moves with time. Right for a standing
    //                     bookmark: "the last 90 days at Chinko", forever.
    //   from=…&to=…       a FACT. The same picture in March as today. Right
    //                     for a report footnote, an incident, a season.
    //
    // Sending the wrong one produces either a dashboard that quietly goes
    // stale or a citation that no longer says what it said — and in both
    // cases the sender finds out from the recipient, months later. So when the
    // view is on a preset, the dialog ASKS, because at that moment the sender
    // is the only person who knows which they meant.
    //
    // The dates are never recomputed here. They are read off the slider, which
    // already resolved the preset to load the map — a second implementation of
    // "what does 90d mean" is a second answer waiting to disagree with the one
    // on screen.
    function urlDates(u) {
        try {
            var q = new URL(u, window.location.href).searchParams;
            return { preset: q.get('date_preset') || '', from: q.get('from') || '', to: q.get('to') || '' };
        } catch (e) { return { preset: '', from: '', to: '' }; }
    }

    // The window this link would show if opened right now — the fact behind
    // the rule. Empty when neither the URL nor the slider can say.
    function resolvedWindow(u) {
        var d = urlDates(u);
        if (d.from && d.to) return { from: d.from, to: d.to };
        if (d.preset && window.dateFrom && window.dateTo) {
            return { from: window.dateFrom, to: window.dateTo };
        }
        return null;
    }

    // freeze/thaw — the two URLs the choice is between. Each is a DIFFERENT
    // link (different URL ⇒ different slug), which is why choosing re-mints
    // rather than editing something.
    function freezeURL(u) {
        var w = resolvedWindow(u);
        if (!w) return u;
        try {
            var url = new URL(u, window.location.href);
            url.searchParams.delete('date_preset');
            url.searchParams.set('from', w.from);
            url.searchParams.set('to', w.to);
            return url.toString();
        } catch (e) { return u; }
    }
    function thawURL(u, preset) {
        if (!preset) return u;
        try {
            var url = new URL(u, window.location.href);
            url.searchParams.delete('from');
            url.searchParams.delete('to');
            url.searchParams.set('date_preset', preset);
            return url.toString();
        } catch (e) { return u; }
    }

    // Is there a choice to offer at all? Only when the shared view is on a
    // rolling preset AND the slider can say what it currently means. A view
    // already pinned to explicit dates has nothing to decide — showing two
    // buttons with one possible answer would invent a decision.
    function hasDateChoice() {
        return !!(state && state.preset && state.window);
    }

    // A short, human range: "15 May – 12 Aug 2026", and "12 Aug 2026" when
    // both ends are the same day (a one-day window written as a range reads
    // like a bug).
    function rangeStr(w) {
        if (!w) return '';
        var a = dateStr(w.from), b = dateStr(w.to);
        return a === b ? a : a + ' – ' + b;
    }

    var PRESET_WORDS = {
        td: 'today', '3d': 'the last 3 days', '7d': 'the last 7 days',
        '14d': 'the last 14 days', '30d': 'the last 30 days',
        '90d': 'the last 90 days', cmo: 'this month'
    };
    function presetWords(p) { return PRESET_WORDS[p] || 'the current window'; }

    function renderDates() {
        var el = document.getElementById(POPUP_ID);
        if (!el || !state) return;
        var box = el.querySelector('#sl-dates');
        box.classList.toggle('no-guest', !hasDateChoice()); // reuses the hide rule
        if (!hasDateChoice()) return;
        var fixed = !!state.fixed;
        el.querySelector('#sl-date-rolling').setAttribute('aria-checked', String(!fixed));
        el.querySelector('#sl-date-fixed').setAttribute('aria-checked', String(fixed));
        // Each option says what the RECIPIENT will see, in their words, not
        // what the parameter is called in ours.
        el.querySelector('#sl-date-rolling-sub').textContent =
            presetWords(state.preset) + ', whenever they open it';
        el.querySelector('#sl-date-fixed-sub').textContent = rangeStr(state.window);
    }

    // setFixedDates — swaps which URL is being shared, and re-mints, because
    // the two are not one link with a setting: they are two links, and the one
    // already on the clipboard has to keep meaning what it meant.
    function setFixedDates(want) {
        if (!state || !hasDateChoice() || !!state.fixed === !!want) return;
        state.fixed = !!want;
        // A locked window is a statement about specific dates; a rolling link
        // has none to state. Un-freezing therefore drops the lock rather than
        // carrying an unenforceable promise into the next mint.
        if (!want) state.lockDates = false;
        state.long = want ? freezeURL(state.long) : thawURL(state.long, state.preset);
        // Both remembered links described the OLD url; neither can be recalled.
        state.namedLink = null;
        state.guestLink = null;
        state.slug = '';
        renderDates();
        remintCurrent(want ? 'fixing the dates…' : 'making it follow today…');
    }

    // setDateLock — may the holder look at other dates once the map is open?
    //
    // This is the second question, and it only exists for a key. A frozen URL
    // says what the link OPENS at; it says nothing about what the holder may
    // do next, and dragging the time slider is the first thing anyone does.
    // Usually that is fine and this switch stays off — an interactive map that
    // punishes exploration is a screenshot with extra steps. Sometimes the
    // link exists precisely because one season, one incident or one reporting
    // period is what was agreed, and then the window is part of the grant.
    function setDateLock(on) {
        if (!state || !state.guest) return;
        // Locking a rolling link is incoherent — it would confine the holder
        // to a window that moves under them — so asking for the lock takes the
        // frozen URL with it rather than refusing the click.
        if (on && hasDateChoice() && !state.fixed) { state.lockDates = true; setFixedDates(true); return; }
        state.lockDates = !!on;
        renderOptions();
        remintGuest(on ? 'limiting it to these dates…' : 'opening up the other dates…');
    }

    // remintCurrent — re-mint whichever KIND of link is on screen. A named
    // link is deduped by URL server-side, so this costs a row only when the
    // frozen view is genuinely new.
    function remintCurrent(what) {
        if (state.guest) return remintGuest(what);
        var msg = document.getElementById('sl-msg');
        if (msg) msg.innerHTML = '<span class="sl-dim">' + esc(what) + '</span>';
        var long = state.long;
        create(long, { title: state.title, kind: state.kind, tags: state.tags })
            .then(function (d) {
                if (!state || state.long !== long) return;
                apply({ slug: d.slug, guest: false });
                remember();
                copy(current(), true);
            })
            .catch(function (e) {
                if (!state) return;
                state.error = (e && e.message) || 'could not change that';
                render();
            });
    }

    function dateStr(iso) {
        try {
            var d = new Date(iso);
            return isNaN(d) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
        } catch (e) { return iso; }
    }

    // setGuest — the switch. Each mode is a DIFFERENT LINK, not a setting on
    // one: a named link cannot become a capability, and a capability must not
    // be silently downgraded into a name someone can guess. So flipping the
    // switch mints (or recalls) the other link and re-copies it, which is why
    // the previous one is remembered — flipping back and forth must not leave
    // a trail of orphan keys.
    function setGuest(want) {
        if (!state || !!state.guest === !!want) return;
        if (want && isGuest()) return;
        var slot = want ? 'guestLink' : 'namedLink';
        remember();
        var have = state[slot];
        if (have) {
            apply(have);
            copy(current(), true);
            return;
        }
        state.busy = true;
        state.error = '';
        var msg = document.getElementById('sl-msg');
        if (msg) msg.innerHTML = '<span class="sl-dim">' +
            (want ? 'making a link anyone can open…' : 'switching back…') + '</span>';
        create(state.long, {
            title: state.title, kind: state.kind, guest: want, tags: state.tags,
            days: want ? state.days : 0,
            patrol: want ? state.wantPatrol : undefined,
            lockDates: want && state.lockDates
        })
            .then(function (d) {
                state.busy = false;
                apply({ slug: d.slug, guest: !!d.guest, expires: d.expires_at, scope: d.scope || '',
                    date_from: d.date_from, date_to: d.date_to, tags: d.tags });
                remember();
                copy(current(), true);
            })
            .catch(function (e) {
                state.busy = false;
                state.error = (e && e.message) || 'could not make that link';
                render();
            });
    }

    // setDays / setPatrol — both REMINT.
    //
    // A key's lifetime and its scope are fixed at creation on purpose: they
    // are what the bearer was granted, and a link whose permissions can be
    // edited afterwards means the copy already in somebody's inbox no longer
    // describes what they hold. Changing your mind therefore produces a NEW
    // key and re-copies it; the old one keeps whatever it was and can be
    // switched off in Admin → Sharing, which is the honest record of what was
    // handed out. The alternative — mutating in place — would make the sheet
    // lie about the past.
    function setDays(days) {
        if (!state || !state.guest || days === state.days) return;
        state.days = days;
        remintGuest('changing when it stops working…');
    }

    function setPatrol(on) {
        if (!state || !state.guest) return;
        state.wantPatrol = !!on;
        remintGuest(on ? 'including patrol tracks…' : 'removing patrol tracks…');
    }

    function remintGuest(what) {
        var msg = document.getElementById('sl-msg');
        if (msg) msg.innerHTML = '<span class="sl-dim">' + esc(what) + '</span>';
        // Reflect the pending choice immediately — a switch that waits for the
        // network before it moves reads as broken.
        renderOptions();
        create(state.long, {
            title: state.title, kind: state.kind, guest: true, tags: state.tags,
            days: state.days, patrol: state.wantPatrol, lockDates: state.lockDates
        }).then(function (d) {
            apply({ slug: d.slug, guest: true, expires: d.expires_at, scope: d.scope || '',
                date_from: d.date_from, date_to: d.date_to, tags: d.tags });
            state.guestLink = null; // the remembered key is the OLD one
            remember();
            copy(current(), true);
        }).catch(function (e) {
            state.error = (e && e.message) || 'could not change that';
            // A refused lock must not keep showing as granted — the server is
            // the authority here exactly as it is for scope and lifetime.
            state.lockDates = false;
            renderOptions();
            render();
        });
    }

    function apply(link) {
        state.slug = link.slug;
        state.guest = !!link.guest;
        state.expires = link.expires || '';
        state.scope = link.scope || '';
        state.dateFrom = link.date_from || '';
        state.dateTo = link.date_to || '';
        // The server's answer wins (dedupe may return a row that already
        // carries tags, and a remint adopts the set): an unstated answer keeps
        // the user's pending choice, an empty ARRAY is an answer and is taken.
        if (Array.isArray(link.tags)) {
            state.tags = link.tags.slice();
            state.tag = state.tags[0] || '';
        }
        // A fresh mint is a fresh row: the chips must be rebuilt against it
        // rather than keep pointing at the previous link's controller.
        tagCtl = null;
        // The server is the authority on all of these: it clamps the lifetime,
        // refuses to grant a capability this session does not itself hold, and
        // refuses a lock on a view with no dates. So the UI follows the answer
        // rather than its own request — otherwise a clamped or denied choice
        // would keep showing as granted.
        if (state.guest) {
            state.wantPatrol = hasPatrolScope();
            state.lockDates = !!(link.date_from && link.date_to);
            if (link.expires) state.days = daysUntil(link.expires);
        } else {
            state.lockDates = false;
        }
        state.error = '';
        render();
    }

    function daysUntil(iso) {
        var d = Math.round((new Date(iso) - Date.now()) / 86400000);
        var best = DAY_CHOICES[0].d;
        DAY_CHOICES.forEach(function (c) {
            if (Math.abs(c.d - d) < Math.abs(best - d)) best = c.d;
        });
        return best;
    }

    function remember() {
        if (!state || !state.slug) return;
        state[state.guest ? 'guestLink' : 'namedLink'] = {
            slug: state.slug, guest: state.guest, expires: state.expires, scope: state.scope,
            date_from: state.dateFrom, date_to: state.dateTo
        };
    }

    function commitRename() {
        var el = document.getElementById('sl-slug');
        if (!el || !state || !state.slug || state.guest) return;
        var want = (el.textContent || '').trim().toLowerCase()
            .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64);
        if (!want || want === state.slug) { renderSlug(); return; }
        var from = state.slug;
        fetch('/api/shortlink/' + encodeURIComponent(from) + '/rename' +
            (pwd() ? '?pwd=' + encodeURIComponent(pwd()) : ''), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ slug: want })
        }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (res) {
                if (!res.ok || !res.d.slug) throw new Error(res.d && res.d.error);
                state.slug = res.d.slug;
                state.error = '';
                remember();
                render();
                // Renaming is the second half of copying: the clipboard must
                // not still hold the old name once the field shows a new one.
                copy(current(), true);
            })
            .catch(function (e) {
                state.error = (e && e.message) || 'that name is taken';
                renderSlug();
                render();
            });
    }

    function onKey(e) {
        if (e.key === 'Escape') { close(); return; }
        // Focus trap: a modal that lets Tab wander onto the map behind it is a
        // modal only visually.
        if (e.key !== 'Tab') return;
        var card = document.querySelector('#' + POPUP_ID + ' .sl-card');
        if (!card) return;
        var f = card.querySelectorAll('button:not([tabindex="-1"]), [contenteditable="true"]');
        if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }

    function close() {
        var el = document.getElementById(POPUP_ID);
        if (!el) return;
        el.classList.remove('is-open');
        document.removeEventListener('keydown', onKey, true);
        document.body.classList.remove('sl-open');
        state = null;
        if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) { } }
    }

    // open(url, {title, kind, guest, copy})
    //
    // The copy happens FIRST and with the long URL, inside the click that
    // asked for it: by the time the shortener answers, the gesture is over and
    // Safari will refuse the write. The short URL then replaces it silently,
    // so the clipboard is always at least as good as it was a moment ago and
    // the dialog never has to ask for permission to do its job.
    function open(url, opts) {
        opts = opts || {};
        var long = absolute(url);
        lastFocus = document.activeElement;
        var d0 = urlDates(long);
        state = {
            slug: '', guest: false, long: long, scope: '', days: 30,
            // wantPatrol is undefined until the user says otherwise, so the
            // first key grants exactly what the shared view was showing.
            wantPatrol: undefined,
            // The date question, prepared before anything is minted:
            //   preset  the rolling rule this view is on, if any
            //   window  what that rule resolves to right now, read off the
            //           slider rather than recomputed (one answer, not two)
            //   fixed   which of the two links is currently being shared
            // A view already pinned to explicit dates has fixed=true and no
            // choice to offer, which is correct: there is nothing to decide.
            preset: d0.preset,
            window: resolvedWindow(long),
            fixed: !!(d0.from && d0.to),
            lockDates: false, dateFrom: '', dateTo: '',
            // Tags are a SET on the row; `tag` is the first of them, kept only
            // for the callers that speak one word.
            tags: (opts.tags || (opts.tag ? [opts.tag] : [])).slice(),
            tag: (opts.tags && opts.tags[0]) || opts.tag || '',
            kind: opts.kind || 'view', title: opts.title || '',
            copied: opts.copy !== false, opts: opts
        };

        tagCtl = null; // a new dialog draws a fresh chip row
        var el = build();
        render();
        el.classList.add('is-open');
        document.body.classList.add('sl-open');
        document.addEventListener('keydown', onKey, true);

        if (opts.copy !== false) copy(long, false);

        create(long, { title: opts.title, kind: opts.kind, guest: !!opts.guest, tags: state.tags })
            .then(function (d) {
                if (!state || state.long !== long) return; // dialog moved on
                apply({ slug: d.slug, guest: !!d.guest, expires: d.expires_at, scope: d.scope || '',
                    date_from: d.date_from, date_to: d.date_to, tags: d.tags });
                remember();
                if (opts.copy !== false) copy(current(), false);
            })
            .catch(function () {
                if (!state || state.long !== long) return;
                // No shortener (offline, or a URL it refuses). The long link is
                // already on the clipboard and works; say so and get out of
                // the way rather than showing a dialog with nothing in it.
                close();
                copy(long, false);
                toast('Link copied', 'success');
            });
        return false;
    }
    // ── guest mode ─────────────────────────────────────────────────────
    //
    // CSS hides and dims (body.is-guest in globe.css); this is the part CSS
    // cannot do — stopping the handlers, and re-labelling the admin button.
    //
    // The pencil becomes a map: the admin panel still opens, but for a guest
    // it contains one thing, Map Settings, and a pencil promises editing. An
    // icon that promises what the panel does not do is a bug report waiting to
    // be filed.
    function applyGuestMode() {
        if (!window.IS_GUEST) return;

        var up = document.getElementById('upload-btn');
        if (up) {
            up.setAttribute('aria-disabled', 'true');
            up.title = 'Uploads need your own login — this is a read-only shared link';
            // capture:true so the inline onclick never runs; the button stays
            // in the tab order and keeps announcing itself as disabled.
            up.addEventListener('click', function (e) {
                e.preventDefault(); e.stopPropagation();
                if (typeof showToast === 'function') {
                    showToast('This is a read-only shared link — uploading needs your own login', 'info');
                }
            }, true);
        }

        var admin = document.getElementById('toolbar-admin');
        if (admin) {
            admin.title = 'Map settings';
            admin.setAttribute('aria-label', 'Map settings');
            admin.innerHTML =
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
                'stroke-linecap="round" stroke-linejoin="round">' +
                '<polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/>' +
                '<line x1="8" y1="2" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="22"/></svg>';
        }
        var title = document.querySelector('.admin-panel-title');
        if (title) {
            title.innerHTML =
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
                '<polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/>' +
                '<line x1="8" y1="2" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="22"/></svg> Map Settings';
        }

        // The panel remembers its last tab; for a guest only one tab exists,
        // so every route into it must land there rather than on a hidden tab's
        // empty body.
        if (typeof window.switchAdminTab === 'function') {
            var orig = window.switchAdminTab;
            window.switchAdminTab = function () { return orig('map-settings'); };
            try { orig('map-settings'); } catch (e) { /* panel not built yet */ }
        }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyGuestMode);
    } else { applyGuestMode(); }

    window.ShareLink = { open: open, close: close, shortenURL: shortenURL, copy: copy };
})();
