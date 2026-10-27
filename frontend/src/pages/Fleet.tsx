import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { Button, EmptyState, ErrorNote, Panel, PanelHeader, Spinner, Status, useToast } from "../components/ui";

type Runtime = {
  id: string;
  url: string;
  hostname: string;
  device: string;
  gpu_name: string;
  total_memory_mb: number;
  free_memory_mb: number;
  slots: number;
  status: string;
  fresh: boolean;
  loaded: { version_id: string; state: string; detail: string; queue_depth: number; in_flight: number; requests: number; batches: number; memory_mb: number }[];
  metrics: { providers?: string[]; loaded_count?: number; infer_workers?: number };
  last_heartbeat: string | null;
};

export default function Fleet() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const runtimes = useQuery<Runtime[]>({ queryKey: ["runtimes"], queryFn: () => api("/v1/runtimes"), refetchInterval: 5_000 });

  const reconcile = useMutation({
    mutationFn: () => api("/v1/fleet/reconcile", { method: "POST" }),
    onSuccess: (result) => {
      const summary = result as Record<string, number>;
      toast(`Reconciled: ${summary.assignments_created} placed, ${summary.assignments_removed} drained, ${summary.assignments_orphaned} orphaned.`);
      queryClient.invalidateQueries({ queryKey: ["runtimes"] });
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Fleet</h1>
          <p className="mt-0.5 text-[12.5px] text-ink-3">
            Runtimes hold model versions resident. They pull their assignments on every heartbeat, so scaling a deployment needs no restart.
          </p>
        </div>
        <Button variant="primary" loading={reconcile.isPending} onClick={() => reconcile.mutate()}>
          Reconcile now
        </Button>
      </div>

      {runtimes.isLoading ? (
        <div className="flex h-40 items-center justify-center text-ink-3">
          <Spinner size={18} />
        </div>
      ) : runtimes.error ? (
        <ErrorNote error={runtimes.error} />
      ) : !runtimes.data?.length ? (
        <Panel>
          <EmptyState
            title="No runtime has ever reported in"
            body="Start a runtime container pointed at this control plane (AP_CONTROL_PLANE_URL) with the shared runtime token. On a GPU host, use the CUDA image."
          />
        </Panel>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {runtimes.data.map((runtime) => (
            <Panel key={runtime.id}>
              <PanelHeader
                title={<span className="mono text-[12.5px]">{runtime.id}</span>}
                subtitle={
                  <>
                    {runtime.device}
                    {runtime.gpu_name ? ` · ${runtime.gpu_name}` : ""} · {runtime.loaded.length}/{runtime.slots} slots used
                    {runtime.metrics?.providers?.length ? ` · ${runtime.metrics.providers[0]}` : ""}
                  </>
                }
                action={
                  <>
                    {runtime.total_memory_mb ? (
                      <span className="num text-[11.5px] text-ink-3">
                        {(runtime.free_memory_mb / 1024).toFixed(1)}/{(runtime.total_memory_mb / 1024).toFixed(1)} GB free
                      </span>
                    ) : null}
                    <Status value={runtime.fresh ? runtime.status : "stale"} />
                  </>
                }
              />
              {runtime.loaded.length && runtime.fresh ? (
                <ul className="divide-y divide-[#17171a]">
                  {runtime.loaded.map((entry) => (
                    <li key={entry.version_id} className="flex items-center justify-between gap-3 px-4 py-2">
                      <div className="min-w-0">
                        <p className="mono truncate text-[11.5px] text-ink-2">{entry.version_id.slice(0, 8)}</p>
                        {entry.detail ? <p className="truncate text-[11px] text-ink-3">{entry.detail}</p> : null}
                      </div>
                      <div className="flex shrink-0 items-center gap-3 text-[11.5px]">
                        <span className="num text-ink-3">queue {entry.queue_depth}</span>
                        <span className="num text-ink-3">{fmt.int(entry.requests)} req</span>
                        {entry.memory_mb ? <span className="num text-ink-3">{entry.memory_mb} MB</span> : null}
                        {/* a runtime we cannot hear from cannot be holding a ready
                            version - showing its last-reported state as green would
                            be the most misleading thing on the page */}
                        <Status value={runtime.fresh ? entry.state : "stale"} />
                      </div>
                    </li>
                  ))}
                </ul>
              ) : runtime.loaded.length ? (
                <p className="px-4 py-3 text-[12px] text-ink-3">
                  Last held {runtime.loaded.length} version{runtime.loaded.length === 1 ? "" : "s"}
                  {" "}before it stopped reporting. Its replicas have been re-placed elsewhere.
                </p>
              ) : (
                <p className="px-4 py-3 text-[12px] text-ink-3">Idle — no version assigned.</p>
              )}
              <div className="flex items-center justify-between border-t border-line px-4 py-2 text-[11px] text-ink-3">
                <span className="mono truncate">{runtime.url}</span>
                <span>{runtime.last_heartbeat ? `heartbeat ${fmt.ago(runtime.last_heartbeat)}` : "never"}</span>
              </div>
            </Panel>
          ))}
        </div>
      )}

      <Panel>
        <PanelHeader title="How placement works" />
        <div className="px-4 py-3 text-[12px] leading-relaxed text-ink-2">
          <p>
            Each deployment asks for N replicas. The scheduler spreads them across distinct runtimes, prefers a GPU when the deployment says <span className="mono">auto</span>, and
            re-places any replica whose runtime stops heartbeating. A version only becomes routable once its replica has finished warm-up, which is what makes a version switch
            free of cold starts.
          </p>
        </div>
      </Panel>
    </div>
  );
}
