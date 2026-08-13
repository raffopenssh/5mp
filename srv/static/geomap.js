// ============================================================
// 5MP geology overlay — two scanned sheets, vectorized.
//
// Sudan (GRAS 2004, 1:2M) and CAR (BRGM 1964, 1:1.5M), turned into
// polygons by scripts/geomaps/ and served as vector tiles by
// srv/geomap.go. See docs/GEOLOGY.md.
//
// Vector, not raster — and that IS the feature. The whole point of
// this overlay is that a park facing a gold rush can switch on
// every unit that hosts gold in one click, then hide or isolate
// individual ones. A raster drape can do none of that: it would
// need one pre-rendered tileset per combination of ~47 classes.
//
// Three things that are load-bearing here:
//
// 1. COMMODITY AFFINITY IS AN INFERENCE, NEVER AN OCCURRENCE.
//    "Rocks of this kind host gold" is a statement about lithology,
//    not about a deposit, and nothing here counts, ranks or locates
//    one. Every surface that shows it says so — same line as the
//    mining verdict in AGENTS.md: inference from context ships,
//    fabricated evidence does not.
//
// 2. A MERGED CLASS IS LABELLED WITH ALL ITS CODES. Where the print
//    screen does not separate two units (CAR's GO/GC2, Sudan's
//    MSq/TQ) the vectorizer emits one class carrying both codes,
//    because the sheet genuinely does not say which one a patch is.
//    The UI must never pick one to show.
//
// 3. LAYER ORDER. The units go directly above the basemap raster and
//    below every vector overlay — park/AOI outlines, trajectories,
//    pins. Same rule and the same `before` anchoring as HistMap; see
//    the reattach() note there about styledata vs idle.
//
// 4. IT IS ONE LAYER, NOT TWO SHEETS. Sudan and CAR are two scans
//    that happen to be the provenance of one thing the user wants:
//    "what rock is under here?". Presenting them as two cards, two
//    legends, two opacity sliders and two toggles made the user
//    reconcile two 40-years-apart colour languages — and, at the
//    border, the same rock changed colour. So: ONE Geology switch,
//    ONE legend, and the sheet demoted to what it is, provenance,
//    named in the unit card and in the panel's footnote. The
//    per-sheet API is still here (a share link, a download and the
//    tile source are all per sheet) — it is just no longer the
//    user's unit of thought.
//
// 5. THE LEGEND IS THE INDUSTRY'S, NOT OURS. Colour = age, from the
//    ICS/CGMW International Chronostratigraphic Chart; ornament =
//    lithology, from FGDC-STD-013-2006 §37. Both are computed
//    server-side (srv/geomap_std.go) and ride in the catalogue, so
//    this file never hard-codes a colour or a hatch. Two reasons the
//    hatch is load-bearing and not decoration: a flat translucent
//    fill on a dark basemap is read as WATER (that is the bug this
//    came from — nothing else on the map is hatched), and a texture
//    survives being drawn at 20% opacity over an arbitrary basemap
//    where a hue does not.
//
//    The printed ink is not discarded: `colorMode: 'ink'` draws the
//    sheet as it was printed, which is the honest view when the
//    question is about the SCAN rather than about the rock.
// ============================================================
(function () {
    'use strict';

    const SRC = id => 'geomap-src-' + id;
    const FILL = id => 'geomap-fill-' + id;
    const LINE = id => 'geomap-line-' + id;
    // Contacts ride in the SAME source (tiles.sh builds `units` and `contacts`
    // as two layers of one tileset), so this is a second LAYER, never a second
    // source: one tile request per tile per pan, and the boundaries can never
    // be a build apart from the units they separate.
    const CONT = id => 'geomap-contact-' + id;
    const PAT = (lith, age) => 'geopat-' + lith + '-' + age;

    let sheets = null;          // id -> catalogue entry from /api/geomap
    let order = [];             // sheet ids in server order
    let gpkg = null;            // the ONE combined GeoPackage: {url, bytes, sheets}
    let metaPromise = null;
    const state = {};           // id -> {on, opacity, hidden:Set, isolate:Set|null}

    // Everything the user actually operates is GLOBAL, because the user
    // operates "geology", not "the 1964 BRGM sheet": one on/off, one opacity,
    // one colour mode, one lithology filter. Per-sheet state stays for the
    // things that really are per sheet (which classes are hidden, where the
    // tiles come from, what the download is).
    const shared = {
        colorMode: 'age',       // 'age' (ICS) | 'ink' (as printed)
        pattern: true,          // FGDC ornament on/off
        // Opacity ADAPTS to the basemap by default. The right value is not a
        // constant: 0.42 reads well over the dark basemap and all but
        // disappears over satellite imagery, which is exactly how a working
        // geology layer gets reported as "not showing anything". Auto re-picks
        // whenever the basemap changes; moving the slider is what turns it
        // off, so a hand-set value is never silently overwritten.
        opacityAuto: true,
        opacity: 0.52,
        liths: new Set(),       // lithology filter; empty = all
        // HOW STRONG an affinity has to be to count.
        //
        // A commodity chip answers "what ground can host gold", and the
        // catalogue grades that 1-3: 3 = the classic host, 2 = a plausible
        // one, 1 = a weak or derived association (placer downstream of a lode,
        // a quartzite inside the belt). Selecting gold on Sudan+CAR+Tanzania
        // lights 36 units, and 21 of them are weight 1 — the map then reads as
        // "gold is everywhere", which is the opposite of what the reader
        // asked. So the selection carries a floor: 1 = any affinity (the
        // default, nothing dropped), 3 = classic hosts only.
        //
        // It is a property of the SELECTION, not of a sheet: an affinity grade
        // means the same thing on every sheet by construction (legend.py).
        minWeight: 1,
        // Ages the reader has switched OFF from the key. Stored as the
        // EXCLUDED set, not as the kept one, because the default is "all" and
        // a kept-set would have to be rebuilt every time a sheet is added or
        // the view moves over a period nobody has an opinion about. Empty =
        // nothing hidden, which is also what a share link omits.
        agesOff: new Set(),
        /* ── Contacts: where two units MEET ────────────────────────────
         *
         * OFF by default, and that is not timidity. A geological sheet is
         * mostly boundaries — 882 unit pairs over three sheets — so drawing
         * every one of them turns the drape into a net and buries the fills it
         * is supposed to be a legend for. The layer earns its place by
         * answering a question ("where does granite meet greenstone"), so it
         * arrives when the reader asks it.
         *
         * `contactsGraded` is the one filter that is on from the start: a
         * contact with no commodity affinity is a line between two rocks that
         * the model has nothing to say about, and 501 of those under the 381
         * that mean something is the same net by another route. Switching it
         * off shows every mapped junction, and the strip says which it is.
         */
        contacts: false,
        contactsGraded: true,
        /* ── WHAT THE READER PICKED, NOT WHAT THEY NARROWED TO ─────────
         *
         * Junctions picked in the table: "intrusive|volcanic" (one cell) or
         * "intrusive" (one header = every junction that rock takes part in).
         * Stored as LITHOLOGY, not as unit codes, because that is what the
         * model is keyed on and what survives a re-vectorize that merges two
         * units.
         *
         * A SET, not one value. Picking was a radio — a second cell replaced
         * the first — so the table could only ever ask about one junction,
         * and the gesture that reads as "and this one too" silently threw the
         * first away. Empty = every junction, which is also what a share link
         * omits.
         */
        contactPairs: new Set(),
        /* Cells picked in the rock matrix, as "commodity|age". Same story:
         * a cell used to REPLACE the commodity selection and solo its period,
         * so every gesture in the table narrowed and the table then described
         * a map with one column left in it. Now a cell is a pick, the map
         * draws the UNION of the picks, and the table keeps its full width so
         * the next pick is still reachable. Empty = no cell narrowing. */
        picks: new Set(),
        // Whether the Advanced block is open. A setting, so it travels in the
        // share link with the rest — "look at this" should reproduce the panel
        // the sender was reading, not just the map.
        advOpen: false
    };

    // Per basemap, the opacity at which a hatched drape is legible without
    // burying what is under it. Satellite imagery is bright, busy and
    // mid-toned, so it needs distinctly more ink than the near-black basemap;
    // both were picked by looking at the map, not by halving a number.
    function autoOpacity() {
        const b = (typeof currentBasemap === 'string') ? currentBasemap : 'dark';
        return b.indexOf('satellite') === 0 ? 0.72 : 0.52;
    }

    function api(path) {
        return path + (path.indexOf('?') >= 0 ? '&' : '?') +
            'pwd=' + encodeURIComponent(getPwd());
    }

    function st(id) {
        if (!state[id]) state[id] = {
            on: false, hidden: new Set(), isolate: null,
            // Commodity chips are a MULTI-select: the isolation is the UNION
            // of the host sets of every selected commodity. "gold" alone and
            // "gold + copper" are both ordinary questions, and a radio button
            // makes the second one impossible without hand-picking codes out
            // of a 47-row legend.
            commodities: new Set()
        };
        return state[id];
    }

    function fetchMeta() {
        if (!metaPromise) {
            metaPromise = fetch(api('/api/geomap'))
                .then(r => r.json())
                .then(j => {
                    sheets = {};
                    order = [];
                    (j.sheets || []).forEach(s => { sheets[s.id] = s; order.push(s.id); });
                    // ONE GeoPackage for every sheet — a property of the
                    // catalogue, not of a sheet, because the map is one layer
                    // and the data behind it must not arrive as a per-country
                    // jigsaw for the user to reassemble.
                    gpkg = j.geopackage
                        ? { url: j.geopackage, bytes: j.geopackage_bytes || 0,
                            sheets: j.geopackage_sheets || [] }
                        : null;
                    return sheets;
                })
                .catch(() => { sheets = {}; order = []; gpkg = null; return sheets; });
        }
        return metaPromise;
    }

    // Classes of a sheet, whether or not its tiles are installed: the
    // catalogue is served even when only vectorize.py has run, so the panel
    // can name what is missing instead of showing an empty list.
    function classesOf(id) {
        const s = sheets && sheets[id];
        return (s && s.catalogue && s.catalogue.classes) || [];
    }

    // The shared legend the server computed (ICS ages, FGDC lithologies).
    // From the first sheet that carries it — it is one legend by construction,
    // so taking it from a sheet is only a transport detail.
    function stdLegend() {
        for (const id of order) {
            const s = sheets && sheets[id];
            if (s && s.catalogue && s.catalogue.std) return s.catalogue.std;
        }
        return { ages: [], lithology: [] };
    }

    function ageMeta(key) {
        return (stdLegend().ages || []).find(a => a.key === key) ||
               { key: key, label: key, color: '#BDBDBD', rank: 99 };
    }

    /* ── The contact model, indexed once ───────────────────────────────
     *
     * The server ships ~26 rules keyed by LITHOLOGY pair, once, in the shared
     * legend — not per contact, because "intrusive against carbonate is the
     * skarn setting" is one sentence whether it applies to two junctions or
     * two hundred. Here it becomes a plain object keyed "lithA|lithB", built
     * on first use and thrown away when the catalogue changes.
     *
     * Everything downstream (the line paint, the tip, the matrix's contact
     * mode) reads grades out of this map. It matters that it is O(1): the
     * paint expression below is evaluated per feature per frame, so a scan
     * over the rules there would be a scan on the render thread.
     */
    let contactIdx = null;
    function contactRules() {
        if (contactIdx) return contactIdx;
        contactIdx = {};
        (stdLegend().contact_rules || []).forEach(r => { contactIdx[r.pair] = r; });
        return contactIdx;
    }

    function lithPairKey(a, b) {
        a = a || 'mixed'; b = b || 'mixed';
        return a < b ? a + '|' + b : b + '|' + a;
    }

    /** What the junction of two CLASSES can host: the rule for their two
     *  lithologies, or null where the model says nothing. */
    function contactRuleFor(sheet, codeA, codeB) {
        const a = classOf(sheet, codeA), b = classOf(sheet, codeB);
        if (!a || !b) return null;
        return contactRules()[lithPairKey(a.lith, b.lith)] || null;
    }

    /** Every contact pair of a sheet, each with its rule resolved. Cached per
     *  sheet: the pair list is static for a build, and the matrix asks for it
     *  on every rebuild. */
    const contactCache = {};
    function contactsOf(id) {
        if (contactCache[id]) return contactCache[id];
        const s = sheets && sheets[id];
        const raw = (s && s.contacts && s.contacts.pairs) || [];
        contactCache[id] = raw.map(p => {
            const rule = contactRuleFor(id, p.a, p.b);
            const ca = classOf(id, p.a), cb = classOf(id, p.b);
            return {
                sheet: id, a: p.a, b: p.b, km: p.km,
                pair: p.a + '|' + p.b,
                lithA: (ca && ca.lith) || 'mixed', lithB: (cb && cb.lith) || 'mixed',
                ageA: (ca && ca.age) || 'unknown', ageB: (cb && cb.age) || 'unknown',
                nameA: (ca && ca.name) || p.a, nameB: (cb && cb.name) || p.b,
                rule: rule,
                best: rule ? rule.best : 0,
                commodities: rule ? rule.affinity.map(x => x.commodity) : []
            };
        });
        return contactCache[id];
    }

    /** Contacts across every installed sheet — the user's unit of thought,
     *  exactly like allClasses(). */
    function allContacts(installed) {
        const out = [];
        order.forEach(id => {
            // ON, not merely installed. ?geomap=car with Sudan built but off
            // otherwise reports the junctions of a sheet that is not on the
            // map — a count describing a map nobody is looking at.
            //
            // `installed` asks the other question ("does this build HAVE such
            // a junction"), which is what validating a share link needs: the
            // link is parsed before the sheets it names are switched on, so
            // asking the drawn set there rejects every junction as unknown.
            if (!(sheets[id] || {}).available) return;
            if (!installed && !st(id).on) return;
            contactsOf(id).forEach(c => out.push(c));
        });
        return out;
    }

    /** Does this sheet have a contact layer at all? */
    function hasContacts(id) {
        const s = sheets && sheets[id];
        return !!(s && s.contacts && s.contacts.n_contacts);
    }

    /* Which contact pairs are drawn right now.
     *
     * Same discipline as visibleCodes(): the answer is a LIST OF PAIR KEYS
     * that goes into one `in` filter, because a filter with a function in it
     * runs per feature per frame. A commodity selection narrows contacts the
     * same way it narrows units — the question "where can gold be" does not
     * change its meaning because the answer is a line rather than an area. */
    function visiblePairs(id) {
        const sel = selectedCommodities();
        // The contact layer carries its own floor, and it is 2 rather than 1.
        //
        // Measured, not guessed: at CAR's country zoom every graded junction
        // (97 of 113) draws as a net of orange over the whole sheet and the
        // fills stop being readable at all — the layer buries the map it is
        // annotating. "Likely and up" is 71 lines and reads as a map; the
        // classic-only 20 read as a recommendation. A weight-1 contact is a
        // derived association (alluvium off a granite), which is the least
        // worth drawing over a country and the first thing to drop.
        //
        // It is a FLOOR, not a cap: raising the grade floor raises this with
        // it, and switching "graded only" off shows every mapped junction
        // including the ungraded ones. The strip says which of those it is —
        // a subset that does not announce itself is the failure this app
        // keeps paying for.
        const min = Math.max(shared.minWeight, shared.contactsGraded ? 2 : 0);
        return contactsOf(id).filter(c => {
            // The junctions picked in the table are a filter on TOP of the
            // rest, never instead of it: "granite against greenstone, for
            // gold, classic only" is one question and each clause narrows.
            if (!junctionMatches(c)) return false;
            if (!shared.contactsGraded) return true;
            if (!c.rule) return false;
            if (!sel.size) return c.best >= min;
            return c.rule.affinity.some(a => sel.has(a.commodity) && a.weight >= min);
        }).map(c => c.pair);
    }

    /* ── The junctions picked in the table ───────────────────────────────
     *
     * Each entry of `shared.contactPairs` is either one cell of the triangle
     * ("a|b", both lithologies) or one of its headers ("a", every junction
     * that lithology takes part in). Two shapes, because a reader who taps
     * "intrusive" means "anywhere granite meets anything" and that question
     * has no cell.
     *
     * The picks are ORed: the reader is building the map out of cells, so a
     * contact matched by ANY pick is drawn. Empty = every junction.
     *
     * It is stored as LITHOLOGY, never as unit codes: the model is keyed that
     * way, and a re-vectorize that merges two units must not silently empty a
     * share link. */
    function junctionMatches(c) {
        const want = shared.contactPairs;
        if (!want.size) return true;
        const cell = lithPairKey(c.lithA, c.lithB);
        for (const w of want) {
            if (w.indexOf('|') >= 0) { if (cell === w) return true; }
            else if (c.lithA === w || c.lithB === w) return true;
        }
        return false;
    }

    /* ── The junction table's own data ──────────────────────────────────
     *
     * One row per (lithology, lithology) junction that the installed sheets
     * ACTUALLY contain, aggregated across them — a junction is a statement
     * about two rock types, so "intrusive against volcanic" is one row whether
     * it occurs on one sheet or three. Never the full 10x10 cross product: a
     * cell for a junction no sheet has is a claim that it exists and is barren.
     *
     * Keyed and returned by the same normalised pair key the rules use, so the
     * table, the filter, the paint and the share link cannot disagree about
     * what "intrusive|volcanic" means.
     */
    function junctionIndex(installed) {
        const out = {};
        allContacts(installed).forEach(c => {
            const k = lithPairKey(c.lithA, c.lithB);
            const row = out[k] || (out[k] = {
                pair: k, a: k.split('|')[0], b: k.split('|')[1],
                n: 0, km: 0, best: 0, affinity: (c.rule && c.rule.affinity) || []
            });
            row.n++;
            row.km += c.km || 0;
            row.best = Math.max(row.best, c.best || 0);
        });
        return out;
    }

    /** Every class of every sheet, as one list — the user's unit of thought. */
    function allClasses() {
        const out = [];
        order.forEach(id => classesOf(id).forEach(c => out.push(Object.assign({ sheet: id }, c))));
        return out;
    }

    /** One class by (sheet, code) — what a tile feature indexes into. */
    function classOf(id, code) {
        return classesOf(id).find(c => c.code === code) || null;
    }

    function classColor(c) {
        return shared.colorMode === 'ink' ? (c.color || '#888888')
                                          : (c.ics_color || ageMeta(c.age).color);
    }

    // A contact is drawn in a DARKENED version of the unit, never in the unit's
    // own colour. These polygons are traced off a scan, so their boundaries
    // carry every wiggle of the print: 46 classes outlined at full saturation
    // is a net of bright magenta over an entire country, and it out-shouts the
    // fills it is supposed to bound. Same ink the ornament uses, so a contact
    // and its hatch read as one drawing.
    function inkColor(c) {
        const h = String(classColor(c)).replace('#', '');
        const v = parseInt(h.length === 3 ? h.split('').map(x => x + x).join('') : h, 16);
        if (isNaN(v)) return '#555555';
        const r = (v >> 16) & 255, g = (v >> 8) & 255, b = v & 255;
        const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        const k = lum > 0.55 ? 0.45 : 0.62;
        const hex = n => Math.round(n * (1 - k)).toString(16).padStart(2, '0');
        return '#' + hex(r) + hex(g) + hex(b);
    }

    // Which classes are drawn right now. `isolate` (a commodity selection, or
    // a single class pinned from the legend) wins over `hidden`, so clicking
    // "gold" never has to guess what the user hid earlier. The lithology
    // filter is ANDed on top: it is a legend-wide question ("show me the
    // intrusives") and must not silently discard a commodity selection.
    function visibleCodes(id) {
        const s = st(id);
        const cls = classesOf(id);
        let keep = cls;
        // An isolation that is EMPTY means "this sheet has nothing that
        // answers", not "no isolation". They used to be the same value (null),
        // so raising the strength floor to `classic` made CAR — which has no
        // weight-3 gold host — fall back to drawing all 17 of its units, i.e.
        // the filter that matched nothing rendered as the whole sheet. A
        // sheet with no answer draws nothing; setMinWeight() separately
        // refuses a floor that would empty EVERY sheet, so the map can never
        // go blank without saying so.
        if (s.isolate) keep = keep.filter(c => s.isolate.has(c.code));
        else keep = keep.filter(c => !s.hidden.has(c.code));
        if (shared.liths.size) keep = keep.filter(c => shared.liths.has(c.lith));
        if (shared.agesOff.size) keep = keep.filter(c => !shared.agesOff.has(c.age));
        return keep.map(c => c.code);
    }

    // A data-driven ramp built from the catalogue: the polygons carry a
    // `code`, and the catalogue says what colour and what ornament that code
    // gets. One `match` expression rather than a layer per class keeps this to
    // two layers per sheet regardless of how many units the sheet has.
    function colorExpr(id) {
        const e = ['match', ['get', 'code']];
        classesOf(id).forEach(c => { e.push(c.code, classColor(c)); });
        e.push('#888888');
        return e;
    }

    function lineColorExpr(id) {
        const e = ['match', ['get', 'code']];
        classesOf(id).forEach(c => { e.push(c.code, inkColor(c)); });
        e.push('#555555');
        return e;
    }

    // The FGDC ornament, as a fill-pattern per class.
    //
    // MapLibre needs each pattern registered as an IMAGE before a layer can
    // name it, and an unregistered name renders as NOTHING — an invisible
    // unit, which is the one failure this overlay must never have (a missing
    // formation reads as "no data here"). So: register every pattern the
    // sheet can ask for, up front and synchronously, and fall back to the flat
    // fill if the pattern module did not load at all.
    //
    // Keyed on (lithology, colour) because the ink is derived from the fill:
    // one tile serves every class that shares both, so 46 classes cost ~12
    // images, not 46.
    function ensurePatterns(id) {
        if (!shared.pattern || !window.GeoPatterns || !map) return false;
        let ok = false;
        classesOf(id).forEach(c => {
            const name = patternName(c);
            if (map.hasImage && map.hasImage(name)) { ok = true; return; }
            try {
                map.addImage(name, window.GeoPatterns.tile(c.lith || 'mixed', classColor(c), 1),
                             { pixelRatio: 2 });
                ok = true;
            } catch (err) { /* already added by a concurrent add() */ ok = true; }
        });
        return ok;
    }

    function patternName(c) {
        return PAT(c.lith || 'mixed', String(classColor(c)).replace('#', ''));
    }

    function patternExpr(id) {
        const e = ['match', ['get', 'code']];
        classesOf(id).forEach(c => { e.push(c.code, patternName(c)); });
        e.push(patternName({ lith: 'mixed', color: '#888888', ics_color: '#888888' }));
        return e;
    }

    // Applying the paint is one function because the two modes must stay
    // mutually exclusive: a fill-pattern silently WINS over fill-color in
    // MapLibre, so leaving a stale pattern set is how "as printed" quietly
    // keeps showing the age colours.
    function paintFill(id) {
        const patterned = ensurePatterns(id);
        if (patterned) {
            map.setPaintProperty(FILL(id), 'fill-pattern', patternExpr(id));
            // No boost: the tile already weights ink over background, so the
            // slider means the same thing in both modes and turning geology
            // down actually turns it down.
            map.setPaintProperty(FILL(id), 'fill-opacity', shared.opacity);
        } else {
            map.setPaintProperty(FILL(id), 'fill-pattern', undefined);
            map.setPaintProperty(FILL(id), 'fill-color', colorExpr(id));
            map.setPaintProperty(FILL(id), 'fill-opacity', shared.opacity);
        }
        // The contact hairline is a boundary, not a highlight: at full colour
        // it out-shouts the fill it is bounding (46 saturated outlines is a
        // net of pink over a whole country). Half the fill's opacity, so it
        // separates units without competing with them.
        map.setPaintProperty(LINE(id), 'line-color', lineColorExpr(id));
        map.setPaintProperty(LINE(id), 'line-opacity', Math.min(0.75, shared.opacity * 1.1));
    }

    function filterExpr(id) {
        return ['in', ['get', 'code'], ['literal', visibleCodes(id)]];
    }

    /* ── Drawing a contact ──────────────────────────────────────
     *
     * A contact is NOT another unit outline. The units already have a
     * hairline in their own darkened ink (LINE), and a second line in the same
     * language on top of it would be invisible where it matters and confusing
     * everywhere else. So a contact is drawn as what it is: a thing worth
     * walking. Warm, brighter than the drape, and WIDER WITH ITS GRADE — the
     * classic setting is the fat line, the weak association is the thin one,
     * which is the same 1-3 scale as everywhere else expressed in the only
     * dimension a line has.
     *
     * The colour does not encode the commodity. Eight commodities is eight
     * hues nobody can hold, they would collide with the ICS age colours
     * underneath, and a junction routinely hosts two — so grade is the ink
     * and the identity is in the tip and the matrix.
     */
    function contactFilterExpr(id) {
        return ['in', ['get', 'pair'], ['literal', visiblePairs(id)]];
    }

    // pair -> grade, as a `match` expression: one lookup per feature on the
    // render thread, rather than a rule scan.
    function contactGradeExpr(id, out1, out2, out3, fallback) {
        const e = ['match', ['get', 'pair']];
        const byGrade = { 1: [], 2: [], 3: [] };
        contactsOf(id).forEach(c => { if (c.best) byGrade[c.best].push(c.pair); });
        const outs = { 1: out1, 2: out2, 3: out3 };
        let any = false;
        [3, 2, 1].forEach(g => {
            if (!byGrade[g].length) return;
            any = true;
            e.push(byGrade[g], outs[g]);
        });
        if (!any) return fallback;
        e.push(fallback);
        return e;
    }

    function paintContacts(id) {
        if (!map.getLayer(CONT(id))) return;
        map.setFilter(CONT(id), contactFilterExpr(id));
        // Width by grade, and by zoom: at z3 a 3 px line over a continent is a
        // scribble, at z9 a 1 px line over a 50 km junction is invisible.
        map.setPaintProperty(CONT(id), 'line-width', [
            'interpolate', ['linear'], ['zoom'],
            3, contactGradeExpr(id, 0.6, 1.0, 1.6, 0.6),
            9, contactGradeExpr(id, 1.6, 2.6, 4.0, 1.6)
        ]);
        map.setPaintProperty(CONT(id), 'line-color',
            contactGradeExpr(id, '#fcd34d', '#fbbf24', '#f59e0b', '#9ca3af'));
        // Opaque enough to read over a 72% satellite drape, and it does NOT
        // follow the drape's opacity slider: turning the rock map down is how
        // a reader looks at the ground under it, and the contact is the thing
        // they turned it down to see.
        map.setPaintProperty(CONT(id), 'line-opacity', 0.9);
    }

    // See the HistMap note: anchor above the basemap raster and below the
    // first vector layer, so anything added later (a pin, a trajectory) lands
    // on top for free. 'background' is skipped — it is the paper the basemap
    // sits on, and inserting before it would hide the overlay entirely.
    function firstOverlayLayer() {
        const style = map.getStyle();
        if (!style || !style.layers) return undefined;
        const l = style.layers.find(x =>
            x.id.indexOf('geomap-') !== 0 && x.type !== 'raster' && x.type !== 'background');
        return l ? l.id : undefined;
    }

    // A SHEET THAT CANNOT BE ADDED YET IS UNFINISHED, NOT DONE.
    //
    // This used to `return` when the style was mid-update, which turned "try
    // again in a moment" into "never". It bit exactly where two sheets are
    // switched on at once (?geomap=sudan,car): both queued on the same `idle`,
    // the first one's addSource/addLayer put the style back into a loading
    // state, and the second silently evaporated. The map then looked right --
    // there WAS geology on screen -- while half of it was missing, and a click
    // over the missing half fell through to the park underneath.
    //
    // So: no answer means retry on the next idle, once, and only for a sheet
    // we know is available.
    function add(id) {
        const s = sheets && sheets[id];
        if (!s || !s.available || !map) return;
        if (!map.isStyleLoaded()) {
            if (!pendingAdd.has(id)) {
                pendingAdd.add(id);
                map.once('idle', () => { pendingAdd.delete(id); if (st(id).on) add(id); });
            }
            return;
        }
        pendingAdd.delete(id);
        const o = st(id);
        if (!map.getSource(SRC(id))) {
            map.addSource(SRC(id), {
                type: 'vector',
                tiles: [window.location.origin + api(s.tiles)],
                minzoom: s.minzoom || 0,
                maxzoom: s.maxzoom || 10,
                bounds: s.bounds || undefined,
                attribution: (s.catalogue && s.catalogue.publisher)
                    ? s.catalogue.publisher + ', ' + s.catalogue.year : ''
            });
        }
        const before = firstOverlayLayer();
        if (!map.getLayer(FILL(id))) {
            map.addLayer({
                id: FILL(id), type: 'fill', source: SRC(id), 'source-layer': 'units',
                filter: filterExpr(id),
                paint: { 'fill-color': colorExpr(id), 'fill-opacity': shared.opacity }
            }, before);
        }
        if (!map.getLayer(LINE(id))) {
            // A hairline in the unit's own colour, darkened by opacity rather
            // than a shared grey: at continental zoom 47 classes of flat fill
            // with no edge read as a single blotch.
            map.addLayer({
                id: LINE(id), type: 'line', source: SRC(id), 'source-layer': 'units',
                filter: filterExpr(id),
                paint: {
                    'line-color': lineColorExpr(id), 'line-width': 0.5,
                    'line-opacity': Math.min(0.75, shared.opacity * 1.1)
                }
            }, before);
        }
        // The contact layer is added only when asked for, and REMOVED when
        // switched off rather than filtered to nothing: an empty layer still
        // costs a filter evaluation per tile per frame, and a sheet is mostly
        // boundaries.
        syncContactLayer(id, before);
        paintFill(id);
        bindTip(id);
    }

    /* Add or drop the contact layer for a sheet, to match shared.contacts.
     * Above the unit line work (a junction must not be hidden by the hairline
     * of the unit it bounds) and still below `before`, so pins, trajectories
     * and park outlines stay on top — same rule as everything else here. */
    function syncContactLayer(id, before) {
        // THE SOURCE MAY NOT BE THERE YET. A sheet is added on `idle` (see
        // add()), so a gesture that switches contacts on while a sheet is
        // still queueing would addLayer against a source that does not exist —
        // MapLibre throws, the layer never lands, and the map is quietly one
        // sheet of contacts short. Not an error condition: add() calls this
        // again with the source in place, so the right answer is to wait.
        if (!map.getSource(SRC(id))) return;
        const want = shared.contacts && hasContacts(id);
        const have = !!map.getLayer(CONT(id));
        if (want && !have) {
            map.addLayer({
                id: CONT(id), type: 'line', source: SRC(id), 'source-layer': 'contacts',
                layout: { 'line-cap': 'round', 'line-join': 'round' },
                filter: contactFilterExpr(id),
                paint: { 'line-color': '#fbbf24', 'line-width': 1.2, 'line-opacity': 0.9 }
            }, before || firstOverlayLayer());
            bindContactTip(id);
        } else if (!want && have) {
            map.removeLayer(CONT(id));
            if (window.MapTip) window.MapTip.unregister(CONT(id));
            contactBound[id] = false;
        }
        if (map.getLayer(CONT(id))) paintContacts(id);
    }

    function remove(id) {
        pendingAdd.delete(id);   // a queued re-add must not resurrect a sheet just switched off
        [FILL(id), LINE(id), CONT(id)].forEach(l => { if (map.getLayer(l)) map.removeLayer(l); });
        if (map.getSource(SRC(id))) map.removeSource(SRC(id));
        // Switching the sheet off must also take its tip away, or a click keeps
        // being swallowed by a layer that is no longer on the map.
        if (window.MapTip) { window.MapTip.unregister(FILL(id)); window.MapTip.unregister(CONT(id)); }
        bound[id] = false;
        contactBound[id] = false;
    }

    function refresh(id) {
        if (!map.getLayer(FILL(id))) return;
        [FILL(id), LINE(id)].forEach(l => map.setFilter(l, filterExpr(id)));
        syncContactLayer(id);
        paintFill(id);
    }

    /** Re-apply everything shared to every sheet on the map. */
    function refreshAll() {
        order.forEach(id => { if (st(id).on) refresh(id); });
        if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
    }

    // Hover a unit and it says what it is — an ordinary hover tip, the same as
    // a fire trajectory, a road or a river. It used to be `clickOnly`, on the
    // theory that a country-sized drape has no "off it" to move to, and that
    // was over-thought: MapTip only ever shows ONE tip, so a geology answer
    // simply loses to anything more specific under the cursor, and where there
    // is nothing more specific, naming the rock is exactly what the user wants.
    // (`clickOnly` is still right for the AOI polygon, which owns a click.)
    //
    // `priority: -30` still says geology is the backdrop *under* everything:
    // a trajectory, a settlement or an area answers first, on hover and on
    // click alike.
    //
    // It used to open its own maplibregl.Popup, which is why a tap could
    // produce three answers at once (geology popup + AOI popup + AOI map tip):
    // its click never went through the shared arbitration. One tip, one owner.
    const bound = {};
    const contactBound = {};
    // Sheets waiting for the style to settle before they can be added; see add().
    const pendingAdd = new Set();
    function tipHTML(id, p) {
            const cat = (sheets[id] && sheets[id].catalogue) || {};
            let aff = [];
            try { aff = JSON.parse(p.affinity || '[]'); } catch (err) { aff = []; }
            const codes = String(p.code || '').split('/');
            const merged = codes.length > 1;
            // The TILE carries code/name/group/ink; age and lithology live in
            // the catalogue, keyed by code. Joining here (rather than baking
            // them into the tiles) is what lets the legend change without
            // invalidating 3 GB of vector tiles.
            const cls = classOf(id, p.code) || {};
            const age = ageMeta(cls.age);
            const lith = (stdLegend().lithology || []).find(l => l.key === cls.lith);
            // "As printed" has to be the sheet's OWN words, or the label is a
            // lie about provenance. `group` is what the age scan reads and it
            // is sometimes derived: a vector sheet's chronostratigraphy has its
            // sub-era codes stripped ("Neoproterozoic (NP2-3) - Cambrian(?)"
            // loses a question mark the survey meant), and its Cenozoic units
            // state an age only as a span of Ma, from which we READ a period
            // name. So prefer the verbatim string the catalogue carries, then
            // the sheet's own numbers, and fall back to `group` for the
            // scanned sheets, where `group` IS the printed words.
            const printed = cls.chronostrat || cls.age_strat || p.group || '';
            // The survey's own rock description, where the sheet has one. It is
            // far more specific than the FGDC family the ornament encodes
            // ("Peridotite, dunite, lherzolite, gabbro, norite, anorthosite" vs
            // "Ultramafic / ophiolite"), and the pattern is a legend key, not a
            // description.
            const rock = cls.lithology || '';
            // 16 px, comfortably over the ornament floor: this is the one
            // swatch that has room to be a proper key, and it is where the
            // reader learns what the hatch on the polygon means.
            const swatch = window.GeoPatterns
                ? window.GeoPatterns.swatchStyle(cls.lith || 'mixed', classColor(cls), 16)
                : `background:${escapeHtml(classColor(cls))};`;
            return `
                <div style="font-family:inherit;max-width:260px;">
                    <div style="display:flex;align-items:center;gap:8px;">
                        <span style="width:16px;height:16px;border-radius:3px;flex:none;${swatch}border:1px solid rgba(0,0,0,0.45);"></span>
                        <b style="color:#fff;font-size:13px;">${escapeHtml(p.code || '')}</b>
                    </div>
                    <div style="color:#ddd;font-size:12px;margin-top:6px;line-height:1.45;">${escapeHtml(p.name || '')}</div>
                    ${rock ? `<div style="color:#9ca3af;font-size:11px;margin-top:4px;line-height:1.45;">${escapeHtml(rock)}</div>` : ''}
                    <div style="color:#9ca3af;font-size:11px;margin-top:6px;">
                        ${escapeHtml(age.label)}${cls.age_mixed ? ' (undifferentiated)' : ''}
                        ${lith ? ' &middot; ' + escapeHtml(lith.label) : ''}</div>
                    <div style="color:#777;font-size:10px;margin-top:3px;">
                        as printed: ${escapeHtml(printed)} &middot; ${escapeHtml(cat.short || id)}${cat.year ? ', ' + cat.year : ''}</div>
                    ${merged ? `<div style="color:#f59e0b;font-size:11px;margin-top:6px;line-height:1.4;">
                        Printed as ${codes.length} units (${escapeHtml(codes.join(', '))}) in inks this sheet's
                        print screen does not separate &mdash; which one a given patch is, the map does not say.</div>` : ''}
                    ${aff.length ? `<div style="margin-top:8px;border-top:1px solid rgba(255,255,255,0.08);padding-top:6px;">
                        <div style="color:#aaa;font-size:11px;font-weight:600;">Commodities this rock type can host</div>
                        ${aff.map(a => `<div style="color:#ccc;font-size:11px;margin-top:4px;line-height:1.4;">
                            ${'&#9679;'.repeat(a.weight)} <b style="color:#fff;">${escapeHtml(a.commodity)}</b> &mdash; ${escapeHtml(a.why)}</div>`).join('')}
                        <div style="color:#777;font-size:10px;margin-top:6px;line-height:1.4;">
                            An inference from lithology, not a record of any deposit. Nothing here
                            counts, ranks or locates an occurrence.</div>
                    </div>` : ''}
                </div>`;
    }

    /* ── A contact's own tip ─────────────────────────────────────
     *
     * A hairline is a hard target, so the tip has to be worth hitting: it
     * names BOTH rocks (that is the whole content of a contact — "granite
     * against greenstone"), the setting it implies, and the same 1-3 dots the
     * unit tip, the matrix and the key already use.
     *
     * priority -25: above the unit drape (-30), because a reader whose cursor
     * is on a 2 px line meant the line and not the country-sized polygon under
     * it; still below every real feature. `peers: false` for the same reason
     * the drape sets it — a boundary is a legend, not a pile-up.
     */
    function contactTipHTML(id, p) {
        const c = (contactsOf(id) || []).find(x => x.pair === p.pair);
        const cat = (sheets[id] && sheets[id].catalogue) || {};
        const sw = cls => window.GeoPatterns
            ? window.GeoPatterns.swatchStyle(cls.lith || 'mixed', classColor(cls), 16)
            : `background:${escapeHtml(classColor(cls))};`;
        const ca = classOf(id, (c && c.a) || p.code_a) || {};
        const cb = classOf(id, (c && c.b) || p.code_b) || {};
        const side = (cls, code) => `
            <div style="display:flex;align-items:center;gap:7px;margin-top:5px;">
                <span style="width:15px;height:15px;border-radius:3px;flex:none;${sw(cls)}border:1px solid rgba(0,0,0,0.45);"></span>
                <span style="color:#fff;font-size:12px;">${escapeHtml(code || '')}</span>
                <span style="color:#9ca3af;font-size:11px;">${escapeHtml(ageMeta(cls.age).label)}</span>
            </div>
            <div style="color:#bbb;font-size:11px;margin-left:22px;line-height:1.4;">${escapeHtml(cls.name || '')}</div>`;
        const km = (c && c.km) || Number(p.km) || 0;
        const aff = (c && c.rule && c.rule.affinity) || [];
        return `
            <div style="font-family:inherit;max-width:280px;">
                <div style="color:#fbbf24;font-size:11px;font-weight:600;letter-spacing:0.04em;">
                    CONTACT &middot; ${km >= 10 ? Math.round(km) : km.toFixed(1)} km</div>
                ${side(ca, (c && c.a) || p.code_a)}
                <div style="color:#666;font-size:10px;margin:3px 0 0 22px;">against</div>
                ${side(cb, (c && c.b) || p.code_b)}
                ${aff.length ? `<div style="margin-top:8px;border-top:1px solid rgba(255,255,255,0.08);padding-top:6px;">
                    <div style="color:#aaa;font-size:11px;font-weight:600;">What this junction can host</div>
                    ${aff.map(a => `<div style="color:#ccc;font-size:11px;margin-top:4px;line-height:1.4;">
                        ${'&#9679;'.repeat(a.weight)} <b style="color:#fff;">${escapeHtml(a.commodity.replace(/_/g, ' '))}</b>
                        &mdash; ${escapeHtml(a.why)}</div>`).join('')}
                </div>` : `<div style="color:#777;font-size:11px;margin-top:8px;line-height:1.4;">
                    These two rock types in contact are not a setting this model grades.
                    That is a gap in the model, not evidence of absence.</div>`}
                <div style="color:#777;font-size:10px;margin-top:6px;line-height:1.4;">
                    An inference from the two rock types either side, not a record of any
                    deposit &mdash; nothing here counts, ranks or locates an occurrence.
                    ${escapeHtml(cat.short || id)}${cat.year ? ', ' + cat.year : ''}</div>
            </div>`;
    }

    function bindContactTip(id) {
        if (contactBound[id] || !window.MapTip) return;
        contactBound[id] = true;
        window.MapTip.register(CONT(id), {
            priority: -25, peers: false,
            tabLabel: 'Contact', tabColor: '#fbbf24',
            html: p => contactTipHTML(id, p)
        });
    }

    function bindTip(id) {
        if (bound[id]) return;
        bound[id] = true;
        if (window.MapTip) {
            window.MapTip.register(FILL(id), {
                priority: -30,
                // A drape has no "other features here" worth zooming in for —
                // its 17 units are a legend, not a pile-up. See maptip.js.
                peers: false,
                tabLabel: 'Geology',
                tabColor: '#f59e0b',
                html: p => tipHTML(id, p)
            });
            return;
        }
        // No MapTip (shouldn't happen; keeps the overlay usable if it fails to load)
        map.on('click', FILL(id), e => {
            const f = e.features && e.features[0];
            if (!f) return;
            new maplibregl.Popup({ closeButton: true, maxWidth: '280px' })
                .setLngLat(e.lngLat).setHTML(tipHTML(id, f.properties || {})).addTo(map);
        });
        map.on('mouseenter', FILL(id), () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', FILL(id), () => { map.getCanvas().style.cursor = ''; });
    }

    // Only codes this build of the sheet actually has. See restoreFromParams.
    function keep(id, codes) {
        const known = new Set(classesOf(id).map(c => c.code));
        return new Set(codes.filter(c => known.has(c)));
    }

    // Every commodity this sheet mentions -> the codes that can host it,
    // ONLY at or above the current strength floor. Everything that resolves a
    // commodity to units goes through here, so the floor cannot apply to the
    // map and not to the chip that describes it.
    function hostMap(id) {
        const all = {};
        const w = hostWeights(id);
        Object.keys(w).forEach(k => {
            const codes = Object.keys(w[k]).filter(c => w[k][c] >= shared.minWeight);
            // A commodity whose every host is below the floor keeps its KEY
            // with an empty list rather than disappearing: the chip has to be
            // offerable (and refusable) rather than silently absent, and
            // restoreFromParams tests membership against these keys.
            all[k] = codes;
        });
        return all;
    }

    // commodity -> {code: weight}, ungraded by the floor. The affinity array
    // is the graded form; `commodities` is only its key set.
    function hostWeights(id) {
        const out = {};
        classesOf(id).forEach(c => {
            let aff = c.affinity;
            if (!Array.isArray(aff)) {
                try { aff = JSON.parse(aff || '[]'); } catch (e) { aff = []; }
            }
            if (aff && aff.length) {
                aff.forEach(a => {
                    if (!a || !a.commodity) return;
                    const w = Math.max(1, Math.min(3, a.weight || 1));
                    (out[a.commodity] = out[a.commodity] || {})[c.code] =
                        Math.max(out[a.commodity][c.code] || 0, w);
                });
            } else {
                // A catalogue that ships `commodities` without the graded
                // `affinity` must not lose its commodities: an ungraded
                // affinity counts as the weakest, which is the only reading
                // that cannot overstate it.
                (c.commodities || []).forEach(k => {
                    (out[k] = out[k] || {})[c.code] = Math.max(out[k][c.code] || 0, 1);
                });
            }
        });
        return out;
    }

    /** How many units each strength floor would leave, over a commodity set. */
    function weightCounts(comms) {
        const n = { 1: 0, 2: 0, 3: 0 };
        order.forEach(id => {
            if (!(sheets && sheets[id] && sheets[id].available)) return;
            const w = hostWeights(id);
            const best = {};
            comms.forEach(k => Object.keys(w[k] || {}).forEach(c => {
                best[c] = Math.max(best[c] || 0, w[k][c]);
            }));
            Object.keys(best).forEach(c => { for (let lv = 1; lv <= best[c]; lv++) n[lv]++; });
        });
        return n;
    }

    /** Every commodity currently selected, across sheets. */
    function selectedCommodities() {
        const out = new Set();
        order.forEach(id => st(id).commodities.forEach(k => out.add(k)));
        return out;
    }

    // Which chips a given isolation lights up. Derived rather than stored, so
    // a chip stays honest after the user hand-hides one of its units from the
    // legend: a commodity reads as selected only while ALL of its host units
    // are actually drawn.
    function commoditiesCovered(id, codes) {
        const all = hostMap(id);
        return new Set(Object.keys(all).filter(
            k => all[k].length && all[k].every(c => codes.has(c))));
    }

    /* ── isolate := the UNION of everything the reader picked ────────────
     *
     * Two kinds of pick, one answer:
     *
     *   a COMMODITY row  -> every unit that can host it, at the floor
     *   a matrix CELL    -> that commodity, on that period only
     *
     * They add. That is the whole point of the pass that introduced cells as
     * picks: the table used to NARROW with every gesture (a cell replaced the
     * commodity selection and soloed its period), so a reader building "the
     * Palaeoproterozoic gold ground AND the Archaean copper ground" could
     * only ever hold the last thing they tapped, and the table shrank to one
     * column while they did it. The map is now assembled FROM the table
     * instead of being carved out of it, and the table keeps its full width
     * so the next pick is still reachable.
     *
     * An empty selection is "no isolation" (show everything), never "show
     * nothing" — an empty map is indistinguishable from a sheet with no data
     * here. An empty RESULT is a different statement ("nothing here answers")
     * and is kept as an empty Set; see visibleCodes().
     */
    function applyCommodities(id) {
        const o = st(id);
        if (!o.commodities.size && !shared.picks.size) { o.isolate = null; return; }
        const all = hostMap(id);
        const codes = new Set();
        o.commodities.forEach(k => (all[k] || []).forEach(c => codes.add(c)));
        // A cell is (commodity, age): the same host set as the row, kept to
        // the one period the reader tapped. Resolved against THIS sheet's
        // classes, so a period a sheet does not have simply contributes
        // nothing rather than emptying it.
        shared.picks.forEach(pk => {
            const i = pk.indexOf('|');
            if (i < 0) return;
            const comm = pk.slice(0, i), age = pk.slice(i + 1);
            (all[comm] || []).forEach(c => {
                const cls = classOf(id, c);
                if (cls && cls.age === age) codes.add(c);
            });
        });
        o.isolate = codes;
    }

    /** Every sheet's isolation, recomputed — picks are legend-wide. */
    function applyAllSelections() {
        order.forEach(id => applyCommodities(id));
    }

    const GeoMap = {
        ensureMeta: fetchMeta,
        sheets: () => sheets,
        order: () => order,
        geopackage: () => gpkg,
        classes: classesOf,
        allClasses: allClasses,
        classOf: classOf,
        classColor: classColor,
        std: stdLegend,
        age: ageMeta,
        shared: () => shared,
        state: st,
        isOn: id => st(id).on,
        anyOn: () => order.some(id => st(id).on),

        // ---- the one switch the user sees -------------------------------
        //
        // "Geology" is one thing. Which SHEETS answer for a given place is
        // provenance, not a choice: a user in South Sudan cannot be expected
        // to know that the 2004 GRAS sheet is the one that covers them.
        // Turning it on turns on every sheet installed; the panel still lists
        // them, and a share link still names them, so nothing is lost.
        async setAll(want, opts) {
            opts = opts || {};
            await fetchMeta();
            const avail = order.filter(id => sheets[id] && sheets[id].available);
            if (want && !avail.length) {
                if (!opts.quiet) {
                    showToast('Geology unavailable \u2014 no sheets are built on this server', 'warning');
                }
                return false;
            }
            for (const id of avail) await this.set(id, want, { quiet: true, fly: false });
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            // One offer for the whole gesture, not one per sheet (see set()).
            if (want && !opts.quiet && typeof offerZoomTo === 'function') {
                let bb = null;
                avail.forEach(id => {
                    const b = sheets[id] && sheets[id].bounds;
                    if (!b || b.length !== 4) return;
                    bb = bb ? [Math.min(bb[0], b[0]), Math.min(bb[1], b[1]),
                               Math.max(bb[2], b[2]), Math.max(bb[3], b[3])] : b.slice();
                });
                offerZoomTo('Geology', bb, 'geo-offscreen');
            }
            return want && avail.length > 0;
        },
        toggleAll() { return this.setAll(!this.anyOn()); },

        async set(id, want, opts) {
            opts = opts || {};
            await fetchMeta();
            const s = sheets && sheets[id];
            if (want && !(s && s.available)) {
                if (!opts.quiet) {
                    showToast('Geology sheet unavailable \u2014 ' +
                        ((s && s.reason) || 'not built on this server'), 'warning');
                }
                return false;
            }
            // Auto opacity is evaluated at the moment the layer goes on, not
            // only when the basemap changes: switching to satellite and THEN
            // turning geology on would otherwise draw it at the dark
            // basemap's value, i.e. almost invisibly.
            if (want && shared.opacityAuto) shared.opacity = autoOpacity();
            st(id).on = !!want;
            if (map && map.isStyleLoaded()) { want ? add(id) : remove(id); }
            else if (want) { map.once('idle', () => add(id)); }
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            if (want && opts.fly && s.center) {
                map.flyTo({ center: [s.center[0], s.center[1]], zoom: Math.max(map.getZoom(), 5) });
            }
            return st(id).on;
        },

        // Turning a sheet on does NOT move the camera; see set().
        toggle(id) { return this.set(id, !st(id).on, { fly: false }); },

        // Opacity, colour mode and the lithology filter are legend-wide: two
        // sheets at 40% and 80% of one legend is not a picture anybody asked
        // for, and at a border it reads as a data difference.
        // Moving the slider is what leaves auto mode: an explicit value the
        // user chose must not be recomputed behind their back on the next
        // basemap switch.
        setOpacity(v, opts) {
            opts = opts || {};
            shared.opacity = Math.max(0, Math.min(1, v));
            if (!opts.auto) shared.opacityAuto = false;
            order.forEach(id => { if (st(id).on) refresh(id); });
            const lbl = document.getElementById('geomap-op');
            if (lbl) lbl.textContent = Math.round(shared.opacity * 100) + '%';
            if (!opts.quiet && typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
        },

        /** Back to "whatever this basemap needs". */
        setAutoOpacity(on) {
            shared.opacityAuto = !!on;
            if (shared.opacityAuto) this.setOpacity(autoOpacity(), { auto: true });
            else if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
        },
        autoOpacityOn: () => shared.opacityAuto,

        // Called by switchBasemap(). A drape tuned for the dark basemap is
        // nearly invisible over satellite imagery, so "auto" has to actually
        // re-pick rather than being a one-time default.
        basemapChanged() {
            if (!shared.opacityAuto) return;
            // quiet: the panel is re-rendered once below rather than from
            // inside setOpacity, which also runs on every drag of the slider.
            this.setOpacity(autoOpacity(), { auto: true, quiet: true });
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
        },

        setAdvancedOpen(on) { shared.advOpen = !!on; },
        advancedOpen: () => shared.advOpen,

        // 'age' = the ICS chart (two sheets agree, and a geologist reads it
        // without a legend); 'ink' = the sheet as printed, which is the right
        // answer when the question is about the SCAN. The printed ink is never
        // discarded, only not shown.
        setColorMode(mode) {
            shared.colorMode = mode === 'ink' ? 'ink' : 'age';
            refreshAll();
        },
        colorMode: () => shared.colorMode,

        // The FGDC ornament off is a legitimate ask (printing, a very dense
        // view), so it is a switch — but it defaults ON, because a flat
        // translucent fill on a dark basemap is what gets mistaken for water.
        setPattern(on) { shared.pattern = !!on; refreshAll(); },
        patternOn: () => shared.pattern,

        /* ── Contacts ────────────────────────────────────────
         * The layer is added and removed rather than filtered to empty: a
         * sheet is mostly boundaries, and an invisible layer still costs a
         * filter per tile per frame. */
        setContacts(on) {
            shared.contacts = !!on;
            order.forEach(id => { if (st(id).on) syncContactLayer(id); });
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            if (window.MapLegend) MapLegend.refresh();
        },
        contactsOn: () => shared.contacts,
        /** Graded-only is the default; off shows every mapped junction. */
        setContactsGraded(on) {
            shared.contactsGraded = !!on;
            order.forEach(id => { if (st(id).on && map.getLayer(CONT(id))) paintContacts(id); });
            if (window.MapLegend) MapLegend.refresh();
        },
        contactsGradedOnly: () => shared.contactsGraded,

        /* ── The junctions picked in the table ──────────────────
         * `key` is a lithology pair ("intrusive|volcanic" = one cell) or one
         * lithology ("intrusive" = every junction it takes part in). Tapping
         * one ADDS it; tapping it again removes it, so the gesture is its own
         * undo — the standing rule that every narrowing must be escapable
         * from where it is visible. null clears the lot.
         *
         * ADDS, because a table of cells is a thing to build a map out of.
         * As a radio it could only ever ask one junction at a time: picking
         * "intrusive/volcanic" and then "intrusive/carbonate" silently threw
         * the first away, which reads as a control that forgets.
         *
         * Picking implies the layer: a reader who taps a cell has asked to
         * see those lines, and leaving the layer off would be a control that
         * visibly does nothing. */
        setContactPair(key) {
            if (!key) shared.contactPairs.clear();
            else if (shared.contactPairs.has(key)) shared.contactPairs.delete(key);
            else shared.contactPairs.add(key);
            if (shared.contactPairs.size && !shared.contacts) {
                this.setContacts(true);      // repaints and re-renders
                return;
            }
            order.forEach(id => { if (st(id).on && map.getLayer(CONT(id))) paintContacts(id); });
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            if (window.MapLegend) MapLegend.refresh();
        },
        /** Is this cell/header one of the picks? */
        junctionPicked: key => shared.contactPairs.has(key),
        /** Every junction picked, as a Set (a copy: state is not a handle). */
        contactPairs: () => new Set(shared.contactPairs),
        /** The first pick, for callers that still want one word for it. */
        contactPair: () => (shared.contactPairs.size
            ? Array.from(shared.contactPairs)[0] : null),
        /** The junction table: one row per lithology pair the sheets contain. */
        junctions: junctionIndex,
        /** How many junctions the layer would draw right now — the honest
         *  count for a surface that must not offer an empty layer as if it
         *  were a full one. Derived from the same visiblePairs() the paint
         *  filter uses, so the number and the map cannot disagree. */
        /** How many UNIT classes the drape would draw right now — the same
         *  visibleCodes() the fill filter uses, so the count and the map cannot
         *  disagree. Classes, not polygons: a class is what the legend, the
         *  matrix and the panel all count. */
        drawnUnitCount() {
            return order.reduce((n, id) => n +
                (((sheets[id] || {}).available && st(id).on) ? visibleCodes(id).length : 0), 0);
        },
        /* Only sheets that are actually ON. `available && hasContacts` counts
         * a sheet the reader never switched on — with ?geomap=car that
         * reported 452 lines over a map drawing 97, which is the exact shape
         * of failure this count exists to prevent. */
        drawnContactCount() {
            if (!shared.contacts) return 0;
            return order.reduce((n, id) => n +
                ((st(id).on && (sheets[id] || {}).available && hasContacts(id))
                    ? visiblePairs(id).length : 0), 0);
        },
        contacts: contactsOf,
        allContacts: allContacts,
        hasContacts: hasContacts,
        /** How many contacts a sheet has, and how many the model grades — the
         *  server's own counts, so the panel never reports a number it derived
         *  differently from the map. */
        contactStats(id) {
            const s = sheets && sheets[id];
            return (s && s.contacts) || null;
        },
        contactRule: (sheetId, a, b) => contactRuleFor(sheetId, a, b),
        anyContacts: () => order.some(id => (sheets[id] || {}).available && hasContacts(id)),
        /** Why there is no contact layer, in the server's own words. Named
         *  rather than inferred: "not built on this server" and "this sheet's
         *  units do not touch" are different statements and only one of them
         *  is ever true. */
        contactsReason() {
            for (const id of order) {
                const s = sheets && sheets[id];
                if (s && s.available && s.contacts_reason) return s.contacts_reason;
            }
            return '';
        },

        // "Show me the intrusives" — a legend-wide question, ANDed with
        // whatever commodity selection is running, never replacing it.
        toggleLith(key) {
            if (shared.liths.has(key)) shared.liths.delete(key);
            else shared.liths.add(key);
            refreshAll();
        },
        lithOn: key => shared.liths.has(key),
        clearLiths() { shared.liths.clear(); refreshAll(); },

        // "Not that period" — the map key made operable. Age is the one
        // dimension the legend showed but could not act on, which made the
        // swatch strip a picture of a legend. Stored as an exclusion so the
        // default state carries no state at all.
        toggleAge(key) {
            if (shared.agesOff.has(key)) shared.agesOff.delete(key);
            else shared.agesOff.add(key);
            refreshAll();
            if (window.MapLegend) MapLegend.refresh();
        },
        /** Hide every age EXCEPT this one — the key's "only this" gesture. */
        soloAge(key, allKeys) {
            const all = (allKeys && allKeys.length) ? allKeys
                : Array.from(new Set(allClasses().map(c => c.age)));
            const already = shared.agesOff.size === all.length - 1 && !shared.agesOff.has(key);
            shared.agesOff = new Set(already ? [] : all.filter(k => k !== key));
            refreshAll();
            if (window.MapLegend) MapLegend.refresh();
        },
        ageOn: key => !shared.agesOff.has(key),
        agesOff: () => new Set(shared.agesOff),
        clearAges() {
            shared.agesOff.clear();
            refreshAll();
            if (window.MapLegend) MapLegend.refresh();
        },

        toggleClass(id, code) {
            const o = st(id);
            // Hiding while isolated means "drop this one from the isolation",
            // which is the only reading that does not silently discard the
            // isolation the user just built.
            if (o.isolate) {
                o.isolate.delete(code);
                // Dropping the last isolated unit is "show everything again",
                // not "show nothing": the user is un-picking by hand here,
                // unlike a commodity selection that simply has no answer on
                // this sheet.
                if (!o.isolate.size) o.isolate = null;
                // The chips describe the isolation, so re-derive them rather
                // than leaving "gold" lit next to a gold-bearing unit the user
                // just switched off.
                o.commodities = o.isolate ? commoditiesCovered(id, o.isolate) : new Set();
            }
            else if (o.hidden.has(code)) o.hidden.delete(code);
            else o.hidden.add(code);
            refresh(id);
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
        },

        // The headline interaction: "show me everything that can host gold" —
        // and, one more click, "...or copper". Each chip is an independent
        // switch over the same isolation, so the map shows the union and the
        // panel shows exactly which switches are down.
        toggleCommodity(id, commodity) {
            const o = st(id);
            // Hand-hidden units from the legend are a different question and
            // would silently subtract from the union; drop them when a chip is
            // used, the same way isolation already wins over `hidden`.
            if (o.commodities.has(commodity)) o.commodities.delete(commodity);
            else o.commodities.add(commodity);
            applyCommodities(id);
            if (!o.on) { this.set(id, true, { quiet: true }); }
            refresh(id);
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
        },

        // Old name kept: share links and any caller predating the multi-select.
        isolateCommodity(id, commodity) { return this.toggleCommodity(id, commodity); },

        commodityOn(id, commodity) { return st(id).commodities.has(commodity); },

        /* ── Strength of affinity ─────────────────────────────────────────
         * "Gold" is 36 units only because 21 of them are placer ground and
         * quartzite inside the belt. The floor lets the reader drop those in
         * one gesture instead of hunting 21 rows in the unit list.
         *
         * Refuses to empty the map, for the standing reason: an empty drape
         * is indistinguishable from "no geology here". The caller gets false
         * and says so. */
        setMinWeight(w) {
            const want = Math.max(1, Math.min(3, parseInt(w, 10) || 1));
            const comms = selectedCommodities();
            if (comms.size && !weightCounts(comms)[want]) return false;
            shared.minWeight = want;
            applyAllSelections();
            refreshAll();
            if (window.MapLegend) MapLegend.refresh();
            return true;
        },
        minWeight: () => shared.minWeight,
        /** {1:n,2:n,3:n} — how many units each floor leaves for the selection. */
        weightCounts: comms => weightCounts(comms || selectedCommodities()),
        selectedCommodities: selectedCommodities,
        /** What a class is worth for a commodity (0 = no affinity). */
        commodityWeight(id, code, commodity) {
            return (hostWeights(id)[commodity] || {})[code] || 0;
        },

        /* ── A CELL OF THE MATRIX, AS A PICK ───────────────────────
         *
         * "commodity|age" \u2014 the gold ground, but only its Palaeoproterozoic.
         * Tapping adds, tapping again removes: the map is the UNION of the
         * cells the reader has chosen, so the table is a thing they build a
         * map out of rather than a thing that shrinks under them.
         *
         * A cell used to REPLACE the commodity selection and solo its period.
         * That made the second pick destroy the first, and \u2014 because the
         * matrix's columns are the periods actually drawn \u2014 collapsed the
         * table to one column, hiding the very cells the reader would pick
         * next. The narrowing on the MAP is the same; what changed is that
         * the table stays whole. */
        toggleCell(commodity, age) {
            const key = commodity + '|' + age;
            if (shared.picks.has(key)) shared.picks.delete(key);
            else shared.picks.add(key);
            applyAllSelections();
            refreshAll();
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            if (window.MapLegend) MapLegend.refresh();
        },
        cellPicked: (commodity, age) => shared.picks.has(commodity + '|' + age),
        /** Drop every cell of one commodity — what the ROW gesture means:
         *  "this commodity, on all of its ground" supersedes "this commodity,
         *  on these two periods", and leaving both would show the row's units
         *  while the table still lit two cells as if they were the narrowing. */
        clearPicksFor(commodity) {
            let hit = false;
            shared.picks.forEach(k => {
                if (k.slice(0, k.indexOf('|')) === commodity) { shared.picks.delete(k); hit = true; }
            });
            return hit;
        },
        /** Every cell picked, as a Set of "commodity|age" (a copy). */
        picks: () => new Set(shared.picks),
        clearPicks() {
            if (!shared.picks.size) return;
            shared.picks.clear();
            applyAllSelections();
            refreshAll();
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
            if (window.MapLegend) MapLegend.refresh();
        },

        showAll(id) {
            const o = st(id);
            o.hidden = new Set(); o.isolate = null; o.commodities = new Set();
            shared.liths.clear(); shared.agesOff.clear(); shared.minWeight = 1;
            shared.picks.clear();
            refresh(id);
            if (typeof renderGeoMapPanel === 'function') renderGeoMapPanel();
        },

        /** Clear every filter on every sheet — the panel has one "show all". */
        showEverything() {
            order.forEach(id => {
                const o = st(id);
                o.hidden = new Set(); o.isolate = null; o.commodities = new Set();
            });
            shared.liths.clear(); shared.agesOff.clear(); shared.minWeight = 1;
            // Cells are picks, and a pick is a narrowing: "show all" lifts
            // them with the rest, or the map stays isolated to the union of
            // what the reader tapped while the button claims otherwise.
            shared.picks.clear();
            // A picked junction is a narrowing like any other, so "show all"
            // has to lift it — a button that leaves the contact layer showing
            // one pair out of 26 while claiming to show everything is the
            // failure the escape hatch exists to prevent. Whether the contact
            // LAYER is on is not a narrowing, so it is left alone.
            shared.contactPairs.clear();
            refreshAll();
            order.forEach(id => { if (st(id).on && map.getLayer(CONT(id))) paintContacts(id); });
            if (window.MapLegend) MapLegend.refresh();
        },

        /** Is anything filtered out right now, on any sheet? */
        anyFiltered() {
            if (shared.liths.size || shared.agesOff.size ||
                shared.contactPairs.size || shared.picks.size) return true;
            return order.some(id => {
                const o = st(id);
                return o.hidden.size > 0 || (o.isolate && o.isolate.size);
            });
        },

        // switchBasemap() rebuilds the style and is told to skip our layers,
        // so we re-insert at the right depth here. On 'idle', not inside the
        // 'styledata' handler: addLayer() with a `before` id that has not
        // landed yet is silently dropped.
        reattach() {
            if (!this.anyOn()) return;
            map.once('idle', () => {
                order.forEach(id => { if (st(id).on) { remove(id); add(id); } });
            });
        },

        // Share-link state. `?geomap=sudan` is the common case and stays
        // short; the rest only appears once the user has actually changed it,
        // so a plain "here is the geology" link does not carry 47 codes.
        getShareParams() {
            const on = order.filter(id => st(id).on);
            // With the layer off there is no overlay to describe — except the
            // panel's own disclosure, which is a setting the sender may well
            // be pointing at ("open the unit list"). Nothing else survives:
            // an opacity or a lithology filter for a layer that is not drawn
            // would restore invisibly and then surprise whoever switched it on.
            if (!on.length) return shared.advOpen ? { geomap_adv: '1' } : null;
            const p = { geomap: on.join(',') };
            const only = [], hide = [], host = [];
            on.forEach(id => {
                const o = st(id);
                // A commodity selection travels as the commodities, not as the
                // codes they expand to: a rebuild can merge or rename units,
                // and "everything that can host gold" is still answerable
                // afterwards while a frozen code list is not.
                if (o.commodities.size) host.push(id + ':' + [...o.commodities].join('|'));
                // An isolation the PICKS produced is described by the picks,
                // and describing it twice is how a link comes back with a
                // frozen code list beside the question that generated it: the
                // codes would then win on a build where a unit was merged,
                // which is exactly what geomap_cells exists to survive.
                else if (shared.picks.size) { /* said by geomap_cells */ }
                else if (o.isolate && o.isolate.size) only.push(id + ':' + [...o.isolate].join('|'));
                else if (o.hidden.size) hide.push(id + ':' + [...o.hidden].join('|'));
            });
            if (host.length) p.geomap_host = host.join(',');
            // The floor is not only the commodity chips' business: visiblePairs()
            // applies it to the CONTACT layer too, so "classic hosts only" with
            // no chip selected is still a different map. Emitting it only
            // alongside geomap_host lost exactly that link — the reader shared
            // 20 classic junctions and the recipient opened 97. Carried
            // whenever it is not the default, which is the same rule every
            // other parameter here follows.
            if (shared.minWeight > 1) p.geomap_host_min = String(shared.minWeight);
            if (only.length) p.geomap_only = only.join(',');
            if (hide.length) p.geomap_hide = hide.join(',');
            // Legend-wide state, so no sheet prefix. Each is omitted at its
            // default, so a plain "here is the geology" link stays short.
            // A number means "the user chose this"; auto is the default and
            // is therefore the absence of the parameter. Carrying the computed
            // number instead would freeze one basemap's value into a link that
            // may well be opened on another.
            if (!shared.opacityAuto) p.geomap_opacity = String(Math.round(shared.opacity * 100));
            if (shared.colorMode !== 'age') p.geomap_color = shared.colorMode;
            if (!shared.pattern) p.geomap_pattern = '0';
            if (shared.liths.size) p.geomap_lith = [...shared.liths].join('|');
            // Ages travel as what is HIDDEN, matching how they are stored: a
            // link that hides nothing must not carry a list of everything.
            if (shared.agesOff.size) p.geomap_age_off = [...shared.agesOff].join('|');
            // Cells picked in the matrix, as commodity|age pairs. Legend-wide
            // (a period and a commodity mean the same thing on every sheet),
            // so no sheet prefix, and absent when nothing is picked.
            if (shared.picks.size) p.geomap_cells = [...shared.picks].join(',');
            if (shared.advOpen) p.geomap_adv = '1';
            // The contact layer is off by default, so it only travels when
            // asked for. `graded` is its default, so only its ABSENCE is
            // carried — same rule as every other parameter here.
            if (shared.contacts) {
                p.geomap_contacts = shared.contactsGraded ? '1' : 'all';
                // The junction, when one is picked. It travels as lithology,
                // which is a vocabulary the server owns (geomap_std.go) rather
                // than a sheet's unit codes, so the link survives a re-tile.
                // Every junction picked, not just the first: the picks are a
                // set now, and a link that carried one of three would restore
                // a map the sender was not looking at.
                if (shared.contactPairs.size) {
                    p.geomap_junction = Array.from(shared.contactPairs).join(',');
                }
            }
            return p;
        },

        async restoreFromParams(params) {
            // The Advanced disclosure is a panel setting, not a layer setting,
            // so it is honoured even in a link that does not turn geology on
            // (?panel=admin&admin_tab=map-settings&geomap_adv=1 — "look at the
            // unit list"). Everything below it needs the catalogue and a
            // layer, so it stays behind the early return.
            if (params.get('geomap_adv') === '1') shared.advOpen = true;
            const want = (params.get('geomap') || '').split(',').filter(Boolean);
            if (!want.length) return;
            await fetchMeta();
            const parse = (raw, fn) => (raw || '').split(',').filter(Boolean).forEach(part => {
                const i = part.indexOf(':');
                if (i > 0) fn(part.slice(0, i), part.slice(i + 1));
            });
            // Opacity is legend-wide now, but an OLD link carries the
            // per-sheet form ("car:80"). Accept both: a bare number is the
            // new one, and a prefixed list takes the first sheet's value
            // rather than dropping the user's setting on the floor.
            const opRaw = params.get('geomap_opacity') || '';
            if (opRaw === 'auto') {
                shared.opacityAuto = true;
                shared.opacity = autoOpacity();
            } else if (opRaw) {
                const first = opRaw.split(',')[0];
                const n = parseInt(first.indexOf(':') > 0 ? first.split(':')[1] : first, 10);
                if (!isNaN(n)) {
                    shared.opacity = Math.max(0, Math.min(1, n / 100));
                    shared.opacityAuto = false;
                }
            } else {
                // No parameter = auto, evaluated against the basemap this link
                // actually opens on (which restoreFromParams runs after).
                shared.opacity = autoOpacity();
            }
            const ct = params.get('geomap_contacts');
            if (ct) {
                shared.contacts = true;
                shared.contactsGraded = ct !== 'all';
            }
            // A junction from a link is checked against the junctions these
            // sheets actually have. An unknown one would filter the layer to
            // nothing, and an empty layer reads as "no contacts here" rather
            // than as a stale link — same rule as every other selection above.
            const jn = params.get('geomap_junction');
            if (jn && shared.contacts) {
                const known = junctionIndex(true);
                const liths = new Set(Object.keys(known).flatMap(k => k.split('|')));
                // A link carries every junction the sender had picked; the
                // ones this build still has are kept and the rest dropped.
                // Partial is the right answer: keeping the known picks
                // reproduces most of what was shared, where refusing the lot
                // would silently show every contact instead.
                const asked = jn.split(',').filter(Boolean);
                const want = asked.filter(
                    k => known[k] || (k.indexOf('|') < 0 && liths.has(k)));
                if (want.length) shared.contactPairs = new Set(want);
                if (want.length < asked.length && typeof showToast === 'function') {
                    showToast('Geology selection is out of date — that link names a rock ' +
                        'junction these sheets do not have; showing ' +
                        (want.length ? 'the rest' : 'every contact') + '.', 'warning');
                }
            }
            if (params.get('geomap_color') === 'ink') shared.colorMode = 'ink';
            if (params.get('geomap_pattern') === '0') shared.pattern = false;
            const lithRaw = params.get('geomap_lith') || '';
            if (lithRaw) {
                const known = new Set((stdLegend().lithology || []).map(l => l.key));
                const keys = lithRaw.split('|').filter(k => known.has(k));
                // Same rule as an out-of-date code list: a filter that matches
                // nothing renders an empty map, which is indistinguishable
                // from "no data here". Drop it and say so.
                if (keys.length) shared.liths = new Set(keys);
                else if (typeof showToast === 'function') {
                    showToast('Geology selection is out of date \u2014 that link names rock types ' +
                        'this legend no longer has; showing all of them.', 'warning');
                }
            }
            const ageRaw = params.get('geomap_age_off') || '';
            if (ageRaw) {
                // An exclusion cannot empty the map by being out of date — an
                // unknown key hides nothing — so unknown keys are dropped
                // silently. But a link that hides EVERY age this build has
                // would render an empty drape, which is indistinguishable from
                // "no data here": that one is refused and said out loud.
                const all = new Set(allClasses().map(c => c.age));
                const keys = ageRaw.split('|').filter(k => all.has(k));
                if (keys.length && keys.length < all.size) shared.agesOff = new Set(keys);
                else if (keys.length && typeof showToast === 'function') {
                    showToast('Geology selection is out of date \u2014 that link hides every period ' +
                        'this build of the legend has; showing all of them.', 'warning');
                }
            }
            parse(params.get('geomap_hide'), (id, v) => { st(id).hidden = keep(id, v.split('|')); });
            parse(params.get('geomap_only'), (id, v) => {
                const codes = keep(id, v.split('|'));
                // A code that no longer exists must not silently produce an
                // empty map: a rebuild can MERGE two classes, which renames
                // both (GO + GC2 become 'GC2/GO'), so an old link's codes can
                // all disappear at once. Rendering nothing looks exactly like
                // "this sheet has no data here", so drop the isolation and say
                // so instead.
                if (!codes.size) {
                    if (typeof showToast === 'function') {
                        showToast('Geology selection is out of date \u2014 that link names units this ' +
                            'build of the sheet no longer has; showing all of them.', 'warning');
                    }
                    return;
                }
                st(id).isolate = codes;
                st(id).commodities = commoditiesCovered(id, codes);
            });
            // The strength floor is read BEFORE the chips, because the chips
            // expand to units through it. A floor that leaves the link's
            // commodities with no unit at all is dropped rather than obeyed:
            // the alternative is an empty drape that reads as "no data here".
            const minRaw = parseInt(params.get('geomap_host_min') || '', 10);
            if (minRaw >= 2 && minRaw <= 3) shared.minWeight = minRaw;

            // Commodity chips last: they are the authoritative selection and
            // recompute `isolate` from the sheet as built.
            parse(params.get('geomap_host'), (id, v) => {
                const known = new Set(Object.keys(hostMap(id)));
                const want = v.split('|').filter(k => known.has(k));
                if (!want.length) {
                    if (typeof showToast === 'function') {
                        showToast('Geology selection is out of date \u2014 that link names rock-type ' +
                            'hosts this build of the sheet does not list; showing all units.', 'warning');
                    }
                    return;
                }
                st(id).commodities = new Set(want);
                applyCommodities(id);
            });
            // Cells picked in the matrix. Validated against the ages and
            // commodities this build actually has: a cell that resolves to
            // nothing would contribute nothing to the union, which is
            // indistinguishable from a link that never had it.
            const cellRaw = params.get('geomap_cells') || '';
            if (cellRaw) {
                const ages = new Set(allClasses().map(c => c.age));
                const comms = new Set(allClasses().flatMap(c => c.commodities || []));
                const asked = cellRaw.split(',').filter(Boolean);
                const want = asked.filter(k => {
                    const i = k.indexOf('|');
                    return i > 0 && comms.has(k.slice(0, i)) && ages.has(k.slice(i + 1));
                });
                if (want.length) shared.picks = new Set(want);
                if (want.length < asked.length && typeof showToast === 'function') {
                    showToast('Geology selection is out of date — that link picks rock/commodity ' +
                        'cells this build does not have; showing ' +
                        (want.length ? 'the rest' : 'every unit') + '.', 'warning');
                }
                if (want.length) applyAllSelections();
            }
            // A sheet with no answer at this floor drawing nothing is correct
            // (see visibleCodes); EVERY sheet drawing nothing is not — that is
            // a blank map, and a blank map is indistinguishable from "no
            // geology here". Checked once, after every sheet has been parsed,
            // because a floor that empties Sudan may still be answerable on
            // Tanzania.
            //
            // Only when a COMMODITY was selected, though: the floor also grades
            // the contact layer, and a contacts-only link ("classic junctions
            // here") has no isolation to test — dropping the floor for want of
            // one silently handed the recipient every graded junction instead.
            if (shared.minWeight > 1 && order.some(id => st(id).commodities.size) &&
                !order.some(id => st(id).commodities.size && st(id).isolate && st(id).isolate.size)) {
                shared.minWeight = 1;
                applyAllSelections();
                if (typeof showToast === 'function') {
                    showToast('Geology selection is out of date \u2014 that link keeps only the ' +
                        'strongest hosts and this build of the sheets has none; showing every host.',
                        'warning');
                }
            }
            for (const id of want) await this.set(id, true, { quiet: true, fly: false });
        }
    };

    window.GeoMap = GeoMap;
})();
