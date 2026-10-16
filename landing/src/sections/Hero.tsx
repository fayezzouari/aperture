import { useEffect, useState } from "react";
import { PanelHead } from "../components/PanelHead";
import { shots } from "../components/Shot";
import { useReducedMotion } from "../hooks/useReducedMotion";

/* What `production` resolves to over one deploy-and-rollback cycle. The URL
   above never changes; that is the point. */
const states = [
  { version: "v7", tone: "good", status: "ready", note: "warmed on 2 replicas · p95 61 ms" },
  { version: "v8", tone: "warn", status: "warming", note: "warming on 2 replicas · traffic still on v7" },
  { version: "v8", tone: "good", status: "ready", note: "traffic moved · p95 58 ms" },
  { version: "v7", tone: "good", status: "ready", note: "rolled back by retag · 0 clients changed" },
] as const;

function RequestPanel() {
  const reduce = useReducedMotion();
  const [i, setI] = useState(0);
  const [out, setOut] = useState(false);

  useEffect(() => {
    if (reduce) return;
    const id = setInterval(() => {
      setOut(true);
      setTimeout(() => {
        setI((n) => (n + 1) % states.length);
        setOut(false);
      }, 260);
    }, 3200);
    return () => clearInterval(id);
  }, [reduce]);

  const s = states[i];
  return (
    <div className="panel request" aria-label="Example request">
      <PanelHead
        title="The URL products call"
        subtitle="A tag, not a version. Aperture resolves it on every request."
        action={
          <span className={`status ${s.tone}`}>
            <i /> {s.status}
          </span>
        }
      />
      <pre>
        <span className="kw">POST</span> /v1/models/part-detection/<span className="badge badge-tag">production</span>/predict{"\n"}
        <span className="note">x-api-key: ap_3f9c21d0…</span>{"\n\n"}
        <span className="note">production →</span>{" "}
        <span className={`swap${out ? " out" : ""}`}>
          <span className={`ver ${s.tone}`}>{s.version}</span> <span className="note">· {s.note}</span>
        </span>
      </pre>
    </div>
  );
}

export function Hero() {
  return (
    <section className="hero">
      <h1>Serve vision models the way you ship software.</h1>
      <p className="lede">
        Aperture is self-hosted serving, versioning and observability for computer-vision models. Upload weights, prove
        the config on a reference image, deploy, and give every product one URL that never changes.
      </p>
      <div className="actions">
        <a className="btn btn-primary" href="#run">
          Run it locally
        </a>
        <a className="btn" href="#ideas">
          See how it works
        </a>
      </div>

      <RequestPanel />

      <figure className="panel shot" aria-label="Aperture overview page">
        <PanelHead title="Overview" subtitle="How the instance is behaving right now. Last 24 hours, refreshed every 15 seconds." />
        <img
          className="iris"
          src={shots.overview}
          width={2880}
          height={1800}
          alt="The Aperture overview: requests, error rate, p95 latency, replicas ready, latency over time and recent failures across the instance."
        />
      </figure>
    </section>
  );
}
