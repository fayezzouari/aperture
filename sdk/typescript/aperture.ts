/**
 * Minimal Aperture client for TypeScript. No dependencies - uses fetch.
 *
 * Hand-written so it can be vendored straight into a product. For a fully typed
 * client generated from the OpenAPI document, run `make sdk`.
 *
 *   const aperture = new Aperture({ baseUrl: "https://aperture.internal", apiKey: KEY });
 *   const result = await aperture.predict("part-detection", "production", { images: [file] });
 *   for (const d of result.detections()) console.log(d.class_name, d.score, d.box);
 */

export type Box = [number, number, number, number];

export interface Detection {
  box: Box;
  score: number;
  class_id: number;
  class_name: string;
  mask?: { format: "rle" | "polygon"; size: number[]; counts?: string; polygons?: number[][] } | null;
  keypoints?: { name?: string | null; x: number; y: number; score: number }[] | null;
}

export interface Classification {
  class_id: number;
  class_name: string;
  score: number;
}

export interface TextRegion {
  text: string;
  score: number;
  box?: Box | null;
}

export interface ImageResult {
  index: number;
  width: number;
  height: number;
  detections?: Detection[];
  segments?: Detection[];
  classifications?: Classification[];
  texts?: TextRegion[];
  embedding?: number[];
  embedding_dim?: number;
  error?: string | null;
}

export interface LatencyBreakdown {
  queue_ms: number;
  preprocess_ms: number;
  inference_ms: number;
  postprocess_ms: number;
  runtime_ms: number;
  gateway_ms: number;
  total_ms: number;
}

export interface PredictResponse {
  request_id: string;
  model: string;
  /** The version that actually served this call, even when a tag was used. */
  model_version: number;
  version_id: string;
  tag: string | null;
  task: string;
  batch_size: number;
  results: ImageResult[];
  latency: LatencyBreakdown;
  runtime_id: string | null;
  trace_id: string | null;
  overrides_applied: Record<string, number | boolean>;
  overlays?: string[] | null;
}

/** Stable codes worth branching on. Anything else is a bug to report. */
export type ApertureErrorCode =
  | "model_not_found"
  | "version_not_found"
  | "tag_not_found"
  | "model_not_deployed"
  | "no_replica_available"
  | "queue_full"
  | "rate_limited"
  | "quota_exceeded"
  | "invalid_image"
  | "payload_too_large"
  | "batch_too_large"
  | "timeout"
  | "api_key_invalid"
  | "api_key_revoked"
  | "key_not_scoped_to_model"
  | "inference_failed"
  | "internal_error";

export class ApertureError extends Error {
  readonly code: ApertureErrorCode | string;
  readonly status: number;
  readonly requestId?: string;
  readonly details?: Record<string, unknown>;

  constructor(status: number, code: string, message: string, requestId?: string, details?: Record<string, unknown>) {
    super(`${code}: ${message}${requestId ? ` (request ${requestId})` : ""}`);
    this.name = "ApertureError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }

  /** These mean "try again shortly", not "give up". */
  get retryable(): boolean {
    return [
      "queue_full",
      "rate_limited",
      "no_replica_available",
      "runtime_unhealthy",
      "upstream_error",
      "timeout",
    ].includes(this.code);
  }
}

export interface ApertureOptions {
  baseUrl: string;
  apiKey?: string;
  project?: string;
  fetch?: typeof fetch;
  timeoutMs?: number;
}

export interface PredictOptions {
  images?: (Blob | File)[];
  imageUrls?: string[];
  imagesBase64?: string[];
  overrides?: Record<string, number | boolean>;
  includeOverlay?: boolean;
  traceparent?: string;
  signal?: AbortSignal;
}

export class Aperture {
  private readonly baseUrl: string;
  private readonly apiKey?: string;
  private readonly project?: string;
  private readonly fetchImpl: typeof fetch;
  private readonly timeoutMs: number;

  constructor(options: ApertureOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.apiKey = options.apiKey;
    this.project = options.project;
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
    this.timeoutMs = options.timeoutMs ?? 30_000;
  }

  async predict(model: string, ref = "production", options: PredictOptions = {}): Promise<PredictResult> {
    const url = `${this.baseUrl}/v1/models/${encodeURIComponent(model)}/${encodeURIComponent(ref)}/predict`;
    const headers: Record<string, string> = {};
    if (this.apiKey) headers["x-api-key"] = this.apiKey;
    if (this.project) headers["x-aperture-project"] = this.project;
    if (options.traceparent) headers.traceparent = options.traceparent;

    let body: BodyInit;
    if (options.images?.length) {
      const form = new FormData();
      for (const image of options.images) form.append("images", image);
      form.append("include_overlay", String(Boolean(options.includeOverlay)));
      if (options.overrides) form.append("overrides", JSON.stringify(options.overrides));
      body = form;
    } else {
      headers["content-type"] = "application/json";
      body = JSON.stringify({
        images: options.imagesBase64,
        image_urls: options.imageUrls,
        overrides: options.overrides,
        include_overlay: Boolean(options.includeOverlay),
      });
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    options.signal?.addEventListener("abort", () => controller.abort());

    try {
      const response = await this.fetchImpl(url, { method: "POST", headers, body, signal: controller.signal });
      if (!response.ok) {
        let code = "internal_error";
        let message = `HTTP ${response.status}`;
        let details: Record<string, unknown> | undefined;
        try {
          const payload = await response.json();
          code = payload?.error?.code ?? code;
          message = payload?.error?.message ?? message;
          details = payload?.error?.details;
        } catch {
          /* a non-JSON body (a proxy, say) keeps the HTTP fallback */
        }
        throw new ApertureError(response.status, code, message, response.headers.get("x-request-id") ?? undefined, details);
      }
      return new PredictResult(await response.json());
    } finally {
      clearTimeout(timer);
    }
  }

  async models(): Promise<unknown[]> {
    return this.json("/v1/models");
  }

  async job(id: string): Promise<{ id: string; status: string; progress: number; total: number; result: Record<string, unknown> }> {
    return this.json(`/v1/jobs/${id}`);
  }

  private async json<T>(path: string): Promise<T> {
    const headers: Record<string, string> = {};
    if (this.apiKey) headers["x-api-key"] = this.apiKey;
    if (this.project) headers["x-aperture-project"] = this.project;
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, { headers });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new ApertureError(response.status, payload?.error?.code ?? "internal_error", payload?.error?.message ?? `HTTP ${response.status}`);
    }
    return response.json() as Promise<T>;
  }
}

export class PredictResult {
  constructor(readonly raw: PredictResponse) {}

  get requestId(): string {
    return this.raw.request_id;
  }

  get modelVersion(): number {
    return this.raw.model_version;
  }

  get latencyMs(): number {
    return this.raw.latency.total_ms;
  }

  detections(index = 0): Detection[] {
    return this.raw.results[index]?.detections ?? [];
  }

  classifications(index = 0): Classification[] {
    return this.raw.results[index]?.classifications ?? [];
  }

  texts(index = 0): TextRegion[] {
    return this.raw.results[index]?.texts ?? [];
  }

  embedding(index = 0): number[] | undefined {
    return this.raw.results[index]?.embedding;
  }
}
