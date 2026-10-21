import { SectionHead } from "../components/SectionHead";
import { useScrollProgress } from "../hooks/useScrollProgress";
import { heptagon } from "../lib/heptagon";
import { INFERENCE_MS } from "../lib/detections";

/* Three models as three rings closing on one point, in pipeline order: the
   outer ring settles first, the inner last, then the point lands. */
const rings = [
  { r: 270, rot: 0, from: 0.6 },
  { r: 190, rot: 14, from: 1.6 },
  { r: 115, rot: 28, from: 3.1 },
];

const legend = [
  { where: "outer ring", name: "fruit-bowl-seg", note: `finds each fruit in ${INFERENCE_MS} ms on CPU` },
  { where: "middle ring", name: "crop", note: "cuts each fruit out of the frame" },
  { where: "inner ring", name: "ripeness", note: "names how ripe it is" },
  { where: "centre", name: "one url", note: "fruit + ripeness, one call" },
];

export function Workflows() {
  const ref = useScrollProgress<HTMLElement>("enter", 0.85, 0.15);
  return (
    <section id="workflows" className="workflows" ref={ref}>
      <SectionHead label="03 — workflows" title={<>Three models.<br />One endpoint.</>}>
        One answer usually needs more than one model. Chain the fruit detector, a crop and a ripeness classifier into one
        URL, so products call one thing instead of re-implementing the pipeline.
      </SectionHead>
      <div className="nested">
        <svg viewBox="0 0 600 600" aria-hidden="true">
          {rings.map((ring) => (
            <g key={ring.r} className="nest" style={{ ["--k" as string]: ring.from }}>
              {[0.75, 0.4, 0.22, 0.12, 0.06].map((o, j) => (
                <polygon key={j} points={heptagon(300, 300, ring.r - j * 9, ring.rot + j * 3)} opacity={o} />
              ))}
            </g>
          ))}
          <g className="point">
            <circle cx="300" cy="300" r="44" opacity="0.25" />
            <circle cx="300" cy="300" r="30" opacity="0.5" />
            <circle cx="300" cy="300" r="16" className="dot" />
          </g>
        </svg>
        <ol className="legend">
          {legend.map((row, i) => (
            <li key={row.name} style={{ ["--i" as string]: i }} className={i === legend.length - 1 ? "last" : undefined}>
              <span className="label muted">{row.where}</span>
              <b>{row.name}</b>
              <span>{row.note}</span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
