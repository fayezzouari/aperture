import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, sha256, uploadWeights } from "../lib/api";
import { fmt } from "../lib/format";
import { can, useInstance, useRole } from "../lib/hooks";
import { Dropzone, ProgressBar, TagRail } from "../components/bits";
import { Badge, Button, CodeBlock, EmptyState, ErrorNote, Field, Modal, Panel, Spinner, Status, Tabs, useToast } from "../components/ui";
import Playground from "./model/Playground";
import ConfigTab from "./model/ConfigTab";
import DeployTab from "./model/DeployTab";
import CompareTab from "./model/CompareTab";
import TrafficTab from "./model/TrafficTab";

export type Version = {
  id: string;
  version: number;
  status: string;
  preset: string | null;
  artifact_format: string;
  weights_bytes: number;
  weights_sha256: string | null;
  artifact_info: {
    kind?: string;
    usability?: string;
    family?: string | null;
    suggested_preset?: string | null;
    detail?: string;
    next_step?: string;
    export_snippet?: string | null;
    servable?: boolean;
  } | null;
  config: Record<string, unknown>;
  training_run_id: string | null;
  dataset_version: string | null;
  metrics: Record<string, number>;
  git_commit: string | null;
  changelog: string;
  source: string;
  validation: {
    status?: string;
    source?: string;
    warnings?: string[];
    errors?: string[];
    timings?: Record<string, number>;
    summary?: Record<string, unknown>;
    overlay_key?: string | null;
    stale?: boolean;
  };
  created_at: string;
  published_at: string | null;
  tags: string[];
  deployment: { id: string; status: string; replicas: number; replicas_ready: number; mode: string } | null;
};

export type Model = {
  id: string;
  slug: string;
  name: string;
  task: string;
  owner: string;
  framework: string;
  description: string;
  labels: string[];
  tags: { name: string; version: number | null; version_id: string; locked: boolean }[];
  version_count: number;
  latest_version: number | null;
  requests_24h: number;
  error_rate_24h: number;
  p95_ms_24h: number | null;
};

export default function ModelDetail() {
  const { slug = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "versions";
  const role = useRole();

  const model = useQuery<Model>({ queryKey: ["model", slug], queryFn: () => api(`/v1/models/${slug}`) });
  const versions = useQuery<Version[]>({ queryKey: ["versions", slug], queryFn: () => api(`/v1/models/${slug}/versions`), refetchInterval: 10_000 });

  if (model.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center text-ink-3">
        <Spinner size={18} />
      </div>
    );
  }
  if (model.error) return <ErrorNote error={model.error} />;
  const data = model.data!;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <nav className="flex items-center gap-1.5 text-[12px] text-ink-3">
            <Link to="/models" className="hover:text-ink-2">
              Models
            </Link>
            <span>/</span>
            <span className="mono text-ink-2">{data.slug}</span>
          </nav>
          <h1 className="mt-1 flex items-center gap-2.5 page-title">
            {data.name}
            <Badge>{data.task}</Badge>
          </h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-3">
            {data.owner ? <span>owner {data.owner}</span> : null}
            <span>{data.version_count} versions</span>
            <span>{fmt.int(data.requests_24h)} requests · 24 h</span>
            <span>p95 {fmt.ms(data.p95_ms_24h)}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <TagRail tags={data.tags.map((tag) => `${tag.name}→v${tag.version ?? "?"}`)} />
        </div>
      </div>

      <Tabs
        tabs={[
          { key: "versions", label: "Versions", count: data.version_count },
          { key: "playground", label: "Playground" },
          { key: "config", label: "Config" },
          { key: "deploy", label: "Deploy" },
          { key: "traffic", label: "Traffic" },
          { key: "compare", label: "Compare" },
        ]}
        active={tab}
        onChange={(key) => setParams({ tab: key })}
      />

      {tab === "versions" ? <VersionsTab model={data} versions={versions.data ?? []} loading={versions.isLoading} role={role} /> : null}
      {tab === "playground" ? <Playground model={data} versions={versions.data ?? []} /> : null}
      {tab === "config" ? <ConfigTab model={data} versions={versions.data ?? []} role={role} /> : null}
      {tab === "deploy" ? <DeployTab model={data} versions={versions.data ?? []} role={role} /> : null}
      {tab === "traffic" ? <TrafficTab model={data} versions={versions.data ?? []} /> : null}
      {tab === "compare" ? <CompareTab model={data} versions={versions.data ?? []} /> : null}
    </div>
  );
}

