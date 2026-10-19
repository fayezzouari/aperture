# Architecture

Written for whoever has to change this or debug it at 2am.

## The one-sentence version

The control plane owns *what should be serving*; runtimes own *the weights*;
they agree through a heartbeat every few seconds, and nothing else in the system
needs to know where a model lives.

---

## The request path

```
client
  │  POST /v1/models/part-detection/production/predict
  ▼
nginx ──────────────────────────────── one origin for UI and API
  │
  ▼
api  ┌──────────────────────────────────────────────────────────────────┐
     │ 1. authenticate: API key (hash compare) or session JWT          │
     │ 2. rate limit + quota          [redis counter, fixed window]     │
     │ 3. resolve workspace, model    [postgres]                        │
     │ 4. key scope check                                               │
     │ 5. read images, enforce size and count limits                    │
     │ 6. resolve {version|tag}       [redis, 5 s TTL → postgres]        │
     │ 7. choose target: primary, or a canary slice; collect shadows     │
     │ 8. pick a replica              [redis: fresh heartbeat AND        │
     │                                 version resident, round robin]    │
     │ 9. POST the batch to that runtime                                 │
     └──────────────────────────────────────────────────────────────────┘
        │                                              ▲
        ▼                                              │
runtime ┌────────────────────────────────────────────────────────────────┐
        │ decode images (thread)                                        │
        │ preprocess → tensor (thread)                                  │
        │ ── enqueue ───▶ batcher: collect up to max_batch_size,        │
        │                 waiting at most max_wait_ms, then ONE         │
        │                 forward pass for all of them                  │
        │ ◀── outputs sliced back per request                           │
        │ postprocess → typed results (thread)                          │
        └────────────────────────────────────────────────────────────────┘
        │
        ▼
api  response + a fire-and-forget task: publish the log record,
     upload the captured payload if this request was sampled,
     mirror to shadow deployments
```

Three properties this shape buys:

* **No decode in the gateway.** The API forwards bytes; the runtime decodes once
  and returns the dimensions and input statistics with the result. One decode
  per image, on the process that has to do it anyway.
* **Preprocessing overlaps the GPU.** Only the forward pass is serialised through
  the batcher; decode, preprocess and postprocess run per request in threads, so
  they overlap the inference of other requests.
* **Logging never blocks a response.** Records go onto a Redis stream and the
  response returns. The log sink is a separate process, and a capped stream
  bounds what a Postgres outage can cost.

### Why the gateway is not its own service

Resolving a tag needs the registry. Splitting them would put a network hop and a
cache in front of every single inference call to save an abstraction nobody
asked for. The API is stateless either way, so it scales the same.

---

## Deployment and the reconciler

Desired state lives in `deployments` (which version, how many replicas, which
device, batching, timeouts). Actual state lives in `assignments` (one row per
replica: this runtime should hold this version). The reconciler closes the gap
and runs on every heartbeat, every deployment change, and a 15-second cron tick:

1. drop assignments whose runtime stopped heartbeating — those replicas do not exist
2. for each deployment, compare live assignments against `replicas`
3. scale down by draining the *newest* replicas first, so a warm one keeps serving
4. scale up onto the least-loaded runtimes that match the device request, never
   two replicas of one version on the same runtime
5. roll assignment states up into a deployment status the UI can show

A runtime that receives a new assignment downloads the weights (verifying the
checksum recorded at upload), builds the backend, warms it up, and only then
reports `ready`. The gateway routes exclusively to `ready` replicas, which is
what makes a version switch free of cold starts: the new version is warm before
the tag moves.

```
deployment created ──▶ assignment(pending)
                          │ runtime heartbeat picks it up
                          ▼
                       loading ──▶ warming ──▶ ready ──▶ (routable)
                          │            │
                          └────────────┴──▶ failed (with the reason, shown in the UI)
```

### Failure modes, and what happens

