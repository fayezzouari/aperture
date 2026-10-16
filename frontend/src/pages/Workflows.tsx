import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { can, useRole } from "../lib/hooks";
import { sanitizeSlug, slugProblem, slugify } from "../lib/slug";
import { Badge, Button, CodeBlock, EmptyState, ErrorNote, Field, Modal, Panel, PanelHeader, Spinner } from "../components/ui";

type Workflow = {
  id: string;
  slug: string;
  name: string;
  description: string;
  revision: number;
  status: string;
  problems: string[];
  models_used: string[];
  runs_24h: number;
  error_rate_24h: number;
  p95_ms_24h: number | null;
  updated_at: string;
};

export default function Workflows() {
  const queryClient = useQueryClient();
  const role = useRole();
  const [creating, setCreating] = useState(false);

  const workflows = useQuery<Workflow[]>({ queryKey: ["workflows"], queryFn: () => api("/v1/workflows") });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">Workflows</h1>
          <p className="mt-0.5 max-w-2xl text-[12.5px] leading-relaxed text-ink-3">
            Chain several models into one endpoint — detect a part, crop to it, classify the damage — so products call one URL instead of
            re-implementing the pipeline.
          </p>
        </div>
        {can(role, "developer") ? (
          <Button variant="primary" onClick={() => setCreating(true)}>
            New workflow
          </Button>
        ) : null}
      </div>

      <Panel>
        {workflows.isLoading ? (
          <div className="flex h-32 items-center justify-center text-ink-3">
            <Spinner size={18} />
          </div>
        ) : workflows.error ? (
          <div className="p-4">
            <ErrorNote error={workflows.error} />
          </div>
        ) : !workflows.data?.length ? (
          <EmptyState
            title="No workflows yet"
            body="A workflow is a graph: images in, models and transforms in the middle, a named result out. Build one on the canvas, publish it, and call it like any other endpoint."
            action={can(role, "developer") ? <Button variant="primary" onClick={() => setCreating(true)}>New workflow</Button> : undefined}
          />
        ) : (
          <div className="scroll-x">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Workflow</th>
                  <th>State</th>
                  <th>Models</th>
                  <th className="text-right">Runs 24 h</th>
                  <th className="text-right">Errors</th>
                  <th className="text-right">p95</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {workflows.data.map((workflow) => (
                  <tr key={workflow.id}>
                    <td>
                      <Link to={`/workflows/${workflow.slug}`} className="block min-w-0">
                        <span className="block truncate text-[13px] text-ink hover:underline">{workflow.name}</span>
                        <span className="mono block truncate text-[11px] text-ink-3">{workflow.slug}</span>
                      </Link>
                    </td>
                    <td>
                      <Badge kind={workflow.status === "published" ? "good" : "neutral"}>
                        {workflow.status === "published" ? `r${workflow.revision}` : "draft"}
                      </Badge>
                      {workflow.problems.length ? <Badge kind="warn" className="ml-1.5">{workflow.problems.length} issue{workflow.problems.length > 1 ? "s" : ""}</Badge> : null}
                    </td>
                    <td className="mono max-w-[230px] truncate text-[11.5px] text-ink-2">{workflow.models_used.join(" → ") || "—"}</td>
                    <td className="num text-right text-[12.5px]">{fmt.int(workflow.runs_24h)}</td>
                    <td className={`num text-right text-[12.5px] ${workflow.error_rate_24h > 0.02 ? "text-bad" : workflow.error_rate_24h ? "text-warn" : "text-ink-3"}`}>
                      {workflow.runs_24h ? fmt.pct(workflow.error_rate_24h) : "—"}
                    </td>
                    <td className="num text-right text-[12.5px] text-ink-2">{fmt.ms(workflow.p95_ms_24h)}</td>
                    <td className="text-[11.5px] text-ink-3">{fmt.ago(workflow.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel>
        <PanelHeader title="Calling a workflow" subtitle="Same auth and same error contract as a single model." />
        <div className="px-4 py-4">
          <CodeBlock
            language="curl"
            code={`curl -X POST "${window.location.origin}/v1/workflows/damage-report/run" \\
  -H "x-api-key: $APERTURE_KEY" \\
  -F "images=@photo.jpg"

# the response names every stage that ran
# {
#   "outputs": { "panels": [...], "severity": [...] },
#   "node_stats": [ { "label": "Detect panels", "ms": 61.2, "items_out": 3 },
#                   { "label": "Crop", "ms": 24.9, "items_out": 3 },
#                   { "label": "Classify damage", "ms": 812.4, "items_out": 3 } ],
#   "model_calls": 2, "total_ms": 901.7
# }`}
          />
        </div>
      </Panel>

      <CreateWorkflow open={creating} onClose={() => setCreating(false)} onCreated={() => queryClient.invalidateQueries({ queryKey: ["workflows"] })} />
    </div>
  );
}

function CreateWorkflow({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", slug: "", description: "", owner: "" });
  const [slugEdited, setSlugEdited] = useState(false);

  const create = useMutation({
    mutationFn: () => api<Workflow>("/v1/workflows", { body: form }),
    onSuccess: (workflow) => {
      onCreated();
      onClose();
      setForm({ name: "", slug: "", description: "", owner: "" });
      setSlugEdited(false);
      navigate(`/workflows/${workflow.slug}`);
    },
  });

  const problem = slugProblem(form.slug);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New workflow"
      subtitle="You get a starter graph — images in, one model, one result — to build from."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={create.isPending}
            disabled={!form.name || !form.slug || Boolean(problem)}
            onClick={() => create.mutate()}
          >
            Create and open the canvas
          </Button>
        </>
      }
    >
      <div className="space-y-3.5">
        <Field label="Name">
          <input
            className="input"
            value={form.name}
            onChange={(event) => {
              const name = event.target.value;
              setForm((current) => ({
                ...current,
                name,
                // keep following the name until the slug is edited by hand
                slug: slugEdited ? current.slug : slugify(name),
              }));
            }}
            placeholder="Damage report"
          />
        </Field>
        <Field label="Slug" hint={problem ?? "Derived from the name. Products call /v1/workflows/{slug}/run."}>
          <input
            className="input mono"
            value={form.slug}
            onChange={(event) => {
              const next = sanitizeSlug(event.target.value);
              // clearing it hands control back to the name
              setSlugEdited(next.length > 0);
              setForm((current) => ({ ...current, slug: next }));
            }}
            placeholder="damage-report"
            spellCheck={false}
          />
        </Field>
        <Field label="Owner">
          <input className="input" value={form.owner} onChange={(event) => setForm({ ...form, owner: event.target.value })} />
        </Field>
        <Field label="Description">
          <textarea className="textarea" rows={3} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
        </Field>
        <ErrorNote error={create.error} />
      </div>
    </Modal>
  );
}
