# Mask fidelity

Masks travel as COCO column-major RLE and are decoded **in the browser**, so the
format has two implementations — `aperture_core.masks` and `frontend/src/lib/masks.ts`
— and "close enough" is not good enough: a mask is the model's answer, and a
viewer that redraws it approximately is lying about the model.

```bash
scripts/mask-fidelity/run.sh
```

`cases.py` builds masks that break sloppy RLE code — foreground in the first and
last pixel, a full frame, a ring, a one-pixel diagonal, salt noise, an empty mask
— encodes each with the server encoder, and asserts the server's own decode
round-trips. `check.mjs` bundles the real frontend module, paints each mask, and
compares the painted pixels with the ground truth one pixel at a time (and the
colour, since an exact shape in the wrong colour is not exact either).

Two things this does *not* cover, both measured separately:

- **Canvas resolution.** Masks are drawn 1:1 up to 12 MP; above that the grid is
  coarsened (a 24 MP photo draws at step 2), which can drop a one-pixel feature.
  The buffer still stays far finer than any screen showing it.
- **Polygon format.** `mask_format: "polygon"` is a compact, lossy
  representation by design — traced on a copy capped at 256 px and simplified.
  Measured IoU against the exact mask: 0.98 for a blob, 0.98 for a ring
  (its holes are emitted as separate rings and filled even-odd), 0.83 for a
  6-pixel-wide strip. Use `rle` when the shape has to be exact.
