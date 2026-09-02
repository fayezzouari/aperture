import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../lib/api";
import { LatencyBar } from "../../components/charts";
import { Dropzone, JsonView } from "../../components/bits";
import { ResultViewer, type ResultPayload } from "../../components/results";
import { Button, CodeBlock, ErrorNote, Field, Panel, PanelHeader, Tabs } from "../../components/ui";
import type { Model, Version } from "../ModelDetail";

type PredictResponse = {
  request_id: string;
  model_version: number;
  tag: string | null;
  batch_size: number;
  results: ResultPayload[];
  latency: Record<string, number>;
  runtime_id: string | null;
  overrides_applied: Record<string, number>;
};

/** R6.2 - upload an image, pick a version or tag, see the overlay, raw JSON and latency. */
export default function Playground({ model, versions }: { model: Model; versions: Version[] }) {
  const [params] = useSearchParams();
  const [files, setFiles] = useState<File[]>([]);
  const [ref, setRef] = useState(params.get("version") ? `v${params.get("version")}` : model.tags[0]?.name ?? (versions[0] ? `v${versions[0].version}` : ""));
  const [thresholds, setThresholds] = useState({ score_threshold: "", iou_threshold: "", top_k: "" });
  const [inputsJson, setInputsJson] = useState("");
  const [response, setResponse] = useState<PredictResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState("overlay");
  const objectUrls = useRef<string[]>([]);

  const previews = useMemo(() => {
    objectUrls.current.forEach((url) => URL.revokeObjectURL(url));
    objectUrls.current = files.map((file) => URL.createObjectURL(file));
    return objectUrls.current;
  }, [files]);

  useEffect(() => () => objectUrls.current.forEach((url) => URL.revokeObjectURL(url)), []);

  const options = [
    ...model.tags.map((tag) => ({ value: tag.name, label: `${tag.name} → v${tag.version ?? "?"}` })),
    ...versions.map((version) => ({ value: `v${version.version}`, label: `v${version.version} · ${version.status}` })),
  ];

  /**
   * A multi-input model (a geometry vector, a prompt, a text embedding) cannot
   * run on an image alone, so the playground has to offer those inputs too.
   * The version's config declares their names; a pinned validation value makes a
   * sensible starting point.
   */
  const resolved = ref.startsWith("v")
    ? versions.find((version) => `v${version.version}` === ref)
    : versions.find((version) => version.id === model.tags.find((tag) => tag.name === ref)?.version_id);
  const config = (resolved?.config ?? {}) as {
    input?: { input_names?: string[] };
    extra?: { validation_inputs?: Record<string, unknown>; warmup_inputs?: Record<string, unknown> };
  };
  const extraInputNames = (config.input?.input_names ?? []).slice(1);
  const suggested = config.extra?.warmup_inputs ?? config.extra?.validation_inputs;

  useEffect(() => {
    if (!extraInputNames.length) {
      setInputsJson("");
      return;
    }
    setInputsJson(JSON.stringify(suggested ?? Object.fromEntries(extraInputNames.map((name) => [name, []])), null, 0));
  }, [resolved?.id, extraInputNames.length]);

  async function run() {
    if (!files.length) return;
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      files.forEach((file) => form.append("images", file));
      form.append("include_overlay", "false");
      if (inputsJson.trim()) {
        try {
          const parsed = JSON.parse(inputsJson);
          form.append("inputs", JSON.stringify(parsed));
        } catch {
          throw new Error("Model inputs must be valid JSON, for example {\"aux\": [0.5, 0.5, 0.5, 0.5]}");
        }
      }
      const overrides: Record<string, number> = {};
      if (thresholds.score_threshold) overrides.score_threshold = Number(thresholds.score_threshold);
      if (thresholds.iou_threshold) overrides.iou_threshold = Number(thresholds.iou_threshold);
      if (thresholds.top_k) overrides.top_k = Number(thresholds.top_k);
      if (Object.keys(overrides).length) form.append("overrides", JSON.stringify(overrides));

      // ondemand: the playground is for looking at a version, so it starts a
      // replica if none is running rather than sending you to the Deploy tab.
      // The first run therefore pays for the model load.
      const result = await api<PredictResponse>(
        `/v1/models/${model.slug}/${ref}/predict?ondemand=1`, { form });
      setResponse(result);
    } catch (caught) {
      setError(caught);
      setResponse(null);
    } finally {
      setBusy(false);
    }
  }

  const curl = [
    `curl -X POST "${window.location.origin}/v1/models/${model.slug}/${ref || "production"}/predict"`,
    `  -H "x-api-key: $APERTURE_KEY"`,
    `  -F "images=@image.jpg"`,
    ...(inputsJson.trim() && extraInputNames.length ? [`  -F 'inputs=${inputsJson}'`] : []),
    ...(Object.values(thresholds).some(Boolean)
      ? [`  -F 'overrides={"score_threshold":${thresholds.score_threshold || 0.25}}'`]
      : []),
  ].join(" \\\n");

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,340px)_1fr]">
      <div className="space-y-4">
        <Panel>
          <PanelHeader
            title="Input"
            subtitle="Runs against a deployed replica, exactly as a product would — and starts one if this version has none."
          />
          <div className="space-y-3.5 px-4 py-4">
            <Field label="Version or tag" hint="A tag resolves at request time, so it shows what production is really serving.">
              <select className="select" value={ref} onChange={(event) => setRef(event.target.value)}>
                {options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>

            {files.length ? (
              <div className="space-y-2">
                <div className="grid grid-cols-3 gap-2">
                  {previews.map((url, index) => (
                    <img key={url} src={url} alt={`Input ${index + 1}`} className="h-16 w-full rounded-md border border-line object-cover" />
                  ))}
                </div>
                <Button size="sm" variant="ghost" onClick={() => setFiles([])}>
                  Clear {files.length} image{files.length > 1 ? "s" : ""}
                </Button>
              </div>
            ) : (
              <Dropzone label="Drop images" hint="One or several — a batch goes in a single request" accept="image/*" multiple onFiles={(dropped) => setFiles(dropped)} />
            )}

            {extraInputNames.length ? (
              <Field
                label={`Model inputs · ${extraInputNames.join(", ")}`}
                hint="This model takes more than an image. One row per image, or a single row used for all of them."
              >
                <textarea
                  className="textarea"
                  rows={2}
                  value={inputsJson}
                  onChange={(event) => setInputsJson(event.target.value)}
                  spellCheck={false}
                />
              </Field>
            ) : null}

            <div className="grid grid-cols-3 gap-2">
              <Field label="Score">
                <input className="input" inputMode="decimal" placeholder="0.25" value={thresholds.score_threshold} onChange={(event) => setThresholds({ ...thresholds, score_threshold: event.target.value })} />
              </Field>
              <Field label="IoU">
                <input className="input" inputMode="decimal" placeholder="0.45" value={thresholds.iou_threshold} onChange={(event) => setThresholds({ ...thresholds, iou_threshold: event.target.value })} />
              </Field>
              <Field label="Top-k">
                <input className="input" inputMode="numeric" placeholder="300" value={thresholds.top_k} onChange={(event) => setThresholds({ ...thresholds, top_k: event.target.value })} />
              </Field>
            </div>
            <p className="hint">Overrides apply to this request only — the deployed config is untouched.</p>

            <Button variant="primary" className="w-full" loading={busy} disabled={!files.length || !ref} onClick={run}>
              Run inference
            </Button>
            {busy ? (
              <p className="hint">
                If this version has no replica running, one is starting now — the first run waits
                for the weights to load.
              </p>
            ) : null}
            <ErrorNote error={error} />
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Call it from code" />
          <div className="px-4 py-3">
            <CodeBlock code={curl} language="curl" />
          </div>
        </Panel>
      </div>

      <Panel className="min-h-[420px]">
        <PanelHeader
          title="Result"
          subtitle={response ? `v${response.model_version}${response.tag ? ` via '${response.tag}'` : ""} · ${response.batch_size} image${response.batch_size > 1 ? "s" : ""} · request ${response.request_id.slice(0, 10)}` : "Nothing run yet."}
          action={response ? <Tabs tabs={[{ key: "overlay", label: "Overlay" }, { key: "json", label: "JSON" }]} active={view} onChange={setView} /> : undefined}
        />
        {!response ? (
          <div className="flex h-[360px] items-center justify-center px-6 text-center text-[12.5px] text-ink-3">
            Pick a version, drop an image, and run it. The overlay is rendered from the same decoder the serving path uses.
          </div>
        ) : (
          <div className="space-y-4 px-4 py-4">
            <div>
              <p className="eyebrow mb-2">Latency</p>
              <LatencyBar latency={response.latency} height={8} showLabels />
              {response.runtime_id ? <p className="mono mt-2 text-[11px] text-ink-3">served by {response.runtime_id}</p> : null}
            </div>

            {view === "overlay" ? (
              <div className="grid gap-3">
                {response.results.map((result, index) => (
                  <ResultViewer key={index} imageUrl={previews[index] ?? previews[0]} result={result} />
                ))}
              </div>
            ) : (
              <JsonView value={response} />
            )}

            {response.results[0]?.embedding_dim ? (
              <p className="text-[12px] text-ink-3">
                Embedding of {response.results[0].embedding_dim} dimensions returned. First values:{" "}
                <span className="mono text-ink-2">{response.results[0].embedding?.slice(0, 6).map((v) => v.toFixed(4)).join(", ")}…</span>
              </p>
            ) : null}

            {Object.keys(response.overrides_applied ?? {}).length ? (
              <p className="text-[11.5px] text-ink-3">
                Overrides applied: <span className="mono">{JSON.stringify(response.overrides_applied)}</span>
              </p>
            ) : null}
          </div>
        )}
      </Panel>
    </div>
  );
}
