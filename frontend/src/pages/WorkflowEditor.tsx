import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  MarkerType,
  ReactFlowProvider,
  addEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api } from "../lib/api";
import { fmt } from "../lib/format";
import { can, useRole } from "../lib/hooks";
import { Dropzone, JsonView } from "../components/bits";
import { Badge, Button, ErrorNote, Panel, PanelHeader, Spinner, Status, useToast } from "../components/ui";
import { Segmented } from "../components/toolbar";
import Inspector from "./workflow/Inspector";
import { NODE_META, nodeTypes, type NodeData, type NodeKind } from "./workflow/nodes";

type Workflow = {
  id: string;
  slug: string;
  name: string;
  description: string;
  graph: { nodes: Node[]; edges: Edge[] };
  published_graph: { nodes: Node[]; edges: Edge[] } | null;
  revision: number;
  status: string;
  problems: string[];
  models_used: string[];
  runs_24h: number;
  error_rate_24h: number;
  p95_ms_24h: number | null;
};

type RunResult = {
  run_id: string;
  request_id: string;
  revision: number;
  status: string;
  outputs: Record<string, unknown>;
  node_stats: { node_id: string; label: string; status: string; ms: number; items_out: number; detections: number; version?: number | null; error?: string | null }[];
  model_calls: number;
  total_ms: number;
  failed_node: string | null;
  error?: { code: string; message: string } | null;
  previews: Record<string, string[]>;
};

const PALETTE: NodeKind[] = ["model", "gate", "crop", "filter", "geometry", "merge", "output"];

export default function WorkflowEditor() {
  return (
    <ReactFlowProvider>
      <Editor />
    </ReactFlowProvider>
  );
}

