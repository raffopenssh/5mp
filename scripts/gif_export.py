#!/usr/bin/env python3
"""Headless GIF export of the time animator (docs/agents/animator.md "GIF export").

Opens a share-link path in headless Chromium (swiftshader GL, 1280x720, DPR 1),
logged in as the AOI owner by cookie (AOI_OWNER_PWD from secrets.env — never
printed, never on the URL), waits until Animator.getState() says every layer
is loaded, calls Animator.exportGIF(...) and saves the blob.

  scripts/gif_export.py '/?aoi_focus=XSA_Study_Area&from=...&anim=fireGrid,trajs&anim_speed=10' out.gif \
      [--width 720] [--palette per-frame|global] [--wait 40] [--base http://localhost:8000]

Progress ("frame 37/160") goes to stderr every ~2 s; the last line on stdout
is `gif -> out.gif  frames=160  duration=18.2s  encode=24.1s  size=3.4MB`.
"""
import argparse, base64, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def owner_pwd():
    env = os.environ.get('AOI_OWNER_PWD')
    if env:
        return env
    f = ROOT / 'secrets.env'
    if f.exists():
        for line in f.read_text().splitlines():
            if line.startswith('AOI_OWNER_PWD='):
                return line.split('=', 1)[1].strip().strip('"').strip("'")
    sys.exit('AOI_OWNER_PWD: not in the environment and not in secrets.env')


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('path', help="share-link path+query, e.g. '/?aoi_focus=...&anim=fireGrid&anim_speed=10'")
    ap.add_argument('out')
    ap.add_argument('--width', type=int, default=720)
    ap.add_argument('--palette', choices=['per-frame', 'global'], default='per-frame')
    ap.add_argument('--scale', default='output', help="'output' (render fire canvas at GIF size, default) or 0 (viewport×dpr)")
    ap.add_argument('--max-frames', type=int, default=0, help='lower the frame cap (160); same duration, fewer fps. Use ~80 with season= layers: each frame is a MapLibre repaint')
    ap.add_argument('--wait', type=float, default=40, help='max seconds to wait for layers to load')
    ap.add_argument('--base', default='http://localhost:8000')
    ap.add_argument('--size', default='1280x720', help='viewport WxH')
    ap.add_argument('--timeout', type=float, default=900, help='max seconds for the export itself')
    a = ap.parse_args()
    W, H = map(int, a.size.lower().split('x'))
    if 'anim=' not in a.path:
        log('warning: path has no anim= parameter; the animator will not open')

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        # swiftshader for MapLibre's WebGL; but the 2D fire canvas must NOT be
        # GPU-accelerated: Chrome records 2D draws and rasterises them lazily
        # on readback, and through swiftshader one getImageData of a busy
        # frame took 1–4 s (measured 2026-10-18). Software Skia: 50–400 ms.
        b = p.chromium.launch(args=['--use-gl=angle', '--use-angle=swiftshader', '--ignore-gpu-blocklist',
                                    '--disable-accelerated-2d-canvas'])
        ctx = b.new_context(viewport={'width': W, 'height': H}, device_scale_factor=1)
        ctx.add_cookies([{'name': 'access_pwd', 'value': owner_pwd(), 'url': a.base + '/'}])
        page = ctx.new_page()
        errs = []
        page.on('pageerror', lambda e: errs.append(str(e)))
        page.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
        t_nav = time.time()
        page.goto(a.base + a.path, wait_until='load', timeout=120000)

        # Wait for the animator to open and every ON layer to have its data.
        deadline = time.time() + a.wait
        st = None
        while time.time() < deadline:
            st = page.evaluate("() => window.Animator && Animator.getState && Animator.getState()")
            if st and st.get('ready') and st.get('layers'):
                break
            time.sleep(0.5)
        if not st:
            sys.exit('animator did not open within %.0fs (errors: %s)' % (a.wait, errs[-3:]))
        if not st.get('ready'):
            log('warning: layers still loading after %.0fs: %s' % (a.wait, st.get('loading')))
        # Map tiles: a short settle after the layers so the basemap capture is complete.
        try:
            page.evaluate("() => new Promise(r => { if (map.loaded() && !map.areTilesLoaded || map.areTilesLoaded()) r(); else map.once('idle', r); setTimeout(r, 15000); })")
        except Exception:
            pass
        log('loaded in %.1fs: layers=%s speed=%s aoi=%s' % (time.time() - t_nav, ','.join(st['layers']), st['speed'], st.get('aoi')))

        scale = a.scale if a.scale == 'output' else float(a.scale)
        page.evaluate("""([opts]) => { window.__gif = null; window.__gifErr = null;
            Animator.exportGIF(opts).then(b => { window.__gif = b; }).catch(e => { window.__gifErr = String(e && e.message || e); }); }""",
            [{'width': a.width, 'palette': a.palette, 'scale': scale, 'maxFrames': a.max_frames}])
        t0 = time.time()
        last = None
        while time.time() - t0 < a.timeout:
            ex = page.evaluate("() => Animator.exportState()")
            err = page.evaluate("() => window.__gifErr")
            if err:
                sys.exit('export failed: ' + err)
            key = (ex.get('phase'), ex.get('frame'))
            if ex.get('phase') == 'frames' and key != last:
                log('frame %d/%d  (%.0fs)' % (ex['frame'], ex['frames'], time.time() - t0))
                last = key
            elif ex.get('phase') not in ('frames', 'done') and key != last:
                log('phase %s' % ex.get('phase'))
                last = key
            if ex.get('phase') == 'done' and page.evaluate("() => !!window.__gif"):
                break
            time.sleep(2)
        else:
            sys.exit('export timed out after %.0fs' % a.timeout)
        ex = page.evaluate("() => Animator.exportState()")
        data = page.evaluate("""() => new Promise(r => { const fr = new FileReader();
            fr.onload = () => r(fr.result.split(',')[1]); fr.readAsDataURL(window.__gif); })""")
        raw = base64.b64decode(data)
        Path(a.out).write_bytes(raw)
        b.close()
    print('gif -> %s  frames=%d  delay=%dms  duration=%.1fs  encode=%.1fs  size=%.1fMB  palette=%s  worker=%s' % (
        a.out, ex['frames'], ex['delayMs'], ex['frames'] * ex['delayMs'] / 1000, ex['ms'] / 1000, len(raw) / 1e6,
        ex.get('palette'), ex.get('worker')))


if __name__ == '__main__':
    main()
