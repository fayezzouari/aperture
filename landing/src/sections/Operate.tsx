import { SectionHead } from "../components/SectionHead";

const questions = [
  { q: "Is anything on fire?", a: "Traffic, error rate, p95, replicas ready, recent failures and firing alerts.", where: ["Overview"] },
  { q: "Why was this prediction wrong?", a: "Filter by model, version, outcome, latency or confidence. Captured requests draw the predictions back over the image.", where: ["Inferences"] },
  { q: "Which stage is slow?", a: "A latency waterfall on every request: queue, preprocess, inference, postprocess, gateway.", where: ["Overview", "Inferences"] },
  { q: "Is the fleet healthy?", a: "Every runtime, what it holds, queue depth and GPU memory. Stale runtimes are re-placed automatically.", where: ["Fleet"] },
  { q: "Who calls what, how much?", a: "Usage by model, version, consumer or day. CSV and PDF, schedulable.", where: ["Reports"] },
  { q: "Who changed what?", a: "Every upload, publish, retag, deploy, key and purge, append-only.", where: ["Audit"] },
];

const guarantees = [
  { k: "Runtime", title: "No outbound calls", body: "Nothing calls out to the internet while serving. Air-gapped installs are a documented path, not an exception." },
  { k: "Storage", title: "Encryption at rest", body: "Weights and captured images sit in your own S3-compatible store, with server-side encryption where the store supports it." },
  { k: "Privacy", title: "Retention you set", body: "Captured requests expire on a schedule you choose. Purges are jobs, and every purge is audited." },
  { k: "Compatibility", title: "Roboflow-shaped where it helps", body: "Import a Roboflow project in one step and return results in Roboflow's shape, so existing clients keep working." },
];

export function Operate() {
  return (
    <section id="operate" className="operate">
      <SectionHead title="Built for the night something is on fire.">
        Each operational question has one place to look. Prometheus metrics on every process, an OpenTelemetry collector
        and Jaeger one flag away, and an inbound traceparent honoured so an inference span joins the calling product's
        trace.
      </SectionHead>
      <div className="panel">
        <div className="grid-2">
          {questions.map((x) => (
            <div className="cell" key={x.q}>
              <h3>{x.q}</h3>
              <p>{x.a}</p>
              <span style={{ display: "flex", gap: 6 }}>
                {x.where.map((w) => (
                  <span className="badge" key={w}>
                    {w}
                  </span>
                ))}
              </span>
            </div>
          ))}
        </div>
      </div>
      <div className="panel">
        <div className="grid-4">
          {guarantees.map((g) => (
            <div className="cell" key={g.k}>
              <span className="eyebrow">{g.k}</span>
              <h3>{g.title}</h3>
              <p>{g.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
