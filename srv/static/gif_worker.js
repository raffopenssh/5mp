// GIF encoder worker for the animator's export (anim.js exportGIF).
//
// The main thread draws frames; this thread quantises and writes them, so
// drawing frame i+1 overlaps encoding frame i. gifenc is vendored
// (vendor/gifenc.esm.js) — the export must not depend on a CDN being up.
//
// Protocol (main -> worker):
//   {type:'init',    width, height, palette:'per-frame'|'global'}
//   {type:'palette', buf}            global mode: quantise once from this frame
//   {type:'frame',   i, buf, delay}  RGBA buffer (transferred), delay in ms
//   {type:'finish'}
// (worker -> main):
//   {type:'done', i}                 frame i is written (back-pressure)
//   {type:'result', bytes}           the finished GIF (transferred)
//   {type:'error', message}
import { GIFEncoder, quantize, applyPalette } from './vendor/gifenc.esm.js';

let enc = null, W = 0, H = 0, mode = 'per-frame', globalPalette = null, first = true;

self.onmessage = (ev) => {
    const m = ev.data;
    try {
        if (m.type === 'init') {
            enc = GIFEncoder();
            W = m.width; H = m.height; mode = m.palette === 'global' ? 'global' : 'per-frame';
            globalPalette = null; first = true;
        } else if (m.type === 'palette') {
            globalPalette = quantize(new Uint8ClampedArray(m.buf), 256);
        } else if (m.type === 'frame') {
            const data = new Uint8ClampedArray(m.buf);
            let palette, index;
            if (mode === 'global' && globalPalette) {
                palette = globalPalette;
                index = applyPalette(data, palette);
                // One palette for the file: write it once as the global table,
                // every later frame refers to it (smaller file, no per-frame
                // palette block).
                enc.writeFrame(index, W, H, { palette: first ? palette : undefined, first, delay: m.delay, repeat: 0 });
            } else {
                palette = quantize(data, 256);
                index = applyPalette(data, palette);
                enc.writeFrame(index, W, H, { palette, delay: m.delay });
            }
            first = false;
            self.postMessage({ type: 'done', i: m.i });
        } else if (m.type === 'finish') {
            enc.finish();
            const bytes = enc.bytes();
            // bytes() is a view on the encoder's buffer; copy so the transfer
            // does not detach the encoder's own storage mid-use.
            const out = bytes.slice();
            self.postMessage({ type: 'result', bytes: out }, [out.buffer]);
        }
    } catch (e) {
        self.postMessage({ type: 'error', message: String(e && e.message || e) });
    }
};
