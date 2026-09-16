#!/usr/bin/env node
/* A MODULE THAT THROWS AT LOAD LEAVES NOTHING BEHIND, AND LOOKS LIKE CSS.
 *
 * 2026-09-16: a comment inside anim.js's injected-CSS template literal
 * contained a pair of backticks. That ENDED the literal, so the rest of the
 * sheet became code, the IIFE threw at load, `window.Animator` was never
 * defined — and the only visible symptom was the "animate" button rendering
 * as an unstyled native <button> (white box, Arial 13px) at the bottom of the
 * screen. `node --check` passes such a file: it is syntactically valid, it
 * just means something else. Nothing else in the suite looks at load-time
 * behaviour, so the bug shipped and was found by eye.
 *
 * This runs each front-end module under a stub DOM — enough for its load-time
 * work (inject a <style>, register a listener, set a timer) — and asserts that
 * (a) it does not throw, (b) it left its global behind, and (c) its injected
 * stylesheet still contains the selectors it is supposed to define. Usage:
 *
 *   node tests/js_load_smoke.js srv/static/anim.js Animator '#anim-open-btn' …
 */
'use strict';
const path = require('path');

const styles = [];
function stubEl(tag) {
    const e = {
        tagName: tag, style: {}, dataset: {}, children: [],
        classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
        appendChild() {}, remove() {}, setAttribute() {}, removeAttribute() {},
        addEventListener() {}, removeEventListener() {},
        querySelector() { return null; }, querySelectorAll() { return []; },
        getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
    };
    let text = '', html = '';
    Object.defineProperty(e, 'textContent', { get: () => text, set: v => { text = String(v); } });
    Object.defineProperty(e, 'innerHTML', { get: () => html, set: v => { html = String(v); } });
    return e;
}
global.document = {
    readyState: 'complete',
    head: { appendChild(e) { styles.push(e); } },
    body: { appendChild() {}, classList: { add() {}, remove() {}, toggle() {} } },
    createElement: stubEl,
    getElementById() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {}, removeEventListener() {}
};
global.addEventListener = () => {};
global.removeEventListener = () => {};
global.window = global;
global.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
global.performance = { now: () => 0 };
global.requestAnimationFrame = () => 0;
global.cancelAnimationFrame = () => {};
global.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };

const [file, globalName, ...selectors] = process.argv.slice(2);
if (!file) { console.error('usage: js_load_smoke.js <file.js> [GlobalName] [selector…]'); process.exit(2); }

const problems = [];
try {
    require(path.resolve(file));
} catch (e) {
    console.error(path.basename(file) + ' threw at load: ' + e.message);
    process.exit(1);
}
if (globalName && typeof global[globalName] === 'undefined') {
    problems.push('window.' + globalName + ' was never defined');
}
const css = styles.map(e => e.textContent || e.innerHTML || '').join('\n');
for (const sel of selectors) if (!css.includes(sel)) problems.push('injected CSS is missing ' + sel);

if (problems.length) { console.error(problems.join('; ')); process.exit(1); }
console.log('ok ' + path.basename(file));
process.exit(0);
