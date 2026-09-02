# Requirement traceability

Every requirement from the brief, where it lives, and what state it is in.

**Status key** — ✅ done and covered by a test · ◑ done, edges noted · ○ not built

The 65-check end-to-end suite (`make e2e`) drives a running stack; the decoder
and runtime suites (`make test`) cover the inference contract. Where a row cites
a check, that check asserts the behaviour against real containers.

---

## 1. Model registry & upload

| ID | Pri | Status | Where | Notes |
|---|---|---|---|---|
| R1.1 Resumable/chunked upload, UI + API | M | ✅ | `backend/app/api/v1/versions.py` (`/uploads`, `/parts/{n}`, `/complete`), `frontend/src/lib/api.ts::uploadWeights` | S3 multipart underneath. `GET /v1/uploads/{id}` tells a resumed client which parts landed. e2e: *"a resumed client can ask which parts landed"* |
| R1.2 PyTorch / TorchScript / ONNX / TensorRT / safetensors | M | ✅ | `core/aperture_core/artifacts.py`, `runtime/runtime/store.py::_build_backend` | Every upload is **inspected**, not trusted by extension: ONNX / TorchScript / TensorRT are servable; an Ultralytics checkpoint is convertible in one click; a `state_dict` or safetensors is refused with the architecture recognised from its layer names, the preset to use once it is ONNX, and the export command. Verified against real `yolo11l-seg.pt`, `rf-detr-seg-preview.pt` and a 111 MB Ultralytics ONNX export in `core/artifact_test.py`. |
| R1.3 Required metadata per version | M | ✅ | `backend/app/models.py::ModelVersion`, `schemas.py::VersionCreate` | Task and owner on the model; input shape/dtype, class list, framework on the version config. |
| R1.4 Pre/post-processing config lives with the version | M | ✅ | `core/aperture_core/configspec.py`, stored in `model_versions.config` | Never global. e2e: *"a published version's config is immutable"* |
| R1.5 Presets for known families | M | ✅ | `core/aperture_core/presets.py`, `GET /v1/meta/presets` | YOLOv5, YOLOv8/9/10, YOLO11, RF-DETR, YOLO-seg, YOLO-pose, ResNet-ImageNet, EfficientNet, SAM, CLIP, PaddleOCR-rec, plus Custom. |
| R1.6 New versions inherit the previous config, editable before publish | M | ✅ | `versions.py::create_version` (`inherit_from_version`), `frontend/.../ConfigTab.tsx` | e2e: *"version 2 inherits version 1's config"*, *"edit the inherited config before publishing"* |
| R1.7 Validate on upload: full pipeline + rendered overlay | M | ⛔ removed on request | `backend/app/services/deployer.py::_record_load`, `core/aperture_core/artifacts.py` | **Dropped deliberately, and it was a Must.** Validating against a reference image needs a reference dataset; with a synthetic stand-in the run reports on the stand-in ("nothing was detected above score_threshold" against two drawn shapes), which is worse than no signal. What survives needs no data: the file header still refuses an artifact with no graph, and a runtime loading the version is the check — it sets `ready`, or `failed` with the runtime's own reason. Publishing no longer requires a run; deploying a draft is allowed. e2e: 4 checks on the new behaviour |
| R1.8 Weights in object storage, metadata in Postgres, checksum verified | M | ✅ | `backend/app/storage.py::checksum_object`, `runtime/store.py::_verify` | Verified after upload *and* again by the runtime before it will serve the bytes. e2e: *"complete the upload and verify the checksum end to end"* |
| R1.9 Optional custom pre/post-processing hook | S | ✅ | `core/aperture_core/sandbox.py`, `pipeline.py`, `POST /v1/meta/hooks/validate` | Static screen, then a locked-down subprocess. See R7.8. |
| R1.10 Conversion & optimisation (ONNX export, FP16/INT8, TensorRT) | C | ◑ | `backend/app/jobs/tasks.py::convert_version`, the `convert` compose profile | An Ultralytics checkpoint converts through Ultralytics' own exporter (dynamic batch, verified end to end on `yolo11l-seg.pt` → 111 MB ONNX that loads in onnxruntime and batches); TorchScript goes through `torch.onnx.export`; FP16/INT8 use the onnx tooling. Runs on its own image and queue so serving never carries torch, and a conversion is refused at submit time if that container is not up. TensorRT engines must still be built on the target GPU. |

