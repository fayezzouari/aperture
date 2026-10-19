import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Button, Field } from "../../components/ui";
import { NODE_META, type NodeData, type NodeKind } from "./nodes";

type ModelLite = { id: string; slug: string; task: string; tags: { name: string; version: number | null }[]; version_count: number };
type VersionLite = { version: number; status: string; config: { classes?: string[]; input?: { input_names?: string[] } } };

/** The properties panel: what the selected node does, and nothing else. */
export default function Inspector({
  node,
  onChange,
  onDelete,
}: {
  node: { id: string; data: NodeData } | null;
  onChange: (patch: Partial<NodeData>) => void;
  onDelete: () => void;
}) {
  const models = useQuery<ModelLite[]>({ queryKey: ["models-lite"], queryFn: () => api("/v1/models", { query: { limit: 200 } }) });
  const versions = useQuery<VersionLite[]>({
    queryKey: ["versions", node?.data.model],
    queryFn: () => api(`/v1/models/${node?.data.model}/versions`),
    enabled: Boolean(node?.data.type === "model" && node?.data.model),
  });

  if (!node) {
    return (
      <div className="px-4 py-6 text-[12.5px] leading-relaxed text-ink-3">
        Select a node to configure it. Drag from a node's right edge to another node's left edge to connect them.
      </div>
    );
  }

  const data = node.data;
  const meta = NODE_META[data.type as NodeKind] ?? NODE_META.model;
  const selectedModel = models.data?.find((model) => model.slug === data.model);
  const refOptions = [
    ...(selectedModel?.tags ?? []).map((tag) => ({ value: tag.name, label: `${tag.name} → v${tag.version ?? "?"}` })),
    ...(versions.data ?? []).filter((v) => v.status === "ready").map((v) => ({ value: `v${v.version}`, label: `v${v.version}` })),
  ];
  const classesForModel = versions.data?.[0]?.config?.classes ?? [];
  const extraInputs = (versions.data?.[0]?.config?.input?.input_names ?? []).slice(1);

  return (
    <div className="space-y-4 px-4 py-4">
      <div>
        <p className="eyebrow" style={{ color: meta.accent }}>
          {meta.label}
        </p>
        <p className="mt-1 text-[11.5px] leading-relaxed text-ink-3">{meta.hint}</p>
      </div>

      <Field label="Label" hint="Shown on the canvas and in the per-node timings.">
        <input className="input" value={data.label ?? ""} onChange={(event) => onChange({ label: event.target.value })} placeholder={meta.label} />
      </Field>

      {data.type === "model" ? (
        <>
          <Field label="Model">
            <select className="select" value={data.model ?? ""} onChange={(event) => onChange({ model: event.target.value, ref: "production" })}>
              <option value="">Choose a model…</option>
              {(models.data ?? []).map((model) => (
                <option key={model.id} value={model.slug}>
                  {model.slug} · {model.task}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Version or tag" hint="A tag resolves at run time, so the workflow follows your rollbacks.">
            <select className="select" value={data.ref ?? "production"} onChange={(event) => onChange({ ref: event.target.value })}>
              {refOptions.length ? (
                refOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))
              ) : (
                <option value="production">production</option>
              )}
            </select>
          </Field>
          <Field label="Score threshold" hint="Applies to this node only, like a per-request override.">
            <input
              className="input num"
              inputMode="decimal"
              value={data.overrides?.score_threshold ?? ""}
              placeholder="model default"
              onChange={(event) =>
                onChange({
                  overrides: event.target.value
                    ? { ...(data.overrides ?? {}), score_threshold: Number(event.target.value) }
                    : Object.fromEntries(Object.entries(data.overrides ?? {}).filter(([key]) => key !== "score_threshold")),
                })
              }
            />
          </Field>
          {extraInputs.length ? (
            <p className="rounded-lg border border-line bg-raised px-3 py-2 text-[11.5px] leading-relaxed text-ink-2">
              This model also needs <span className="mono">{extraInputs.join(", ")}</span>. Connect a{" "}
              <span className="text-ink">Box geometry</span> node in front of it, or pin a constant on the model version.
            </p>
          ) : null}
        </>
      ) : null}

      {data.type === "crop" ? (
        <>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Max crops" hint="Per incoming image.">
              <input className="input num" type="number" min={1} max={64} value={data.max_crops ?? 8} onChange={(event) => onChange({ max_crops: Number(event.target.value) })} />
            </Field>
            <Field label="Padding" hint="Fraction of the box size.">
              <input className="input num" type="number" step="0.01" min={0} max={1} value={data.padding ?? 0.05} onChange={(event) => onChange({ padding: Number(event.target.value) })} />
            </Field>
          </div>
          <Field label="Minimum score">
            <input className="input num" type="number" step="0.01" min={0} max={1} value={data.min_score ?? 0} onChange={(event) => onChange({ min_score: Number(event.target.value) })} />
          </Field>
          <Field label="Only these classes" hint="Comma separated. Empty means every class.">
            <input className="input" value={(data.classes ?? []).join(", ")} onChange={(event) => onChange({ classes: splitList(event.target.value) })} placeholder={classesForModel.slice(0, 3).join(", ")} />
          </Field>
          <label className="flex cursor-pointer items-center gap-2 text-[12.5px] text-ink-2">
            <input type="checkbox" checked={Boolean(data.square)} onChange={(event) => onChange({ square: event.target.checked })} />
            Square crops
          </label>
        </>
      ) : null}

      {data.type === "gate" ? (
        <>
          <Field label="Run parameter" hint="A key from the run's `params`, e.g. regions.">
            <input className="input mono" value={data.param ?? ""} placeholder="regions" onChange={(event) => onChange({ param: event.target.value })} />
          </Field>
          <Field label="Test">
            <select className="select" value={data.op ?? "contains"} onChange={(event) => onChange({ op: event.target.value as "contains" | "equals" | "truthy" })}>
              <option value="contains">list contains</option>
              <option value="equals">equals</option>
              <option value="truthy">is set</option>
            </select>
          </Field>
          {data.op !== "truthy" ? (
            <Field label="Value" hint="The branch runs only when the test passes; otherwise it is skipped and costs nothing.">
              <input className="input mono" value={data.value ?? ""} placeholder="north" onChange={(event) => onChange({ value: event.target.value })} />
            </Field>
          ) : null}
        </>
      ) : null}

      {data.type === "filter" ? (
        <>
          <Field label="Minimum score">
            <input className="input num" type="number" step="0.01" min={0} max={1} value={data.min_score ?? 0.5} onChange={(event) => onChange({ min_score: Number(event.target.value) })} />
          </Field>
          <Field label="Keep at most" hint="Per image, highest score first.">
            <input className="input num" type="number" min={1} value={data.top_k ?? 5} onChange={(event) => onChange({ top_k: Number(event.target.value) })} />
          </Field>
          <Field label="Only these classes">
            <input className="input" value={(data.classes ?? []).join(", ")} onChange={(event) => onChange({ classes: splitList(event.target.value) })} />
          </Field>
        </>
      ) : null}

      {data.type === "geometry" ? (
        <>
          <Field label="Input name" hint="Must match the name the model's graph uses.">
            <input className="input mono" value={data.input_name ?? "aux"} onChange={(event) => onChange({ input_name: event.target.value })} />
          </Field>
          <Field label="Format">
            <select className="select" value={data.format ?? "cxcywh"} onChange={(event) => onChange({ format: event.target.value })}>
              <option value="cxcywh">cx, cy, w, h (normalised)</option>
              <option value="xyxy">x1, y1, x2, y2 (normalised)</option>
            </select>
          </Field>
          <p className="text-[11.5px] leading-relaxed text-ink-3">
            A crop has lost where it came from. This puts it back, so a position-aware second stage sees the same geometry it was trained with.
          </p>
        </>
      ) : null}

      {data.type === "output" ? (
        <>
          <Field label="Result key" hint="The field name in the response's outputs object.">
            <input className="input mono" value={data.key ?? "result"} onChange={(event) => onChange({ key: event.target.value })} />
          </Field>
          <Field
            label="Response shape"
            hint="Roboflow shape means centre-point boxes, `confidence` and `class` — so a client written against Roboflow needs no change."
          >
            <select className="select" value={data.format ?? "aperture"} onChange={(event) => onChange({ format: event.target.value })}>
              <option value="aperture">Aperture (typed, corner boxes)</option>
              <option value="roboflow">Roboflow compatible</option>
            </select>
          </Field>
        </>
      ) : null}

      {data.type !== "input" ? (
        <div className="border-t border-line pt-3">
          <Button size="sm" variant="danger" onClick={onDelete}>
            Delete node
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function splitList(value: string) {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}
