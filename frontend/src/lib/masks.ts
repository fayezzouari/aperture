/**
 * Drawing the masks a model actually returns.
 *
 * Aperture encodes masks as COCO-style RLE by default, because it is compact and
 * lossless — but that means a viewer has to decode it. The runs are column-major
 * and start with a background run, matching `aperture_core.masks.encode_rle`, so
 * the two implementations have to agree exactly.
 *
 * Only foreground pixels are written: the background runs are skipped rather
 * than painted, which keeps a full-resolution instance mask in the low
 * milliseconds instead of touching every pixel of the image.
 */

export type MaskPayload = {
  format?: "rle" | "polygon" | string;
  size?: number[];
  counts?: string | null;
  polygons?: number[][] | null;
  area?: number | null;
};

/**
 * Above this, masks are drawn on a coarser grid than the image.
 *
 * That is a lie about the model's output, so the ceiling sits above the sizes a
 * phone or a DSLR actually produces (a 2448x3264 photo is 8 MP) — those draw
 * 1:1, pixel for pixel. Dropping every second row and column does not merely
 * soften an edge: a one-pixel feature lands on a skipped row and disappears
 * completely. Only a genuinely huge image (>12 MP) is coarsened, and even at
 * step 3 the buffer stays far finer than any screen it is shown on.
 */
const MAX_CANVAS_PIXELS = 12_000_000;

export function canvasStep(width: number, height: number): number {
  const pixels = Math.max(1, width * height);
  return Math.max(1, Math.ceil(Math.sqrt(pixels / MAX_CANVAS_PIXELS)));
}

/**
 * A shared drawing surface for one image's masks.
 *
 * Filling overlapping translucent shapes stacks into mush once there are more
 * than a handful, and a fill alone gives the eye nothing to follow. So every
 * mask is drawn twice: a translucent fill, then an opaque one-pixel edge where
 * it stops. The edge is what makes a mask read as a shape.
 *
 * `owner` records which instance painted each pixel, which is how the edge is
 * found without a second decode: a foreground pixel whose four neighbours are
 * not the same instance is a boundary pixel. Stamps are per instance and never
 * cleared, so painting N masks costs two passes over their own pixels rather
 * than N passes over the image.
 */
export type MaskLayer = {
  pixels: Uint8ClampedArray;
  owner: Uint16Array;
  width: number;
  height: number;
  stamp: number;
};

export function createMaskLayer(width: number, height: number): MaskLayer {
  return {
    pixels: new Uint8ClampedArray(width * height * 4),
    owner: new Uint16Array(width * height),
    width,
    height,
    stamp: 0,
  };
}

/**
 * Reuse a layer rather than allocating one per repaint.
 *
 * A full-resolution 8 MP layer is 48 MB across the two buffers, and repaints
 * happen on every hover — allocating that each time stutters. Both buffers are
 * cleared: leaving stale pixels would let a mask that has since been filtered
 * out keep showing, and leaving stale owners would let it keep answering the
 * pointer.
 */
export function reuseMaskLayer(
  layer: MaskLayer | null,
  width: number,
  height: number
): MaskLayer {
  if (!layer || layer.width !== width || layer.height !== height) {
    return createMaskLayer(width, height);
  }
  layer.pixels.fill(0);
  layer.owner.fill(0);
  layer.stamp = 0;
  return layer;
}

/**
 * Paint one RLE mask into the layer.
 *
 * `step` downsamples: with step 2 every second row and column is written, which
 * is what keeps a huge image affordable without a separate resize pass.
 */
