import { useState, type KeyboardEvent } from "react";
import { PanelHead } from "../components/PanelHead";
import { SectionHead } from "../components/SectionHead";
import { shots, type ShotKey } from "../components/Shot";

type Page = { key: ShotKey; title: string; subtitle: string; alt: string };

const pages: Page[] = [
  { key: "models", title: "Models", subtitle: "Every model in the workspace, what is live and how it is behaving. Import from Roboflow or upload weights.", alt: "The Models page: a table of models with task, tags, live version, replicas, requests, errors and p95." },
  { key: "modelDetail", title: "Versions", subtitle: "Published weights are immutable. Upload, publish, tag and deploy; a change is always a new version.", alt: "A model's versions: two published ONNX versions, with the production tag on one of them." },
  { key: "workflows", title: "Workflows", subtitle: "Chain several models into one endpoint so products call one URL instead of re-implementing the pipeline.", alt: "The Workflows page: a published workflow with the models it chains, plus a curl example." },
  { key: "jobs", title: "Jobs", subtitle: "Long work runs here, never on the inference path: conversion, imports, bulk folders, video, exports.", alt: "The Jobs page: export jobs with status, result and duration." },
  { key: "fleet", title: "Fleet", subtitle: "Runtimes hold model versions resident and pull their assignments on every heartbeat. Scaling needs no restart.", alt: "The Fleet page: runtime cards with slots used, execution provider and last heartbeat." },
  { key: "keys", title: "API keys", subtitle: "One key per product, scoped to the models that product actually calls, with a rate limit and a monthly quota.", alt: "The API keys page: keys with prefix, scope, rate limit, usage and last used." },
  { key: "audit", title: "Audit", subtitle: "Every upload, publish, retag, deploy, key issuance and purge. Append-only, with actor and client IP.", alt: "The Audit log: timestamped actions with actor, target, detail and IP." },
];

export function Tour() {
  const [active, setActive] = useState(0);
  const page = pages[active];

  function onKey(e: KeyboardEvent<HTMLButtonElement>, k: number) {
    let next: number | null = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (k + 1) % pages.length;
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (k - 1 + pages.length) % pages.length;
    if (next === null) return;
    e.preventDefault();
    setActive(next);
    (e.currentTarget.parentElement?.children[next] as HTMLElement | undefined)?.focus();
  }

  return (
    <section id="tour" className="tour">
      <SectionHead title="An operations console, read at 2am.">
        The visual system is deliberately quiet. Colour is reserved for state, numbers sit in a monospace face, and every
        page answers one operational question.
      </SectionHead>
      <div className="panel">
        <PanelHead
          title={page.title}
          subtitle={page.subtitle}
          action={
            <div className="segmented" role="tablist" aria-label="Console pages">
              {pages.map((p, k) => (
                <button
                  key={p.key}
                  type="button"
                  role="tab"
                  aria-selected={k === active}
                  aria-controls="tour-stage"
                  tabIndex={k === active ? 0 : -1}
                  onClick={() => setActive(k)}
                  onKeyDown={(e) => onKey(e, k)}
                >
                  {p.title}
                </button>
              ))}
            </div>
          }
        />
        <div className="stage" id="tour-stage" role="tabpanel">
          {pages.map((p, k) => (
            <img key={p.key} className={k === active ? "on" : undefined} src={shots[p.key]} alt={p.alt} loading={k === 0 ? "eager" : "lazy"} />
          ))}
        </div>
      </div>
    </section>
  );
}
