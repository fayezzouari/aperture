import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { can, useInstanceRole } from "../lib/hooks";
import { Badge, Button, EmptyState, ErrorNote, Field, Modal, Panel, PanelHeader, Spinner, Status, useToast } from "../components/ui";

type Rule = {
  id: string;
  name: string;
  metric: string;
  model_id: string | null;
  comparator: string;
  threshold: number;
  window_minutes: number;
  min_samples: number;
  channel: string;
  destination: string;
  enabled: boolean;
  state: string;
  last_value: number | null;
  last_fired_at: string | null;
  last_checked_at: string | null;
};

const METRICS: { value: string; label: string; hint: string; unit: string }[] = [
  { value: "error_rate", label: "Error rate", hint: "Fraction of non-ok responses in the window. 0.02 = 2%.", unit: "ratio" },
  { value: "p95_latency_ms", label: "p95 latency", hint: "95th percentile end-to-end latency in milliseconds.", unit: "ms" },
  { value: "p99_latency_ms", label: "p99 latency", hint: "99th percentile end-to-end latency in milliseconds.", unit: "ms" },
  { value: "replica_down", label: "Replicas missing", hint: "Replicas requested but not ready. Fires on 1 or more.", unit: "replicas" },
  { value: "no_traffic", label: "No traffic", hint: "Requests in the window. Use with '<' to catch a silent consumer.", unit: "requests" },
  { value: "confidence_drop", label: "Mean confidence", hint: "Average prediction confidence. Use with '<'.", unit: "score" },
];

