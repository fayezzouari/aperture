import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { fmt } from "../../lib/format";
import { Badge, ErrorNote, Panel, PanelHeader, Spinner } from "../../components/ui";
import type { Model, Version } from "../ModelDetail";

type Compare = {
  left: Version;
  right: Version;
  config_diff: { path: string; left: unknown; right: unknown; kind: string }[];
  metric_diff: Record<string, { left: number | null; right: number | null; delta: number | null }>;
  traffic: Record<string, { requests?: number; error_rate?: number; p50_ms?: number | null; p95_ms?: number | null; inference_ms?: number | null }>;
};

/** R2.5 - metadata, metrics, latency and config differences, side by side. */
export default function CompareTab({ model, versions }: { model: Model; versions: Version[] }) {
  const [left, setLeft] = useState(versions[1]?.version ?? versions[0]?.version ?? 0);
  const [right, setRight] = useState(versions[0]?.version ?? 0);

  const compare = useQuery<Compare>({
    queryKey: ["compare", model.slug, left, right],
    queryFn: () => api(`/v1/models/${model.slug}/version-compare`, { query: { left, right } }),
    enabled: Boolean(left && right),
  });

  if (versions.length < 2) {
    return (
      <Panel>
        <div className="px-4 py-10 text-center text-[12.5px] text-ink-3">Two versions are needed to compare. Upload another one first.</div>
      </Panel>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <select className="select w-auto" value={left} onChange={(event) => setLeft(Number(event.target.value))} aria-label="Left version">
          {versions.map((version) => (
            <option key={version.id} value={version.version}>
              v{version.version}
            </option>
          ))}
        </select>
        <span className="text-ink-3">vs</span>
        <select className="select w-auto" value={right} onChange={(event) => setRight(Number(event.target.value))} aria-label="Right version">
          {versions.map((version) => (
            <option key={version.id} value={version.version}>
              v{version.version}
            </option>
          ))}
        </select>
      </div>

      {compare.isLoading ? (
        <Panel>
          <div className="flex h-40 items-center justify-center text-ink-3">
            <Spinner size={18} />
          </div>
        </Panel>
      ) : compare.error ? (
        <ErrorNote error={compare.error} />
      ) : compare.data ? (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            {[compare.data.left, compare.data.right].map((version, index) => {
              const traffic = compare.data!.traffic[index === 0 ? "left" : "right"] ?? {};
              return (
                <Panel key={version.id}>
                  <PanelHeader
                    title={
                      <span className="flex items-center gap-2">
                        <span className="num">v{version.version}</span>
                        <Badge>{version.status}</Badge>
                        {version.tags.map((tag) => (
                          <Badge key={tag} kind="good" className="badge-tag">
                            {tag}
                          </Badge>
                        ))}
                      </span>
                    }
                    subtitle={version.changelog || "No changelog."}
                  />
                  <div className="space-y-3 px-4 py-3">
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12px]">
                      <Row label="Uploaded" value={fmt.when(version.created_at)} />
                      <Row label="Artifact" value={`${version.artifact_format} · ${fmt.bytes(version.weights_bytes)}`} />
                      <Row label="Dataset" value={version.dataset_version ?? "—"} />
                      <Row label="Training run" value={version.training_run_id ?? "—"} />
                      <Row label="Requests · 7 d" value={fmt.int(traffic.requests ?? 0)} />
                      <Row label="Error rate" value={traffic.requests ? fmt.pct(traffic.error_rate ?? 0) : "—"} />
                      <Row label="p50 / p95" value={`${fmt.ms(traffic.p50_ms)} / ${fmt.ms(traffic.p95_ms)}`} />
                      <Row label="Mean inference" value={fmt.ms(traffic.inference_ms)} />
                    </dl>
                  </div>
                </Panel>
              );
            })}
          </div>

          <Panel>
            <PanelHeader title="Metrics" subtitle="Reported at upload time by whoever trained the model." />
            {Object.keys(compare.data.metric_diff).length === 0 ? (
              <p className="px-4 py-6 text-center text-[12.5px] text-ink-3">Neither version carries metrics. Send them with the upload so this table is useful.</p>
            ) : (
              <div className="scroll-x">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th className="text-right">v{compare.data.left.version}</th>
                      <th className="text-right">v{compare.data.right.version}</th>
                      <th className="text-right">Δ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(compare.data.metric_diff).map(([key, value]) => (
                      <tr key={key}>
                        <td>{key}</td>
                        <td className="num text-right">{value.left ?? "—"}</td>
                        <td className="num text-right">{value.right ?? "—"}</td>
                        <td className={`num text-right ${value.delta === null ? "text-ink-3" : value.delta > 0 ? "text-good" : value.delta < 0 ? "text-bad" : "text-ink-2"}`}>
                          {value.delta === null ? "—" : value.delta > 0 ? `+${value.delta}` : value.delta}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel>
            <PanelHeader
              title="Config differences"
              subtitle={compare.data.config_diff.length ? `${compare.data.config_diff.length} field${compare.data.config_diff.length === 1 ? "" : "s"} differ` : "The configs are identical — only the weights changed."}
            />
            {compare.data.config_diff.length ? (
              <div className="scroll-x">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Field</th>
                      <th>v{compare.data.left.version}</th>
                      <th>v{compare.data.right.version}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {compare.data.config_diff.map((row) => (
                      <tr key={row.path}>
                        <td className="mono text-[11.5px]">{row.path}</td>
                        <td className="mono text-[11.5px] text-ink-2">{render(row.left)}</td>
                        <td className="mono text-[11.5px] text-ink">{render(row.right)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </Panel>
        </>
      ) : null}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-ink-3">{label}</dt>
      <dd className="truncate text-right text-ink">{value}</dd>
    </>
  );
}

function render(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
