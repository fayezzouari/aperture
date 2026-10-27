import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { can } from "../../lib/hooks";
import { Button, ErrorNote, Field, Panel, PanelHeader, useToast } from "../../components/ui";
import type { Model, Version } from "../ModelDetail";

type Config = {
  task: string;
  backend: string;
  input: { width: number; height: number; channels: number; layout: string; dtype: string; color: string };
  preprocess: {
    resize: { mode: string; width: number; height: number; pad_value: number; interpolation: string; resize_side: number | null };
    normalize: { scale: number; mean: number[]; std: number[] };
    grayscale: boolean;
    auto_orient: boolean;
  };
  postprocess: {
    decoder: string;
    score_threshold: number;
    iou_threshold: number;
    top_k: number;
    max_detections: number;
    nms: boolean;
    class_agnostic_nms: boolean;
    normalized_boxes: boolean;
    box_format: string;
    sigmoid_logits: boolean;
    mask_format: string;
    mask_threshold: number;
    normalize_embedding: boolean;
    output_index: Record<string, number>;
    charset?: string[];
  };
  classes: string[];
  hook: { enabled: boolean; preprocess_source: string | null; postprocess_source: string | null; timeout_ms: number; memory_mb: number };
  warmup: { iterations: number; batch_sizes: number[] };
  notes: string;
  extra: Record<string, unknown>;
};

const RESIZE_MODES = ["letterbox", "stretch", "shortest_side", "center_crop", "none"];
// A pasted JSON list (imagenet_classes.json) or an Ultralytics `names` dict is
// taken as the list it is; anything else is one class per line.
function parseClasses(text: string): string[] {
  const trimmed = text.trim();
  if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
    try {
      const parsed = JSON.parse(trimmed);
      const values = Array.isArray(parsed)
        ? parsed
        : Object.entries(parsed).sort(([a], [b]) => Number(a) - Number(b)).map(([, name]) => name);
      if (values.every((value) => typeof value === "string")) return values.map((value) => value.trim()).filter(Boolean);
    } catch {
      // not JSON after all; fall through to lines
    }
  }
  return text.split("\n").map((line) => line.trim()).filter(Boolean);
}

// A charset is not a class list: a space is a real character (PaddleOCR's
// dictionaries end with one), so nothing here is trimmed. Only empty lines go,
// and a pasted JSON list is taken verbatim.
function parseCharset(text: string): string[] {
  const trimmed = text.trim();
  if (trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed) && parsed.every((value) => typeof value === "string")) return parsed;
    } catch {
      // not JSON after all; fall through to lines
    }
  }
  return text.split("\n").map((line) => line.replace(/\r$/, "")).filter((line) => line !== "");
}

const DECODERS = ["yolo_v5", "yolo_v8", "yolo_seg", "yolo_pose", "detr", "classifier_softmax", "classifier_sigmoid", "classifier_probs", "embedding", "ctc_greedy", "binary_mask", "passthrough"];

/**
 * R6.3 - the config editor with a live preview. Editing is only allowed while a
 * version is a draft; a published version's config is frozen, which is what
 * makes "the version number tells you what ran" true.
 */
