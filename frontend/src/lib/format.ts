export const fmt = {
  ms(value?: number | null, digits = 1) {
    if (value === null || value === undefined) return "—";
    if (value >= 1000) return `${(value / 1000).toFixed(2)} s`;
    return `${value.toFixed(digits)} ms`;
  },
  int(value?: number | null) {
    if (value === null || value === undefined) return "—";
    return value.toLocaleString("en-US");
  },
  pct(value?: number | null, digits = 2) {
    if (value === null || value === undefined) return "—";
    return `${(value * 100).toFixed(digits)}%`;
  },
  bytes(value?: number | null) {
    if (!value) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let index = 0;
    let size = value;
    while (size >= 1024 && index < units.length - 1) {
      size /= 1024;
      index += 1;
    }
    return `${size.toFixed(size < 10 && index > 0 ? 1 : 0)} ${units[index]}`;
  },
  score(value?: number | null) {
    if (value === null || value === undefined) return "—";
    return value.toFixed(3);
  },
  when(value?: string | null) {
    if (!value) return "—";
    const date = new Date(value);
    return date.toLocaleString("en-GB", { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  },
  ago(value?: string | null) {
    if (!value) return "—";
    const seconds = (Date.now() - new Date(value).getTime()) / 1000;
    if (seconds < 45) return "just now";
    const steps: [number, string][] = [
      [60, "min"],
      [3600, "h"],
      [86400, "d"],
    ];
    if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
    if (seconds < 86400 * 30) return `${Math.round(seconds / 86400)} d ago`;
    void steps;
    return fmt.when(value);
  },
  duration(seconds?: number | null) {
    if (!seconds) return "—";
    if (seconds < 60) return `${seconds.toFixed(0)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
  },
};

export const STATUS_TONE: Record<string, "good" | "warn" | "bad" | "neutral"> = {
  ok: "good",
  ready: "good",
  live: "good",
  passed: "good",
  succeeded: "good",
  warming: "warn",
  pending: "warn",
  validating: "warn",
  queued: "warn",
  running: "warn",
  draining: "warn",
  draft: "neutral",
  deprecated: "neutral",
  archived: "neutral",
  stopped: "neutral",
  cancelled: "neutral",
  error: "bad",
  failed: "bad",
  timeout: "bad",
  rejected: "bad",
  firing: "bad",
};

export function tone(status?: string | null) {
  return STATUS_TONE[(status ?? "").toLowerCase()] ?? "neutral";
}
