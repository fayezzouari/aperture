import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { useWorkspace } from "../lib/hooks";
import { Legend, MetricTile, StageBreakdown, TimeSeries, VolumeChart } from "../components/charts";
import { Badge, EmptyState, ErrorNote, Panel, PanelHeader, Spinner, Status } from "../components/ui";

type Overview = {
  models: number;
  versions: number;
  deployments: number;
  replicas_ready: number;
  replicas_desired: number;
  runtimes: { id: string; device: string; gpu_name: string; status: string; fresh: boolean; loaded: unknown[]; free_memory_mb: number; total_memory_mb: number }[];
  metrics: {
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
  };
  recent_errors: {
    id: string;
    request_id: string;
    model_slug: string;
    version_number: number | null;
    error_code: string | null;
    error_message: string | null;
    created_at: string;
    total_ms: number;
  }[];
  firing_alerts: { id: string; name: string; metric: string; last_value: number | null; threshold: number }[];
  log_stream_depth: number;
};

export default function OverviewPage() {
  const workspace = useWorkspace();
  const overview = useQuery<Overview>({ queryKey: ["overview"], queryFn: () => api("/v1/overview"), refetchInterval: 15_000 });

  if (overview.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center text-ink-3">
        <Spinner size={18} />
      </div>
    );
  }
  if (overview.error) return <ErrorNote error={overview.error} />;
  const data = overview.data!;
  const metrics = data.metrics;
  const freshRuntimes = data.runtimes.filter((runtime) => runtime.fresh);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">{workspace?.name ?? "Overview"}</h1>
          <p className="mt-0.5 text-[12.5px] text-ink-3">
            How this workspace is behaving right now. Last 24 hours, refreshed every 15 seconds.
          </p>
        </div>
      </div>

      {data.firing_alerts.length ? (
        <div className="rounded-lg border border-bad/40 bg-bad/8 px-3.5 py-3">
          <p className="text-[12.5px] font-medium text-ink">
            {data.firing_alerts.length} alert{data.firing_alerts.length > 1 ? "s" : ""} firing
          </p>
          <ul className="mt-1.5 space-y-1">
            {data.firing_alerts.map((alert) => (
              <li key={alert.id} className="text-[12px] text-ink-2">
                <span className="text-ink">{alert.name}</span> — {alert.metric} is {fmt.int(alert.last_value ?? 0)} against a threshold of {alert.threshold}
              </li>
            ))}
          </ul>
          <Link to="/alerts" className="link mt-2 inline-block text-[12px]">
            Review alert rules
          </Link>
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricTile label="Requests · 24 h" value={fmt.int(metrics.requests)} sub={`${metrics.rps.toFixed(2)} req/s · ${fmt.int(metrics.images)} images`} />
        <MetricTile
          label="Error rate"
          value={fmt.pct(metrics.error_rate)}
          tone={metrics.error_rate > 0.02 ? "bad" : metrics.error_rate > 0.005 ? "warn" : "good"}
          sub={`${fmt.int(metrics.errors)} failed requests`}
        />
        <MetricTile label="p95 latency" value={fmt.ms(metrics.p95_ms)} sub={`p50 ${fmt.ms(metrics.p50_ms)} · p99 ${fmt.ms(metrics.p99_ms)}`} />
        <MetricTile
          label="Replicas ready"
          value={`${data.replicas_ready}/${data.replicas_desired}`}
          tone={data.replicas_desired > 0 && data.replicas_ready < data.replicas_desired ? "warn" : "neutral"}
          sub={`${data.deployments} deployment${data.deployments === 1 ? "" : "s"} · ${freshRuntimes.length} runtime${freshRuntimes.length === 1 ? "" : "s"} up`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel className="lg:col-span-2">
          <PanelHeader
            title="Latency over time"
            subtitle="Percentiles of end-to-end request latency, one line per percentile."
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
              points={metrics.series}
              series={[
                { key: "p50_ms", label: "p50", color: "var(--color-ramp-1)" },
                { key: "p95_ms", label: "p95", color: "var(--color-ramp-2)" },
                { key: "p99_ms", label: "p99", color: "var(--color-ramp-3)" },
              ]}
            />
          </div>
          <div className="border-t border-line px-4 py-3">
            <div className="mb-2 flex items-center justify-between">
              <p className="eyebrow">Requests and errors</p>
              <Legend
                items={[
                  { label: "Requests", color: "var(--color-series-1)" },
                  { label: "Errors", color: "var(--color-bad)" },
                ]}
              />
            </div>
            <VolumeChart points={metrics.series} height={92} />
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Where the time goes" subtitle="Mean cost per stage across the window." />
          <div className="px-4 py-4">
            <StageBreakdown stages={metrics.stages} />
            <div className="mt-4 border-t border-line pt-3">
              <p className="eyebrow mb-2">Outcomes</p>
              <ul className="space-y-1">
                {Object.entries(metrics.by_status).length === 0 ? (
                  <li className="text-[12px] text-ink-3">No requests yet.</li>
                ) : (
                  Object.entries(metrics.by_status).map(([status, count]) => (
                    <li key={status} className="flex items-center justify-between text-[12.5px]">
                      <Status value={status} />
                      <span className="num text-ink">{fmt.int(count)}</span>
                    </li>
                  ))
                )}
              </ul>
            </div>
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel>
          <PanelHeader
            title="Runtimes"
            subtitle="A runtime holds model versions resident and reports in every few seconds."
            action={
              <Link to="/fleet" className="btn btn-sm btn-ghost">
                Fleet
              </Link>
            }
          />
          {data.runtimes.length === 0 ? (
            <EmptyState
              title="No runtime has reported in"
              body="Start at least one runtime container. Until one is up, models can be uploaded and validated but not served."
            />
          ) : (
            <ul className="divide-y divide-[#17171a]">
              {data.runtimes.map((runtime) => (
                <li key={runtime.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                  <div className="min-w-0">
                    <p className="mono truncate text-[12.5px] text-ink">{runtime.id}</p>
                    <p className="text-[11.5px] text-ink-3">
                      {runtime.device}
                      {runtime.gpu_name ? ` · ${runtime.gpu_name}` : ""} · {runtime.loaded.length} version{runtime.loaded.length === 1 ? "" : "s"} resident
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {runtime.total_memory_mb ? (
                      <span className="num text-[11.5px] text-ink-3">
                        {Math.round(runtime.free_memory_mb / 1024)}/{Math.round(runtime.total_memory_mb / 1024)} GB free
                      </span>
                    ) : null}
                    <Status value={runtime.fresh ? runtime.status : "stale"} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel>
          <PanelHeader
            title="Recent failures"
            subtitle="Most recent non-ok responses, newest first."
            action={
              <Link to="/traces?status=error" className="btn btn-sm btn-ghost">
                Explore
              </Link>
            }
          />
          {data.recent_errors.length === 0 ? (
            <EmptyState title="No failures in the window" body="Every request in the last 24 hours returned a prediction." />
          ) : (
            <ul className="divide-y divide-[#17171a]">
              {data.recent_errors.map((entry) => (
                <li key={entry.id} className="px-4 py-2.5">
                  <div className="flex items-center justify-between gap-3">
                    <span className="flex min-w-0 items-center gap-2">
                      <Badge kind="bad">{entry.error_code ?? "error"}</Badge>
                      <Link to={`/models/${entry.model_slug}`} className="truncate text-[12.5px] text-ink hover:underline">
                        {entry.model_slug}
                      </Link>
                      {entry.version_number ? <span className="mono text-[11px] text-ink-3">v{entry.version_number}</span> : null}
                    </span>
                    <span className="shrink-0 text-[11.5px] text-ink-3">{fmt.ago(entry.created_at)}</span>
                  </div>
                  {entry.error_message ? <p className="mt-1 line-clamp-2 text-[11.5px] leading-relaxed text-ink-3">{entry.error_message}</p> : null}
                  <p className="num mt-1 text-[11px] text-ink-3">{fmt.ms(entry.total_ms)}</p>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      {data.log_stream_depth > 5000 ? (
        <p className="text-[11.5px] text-warn">
          The inference log buffer holds {fmt.int(data.log_stream_depth)} records — the log sink is behind. Serving is unaffected, but the explorer will lag.
        </p>
      ) : null}
    </div>
  );
}
