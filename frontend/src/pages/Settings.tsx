import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { can, useInstance, useMe, useProjects, useRole } from "../lib/hooks";
import { Badge, Button, ErrorNote, Field, KeyValue, Panel, PanelHeader, Spinner, Tabs, useToast } from "../components/ui";

type Member = { user_id: string; email: string; name: string; role: string; provider: string };

export default function Settings() {
  const [tab, setTab] = useState("project");
  const projects = useProjects();
  const me = useMe();
  const instance = useInstance();
  const active = projects.data?.find((project) => project.slug === localStorage.getItem("aperture.project")) ?? projects.data?.[0];
  const role = useRole(active?.id);

  if (!active) {
    return (
      <div className="flex h-40 items-center justify-center text-ink-3">
        <Spinner size={18} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-[19px] font-semibold tracking-tight">Settings</h1>
        <p className="mt-0.5 text-[12.5px] text-ink-3">
          Project, members, capture and retention for {active.name}. Your role here is{" "}
          <span className="text-ink-2">{role}</span>.
        </p>
      </div>

      <Tabs
        tabs={[
          { key: "project", label: "Project" },
          { key: "members", label: "Members" },
          { key: "privacy", label: "Capture & retention" },
          { key: "instance", label: "Instance" },
        ]}
        active={tab}
        onChange={setTab}
      />

      {tab === "project" ? <ProjectTab project={active} role={role} /> : null}
      {tab === "members" ? <MembersTab slug={active.slug} role={role} /> : null}
      {tab === "privacy" ? <PrivacyTab project={active} role={role} /> : null}
      {tab === "instance" ? <InstanceTab instance={instance.data} superadmin={Boolean(me.data?.is_superadmin)} /> : null}
    </div>
  );
}

function ProjectTab({ project, role }: { project: { slug: string; name: string; description: string }; role: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({ name: project.name, description: project.description });

  const save = useMutation({
    mutationFn: () => api(`/v1/projects/${project.slug}`, { method: "PATCH", body: form }),
    onSuccess: () => {
      toast("Project updated.");
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  return (
    <Panel>
      <PanelHeader title="Project" subtitle="Projects isolate models, keys and logs between teams. The slug is fixed once created." />
      <div className="max-w-lg space-y-3.5 px-4 py-4">
        <Field label="Slug">
          <input className="input mono" value={project.slug} disabled />
        </Field>
        <Field label="Name">
          <input className="input" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} disabled={!can(role, "admin")} />
        </Field>
        <Field label="Description">
          <textarea className="textarea" rows={3} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} disabled={!can(role, "admin")} />
        </Field>
        {can(role, "admin") ? (
          <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>
            Save
          </Button>
        ) : null}
        <ErrorNote error={save.error} />
      </div>
    </Panel>
  );
}

function MembersTab({ slug, role }: { slug: string; role: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [invite, setInvite] = useState({ email: "", role: "viewer" });

  const members = useQuery<Member[]>({ queryKey: ["members", slug], queryFn: () => api(`/v1/projects/${slug}/members`) });

  const upsert = useMutation({
    mutationFn: (body: { email: string; role: string }) => api(`/v1/projects/${slug}/members`, { method: "PUT", body }),
    onSuccess: () => {
      toast("Membership saved.");
      setInvite({ email: "", role: "viewer" });
      queryClient.invalidateQueries({ queryKey: ["members", slug] });
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const remove = useMutation({
    mutationFn: (userId: string) => api(`/v1/projects/${slug}/members/${userId}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["members", slug] }),
  });

  return (
    <div className="space-y-4">
      <Panel>
        <PanelHeader
          title="Members"
          subtitle="viewer reads · developer uploads and deploys · admin manages keys, members and deletion."
        />
        {members.isLoading ? (
          <div className="flex h-24 items-center justify-center text-ink-3">
            <Spinner size={18} />
          </div>
        ) : (
          <div className="scroll-x">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Provider</th>
                  <th>Role</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(members.data ?? []).map((member) => (
                  <tr key={member.user_id}>
                    <td className="text-[12.5px] text-ink">{member.email}</td>
                    <td>
                      <Badge>{member.provider}</Badge>
                    </td>
                    <td>
                      {can(role, "admin") ? (
                        <select
                          className="select w-[130px]"
                          value={member.role}
                          onChange={(event) => upsert.mutate({ email: member.email, role: event.target.value })}
                        >
                          <option value="viewer">viewer</option>
                          <option value="developer">developer</option>
                          <option value="admin">admin</option>
                        </select>
                      ) : (
                        <span className="text-[12.5px] text-ink-2">{member.role}</span>
                      )}
                    </td>
                    <td className="text-right">
                      {can(role, "admin") ? (
                        <Button size="sm" variant="danger" onClick={() => remove.mutate(member.user_id)}>
                          Remove
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

      {can(role, "admin") ? (
        <Panel>
          <PanelHeader title="Add a member" subtitle="With SSO enabled, adding someone here pre-assigns their role before their first sign-in." />
          <div className="flex flex-wrap items-end gap-3 px-4 py-4">
            <Field label="Email">
              <input className="input w-[260px]" value={invite.email} onChange={(event) => setInvite({ ...invite, email: event.target.value })} />
            </Field>
            <Field label="Role">
              <select className="select w-[140px]" value={invite.role} onChange={(event) => setInvite({ ...invite, role: event.target.value })}>
                <option value="viewer">viewer</option>
                <option value="developer">developer</option>
                <option value="admin">admin</option>
              </select>
            </Field>
            <Button variant="primary" loading={upsert.isPending} disabled={!invite.email} onClick={() => upsert.mutate(invite)}>
              Add
            </Button>
          </div>
        </Panel>
      ) : null}
    </div>
  );
}

function PrivacyTab({ project, role }: { project: { slug: string; capture_sample_rate: number | null; capture_retention_days: number | null }; role: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const instance = useInstance();
  const [form, setForm] = useState({
    capture_sample_rate: project.capture_sample_rate ?? instance.data?.capture.default_sample_rate ?? 0.05,
    capture_retention_days: project.capture_retention_days ?? instance.data?.capture.retention_days ?? 30,
  });

  const save = useMutation({
    mutationFn: () => api(`/v1/projects/${project.slug}`, { method: "PATCH", body: form }),
    onSuccess: () => {
      toast("Capture settings saved.");
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const purge = useMutation({
    mutationFn: () => api("/v1/jobs/captures/purge", { method: "POST", query: { before_hours: 0 } }),
    onSuccess: () => toast("Purge queued — expired captures are hard-deleted from object storage."),
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const exportFlagged = useMutation({
    mutationFn: () => api("/v1/jobs/exports/flagged", { method: "POST", query: { fmt: "coco" } }),
    onSuccess: () => toast("Export queued — download it from the Jobs page when it finishes."),
    onError: (error) => toast((error as Error).message, "bad"),
  });

  return (
    <div className="space-y-4">
      <Panel>
        <PanelHeader
          title="Payload capture"
          subtitle="Captured images make the explorer and retraining exports possible. Errors are always captured; everything else is sampled."
        />
        <div className="max-w-lg space-y-3.5 px-4 py-4">
          <Field label={`Sample rate: ${(form.capture_sample_rate * 100).toFixed(1)}%`} hint="5% is a reasonable default: enough to spot a pattern, small enough to keep storage sane.">
            <input
              type="range"
              min={0}
              max={100}
              value={Math.round(form.capture_sample_rate * 100)}
              onChange={(event) => setForm({ ...form, capture_sample_rate: Number(event.target.value) / 100 })}
              className="w-full"
              disabled={!can(role, "admin")}
            />
          </Field>
          <Field label="Retention (days)" hint="After this, captured images are hard-deleted. Flagged records are kept so a retraining set is not lost.">
            <input
              className="input num"
              type="number"
              min={1}
              max={3650}
              value={form.capture_retention_days}
              onChange={(event) => setForm({ ...form, capture_retention_days: Number(event.target.value) })}
              disabled={!can(role, "admin")}
            />
          </Field>
          {can(role, "admin") ? (
            <div className="flex gap-2">
              <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>
                Save
              </Button>
              <Button variant="danger" loading={purge.isPending} onClick={() => purge.mutate()}>
                Purge expired now
              </Button>
            </div>
          ) : null}
          <ErrorNote error={save.error} />
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="Retraining export" subtitle="Flagged predictions leave as a COCO dataset: images plus the model's boxes to correct." />
        <div className="px-4 py-4">
          <Button loading={exportFlagged.isPending} onClick={() => exportFlagged.mutate()} disabled={!can(role, "developer")}>
            Export flagged predictions
          </Button>
          <p className="hint mt-2">
            The boxes in the export are predictions, not verified labels. Correct them in your annotation tool before training — the manifest keeps the request id so you can
            trace any sample back to the request that produced it.
          </p>
        </div>
      </Panel>
    </div>
  );
}

function InstanceTab({ instance, superadmin }: { instance?: { instance_name: string; env: string; version: string; limits: Record<string, number>; capture: Record<string, number>; features: Record<string, boolean> }; superadmin: boolean }) {
  if (!instance) {
    return (
      <div className="flex h-24 items-center justify-center text-ink-3">
        <Spinner size={18} />
      </div>
    );
  }
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Panel>
        <PanelHeader title="Limits" subtitle="Set with environment variables on the API container." />
        <div className="px-4 py-3">
          <KeyValue
            items={[
              ["Max request payload", `${instance.limits.max_payload_mb} MB`],
              ["Max images per request", String(instance.limits.max_batch_images)],
              ["Max weights upload", `${(instance.limits.max_upload_mb / 1024).toFixed(0)} GB`],
              ["Upload chunk size", `${instance.limits.upload_chunk_mb} MB`],
              ["Default capture rate", fmt.pct(instance.capture.default_sample_rate, 1)],
              ["Default retention", `${instance.capture.retention_days} days`],
            ]}
          />
        </div>
      </Panel>
      <Panel>
        <PanelHeader title="Roboflow" subtitle="Needed to import a trained model from a Roboflow project." />
        <div className="px-4 py-3">
          {instance.features.roboflow_import ? (
            <>
              <Badge kind="good">key configured</Badge>
              <p className="hint mt-2">
                Imports use the instance key, so nobody has to paste one. It is set with{" "}
                <span className="mono">AP_ROBOFLOW_API_KEY</span> and is never returned by the API.
              </p>
            </>
          ) : (
            <>
              <Badge>no key</Badge>
              <p className="hint mt-2">
                Set <span className="mono">AP_ROBOFLOW_API_KEY</span> on the api and converter
                containers, or paste a key on the import itself — an import that carries its own key
                uses that one instead.
              </p>
            </>
          )}
          <p className="hint mt-2">
            Importing also needs the converter running:{" "}
            <span className="mono">docker compose --profile convert up -d converter</span>.
          </p>
        </div>
      </Panel>
      <Panel>
        <PanelHeader title="Features" subtitle={`${instance.instance_name} · ${instance.env} · v${instance.version}`} />
        <div className="px-4 py-3">
          <KeyValue
            items={Object.entries(instance.features).map(([key, value]) => [
              key.replace(/_/g, " "),
              value ? <Badge kind="good">enabled</Badge> : <Badge>off</Badge>,
            ])}
          />
          {superadmin ? (
            <p className="hint mt-3">
              You are an instance admin, so you can create projects through <span className="mono">POST /v1/projects</span> and see every project in the switcher.
            </p>
          ) : null}
        </div>
      </Panel>
    </div>
  );
}
