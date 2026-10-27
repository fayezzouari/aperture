import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { LatencyBar } from "../components/charts";
import { JsonView } from "../components/bits";
import { CaptureImage, ResultViewer, type ResultPayload } from "../components/results";
import { Badge, Button, EmptyState, ErrorNote, Field, KeyValue, Modal, Panel, Spinner, Status, useToast } from "../components/ui";
import { Segmented, TIME_WINDOWS, Toolbar } from "../components/toolbar";

type Row = {
  id: string;
  request_id: string;
  trace_id: string | null;
  model_id: string | null;
  model_slug: string;
  version_number: number | null;
  tag: string | null;
  route: string;
  caller_type: string;
  caller_label: string;
  status: string;
  error_code: string | null;
  error_message: string | null;
  batch_size: number;
  queue_ms: number;
  preprocess_ms: number;
  inference_ms: number;
  postprocess_ms: number;
  gateway_ms: number;
  total_ms: number;
  runtime_id: string | null;
  detection_count: number;
  confidence: { mean?: number | null; count?: number; classes?: Record<string, number> };
  input_stats: Record<string, unknown>;
  input_ref: Record<string, unknown>;
  overrides: Record<string, unknown>;
  flagged: boolean;
  flag_label: string | null;
  created_at: string;
  has_capture: boolean;
};

type Detail = Row & {
  output: { results?: ResultPayload[]; truncated?: boolean };
  capture: Record<string, unknown>;
  image_urls: string[];
  overlay_urls: string[];
};

