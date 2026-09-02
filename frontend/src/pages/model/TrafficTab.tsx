import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { fmt } from "../../lib/format";
import { Legend, MetricTile, StageBreakdown, TimeSeries, VolumeChart } from "../../components/charts";
import { Badge, EmptyState, ErrorNote, Panel, PanelHeader, Spinner } from "../../components/ui";
import { Segmented, TIME_WINDOWS } from "../../components/toolbar";
import type { Model, Version } from "../ModelDetail";

/** R5.4 per model, plus the drift signals of R5.9 for the version behind a tag. */
export default function TrafficTab({ model, versions }: { model: Model; versions: Version[] }) {
  const [window, setWindow] = useState(1440);
  const [versionId, setVersionId] = useState("");

  const metrics = useQuery<{
    requests: number;
    errors: number;
    error_rate: number;
    images: number;
    rps: number;
    p50_ms: number | null;
    p95_ms: number | null;
    p99_ms: number | null;
    stages: Record<string, number>;
    by_status: Record<string, number>;
    series: { ts: string; requests: number; errors: number; p50_ms: number | null; p95_ms: number | null; p99_ms: number | null }[];
  }>({
    queryKey: ["model-metrics", model.id, window, versionId],
    queryFn: () => api("/v1/metrics/summary", { query: { window_minutes: window, model_id: model.id, version_id: versionId || undefined } }),
    refetchInterval: 20_000,
  });

  const errors = useQuery<{ error_code: string; count: number; last_seen: string; sample_message: string | null }[]>({
    queryKey: ["model-errors", model.id, window],
    queryFn: () => api("/v1/metrics/errors", { query: { since_hours: Math.max(1, Math.round(window / 60)), model_id: model.id } }),
  });

  const liveVersionId = versionId || versions.find((version) => version.tags.includes("production"))?.id || versions[0]?.id;
  const drift = useQuery<{ baseline: unknown; current: unknown; signals: { signal: string; severity: string; message: string }[]; windows: { window_start: string; requests: number; stats: Record<string, number> }[] }>({
    queryKey: ["drift", liveVersionId],
    queryFn: () => api(`/v1/drift/${liveVersionId}`),
    enabled: Boolean(liveVersionId),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Segmented
          label="Time window"
          options={TIME_WINDOWS.map((option) => ({ value: option.value * 60, label: option.label }))}
          value={window}
          onChange={setWindow}
        />
        <select className="select w-auto" value={versionId} onChange={(event) => setVersionId(event.target.value)} aria-label="Filter by version">
          <option value="">All versions</option>
          {versions.map((version) => (
            <option key={version.id} value={version.id}>
              v{version.version}
            </option>
          ))}
        </select>
        <Link to={`/traces?model_id=${model.id}`} className="btn btn-sm ml-auto">
          Open in the explorer
        </Link>
      </div>

      {metrics.isLoading ? (
        <div className="flex h-40 items-center justify-center text-ink-3">
          <Spinner size={18} />
        </div>
      ) : metrics.error ? (
        <ErrorNote error={metrics.error} />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <MetricTile label="Requests" value={fmt.int(metrics.data!.requests)} sub={`${metrics.data!.rps.toFixed(3)} req/s`} />
            <MetricTile label="Images" value={fmt.int(metrics.data!.images)} sub={metrics.data!.requests ? `${(metrics.data!.images / metrics.data!.requests).toFixed(2)} per request` : undefined} />
            <MetricTile label="Error rate" value={fmt.pct(metrics.data!.error_rate)} tone={metrics.data!.error_rate > 0.02 ? "bad" : metrics.data!.error_rate ? "warn" : "good"} sub={`${fmt.int(metrics.data!.errors)} failures`} />
            <MetricTile label="p95" value={fmt.ms(metrics.data!.p95_ms)} sub={`p50 ${fmt.ms(metrics.data!.p50_ms)} · p99 ${fmt.ms(metrics.data!.p99_ms)}`} />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Panel className="lg:col-span-2">
              <PanelHeader
                title="Latency"
                action={
                  <Legend
                    items={[
                      { label: "p50", color: "var(--color-ramp-1)" },
                      { label: "p95", color: "var(--color-ramp-2)" },
                      { label: "p99", color: "var(--color-ramp-3)" },
                    ]}
                  />
                }
              />
              <div className="px-2 py-3">
                <TimeSeries
                  points={metrics.data!.series}
                  series={[
                    { key: "p50_ms", label: "p50", color: "var(--color-ramp-1)" },
                    { key: "p95_ms", label: "p95", color: "var(--color-ramp-2)" },
                    { key: "p99_ms", label: "p99", color: "var(--color-ramp-3)" },
                  ]}
                />
              </div>
              <div className="border-t border-line px-4 py-3">
                <div className="mb-2 flex items-center justify-between">
                  <p className="eyebrow">Volume</p>
                  <Legend items={[{ label: "Requests", color: "var(--color-series-1)" }, { label: "Errors", color: "var(--color-bad)" }]} />
                </div>
                <VolumeChart points={metrics.data!.series} height={88} />
              </div>
            </Panel>

            <Panel>
              <PanelHeader title="Stage cost" subtitle="Mean per request." />
              <div className="px-4 py-4">
                <StageBreakdown stages={metrics.data!.stages} />
              </div>
            </Panel>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Panel>
              <PanelHeader title="Errors by code" />
              {!errors.data?.length ? (
                <EmptyState title="No errors" body="Nothing failed in this window." />
              ) : (
                <div className="scroll-x">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Code</th>
                        <th className="text-right">Count</th>
                        <th>Last seen</th>
                        <th>Sample</th>
                      </tr>
                    </thead>
                    <tbody>
                      {errors.data.map((row) => (
                        <tr key={row.error_code}>
                          <td>
                            <Badge kind="bad">{row.error_code}</Badge>
                          </td>
                          <td className="num text-right">{fmt.int(row.count)}</td>
                          <td className="text-[11.5px] text-ink-3">{fmt.ago(row.last_seen)}</td>
                          <td className="max-w-[240px] truncate text-[11.5px] text-ink-3">{row.sample_message ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>

            <Panel>
              <PanelHeader title="Drift signals" subtitle="Confidence, class mix and input statistics against the baseline window." />
              <div className="px-4 py-3">
                {!drift.data?.signals?.length ? (
                  <p className="text-[12px] text-ink-3">No rolled-up windows yet — signals appear after the first hourly rollup.</p>
                ) : (
                  <ul className="space-y-2">
                    {drift.data.signals.map((signal, index) => (
                      <li key={index} className="flex gap-2 text-[12px] leading-relaxed">
                        <span className={signal.severity === "warning" ? "text-warn" : signal.severity === "ok" ? "text-good" : "text-ink-3"} aria-hidden>
                          {signal.severity === "warning" ? "▲" : signal.severity === "ok" ? "●" : "○"}
                        </span>
                        <span className="text-ink-2">{signal.message}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {drift.data?.windows?.length ? (
                  <p className="mt-3 border-t border-line pt-2 text-[11.5px] text-ink-3">
                    {drift.data.windows.length} hourly windows recorded · latest {fmt.int(drift.data.windows[drift.data.windows.length - 1]?.requests ?? 0)} requests
                  </p>
                ) : null}
              </div>
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}
