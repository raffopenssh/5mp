#!/usr/bin/env python3
"""Render srv/static/og-methods.png (1200x630) — the link-preview card for
`/?methods=…` section links. Layer names/colours are read from the About
modal's Data Layers list in globe.html so the card cannot drift from it."""
import re, html
from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
F = '/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf'
bold = lambda s: ImageFont.truetype(F % '-Bold', s)
reg = lambda s: ImageFont.truetype(F % '', s)

src = open('srv/templates/globe.html', encoding='utf-8').read()
layers = re.findall(r'<strong style="color:(#[0-9a-f]{6})">([^<]+)</strong>\s*–\s*([^<]+)</span>', src)
assert layers, 'no Data Layers list found in globe.html'

im = Image.new('RGB', (W, H), '#0a0a0a')
d = ImageDraw.Draw(im)
# subtle vignette
for i in range(0, 400, 4):
    a = int(22 * (1 - i / 400))
    d.ellipse([W - 300 - i, -200 - i, W + 300 + i, 200 + i], outline=(10 + a, 10 + a * 2, 10 + a))
# globe mark
cx, cy, r = 110, 118, 46
g = '#22c55e'
d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=g, width=3)
d.ellipse([cx - r * 0.4, cy - r, cx + r * 0.4, cy + r], outline=g, width=2)
d.line([cx - r, cy, cx + r, cy], fill=g, width=2)
# title block
d.text((190, 72), 'How this map works', font=bold(54), fill='#ffffff')
d.text((192, 140), '5 Megapixels of Global Conservation · Methods', font=reg(24), fill='#8a8a8a')
d.line([64, 205, W - 64, 205], fill='#262626', width=2)
# layers
y = 232
for col, name, desc in layers:
    d.ellipse([70, y + 10, 92, y + 32], fill=col)
    d.text((112, y), html.unescape(name.strip()), font=bold(30), fill=col)
    d.text((112, y + 40), html.unescape(desc.strip()), font=reg(22), fill='#c8c8c8')
    y += 84
d.text((64, H - 44), '5MP.globe · five-megapixel-conservation.exe.xyz', font=reg(20), fill='#4ade80')
im.save('srv/static/og-methods.png', optimize=True)
print('wrote srv/static/og-methods.png', len(layers), 'layers')
