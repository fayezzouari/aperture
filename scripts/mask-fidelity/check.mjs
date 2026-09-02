/**
 * Does the browser draw the mask the server sent, exactly?
 *
 * This runs the real frontend module - not a copy of it - against masks encoded
 * by the real server encoder, and compares the painted pixels with the ground
 * truth one pixel at a time. Anything less would only prove the two
 * implementations are close.
 */
import { readFileSync } from "node:fs";
import { createMaskLayer, paintRle } from "./.build/masks.mjs";

const cases = JSON.parse(readFileSync(process.argv[2], "utf8"));
let failures = 0;

for (const item of cases) {
  const [height, width] = item.size;
  const layer = createMaskLayer(width, height);
  paintRle(layer, { format: "rle", size: item.size, counts: item.counts },
           [255, 128, 64], 0.5, 1);

  const painted = [];
  for (let index = 0; index < width * height; index += 1) {
    if (layer.pixels[index * 4 + 3] > 0) painted.push(index);
  }

  const expected = item.pixels;
  const missing = expected.filter((p) => layer.pixels[p * 4 + 3] === 0);
  const extraSet = new Set(expected);
  const extra = painted.filter((p) => !extraSet.has(p));

  const ok = missing.length === 0 && extra.length === 0;
  if (!ok) failures += 1;
  const label = `${item.name} ${width}x${height}`.padEnd(26);
  console.log(
    `${ok ? "PASS " : "FAIL "} ${label} expected ${String(expected.length).padStart(6)} px` +
    `  painted ${String(painted.length).padStart(6)} px` +
    (ok ? "" : `  missing ${missing.length}  extra ${extra.length}`)
  );

  // the colour has to survive too: an exact shape in the wrong colour is not
  // "exactly as returned" either
  if (ok && expected.length) {
    const p = expected[0] * 4;
    const rgb = [layer.pixels[p], layer.pixels[p + 1], layer.pixels[p + 2]];
    if (rgb.join(",") !== "255,128,64") {
      console.log(`       colour drifted: ${rgb.join(",")}`);
      failures += 1;
    }
  }
}

console.log(`\n${cases.length - failures}/${cases.length} masks drawn exactly`);
process.exit(failures ? 1 : 0);
