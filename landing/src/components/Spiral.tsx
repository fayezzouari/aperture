import { fade, heptagon } from "../lib/heptagon";

/* The hero's opening: rings of heptagons widening and twisting outward from an
   empty centre. One ring, fourth from the opening, is the production version
   and stays bright. Drawn in a 1440×1700 box so the outer rings run on below
   the hero and fade out behind the next section instead of being cut. */
const CX = 720;
const CY = 500;
const RINGS = 44;
export const PRODUCTION_RING = 3;

export function Spiral({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 1440 1700" preserveAspectRatio="xMidYMin slice" aria-hidden="true">
      {Array.from({ length: RINGS }, (_, i) => {
        const production = i === PRODUCTION_RING;
        return (
          <polygon
            key={i}
            className={production ? "ring production" : "ring"}
            style={{ ["--i" as string]: i }}
            points={heptagon(CX, CY, 350 + i * 17, i * 3)}
            opacity={production ? 1 : fade(i, 26)}
          />
        );
      })}
    </svg>
  );
}