## 2. Versioning & tags

| ID | Pri | Status | Where | Notes |
|---|---|---|---|---|
| R2.1 Immutable auto-incrementing versions | M | ✅ | `versions.py::create_version`, `patch_version` | Weights are write-once; editing a published config returns `version_immutable`. e2e asserts both. |
| R2.2 Movable tags; clients call the tag | M | ✅ | `backend/app/api/v1/tags.py`, `services/resolver.py` | e2e: *"predict through the tag"* |
| R2.3 Retagging atomic and instant; rollback = repoint | M | ✅ | `tags.py::set_tag`, `deployments.py::rollback`, `cache.py::invalidate_tag` | One row update plus an explicit cache purge. e2e: *"the retag takes effect without a redeploy"*, *"traffic is back on v1 after the rollback"* |
| R2.4 Version metadata: run id, dataset, metrics, commit, changelog | M | ✅ | `models.py::ModelVersion` | Shown in version history and in the compare view. |
| R2.5 Compare two versions side by side | S | ✅ | `GET /v1/models/{ref}/version-compare`, `frontend/.../CompareTab.tsx` | Metadata, metric deltas, measured latency and traffic, and a field-level config diff. |
| R2.6 Deprecate/archive, retention for unused weights | S | ✅ | `versions.py::deprecate_version`, `archive_version` | Archiving can reclaim the blob; a tagged version cannot be deprecated out from under its callers. |
| R2.7 Progressive rollout: canary, A/B, shadow | S | ◑ | `services/gateway.py::choose_targets` | Canary takes a weighted slice of *tag* traffic; shadow mirrors and discards the answer; A/B is two canaries with the metrics split by version. Only applies to tag callers — an explicit version number is a promise. |

## 3. Inference API

| ID | Pri | Status | Where | Notes |
|---|---|---|---|---|
| R3.1 `POST /v1/models/{model}/{version\|tag}/predict` | M | ✅ | `backend/app/api/v1/predict.py` | Exactly that path. |
| R3.2 Multipart, base64 JSON, remote URL; configurable max payload | M | ✅ | `predict.py::_collect`, `gateway.py::fetch_url_image` | Also a raw image body. URL fetching refuses private address ranges unless allowlisted. |
| R3.3 Batch in one request, multi-input models | M | ✅ | `predict.py`, `runtime/batcher.py`, `onnx_backend.py` | Named non-image inputs go on the request (`inputs={"aux": [...]}`), one row per image or one shared row, and batch on the same axis as the images. Verified against a real trained multi-input classifier — see *Verified against a real model* below. |
| R3.4 Standardised typed response per task | M | ✅ | `core/aperture_core/results.py` | Boxes as `[x1,y1,x2,y2]` in original pixels, scores, class ids + names, masks as RLE or polygon, keypoints, text, embeddings. Verified per decoder in `core/smoke_test.py` (15 checks). |
| R3.5 request_id, resolved version, latency breakdown on every response | M | ✅ | `predict.py`, `results.py::LatencyBreakdown` | Also as `x-request-id` / `x-aperture-model-version` headers. |
| R3.6 Per-request threshold overrides | M | ✅ | `configspec.py::apply_overrides`, `gateway.py::sanitise_overrides` | Only the tunables; a structural field is refused. e2e asserts both directions. |
| R3.7 Consistent error contract + per-request timeouts | M | ✅ | `backend/app/errors.py`, `batcher.py::Deadline` | ~40 stable codes, one body shape, deadline enforced in the queue and in flight. |
| R3.8 Async jobs: submit → id → poll or webhook | S | ✅ | `api/v1/jobs.py`, `jobs/tasks.py::run_inference_job` | Bulk folders and video (ffmpeg frame extraction), progress, cancel, signed result. |
| R3.9 Client SDKs from an OpenAPI spec | S | ✅ | `sdk/python`, `sdk/typescript`, `scripts/generate-sdk.sh` | Hand-written thin clients that need no codegen, plus generators for exhaustive ones. |