| What breaks | What the user sees |
|---|---|
| A runtime dies | Its heartbeat goes stale within 20 s; the gateway stops routing to it; the reconciler re-places its replicas. Requests in flight fail with `upstream_error`. |
| Every replica of a version is gone | `503 no_replica_available` — a distinct code from `model_not_deployed`, because one means "capacity" and the other means "you never deployed it". |
| A runtime is saturated | `503 queue_full` with the queue depth in `details`. Deliberate: the alternative is a timeout storm. |
| Redis is down | Routing and rate limits fail; the registry is derived state, so recovery is a cold cache and nothing else. |
| Postgres is down | Serving continues for already-resolved tags until their cache entry expires; the log stream buffers. |
| Object storage is down | Serving continues (weights are on the runtime's local cache); uploads and capture fail. |
| A model's config is wrong | Caught at validation, before publish, as a visibly wrong overlay. |

---

## Why the pipeline lives in `core/`

Preprocessing, the decoders, mask encoding and overlay rendering are one package
imported by *both* the API and the runtime. The API needs them to validate a
config and render a preview; the runtime needs them to serve. If they were
duplicated, the playground would eventually disagree with production — which is
precisely the class of bug this platform exists to prevent.

The contract is `(outputs, config, geometry) → typed results`, pure and
numpy-only. That is why the decoders are testable without a model: `core/smoke_test.py`
drives all eleven of them with synthetic tensors, including the geometry
round-trip (letterbox padding undone, boxes back in original pixels).

### The geometry problem

Every detector's boxes come out in *model space* — 640×640 after a letterbox
that padded a 1280×720 image. Getting them back to original pixels is where
implementations silently disagree. `Geometry` carries `scale`, `pad_x`, `pad_y`
from the preprocessing step, and every decoder rescales through it. There is one
implementation of that arithmetic, and the synthetic tests assert the corners
land where they should.

---

## Multi-input models

Not every model takes only an image. The damage-group classifier this was
validated against takes the crop *and* the damage box geometry as a second
input. Those inputs are named on the request:

```bash
-F 'inputs={"aux": [0.5, 0.5, 0.5, 0.5]}'      # one row, used for every image
-F 'inputs={"aux": [[0.5,0.5,0.5,0.5], [0.2,0.1,0.3,0.4]]}'   # one row per image
```

They flow through the batcher alongside the image tensor and are concatenated on
the same batch axis, so a multi-input model still batches. Requests whose named
inputs differ in *shape* are not merged. Warm-up synthesises them (from
`config.extra.warmup_inputs`, else zeros of the declared shape) — otherwise a
multi-input model could never reach `ready`, since warm-up would fail before any
caller ever sent one.

---

## Workflows

A workflow is a DAG stored as one JSON document (`{nodes, edges}`) — the same
document the React Flow canvas edits and the engine executes. There is no
compilation step and no second representation to drift.

The execution model is one idea: **every node consumes and produces a list of
`Item`s**, where an item is one image plus its provenance (which source image,
which box, which class) and any named model inputs attached to it. Fan-out (one
photo → six crops) and fan-in (six crops → one summary) then fall out of the
same machinery, and a model node never needs to know whether it is looking at a
whole photo or a crop.

```
topological order ─▶ for each node:
                       input     → items from the request
                       model     → one batched call through the normal gateway path
                       crop      → one item per detection (this is the fan-out)
                       filter    → drop predictions
                       geometry  → attach each item's own box as a named input
                       merge     → concatenate branches
                       output    → collect into a named key, in either response shape
```

Three properties worth knowing:

* **Model nodes reuse the serving path.** Same tag resolution, same replica
  choice, same dynamic batching, same inference log. A workflow's model calls
  appear in the explorer next to direct calls, which is why a slow workflow can
  be traced down to a specific version on a specific runtime.
* **Tags stay live inside a graph.** A node pointing at `production` follows
  your rollbacks without republishing the workflow.
* **Fan-out is capped** (64 items per node). A crop node with a bad threshold
  should fail loudly, not quietly enqueue a thousand forward passes.

Crops are decoded and re-encoded in the API process. That is a deliberate
trade — it keeps the runtime protocol to "here are images" — and it is why the
cap exists and why a large fan-out belongs in an async job.

## Data model

```
users ─┬─ personal_access_tokens        (instance role: admin | developer | viewer)
       └─ workspace_members ── workspaces   (a role per workspace; last_workspace_id on users)
instance_settings                      (one row: capture policy overrides)

workspaces ─┬─ workflows
            ├─ api_keys                (optionally scoped to its models)
            └─ models ─┬─ model_versions ─┬─ deployments ── assignments ── runtimes
                       │                  └─ drift_windows
                       └─ tags ──────────────▶ (points at one version)

Rows that hang off a model (versions, deployments, inference logs, drift,
uploads, jobs) follow its workspace through `model_id`; `app/scope.py` holds the filter.

inference_logs   append-only, indexed on (model, created_at),
                 (version, created_at), (status, created_at), total_ms
audit_logs       append-only
jobs             async work + progress
workflows        the graph (draft) + published_graph + revision
workflow_runs    one row per execution, with per-node timings
alert_rules / alert_events / report_schedules
upload_sessions  in-flight multipart uploads
```

Two deliberate choices:

* **Weights never live in Postgres.** `model_versions.weights_key` points into
  object storage. Runtimes pull from there directly with a presigned URL; multi-GB
  files never pass through the API.
* **`inference_logs` is written by exactly one process.** That is what makes it
  cheap to move: point the log sink at ClickHouse and nothing else changes.

---

## What runs on a timer

| Job | Cadence | Why |
|---|---|---|
| `reconcile_tick` | 15 s | converge the fleet even with no API traffic |
| `autoscale_tick` | 30 s | move replicas to hold the target queue depth |
| `evaluate_alerts` | 1 min | alert rules |
| `report_scheduler` | 1 min | fire report schedules whose cron matches |
| `drift_rollup` | hourly | aggregate the previous hour per version |
| `retention_sweep` | hourly | hard-delete expired captures, abort stale uploads |

All of them are arq cron jobs in the worker. None of them touch the request path.

---

## Testing

| Suite | What it proves | How |
|---|---|---|
| `core/smoke_test.py` | every decoder produces the right typed output, geometry round-trips, masks encode and decode, overlays render | synthetic tensors, no model needed |
| `runtime/smoke_test.py` | a real ONNX model serves, requests from different callers batch together, a full queue refuses rather than piles up, deadlines are honoured, validation renders an overlay | a small ONNX graph built in the test |
| `backend/tests/` | the control plane's auth plumbing: a personal access token acts as its owner, stops at revocation and expiry, dies with a deactivated owner, cannot mint another token, is not an API key, and is attributed in the audit log | the FastAPI app over a per-test SQLite file, no Redis or MinIO |
| `mcp/tests/` | the agent tools: what each one sends, that responses are trimmed and paged, that overlays and captures come back as images, and that an API error becomes one actionable line | a fake `/v1` behind `httpx.MockTransport` |
| `scripts/e2e.py` | the whole product, against real containers: chunked upload, checksum, validation, publish, deploy, predict, tag, retag, rollback, key scoping, rate limits, personal access tokens, the MCP server in-process, logging, audit, reports | `make e2e` |

The end-to-end suite asserts the *guarantees*, not just the happy path: that an
unvalidated version cannot be published, that a published config cannot be
edited, that a tag still resolves to the old version until it is moved, that a
revoked key stops working immediately, and that a request which failed before
reaching a runtime still lands in the log.
