import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { fmt } from "../../lib/format";
import { can } from "../../lib/hooks";
import { Badge, Button, EmptyState, ErrorNote, Field, KeyValue, Modal, Panel, PanelHeader, Spinner, Status, useToast } from "../../components/ui";
import type { Model, Version } from "../ModelDetail";

type Deployment = {
  id: string;
  version: number | null;
  version_id: string;
  mode: string;
  traffic_weight: number;
  replicas: number;
  replicas_ready: number;
  device: string;
  max_batch_size: number;
  max_wait_ms: number;
  max_queue_depth: number;
  request_timeout_ms: number;
  autoscale: boolean;
  min_replicas: number;
  max_replicas: number;
  target_queue_depth: number;
  status: string;
  status_detail: string;
  replica_states: { runtime_id: string; state: string; detail: string; device: string; gpu_name: string; queue_depth: number; ready_at: string | null }[];
};

/** R6.4 - replicas, batching, thresholds, tags and one-click rollback in one place. */
export default function DeployTab({ model, versions, role }: { model: Model; versions: Version[]; role: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const editable = can(role, "developer");

  const deployments = useQuery<Deployment[]>({
    queryKey: ["deployments", model.slug],
    queryFn: () => api(`/v1/models/${model.slug}/deployments`),
    refetchInterval: 6_000,
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["deployments", model.slug] });
    queryClient.invalidateQueries({ queryKey: ["versions", model.slug] });
    queryClient.invalidateQueries({ queryKey: ["model", model.slug] });
  };

  const scale = useMutation({
    mutationFn: ({ id, replicas }: { id: string; replicas: number }) => api(`/v1/deployments/${id}`, { method: "PATCH", body: { replicas } }),
    onSuccess: refresh,
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const stop = useMutation({
    mutationFn: (id: string) => api(`/v1/deployments/${id}/stop`, { method: "POST" }),
    onSuccess: () => {
      toast("Draining — in-flight requests finish before the replicas unload.");
      refresh();
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const rollback = useMutation({
    mutationFn: (tag: string) => api(`/v1/models/${model.slug}/rollback`, { method: "POST", body: { tag } }),
    onSuccess: () => {
      toast("Rolled back — the tag now points at the previous version.");
      refresh();
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,330px)]">
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[12.5px] text-ink-3">A new version warms up on its own replicas first. Traffic only moves when you move the tag.</p>
            {editable ? (
              <Button variant="primary" onClick={() => setCreating(true)}>
                Deploy a version
              </Button>
            ) : null}
          </div>

          {deployments.isLoading ? (
            <Panel>
              <div className="flex h-32 items-center justify-center text-ink-3">
                <Spinner size={18} />
              </div>
            </Panel>
          ) : !deployments.data?.length ? (
            <Panel>
              <EmptyState
                title="Nothing deployed"
                body="Deploying a version asks the fleet to hold it resident and warm. Until then, calls return model_not_deployed."
                action={editable ? <Button variant="primary" onClick={() => setCreating(true)}>Deploy a version</Button> : undefined}
              />
            </Panel>
          ) : (
            deployments.data.map((deployment) => (
              <Panel key={deployment.id}>
                <PanelHeader
                  title={
                    <span className="flex items-center gap-2">
                      <span className="num">v{deployment.version}</span>
                      <Badge kind={deployment.mode === "primary" ? "neutral" : deployment.mode === "canary" ? "accent" : "warn"}>{deployment.mode}</Badge>
                      <Status value={deployment.status} />
                    </span>
                  }
                  subtitle={deployment.status_detail || `${deployment.device} · batch ≤ ${deployment.max_batch_size} · wait ${deployment.max_wait_ms} ms · queue ≤ ${deployment.max_queue_depth}`}
                  action={
                    editable ? (
                      <>
                        <Button size="sm" onClick={() => scale.mutate({ id: deployment.id, replicas: Math.max(0, deployment.replicas - 1) })} disabled={deployment.replicas <= 0}>
                          −
                        </Button>
                        <span className="num px-1 text-[13px]">
                          {deployment.replicas_ready}/{deployment.replicas}
                        </span>
                        <Button size="sm" onClick={() => scale.mutate({ id: deployment.id, replicas: deployment.replicas + 1 })}>
                          +
                        </Button>
                        <Button size="sm" variant="danger" onClick={() => stop.mutate(deployment.id)}>
                          Stop
                        </Button>
                      </>
                    ) : (
                      <span className="num text-[13px]">
                        {deployment.replicas_ready}/{deployment.replicas}
                      </span>
                    )
                  }
                />
                {deployment.replica_states.length ? (
                  <ul className="divide-y divide-[#17171a]">
                    {deployment.replica_states.map((replica) => (
                      <li key={replica.runtime_id} className="flex items-center justify-between gap-3 px-4 py-2">
                        <div className="min-w-0">
                          <p className="mono truncate text-[12px] text-ink">{replica.runtime_id}</p>
                          <p className="text-[11px] text-ink-3">
                            {replica.device || "—"}
                            {replica.gpu_name ? ` · ${replica.gpu_name}` : ""}
                            {replica.detail ? ` · ${replica.detail}` : ""}
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-3">
                          <span className="num text-[11.5px] text-ink-3">queue {replica.queue_depth}</span>
                          <Status value={replica.state} />
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="px-4 py-3 text-[12px] text-ink-3">
                    No replica has picked this up yet. The scheduler places it on the next heartbeat; if it stays here, no runtime matches the requested device.
                  </p>
                )}
                {deployment.autoscale ? (
                  <p className="border-t border-line px-4 py-2 text-[11.5px] text-ink-3">
                    Autoscaling between {deployment.min_replicas} and {deployment.max_replicas} replicas, targeting a queue depth of {deployment.target_queue_depth}.
                  </p>
                ) : null}
              </Panel>
            ))
          )}
        </div>

        <div className="space-y-4">
          <Panel>
            <PanelHeader title="Tags" subtitle="What each tag points at right now." />
            <div className="px-4 py-3">
              {model.tags.length ? (
                <ul className="space-y-2">
                  {model.tags.map((tag) => (
                    <li key={tag.name} className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2">
                        <Badge kind={tag.name === "production" ? "good" : "accent"} className="badge-tag">
                          {tag.name}
                        </Badge>
                        {tag.locked ? <span className="text-[11px] text-ink-3">locked</span> : null}
                      </span>
                      <span className="flex items-center gap-2">
                        <span className="num text-[12.5px] text-ink">v{tag.version}</span>
                        {editable && !tag.locked ? (
                          <Button size="sm" variant="ghost" loading={rollback.isPending && rollback.variables === tag.name} onClick={() => rollback.mutate(tag.name)}>
                            Roll back
                          </Button>
                        ) : null}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[12px] leading-relaxed text-ink-3">
                  No tags yet. Point <span className="mono">production</span> at a version so products can call a stable name instead of a version number.
                </p>
              )}
              <ErrorNote error={rollback.error} className="mt-3" />
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="How rollback works" />
            <div className="px-4 py-3 text-[12px] leading-relaxed text-ink-2">
              <p>
                Rollback repoints the tag at the version it pointed to before its last move. It is instant and needs no redeploy — but the target version has to still be
                deployed. Keep the previous version running with one replica while a new one beds in.
              </p>
            </div>
          </Panel>
        </div>
      </div>

      <DeployDialog model={model} versions={versions} open={creating} onClose={() => setCreating(false)} onDone={refresh} />
    </div>
  );
}

function DeployDialog({ model, versions, open, onClose, onDone }: { model: Model; versions: Version[]; open: boolean; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const deployable = versions.filter((version) => ["ready", "deprecated"].includes(version.status));
  const [form, setForm] = useState({
    version: deployable[0]?.version ?? 0,
    mode: "primary",
    traffic_weight: 100,
    replicas: 1,
    device: "auto",
    max_batch_size: 8,
    max_wait_ms: 8,
    max_queue_depth: 64,
    request_timeout_ms: 30000,
    autoscale: false,
    min_replicas: 1,
    max_replicas: 3,
    target_queue_depth: 8,
  });

  const create = useMutation({
    mutationFn: () => api(`/v1/models/${model.slug}/deployments`, { body: form }),
    onSuccess: () => {
      toast(`v${form.version} is warming up. It becomes routable once a replica reports ready.`);
      onDone();
      onClose();
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      wide
      title="Deploy a version"
      subtitle="Replicas load the weights, warm up, and only then accept traffic."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={create.isPending} disabled={!form.version} onClick={() => create.mutate()}>
            Deploy
          </Button>
        </>
      }
    >
      {!deployable.length ? (
        <p className="text-[12.5px] text-ink-3">No published version to deploy. Validate and publish a version first.</p>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Version">
              <select className="select" value={form.version} onChange={(event) => setForm({ ...form, version: Number(event.target.value) })}>
                {deployable.map((version) => (
                  <option key={version.id} value={version.version}>
                    v{version.version} · {version.status}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Mode" hint={form.mode === "shadow" ? "Mirrors traffic, answers nothing." : form.mode === "canary" ? "Takes a slice of tag traffic." : "Answers tag and version calls."}>
              <select className="select" value={form.mode} onChange={(event) => setForm({ ...form, mode: event.target.value })}>
                <option value="primary">primary</option>
                <option value="canary">canary</option>
                <option value="shadow">shadow</option>
              </select>
            </Field>
            <Field label="Replicas">
              <input className="input num" type="number" min={0} max={64} value={form.replicas} onChange={(event) => setForm({ ...form, replicas: Number(event.target.value) })} />
            </Field>
          </div>

          {form.mode === "canary" ? (
            <Field label={`Traffic share: ${form.traffic_weight}%`} hint="Only applies to callers using a tag. Explicit version calls always hit the primary.">
              <input type="range" min={1} max={50} value={form.traffic_weight} onChange={(event) => setForm({ ...form, traffic_weight: Number(event.target.value) })} className="w-full" />
            </Field>
          ) : null}

          <div className="grid gap-3 sm:grid-cols-4">
            <Field label="Device" hint="auto prefers a GPU runtime">
              <select className="select" value={form.device} onChange={(event) => setForm({ ...form, device: event.target.value })}>
                <option value="auto">auto</option>
                <option value="cuda">cuda (any GPU)</option>
                <option value="cuda:0">cuda:0</option>
                <option value="cuda:1">cuda:1</option>
                <option value="cpu">cpu</option>
              </select>
            </Field>
            <Field label="Max batch" hint="Requests merged per forward pass">
              <input className="input num" type="number" min={1} max={128} value={form.max_batch_size} onChange={(event) => setForm({ ...form, max_batch_size: Number(event.target.value) })} />
            </Field>
            <Field label="Batch wait (ms)" hint="How long to wait for company">
              <input className="input num" type="number" min={0} max={2000} value={form.max_wait_ms} onChange={(event) => setForm({ ...form, max_wait_ms: Number(event.target.value) })} />
            </Field>
            <Field label="Queue depth" hint="Beyond this, callers get 503 queue_full">
              <input className="input num" type="number" min={1} value={form.max_queue_depth} onChange={(event) => setForm({ ...form, max_queue_depth: Number(event.target.value) })} />
            </Field>
          </div>

          <Field label="Request timeout (ms)">
            <input className="input num" type="number" min={100} value={form.request_timeout_ms} onChange={(event) => setForm({ ...form, request_timeout_ms: Number(event.target.value) })} />
          </Field>

          <label className="flex cursor-pointer items-center gap-2 text-[12.5px] text-ink-2">
            <input type="checkbox" checked={form.autoscale} onChange={(event) => setForm({ ...form, autoscale: event.target.checked })} />
            Autoscale on queue depth
          </label>
          {form.autoscale ? (
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Min replicas">
                <input className="input num" type="number" min={0} value={form.min_replicas} onChange={(event) => setForm({ ...form, min_replicas: Number(event.target.value) })} />
              </Field>
              <Field label="Max replicas">
                <input className="input num" type="number" min={1} value={form.max_replicas} onChange={(event) => setForm({ ...form, max_replicas: Number(event.target.value) })} />
              </Field>
              <Field label="Target queue depth">
                <input className="input num" type="number" min={1} value={form.target_queue_depth} onChange={(event) => setForm({ ...form, target_queue_depth: Number(event.target.value) })} />
              </Field>
            </div>
          ) : null}

          <KeyValue
            items={[
              ["Batching", `up to ${form.max_batch_size} images per pass, waiting ${form.max_wait_ms} ms`],
              ["Backpressure", `refuse past ${form.max_queue_depth} queued requests`],
              ["Timeout", fmt.ms(form.request_timeout_ms)],
            ]}
          />
          <ErrorNote error={create.error} />
        </div>
      )}
    </Modal>
  );
}
