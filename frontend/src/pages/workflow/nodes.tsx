import { Handle, Position, type NodeProps } from "@xyflow/react";
import { fmt } from "../../lib/format";

/**
 * The node vocabulary. Each type is a shape in the graph *and* a step in the
 * engine, so the canvas is not a diagram of the pipeline - it is the pipeline.
 */
export type NodeKind = "input" | "model" | "gate" | "crop" | "filter" | "geometry" | "merge" | "output";

export const NODE_META: Record<NodeKind, { label: string; hint: string; accent: string; glyph: string }> = {
  input: {
    label: "Images in",
    hint: "Where the request's images enter.",
    accent: "var(--color-ink-3)",
    glyph: "▸",
  },
  model: {
    label: "Model",
    hint: "Run a deployed model on whatever reaches it.",
    accent: "var(--color-series-1)",
    glyph: "◆",
  },
  gate: {
    label: "Gate",
    hint: "Only run this branch when a run parameter says to.",
    accent: "var(--color-warn)",
    glyph: "◇",
  },
  crop: {
    label: "Crop to detections",
    hint: "One crop per detection — this is where one photo becomes several.",
    accent: "var(--color-series-2)",
    glyph: "⧉",
  },
  filter: {
    label: "Filter",
    hint: "Keep only the predictions worth passing on.",
    accent: "var(--color-series-4)",
    glyph: "⌗",
  },
  geometry: {
    label: "Box geometry",
    hint: "Attach each crop's own box as a named model input.",
    accent: "var(--color-series-3)",
    glyph: "⊞",
  },
  merge: {
    label: "Merge",
    hint: "Bring branches back together.",
    accent: "var(--color-ink-2)",
    glyph: "⋈",
  },
  output: {
    label: "Result",
    hint: "What the API returns, under this key.",
    accent: "var(--color-ink)",
    glyph: "◼",
  },
};

export type NodeData = {
  type: NodeKind;
  label?: string;
  model?: string;
  ref?: string;
  overrides?: Record<string, number>;
  inputs?: Record<string, number[]>;
  /** gate: which run parameter to read, how to test it, and against what */
  param?: string;
  op?: "contains" | "equals" | "truthy";
  value?: string;
  classes?: string[];
  min_score?: number;
  top_k?: number;
  padding?: number;
  max_crops?: number;
  square?: boolean;
  input_name?: string;
  /** geometry: the vector layout · output: the response shape */
  format?: string;
  key?: string;
  /** attached after a run so the canvas can show what happened */
  stat?: { status: string; ms: number; items_out: number; detections: number; version?: number | null; error?: string | null };
};

export function FlowNode({ data, selected }: NodeProps) {
  const node = data as NodeData;
  const meta = NODE_META[node.type] ?? NODE_META.model;
  const stat = node.stat;
  const failed = stat?.status === "error";

  return (
    <div
      className="rounded-lg border bg-panel text-left shadow-lg transition-colors"
      style={{
        minWidth: 178,
        borderColor: failed ? "var(--color-bad)" : selected ? meta.accent : "var(--color-line-2)",
        boxShadow: selected ? `0 0 0 3px color-mix(in oklab, ${meta.accent} 22%, transparent)` : undefined,
      }}
    >
      {node.type !== "input" ? (
        <Handle type="target" position={Position.Left} style={{ background: "#3a3a40", width: 8, height: 8, border: "none" }} />
      ) : null}

      <div className="flex items-center gap-2 border-b border-line px-2.5 py-1.5">
        <span className="text-[11px]" style={{ color: meta.accent }} aria-hidden>
          {meta.glyph}
        </span>
        <span className="truncate text-[12.5px] font-medium text-ink">{node.label || meta.label}</span>
        {stat ? (
          <span className={`num ml-auto shrink-0 text-[10.5px] ${failed ? "text-bad" : "text-ink-3"}`}>
            {failed ? "failed" : fmt.ms(stat.ms, 0)}
          </span>
        ) : null}
      </div>

      <div className="space-y-0.5 px-2.5 py-1.5">
        {node.type === "model" ? (
          <p className="mono truncate text-[11px] text-ink-2">
            {node.model || <span className="text-warn">pick a model</span>}
            {node.model ? <span className="text-ink-3"> · {node.ref || "production"}</span> : null}
          </p>
        ) : null}
        {node.type === "gate" ? (
          <p className="mono truncate text-[11px] text-ink-2">
            {node.param ? (
              <>
                {node.param}
                <span className="text-ink-3">
                  {" "}
                  {node.op === "truthy" ? "is set" : `${node.op || "contains"} ${node.value ?? "…"}`}
                </span>
              </>
            ) : (
              <span className="text-warn">pick a parameter</span>
            )}
          </p>
        ) : null}
        {node.type === "crop" ? (
          <p className="text-[11px] text-ink-3">
            max {node.max_crops ?? 8} · pad {Math.round((node.padding ?? 0.05) * 100)}%
            {node.classes?.length ? ` · ${node.classes.length} class filter` : ""}
          </p>
        ) : null}
        {node.type === "filter" ? (
          <p className="text-[11px] text-ink-3">
            {node.min_score !== undefined ? `score ≥ ${node.min_score}` : "no score floor"}
            {node.classes?.length ? ` · ${node.classes.join(", ").slice(0, 26)}` : ""}
          </p>
        ) : null}
        {node.type === "geometry" ? (
          <p className="mono text-[11px] text-ink-3">
            {node.input_name || "aux"} · {node.format || "cxcywh"}
          </p>
        ) : null}
        {node.type === "output" ? (
          <p className="mono text-[11px] text-ink-3">
            {node.key || "result"}
            {node.format === "roboflow" ? <span className="text-ink-2"> · roboflow</span> : null}
          </p>
        ) : null}

        {stat && !failed ? (
          <p className="text-[10.5px] text-ink-3">
            {stat.items_out} out
            {stat.detections ? ` · ${stat.detections} predictions` : ""}
            {stat.version ? ` · v${stat.version}` : ""}
          </p>
        ) : null}
        {failed ? <p className="text-[10.5px] leading-snug text-bad">{stat?.error}</p> : null}
      </div>

      {node.type !== "output" ? (
        <Handle type="source" position={Position.Right} style={{ background: "#3a3a40", width: 8, height: 8, border: "none" }} />
      ) : null}
    </div>
  );
}

export const nodeTypes = { aperture: FlowNode };
