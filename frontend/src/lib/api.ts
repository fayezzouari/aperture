/**
 * One fetch wrapper for the whole app.
 *
 * It carries the session token, the active workspace header, and turns the API's
 * error contract into a typed exception, so every page can render a real
 * message ("tag 'production' is locked") instead of "request failed".
 */

export class ApiError extends Error {
  code: string;
  status: number;
  details?: Record<string, unknown>;
  requestId?: string;

  constructor(status: number, code: string, message: string, details?: Record<string, unknown>, requestId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

const TOKEN_KEY = "aperture.token";

// held in memory only: the server remembers each user's last workspace, so a
// new tab or another device opens on the same one without anything stored here
let activeWorkspace: string | null = null;

export const session = {
  get token() {
    return localStorage.getItem(TOKEN_KEY);
  },
  set token(value: string | null) {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  },
  get workspace() {
    return activeWorkspace;
  },
  set workspace(slug: string | null) {
    activeWorkspace = slug;
  },
};

type Options = {
  method?: string;
  body?: unknown;
  form?: FormData;
  query?: Record<string, string | number | boolean | undefined | null>;
  raw?: boolean;
  signal?: AbortSignal;
};

function buildUrl(path: string, query?: Options["query"]) {
  const url = new URL(path.startsWith("/") ? path : `/${path}`, window.location.origin);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

export async function api<T = unknown>(path: string, options: Options = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (session.token) headers.authorization = `Bearer ${session.token}`;
  if (session.workspace) headers["x-aperture-workspace"] = session.workspace;

  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form;
  } else if (options.body !== undefined) {
    body = JSON.stringify(options.body);
    headers["content-type"] = "application/json";
  }

  const response = await fetch(buildUrl(path, options.query), {
    method: options.method ?? (body ? "POST" : "GET"),
    headers,
    body,
    signal: options.signal,
    credentials: "same-origin",
  });

  if (response.status === 204) return undefined as T;

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
      /* a non-JSON error body (a proxy, say) keeps the HTTP fallback above */
    }
    if (response.status === 401 && !path.includes("/auth/")) {
      session.token = null;
      if (!window.location.pathname.startsWith("/login")) {
        // the query too: the OAuth consent page is nothing without it
        window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
      }
    }
    throw new ApiError(response.status, code, message, details, response.headers.get("x-request-id") ?? undefined);
  }

  if (options.raw) return (await response.blob()) as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export async function download(path: string, options: Options & { filename: string }) {
  const blob = await api<Blob>(path, { ...options, raw: true });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = options.filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/**
 * R1.1 - resumable chunked upload. Each part is a separate request, so a
 * dropped connection costs one chunk, not a 4 GB re-send.
 */
export async function uploadWeights(opts: {
  model: string;
  version: number;
  file: File;
  sha256?: string;
  chunkBytes?: number;
  onProgress?: (sent: number, total: number) => void;
  signal?: AbortSignal;
}) {
  const chunkBytes = opts.chunkBytes ?? 32 * 1024 * 1024;
  const upload = await api<{ id: string; chunk_bytes: number; next_part: number }>(
    `/v1/models/${opts.model}/versions/${opts.version}/uploads`,
    { body: { filename: opts.file.name, total_bytes: opts.file.size, sha256: opts.sha256, chunk_bytes: chunkBytes } }
  );

  const size = upload.chunk_bytes || chunkBytes;
  const parts = Math.max(1, Math.ceil(opts.file.size / size));
  let sent = 0;
  for (let part = 1; part <= parts; part += 1) {
    if (opts.signal?.aborted) throw new ApiError(0, "cancelled", "the upload was cancelled");
    const slice = opts.file.slice((part - 1) * size, part * size);
    const form = new FormData();
    form.append("chunk", slice, `${opts.file.name}.part${part}`);
    await api(`/v1/uploads/${upload.id}/parts/${part}`, { method: "PUT", form, signal: opts.signal });
    sent += slice.size;
    opts.onProgress?.(sent, opts.file.size);
  }
  return api(`/v1/uploads/${upload.id}/complete`, { method: "POST" });
}

/** Browser-side SHA-256 so the server can prove the bytes survived the trip (R1.8). */
export async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
