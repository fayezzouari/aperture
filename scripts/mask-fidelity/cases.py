"""Generate mask cases and encode them the way the server does.

The browser decodes these RLE strings itself, so the two implementations have to
agree exactly - not approximately. This writes the ground truth next to the wire
format so check.mjs can compare pixel for pixel.
"""
import json
import sys

import numpy as np

sys.path.insert(0, "/app/core")
from aperture_core.masks import encode_rle, decode_rle  # noqa: E402

rng = np.random.default_rng(7)


def blob(h, w, cy, cx, ry, rx):
    y, x = np.ogrid[:h, :w]
    return (((y - cy) / ry) ** 2 + ((x - cx) / rx) ** 2 <= 1).astype(np.uint8)


cases = {}

# a plain ellipse
cases["ellipse"] = blob(120, 200, 60, 100, 40, 80)

# touching every edge - runs that start and end on a boundary
m = np.ones((60, 90), dtype=np.uint8)
cases["full_frame"] = m

# foreground in the very first pixel: the encoder has to emit a leading zero run
m = np.zeros((40, 40), dtype=np.uint8)
m[0, 0] = 1
cases["first_pixel"] = m

# foreground in the very last pixel
m = np.zeros((40, 40), dtype=np.uint8)
m[-1, -1] = 1
cases["last_pixel"] = m

# a ring: interior background surrounded by foreground
outer = blob(150, 150, 75, 75, 60, 60)
inner = blob(150, 150, 75, 75, 25, 25)
cases["ring"] = (outer & ~inner).astype(np.uint8)

# single-pixel-wide diagonal, the worst case for run lengths
m = np.zeros((100, 100), dtype=np.uint8)
m[np.arange(100), np.arange(100)] = 1
cases["diagonal"] = m

# two disjoint components
m = np.zeros((100, 160), dtype=np.uint8)
m |= blob(100, 160, 30, 40, 18, 22)
m |= blob(100, 160, 70, 120, 20, 25)
cases["two_blobs"] = m

# noise: every run length is short
cases["salt"] = (rng.random((80, 130)) > 0.5).astype(np.uint8)

# empty
cases["empty"] = np.zeros((32, 32), dtype=np.uint8)

# a tall thin strip (column-major runs are long here)
m = np.zeros((200, 60), dtype=np.uint8)
m[:, 28:31] = 1
cases["strip"] = m

out = []
for name, mask in cases.items():
    encoded = encode_rle(mask)
    # the server's own decode must round-trip first, or the case is meaningless
    assert np.array_equal(decode_rle(encoded["counts"], encoded["size"]), mask), name
    ys, xs = np.nonzero(mask)
    out.append({
        "name": name,
        "size": encoded["size"],
        "counts": encoded["counts"],
        "area": encoded["area"],
        # ground truth as flat row-major indices, which is what the canvas uses
        "pixels": sorted(int(y) * mask.shape[1] + int(x) for y, x in zip(ys, xs)),
    })

print(json.dumps(out))
