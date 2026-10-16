import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { can, useInstance, useMe, useRole } from "../lib/hooks";
import { Badge, Button, ErrorNote, Field, KeyValue, Panel, PanelHeader, Spinner, Tabs, useToast } from "../components/ui";

type User = { id: string; email: string; name: string; role: string; provider: string; is_active: boolean; last_login_at: string | null };
type CaptureSettings = {
  capture_sample_rate: number | null;
  capture_retention_days: number | null;
  effective_sample_rate: number;
  effective_retention_days: number;
};

export default function Settings() {
  const [tab, setTab] = useState("users");
  const me = useMe();
  const instance = useInstance();
  const role = useRole();

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-[19px] font-semibold tracking-tight">Settings</h1>
        <p className="mt-0.5 text-[12.5px] text-ink-3">
          Users, capture and retention for this instance. Your role is <span className="text-ink-2">{role}</span>.
        </p>
      </div>

      <Tabs
        tabs={[
          { key: "users", label: "Users" },
          { key: "privacy", label: "Capture & retention" },
          { key: "instance", label: "Instance" },
        ]}
        active={tab}
        onChange={setTab}
      />

      {tab === "users" ? <UsersTab role={role} myId={me.data?.id} /> : null}
      {tab === "privacy" ? <PrivacyTab role={role} /> : null}
      {tab === "instance" ? <InstanceTab instance={instance.data} /> : null}
    </div>
  );
}

function UsersTab({ role, myId }: { role: string; myId?: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [invite, setInvite] = useState({ email: "", role: "viewer" });
  const admin = can(role, "admin");

  const users = useQuery<User[]>({ queryKey: ["users"], queryFn: () => api("/v1/users"), enabled: admin });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["users"] });

  const inviteUser = useMutation({
    mutationFn: (body: { email: string; role: string }) => api("/v1/users", { method: "PUT", body }),
    onSuccess: () => {
      toast("User saved.");
      setInvite({ email: "", role: "viewer" });
      refresh();
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const update = useMutation({
    mutationFn: ({ id, ...body }: { id: string; role?: string; is_active?: boolean }) => api(`/v1/users/${id}`, { method: "PATCH", body }),
    onSuccess: refresh,
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const deactivate = useMutation({
    mutationFn: (id: string) => api(`/v1/users/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast("User deactivated.");
      refresh();
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  if (!admin) {
    return (
      <Panel>
        <PanelHeader title="Users" subtitle="viewer reads · developer uploads and deploys · admin manages keys, users and deletion." />
        <p className="px-4 py-4 text-[12.5px] text-ink-3">Only an admin can see and change who has access. Your role is {role}.</p>
      </Panel>
    );
  }

  return (
    <div className="space-y-4">
      <Panel>
        <PanelHeader title="Users" subtitle="viewer reads · developer uploads and deploys · admin manages keys, users and deletion." />
        {users.isLoading ? (
          <div className="flex h-24 items-center justify-center text-ink-3">
            <Spinner size={18} />
          </div>
        ) : users.isError ? (
          <div className="px-4 py-4">
            <ErrorNote error={users.error} />
          </div>
        ) : (
          <div className="scroll-x">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Provider</th>
                  <th>Role</th>
                  <th>Last sign-in</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(users.data ?? []).map((user) => {
                  const self = user.id === myId;
                  return (
                    <tr key={user.id} className={user.is_active ? "" : "opacity-50"}>
                      <td className="text-[12.5px] text-ink">
                        {user.email}
                        {self ? <span className="ml-2 text-[11px] text-ink-3">you</span> : null}
                      </td>
                      <td>
                        <Badge>{user.provider}</Badge>
                      </td>
                      <td>
                        <select
                          className="select w-[130px]"
                          value={user.role}
                          disabled={self || !user.is_active}
                          onChange={(event) => update.mutate({ id: user.id, role: event.target.value })}
                        >
                          <option value="viewer">viewer</option>
                          <option value="developer">developer</option>
                          <option value="admin">admin</option>
                        </select>
                      </td>
                      <td className="text-[12px] text-ink-3">{user.last_login_at ? fmt.ago(user.last_login_at) : "never"}</td>
                      <td className="text-right">
                        {self ? null : user.is_active ? (
                          <Button size="sm" variant="danger" loading={deactivate.isPending && deactivate.variables === user.id} onClick={() => deactivate.mutate(user.id)}>
                            Deactivate
                          </Button>
                        ) : (
                          <Button size="sm" onClick={() => update.mutate({ id: user.id, is_active: true })}>
                            Reactivate
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel>
        <PanelHeader title="Invite a user" subtitle="With SSO or LDAP enabled, inviting someone here sets their role before their first sign-in." />
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
          <Button variant="primary" loading={inviteUser.isPending} disabled={!invite.email} onClick={() => inviteUser.mutate(invite)}>
            Invite
          </Button>
        </div>
      </Panel>
    </div>
  );
}

function PrivacyTab({ role }: { role: string }) {
  const current = useQuery<CaptureSettings>({ queryKey: ["settings"], queryFn: () => api("/v1/settings") });

  if (!current.data) {
    return (
      <div className="flex h-24 items-center justify-center text-ink-3">
        {current.isError ? <ErrorNote error={current.error} /> : <Spinner size={18} />}
      </div>
    );
  }
  return <PrivacyForm role={role} current={current.data} />;
}

function PrivacyForm({ role, current }: { role: string; current: CaptureSettings }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState({
    capture_sample_rate: current.effective_sample_rate,
    capture_retention_days: current.effective_retention_days,
  });

  const save = useMutation({
    mutationFn: () => api("/v1/settings", { method: "PATCH", body: form }),
    onSuccess: () => {
      toast("Capture settings saved.");
      queryClient.invalidateQueries({ queryKey: ["settings"] });
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

function InstanceTab({ instance }: { instance?: { instance_name: string; env: string; version: string; limits: Record<string, number>; capture: Record<string, number>; features: Record<string, boolean> } }) {
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
        </div>
      </Panel>
    </div>
  );
}
