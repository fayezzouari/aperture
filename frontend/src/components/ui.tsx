import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { tone } from "../lib/format";

/* --------------------------------------------------------------- primitives */

export function Panel({ children, className = "", ...rest }: { children: ReactNode; className?: string } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`panel ${className}`} {...rest}>
      {children}
    </div>
  );
}

export function PanelHeader({ title, subtitle, action }: { title: ReactNode; subtitle?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-line px-4 py-3">
      <div className="min-w-0">
        <h2 className="text-[13.5px] font-semibold text-ink">{title}</h2>
        {subtitle ? <p className="mt-0.5 text-xs leading-relaxed text-ink-3">{subtitle}</p> : null}
      </div>
      {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
    </div>
  );
}

export function Badge({ children, kind = "neutral", className = "" }: { children: ReactNode; kind?: "neutral" | "good" | "warn" | "bad" | "accent"; className?: string }) {
  const map = { neutral: "", good: "badge-good", warn: "badge-warn", bad: "badge-bad", accent: "badge-accent" };
  return <span className={`badge ${map[kind]} ${className}`}>{children}</span>;
}

/** Status is never colour alone: a dot plus the word, every time. */
export function Status({ value, className = "" }: { value?: string | null; className?: string }) {
  const t = tone(value);
  const colors = { good: "var(--color-good)", warn: "var(--color-warn)", bad: "var(--color-bad)", neutral: "var(--color-ink-3)" };
  return (
    <span className={`inline-flex items-center gap-1.5 text-[12.5px] ${className}`}>
      <span className="inline-block h-[7px] w-[7px] shrink-0 rounded-full" style={{ background: colors[t] }} aria-hidden />
      <span className={t === "bad" ? "text-bad" : t === "warn" ? "text-warn" : t === "good" ? "text-good" : "text-ink-2"}>{value ?? "unknown"}</span>
    </span>
  );
}

export function Button({
  children,
  variant = "default",
  size = "md",
  loading = false,
  className = "",
  ...rest
}: { children: ReactNode; variant?: "default" | "primary" | "accent" | "danger" | "ghost"; size?: "md" | "sm"; loading?: boolean } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const variants = { default: "", primary: "btn-primary", accent: "btn-accent", danger: "btn-danger", ghost: "btn-ghost" };
  return (
    <button className={`btn ${variants[variant]} ${size === "sm" ? "btn-sm" : ""} ${className}`} disabled={rest.disabled || loading} {...rest}>
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
}

export function Spinner({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className="animate-spin" aria-hidden>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function Field({ label, hint, children, htmlFor }: { label: ReactNode; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div>
      <label className="field-label" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {hint ? <p className="hint">{hint}</p> : null}
    </div>
  );
}

export function Tabs({ tabs, active, onChange }: { tabs: { key: string; label: string; count?: number }[]; active: string; onChange: (key: string) => void }) {
  return (
    // No negative margin on the row itself: `space-y-*` is defined inside
    // `:where()` in Tailwind v4, so it has zero specificity and any margin
    // utility on a child wins outright — a stray -mb-px here collapses the
    // page's gap between the tabs and whatever they switch. The buttons carry
    // their own offset for the active underline.
    <div className="nav-x flex gap-1 border-b border-line">
      {tabs.map((tab) => {
        const on = tab.key === active;
        return (
          <button
            key={tab.key}
            onClick={() => onChange(tab.key)}
            className={`relative -mb-px whitespace-nowrap px-3 py-2 text-[13px] transition-colors ${on ? "text-ink" : "text-ink-3 hover:text-ink-2"}`}
            aria-current={on ? "page" : undefined}
          >
            {tab.label}
            {tab.count !== undefined ? <span className="num ml-1.5 text-[11px] text-ink-3">{tab.count}</span> : null}
            {on ? <span className="absolute inset-x-0 -bottom-px h-[1.5px] bg-ink" /> : null}
          </button>
        );
      })}
    </div>
  );
}

export function EmptyState({ title, body, action, icon }: { title: string; body?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {icon ? <div className="mb-3 text-ink-3">{icon}</div> : null}
      <h3 className="text-sm font-medium text-ink">{title}</h3>
      {body ? <p className="mt-1.5 max-w-md text-[12.5px] leading-relaxed text-ink-3">{body}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function ErrorNote({ error, className = "" }: { error: unknown; className?: string }) {
  if (!error) return null;
  const anyError = error as { code?: string; message?: string; requestId?: string };
  return (
    <div className={`rounded-lg border border-bad/40 bg-bad/8 px-3 py-2.5 text-[12.5px] text-ink ${className}`} role="alert">
      <div className="flex items-start gap-2">
        <span className="mt-[3px] inline-block h-[7px] w-[7px] shrink-0 rounded-full" style={{ background: "var(--color-bad)" }} aria-hidden />
        <div className="min-w-0">
          <p>{anyError.message ?? String(error)}</p>
          {anyError.code ? (
            <p className="mono mt-1 text-[11px] text-ink-3">
              {anyError.code}
              {anyError.requestId ? ` · request ${anyError.requestId.slice(0, 12)}` : ""}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function Modal({ open, onClose, title, subtitle, children, footer, wide = false }: { open: boolean; onClose: () => void; title: ReactNode; subtitle?: ReactNode; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 pt-[8vh] backdrop-blur-sm" role="dialog" aria-modal="true" onClick={onClose}>
      <div className={`panel w-full ${wide ? "max-w-4xl" : "max-w-lg"} bg-panel shadow-2xl`} onClick={(event) => event.stopPropagation()}>
        <PanelHeader
          title={title}
          subtitle={subtitle}
          action={
            <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
              ✕
            </button>
          }
        />
        <div className="max-h-[62vh] overflow-y-auto scroll-y px-4 py-4">{children}</div>
        {footer ? <div className="flex items-center justify-end gap-2 border-t border-line px-4 py-3">{footer}</div> : null}
      </div>
    </div>
  );
}

export function Copy({ value, label = "Copy", size = "sm" }: { value: string; label?: string; size?: "sm" | "md" }) {
  const [done, setDone] = useState(false);
  return (
    <Button
      size={size}
      variant="ghost"
      onClick={async () => {
        await navigator.clipboard.writeText(value);
        setDone(true);
        setTimeout(() => setDone(false), 1400);
      }}
    >
      {done ? "Copied" : label}
    </Button>
  );
}

export function CodeBlock({ code, language, actions }: { code: string; language?: string; actions?: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-[#08080a]">
      <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
        <span className="eyebrow">{language ?? "shell"}</span>
        <div className="flex items-center gap-1">
          {actions}
          <Copy value={code} />
        </div>
      </div>
      <pre className="scroll-x mono px-3 py-2.5 text-[11.5px] leading-relaxed text-ink-2">
        <code>{code}</code>
      </pre>
    </div>
  );
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="divide-y divide-[#17171a]">
      {items.map(([key, value], index) => (
        <div key={index} className="flex items-baseline justify-between gap-6 py-1.5">
          <dt className="text-[12px] text-ink-3">{key}</dt>
          <dd className="min-w-0 truncate text-right text-[12.5px] text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ------------------------------------------------------------------ toasts */

type Toast = { id: number; message: string; kind: "ok" | "bad" };
const ToastContext = createContext<(message: string, kind?: "ok" | "bad") => void>(() => {});
export const useToast = () => useContext(ToastContext);

export function ToastHost({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = (message: string, kind: "ok" | "bad" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, message, kind }]);
    setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 5000);
  };
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(380px,90vw)] flex-col gap-2">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className="panel pointer-events-auto flex items-start gap-2 bg-raised px-3 py-2.5 text-[12.5px] shadow-xl"
            role="status"
          >
            <span
              className="mt-[5px] inline-block h-[7px] w-[7px] shrink-0 rounded-full"
              style={{ background: toast.kind === "bad" ? "var(--color-bad)" : "var(--color-good)" }}
              aria-hidden
            />
            <span className="min-w-0">{toast.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
