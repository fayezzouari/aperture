import { PanelHead } from "../components/PanelHead";
import { SectionHead } from "../components/SectionHead";
import { shots } from "../components/Shot";
import { useInView } from "../hooks/useInView";

/* One run of the damage-report workflow, from the README. */
const stages = [
  { name: "Detect panels", ms: 231.4 },
  { name: "Crop each panel", ms: 57.1 },
  { name: "Classify damage", ms: 713.2 },
];
const total = stages.reduce((a, s) => a + s.ms, 0);

export function Workflows() {
  const { ref, inView } = useInView<HTMLDivElement>();
  return (
    <section id="workflows">
      <SectionHead title="One answer usually needs more than one model.">
        “Which panel is damaged, and how badly” is a detector, a crop and a classifier that also wants the box geometry.
        Chained by hand, every product re-implements that, and each one drifts.
      </SectionHead>
      <div ref={ref} className={`split${inView ? " in-view" : ""}`}>
        <div>
          <p className="lead">
            A workflow is that chain, drawn once on a canvas and served as one endpoint. Every node is timed, so a slow
            workflow says which stage is slow. Publishing snapshots the graph as a numbered revision, recorded on every
            run. A tag inside a workflow still resolves per call, so a model rollback moves the workflow with it.
          </p>
          <div className="pipeline" aria-label="Per-node timings from one run">
            {stages.map((s) => (
              <div className="stage-row" key={s.name}>
                <span className="n">{s.name}</span>
                <span className="ms">{s.ms.toFixed(1)} ms</span>
                <span className="bar">
                  <i style={{ ["--w" as string]: `${Math.round((s.ms / total) * 100)}%` }} />
                </span>
              </div>
            ))}
            <div className="stage-row">
              <span className="n">total</span>
              <span className="ms">{total.toFixed(1)} ms · 2 model calls</span>
            </div>
          </div>
        </div>
        <div className="panel frame">
          <PanelHead title="Workflow editor" subtitle="Build is the canvas; Try it runs the graph on an image and shows every stage's output and timing." />
          <img
            src={shots.workflowEditor}
            width={2880}
            height={1800}
            loading="lazy"
            alt="The workflow editor: an input node fanning out to five gates, five model nodes and five result nodes on a canvas."
          />
        </div>
      </div>
    </section>
  );
}