function VersionsTab({ model, versions, loading, role }: { model: Model; versions: Version[]; loading: boolean; role: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [uploading, setUploading] = useState(false);
  const [tagging, setTagging] = useState<Version | null>(null);
  const [converting, setConverting] = useState<Version | null>(null);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["versions", model.slug] });
    queryClient.invalidateQueries({ queryKey: ["model", model.slug] });
  };

  const publish = useMutation({
    mutationFn: (version: number) => api(`/v1/models/${model.slug}/versions/${version}/publish`, { method: "POST" }),
    onSuccess: () => {
      toast("Version published. It can now be deployed and tagged.");
      refresh();
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center text-ink-3">
        <Spinner size={18} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12.5px] text-ink-3">
          Published weights are immutable. A change means a new version — tags move, files never do.
        </p>
        {can(role, "developer") ? (
          <Button variant="primary" onClick={() => setUploading(true)}>
            New version
          </Button>
        ) : null}
      </div>

      {!versions.length ? (
        <Panel>
          <EmptyState
            title="No versions yet"
            body="Upload weights as version 1, then deploy it — loading it on a runtime is the check."
            action={can(role, "developer") ? <Button variant="primary" onClick={() => setUploading(true)}>Upload version 1</Button> : undefined}
          />
        </Panel>
      ) : (
        <div className="space-y-2.5">
          {versions.map((version) => {
            const validation = version.validation ?? {};
            // the artifact note carries the same sentences, with the evidence and
            // the fix; do not print them twice
            const fromArtifact = validation.source === "artifact-inspection";
            const isLive = Boolean(version.deployment && version.deployment.status !== "stopped");
            return (
              <Panel key={version.id} className="overflow-hidden">
                <div className="flex flex-wrap items-start gap-4 px-4 py-3">
                  <div className="flex items-center gap-3">
                    <span
                      className={`version-dot ${isLive ? "version-dot-live" : ""}`}
                      title={isLive ? "Serving traffic" : "Not deployed"}
                      aria-hidden
                    />
                    <div>
                      <p className="num text-[15px] font-semibold text-ink">v{version.version}</p>
                      <p className="text-[11px] text-ink-3">{fmt.ago(version.created_at)}</p>
                    </div>
                  </div>

                  <div className="min-w-[200px] flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Status value={version.status} />
                      {version.tags.length ? <TagRail tags={version.tags} /> : null}
                      {version.deployment ? (
                        <Badge kind={version.deployment.replicas_ready >= version.deployment.replicas ? "good" : "warn"}>
                          {version.deployment.replicas_ready}/{version.deployment.replicas} replicas
                        </Badge>
                      ) : null}
                      {version.source === "conversion" ? <Badge>converted</Badge> : null}
                      {validation.stale ? <Badge kind="warn">config changed since it loaded</Badge> : null}
                    </div>
                    {version.changelog ? <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-2">{version.changelog}</p> : null}
                    <div className="mt-2 grid gap-x-6 gap-y-1 text-[11.5px] text-ink-3 sm:grid-cols-2">
                      <span>
                        {version.artifact_format} · {fmt.bytes(version.weights_bytes)}
                        {version.weights_sha256 ? <span className="mono"> · {version.weights_sha256.slice(0, 10)}</span> : null}
                      </span>
                      {version.dataset_version ? <span>dataset {version.dataset_version}</span> : null}
                      {version.training_run_id ? <span>run {version.training_run_id}</span> : null}
                      {version.git_commit ? <span className="mono">commit {version.git_commit.slice(0, 8)}</span> : null}
                    </div>
                    {Object.keys(version.metrics ?? {}).length ? (
                      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                        {Object.entries(version.metrics).map(([key, value]) => (
                          <span key={key} className="text-[11.5px] text-ink-2">
                            {key} <span className="num text-ink">{typeof value === "number" ? value.toFixed(4) : String(value)}</span>
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {validation.warnings?.length && !fromArtifact ? (
                      <ul className="mt-2 space-y-1">
                        {validation.warnings.map((warning, index) => (
                          <li key={index} className="flex gap-1.5 text-[11.5px] leading-relaxed text-warn">
                            <span aria-hidden>▲</span>
                            <span>{warning}</span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    {validation.errors?.length && !fromArtifact ? (
                      <ul className="mt-2 space-y-1">
                        {validation.errors.map((problem, index) => (
                          <li key={index} className="flex gap-1.5 text-[11.5px] leading-relaxed text-bad">
                            <span aria-hidden>■</span>
                            <span>{problem}</span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                    {/* A version a runtime has loaded has answered the only
                        question this note asks; telling someone to convert what
                        is already serving is noise. */}
                    {version.artifact_info
                      && version.artifact_info.usability !== "servable"
                      && version.status !== "ready"
                      && version.status !== "deprecated" ? (
                      <ArtifactNote
                        model={model}
                        version={version}
                        onConvert={() => setConverting(version)}
                      />
                    ) : null}
                  </div>

                  <div className="flex flex-wrap items-center gap-1.5">
                    {can(role, "developer") ? (
                      <>
                        {version.status === "draft" || version.status === "failed" ? (
                          <Button size="sm" variant="primary" loading={publish.isPending && publish.variables === version.version} onClick={() => publish.mutate(version.version)}>
                            Publish
                          </Button>
                        ) : null}
                        {version.status === "ready" || version.status === "deprecated" ? (
                          <Button size="sm" onClick={() => setTagging(version)}>
                            Tag
                          </Button>
                        ) : null}
                        {version.artifact_info?.usability === "convertible" ? (
                          <Button size="sm" variant="primary" onClick={() => setConverting(version)}>
                            Convert
                          </Button>
                        ) : null}
                      </>
                    ) : null}
                    <Link to={`/models/${model.slug}?tab=playground&version=${version.version}`} className="btn btn-sm">
                      Try
                    </Link>
                  </div>
                </div>

              </Panel>
            );
          })}
        </div>
      )}

      <UploadWizard model={model} open={uploading} onClose={() => setUploading(false)} onDone={refresh} latest={model.latest_version} />
      <TagDialog model={model} version={tagging} onClose={() => setTagging(null)} onDone={refresh} />
      <ConvertDialog model={model} version={converting} onClose={() => setConverting(null)} onDone={refresh} />
    </div>
  );
}

/**
 * What was actually uploaded, and what to do about it. A `.pt` can be three very
 * different things, and "validation failed" is not an answer — this says which
 * one it is, names the architecture we recognised, and gives the export command.
 */
function ArtifactNote({
  model,
  version,
  onConvert,
}: {
  model: Model;
  version: Version;
  onConvert: () => void;
}) {
  const info = version.artifact_info!;
  const convertible = info.usability === "convertible";
  const applyPreset = useApplyPreset(model.slug, version.version);

  return (
    <div
      className={`mt-2.5 rounded-lg border px-3 py-2.5 ${
        convertible ? "border-warn/40 bg-warn/8" : "border-bad/40 bg-bad/8"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge kind={convertible ? "warn" : "bad"}>{info.kind ?? "unknown"}</Badge>
        {info.family ? <Badge>{info.family}</Badge> : null}
        <span className="text-[12.5px] text-ink">{info.detail}</span>
      </div>
      {info.next_step ? (
        <p className="mt-1.5 text-[12px] leading-relaxed text-ink-2">{info.next_step}</p>
      ) : null}

      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        {convertible ? (
          <Button size="sm" variant="primary" onClick={onConvert}>
            Convert to ONNX
          </Button>
        ) : null}
        {info.suggested_preset && version.status !== "ready" ? (
          <Button
            size="sm"
            loading={applyPreset.isPending}
            onClick={() => applyPreset.mutate(info.suggested_preset!)}
          >
            Use the {info.suggested_preset} preset
          </Button>
        ) : null}
      </div>

      {info.export_snippet ? (
        <div className="mt-2.5">
          <CodeBlock code={info.export_snippet} language="export it yourself" />
        </div>
      ) : null}
    </div>
  );
}

/** Swap a draft's config for the preset the artifact actually looks like. */
function useApplyPreset(slug: string, version: number) {
  const queryClient = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: async (preset: string) => {
      const config = await api(`/v1/meta/presets/${preset}`);
      return api(`/v1/models/${slug}/versions/${version}`, { method: "PATCH", body: { config } });
    },
    onSuccess: (_, preset) => {
      toast(`Config replaced with the ${preset} preset. Check the class list before deploying.`);
      queryClient.invalidateQueries({ queryKey: ["versions", slug] });
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });
}

function ConvertDialog({
  model,
  version,
  onClose,
  onDone,
}: {
  model: Model;
  version: Version | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [target, setTarget] = useState("onnx");
  const [opset, setOpset] = useState(17);

  const convert = useMutation({
    mutationFn: () =>
      api(`/v1/models/${model.slug}/versions/${version?.version}/convert`, {
        body: { target, opset, changelog: `${target} conversion of v${version?.version}` },
      }),
    onSuccess: () => {
      toast("Conversion queued. It lands as a new draft version — watch it on the Jobs page.");
      onDone();
      onClose();
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  return (
    <Modal
      open={Boolean(version)}
      onClose={onClose}
      title={`Convert v${version?.version}`}
      subtitle="The result arrives as a new draft version, so this one is left untouched."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={convert.isPending} onClick={() => convert.mutate()}>
            Start conversion
          </Button>
        </>
      }
    >
      <div className="space-y-3.5">
        <Field label="Target" hint="ONNX is the portable one; a TensorRT engine only runs on the GPU that built it.">
          <select className="select" value={target} onChange={(event) => setTarget(event.target.value)}>
            <option value="onnx">ONNX</option>
            <option value="onnx_fp16">ONNX · FP16 weights</option>
            <option value="onnx_int8">ONNX · INT8 (dynamic quantisation)</option>
            <option value="tensorrt_fp16">TensorRT engine · FP16</option>
          </select>
        </Field>
        <Field label="Opset" hint="17 suits current runtimes. Lower it only if your exporter needs to.">
          <input className="input num" type="number" min={11} max={21} value={opset} onChange={(event) => setOpset(Number(event.target.value))} />
        </Field>
        <p className="text-[12px] leading-relaxed text-ink-3">
          Conversion runs in the worker, not on a serving runtime, so it cannot affect inference
          latency. It needs the converter profile running:{" "}
          <span className="mono">docker compose --profile convert up -d</span>
        </p>
        <ErrorNote error={convert.error} />
      </div>
    </Modal>
  );
}

function UploadWizard({ model, open, onClose, onDone, latest }: { model: Model; open: boolean; onClose: () => void; onDone: () => void; latest: number | null }) {
  const instance = useInstance();
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [preset, setPreset] = useState<string>("");
  const [inherit, setInherit] = useState(Boolean(latest));
  const [meta, setMeta] = useState({ training_run_id: "", dataset_version: "", git_commit: "", changelog: "", metrics: "" });
  const [progress, setProgress] = useState<{ sent: number; total: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const presets = useQuery<{ name: string; label: string; family: string; task: string; description: string }[]>({
    queryKey: ["presets"],
    queryFn: () => api("/v1/meta/presets"),
    enabled: open,
    staleTime: 600_000,
  });

  const relevant = (presets.data ?? []).filter((item) => item.task === model.task);

  async function run() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      let metrics: Record<string, number> = {};
      if (meta.metrics.trim()) {
        try {
          metrics = JSON.parse(meta.metrics);
        } catch {
          throw new Error('Metrics must be JSON, for example {"mAP50": 0.71, "f1": 0.68}');
        }
      }
      const version = await api<Version>(`/v1/models/${model.slug}/versions`, {
        body: {
          preset: inherit ? null : preset || null,
          inherit_from_version: inherit ? latest : null,
          artifact_format: guessFormat(file.name),
          training_run_id: meta.training_run_id || null,
          dataset_version: meta.dataset_version || null,
          git_commit: meta.git_commit || null,
          changelog: meta.changelog,
          metrics,
        },
      });

      const digest = file.size < 512 * 1024 * 1024 ? await sha256(file) : undefined;
      await uploadWeights({
        model: model.slug,
        version: version.version,
        file,
        sha256: digest,
        chunkBytes: (instance.data?.limits.upload_chunk_mb ?? 32) * 1024 * 1024,
        onProgress: (sent, total) => setProgress({ sent, total }),
      });

      toast(`Version ${version.version} uploaded. Deploy it, or hit Try to run it.`);
      onDone();
      onClose();
      setFile(null);
      setProgress(null);
      setMeta({ training_run_id: "", dataset_version: "", git_commit: "", changelog: "", metrics: "" });
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      wide
      title={`New version${latest ? ` (v${latest + 1})` : " (v1)"}`}
      subtitle="Weights land in object storage and are checksummed end to end. A runtime loading them is what proves they work — no reference dataset needed."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" loading={busy} disabled={!file || (!inherit && !preset)} onClick={run}>
            Upload
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {file ? (
          <div className="panel flex items-center justify-between gap-3 bg-raised px-3 py-2.5">
            <div className="min-w-0">
              <p className="mono truncate text-[12.5px] text-ink">{file.name}</p>
              <p className="text-[11.5px] text-ink-3">
                {fmt.bytes(file.size)} · {guessFormat(file.name)}
              </p>
            </div>
            <Button size="sm" variant="ghost" onClick={() => setFile(null)} disabled={busy}>
              Replace
            </Button>
          </div>
        ) : (
          <Dropzone
            label="Drop the weights file here"
            hint={`.onnx, .pt, .torchscript, .safetensors, .engine — up to ${((instance.data?.limits.max_upload_mb ?? 8192) / 1024).toFixed(0)} GB, uploaded in resumable chunks`}
            accept=".onnx,.pt,.pth,.torchscript,.ts,.safetensors,.engine,.plan,.trt"
            onFiles={(files) => setFile(files[0])}
          />
        )}

        {progress ? <ProgressBar value={progress.sent} total={progress.total} label={`${fmt.bytes(progress.sent)} of ${fmt.bytes(progress.total)} uploaded`} /> : null}

        <div>
          <p className="eyebrow mb-2">Pre/post-processing config</p>
          {latest ? (
            <label className="mb-2 flex cursor-pointer items-start gap-2 text-[12.5px] text-ink-2">
              <input type="checkbox" className="mt-0.5" checked={inherit} onChange={(event) => setInherit(event.target.checked)} />
              <span>
                Inherit v{latest}&rsquo;s config
                <span className="block text-[11.5px] text-ink-3">The usual case for a retrained model: same architecture, new weights. You can edit it before publishing.</span>
              </span>
            </label>
          ) : null}

          {!inherit ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {relevant.map((item) => (
                <button
                  key={item.name}
                  onClick={() => setPreset(item.name)}
                  className={`rounded-lg border px-3 py-2.5 text-left transition-colors ${preset === item.name ? "border-accent bg-accent/8" : "border-line hover:border-line-2 hover:bg-raised"}`}
                >
                  <p className="text-[12.5px] text-ink">{item.label}</p>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-ink-3">{item.description}</p>
                </button>
              ))}
              {relevant.length === 0 ? <p className="text-[12px] text-ink-3">No preset matches this task — pick “Custom” and edit the config after upload.</p> : null}
            </div>
          ) : null}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Training run ID" hint="Whatever your training stack calls this run.">
            <input className="input" value={meta.training_run_id} onChange={(event) => setMeta({ ...meta, training_run_id: event.target.value })} />
          </Field>
          <Field label="Dataset version">
            <input className="input" value={meta.dataset_version} onChange={(event) => setMeta({ ...meta, dataset_version: event.target.value })} placeholder="birds-2026-03" />
          </Field>
          <Field label="Git commit">
            <input className="input mono" value={meta.git_commit} onChange={(event) => setMeta({ ...meta, git_commit: event.target.value })} />
          </Field>
          <Field label="Metrics (JSON)" hint='{"mAP50": 0.71, "f1": 0.68}'>
            <input className="input mono" value={meta.metrics} onChange={(event) => setMeta({ ...meta, metrics: event.target.value })} />
          </Field>
        </div>

        <Field label="Changelog" hint="What changed against the previous version, in one line.">
          <textarea className="textarea" rows={2} value={meta.changelog} onChange={(event) => setMeta({ ...meta, changelog: event.target.value })} />
        </Field>

        <ErrorNote error={error} />
      </div>
    </Modal>
  );
}

function TagDialog({ model, version, onClose, onDone }: { model: Model; version: Version | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [name, setName] = useState("production");

  const move = useMutation({
    mutationFn: () => api(`/v1/models/${model.slug}/tags`, { method: "PUT", body: { name, version: version?.version } }),
    onSuccess: () => {
      toast(`Tag '${name}' now points at v${version?.version}. Callers using the tag pick it up on the next request.`);
      onDone();
      onClose();
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const existing = model.tags.find((tag) => tag.name === name);

  return (
    <Modal
      open={Boolean(version)}
      onClose={onClose}
      title={`Point a tag at v${version?.version}`}
      subtitle="Clients call the tag, so this takes effect on the next request — no redeploy."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={move.isPending} onClick={() => move.mutate()}>
            Move tag
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Tag" hint="production, staging, champion — or any name your products already use.">
          <input className="input mono" value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        {existing ? (
          <p className="rounded-lg border border-warn/40 bg-warn/8 px-3 py-2 text-[12px] text-ink">
            &lsquo;{name}&rsquo; currently points at v{existing.version}. Moving it switches live traffic immediately; rolling back is the same action in reverse.
          </p>
        ) : null}
        {version && !version.deployment ? (
          <p className="rounded-lg border border-warn/40 bg-warn/8 px-3 py-2 text-[12px] text-ink">
            v{version.version} has no replicas yet. Tagging it now means callers get <span className="mono">model_not_deployed</span> until you deploy it — deploy first, then move the tag.
          </p>
        ) : null}
        <ErrorNote error={move.error} />
      </div>
    </Modal>
  );
}

function guessFormat(filename: string) {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".onnx")) return "onnx";
  if (lower.endsWith(".engine") || lower.endsWith(".plan") || lower.endsWith(".trt")) return "tensorrt";
  if (lower.endsWith(".safetensors")) return "safetensors";
  if (lower.endsWith(".torchscript") || lower.endsWith(".ts")) return "torchscript";
  if (lower.endsWith(".pt") || lower.endsWith(".pth")) return "pt";
  return "other";
}
