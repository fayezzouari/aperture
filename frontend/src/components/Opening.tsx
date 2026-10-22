/* The landing page's opening (landing/src/components/Spiral.tsx): rings of
   heptagons widening and twisting outward from an empty centre, the fourth one
   bright. Drawn behind the sign-in form, which sits in the centre. */
const CX = 960;
const CY = 540;
const RINGS = 44;
const LAYERS = 4;
const PRODUCTION_RING = 3;

function heptagon(r: number, rot: number): string {
  const points: string[] = [];
  for (let k = 0; k < 7; k++) {
    const a = ((rot + (k * 360) / 7 - 90) * Math.PI) / 180;
    points.push(`${(CX + r * Math.cos(a)).toFixed(1)},${(CY + r * Math.sin(a)).toFixed(1)}`);
  }
  return points.join(" ");
}

/** Strongest at the opening, fading outward. */
function fade(i: number): number {
  return Math.max(0.04, 0.85 * Math.max(0, 1 - i / 26) ** 1.7);
}

export function Opening() {
  const per = Math.ceil(RINGS / LAYERS);
  return (
    <svg className="opening" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      {Array.from({ length: LAYERS }, (_, l) => (
        <g key={l} className="opening-layer" style={{ ["--l" as string]: l }}>
          {Array.from({ length: Math.min(RINGS, (l + 1) * per) - l * per }, (_, j) => {
            const i = l * per + j;
            const production = i === PRODUCTION_RING;
            return (
              <polygon
                key={i}
                className={production ? "opening-ring production" : "opening-ring"}
                points={heptagon(350 + i * 17, i * 3)}
                opacity={production ? 1 : fade(i)}
              />
            );
          })}
        </g>
      ))}
    </svg>
  );
}
