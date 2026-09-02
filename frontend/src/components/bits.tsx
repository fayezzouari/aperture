import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import { fmt } from "../lib/format";
import { Badge, Button } from "./ui";

/** Drop target for images and weights. */
export function Dropzone({
  onFiles,
  accept,
  multiple = false,
  label,
  hint,
}: {
  onFiles: (files: File[]) => void;
  accept?: string;
  multiple?: boolean;
  label: string;
  hint?: ReactNode;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        const files = Array.from(event.dataTransfer.files ?? []);
        if (files.length) onFiles(multiple ? files : files.slice(0, 1));
      }}
      onClick={() => input.current?.click()}
      className={`cursor-pointer rounded-xl border border-dashed px-4 py-7 text-center transition-colors ${
        over ? "border-accent bg-accent/8" : "border-line-2 hover:border-[#3a3a40] hover:bg-raised"
      }`}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") input.current?.click();
      }}
    >
      <input
        ref={input}
        type="file"
        accept={accept}
        multiple={multiple}
        className="hidden"
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (files.length) onFiles(files);
          event.target.value = "";
        }}
      />
      <p className="text-[13px] text-ink">{label}</p>
      {hint ? <p className="mt-1 text-[11.5px] text-ink-3">{hint}</p> : null}
    </div>
  );
}

export function JsonView({ value, collapsedDepth = 2 }: { value: unknown; collapsedDepth?: number }) {
  const text = useMemo(() => JSON.stringify(value, null, 2), [value]);
  const [expanded, setExpanded] = useState(false);
  const lines = text.split("\n");
  const clipped = !expanded && lines.length > 24;
  void collapsedDepth;

  return (
    <div className="overflow-hidden rounded-lg border border-line bg-[#08080a]">
      <pre className="scroll-x mono px-3 py-2.5 text-[11.5px] leading-relaxed text-ink-2">
        <code>{clipped ? lines.slice(0, 24).join("\n") : text}</code>
      </pre>
      {lines.length > 24 ? (
        <div className="border-t border-line px-3 py-1.5">
          <Button size="sm" variant="ghost" onClick={() => setExpanded((current) => !current)}>
            {expanded ? "Collapse" : `Show all ${lines.length} lines`}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * The tag rail. Version history is only useful if you can see, at a glance,
 * which version each movable tag points at - that is the deploy state.
 */
export function TagRail({ tags }: { tags: string[] }) {
  if (!tags.length) return null;
  return (
    <span className="flex flex-wrap items-center gap-1">
      {tags.map((tag) => (
        <Badge key={tag} kind={tag === "production" ? "good" : tag === "staging" ? "accent" : "neutral"} className="badge-tag">
          {tag}
        </Badge>
      ))}
    </span>
  );
}

export function ProgressBar({ value, total, label }: { value: number; total: number; label?: string }) {
  const pct = total > 0 ? Math.min(100, (value / total) * 100) : 0;
  return (
    <div>
      <div className="h-[6px] overflow-hidden rounded-[3px] bg-[#141418]">
        <div className="h-full rounded-[3px] transition-[width] duration-300" style={{ width: `${pct}%`, background: "var(--color-accent)" }} />
      </div>
      <p className="mt-1 text-[11px] text-ink-3">
        {label ?? `${fmt.int(value)} / ${fmt.int(total)}`} · {pct.toFixed(0)}%
      </p>
    </div>
  );
}

export function useDebounced<T>(value: T, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  const timer = useRef<number>();
  const set = useCallback(
    (next: T) => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setDebounced(next), delay);
    },
    [delay]
  );
  if (value !== debounced) set(value);
  return debounced;
}
