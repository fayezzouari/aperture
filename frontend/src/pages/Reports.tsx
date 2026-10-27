import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, download } from "../lib/api";
import { fmt } from "../lib/format";
import { Badge, Button, EmptyState, ErrorNote, Field, Modal, Panel, PanelHeader, Spinner, useToast } from "../components/ui";
import { Segmented, Toolbar } from "../components/toolbar";

type Report = { kind: string; title: string; group_by: string; columns: string[]; rows: Record<string, unknown>[]; totals: Record<string, number>; window_hours: number; generated_at: string };
type Schedule = { id: string; name: string; kind: string; fmt: string; cron: string | null; channel: string; destination: string; enabled: boolean; last_run_at: string | null; last_artifact_url?: string };

const KINDS = [
  { value: "usage", label: "Usage" },
  { value: "errors", label: "Errors" },
  { value: "latency", label: "Latency trend" },
  { value: "flagged", label: "Flagged predictions" },
];
const GROUPS = [
  { value: "model", label: "by model" },
  { value: "version", label: "by version" },
  { value: "consumer", label: "by consumer" },
  { value: "day", label: "by day" },
  { value: "error_code", label: "by error code" },
];
const WINDOWS = [
  { value: 24, label: "24 h" },
  { value: 168, label: "7 d" },
  { value: 720, label: "30 d" },
  { value: 2160, label: "90 d" },
];

