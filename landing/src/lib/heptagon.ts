/** Points of a regular heptagon, first vertex straight up, rotated by `rot` degrees.
 *  The page's one shape: an aperture's opening, drawn as seven straight blades. */
export function heptagon(cx: number, cy: number, r: number, rot = 0): string {
  const points: string[] = [];
  for (let k = 0; k < 7; k++) {
    const a = ((rot + (k * 360) / 7 - 90) * Math.PI) / 180;
    points.push(`${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`);
  }
  return points.join(" ");
}

/** Line opacity that is strongest at the opening and fades outward. */
export function fade(i: number, span: number, floor = 0.04, peak = 0.85): number {
  return Math.max(floor, peak * Math.max(0, 1 - i / span) ** 1.7);
}
