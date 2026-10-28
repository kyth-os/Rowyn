#!/usr/bin/env python3
"""Render Rowyn's supplied logo (with its navy matte removed) on black.

Development-only tool: Pillow and a local Noto Sans font are needed to regenerate
artwork, not to build either OS image. Source artwork is kept unchanged.
"""
import argparse
import base64
from pathlib import Path
from typing import cast
from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--font', type=Path, required=True, help='NotoSans-Regular.ttf')
args = parser.parse_args()
assets = ROOT / 'shared/wallpaper'
source = Image.open(assets / 'rowyn-logo-source.png').convert('RGBA')
background = cast(tuple[int, int, int, int], source.getpixel((source.width // 2, 20)))[:3]
# Unmatte all navy pixels, including enclosed leaf cut-outs. Feather and
# decontaminate edge colors so compositing on black leaves no navy halo.
transparent = Image.new('RGBA', source.size, (0, 0, 0, 0))
src, dst = source.load(), transparent.load()
assert src is not None and dst is not None
for y in range(source.height):
    for x in range(source.width):
        rgba = cast(tuple[int, int, int, int], src[x, y])
        if not rgba[3]:
            continue
        distance = max(rgba[i] - background[i] for i in range(3))
        if distance <= 3:
            continue
        alpha = min(1.0, distance / 96)
        color = tuple(max(0, min(255, round((rgba[i] - (1 - alpha) * background[i]) / alpha))) for i in range(3))
        dst[x, y] = (*color, round(rgba[3] * alpha))
# Ignore very faint rounded-icon edge residue when trimming the logo canvas;
# retain a small antialiasing margin around the actual colored artwork.
bbox = transparent.getchannel('A').point([0] * 128 + [255] * 128).getbbox()
assert bbox is not None
bbox = (max(0, bbox[0] - 3), max(0, bbox[1] - 3),
        min(source.width, bbox[2] + 3), min(source.height, bbox[3] + 3))
logo = transparent.crop(bbox)
logo_path = assets / 'rowyn-logo.png'
logo.save(logo_path, optimize=True)

w, h, scale = 3840, 2160, 2
logo_height = 320
logo_width = round(logo.width * logo_height / logo.height)
logo_x, logo_y = (w - logo_width) / 2, 864
word_y, font_size, tracking = 1296, 72, 14
encoded = base64.b64encode(logo_path.read_bytes()).decode('ascii')
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="title description">
  <title id="title">ROWYN — Black</title>
  <desc id="description">The supplied multicolored Rowyn logo without its blue background, centered above the ROWYN name on pure black.</desc>
  <rect width="{w}" height="{h}" fill="#000000"/>
  <image x="{logo_x}" y="{logo_y}" width="{logo_width}" height="{logo_height}" href="data:image/png;base64,{encoded}"/>
  <text x="{w // 2}" y="{word_y}" text-anchor="middle" font-family="Noto Sans, sans-serif" font-size="{font_size}" font-weight="400" letter-spacing="{tracking}" fill="#ededed">ROWYN</text>
</svg>
'''
(assets / 'rowyn-black.svg').write_text(svg)
image = Image.new('RGB', (w * scale, h * scale), '#000000')
resized = logo.resize((logo_width * scale, logo_height * scale), Image.Resampling.LANCZOS)
image.paste(resized, (round(logo_x * scale), logo_y * scale), resized)
draw = ImageDraw.Draw(image)
font = ImageFont.truetype(str(args.font), font_size * scale)
word = 'ROWYN'
widths = [draw.textlength(c, font=font) for c in word]
text_width = sum(widths) + tracking * scale * (len(widths) - 1)
x = w * scale / 2 - text_width / 2
for c, width in zip(word, widths):
    draw.text((x, word_y * scale), c, font=font, fill='#ededed', anchor='ls')
    x += width + tracking * scale
# Center actual visible artwork, independent of image padding and font bearings.
bbox = ImageChops.difference(image, Image.new('RGB', image.size, '#000000')).getbbox()
assert bbox is not None
image = ImageChops.offset(image, round(w * scale / 2 - (bbox[0] + bbox[2]) / 2),
                        round(h * scale / 2 - (bbox[1] + bbox[3]) / 2))
image = image.resize((w, h), Image.Resampling.LANCZOS)
# Resampling can shift the last faint edge pixels; center the delivered PNG too.
bbox = ImageChops.difference(image, Image.new('RGB', image.size, '#000000')).getbbox()
assert bbox is not None
image = ImageChops.offset(image, round(w / 2 - (bbox[0] + bbox[2]) / 2),
                        round(h / 2 - (bbox[1] + bbox[3]) / 2))
output = assets / 'contents/images/3840x2160.png'
output.parent.mkdir(parents=True, exist_ok=True)
image.save(output, optimize=True)
print('Transparent supplied logo:', logo_path)
print('Shared 4K wallpaper:', output)
