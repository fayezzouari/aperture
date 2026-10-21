import { useState } from "react";
import { SectionHead } from "../components/SectionHead";
import { Spiral } from "../components/Spiral";

const steps = [
  { cmd: "git clone https://github.com/fayezzouari/aperture && cd aperture" },
  { cmd: "cp .env.example .env", note: "change the three CHANGE ME values" },
  { cmd: "make up", note: "or docker compose up -d --build" },
  { cmd: "make seed", note: "optional: a demo model, deployed" },
  { cmd: "open http://localhost:8080", note: "sign in as admin@localhost" },
];

export function Run() {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(steps.map((s) => s.cmd).join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard blocked: the commands stay selectable */
    }
  };

  return (
    <section id="run" className="run">
      <Spiral className="spiral echo" />
      <SectionHead label="05 — run it" title={<>One compose file.<br />Your hardware.</>}>
        Four processes, three stores, one host. Postgres for metadata, MinIO or S3 for weights, Redis for tags and the log
        stream.
      </SectionHead>
      <div className="steps">
        <ol>
          {steps.map((s, i) => (
            <li key={s.cmd} className={i === steps.length - 1 ? "last" : undefined}>
              <span className="n">{String(i + 1).padStart(2, "0")}</span>
              <code>{s.cmd}</code>
              {s.note && <span className="note">{s.note}</span>}
            </li>
          ))}
        </ol>
        <button type="button" className="btn-text" onClick={copy}>
          {copied ? "Copied" : "Copy the commands"}
        </button>
      </div>
    </section>
  );
}