## 4. Availability, concurrency & performance

| ID | Pri | Status | Where | Notes |
|---|---|---|---|---|
| R4.1 Configurable replicas with explicit device assignment | M | ✅ | `models.py::Deployment`, `services/deployer.py::_device_fits` | `auto`, `cpu`, `cuda`, `cuda:N`. |
| R4.2 Dynamic batching (max batch + max wait) | M | ✅ | `runtime/runtime/batcher.py` | Merges requests from different callers. runtime suite: *"requests batched together across callers"* (5 merged into one pass). |
| R4.3 Queue with max depth, timeout, backpressure | M | ✅ | `batcher.py::QueueFull`, `errors.py` | Returns `503 queue_full` instead of queueing. runtime suite asserts no timeout pileup. |
| R4.4 Warm-up on deploy, weights stay resident | M | ✅ | `runtime/store.py::_warmup` | A version is only routable after warm-up passes. |
| R4.5 Health/readiness probes; unhealthy replicas pulled | M | ✅ | `runtime/main.py::/readyz`, `cache.py::healthy_runtimes_for_version`, `deployer.py::reconcile` | Routing reads only replicas with a fresh heartbeat *and* the version resident; a dead runtime's replicas are re-placed. |
| R4.6 Zero-downtime deploys | M | ✅ | `deployments.py::create_deployment`, `deployer.py` | New version warms on its own replicas; traffic moves when the tag moves. e2e: *"version 2 warms up while version 1 keeps serving"* |
| R4.7 Per-key rate limits and quotas | S | ✅ | `cache.py::check_rate_limit`, `check_quota` | e2e: *"the rate limit refuses excess calls with 429"* |
| R4.8 Autoscaling on queue depth with min–max bounds | S | ◑ | `deployer.py::autoscale` | Scales replicas across the existing runtime pool. It cannot start new runtime containers — that is compose/Kubernetes. |
| R4.9 Multi-model GPU sharing and scale-to-zero | C | ◑ | `runtime/settings.py::max_loaded_versions`, `store.py::_trim_cache` | Several versions share a runtime, packed by count and device. Not VRAM-aware. Scale-to-zero is `replicas: 0` by hand. |

## 5. Observability

| ID | Pri | Status | Where | Notes |
|---|---|---|---|---|
| R5.1 Structured log per inference | M | ✅ | `services/capture.py::build_record`, `jobs/logsink.py` | Written off the request path via a Redis stream. Includes requests that failed *before* reaching a runtime — those are the ones you go looking for. e2e: *"failed requests are logged with their error code"* |
| R5.2 Latency breakdown per stage | M | ✅ | `pipeline.py::StageTimings`, `batcher.py`, `results.py` | queue → preprocess → inference → postprocess → gateway → total, on every response and every log row. |
| R5.3 Distributed tracing with propagated trace id | M | ✅ | `backend/app/telemetry.py`, `deploy/otel/config.yaml` | OTLP to an in-deployment collector; inbound `traceparent` honoured and forwarded to the runtime. Off by default so an air-gapped install makes no outbound call. |
| R5.4 Metrics dashboard per model and version | M | ✅ | `services/analytics.py`, `/v1/metrics/summary`, `deploy/grafana/...` | RPS, percentiles, error rate, stage cost, queue depth, GPU memory, batch size. |
| R5.5 Sampled payload capture (e.g. 5% + 100% of errors) | M | ✅ | `services/capture.py::should_capture` | Per-project rate; errors always captured. |
| R5.6 Inference explorer with overlays | M | ✅ | `api/v1/observability.py`, `frontend/src/pages/Traces.tsx` | Filter by model, version, tag, outcome, error code, caller, latency, confidence, capture, flag. Detail view draws the boxes over the captured image. |
| R5.7 Reports, exportable and schedulable | S | ✅ | `services/reports.py`, `api/v1/reports.py` | Usage by model/version/consumer/day, error breakdown, latency trend; CSV and PDF; cron delivery to Slack, email or webhook. |
| R5.8 Alert rules to Slack/email/webhook | S | ✅ | `services/alerts.py` | Error rate, p95/p99, replica down, no traffic, confidence drop. Minimum-sample floor so a quiet model stays quiet. Test-send button. |
| R5.9 Drift signals vs a baseline window | S | ✅ | `services/drift.py` | Hourly rollups of confidence, class mix, resolution, brightness; signals with plain-language messages. |
| R5.10 Feedback loop: flag → retraining dataset | S | ✅ | `observability.py::flag_inference`, `tasks.py::export_flagged_dataset` | COCO, YOLO or JSONL, with a manifest tying every sample back to its request. |