export default function ConfigTab({ model, versions, role }: { model: Model; versions: Version[]; role: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  // null until picked: the tab can mount before the versions arrive, and a
  // number frozen then would send every save to a version that does not exist
  const [picked, setPicked] = useState<number | null>(null);
  const version = versions.find((item) => item.version === picked) ?? versions[0];
  const selected = version?.version;
  const editable = Boolean(version) && ["draft", "failed", "validating"].includes(version.status) && can(role, "developer");

  const [config, setConfig] = useState<Config | null>(null);
  useEffect(() => {
    if (version) setConfig(JSON.parse(JSON.stringify(version.config)) as Config);
  }, [version?.id]);

  const dirty = useMemo(() => (version && config ? JSON.stringify(config) !== JSON.stringify(version.config) : false), [config, version]);

  const save = useMutation({
    mutationFn: () => api(`/v1/models/${model.slug}/versions/${selected}`, { method: "PATCH", body: { config } }),
    onSuccess: () => {
      toast("Config saved. Redeploy the version to load it, or hit Try to run it.");
      queryClient.invalidateQueries({ queryKey: ["versions", model.slug] });
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  if (!version || !config) {
    return (
      <Panel>
        <div className="px-4 py-10 text-center text-[12.5px] text-ink-3">Upload a version first — the config lives on the version, not on the model.</div>
      </Panel>
    );
  }

  const set = (path: string, value: unknown) => {
    setConfig((current) => {
      if (!current) return current;
      const next = JSON.parse(JSON.stringify(current));
      const keys = path.split(".");
      let node: Record<string, unknown> = next;
      keys.slice(0, -1).forEach((key) => {
        node = node[key] as Record<string, unknown>;
      });
      node[keys[keys.length - 1]] = value;
      return next;
    });
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,400px)]">
      <div className="space-y-4">
        <Panel>
          <PanelHeader
            title="Pre/post-processing"
            subtitle={
              editable
                ? "Every field here travels with this version. Change one, preview it, then publish."
                : `v${version.version} is ${version.status}, so its config is frozen. Create a new version to change preprocessing.`
            }
            action={
              <select className="select w-auto" value={selected} onChange={(event) => setPicked(Number(event.target.value))}>
                {versions.map((item) => (
                  <option key={item.id} value={item.version}>
                    v{item.version} · {item.status}
                  </option>
                ))}
              </select>
            }
          />

          <fieldset disabled={!editable} className="space-y-5 px-4 py-4 disabled:opacity-70">
            <section>
              <p className="eyebrow mb-2.5">Input</p>
              <div className="grid gap-3 sm:grid-cols-4">
                <Field label="Width">
                  <input className="input num" type="number" value={config.preprocess.resize.width} onChange={(event) => { set("preprocess.resize.width", Number(event.target.value)); set("input.width", Number(event.target.value)); }} />
                </Field>
                <Field label="Height">
                  <input className="input num" type="number" value={config.preprocess.resize.height} onChange={(event) => { set("preprocess.resize.height", Number(event.target.value)); set("input.height", Number(event.target.value)); }} />
                </Field>
                <Field label="Layout">
                  <select className="select" value={config.input.layout} onChange={(event) => set("input.layout", event.target.value)}>
                    <option value="NCHW">NCHW</option>
                    <option value="NHWC">NHWC</option>
                  </select>
                </Field>
                <Field label="Colour">
                  <select className="select" value={config.input.color} onChange={(event) => set("input.color", event.target.value)}>
                    <option value="RGB">RGB</option>
                    <option value="BGR">BGR</option>
                    <option value="GRAY">GRAY</option>
                  </select>
                </Field>
              </div>
            </section>

            <section>
              <p className="eyebrow mb-2.5">Resize</p>
              <div className="grid gap-3 sm:grid-cols-4">
                <Field label="Mode" hint={config.preprocess.resize.mode === "letterbox" ? "Aspect kept, padded — the YOLO default." : undefined}>
                  <select className="select" value={config.preprocess.resize.mode} onChange={(event) => set("preprocess.resize.mode", event.target.value)}>
                    {RESIZE_MODES.map((mode) => (
                      <option key={mode} value={mode}>
                        {mode}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Pad value">
                  <input className="input num" type="number" value={config.preprocess.resize.pad_value} onChange={(event) => set("preprocess.resize.pad_value", Number(event.target.value))} />
                </Field>
                <Field label="Interpolation">
                  <select className="select" value={config.preprocess.resize.interpolation} onChange={(event) => set("preprocess.resize.interpolation", event.target.value)}>
                    {["bilinear", "bicubic", "nearest", "area"].map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Resize side" hint="center_crop / shortest_side only">
                  <input className="input num" type="number" value={config.preprocess.resize.resize_side ?? ""} onChange={(event) => set("preprocess.resize.resize_side", event.target.value ? Number(event.target.value) : null)} />
                </Field>
              </div>
            </section>

            <section>
              <p className="eyebrow mb-2.5">Normalise</p>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Scale" hint="1/255 for 0–1 inputs">
                  <input className="input num" type="number" step="0.0001" value={config.preprocess.normalize.scale} onChange={(event) => set("preprocess.normalize.scale", Number(event.target.value))} />
                </Field>
                <Field label="Mean" hint="comma separated per channel">
                  <input className="input mono" value={config.preprocess.normalize.mean.join(", ")} onChange={(event) => set("preprocess.normalize.mean", parseNumbers(event.target.value))} />
                </Field>
                <Field label="Std">
                  <input className="input mono" value={config.preprocess.normalize.std.join(", ")} onChange={(event) => set("preprocess.normalize.std", parseNumbers(event.target.value))} />
                </Field>
              </div>
              <label className="mt-2 flex cursor-pointer items-center gap-2 text-[12.5px] text-ink-2">
                <input type="checkbox" checked={config.preprocess.auto_orient} onChange={(event) => set("preprocess.auto_orient", event.target.checked)} />
                Honour EXIF orientation
              </label>
            </section>

            <section>
              <p className="eyebrow mb-2.5">Decode</p>
              <div className="grid gap-3 sm:grid-cols-4">
                <Field label="Decoder">
                  <select className="select" value={config.postprocess.decoder} onChange={(event) => set("postprocess.decoder", event.target.value)}>
                    {DECODERS.map((decoder) => (
                      <option key={decoder} value={decoder}>
                        {decoder}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Box format">
                  <select className="select" value={config.postprocess.box_format} onChange={(event) => set("postprocess.box_format", event.target.value)}>
                    {["cxcywh", "xyxy", "xywh"].map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Score threshold">
                  <input className="input num" type="number" step="0.01" value={config.postprocess.score_threshold} onChange={(event) => set("postprocess.score_threshold", Number(event.target.value))} />
                </Field>
                <Field label="IoU threshold">
                  <input className="input num" type="number" step="0.01" value={config.postprocess.iou_threshold} onChange={(event) => set("postprocess.iou_threshold", Number(event.target.value))} />
                </Field>
              </div>
              <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2">
                {[
                  ["postprocess.nms", "Apply NMS", config.postprocess.nms],
                  ["postprocess.class_agnostic_nms", "Class-agnostic NMS", config.postprocess.class_agnostic_nms],
                  ["postprocess.normalized_boxes", "Boxes are normalised 0–1", config.postprocess.normalized_boxes],
                  ["postprocess.sigmoid_logits", "Apply sigmoid to logits", config.postprocess.sigmoid_logits],
                ].map(([path, label, value]) => (
                  <label key={String(path)} className="flex cursor-pointer items-center gap-2 text-[12.5px] text-ink-2">
                    <input type="checkbox" checked={Boolean(value)} onChange={(event) => set(String(path), event.target.checked)} />
                    {String(label)}
                  </label>
                ))}
              </div>
            </section>

            {config.postprocess.decoder === "ctc_greedy" ? (
              <section>
                <p className="eyebrow mb-2.5">Charset ({config.postprocess.charset?.length ?? 0})</p>
                <textarea
                  className="textarea mono"
                  rows={5}
                  value={(config.postprocess.charset ?? []).join("\n")}
                  onChange={(event) => set("postprocess.charset", parseCharset(event.target.value))}
                />
                <p className="hint">The model's dictionary, one character per line or a JSON list, in order. Spaces are kept: PaddleOCR dictionaries end with a space, so the model scores charset + 1 classes (the CTC blank).</p>
              </section>
            ) : (
              <section>
                <p className="eyebrow mb-2.5">Classes ({config.classes.length})</p>
                <textarea
                  className="textarea"
                  rows={5}
                  value={config.classes.join("\n")}
                  onChange={(event) => set("classes", parseClasses(event.target.value))}
                />
                <p className="hint">One per line, or paste a JSON list, in the order the model emits them. A mismatch here is the single most common cause of confidently wrong labels.</p>
              </section>
            )}

            <section>
              <p className="eyebrow mb-2.5">Warm-up</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Iterations" hint="Runs on deploy so the first real request is not the slow one.">
                  <input className="input num" type="number" value={config.warmup.iterations} onChange={(event) => set("warmup.iterations", Number(event.target.value))} />
                </Field>
                <Field label="Batch sizes">
                  <input className="input mono" value={config.warmup.batch_sizes.join(", ")} onChange={(event) => set("warmup.batch_sizes", parseNumbers(event.target.value).map((value) => Math.max(1, Math.round(value))))} />
                </Field>
              </div>
            </section>
          </fieldset>

          {editable ? (
            <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-3">
              <p className="text-[11.5px] text-ink-3">{dirty ? "Unsaved changes" : "Saved"}</p>
              <div className="flex gap-2">
                <Button onClick={() => setConfig(JSON.parse(JSON.stringify(version.config)) as Config)} disabled={!dirty}>
                  Revert
                </Button>
                <Button variant="primary" loading={save.isPending} disabled={!dirty} onClick={() => save.mutate()}>
                  Save config
                </Button>
              </div>
            </div>
          ) : null}
          <ErrorNote error={save.error} className="mx-4 mb-4" />
        </Panel>
      </div>

      <div className="space-y-4">
        <Panel>
          <PanelHeader
            title="Check it on your own image"
            subtitle="A config is right or wrong on your data, not on a stand-in."
          />
          <div className="space-y-3 px-4 py-4">
            <p className="text-[12px] leading-relaxed text-ink-3">
              The playground runs this exact config through a real runtime — the same
              preprocess, forward pass and decoder the serving path uses — on an image you
              choose. It starts a replica if this version has none, so there is nothing to
              set up first.
            </p>
            <Link to={`/models/${model.slug}?tab=playground&version=${selected}`} className="btn w-full justify-center">
              Open the playground
            </Link>
          </div>
        </Panel>

        <Panel>
          <PanelHeader title="Custom hook" subtitle="For heads the built-in decoders cannot express." />
          <div className="space-y-3 px-4 py-4">
            <label className="flex cursor-pointer items-center gap-2 text-[12.5px] text-ink-2">
              <input type="checkbox" disabled={!editable} checked={config.hook.enabled} onChange={(event) => set("hook.enabled", event.target.checked)} />
              Enable a Python hook
            </label>
            {config.hook.enabled ? (
              <>
                <Field label="postprocess(outputs, config, geometry) → dict">
                  <textarea
                    className="textarea"
                    rows={8}
                    disabled={!editable}
                    value={config.hook.postprocess_source ?? ""}
                    onChange={(event) => set("hook.postprocess_source", event.target.value)}
                    placeholder={"def postprocess(outputs, config, geometry):\n    import numpy as np\n    return {\"results\": [...]}"}
                  />
                </Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Timeout (ms)">
                    <input className="input num" type="number" disabled={!editable} value={config.hook.timeout_ms} onChange={(event) => set("hook.timeout_ms", Number(event.target.value))} />
                  </Field>
                  <Field label="Memory (MB)">
                    <input className="input num" type="number" disabled={!editable} value={config.hook.memory_mb} onChange={(event) => set("hook.memory_mb", Number(event.target.value))} />
                  </Field>
                </div>
                <p className="hint">
                  Hooks run in a separate process with no filesystem or network access and a hard CPU/memory cap. Imports are limited to numpy and the standard library maths modules.
                </p>
              </>
            ) : null}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function parseNumbers(value: string) {
  return value
    .split(/[,\s]+/)
    .map((part) => Number(part))
    .filter((part) => Number.isFinite(part));
}
