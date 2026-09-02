import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { can, useProjects, useRole } from "../lib/hooks";
import { Badge, Button, CodeBlock, EmptyState, ErrorNote, Field, Modal, Panel, PanelHeader, Spinner, useToast } from "../components/ui";

type Key = {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  rate_limit_rpm: number;
  monthly_quota: number;
  used_this_month: number;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
};

export default function Keys() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const projects = useProjects();
  const projectId = projects.data?.find((project) => project.slug === localStorage.getItem("aperture.project"))?.id;
  const role = useRole(projectId);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<(Key & { key: string }) | null>(null);

  const keys = useQuery<Key[]>({ queryKey: ["keys"], queryFn: () => api("/v1/keys") });
  const models = useQuery<{ id: string; slug: string }[]>({ queryKey: ["models-lite"], queryFn: () => api("/v1/models", { query: { limit: 200 } }) });

  const revoke = useMutation({
    mutationFn: (id: string) => api(`/v1/keys/${id}/revoke`, { method: "POST" }),
    onSuccess: () => {
      toast("Key revoked. Calls using it now fail with api_key_revoked.");
      queryClient.invalidateQueries({ queryKey: ["keys"] });
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">API keys</h1>
          <p className="mt-0.5 text-[12.5px] text-ink-3">One key per product. Scope it to the models that product actually calls.</p>
        </div>
        {can(role, "admin") ? (
          <Button variant="primary" onClick={() => setCreating(true)}>
            New key
          </Button>
        ) : null}
      </div>

      <Panel>
        {keys.isLoading ? (
          <div className="flex h-32 items-center justify-center text-ink-3">
            <Spinner size={18} />
          </div>
        ) : !keys.data?.length ? (
          <EmptyState
            title="No API keys"
            body="Products authenticate with a key, not with a user session. Issue one per consumer so you can revoke and rate-limit them independently."
            action={can(role, "admin") ? <Button variant="primary" onClick={() => setCreating(true)}>New key</Button> : undefined}
          />
        ) : (
          <div className="scroll-x">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Prefix</th>
                  <th>Scope</th>
                  <th className="text-right">Rate limit</th>
                  <th className="text-right">This month</th>
                  <th>Last used</th>
                  <th>Expires</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {keys.data.map((key) => (
                  <tr key={key.id} className={key.revoked_at ? "opacity-50" : ""}>
                    <td className="text-[12.5px] text-ink">{key.name}</td>
                    <td className="mono text-[11.5px] text-ink-3">{key.prefix}</td>
                    <td>
                      {key.scopes.includes("*") ? (
                        <Badge kind="warn">every model</Badge>
                      ) : (
                        <span className="flex flex-wrap gap-1">
                          {key.scopes.slice(0, 3).map((scope) => (
                            <Badge key={scope}>{models.data?.find((model) => model.id === scope)?.slug ?? scope}</Badge>
                          ))}
                          {key.scopes.length > 3 ? <Badge>+{key.scopes.length - 3}</Badge> : null}
                        </span>
                      )}
                    </td>
                    <td className="num text-right text-[12px]">{key.rate_limit_rpm ? `${fmt.int(key.rate_limit_rpm)}/min` : "unlimited"}</td>
                    <td className="num text-right text-[12px]">
                      {fmt.int(key.used_this_month)}
                      {key.monthly_quota ? <span className="text-ink-3"> / {fmt.int(key.monthly_quota)}</span> : null}
                    </td>
                    <td className="text-[11.5px] text-ink-3">{key.last_used_at ? fmt.ago(key.last_used_at) : "never"}</td>
                    <td className="text-[11.5px] text-ink-3">{key.expires_at ? fmt.when(key.expires_at) : "—"}</td>
                    <td className="text-right">
                      {key.revoked_at ? (
                        <Badge kind="bad">revoked</Badge>
                      ) : can(role, "admin") ? (
                        <Button size="sm" variant="danger" onClick={() => revoke.mutate(key.id)}>
                          Revoke
                        </Button>
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
        <PanelHeader title="Calling the API" subtitle="Substitute your key and a model slug. A tag resolves at request time." />
        <div className="space-y-3 px-4 py-4">
          <CodeBlock
            language="curl"
            code={`curl -X POST "${window.location.origin}/v1/models/part-detection/production/predict" \\
  -H "x-api-key: $APERTURE_KEY" \\
  -F "images=@damage.jpg" \\
  -F 'overrides={"score_threshold":0.4}'`}
          />
          <CodeBlock
            language="python"
            code={`import httpx

with open("damage.jpg", "rb") as fh:
    response = httpx.post(
        "${window.location.origin}/v1/models/part-detection/production/predict",
        headers={"x-api-key": APERTURE_KEY},
        files={"images": fh},
        timeout=30,
    )
response.raise_for_status()
payload = response.json()
print(payload["model_version"], payload["latency"]["total_ms"])
for det in payload["results"][0]["detections"]:
    print(det["class_name"], det["score"], det["box"])`}
          />
          <CodeBlock
            language="typescript"
            code={`const form = new FormData();
form.append("images", file);

const response = await fetch(
  "${window.location.origin}/v1/models/part-detection/production/predict",
  { method: "POST", headers: { "x-api-key": APERTURE_KEY }, body: form },
);
if (!response.ok) {
  const { error } = await response.json();
  throw new Error(\`\${error.code}: \${error.message}\`);
}
const { model_version, results, latency } = await response.json();`}
          />
        </div>
      </Panel>

      <CreateKey
        open={creating}
        models={models.data ?? []}
        onClose={() => setCreating(false)}
        onCreated={(key) => {
          setCreated(key);
          queryClient.invalidateQueries({ queryKey: ["keys"] });
        }}
      />

      <Modal
        open={Boolean(created)}
        onClose={() => setCreated(null)}
        title="Copy this key now"
        subtitle="Aperture stores only a hash of it. If it is lost, issue a new one."
        footer={
          <Button variant="primary" onClick={() => setCreated(null)}>
            Done
          </Button>
        }
      >
        {created ? <CodeBlock language="key" code={created.key} /> : null}
      </Modal>
    </div>
  );
}

function CreateKey({ open, models, onClose, onCreated }: { open: boolean; models: { id: string; slug: string }[]; onClose: () => void; onCreated: (key: Key & { key: string }) => void }) {
  const [form, setForm] = useState({ name: "", scopes: [] as string[], rate_limit_rpm: 600, monthly_quota: 0, expires_in_days: null as number | null });
  const [allModels, setAllModels] = useState(true);

  const create = useMutation({
    mutationFn: () => api<Key & { key: string }>("/v1/keys", { body: { ...form, scopes: allModels ? ["*"] : form.scopes } }),
    onSuccess: (key) => {
      onCreated(key);
      onClose();
      setForm({ name: "", scopes: [], rate_limit_rpm: 600, monthly_quota: 0, expires_in_days: null });
    },
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New API key"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={create.isPending} disabled={!form.name || (!allModels && !form.scopes.length)} onClick={() => create.mutate()}>
            Create key
          </Button>
        </>
      }
    >
      <div className="space-y-3.5">
        <Field label="Name" hint="The consuming product or service.">
          <input className="input" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="claims-web" />
        </Field>

        <div>
          <label className="flex cursor-pointer items-start gap-2 text-[12.5px] text-ink-2">
            <input type="checkbox" className="mt-0.5" checked={allModels} onChange={(event) => setAllModels(event.target.checked)} />
            <span>
              Every model in this project
              <span className="block text-[11.5px] text-ink-3">Convenient, but a leaked key then reaches everything. Prefer an explicit list.</span>
            </span>
          </label>
          {!allModels ? (
            <div className="mt-2 max-h-40 overflow-y-auto scroll-y rounded-lg border border-line p-2">
              {models.map((model) => (
                <label key={model.id} className="flex cursor-pointer items-center gap-2 px-1 py-1 text-[12.5px] text-ink-2">
                  <input
                    type="checkbox"
                    checked={form.scopes.includes(model.id)}
                    onChange={(event) =>
                      setForm({ ...form, scopes: event.target.checked ? [...form.scopes, model.id] : form.scopes.filter((scope) => scope !== model.id) })
                    }
                  />
                  <span className="mono">{model.slug}</span>
                </label>
              ))}
            </div>
          ) : null}
        </div>

        <div className="grid grid-cols-3 gap-3">
          <Field label="Rate limit / min" hint="0 = none">
            <input className="input num" type="number" min={0} value={form.rate_limit_rpm} onChange={(event) => setForm({ ...form, rate_limit_rpm: Number(event.target.value) })} />
          </Field>
          <Field label="Monthly quota" hint="0 = none">
            <input className="input num" type="number" min={0} value={form.monthly_quota} onChange={(event) => setForm({ ...form, monthly_quota: Number(event.target.value) })} />
          </Field>
          <Field label="Expires in days" hint="blank = never">
            <input className="input num" type="number" min={1} value={form.expires_in_days ?? ""} onChange={(event) => setForm({ ...form, expires_in_days: event.target.value ? Number(event.target.value) : null })} />
          </Field>
        </div>
        <ErrorNote error={create.error} />
      </div>
    </Modal>
  );
}
