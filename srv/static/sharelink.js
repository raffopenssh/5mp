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
    function absolute(u) {
        try { return new URL(u, window.location.href).toString(); } catch (e) { return u; }
    }
    // shortURL builds what the user sees and copies. `pwd` rides along only
    // when the current session itself is authenticated by query param —
    // otherwise the recipient's own cookie answers for them, and a password in
    // a URL is exactly what the guest link exists to avoid.
    function shortURL(slug, guest) {
        var u = window.location.origin + '/s/' + slug;
        if (!guest && pwd()) u += '?pwd=' + encodeURIComponent(pwd());
        return u;
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
                // undefined (not false) when unstated: the server reads three
                // states here, and "unstated" means "whatever the shared view
                // was showing", which is the default worth keeping.
                patrol: opts.patrol
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
            '   </div>' +
            '  </div>' +
            '  <div class="sl-note" id="sl-note"></div>' +
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
        el.querySelector('#sl-msg').innerHTML = msgHTML();
        el.querySelector('#sl-note').innerHTML = noteHTML();
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
        if (hasPatrolScope()) {
            return '<span class="sl-note-warn">Whoever opens this can see your patrol tracks.</span>';
        }
        if (!canSharePatrol()) return '';
        return '<span class="sl-dim">Fires, forest loss and settlements only — patrol tracks stay private.</span>';
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
            title: state.title, kind: state.kind, guest: want,
            days: want ? state.days : 0,
            patrol: want ? state.wantPatrol : undefined
        })
            .then(function (d) {
                state.busy = false;
                apply({ slug: d.slug, guest: !!d.guest, expires: d.expires_at, scope: d.scope || '' });
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
            title: state.title, kind: state.kind, guest: true,
            days: state.days, patrol: state.wantPatrol
        }).then(function (d) {
            apply({ slug: d.slug, guest: true, expires: d.expires_at, scope: d.scope || '' });
            state.guestLink = null; // the remembered key is the OLD one
            remember();
            copy(current(), true);
        }).catch(function (e) {
            state.error = (e && e.message) || 'could not change that';
            render();
        });
    }

    function apply(link) {
        state.slug = link.slug;
        state.guest = !!link.guest;
        state.expires = link.expires || '';
        state.scope = link.scope || '';
        // The server is the authority on both: it clamps the lifetime, and it
        // refuses to grant a capability this session does not itself hold. So
        // the UI follows the answer rather than its own request — otherwise a
        // clamped or denied choice would keep showing as granted.
        if (state.guest) {
            state.wantPatrol = hasPatrolScope();
            if (link.expires) state.days = daysUntil(link.expires);
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
            slug: state.slug, guest: state.guest, expires: state.expires, scope: state.scope
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
        state = {
            slug: '', guest: false, long: long, scope: '', days: 30,
            // wantPatrol is undefined until the user says otherwise, so the
            // first key grants exactly what the shared view was showing.
            wantPatrol: undefined,
            kind: opts.kind || 'view', title: opts.title || '',
            copied: opts.copy !== false, opts: opts
        };

        var el = build();
        render();
        el.classList.add('is-open');
        document.body.classList.add('sl-open');
        document.addEventListener('keydown', onKey, true);

        if (opts.copy !== false) copy(long, false);

        create(long, { title: opts.title, kind: opts.kind, guest: !!opts.guest })
            .then(function (d) {
                if (!state || state.long !== long) return; // dialog moved on
                apply({ slug: d.slug, guest: !!d.guest, expires: d.expires_at, scope: d.scope || '' });
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
