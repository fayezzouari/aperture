/**
 * The guide's building blocks. They borrow the console's own vocabulary - panels,
 * hairline rows, badges, mono labels - so a screenshot and the prose around it
 * read as one surface.
 */
import { useEffect, useState, type ReactNode } from "react";

export type Section = { id: string; title: string; body: ReactNode };
export type Chapter = { id: string; group: string; title: string; lede: ReactNode; sections: Section[] };

/** All guide screenshots: 1440×900 viewports at 2×, WebP, captured by scripts/guide_screenshots.py. */
const SHOT_DIR = "/screenshots/guide";

/** A console screenshot in a panel whose header shows the page it was taken on. */
export function Shot({ name, path, caption, alt }: { name: string; path: string; caption?: ReactNode; alt: string }) {
  const [open, setOpen] = useState(false);
  const src = `${SHOT_DIR}/${name}.webp`;

  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open]);

  return (
    <figure className="shot-frame">
      <div className="panel">
        <div className="shot-bar">
          <span className="dots" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span className="mono">localhost:8080{path}</span>
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setOpen(true)} aria-label={`Enlarge: ${alt}`}>
            Enlarge
          </button>
        </div>
        <button type="button" className="shot-img" onClick={() => setOpen(true)} aria-label={`Enlarge: ${alt}`}>
          {/* sized up front, so lazy loading never shifts the page under an #anchor */}
          <img src={src} alt={alt} width={2880} height={1800} loading="lazy" decoding="async" />
        </button>
      </div>
      {caption ? <figcaption>{caption}</figcaption> : null}
      {open ? (
        <div className="lightbox" role="dialog" aria-modal="true" aria-label={alt} onClick={() => setOpen(false)}>
          <img src={src} alt={alt} />
        </div>
      ) : null}
    </figure>
  );
}

/** Numbered steps: the console's hairline rows with a step counter. */
export function Steps({ children }: { children: ReactNode }) {
  return <ol className="steps">{children}</ol>;
}

/** A label exactly as the console renders it: a button, tab or menu item. */
export function Ui({ children }: { children: ReactNode }) {
  return <span className="ui">{children}</span>;
}

/** A form field's label, as shown above the input. */
export function FieldName({ children }: { children: ReactNode }) {
  return <span className="field-name">{children}</span>;
}

const ROLE_COPY = { viewer: "viewer", developer: "developer", admin: "admin" } as const;

/** Who can do this: the minimum role, in the workspace or on the instance. */
export function Role({ min, scope = "workspace" }: { min: keyof typeof ROLE_COPY; scope?: "workspace" | "instance" }) {
  return (
    <span className="badge role" title={`Needs at least the ${min} role ${scope === "instance" ? "on the instance" : "in the workspace"}`}>
      {scope === "instance" ? "instance " : ""}
      {ROLE_COPY[min]}+
    </span>
  );
}

/** A side note. `warn` is for anything that loses data or breaks callers. */
export function Note({ kind = "info", title, children }: { kind?: "info" | "warn" | "tip"; title?: string; children: ReactNode }) {
  return (
    <aside className={`note note-${kind}`}>
      {title ? <b>{title}</b> : null}
      <div>{children}</div>
    </aside>
  );
}

/** A block of commands or a request, in the quickstart's terminal style. */
export function Code({ title, children }: { title?: string; children: string }) {
  return (
    <div className="panel term code">
      {title ? (
        <div className="panel-head">
          <h3>{title}</h3>
        </div>
      ) : null}
      <pre>{children}</pre>
    </div>
  );
}

/** Two-column reference rows: a mono term on the left, what it means on the right. */
export function Rows({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <div className="rows">
      {items.map(([term, body], index) => (
        <div className="row" key={index}>
          <b>{term}</b>
          <span>{body}</span>
        </div>
      ))}
    </div>
  );
}
