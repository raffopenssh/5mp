/* tagchips.js — ONE tag control, used everywhere tags appear.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Purpose tags were drawn three times: `.sh-tag` in Admin → Access & Sharing,
 * `.sl-tag` in the share dialog, and an inline-styled `.gpkg-tag` on export
 * cards. Three implementations meant three behaviours — only one of them could
 * rename, only one autocompleted, only one could hold more than a single tag —
 * and the rename input was a pill nested inside a chip, so editing a tag drew
 * TWO boundaries around one word. A control that looks different in each place
 * teaches the user that they are different things. They are one thing:
 *
 *   a tag is bookkeeping, not a grant. Setting, renaming or clearing one never
 *   remints a key, never changes what it shows and never changes when it dies.
 *
 * SHAPE. A soft-cornered rectangle (6px), not a pill: a pill reads as a status
 * badge (this app already uses pills for "key", "expired", "patrol tracks"),
 * while a tag is an editable label. The outline is DASHED — the same visual
 * grammar as the "# add tag" affordance next to it, so the chip and the empty
 * slot are recognisably the same control in two states, and dashed says
 * "provisional, editable" where solid says "issued".
 *
 * EDITING IS IN PLACE AND HAS ONE BORDER. The input replaces the chip's own
 * text inside the chip's own box (`.tc-chip.is-editing > input`, input has no
 * border of its own) and is sized to its content, so a rename does not move
 * the row or double the outline.
 *
 * TAGS ARE A SET. Every surface allows several — the same link can be cited by
 * a report and handed out at a workshop, and forcing it to pick would silently
 * exempt it from the next "renew #report". After a commit the caller's promise
 * resolves with the server's set, and that is what we draw: the server
 * sanitises, dedupes and caps, so the UI never paints its own guess.
 *
 * Public API
 *   TagChips.mount(el, opts) → controller {setTags, refresh, getTags}
 *     opts.tags      array of strings (initial, may be empty)
 *     opts.editable  false → read-only chips (a guest, a dead link)
 *     opts.addLabel  text of the empty affordance (default "add tag")
 *     opts.size      'sm' (admin rows) | 'md' (dialogs)
 *     opts.onAdd(tag)            → Promise<string[]>  add one, keep the rest
 *     opts.onRemove(tag)         → Promise<string[]>  drop one from THIS link
 *     opts.onRename(old, next)   → Promise<string[]>  rename EVERYWHERE
 *     (any handler omitted → that gesture is not offered)
 *   TagChips.vocab()      → Promise<[{tag,links,live}]>, cached per page
 *   TagChips.invalidate() → forget the vocabulary (after a rename)
 */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function pwd() { return (typeof window.getPwd === 'function' ? window.getPwd() : '') || ''; }
    function toast(msg, type) {
        if (typeof window.showToast === 'function') window.showToast(msg, type || 'info');
    }

    // sanitise — the SAME rule as the server's shortSanitizeTag, applied as the
    // user types so the chip they see is the tag that will exist. Where the two
    // could disagree the server wins: we redraw from its answer.
    function sanitise(v) {
        return String(v || '').trim().toLowerCase().replace(/^#+/, '')
            .replace(/[^a-z0-9_-]+/g, '-').replace(/^[-_]+|[-_]+$/g, '').slice(0, 32);
    }

    // ── the vocabulary ───────────────────────────────────────────────────
    // The whole point of a tag is to be the SAME word as last time; a fresh
    // spelling per link is a group of one. So autocomplete is the feature, not
    // a convenience, and it is shared: one fetch per page, invalidated by any
    // rename. Counts ride along so "report (12)" is distinguishable from a
    // typo made once — a chooser that cannot show that spreads the typo.
    var vocabCache = null;
    function vocab() {
        if (vocabCache) return Promise.resolve(vocabCache);
        return fetch('/api/shortlink-tags' + (pwd() ? '?pwd=' + encodeURIComponent(pwd()) : ''))
            .then(function (r) { return r.ok ? r.json() : {}; })
            .then(function (d) {
                var detail = (d && d.detail) || [];
                if (!detail.length && d && d.tags) {
                    detail = d.tags.map(function (t) { return { tag: t, links: 0, live: 0 }; });
                }
                vocabCache = detail;
                return detail;
            })
            .catch(function () { vocabCache = []; return vocabCache; });
    }
    function invalidate() { vocabCache = null; }

    // ── inline completion, not a dropdown ────────────────────────────────
    //
    // The first version handed the input a <datalist>, which the browser
    // decorates with a dropdown arrow INSIDE the chip and opens as a floating
    // menu over the dialog: a second boundary and a second surface around one
    // word, which is exactly the noise this rewrite removed from the chip.
    //
    // Instead: type-ahead like an address bar. The rest of the best-matching
    // known tag is completed after the caret and left SELECTED, so the next
    // keystroke overwrites it (typing is never blocked) while Enter, Tab, →
    // or End accept it. Deleting never re-completes — a completion that grows
    // back while you erase it cannot be erased.
    //
    // Ranking: prefix matches first, then by how many links carry the tag. The
    // point of a tag is to be the SAME word as last time, so the word most
    // links already use is the one worth offering.
    // `exclude`: tags this link already carries. Completing to one of them
    // would offer the user a word that cannot be added (the set already holds
    // it) — a suggestion whose acceptance is a no-op reads as a broken control.
    function matches(prefix, exclude) {
        if (!prefix) return [];
        var skip = {};
        (exclude || []).forEach(function (t) { skip[t] = true; });
        return (vocabCache || []).filter(function (t) {
            return t.tag.indexOf(prefix) === 0 && t.tag !== prefix && !skip[t.tag];
        }).sort(function (x, y) { return (y.links || 0) - (x.links || 0); })
          .map(function (t) { return t.tag; });
    }

    // measure — the pixel width of a string in the input's own font.
    //
    // Sizing the input in `ch` is what put a gap between the typed text and the
    // completion: `ch` is the width of "0", and in a proportional font that is
    // wider than most letters, so "rep" reserved room for "rep0" and the ghost
    // floated off the caret — one word reading as two.
    //
    // The mirror lives on <body>, NOT inside the chip: a span appended to a
    // flex container becomes a flex item and measured 0 even when absolutely
    // positioned. Its font is copied from the live input rather than inherited,
    // so admin's 10.5px and the dialog's 11px each measure themselves.
    var mirrorEl = null;
    function measure(text, fromEl) {
        if (!mirrorEl) {
            mirrorEl = document.createElement('span');
            mirrorEl.setAttribute('aria-hidden', 'true');
            mirrorEl.style.cssText = 'position:absolute;left:-9999px;top:0;' +
                'visibility:hidden;white-space:pre;pointer-events:none;';
            document.body.appendChild(mirrorEl);
        }
        var cs = window.getComputedStyle(fromEl);
        mirrorEl.style.font = cs.font;
        mirrorEl.style.letterSpacing = cs.letterSpacing;
        mirrorEl.textContent = text;
        return Math.ceil(mirrorEl.getBoundingClientRect().width);
    }

    function mount(el, opts) {
        if (!el) return null;
        opts = opts || {};
        // ONE CONTROLLER PER ELEMENT. Mounting twice on one node (the share
        // dialog re-opens, the admin sheet reloads) left both alive: the old
        // one still held the previous link's tag array and would repaint over
        // the new one's, so a × appeared to do nothing at all. The previous
        // controller is retired before the new one draws.
        if (el.__tc) { el.__tc.retire(); }
        // `retired` is THIS mount's own flag, checked by its own render. Reading
        // el.__tc instead would read the newest controller's flag, which is
        // never set — the retired closure would keep painting.
        var retired = false;
        var tags = (opts.tags || []).slice();
        var editable = opts.editable !== false;
        var size = opts.size === 'md' ? 'md' : 'sm';
        var busy = false;
        // `editing` guards the repaint, NOT the presence of an <input>: the
        // input is still in the DOM while a commit is in flight, so a
        // DOM-based guard made the chip freeze mid-edit after Enter.
        var editing = false;

        el.classList.add('tc-row');
        el.classList.toggle('tc-md', size === 'md');

        function chipHTML(t) {
            var canRename = editable && !!opts.onRename;
            return '<span class="tc-chip" data-tag="' + esc(t) + '">' +
                '<span class="tc-hash">#</span>' +
                '<span class="tc-name"' + (canRename ? ' role="button" tabindex="0"' +
                    ' title="Rename this tag on every link that carries it"' : '') + '>' +
                esc(t) + '</span>' +
                (editable && opts.onRemove
                    ? '<button class="tc-x" type="button" title="Remove this tag from this link"' +
                      ' aria-label="Remove tag ' + esc(t) + '">\u00d7</button>'
                    : '') +
                '</span>';
        }

        function render() {
            if (retired) return; // a retired controller draws nothing
            if (editing) return; // never repaint under a typing user
            var html = tags.map(chipHTML).join('');
            // The add affordance stays visible with tags already present:
            // tags are a set, and an "add" that disappears after the first one
            // is a control that taught the user the set holds one.
            if (editable && opts.onAdd) {
                html += '<button class="tc-add" type="button" title="' +
                    esc(opts.addTitle || 'Group this link with others made for one purpose — tagged links can be renewed, audited and revoked together') +
                    '"><span class="tc-hash">#</span> ' + esc(opts.addLabel || 'add tag') + '</button>';
            }
            el.innerHTML = html;
            wire();
        }

        function wire() {
            Array.prototype.forEach.call(el.querySelectorAll('.tc-chip'), function (chip) {
                var t = chip.dataset.tag;
                var x = chip.querySelector('.tc-x');
                if (x) x.addEventListener('click', function (ev) {
                    ev.stopPropagation();
                    commit(opts.onRemove, t);
                });
                var name = chip.querySelector('.tc-name');
                if (name && opts.onRename) {
                    var go = function (ev) { ev.stopPropagation(); edit(chip, t); };
                    name.addEventListener('click', go);
                    name.addEventListener('keydown', function (ev) {
                        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(ev); }
                    });
                }
            });
            var add = el.querySelector('.tc-add');
            if (add) add.addEventListener('click', function (ev) {
                ev.stopPropagation();
                edit(add, '');
            });
        }

        // edit — turn a chip (or the add button) into an input INSIDE its own
        // box. One border, no reflow, and inline type-ahead rather than a
        // dropdown (see `matches` above for why).
        //
        // THE COMPLETION IS AN ELEMENT, NOT A SELECTION. The first cut completed
        // by writing the whole match into the input and selecting the tail —
        // the address-bar trick. It reads correctly on a desktop and is
        // unusable on a phone: selected-text-inside-an-input is not tappable,
        // and a touch keyboard has no Tab, so the suggestion could be seen and
        // not accepted. The remainder is now its own `.tc-ghost` span sitting
        // after the caret: tappable (the mobile gesture), and accepted by
        // Enter / Tab / → / End on a keyboard.
        function edit(host, initial) {
            if (editing) return;
            editing = true;
            // Warm the vocabulary AND recompute when it lands: every commit
            // invalidates the cache, so without the callback the first edit
            // after adding a tag silently had no suggestions at all — a feature
            // that works only until you use it.
            vocab().then(function () { if (!done) recompute(); });
            var span = document.createElement('span');
            span.className = 'tc-chip is-editing';
            span.innerHTML = '<span class="tc-hash">#</span>' +
                '<input type="text" maxlength="32" spellcheck="false" autocomplete="off" ' +
                'autocapitalize="off" autocorrect="off" placeholder="report" ' +
                'value="' + esc(initial) + '" aria-label="Tag name" ' +
                'aria-autocomplete="inline" aria-describedby="tc-ghost-hint">' +
                '<span class="tc-ghost" id="tc-ghost-hint" role="button" ' +
                'title="Tap to use this tag" aria-label="Use the suggested tag"></span>';
            host.replaceWith(span);
            var inp = span.querySelector('input');
            var ghost = span.querySelector('.tc-ghost');

            var resize = function () {
                var text = inp.value || '';
                // An empty field still needs somewhere to type: fall back to the
                // placeholder's width, which is the only case where reserving
                // space is right (there is no ghost to push away yet).
                inp.style.width = (measure(text || inp.placeholder || '', inp) + 1) + 'px';
            };
            // suggest — the remainder of the best match, or nothing. Never
            // written into the input: the value stays exactly what was typed,
            // so a suggestion can be ignored simply by carrying on typing.
            var suggestion = '';
            function setGhost(rest) {
                suggestion = rest || '';
                ghost.textContent = suggestion;
                span.classList.toggle('has-ghost', !!suggestion);
            }
            function accept() {
                if (!suggestion) return false;
                inp.value = inp.value + suggestion;
                setGhost('');
                resize();
                inp.focus();
                inp.setSelectionRange(inp.value.length, inp.value.length);
                return true;
            }
            function recompute() {
                var typed = inp.value.toLowerCase();
                // Only complete while what is typed is already a legal prefix;
                // otherwise the ghost would describe a word the server would
                // reject anyway.
                if (!typed || sanitise(typed) !== typed) { setGhost(''); return; }
                // Renaming excludes every OTHER tag on the link, not the one
                // being renamed: "report" → "report-2026" is a real rename.
                var others = tags.filter(function (t) { return t !== initial; });
                var best = matches(typed, others)[0];
                setGhost(best ? best.slice(typed.length) : '');
            }

            resize();
            inp.focus();
            inp.select();

            // A tap on the ghost is the mobile accept. mousedown/touchstart,
            // not click: the input's blur would otherwise commit the typed
            // fragment before the click ever lands.
            ['mousedown', 'touchstart'].forEach(function (evt) {
                ghost.addEventListener(evt, function (ev) {
                    ev.preventDefault();
                    ev.stopPropagation();
                    accept();
                });
            });

            inp.addEventListener('keydown', function (ev) {
                ev.stopPropagation(); // the globe listens for single-key shortcuts
                if (ev.key === 'Enter') {
                    // Enter takes the suggestion if one is showing, exactly as
                    // an address bar does: what is on screen is what you get.
                    ev.preventDefault();
                    accept();
                    finish(true);
                    return;
                }
                if (ev.key === 'Escape') {
                    // One Escape dismisses the suggestion, a second cancels the
                    // edit — otherwise refusing a completion means losing what
                    // you had already typed.
                    ev.preventDefault();
                    if (suggestion) { setGhost(''); return; }
                    finish(false);
                    return;
                }
                if (suggestion && (ev.key === 'Tab' || ev.key === 'ArrowRight' || ev.key === 'End')) {
                    ev.preventDefault();
                    accept();
                    return;
                }
                if (ev.key === 'Backspace' || ev.key === 'Delete') {
                    // Never let the ghost grow back over an erasure: a
                    // completion that reappears while you delete it cannot be
                    // deleted. It returns on the next real keystroke.
                    setGhost('');
                    erased = true;
                }
            });
            var erased = false;
            inp.addEventListener('input', function () {
                resize();
                if (erased) { erased = false; setGhost(''); return; }
                recompute();
            });

            var done = false;
            function finish(save) {
                if (done) return;
                done = true;
                editing = false;
                var v = sanitise(inp.value);
                if (!save || !v || v === initial) { render(); return; }
                if (initial) commit(function (next) { return opts.onRename(initial, next); }, v);
                else commit(opts.onAdd, v);
            }
            inp.addEventListener('blur', function () { finish(true); });
            inp.addEventListener('click', function (ev) { ev.stopPropagation(); });
        }

        // commit — run a handler and redraw from ITS answer, never from what we
        // asked for: the server sanitises, dedupes and caps, and a UI that
        // painted its own request would show a refused tag as accepted.
        function commit(handler, arg) {
            if (!handler || busy) { render(); return; }
            busy = true;
            el.classList.add('is-busy');
            var p;
            try { p = handler(arg); } catch (e) { p = Promise.reject(e); }
            Promise.resolve(p).then(function (next) {
                if (Array.isArray(next)) tags = next.slice();
                invalidate();
            }, function (e) {
                toast((e && e.message) ? e.message : 'Could not change the tag', 'error');
            }).then(function () {
                busy = false;
                el.classList.remove('is-busy');
                render();
            });
        }

        var ctl = {
            retire: function () { retired = true; },
            setTags: function (next) { tags = (next || []).slice(); render(); },
            getTags: function () { return tags.slice(); },
            refresh: render
        };
        el.__tc = ctl;
        render();
        return ctl;
    }

    window.TagChips = { mount: mount, vocab: vocab, invalidate: invalidate, sanitise: sanitise };
})();