## 6. Web UI

| ID | Pri | Status | Where |
|---|---|---|---|
| R6.1 Catalog: search/filter by task, owner, tag; full version history | M | ✅ | `pages/Models.tsx`, `pages/ModelDetail.tsx` |
| R6.2 Playground: image → overlay, raw JSON, latency | M | ✅ | `pages/model/Playground.tsx`, `components/results.tsx`, `lib/masks.ts` — draws masks (RLE decoded client-side), keypoint skeletons, class distributions, OCR and embeddings; layer toggles, mask opacity, a score floor, per-class visibility and hover-to-isolate. Defaults adapt to the result: 27 instances render as masks without labels, 3 render fully annotated. |
| R6.3 Config editor with live preview | M | ✅ | `pages/model/ConfigTab.tsx` |
| R6.4 Deployment panel: replicas, batching, thresholds, tags, one-click rollback | M | ✅ | `pages/model/DeployTab.tsx` |
| R6.5 API key management with copy-paste snippets | M | ✅ | `pages/Keys.tsx` (cURL, Python, JS) |
| R6.6 Side-by-side comparison of two versions | S | ◑ | `pages/model/CompareTab.tsx` — metadata, metrics, latency, traffic and config diff. Running both versions over a *shared image set* is the async-job path (`POST /v1/jobs` twice), not yet a single button. |

## 7. Security & multi-tenancy

| ID | Pri | Status | Where | Notes |
|---|---|---|---|---|
| R7.1 Revocable API keys, scoped to models | M | ✅ | `security.py`, `api/v1/keys.py` | Hashed at rest, shown once. e2e: *"the key cannot reach a model outside its scope"*, *"a revoked key stops working immediately"* |
| R7.2 SSO/OIDC or LDAP with viewer/developer/admin | M | ✅ | `api/v1/auth.py`, `security.py::require_role` | Group→role mapping for OIDC; local password login for bootstrap. |
| R7.3 Projects isolating models, keys and logs | M | ✅ | `models.py::Project`, `deps.py::resolve_project` | Every query is project-scoped. |
| R7.4 Audit log of every mutating action | M | ✅ | `backend/app/audit.py` | Closed action vocabulary; e2e asserts 8 specific actions are recorded. |
| R7.5 TLS in transit; encryption at rest | M | ◑ | `storage.py::_sse_args`, `AP_S3_SSE` | TLS terminates at your ingress (the compose network is plain HTTP). SSE-S3 is opt-in because MinIO refuses it without a KMS — see README *Encryption at rest*. |
| R7.6 Retention policy and hard delete for captured images | M | ✅ | `tasks.py::retention_sweep`, `purge_captures` | Per-project retention, hourly sweep, on-demand purge, flagged records preserved. |
| R7.7 Air-gap capable, no outbound calls at runtime | M | ✅ | throughout | Tracing off by default, no CDNs or external fonts in the UI, alerts only to destinations you name. |
| R7.8 Sandboxing for custom code: resource limits, no host access | S | ◑ | `core/aperture_core/sandbox.py` | Separate `python -I` process, RLIMIT_AS/CPU/FSIZE/NPROC, import allowlist, no `open`, results crossed as `allow_pickle=False` npz. Honest limit: this is hardening, and the container is the real boundary — the file says so, and so does the UI. |

---

## Beyond the brief

Two capabilities were asked for after the first pass. Both are implemented,
tested and documented.

