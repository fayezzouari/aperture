import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { can, useInstance, useRole } from "../lib/hooks";
import { sanitizeSlug, slugProblem, slugify } from "../lib/slug";
import { TagRail } from "../components/bits";
import { Badge, Button, EmptyState, ErrorNote, Field, Modal, Panel, Spinner, Status } from "../components/ui";
import { Toolbar } from "../components/toolbar";

type ModelRow = {
  id: string;
  slug: string;
  name: string;
  task: string;
  owner: string;
  framework: string;
  labels: string[];
  version_count: number;
  latest_version: number | null;
  tags: { name: string; version: number | null }[];
  deployed_versions: number[];
  replicas_ready: number;
  replicas_desired: number;
  requests_24h: number;
  error_rate_24h: number;
  p95_ms_24h: number | null;
  updated_at: string;
};

const TASKS = ["detection", "segmentation", "classification", "keypoints", "ocr", "embedding"];

export default function Models() {
  const queryClient = useQueryClient();
  const role = useRole();

  const [search, setSearch] = useState("");
  const [task, setTask] = useState("");
  const [deployedOnly, setDeployedOnly] = useState(false);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);

  const models = useQuery<ModelRow[]>({
    queryKey: ["models", search, task, deployedOnly],
    queryFn: () => api("/v1/models", { query: { q: search || undefined, task: task || undefined, deployed: deployedOnly || undefined, limit: 200 } }),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">Models</h1>
          <p className="mt-0.5 text-[12.5px] text-ink-3">Every model in this workspace, with what is live and how it is behaving.</p>
        </div>
        {can(role, "developer") ? (
          <div className="flex items-center gap-2">
            <Button onClick={() => setImporting(true)}>Import from Roboflow</Button>
            <Button variant="primary" onClick={() => setCreating(true)}>
              New model
            </Button>
          </div>
        ) : null}
      </div>

      <Panel className="overflow-hidden">
        <Toolbar
          trailing={
            <span className="text-[12px] text-ink-3">
              {models.data ? `${models.data.length} model${models.data.length === 1 ? "" : "s"}` : ""}
            </span>
          }
        >
          <input
            className="input w-[220px]"
            placeholder="Search slug, name, owner…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search models"
          />
          <select className="select w-auto" value={task} onChange={(event) => setTask(event.target.value)} aria-label="Filter by task">
            <option value="">All tasks</option>
            {TASKS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
          <label className="flex cursor-pointer items-center gap-2 text-[12.5px] text-ink-2">
            <input type="checkbox" checked={deployedOnly} onChange={(event) => setDeployedOnly(event.target.checked)} />
            Live only
          </label>
        </Toolbar>

        {models.isLoading ? (
          <div className="flex h-40 items-center justify-center text-ink-3">
            <Spinner size={18} />
          </div>
        ) : models.error ? (
          <div className="p-4">
            <ErrorNote error={models.error} />
          </div>
        ) : !models.data?.length ? (
          <EmptyState
            title={search || task ? "No model matches those filters" : "No models yet"}
            body={
              search || task
                ? "Clear the filters to see everything in the workspace."
                : "A model is a named slot with its own version history. Create one, then upload weights as version 1."
            }
            action={can(role, "developer") ? <Button variant="primary" onClick={() => setCreating(true)}>New model</Button> : undefined}
          />
        ) : (
          <div className="scroll-x">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Task</th>
                  <th>Tags</th>
                  <th>Live</th>
                  <th>Replicas</th>
                  <th className="text-right">Requests 24 h</th>
                  <th className="text-right">Errors</th>
                  <th className="text-right">p95</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {models.data.map((model) => (
                  <tr key={model.id}>
                    <td>
                      <Link to={`/models/${model.slug}`} className="block min-w-0">
                        <span className="block truncate text-[13px] text-ink hover:underline">{model.name}</span>
                        <span className="mono block truncate text-[11px] text-ink-3">{model.slug}</span>
                      </Link>
                    </td>
                    <td>
                      <Badge>{model.task}</Badge>
                    </td>
                    <td>
                      <TagRail tags={model.tags.map((tag) => `${tag.name}→v${tag.version ?? "?"}`)} />
                    </td>
                    <td className="num text-[12px] text-ink-2">
                      {model.deployed_versions.length ? model.deployed_versions.map((version) => `v${version}`).join(", ") : <span className="text-ink-3">—</span>}
                    </td>
                    <td>
                      {model.replicas_desired ? (
                        <Status value={model.replicas_ready >= model.replicas_desired ? "live" : model.replicas_ready ? "warming" : "pending"} />
                      ) : (
                        <span className="text-[12px] text-ink-3">not deployed</span>
                      )}
                      {model.replicas_desired ? (
                        <span className="num ml-1.5 text-[11px] text-ink-3">
                          {model.replicas_ready}/{model.replicas_desired}
                        </span>
                      ) : null}
                    </td>
                    <td className="num text-right text-[12.5px]">{fmt.int(model.requests_24h)}</td>
                    <td className={`num text-right text-[12.5px] ${model.error_rate_24h > 0.02 ? "text-bad" : model.error_rate_24h > 0 ? "text-warn" : "text-ink-3"}`}>
                      {model.requests_24h ? fmt.pct(model.error_rate_24h) : "—"}
                    </td>
                    <td className="num text-right text-[12.5px] text-ink-2">{fmt.ms(model.p95_ms_24h)}</td>
                    <td className="text-[11.5px] text-ink-3">{fmt.ago(model.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <ImportRoboflow open={importing} onClose={() => setImporting(false)} onStarted={() => queryClient.invalidateQueries({ queryKey: ["models"] })} />
      <CreateModel open={creating} onClose={() => setCreating(false)} onCreated={() => queryClient.invalidateQueries({ queryKey: ["models"] })} />
    </div>
  );
}

function CreateModel({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({ name: "", slug: "", task: "detection", owner: "", description: "" });
  const [slugEdited, setSlugEdited] = useState(false);

  const reset = () => {
    setForm({ name: "", slug: "", task: "detection", owner: "", description: "" });
    setSlugEdited(false);
  };

  const create = useMutation({
    mutationFn: () => api<ModelRow>("/v1/models", { body: { ...form, labels: [] } }),
    onSuccess: () => {
      onCreated();
      onClose();
      reset();
    },
  });

  const problem = slugProblem(form.slug);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New model"
      subtitle="The slug is what products call. It cannot change later, so pick the name the API should carry."
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
            Create model
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
            placeholder="Bird species detector"
          />
        </Field>
        <Field
          label="Slug"
          hint={problem ?? "Derived from the name. Products call this, and it cannot change later."}
        >
          <input
            className="input mono"
            value={form.slug}
            onChange={(event) => {
              const next = sanitizeSlug(event.target.value);
              // clearing it hands control back to the name
              setSlugEdited(next.length > 0);
              setForm((current) => ({ ...current, slug: next }));
            }}
            placeholder="bird-species"
            spellCheck={false}
          />
        </Field>
        <Field label="Task" hint="Fixes the response shape products can rely on.">
          <select className="select" value={form.task} onChange={(event) => setForm({ ...form, task: event.target.value })}>
            {TASKS.map((task) => (
              <option key={task} value={task}>
                {task}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Owner" hint="Who to ask when this model misbehaves.">
          <input className="input" value={form.owner} onChange={(event) => setForm({ ...form, owner: event.target.value })} placeholder="vision team" />
        </Field>
        <Field label="Description">
          <textarea className="textarea" rows={3} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
        </Field>
        <ErrorNote error={create.error} />
      </div>
    </Modal>
  );
}

/**
 * Import a trained model straight out of Roboflow.
 *
 * The point of this over uploading the weights by hand is the two things that
 * are easy to get wrong and impossible to see afterwards: the class list has to
 * come from the same version the weights did, and RF-DETR's dead leading logit
 * column has to be accounted for. Both are read from the version being imported.
 */
function ImportRoboflow({ open, onClose, onStarted }: { open: boolean; onClose: () => void; onStarted: () => void }) {
  const instance = useInstance();
  const [ref, setRef] = useState("");
  const [slug, setSlug] = useState("");
  const [apiKey, setApiKey] = useState("");
  const hasInstanceKey = Boolean(instance.data?.features?.roboflow_import);

  const start = useMutation({
    mutationFn: () =>
      api<{ id: string }>("/v1/imports/roboflow", {
        body: {
          ref: ref.trim(),
          ...(slug.trim() ? { slug: slug.trim() } : {}),
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        },
      }),
    onSuccess: () => {
      onStarted();
      onClose();
      setRef("");
      setSlug("");
      setApiKey("");
    },
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Import from Roboflow"
      subtitle="Pulls the weights and the version's own class list, converts to ONNX, and lands a draft version."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={start.isPending} disabled={!ref.trim()} onClick={() => start.mutate()}>
            Start import
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field
          label="Model reference"
          hint="Paste the page you were on — the version, the trained-model page, or workspace/project/version."
        >
          <input
            className="input mono"
            value={ref}
            placeholder="https://app.roboflow.com/workspace/project/models/project/13"
            onChange={(event) => setRef(event.target.value)}
          />
        </Field>
        <Field label="Model slug" hint="Optional. Defaults to the Roboflow project id; an existing model gains a new version.">
          <input className="input mono" value={slug} placeholder="bird-species" onChange={(event) => setSlug(event.target.value)} />
        </Field>
        {hasInstanceKey ? (
          <p className="hint">
            Using the instance&apos;s Roboflow key. Set one here only to import from a different account.
          </p>
        ) : (
          <p className="hint">
            This instance has no Roboflow key, so the import needs one. Set{" "}
            <span className="mono">AP_ROBOFLOW_API_KEY</span> to stop pasting it.
          </p>
        )}
        <Field label={hasInstanceKey ? "API key (override)" : "API key"}>
          <input
            className="input mono"
            type="password"
            value={apiKey}
            placeholder={hasInstanceKey ? "leave empty to use the instance key" : "your Roboflow API key"}
            onChange={(event) => setApiKey(event.target.value)}
          />
        </Field>
        <p className="hint">
          Runs on the converter, which has to be up:{" "}
          <span className="mono">docker compose --profile convert up -d converter</span>. Progress shows in Jobs.
        </p>
        <ErrorNote error={start.error} />
      </div>
    </Modal>
  );
}
