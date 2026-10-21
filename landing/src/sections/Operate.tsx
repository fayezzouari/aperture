import { useEffect, useRef, useState } from "react";
import { SectionHead } from "../components/SectionHead";
import { heptagon } from "../lib/heptagon";

const questions = [
  { q: "Is anything on fire?", a: "Traffic, error rate, p95, replicas ready, recent failures and firing alerts.", where: "overview" },
  { q: "Why was this prediction wrong?", a: "Filter by model, version, outcome or confidence. Captured requests draw predictions back over the image.", where: "inferences" },
  { q: "Which stage is slow?", a: "A latency waterfall on every request: queue, preprocess, inference, postprocess, gateway.", where: "overview" },
  { q: "Is the fleet healthy?", a: "Every runtime, what it holds, queue depth and GPU memory. Stale runtimes are re-placed automatically.", where: "fleet" },
  { q: "Who calls what, how much?", a: "Usage by model, version, consumer or day. CSV and PDF, schedulable.", where: "reports" },
  { q: "Who changed what?", a: "Every upload, publish, retag, deploy, key and purge, append-only.", where: "audit" },
];

const guarantees = [
  { title: "No outbound calls", body: "Nothing calls out to the internet while serving. Air-gapped installs are a documented path." },
  { title: "Encryption at rest", body: "Weights and captured images sit in your own S3-compatible store, encrypted where the store supports it." },
  { title: "Retention you set", body: "Captured requests expire on your schedule. Purges are jobs, and every purge is audited." },
  { title: "Roboflow-shaped", body: "Import a Roboflow project in one step and return results in its shape, so clients keep working." },
];

/* Each question's mark turns once, 30°, when the grid enters: not scrubbed. */
function Mark({ i }: { i: number }) {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true">
      {[20, 15, 10, 5].map((r, j) => (
        <polygon
          key={r}
          className={j === 0 ? "outer" : undefined}
          style={{ ["--j" as string]: j + 1 }}
          points={heptagon(24, 24, r, i * 9 + j * 10)}
          opacity={[1, 0.5, 0.28, 0.14][j]}
        />
      ))}
    </svg>
  );
}

export function Operate() {
  const ref = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || !("IntersectionObserver" in window)) return setSeen(true);
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setSeen(true);
        io.disconnect();
      }
    }, { threshold: 0.25 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <section id="operate" className="operate">
      <SectionHead label="04 — operate" title={<>Built for the night<br />something is on fire.</>}>
        Each operational question has one place to look. Prometheus on every process, OpenTelemetry one flag away.
      </SectionHead>
      <div ref={ref} className={seen ? "questions seen" : "questions"}>
        {questions.map((item, i) => (
          <div className="question" key={item.q} style={{ ["--i" as string]: i }}>
            <Mark i={i} />
            <h3>{item.q}</h3>
            <p>{item.a}</p>
            <span className="where">{item.where}</span>
          </div>
        ))}
      </div>
      <div className="guarantees">
        {guarantees.map((g) => (
          <div key={g.title}>
            <h3>{g.title}</h3>
            <p>{g.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