export default function Alerts() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const role = useInstanceRole();
  const [creating, setCreating] = useState(false);

  const rules = useQuery<Rule[]>({ queryKey: ["alerts"], queryFn: () => api("/v1/alerts"), refetchInterval: 30_000 });
  const events = useQuery<{ id: string; rule: string; value: number; message: string; delivered: boolean; delivery_error: string | null; resolved_at: string | null; created_at: string }[]>({
    queryKey: ["alert-events"],
    queryFn: () => api("/v1/alerts/events", { query: { limit: 30 } }),
  });

  const test = useMutation({
    mutationFn: (id: string) => api<{ delivered: boolean; error: string | null }>(`/v1/alerts/${id}/test`, { method: "POST" }),
    onSuccess: (result) => toast(result.delivered ? "Test message delivered." : `Delivery failed: ${result.error}`, result.delivered ? "ok" : "bad"),
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api(`/v1/alerts/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Alerts</h1>
          <p className="mt-0.5 text-[12.5px] text-ink-3">Evaluated every minute. A rule needs a minimum number of samples before it can fire, so a quiet model stays quiet.</p>
        </div>
        {can(role, "developer") ? (
          <Button variant="primary" onClick={() => setCreating(true)}>
            New rule
          </Button>
        ) : null}
      </div>

      <Panel>
        {rules.isLoading ? (
          <div className="flex h-32 items-center justify-center text-ink-3">
            <Spinner size={18} />
          </div>
        ) : !rules.data?.length ? (
          <EmptyState
            title="No alert rules"
            body="Three rules cover most of it: error rate above a few percent, p99 latency past your SLA, and any replica missing."
            action={can(role, "developer") ? <Button variant="primary" onClick={() => setCreating(true)}>New rule</Button> : undefined}
          />
        ) : (
          <div className="scroll-x">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Rule</th>
                  <th>Condition</th>
                  <th>Window</th>
                  <th className="text-right">Last value</th>
                  <th>State</th>
                  <th>Channel</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rules.data.map((rule) => (
                  <tr key={rule.id} className={rule.enabled ? "" : "opacity-50"}>
                    <td className="text-[12.5px] text-ink">{rule.name}</td>
                    <td className="mono text-[11.5px] text-ink-2">
                      {rule.metric} {rule.comparator} {rule.threshold}
                    </td>
                    <td className="text-[12px] text-ink-3">
                      {rule.window_minutes} min · ≥{rule.min_samples} samples
                    </td>
                    <td className="num text-right text-[12px]">{rule.last_value ?? "—"}</td>
                    <td>
                      <Status value={rule.state} />
                      {rule.last_fired_at ? <p className="text-[11px] text-ink-3">fired {fmt.ago(rule.last_fired_at)}</p> : null}
                    </td>
                    <td>
                      <Badge>{rule.channel}</Badge>
                    </td>
                    <td className="text-right">
                      {can(role, "developer") ? (
                        <span className="flex justify-end gap-1">
                          <Button size="sm" variant="ghost" loading={test.isPending && test.variables === rule.id} onClick={() => test.mutate(rule.id)}>
                            Test
                          </Button>
                          <Button size="sm" variant="danger" onClick={() => remove.mutate(rule.id)}>
                            Delete
                          </Button>
                        </span>
                      ) : null}
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
          title="Recent alert events"
          subtitle="Every firing and resolution, with whether it was delivered."
        />
        {!events.data?.length ? (
          <EmptyState title="Nothing has fired" body="Alert events land here with their delivery status, so a silent Slack webhook is visible." />
        ) : (
          <ul className="divide-y divide-[#17171a]">
            {events.data.map((event) => (
              <li key={event.id} className="flex items-start justify-between gap-3 px-4 py-2.5">
                <div className="min-w-0">
                  <p className="text-[12.5px] text-ink">{event.rule}</p>
                  <p className="text-[11.5px] leading-relaxed text-ink-3">{event.message}</p>
                  {event.delivery_error ? <p className="text-[11px] text-bad">delivery failed: {event.delivery_error}</p> : null}
                </div>
                <div className="shrink-0 text-right">
                  <p className="text-[11.5px] text-ink-3">{fmt.ago(event.created_at)}</p>
                  {event.resolved_at ? <Badge kind="good">resolved</Badge> : event.delivered ? <Badge>delivered</Badge> : <Badge kind="bad">not delivered</Badge>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <CreateRule open={creating} onClose={() => setCreating(false)} onCreated={() => queryClient.invalidateQueries({ queryKey: ["alerts"] })} />
    </div>
  );
}

function CreateRule({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({
    name: "",
    metric: "error_rate",
    comparator: ">",
    threshold: 0.02,
    window_minutes: 5,
    min_samples: 20,
    channel: "slack",
    destination: "",
    model_id: null as string | null,
    enabled: true,
  });
  const models = useQuery<{ id: string; slug: string }[]>({ queryKey: ["models-lite"], queryFn: () => api("/v1/models", { query: { limit: 200 } }), enabled: open });
  const metric = METRICS.find((item) => item.value === form.metric)!;

  const create = useMutation({
    mutationFn: () => api("/v1/alerts", { body: form }),
    onSuccess: () => {
      onCreated();
      onClose();
    },
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New alert rule"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={create.isPending} disabled={!form.name || !form.destination} onClick={() => create.mutate()}>
            Create rule
          </Button>
        </>
      }
    >
      <div className="space-y-3.5">
        <Field label="Name">
          <input className="input" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Error rate above 2%" />
        </Field>
        <Field label="Metric" hint={metric.hint}>
          <select className="select" value={form.metric} onChange={(event) => setForm({ ...form, metric: event.target.value })}>
            {METRICS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label="Comparator">
            <select className="select" value={form.comparator} onChange={(event) => setForm({ ...form, comparator: event.target.value })}>
              <option value=">">above</option>
              <option value="<">below</option>
            </select>
          </Field>
          <Field label={`Threshold (${metric.unit})`}>
            <input className="input num" type="number" step="any" value={form.threshold} onChange={(event) => setForm({ ...form, threshold: Number(event.target.value) })} />
          </Field>
          <Field label="Window (min)">
            <input className="input num" type="number" min={1} value={form.window_minutes} onChange={(event) => setForm({ ...form, window_minutes: Number(event.target.value) })} />
          </Field>
        </div>
        <Field label="Scope">
          <select className="select" value={form.model_id ?? ""} onChange={(event) => setForm({ ...form, model_id: event.target.value || null })}>
            <option value="">Every model</option>
            {(models.data ?? []).map((model) => (
              <option key={model.id} value={model.id}>
                {model.slug}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Minimum samples" hint="Below this many requests the rule stays silent instead of firing on noise.">
          <input className="input num" type="number" min={1} value={form.min_samples} onChange={(event) => setForm({ ...form, min_samples: Number(event.target.value) })} />
        </Field>
        <div className="grid grid-cols-[110px_1fr] gap-3">
          <Field label="Channel">
            <select className="select" value={form.channel} onChange={(event) => setForm({ ...form, channel: event.target.value })}>
              <option value="slack">Slack</option>
              <option value="webhook">Webhook</option>
              <option value="email">Email</option>
            </select>
          </Field>
          <Field label="Destination" hint={form.channel === "email" ? "Needs AP_SMTP_HOST set on the API." : "Incoming webhook URL."}>
            <input className="input" value={form.destination} onChange={(event) => setForm({ ...form, destination: event.target.value })} placeholder={form.channel === "email" ? "perception@example.com" : "https://hooks.slack.com/services/…"} />
          </Field>
        </div>
        <ErrorNote error={create.error} />
      </div>
    </Modal>
  );
}
