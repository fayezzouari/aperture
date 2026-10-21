import { fade, heptagon } from "../lib/heptagon";

/* The hero's opening: rings of heptagons widening and twisting outward from an
   empty centre. One ring, fourth from the opening, is the production version
   and stays bright. Drawn in a 1440×1700 box so the outer rings run on below
   the hero and fade out behind the next section instead of being cut.

   The rings are split into a few stacked <svg> layers rather than transformed
   one by one: each layer is its own compositor layer, so scrolling moves
   pixels the GPU already has instead of repainting 44 polygons every frame.
   Outer layers turn a little further than inner ones, which keeps the twist. */
const CX = 720;
const CY = 500;
const RINGS = 44;
const LAYERS = 4;
export const PRODUCTION_RING = 3;

function rings(from: number, to: number) {
  const out = [];
  for (let i = from; i < to; i++) {
    const production = i === PRODUCTION_RING;
    out.push(
      <polygon
        key={i}
        className={production ? "ring production" : "ring"}
        points={heptagon(CX, CY, 350 + i * 17, i * 3)}
        opacity={production ? 1 : fade(i, 26)}
      />,
    );
  }
  return out;
}

export function Spiral({ className }: { className?: string }) {
  const per = Math.ceil(RINGS / LAYERS);
  return (
    <div className={className ? `spiral ${className}` : "spiral"} aria-hidden="true">
      {Array.from({ length: LAYERS }, (_, l) => (
        <svg key={l} className="layer" style={{ ["--l" as string]: l }} viewBox="0 0 1440 1700" preserveAspectRatio="xMidYMin slice">
          {rings(l * per, Math.min(RINGS, (l + 1) * per))}
        </svg>
      ))}
    </div>
  );
}
