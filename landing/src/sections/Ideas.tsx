import type { ReactNode } from "react";
import { SectionHead } from "../components/SectionHead";
import { useInView } from "../hooks/useInView";

/* One drawing vocabulary for all three figures, taken from the workflow
   editor's nodes: raised fill, hairline stroke, mono labels, the accent only
   on the edge that matters, state as a dot plus a word. */
const NODE = { fill: "#111113", stroke: "#2e2e33", rx: 7 } as const;
const QUIET = { fill: "#0a0a0a", stroke: "#232326", rx: 7 } as const;

function Idea({ title, figure, children, focusable }: { title: string; figure: ReactNode; children: ReactNode; focusable?: boolean }) {
  const { ref, inView } = useInView<HTMLElement>();
  return (
    <article ref={ref} className={`cell${inView ? " in-view" : ""}`} tabIndex={focusable ? 0 : undefined}>
      <figure aria-hidden="true">{figure}</figure>
      <h3>{title}</h3>
      <p>{children}</p>
    </article>
  );
}

function Dot({ x, y, color }: { x: number; y: number; color: string }) {
  return <circle cx={x} cy={y} r="3" fill={color} />;
}

function ConfigFigure() {
  return (
    <svg viewBox="0 0 300 124">
      <rect x="0.5" y="8.5" width="186" height="106" {...NODE} />
      <text className="svg-text" x="14" y="30">v7</text>
      <Dot x={40} y={26.5} color="#2bd97c" />
      <text className="svg-label" x="48" y="30">published · immutable</text>
      <line x1="14" y1="42" x2="172" y2="42" stroke="#232326" />
      <text className="svg-muted" x="14" y="62">resize    letterbox 640</text>
      <text className="svg-muted" x="14" y="80">decoder   yolo11</text>
      <text className="svg-muted" x="14" y="98">conf/iou  0.25 / 0.45</text>
      <path className="draw" d="M187 61 H 226" stroke="#0072f5" strokeWidth="1.5" fill="none" pathLength={1} />
      <rect x="226.5" y="44.5" width="73" height="34" rx="7" fill="#0a0a0a" stroke="#0072f5" />
      <text className="svg-text" x="242" y="65">config</text>
    </svg>
  );
}

function TagFigure() {
  return (
    <svg viewBox="0 0 300 124">
      <rect x="128.5" y="4.5" width="171" height="30" {...NODE} />
      <text className="svg-text" x="142" y="23">v7</text>
      <Dot x={168} y={19.5} color="#2bd97c" />
      <text className="svg-label" x="176" y="23">ready · 2 replicas</text>
      <rect x="128.5" y="40.5" width="171" height="30" {...NODE} />
      <text className="svg-text" x="142" y="59">v6</text>
      <Dot x={168} y={55.5} color="#2bd97c" />
      <text className="svg-label" x="176" y="59">ready · 2 replicas</text>
      <rect x="128.5" y="76.5" width="171" height="30" {...QUIET} />
      <text className="svg-muted" x="142" y="95">v5</text>
      <Dot x={168} y={91.5} color="#6e6e73" />
      <text className="svg-label" x="176" y="95">archived</text>
      <g className="pointer">
        <rect x="0.5" y="40.5" width="96" height="30" rx="7" fill="#0a0a0a" stroke="#0072f5" />
        <text className="svg-text" x="14" y="59">production</text>
        <path d="M97 55.5 H 122" stroke="#0072f5" strokeWidth="1.5" fill="none" />
        <path d="M117 51 L 123 55.5 L 117 60" stroke="#0072f5" strokeWidth="1.5" fill="none" />
      </g>
      <text className="svg-label" x="0" y="118">hover: retag to v7</text>
    </svg>
  );
}

function OverlayFigure() {
  const label = { font: "500 9px ui-monospace, SFMono-Regular, Menlo, monospace" } as const;
  return (
    <svg viewBox="0 0 300 124">
      <rect x="0.5" y="0.5" width="299" height="123" {...QUIET} fill="#0d0d0f" />
      <path d="M0.5 96 L 70 78 L 130 90 L 200 66 L 299.5 84 L 299.5 123 L 0.5 123 Z" fill="#141416" />
      <rect className="draw" x="40" y="34" width="82" height="52" rx="3" fill="none" stroke="#2bd97c" strokeWidth="1.5" pathLength={1} />
      <rect className="draw d2" x="140" y="22" width="60" height="60" rx="3" fill="none" stroke="#2bd97c" strokeWidth="1.5" pathLength={1} />
      <rect className="draw d3" x="218" y="46" width="68" height="40" rx="3" fill="none" stroke="#f5a524" strokeWidth="1.5" pathLength={1} />
      <rect x="40" y="22" width="82" height="12" rx="2" fill="#2bd97c" />
      <text x="44" y="31" style={label} fill="#04140a">door_front .94</text>
      <rect x="140" y="10" width="60" height="12" rx="2" fill="#2bd97c" />
      <text x="144" y="19" style={label} fill="#04140a">hood .91</text>
      <rect x="218" y="34" width="68" height="12" rx="2" fill="#f5a524" />
      <text x="222" y="43" style={label} fill="#1a1000">scratch .62</text>
    </svg>
  );
}

export function Ideas() {
  return (
    <section id="ideas">
      <SectionHead title="Three ideas worth knowing.">
        Everything else in Aperture follows from these. They are the reason a retrain does not need a coordinated deploy,
        and a rollback does not need a client change.
      </SectionHead>
      <div className="panel">
        <div className="grid-3">
          <Idea title="Config travels with the version" figure={<ConfigFigure />}>
            Preprocessing, decoder, thresholds and the class list live on the model version, not globally. A retrain that
            needs a different resize mode is a new version. Published versions are immutable, and the API refuses to edit
            one.
          </Idea>
          <Idea title="Tags resolve per request" figure={<TagFigure />} focusable>
            Products call <code>production</code>. Aperture resolves it on every call, so rolling back is repointing a tag:
            instant, no redeploy, no client change. A new version warms up on its own replicas first, and traffic moves
            only once something is ready to answer.
          </Idea>
          <Idea title="Validation is a rendered overlay, not a checkmark" figure={<OverlayFigure />}>
            Before a version can be published, Aperture runs the whole pipeline on a reference image through a real runtime
            and stores the overlay. A letterbox that should have been a stretch, a class list off by one, BGR read as RGB:
            these look wrong in the overlay and silent in a status field.
          </Idea>
        </div>
      </div>
    </section>
  );
}
