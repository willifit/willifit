#!/usr/bin/env python3
"""Patch the bottom-strip caption on og-image.png so it reflects current
location count.  Original PNG was hand-crafted by an earlier session with
"7,400+ GARAGES" baked in; database is now 8,863.

Strategy:
  1. Load original image
  2. Paint over the entire bottom-strip text region with the gradient
     background colour (sampled from above/below the text)
  3. Re-render: "226 US CITIES   ·   8,800+ GARAGES   ·   READ FROM STREET VIEW"
     using Helvetica.ttc (the closest match to whatever the original
     designer used -- the original looks like a wide-tracked sans-serif)

Output: overwrites /Users/MrLaptop2/Downloads/ClearPath/og-image.png

Verification: Image dimensions stay 1200x630, 8-bit RGB, PNG.  Width of
new caption is centred so it lines up under the truck and headline.
"""
import os
from PIL import Image, ImageDraw, ImageFont

PATH = "/Users/MrLaptop2/Downloads/ClearPath/og-image.png"
HELVETICA = "/System/Library/Fonts/Helvetica.ttc"

img = Image.open(PATH).convert("RGB")
W, H = img.size
draw = ImageDraw.Draw(img)

# Sample the background gradient in the strip we're about to paint over.
# Original strip text occupies roughly y=560-595, x=198-1001.  We expand
# the paint area slightly so any anti-aliased fringe gets covered.
strip_top, strip_bottom = 545, 605
strip_left, strip_right = 100, 1100

# Paint with the local background colour at each pixel (preserving the
# subtle dark-blue gradient instead of a flat fill that would visibly
# differ).  Cheap method: copy the row from y=545 (just above text) and
# y=605 (just below) and blend into the text region.
# Actually simpler: the background here is essentially uniform dark
# navy-black RGB(13,17,23 -> 30,41,59 at left edge).  Sample one pixel
# at each x just below the strip and use it as the fill colour for the
# whole strip column-by-column -- preserves the gradient exactly.
for x in range(strip_left, strip_right):
    bg = img.getpixel((x, 615))  # well below the text
    for y in range(strip_top, strip_bottom):
        img.putpixel((x, y), bg)

# Now redraw the caption.  Original looks like ~24px caps, wide tracking.
# Helvetica regular at size 22, all caps, with manual extra spacing
# between segments via the middle-dot character.
font = ImageFont.truetype(HELVETICA, 22, index=0)  # 0 = Regular

CAPTION = "226 US CITIES   ·   8,800+ GARAGES   ·   READ FROM STREET VIEW"
TEXT_COLOR = (148, 163, 184)  # matches the brighter pixels we sampled

# Centre horizontally, vertically position so it sits in the same strip
bbox = draw.textbbox((0, 0), CAPTION, font=font)
text_w = bbox[2] - bbox[0]
text_h = bbox[3] - bbox[1]
text_x = (W - text_w) // 2
text_y = (strip_top + strip_bottom - text_h) // 2 - bbox[1]  # account for baseline

draw.text((text_x, text_y), CAPTION, fill=TEXT_COLOR, font=font)

img.save(PATH, "PNG", optimize=True)
print(f"Patched: {PATH}")
print(f"  Caption: '{CAPTION}'")
print(f"  Position: ({text_x},{text_y})  text_w={text_w}px")
print(f"  File size: {os.path.getsize(PATH):,} bytes")
