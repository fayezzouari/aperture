import { useRef, useState } from "react";
import { PanelHead } from "../components/PanelHead";
import { SectionHead } from "../components/SectionHead";

const processes = [
  { name: "api", body: "Registry and inference gateway in one process, so resolving a tag costs no network hop. Stateless; scale with replicas." },
  { name: "runtime ×N", body: "Holds model versions resident, batches requests across callers, reports what it holds every few seconds. The only process that loads weights." },
  { name: "worker", body: "Bulk and video jobs, conversion, exports, and the cron ticks that reconcile the fleet, evaluate alerts and expire captures." },
  { name: "logsink", body: "Drains the inference-log stream into Postgres. A database hiccup degrades observability, not serving." },
  { name: "stores", body: "Postgres for metadata, MinIO or S3 for weights and captures, Redis for the replica registry, tags, rate limits and the log stream." },
];

export function Run() {
  const pre = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState<"idle" | "done" | "fail">("idle");

  async function copy() {
    const text = pre.current?.innerText ?? "";
    try {
      await navigator.clipboard.writeText(text);
      setCopied("done");
    } catch {
      setCopied("fail");
    }
    setTimeout(() => setCopied("idle"), 1600);
  }

  return (
    <section id="run">
      <SectionHead title="Four processes, three stores, one compose file.">
        Everything runs on your hardware. The API is the registry and the gateway in one stateless process, runtimes hold
        weights on CPU or GPU, a worker takes everything that must not touch inference latency, and a log sink keeps a
        database hiccup from becoming a serving incident.
      </SectionHead>
      <div className="run">
        <div className="panel term">
          <PanelHead
            title="Quickstart"
            subtitle="Docker Compose, one host. Sign in as admin@localhost."
            action={
              <button type="button" className="btn btn-sm btn-ghost" onClick={copy} aria-label="Copy the quickstart commands">
                {copied === "idle" ? "Copy" : copied === "done" ? "Copied" : "Select and copy"}
              </button>
            }
          />
          <pre ref={pre}>
            <span className="p">git clone</span> &lt;this repo&gt; <span className="p">&amp;&amp; cd</span> aperture{"\n"}
            <span className="p">cp</span> .env.example .env{"\n"}
            <span className="c"># change the three CHANGE ME values</span>{"\n"}
            <span className="p">make</span> up{"\n"}
            <span className="c"># or: docker compose up -d --build</span>{"\n"}
            <span className="p">make</span> seed{"\n"}
            <span className="c"># optional: demo models with traffic in them</span>{"\n"}
            <span className="p">open</span> http://localhost:8080
          </pre>
        </div>
        <div className="rows">
          {processes.map((p) => (
            <div className="row" key={p.name}>
              <b>{p.name}</b>
              <span>{p.body}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