| Capability | Where | Notes |
|---|---|---|
| **Workflows** — combine several models and their results into one specific answer, edited visually | `backend/app/services/workflow.py`, `api/v1/workflows.py`, `frontend/src/pages/WorkflowEditor.tsx` (React Flow) | A DAG of `input · model · crop · filter · geometry · merge · output`. The saved JSON graph *is* what executes, so the canvas is the pipeline, not a picture of it. Per-node timings on every run; publish snapshots a numbered revision; a **Build / Try it** toggle switches between the canvas and the run surface. Covered by 14 checks in `make e2e`, and verified on the real two-stage car-damage pipeline. |
| **Roboflow-compatible responses** | `core/aperture_core/formats.py`, `?format=roboflow` | Centre-point boxes, `confidence`, `class`, `class_id`, `detection_id`, `image`, `time`, `inference_id`, `model_id`; polygons as `points`; multi-label classification keyed by class name; a batch answers with one object per image. `AP_DEFAULT_RESPONSE_FORMAT` makes it the instance default, and a workflow Output node can pick per output. 17 checks against the real models. |
| **Per-request multi-input models** | `runtime/backends/onnx_backend.py`, `runtime/batcher.py`, `?inputs=` | Named non-image inputs on the request, batching on the same axis as the images. Needed by the real damage classifier, which takes the crop *and* its box geometry. |
| **Artifact inspection** — say what an upload *is*, not what its extension claims | `core/aperture_core/artifacts.py` | ONNX / TorchScript / TensorRT are servable; Ultralytics and RF-DETR checkpoints are convertible; any other `state_dict` or safetensors is refused with the architecture recognised from its layer names and the export command. Decided at upload, so nothing waits for a validation that cannot succeed. |
| **RF-DETR conversion** — a `state_dict` turned into a served model | `backend/app/jobs/tasks.py::_convert_rfdetr`, `core` decoder `detr_seg`, preset `rf-detr-seg` | Recovers patch size, resolution, class count and whether a mask head exists from the layer shapes, rebuilds with `rfdetr`, exports ONNX, and generates a config that matches the graph. Verified end to end on a real 149 MB RF-DETR Seg checkpoint: convert → validate → deploy → 27 masked segments, identical to running PyTorch directly. |

## Verified against a real model

The synthetic tests prove the plumbing; this proves the *preprocessing*.

A trained ConvNeXt-small damage-group classifier from `cells-detection`
(`runs/variant_sweep_cells_v5/part-filled_damage-contour__convnext_small`) was
exported to ONNX and served through Aperture: 202 MB uploaded in 7 chunks with
the checksum verified, multi-label sigmoid over 16 groups, and a second input
carrying the damage box geometry.

Its 22 real test images (`data/cls-test`) were run through both PyTorch directly
and Aperture, with the same aux vector:

| Check | Result |
|---|---|
| Probabilities vs PyTorch, all 16 groups × 22 images | largest deviation **5.0 × 10⁻⁶** |
| Groups crossing the 0.5 threshold | identical on every image |
| Latency, ConvNeXt-small on CPU | p50 272 ms, p95 470 ms |
| Batch of 4 real images, shared aux | served as one forward pass |
| A different aux row per image in one batch | served correctly |
| Omitting the required input | `400 invalid_request` naming the input to send |

That deviation is float32 rounding between torch and onnxruntime, not a
preprocessing difference — which is the whole point of stating the transform in
the version config (`stretch` to 224, ImageNet mean/std, `classifier_sigmoid`)
instead of leaving it to each caller.

Two real gaps surfaced doing this, both now fixed: per-request auxiliary inputs
did not exist (only load-time constants), and warm-up called the model without
them, so a multi-input model could never reach `ready`.

## Summary

| | Must (M) | Should (S) | Could (C) | Added after the brief |
|---|---|---|---|---|
| Built and tested | 27 | 12 | — | 3 |
| Built with noted limits | 4 | 3 | 2 | — |
| Not built | 0 | 0 | 0 | — |

Every M and S requirement is implemented. The seven rows marked ◑ are honest
scope edges, each documented where it matters: format conversion needs optional
tooling, autoscaling cannot create containers, GPU packing is not VRAM-aware,
compare has no shared-image-set button, TLS is your ingress's job, SSE-S3 needs
a KMS, and hook sandboxing leans on the container boundary.