export default function Reports() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [kind, setKind] = useState("usage");
  const [groupBy, setGroupBy] = useState("model");
  const [hours, setHours] = useState(168);
  const [scheduling, setScheduling] = useState(false);

  const report = useQuery<Report>({
    queryKey: ["report", kind, groupBy, hours],
    queryFn: () => api("/v1/reports/run", { body: { kind, group_by: groupBy, since_hours: hours, fmt: "json" } }),
  });

  const schedules = useQuery<Schedule[]>({ queryKey: ["schedules"], queryFn: () => api("/v1/reports/schedules") });

  const exportFile = useMutation({
    mutationFn: (format: "csv" | "pdf") =>
      download("/v1/reports/run", { body: { kind, group_by: groupBy, since_hours: hours, fmt: format }, filename: `aperture-${kind}-${hours}h.${format}` }),
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const runNow = useMutation({
    mutationFn: (id: string) => api(`/v1/reports/schedules/${id}/run`, { method: "POST" }),
    onSuccess: () => toast("Queued — the artifact appears here when the worker finishes."),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api(`/v1/reports/schedules/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["schedules"] }),
  });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="page-title">Reports</h1>
        <p className="mt-0.5 text-[12.5px] text-ink-3">Usage per model, version and consumer; error breakdowns; latency trends. Exportable, and schedulable to Slack, email or a webhook.</p>
      </div>

      <Panel className="overflow-hidden">
        <Toolbar
          trailing={
            <>
              <Button size="sm" loading={exportFile.isPending && exportFile.variables === "csv"} onClick={() => exportFile.mutate("csv")}>
                Export CSV
              </Button>
              <Button size="sm" loading={exportFile.isPending && exportFile.variables === "pdf"} onClick={() => exportFile.mutate("pdf")}>
                Export PDF
              </Button>
              <Button size="sm" onClick={() => setScheduling(true)}>
                Schedule
              </Button>
            </>
          }
        >
          <Segmented label="Time window" options={WINDOWS} value={hours} onChange={setHours} />
          <select className="select w-auto" value={kind} onChange={(event) => setKind(event.target.value)} aria-label="Report">
            {KINDS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {kind === "usage" ? (
            <select className="select w-auto" value={groupBy} onChange={(event) => setGroupBy(event.target.value)} aria-label="Group by">
              {GROUPS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          ) : null}
        </Toolbar>

        {report.isLoading ? (
          <div className="flex h-40 items-center justify-center text-ink-3">
            <Spinner size={18} />
          </div>
        ) : report.error ? (
          <div className="p-4">
            <ErrorNote error={report.error} />
          </div>
        ) : !report.data?.rows.length ? (
          <EmptyState title="Nothing to report" body="No requests matched this window." />
        ) : (
          <div className="scroll-x">
            <table className="data-table">
              <thead>
                <tr>
                  {report.data.columns.map((column) => (
                    <th key={column} className={typeof report.data!.rows[0][column] === "number" ? "text-right" : ""}>
                      {column.replace(/_/g, " ")}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {report.data.rows.slice(0, 200).map((row, index) => (
                  <tr key={index}>
                    {report.data!.columns.map((column) => (
                      <td key={column} className={typeof row[column] === "number" ? "num text-right" : "text-[12.5px]"}>
                        {renderCell(column, row[column])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {report.data && Object.keys(report.data.totals).length ? (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-line px-4 py-2.5">
            {Object.entries(report.data.totals).map(([key, value]) => (
              <span key={key} className="flex items-baseline gap-1.5">
                <span className="eyebrow">{key.replace(/_/g, " ")}</span>
                <span className="num text-[12.5px] text-ink">{key.includes("rate") ? fmt.pct(value) : fmt.int(value)}</span>
              </span>
            ))}
            {report.data.rows.length > 200 ? <span className="text-ink-3">showing 200 of {fmt.int(report.data.rows.length)} rows — export for the rest</span> : null}
          </div>
        ) : null}
      </Panel>

      <Panel>
        <PanelHeader title="Scheduled reports" subtitle="Cron is evaluated in UTC by the worker." />
        {!schedules.data?.length ? (
          <EmptyState title="No schedules" body="A weekly usage report to a Slack channel is usually enough to keep consumers honest about what they call." />
        ) : (
          <ul className="divide-y divide-[#17171a]">
            {schedules.data.map((schedule) => (
              <li key={schedule.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5">
                <div className="min-w-0">
                  <p className="text-[12.5px] text-ink">{schedule.name}</p>
                  <p className="text-[11.5px] text-ink-3">
                    {schedule.kind} · {schedule.fmt.toUpperCase()} · <span className="mono">{schedule.cron ?? "manual"}</span> · {schedule.channel}
                    {schedule.destination ? ` → ${schedule.destination.slice(0, 42)}` : ""}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {schedule.last_run_at ? <span className="text-[11.5px] text-ink-3">ran {fmt.ago(schedule.last_run_at)}</span> : <Badge>never run</Badge>}
                  {schedule.last_artifact_url ? (
                    <a className="btn btn-sm" href={schedule.last_artifact_url} target="_blank" rel="noreferrer">
                      Latest
                    </a>
                  ) : null}
                  <Button size="sm" loading={runNow.isPending && runNow.variables === schedule.id} onClick={() => runNow.mutate(schedule.id)}>
                    Run now
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => remove.mutate(schedule.id)}>
                    Delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <ScheduleDialog
        open={scheduling}
        defaults={{ kind, group_by: groupBy, since_hours: hours }}
        onClose={() => setScheduling(false)}
        onCreated={() => queryClient.invalidateQueries({ queryKey: ["schedules"] })}
      />
    </div>
  );
}

function renderCell(column: string, value: unknown) {
  if (value === null || value === undefined) return "—";
  if (column.includes("rate")) return fmt.pct(Number(value));
  if (column.endsWith("_ms")) return fmt.ms(Number(value));
  if (typeof value === "number") return fmt.int(value);
  if (column.includes("seen") || column.includes("at") || column === "bucket") return fmt.when(String(value));
  const text = String(value);
  return text.length > 80 ? `${text.slice(0, 77)}…` : text;
}

function ScheduleDialog({ open, defaults, onClose, onCreated }: { open: boolean; defaults: { kind: string; group_by: string; since_hours: number }; onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({ name: "", fmt: "csv", cron: "0 7 * * 1", channel: "slack", destination: "", enabled: true });

  const create = useMutation({
    mutationFn: () => api("/v1/reports/schedules", { body: { ...form, kind: defaults.kind, params: { group_by: defaults.group_by, since_hours: defaults.since_hours } } }),
    onSuccess: () => {
      onCreated();
      onClose();
    },
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Schedule this report"
      subtitle={`${defaults.kind} · ${defaults.group_by} · last ${defaults.since_hours} h`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={create.isPending} disabled={!form.name} onClick={() => create.mutate()}>
            Create schedule
          </Button>
        </>
      }
    >
      <div className="space-y-3.5">
        <Field label="Name">
          <input className="input" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Weekly usage by consumer" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Format">
            <select className="select" value={form.fmt} onChange={(event) => setForm({ ...form, fmt: event.target.value })}>
              <option value="csv">CSV</option>
              <option value="pdf">PDF</option>
            </select>
          </Field>
          <Field label="Cron (UTC)" hint="minute hour day-of-month month day-of-week">
            <input className="input mono" value={form.cron} onChange={(event) => setForm({ ...form, cron: event.target.value })} />
          </Field>
        </div>
        <div className="grid grid-cols-[110px_1fr] gap-3">
          <Field label="Deliver via">
            <select className="select" value={form.channel} onChange={(event) => setForm({ ...form, channel: event.target.value })}>
              <option value="none">nowhere</option>
              <option value="slack">Slack</option>
              <option value="webhook">Webhook</option>
              <option value="email">Email</option>
            </select>
          </Field>
          <Field label="Destination">
            <input className="input" value={form.destination} onChange={(event) => setForm({ ...form, destination: event.target.value })} disabled={form.channel === "none"} />
          </Field>
        </div>
        <ErrorNote error={create.error} />
      </div>
    </Modal>
  );
}