/** R5.6 - the inference explorer. */
export default function Traces() {
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<string | null>(null);
  const [filters, setFilters] = useState({
    model_id: params.get("model_id") ?? "",
    status: params.get("status") ?? "",
    error_code: "",
    request_id: "",
    trace_id: "",
    min_latency_ms: "",
    min_confidence: "",
    max_confidence: "",
    flagged: "",
    has_capture: "",
    since_hours: 24,
  });

  const models = useQuery<{ id: string; slug: string; name: string }[]>({ queryKey: ["models-lite"], queryFn: () => api("/v1/models", { query: { limit: 200 } }) });

  const rows = useQuery<Row[]>({
    queryKey: ["inferences", filters],
    queryFn: () =>
      api("/v1/inferences", {
        query: {
          ...filters,
          flagged: filters.flagged || undefined,
          has_capture: filters.has_capture || undefined,
          limit: 100,
        },
      }),
    refetchInterval: 20_000,
  });

  const update = (patch: Partial<typeof filters>) => {
    const next = { ...filters, ...patch };
    setFilters(next);
    const search: Record<string, string> = {};
    if (next.model_id) search.model_id = next.model_id;
    if (next.status) search.status = next.status;
    setParams(search);
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="page-title">Inferences</h1>
        <p className="mt-0.5 text-[12.5px] text-ink-3">
          Every request that reached the gateway. Captured requests carry the image, so you can see exactly what the model saw.
        </p>
      </div>

      <Panel className="overflow-hidden">
        <Toolbar
          trailing={
            <span className="text-[12px] text-ink-3">
              {rows.data ? `${rows.data.length} request${rows.data.length === 1 ? "" : "s"}` : ""}
            </span>
          }
        >
          <Segmented
            label="Time window"
            options={TIME_WINDOWS.map((option) => ({ value: option.value, label: option.label }))}
            value={filters.since_hours}
            onChange={(hours) => update({ since_hours: hours })}
          />
          <select className="select w-auto" value={filters.model_id} onChange={(event) => update({ model_id: event.target.value })} aria-label="Model">
            <option value="">All models</option>
            {(models.data ?? []).map((model) => (
              <option key={model.id} value={model.id}>
                {model.slug}
              </option>
            ))}
          </select>
          <select className="select w-auto" value={filters.status} onChange={(event) => update({ status: event.target.value })} aria-label="Outcome">
            <option value="">All outcomes</option>
            <option value="ok">Succeeded</option>
            <option value="error">Failed</option>
            <option value="timeout">Timed out</option>
            <option value="rejected">Rejected</option>
          </select>
          <select className="select w-auto" value={filters.flagged} onChange={(event) => update({ flagged: event.target.value })} aria-label="Flag state">
            <option value="">All flag states</option>
            <option value="true">Flagged</option>
            <option value="false">Not flagged</option>
          </select>
          <select className="select w-auto" value={filters.has_capture} onChange={(event) => update({ has_capture: event.target.value })} aria-label="Capture">
            <option value="">All captures</option>
            <option value="true">With image</option>
            <option value="false">Without image</option>
          </select>
          <input className="input w-[130px]" placeholder="Min latency ms" inputMode="numeric" value={filters.min_latency_ms} onChange={(event) => update({ min_latency_ms: event.target.value })} aria-label="Minimum latency" />
          <input className="input w-[130px]" placeholder="Min confidence" inputMode="decimal" value={filters.min_confidence} onChange={(event) => update({ min_confidence: event.target.value })} aria-label="Minimum confidence" />
          <input className="input mono w-[150px]" placeholder="Request id" value={filters.request_id} onChange={(event) => update({ request_id: event.target.value })} aria-label="Request id" />
          <input className="input mono w-[150px]" placeholder="Trace id" value={filters.trace_id} onChange={(event) => update({ trace_id: event.target.value })} aria-label="Trace id" />
        </Toolbar>

        {rows.isLoading ? (
          <div className="flex h-40 items-center justify-center text-ink-3">
            <Spinner size={18} />
          </div>
        ) : rows.error ? (
          <div className="p-4">
            <ErrorNote error={rows.error} />
          </div>
        ) : !rows.data?.length ? (
          <EmptyState
            title="No matching requests"
            body="Widen the window or clear a filter. Inference logs are written a moment after the response, so a request made seconds ago may still be in flight."
          />
        ) : (
          <div className="scroll-x">
            <table className="data-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Model</th>
                  <th>Route</th>
                  <th>Caller</th>
                  <th>Outcome</th>
                  <th className="text-right">Preds</th>
                  <th className="text-right">Confidence</th>
                  <th>Latency</th>
                  <th className="text-right">Total</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.data.map((row) => (
                  <tr key={row.id} className="cursor-pointer" onClick={() => setSelected(row.id)}>
                    <td className="whitespace-nowrap text-[11.5px] text-ink-3">{fmt.ago(row.created_at)}</td>
                    <td>
                      <span className="block truncate text-[12.5px] text-ink">{row.model_slug}</span>
                      <span className="mono block text-[11px] text-ink-3">
                        v{row.version_number ?? "?"}
                        {row.tag ? ` · ${row.tag}` : ""}
                      </span>
                    </td>
                    <td>{row.route === "primary" ? <span className="text-[11.5px] text-ink-3">primary</span> : <Badge kind={row.route === "canary" ? "accent" : "warn"}>{row.route}</Badge>}</td>
                    <td className="max-w-[140px] truncate text-[12px] text-ink-2">{row.caller_label || row.caller_type}</td>
                    <td>
                      {row.status === "ok" ? <Status value="ok" /> : <Badge kind="bad">{row.error_code ?? row.status}</Badge>}
                    </td>
                    <td className="num text-right text-[12px]">{row.detection_count}</td>
                    <td className="num text-right text-[12px] text-ink-2">{row.confidence?.mean != null ? row.confidence.mean.toFixed(3) : "—"}</td>
                    <td>
                      <LatencyBar latency={row} width={110} />
                    </td>
                    <td className="num whitespace-nowrap text-right text-[12px]">{fmt.ms(row.total_ms)}</td>
                    <td>{row.flagged ? <Badge kind="warn">flagged</Badge> : row.has_capture ? <span className="text-[11px] text-ink-3">image</span> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <DetailDrawer id={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function DetailDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [label, setLabel] = useState("false_positive");
  const [note, setNote] = useState("");

  const detail = useQuery<Detail>({ queryKey: ["inference", id], queryFn: () => api(`/v1/inferences/${id}`), enabled: Boolean(id) });

  const flag = useMutation({
    mutationFn: (flagged: boolean) => api(`/v1/inferences/${id}/flag`, { method: "POST", body: { flagged, label, note } }),
    onSuccess: () => {
      toast("Flagged. It will be included in the next retraining export.");
      queryClient.invalidateQueries({ queryKey: ["inference", id] });
      queryClient.invalidateQueries({ queryKey: ["inferences"] });
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const data = detail.data;

  return (
    <Modal
      open={Boolean(id)}
      onClose={onClose}
      wide
      title={data ? <span className="mono text-[13px]">{data.request_id}</span> : "Loading"}
      subtitle={data ? `${data.model_slug} v${data.version_number ?? "?"} · ${fmt.when(data.created_at)}` : undefined}
      footer={
        data ? (
          <>
            <div className="mr-auto flex items-end gap-2">
              <Field label="Flag as">
                <select className="select w-[170px]" value={label} onChange={(event) => setLabel(event.target.value)}>
                  <option value="false_positive">false positive</option>
                  <option value="missed_detection">missed detection</option>
                  <option value="wrong_class">wrong class</option>
                  <option value="bad_box">bad box</option>
                  <option value="other">other</option>
                </select>
              </Field>
              <Field label="Note">
                <input className="input w-[220px]" value={note} onChange={(event) => setNote(event.target.value)} placeholder="optional" />
              </Field>
            </div>
            {data.flagged ? (
              <Button variant="ghost" loading={flag.isPending} onClick={() => flag.mutate(false)}>
                Unflag
              </Button>
            ) : null}
            <Button variant="primary" loading={flag.isPending} onClick={() => flag.mutate(true)} disabled={!data.has_capture}>
              Flag for retraining
            </Button>
          </>
        ) : undefined
      }
    >
      {detail.isLoading ? (
        <div className="flex h-40 items-center justify-center text-ink-3">
          <Spinner size={18} />
        </div>
      ) : detail.error ? (
        <ErrorNote error={detail.error} />
      ) : data ? (
        <div className="space-y-4">
          <div>
            <p className="eyebrow mb-2">Latency</p>
            <LatencyBar latency={data} height={8} showLabels />
          </div>

          {data.status !== "ok" ? (
            <div className="rounded-lg border border-bad/40 bg-bad/8 px-3 py-2.5">
              <p className="text-[12.5px] text-ink">
                <span className="mono">{data.error_code}</span> — {data.error_message}
              </p>
            </div>
          ) : null}

          {data.image_urls.length && (data.output?.results ?? []).length ? (
            <div>
              <p className="eyebrow mb-2">What the model saw</p>
              <div className="grid gap-2">
                {data.image_urls.slice(0, 4).map((url, index) => (
                  <ResultViewer
                    key={url}
                    imageUrl={url}
                    result={(data.output.results as ResultPayload[])[index]}
                    maxHeight={380}
                  />
                ))}
              </div>
              {data.overlay_urls.length ? (
                <p className="hint mt-2">
                  Masks are summarised in the log rather than stored, so shapes are not drawn here.
                  The overlay saved with the request has them.
                </p>
              ) : null}
            </div>
          ) : data.overlay_urls.length || data.image_urls.length ? (
            <div>
              <p className="eyebrow mb-2">Captured input</p>
              <div className="grid gap-2 sm:grid-cols-2">
                {(data.overlay_urls.length ? data.overlay_urls : data.image_urls).map((url, index) => (
                  <CaptureImage key={url} url={url} alt={`Captured image ${index + 1}`} />
                ))}
              </div>
            </div>
          ) : (
            <p className="text-[12px] leading-relaxed text-ink-3">
              This request was not sampled for capture, so there is no image. Raise the capture rate in Settings if you need more of them.
            </p>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <p className="eyebrow mb-1.5">Request</p>
              <KeyValue
                items={[
                  ["Caller", `${data.caller_label} (${data.caller_type})`],
                  ["Route", data.route],
                  ["Tag", data.tag ?? "—"],
                  ["Batch", String(data.batch_size)],
                  ["Runtime", data.runtime_id ?? "—"],
                  ["Trace", data.trace_id ? data.trace_id.slice(0, 16) : "—"],
                  ["Overrides", Object.keys(data.overrides ?? {}).length ? JSON.stringify(data.overrides) : "none"],
                ]}
              />
            </div>
            <div>
              <p className="eyebrow mb-1.5">Predictions</p>
              <KeyValue
                items={[
                  ["Count", String(data.detection_count)],
                  ["Mean score", data.confidence?.mean != null ? data.confidence.mean.toFixed(4) : "—"],
                  ["Classes", Object.keys(data.confidence?.classes ?? {}).slice(0, 4).join(", ") || "—"],
                  ["Input", `${(data.input_ref?.count as number) ?? 0} image(s), ${fmt.bytes(data.input_ref?.bytes as number)}`],
                ]}
              />
            </div>
          </div>

          <div>
            <p className="eyebrow mb-1.5">Output {data.output?.truncated ? "(truncated)" : ""}</p>
            <JsonView value={data.output} />
          </div>
        </div>
      ) : null}
    </Modal>
  );
}
