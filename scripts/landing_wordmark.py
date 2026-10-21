"""Outline the landing page's footer wordmark into landing/src/lib/wordmark.ts.

Sets "aperture" in Inter Tight at weight 700 with -0.06em letter-spacing, the way
the browser set the old CSS text: HarfBuzz shapes it with the font's kerning,
letter-spacing is added after every character, and the baseline sits where a
0.76em line box puts it. The glyphs come from the font file the page already
ships, so the SVG matches the text it replaced.

    uv run --with fonttools --with brotli --with uharfbuzz python scripts/landing_wordmark.py
"""

from __future__ import annotations

import io
import os
import re

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "landing")
FONT = os.path.join(ROOT, "node_modules", "@fontsource-variable", "inter-tight", "files",
                    "inter-tight-latin-wght-normal.woff2")
OUT = os.path.join(ROOT, "src", "lib", "wordmark.ts")
TEXT, WEIGHT, TRACKING, LINE_HEIGHT = "aperture", 700, -0.06, 0.76

font = instancer.instantiateVariableFont(TTFont(FONT), {"wght": WEIGHT})
font.flavor = None
raw = io.BytesIO()
font.save(raw)
upem = font["head"].unitsPerEm
os2, hhea = font["OS/2"], font["hhea"]
if os2.fsSelection & (1 << 7):  # USE_TYPO_METRICS: the browser takes the typo metrics
    ascent, descent = os2.sTypoAscender, -os2.sTypoDescender
else:
    ascent, descent = hhea.ascent, -hhea.descent

shaper = hb.Font(hb.Face(raw.getvalue()))
buf = hb.Buffer()
buf.add_str(TEXT)
buf.guess_segment_properties()
hb.shape(shaper, buf, {"kern": True, "liga": True})

line = LINE_HEIGHT * upem
baseline = (line - (ascent + descent)) / 2 + ascent  # content area centred in the line box
glyphs, order = font.getGlyphSet(), font.getGlyphOrder()
x, parts = 0.0, []
for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
    pen = SVGPathPen(glyphs)
    glyphs[order[info.codepoint]].draw(TransformPen(pen, (1, 0, 0, -1, x + pos.x_offset, baseline - pos.y_offset)))
    parts.append(pen.getCommands())
    x += pos.x_advance + TRACKING * upem

d = re.sub(r"-?\d+\.\d+", lambda m: f"{float(m.group()):.1f}".rstrip("0").rstrip("."), " ".join(parts))
with open(OUT, "w") as out:
    out.write(f'''/* "{TEXT}" set in Inter Tight at weight {WEIGHT} with {TRACKING}em letter-spacing and
   the font's kerning, shaped by HarfBuzz and outlined from the font file the
   page ships (@fontsource-variable/inter-tight). Units are font units at
   {upem} per em: the box is the {x / upem:.4f}em advance by a {LINE_HEIGHT}em line box,
   so it crops exactly where the old line-height did. Regenerate with
   scripts/landing_wordmark.py rather than editing by hand. */
export const WORDMARK = {{
  em: {upem},
  width: {x:.2f},
  height: {line:.2f},
  d: "{d}",
}};
''')
print("wrote", OUT)
