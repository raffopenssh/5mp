"""Contact sheet of Esri imagery chips for visual adjudication of candidates.

Rationale: the whole reason data/mining_pits/*.json was 0.1% consistent with
truth is that nobody ever looked at what it ranked highest. Building a negatives
set (docs/MINING_FINDINGS_2026-08.md §Plan step 4) means an eyeball pass over
top-ranked candidates, and one 4x3 contact sheet costs one image instead of
twelve.

Usage:
  python3 analysis/chip_grid.py --json data/mining_candidates/CAF_Chinko.json \
      --n 12 --out analysis/out/chinko_top12.png
  python3 analysis/chip_grid.py --points "24.23213,6.42818;15.03891,11.42193" \
      --out /tmp/fps.png --labels "sandbank;rice"
"""
import argparse, json, os, sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import basemap_chip as bc

CELL = 384


def chip(lon, lat, z, half_deg, label, src="esri"):
    bbox = (lon - half_deg, lat - half_deg, lon + half_deg, lat + half_deg)
    im, got = bc.fetch(bbox, z, src)
    # crop to the requested bbox inside the tile-aligned mosaic
    w, h = im.size
    gw, gh = got[2] - got[0], got[3] - got[1]
    px = lambda lo, la: (int((lo - got[0]) / gw * w), int((got[3] - la) / gh * h))
    x0, y0 = px(bbox[0], bbox[3])
    x1, y1 = px(bbox[2], bbox[1])
    im = im.crop((max(0, x0), max(0, y0), min(w, x1), min(h, y1)))
    im = im.resize((CELL, CELL))
    d = ImageDraw.Draw(im)
    c = CELL // 2
    d.line([(c - 14, c), (c - 5, c)], fill=(255, 40, 40), width=2)
    d.line([(c + 5, c), (c + 14, c)], fill=(255, 40, 40), width=2)
    d.line([(c, c - 14), (c, c - 5)], fill=(255, 40, 40), width=2)
    d.line([(c, c + 5), (c, c + 14)], fill=(255, 40, 40), width=2)
    d.rectangle([0, 0, CELL - 1, 16], fill=(0, 0, 0))
    d.text((3, 4), label[:70], fill=(255, 255, 0))
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="scan output; uses sites[] in rank order")
    ap.add_argument("--points", help="lon,lat;lon,lat;...")
    ap.add_argument("--labels", help="semicolon separated, matches --points")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--z", type=int, default=16)
    ap.add_argument("--half-deg", type=float, default=0.0045)  # ~1 km box
    ap.add_argument("--src", default="esri")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    items = []
    if a.json:
        d = json.load(open(a.json))
        for i, s in enumerate(d.get("sites", [])[a.skip:a.skip + a.n],
                              start=a.skip):
            lbl = f"#{i} {s['lat']:.4f},{s['lon']:.4f} px{s.get('px')}"
            if s.get("rank_score") is not None:
                lbl += f" r{s['rank_score']}"
            elif s.get("score") is not None:
                lbl += f" s{s['score']}"
            items.append((s["lon"], s["lat"], lbl))
    if a.points:
        labs = (a.labels or "").split(";")
        for i, p in enumerate(a.points.split(";")):
            if not p.strip():
                continue
            lo, la = [float(x) for x in p.split(",")]
            items.append((lo, la, labs[i] if i < len(labs) and labs[i]
                          else f"{la:.4f},{lo:.4f}"))
    if not items:
        ap.error("need --json or --points")

    cols = min(a.cols, len(items))
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * CELL, rows * CELL), (20, 20, 20))
    for i, (lo, la, lbl) in enumerate(items):
        try:
            im = chip(lo, la, a.z, a.half_deg, lbl, a.src)
        except Exception as ex:
            print(f"  chip fail {lbl}: {str(ex)[:60]}", file=sys.stderr)
            continue
        sheet.paste(im, ((i % cols) * CELL, (i // cols) * CELL))
        print(f"  {i+1}/{len(items)} {lbl}", file=sys.stderr)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    sheet.save(a.out)
    print(a.out)


if __name__ == "__main__":
    main()
