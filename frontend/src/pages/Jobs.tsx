import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { ProgressBar } from "../components/bits";
import { Badge, Button, EmptyState, ErrorNote, Panel, PanelHeader, Spinner, Status, useToast } from "../components/ui";

type Job = {
  id: string;
  kind: string;
  status: string;
  params: Record<string, unknown>;
  progress: number;
  total: number;
  result: Record<string, unknown>;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export default function Jobs() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const jobs = useQuery<Job[]>({ queryKey: ["jobs"], queryFn: () => api("/v1/jobs", { query: { limit: 60 } }), refetchInterval: 5_000 });

  const cancel = useMutation({
    mutationFn: (id: string) => api(`/v1/jobs/${id}/cancel`, { method: "POST" }),
    onSuccess: () => {
      toast("Cancelled. Batches already running finish, nothing new starts.");
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const openResult = useMutation({
    mutationFn: (id: string) => api<{ url?: string }>(`/v1/jobs/${id}/result`),
    onSuccess: (result) => {
      if (result.url) window.open(result.url, "_blank", "noreferrer");
      else toast("This job produced no downloadable artifact.", "bad");
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="page-title">Jobs</h1>
        <p className="mt-0.5 text-[12.5px] text-ink-3">
          Long work runs here, never on the inference path: bulk folders, video, format conversion, dataset exports and capture purges.
        </p>
      </div>

      <Panel>
        {jobs.isLoading ? (
          <div className="flex h-32 items-center justify-center text-ink-3">
            <Spinner size={18} />
          </div>
        ) : !jobs.data?.length ? (
          <EmptyState
            title="No jobs yet"
            body="Submit one from a model's page (bulk inference, conversion) or from the explorer (export flagged predictions). Every job returns an id you can poll, or a webhook."
          />
        ) : (
          <div className="scroll-x">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Kind</th>
                  <th>Status</th>
                  <th>Progress</th>
                  <th>Result</th>
                  <th>Started</th>
                  <th className="text-right">Took</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {jobs.data.map((job) => {
                  const took =
                    job.started_at && job.finished_at ? (new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000 : null;
                  return (
                    <tr key={job.id}>
                      <td>
                        <Badge>{job.kind}</Badge>
                        <span className="mono ml-2 text-[11px] text-ink-3">{job.id.slice(0, 8)}</span>
                      </td>
                      <td>
                        <Status value={job.status} />
                        {job.error ? <p className="mt-0.5 max-w-[260px] truncate text-[11px] text-bad">{job.error}</p> : null}
                      </td>
                      <td className="w-[160px]">{job.total ? <ProgressBar value={job.progress} total={job.total} /> : <span className="text-[11.5px] text-ink-3">—</span>}</td>
                      <td className="text-[11.5px] text-ink-3">
                        {job.status === "succeeded"
                          ? Object.entries(job.result ?? {})
                              .filter(([key]) => !["artifact_key", "next_step"].includes(key))
                              .slice(0, 3)
                              .map(([key, value]) => `${key} ${value}`)
                              .join(" · ")
                          : "—"}
                      </td>
                      <td className="text-[11.5px] text-ink-3">{job.started_at ? fmt.ago(job.started_at) : fmt.ago(job.created_at)}</td>
                      <td className="num text-right text-[11.5px] text-ink-3">{fmt.duration(took)}</td>
                      <td className="text-right">
                        {job.status === "succeeded" && (job.result ?? {}).artifact_key ? (
                          <Button size="sm" loading={openResult.isPending} onClick={() => openResult.mutate(job.id)}>
                            Download
                          </Button>
                        ) : ["queued", "running"].includes(job.status) ? (
                          <Button size="sm" variant="danger" onClick={() => cancel.mutate(job.id)}>
                            Cancel
                          </Button>
                        ) : null}
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
        <PanelHeader title="Submitting a job from code" />
        <div className="px-4 py-3">
          <pre className="mono scroll-x text-[11.5px] leading-relaxed text-ink-2">{`POST /v1/jobs
{
  "kind": "bulk_images",
  "model": "my-model",
  "ref": "production",
  "source_keys": ["batch-2026-08/img-0001.jpg", "..."],
  "webhook_url": "https://internal.example/aperture-callback"
}
-> 202 { "id": "...", "status": "queued" }

GET /v1/jobs/{id}          # poll
GET /v1/jobs/{id}/result   # signed link to the predictions`}</pre>
        </div>
      </Panel>
      <ErrorNote error={jobs.error} />
    </div>
  );
}