function Editor() {
  const { slug = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const role = useRole();
  const editable = can(role, "developer");

  const workflow = useQuery<Workflow>({ queryKey: ["workflow", slug], queryFn: () => api(`/v1/workflows/${slug}`) });

  const flow = useReactFlow();
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [mode, setMode] = useState<"build" | "try">("build");
  const [files, setFiles] = useState<File[]>([]);
  const [run, setRun] = useState<RunResult | null>(null);
  const [runError, setRunError] = useState<unknown>(null);

  // load the saved graph into the canvas once
  useEffect(() => {
    if (!workflow.data) return;
    const graph = workflow.data.graph ?? { nodes: [], edges: [] };
    setNodes((graph.nodes ?? []).map((node) => ({ ...node, type: "aperture" })));
    setEdges((graph.edges ?? []).map((edge) => ({ ...edge })));
    setDirty(false);
    // fitView on mount runs before the nodes have been measured, which leaves
    // the graph parked in a corner. Fit once they have real dimensions.
    const timer = window.setTimeout(() => flow.fitView({ padding: 0.18, maxZoom: 1, duration: 200 }), 60);
    return () => window.clearTimeout(timer);
  }, [workflow.data?.id, workflow.data?.revision, flow, setNodes, setEdges]);

  const save = useMutation({
    mutationFn: () =>
      api(`/v1/workflows/${slug}`, {
        method: "PATCH",
        body: { graph: { nodes: stripRuntime(nodes), edges: edges.map(({ id, source, target }) => ({ id, source, target })) } },
      }),
    onSuccess: () => {
      setDirty(false);
      toast("Saved.");
      queryClient.invalidateQueries({ queryKey: ["workflow", slug] });
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const publish = useMutation({
    mutationFn: async () => {
      await save.mutateAsync();
      return api<Workflow>(`/v1/workflows/${slug}/publish`, { method: "POST" });
    },
    onSuccess: (result) => {
      toast(`Published revision ${result.revision}. Callers get this graph now.`);
      queryClient.invalidateQueries({ queryKey: ["workflow", slug] });
    },
    onError: (error) => toast((error as Error).message, "bad"),
  });

  const execute = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      files.forEach((file) => form.append("images", file));
      form.append("draft", "true");
      form.append("include_previews", "true");
      if (dirty) await save.mutateAsync();
      return api<RunResult>(`/v1/workflows/${slug}/run`, { form });
    },
    onSuccess: (result) => {
      setRun(result);
      setRunError(null);
      if (result.status !== "ok") toast(result.error?.message ?? "The run failed.", "bad");
    },
    onError: (error) => {
      setRun(null);
      setRunError(error);
    },
  });

  // paint the last run's per-node timings onto the canvas
  useEffect(() => {
    if (!run) return;
    const byId = new Map(run.node_stats.map((stat) => [stat.node_id, stat]));
    setNodes((current) =>
      current.map((node) => ({
        ...node,
        data: { ...(node.data as NodeData), stat: byId.get(node.id) as NodeData["stat"] },
      }))
    );
  }, [run, setNodes]);

  const selected = useMemo(() => {
    const found = nodes.find((node) => node.id === selectedId);
    return found ? { id: found.id, data: found.data as NodeData } : null;
  }, [nodes, selectedId]);

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((current) => addEdge({ ...connection, id: `e${Date.now()}` }, current));
      setDirty(true);
    },
    [setEdges]
  );

  const addNode = (kind: NodeKind) => {
    const id = `${kind}-${Math.random().toString(36).slice(2, 7)}`;
    const offset = nodes.length * 18;
    setNodes((current) => [
      ...current,
      {
        id,
        type: "aperture",
        position: { x: 260 + (offset % 220), y: 260 + (offset % 160) },
        data: { type: kind, label: NODE_META[kind].label, ...defaults(kind) } as NodeData,
      },
    ]);
    setSelectedId(id);
    setDirty(true);
  };

  const patchNode = (patch: Partial<NodeData>) => {
    if (!selectedId) return;
    setNodes((current) =>
      current.map((node) => (node.id === selectedId ? { ...node, data: { ...(node.data as NodeData), ...patch } } : node))
    );
    setDirty(true);
  };

  const deleteNode = () => {
    if (!selectedId) return;
    setNodes((current) => current.filter((node) => node.id !== selectedId));
    setEdges((current) => current.filter((edge) => edge.source !== selectedId && edge.target !== selectedId));
    setSelectedId(null);
    setDirty(true);
  };

  if (workflow.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center text-ink-3">
        <Spinner size={18} />
      </div>
    );
  }
  if (workflow.error) return <ErrorNote error={workflow.error} />;
  const data = workflow.data!;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <nav className="flex items-center gap-1.5 text-[12px] text-ink-3">
            <Link to="/workflows" className="hover:text-ink-2">
              Workflows
            </Link>
            <span>/</span>
            <span className="mono text-ink-2">{data.slug}</span>
          </nav>
          <h1 className="mt-1 flex items-center gap-2.5 text-[19px] font-semibold tracking-tight">
            {data.name}
            <Badge kind={data.status === "published" ? "good" : "neutral"}>
              {data.status === "published" ? `published · r${data.revision}` : "draft"}
            </Badge>
            {dirty ? <Badge kind="warn">unsaved</Badge> : null}
          </h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 text-[12px] text-ink-3">
            <span>{data.models_used.length} model{data.models_used.length === 1 ? "" : "s"}</span>
            <span>{fmt.int(data.runs_24h)} runs · 24 h</span>
            <span>p95 {fmt.ms(data.p95_ms_24h)}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Segmented
            label="Editor mode"
            options={[
              { value: "build", label: "Build" },
              { value: "try", label: "Try it" },
            ]}
            value={mode}
            onChange={(next) => setMode(next as "build" | "try")}
          />
          {editable ? (
            <>
              <Button loading={save.isPending} disabled={!dirty} onClick={() => save.mutate()}>
                Save
              </Button>
              <Button variant="primary" loading={publish.isPending} onClick={() => publish.mutate()}>
                Publish
              </Button>
            </>
          ) : null}
        </div>
      </div>

      {data.problems.length ? (
        <div className="rounded-lg border border-warn/40 bg-warn/8 px-3.5 py-2.5">
          <p className="text-[12.5px] text-ink">This graph cannot be published yet:</p>
          <ul className="mt-1 space-y-0.5">
            {data.problems.map((problem, index) => (
              <li key={index} className="text-[12px] text-ink-2">
                · {problem}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className={mode === "build" ? "grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]" : "hidden"}>
        <Panel className="overflow-hidden">
          <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-2">
            <span className="eyebrow mr-1">Add</span>
            {PALETTE.map((kind) => (
              <Button key={kind} size="sm" disabled={!editable} onClick={() => addNode(kind)}>
                <span style={{ color: NODE_META[kind].accent }} aria-hidden>
                  {NODE_META[kind].glyph}
                </span>
                {NODE_META[kind].label}
              </Button>
            ))}
          </div>
          <div style={{ height: 520 }} className="bg-[#08080a]">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={(changes) => {
                onNodesChange(changes);
                if (changes.some((change) => change.type === "position" || change.type === "remove")) setDirty(true);
              }}
              onEdgesChange={(changes) => {
                onEdgesChange(changes);
                if (changes.some((change) => change.type === "remove")) setDirty(true);
              }}
              onConnect={onConnect}
              onNodeClick={(_, node) => setSelectedId(node.id)}
              onPaneClick={() => setSelectedId(null)}
              nodeTypes={nodeTypes}
              // React Flow's own dark palette for its chrome; flow-dark then
              // pulls the accents onto the console's colours.
              colorMode="dark"
              className="flow-dark"
              fitView
              // never zoom out past readable: a wide graph pans, it does not shrink
              fitViewOptions={{ padding: 0.18, minZoom: 0.55, maxZoom: 1 }}
              minZoom={0.3}
              maxZoom={1.75}
              proOptions={{ hideAttribution: false }}
              defaultEdgeOptions={{
                markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: "#4a4a52" },
              }}
              nodesDraggable={editable}
              nodesConnectable={editable}
              edgesReconnectable={editable}
            >
              <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#1e1e22" />
              <Controls showInteractive={false} />
              <MiniMap
                pannable
                zoomable
                nodeColor={(node) => NODE_META[(node.data as NodeData).type]?.accent ?? "#3a3a40"}
                nodeStrokeWidth={0}
                maskColor="rgba(0,0,0,0.62)"
              />
            </ReactFlow>
          </div>
        </Panel>

        <Panel className="h-fit overflow-hidden">
          <PanelHeader title="Node" subtitle={selected ? undefined : "Nothing selected"} />
          <Inspector node={selected} onChange={patchNode} onDelete={deleteNode} />
        </Panel>
      </div>

      <div className={mode === "try" ? "grid gap-4 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]" : "hidden"}>
        <div className="space-y-4">
          <Panel>
            <PanelHeader
              title="Run this workflow"
              subtitle="Runs the graph as it stands on the canvas, published or not."
            />
            <div className="space-y-3 px-4 py-4">
              {files.length ? (
                <div className="flex items-center justify-between gap-2 text-[12px]">
                  <span className="truncate text-ink-2">{files.length} image{files.length > 1 ? "s" : ""} ready</span>
                  <Button size="sm" variant="ghost" onClick={() => setFiles([])}>
                    Clear
                  </Button>
                </div>
              ) : (
                <Dropzone label="Drop an image" hint="The whole graph runs on it" accept="image/*" multiple onFiles={setFiles} />
              )}
              <Button variant="primary" className="w-full" loading={execute.isPending} disabled={!files.length || !editable} onClick={() => execute.mutate()}>
                Run workflow
              </Button>
              <ErrorNote error={runError} />
              {dirty ? <p className="hint">Unsaved canvas changes are saved before the run.</p> : null}
            </div>
          </Panel>

          {run ? (
            <Panel>
              <PanelHeader title="Where the time went" subtitle="Per node, in order." />
              <div className="space-y-2 px-4 py-4">
                <div className="flex items-center justify-between text-[12px]">
                  <Status value={run.status} />
                  <span className="num text-ink-2">
                    {fmt.ms(run.total_ms)} · {run.model_calls} model call{run.model_calls === 1 ? "" : "s"}
                  </span>
                </div>
                {run.node_stats.map((stat) => (
                  <div key={stat.node_id} className="grid grid-cols-[1fr_auto] items-center gap-2">
                    <div className="min-w-0">
                      <p className={`truncate text-[11.5px] ${stat.status === "error" ? "text-bad" : "text-ink-2"}`}>
                        {stat.label}
                      </p>
                      <div className="mt-0.5 h-[5px] rounded-[2px] bg-[#141418]">
                        <div
                          className="h-[5px] rounded-[2px]"
                          style={{
                            width: `${Math.max(1.5, (stat.ms / Math.max(run.total_ms, 1)) * 100)}%`,
                            background: stat.status === "error" ? "var(--color-bad)" : "var(--color-series-1)",
                          }}
                        />
                      </div>
                    </div>
                    <span className="num shrink-0 text-[11px] text-ink-3">
                      {stat.items_out} · {fmt.ms(stat.ms, 0)}
                    </span>
                  </div>
                ))}
              </div>
            </Panel>
          ) : null}
        </div>

        <div className="space-y-4">
          {run ? (
            <>
              {Object.entries(run.previews).length ? (
                <Panel>
                  <PanelHeader title="What each stage saw" subtitle="Intermediate images, in graph order." />
                  <div className="grid grid-cols-3 gap-2 px-4 py-4 sm:grid-cols-6">
                    {Object.values(run.previews)
                      .flat()
                      .slice(0, 12)
                      .map((b64, index) => (
                        <img
                          key={index}
                          src={`data:image/jpeg;base64,${b64}`}
                          alt={`Intermediate image ${index + 1}`}
                          className="h-20 w-full rounded border border-line object-cover"
                        />
                      ))}
                  </div>
                </Panel>
              ) : null}
              <Panel>
                <PanelHeader
                  title="Output"
                  subtitle={`${Object.keys(run.outputs).length} key(s) · request ${run.request_id.slice(0, 10)}`}
                />
                <div className="px-4 py-4">
                  <JsonView value={run.outputs} />
                </div>
              </Panel>
            </>
          ) : (
            <Panel>
              <div className="px-6 py-16 text-center text-[12.5px] leading-relaxed text-ink-3">
                Drop an image and run the workflow. You get the output, the intermediate images each
                stage produced, and how long every node took.
              </div>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}

function defaults(kind: NodeKind): Partial<NodeData> {
  if (kind === "model") return { ref: "production" };
  if (kind === "crop") return { max_crops: 8, padding: 0.05, min_score: 0.3 };
  if (kind === "filter") return { min_score: 0.5, top_k: 5 };
  if (kind === "geometry") return { input_name: "aux", format: "cxcywh" };
  if (kind === "output") return { key: "result", format: "aperture" };
  return {};
}

/** The saved graph keeps positions and config, never the last run's timings. */
function stripRuntime(nodes: Node[]) {
  return nodes.map((node) => {
    const { stat, ...rest } = node.data as NodeData;
    void stat;
    return { id: node.id, position: node.position, data: rest };
  });
}