export function paintRle(
  layer: MaskLayer,
  mask: MaskPayload,
  color: [number, number, number],
  alpha: number,
  step = 1,
  outline = true
): void {
  if (!mask.counts || !mask.size || mask.size.length < 2) return;
  const [height, width] = [mask.size[0], mask.size[1]];
  const { pixels, owner, width: bufferWidth, height: bufferHeight } = layer;
  const [red, green, blue] = color;
  const fill = Math.round(Math.min(1, Math.max(0, alpha)) * 255);
  // the edge carries the shape, so it stays legible when the fill is faint
  const edge = Math.round(Math.min(1, Math.max(0, alpha * 1.9 + 0.25)) * 255);
  const stamp = (layer.stamp += 1);
  if (stamp > 65000) return;

  const touched: number[] = [];
  let index = 0;
  let foreground = false;
  for (const token of mask.counts.split(" ")) {
    const length = Number(token);
    if (!Number.isFinite(length)) continue;
    if (foreground && length > 0) {
      const end = index + length;
      for (let flat = index; flat < end; flat += 1) {
        // column-major: the run walks down a column, then moves right
        const row = flat % height;
        if (row % step) continue;
        const column = (flat / height) | 0;
        if (column % step) continue;
        const x = (column / step) | 0;
        const y = (row / step) | 0;
        if (x >= bufferWidth || y >= bufferHeight) continue;
        const at = y * bufferWidth + x;
        owner[at] = stamp;
        touched.push(at);
        const offset = at * 4;
        pixels[offset] = red;
        pixels[offset + 1] = green;
        pixels[offset + 2] = blue;
        pixels[offset + 3] = fill;
      }
    }
    index += length;
    foreground = !foreground;
    if (index > height * width) break;
  }

  if (!outline) return;
  for (const at of touched) {
    const x = at % bufferWidth;
    const y = (at / bufferWidth) | 0;
    const boundary =
      x === 0 || y === 0 || x === bufferWidth - 1 || y === bufferHeight - 1 ||
      owner[at - 1] !== stamp || owner[at + 1] !== stamp ||
      owner[at - bufferWidth] !== stamp || owner[at + bufferWidth] !== stamp;
    if (boundary) pixels[at * 4 + 3] = edge;
  }
}

/**
 * Polygon masks go straight to a 2D path — no decode needed.
 *
 * Every ring of one mask goes into a *single* path filled even-odd, because a
 * mask can have holes: the encoder emits enclosed background pockets as their
 * own rings, and filling each ring separately would paint the holes back in.
 */
export function fillPolygons(
  context: CanvasRenderingContext2D,
  mask: MaskPayload,
  color: string,
  step = 1,
  edge?: string
): void {
  const rings = (mask.polygons ?? []).filter((ring) => ring.length >= 6);
  if (!rings.length) return;

  context.beginPath();
  for (const ring of rings) {
    context.moveTo(ring[0] / step, ring[1] / step);
    for (let i = 2; i < ring.length - 1; i += 2) {
      context.lineTo(ring[i] / step, ring[i + 1] / step);
    }
    context.closePath();
  }
  context.fillStyle = color;
  context.fill("evenodd");
  if (edge) {
    // same reason as the RLE path: the outline is what makes it a shape
    context.strokeStyle = edge;
    context.lineWidth = 1;
    context.stroke();
  }
}

/** The same hue rule the server-side renderer uses, so overlays match. */
export function classColor(classId: number): [number, number, number] {
  const hue = ((classId * 0.6180339887) % 1) * 360;
  return hslToRgb(hue, 0.72, 0.62);
}

export function classColorCss(classId: number, alpha = 1): string {
  const [r, g, b] = classColor(classId);
  return alpha >= 1 ? `rgb(${r} ${g} ${b})` : `rgb(${r} ${g} ${b} / ${alpha})`;
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const chroma = (1 - Math.abs(2 * l - 1)) * s;
  const sector = h / 60;
  const x = chroma * (1 - Math.abs((sector % 2) - 1));
  const [r1, g1, b1] =
    sector < 1 ? [chroma, x, 0] :
    sector < 2 ? [x, chroma, 0] :
    sector < 3 ? [0, chroma, x] :
    sector < 4 ? [0, x, chroma] :
    sector < 5 ? [x, 0, chroma] : [chroma, 0, x];
  const m = l - chroma / 2;
  return [
    Math.round((r1 + m) * 255),
    Math.round((g1 + m) * 255),
    Math.round((b1 + m) * 255),
  ];
}
