import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { JsonView } from "../components/bits";
import { Badge, EmptyState, ErrorNote, Panel, Spinner } from "../components/ui";
import { Segmented, TIME_WINDOWS, Toolbar } from "../components/toolbar";

type Entry = {
  id: string;
  actor_type: string;
  actor_label: string;
  action: string;
  target_type: string;
  target_label: string;
  payload: Record<string, unknown>;
  client_ip: string | null;
  created_at: string;
};

export default function Audit() {
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [sinceHours, setSinceHours] = useState<number>(720);
  const [expanded, setExpanded] = useState<string | null>(null);

  const actions = useQuery<string[]>({ queryKey: ["audit-actions"], queryFn: () => api("/v1/audit/actions"), staleTime: 600_000 });
  const entries = useQuery<Entry[]>({
    queryKey: ["audit", action, actor, sinceHours],
    queryFn: () => api("/v1/audit", { query: { action: action || undefined, actor: actor || undefined, limit: 200, since_hours: sinceHours } }),
  });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="page-title">Audit log</h1>
        <p className="mt-0.5 text-[12.5px] text-ink-3">Every mutating action: uploads, publishes, retags, deploys, key issuance, purges. Append-only.</p>
      </div>

      <Panel className="overflow-hidden">
        <Toolbar
          trailing={
            <span className="text-[12px] text-ink-3">
              {entries.data ? `${entries.data.length} entr${entries.data.length === 1 ? "y" : "ies"}` : ""}
            </span>
          }
        >
          <Segmented
            label="Time window"
            options={[...TIME_WINDOWS.map((o) => ({ value: o.value as number, label: o.label })),
                      { value: 24 * 90, label: "90 d" }]}
            value={sinceHours}
            onChange={setSinceHours}
          />
          <select className="select w-auto" value={action} onChange={(event) => setAction(event.target.value)} aria-label="Action">
            <option value="">All actions</option>
            {(actions.data ?? []).map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
          <input className="input w-[200px]" placeholder="Filter by actor" value={actor} onChange={(event) => setActor(event.target.value)} aria-label="Filter by actor" />
        </Toolbar>

        {entries.isLoading ? (
          <div className="flex h-32 items-center justify-center text-ink-3">
            <Spinner size={18} />
          </div>
        ) : entries.error ? (
          <div className="p-4">
            <ErrorNote error={entries.error} />
          </div>
        ) : !entries.data?.length ? (
          <EmptyState title="Nothing recorded" body="Actions appear here as soon as anyone changes something." />
        ) : (
          <div className="scroll-x">
            <table className="data-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Actor</th>
                  <th>Action</th>
                  <th>Target</th>
                  <th>Detail</th>
                  <th>IP</th>
                </tr>
              </thead>
              <tbody>
                {entries.data.map((entry) => (
                  <tr key={entry.id} className="cursor-pointer" onClick={() => setExpanded(expanded === entry.id ? null : entry.id)}>
                    <td className="whitespace-nowrap text-[11.5px] text-ink-3">{fmt.when(entry.created_at)}</td>
                    <td className="text-[12px] text-ink">
                      {entry.actor_label || "—"}
                      {entry.actor_type !== "user" ? <Badge className="ml-1.5">{entry.actor_type}</Badge> : null}
                    </td>
                    <td>
                      <span className="mono text-[11.5px] text-ink-2">{entry.action}</span>
                    </td>
                    <td className="max-w-[220px] truncate text-[12px] text-ink-2">{entry.target_label || entry.target_type}</td>
                    <td className="max-w-[300px] truncate text-[11.5px] text-ink-3">
                      {expanded === entry.id ? <JsonView value={entry.payload} /> : Object.keys(entry.payload ?? {}).length ? JSON.stringify(entry.payload) : "—"}
                    </td>
                    <td className="mono text-[11px] text-ink-3">{entry.client_ip ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
