"""End-to-end runtime checks against a real ONNX graph. `uv run --with onnx python smoke_test.py`"""
import asyncio, base64, io, os, tempfile, time
import numpy as np
from PIL import Image

import onnx
from onnx import TensorProto, helper, numpy_helper


def build_yolo_onnx(path, nc=80, anchors=100, batch="N"):
    """A graph with a YOLOv8-shaped head: (N, 4+nc, anchors), dynamic batch unless `batch` pins it."""
    const = np.full((1, 4 + nc, anchors), 0.01, np.float32)
    const[0, 0, :3], const[0, 1, :3], const[0, 2, :3], const[0, 3, :3] = 320, 240, 120, 90
    const[0, 4 + 2, :3] = 0.9  # class 2 = car
    nodes = [
        helper.make_node("ReduceMean", ["images", "axes"], ["mean4"], keepdims=1),
        helper.make_node("Reshape", ["mean4", "shape"], ["mean"]),
        helper.make_node("Mul", ["mean", "zero"], ["zeroed"]),
        helper.make_node("Add", ["zeroed", "head"], ["output0"]),
    ]
    graph = helper.make_graph(
        nodes, "fake-yolo",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [batch, 3, 640, 640])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, [batch, 4 + nc, anchors])],
        [numpy_helper.from_array(np.array([1, 2, 3], np.int64), "axes"),
         numpy_helper.from_array(np.array([-1, 1, 1], np.int64), "shape"),
         numpy_helper.from_array(np.zeros((1, 1, 1), np.float32), "zero"),
         numpy_helper.from_array(const, "head")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    model.ir_version = 9
    onnx.checker.check_model(model)
    onnx.save(model, path)
    return path


async def main():
    os.environ.setdefault("AP_RUNTIME_INTERNAL_TOKEN", "test-token")
    os.environ["AP_CACHE_DIR"] = tempfile.mkdtemp(prefix="ap-cache-")
    import httpx
    from runtime import main as rt
    from runtime.batcher import Batcher
    from runtime.device import detect
    from runtime.store import Loaded, ModelStore
    from runtime.backends.onnx_backend import OnnxBackend
    from aperture_core import preset_config

    tmp = tempfile.mkdtemp(prefix="ap-onnx-")
    onnx_path = build_yolo_onnx(os.path.join(tmp, "fake-yolo.onnx"))

    device = detect()
    store = ModelStore(device)
    rt.state.update({"store": store, "control": None, "device": device,
                     "runtime_id": "runtime-test", "started_at": time.time()})

    cfg = preset_config("yolov11")
    backend = OnnxBackend(onnx_path, device="cpu")
    entry = Loaded(version_id="v-1", model_slug="part-detection", version=7, config=cfg,
                   backend=backend)
    entry.batcher = Batcher(version_id="v-1", backend=backend, semaphore=store._semaphore,
                            max_batch_size=8, max_wait_ms=25, max_queue_depth=16)
    await entry.batcher.start()
    entry.state = "ready"
    store.models["v-1"] = entry

    img = Image.fromarray((np.random.default_rng(3).random((480, 640, 3)) * 255).astype(np.uint8))
    buf = io.BytesIO(); img.save(buf, format="JPEG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    checks = []
    headers = {"x-runtime-token": os.environ["AP_RUNTIME_INTERNAL_TOKEN"]}
    transport = httpx.ASGITransport(app=rt.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://rt", timeout=60) as c:
        # 1 - single image inference
        r = await c.post("/v1/infer", headers=headers, json={
            "request_id": "r1", "version_id": "v-1", "images": [{"b64": b64, "name": "a.jpg"}],
            "want_overlay": True, "deadline_ms": 15000})
        body = r.json()
        dets = body.get("results", [{}])[0].get("detections", []) if r.status_code == 200 else []
        checks.append(("single image inference",
                       r.status_code == 200 and len(dets) == 1 and dets[0]["class_name"] == "car",
                       body if r.status_code != 200 else dets[0]))

        # 2 - latency breakdown present and coherent
        lat = body.get("latency", {})
        checks.append(("latency breakdown (R5.2)",
                       all(k in lat for k in ("queue_ms", "preprocess_ms", "inference_ms",
                                              "postprocess_ms", "runtime_ms"))
                       and lat["runtime_ms"] >= lat["inference_ms"], lat))

        # 3 - overlay returned
        checks.append(("overlay png returned",
                       len(body.get("overlays") or []) == 1
                       and base64.b64decode(body["overlays"][0])[:4] == b"\x89PNG", None))

        # 4 - input stats for drift
        st = (body.get("stats") or [{}])[0]
        checks.append(("input stats captured (R5.9)",
                       st.get("width") == 640 and "brightness" in st, st))

        # 5 - batch of 4 in one request (R3.3)
        r = await c.post("/v1/infer", headers=headers, json={
            "request_id": "r2", "version_id": "v-1",
            "images": [{"b64": b64, "name": f"{i}.jpg"} for i in range(4)], "deadline_ms": 15000})
        body2 = r.json()
        checks.append(("batch of 4 images in one request",
                       r.status_code == 200 and len(body2["results"]) == 4
                       and body2["batch_executed"] == 4, body2.get("batch_executed")))

        # 6 - threshold override (R3.6) does not mutate the deployed config
        r = await c.post("/v1/infer", headers=headers, json={
            "request_id": "r3", "version_id": "v-1", "images": [{"b64": b64}],
            "overrides": {"score_threshold": 0.99}, "deadline_ms": 15000})
        ovr = r.json()
        empty = ovr.get("results", [{}])[0].get("detections", []) if r.status_code == 200 else ["?"]
        checks.append(("per-request threshold override",
                       r.status_code == 200 and len(empty) == 0
                       and entry.config.postprocess.score_threshold == 0.25,
                       None if r.status_code == 200 else ovr))

        # 7 - cross-request dynamic batching (R4.2)
        before = entry.batcher.batches
        results = await asyncio.gather(*[
            c.post("/v1/infer", headers=headers, json={
                "request_id": f"c{i}", "version_id": "v-1", "images": [{"b64": b64}],
                "deadline_ms": 15000}) for i in range(6)])
        executed = [x.json().get("batch_executed") for x in results]
        checks.append(("requests batched together across callers",
                       all(x.status_code == 200 for x in results) and max(executed) > 1,
                       f"batch sizes executed: {executed}, batches={entry.batcher.batches - before}"))

        # 8 - auth is enforced
        r = await c.post("/v1/infer", json={"request_id": "x", "version_id": "v-1",
                                            "images": [{"b64": b64}]})
        checks.append(("internal token enforced", r.status_code == 401, r.status_code))

        # 9 - unknown version is a clean 503, not a 500
        r = await c.post("/v1/infer", headers=headers, json={
            "request_id": "x", "version_id": "nope", "images": [{"b64": b64}]})
        checks.append(("unknown version -> no_replica_available",
                       r.status_code == 503
                       and r.json()["error"]["code"] == "no_replica_available", r.json()))

        # 10 - a corrupt image is a 400 with a machine-readable code
        r = await c.post("/v1/infer", headers=headers, json={
            "request_id": "x", "version_id": "v-1",
            "images": [{"b64": base64.b64encode(b"not-an-image").decode()}]})
        checks.append(("corrupt image -> invalid_image",
                       r.status_code == 400 and r.json()["error"]["code"] == "invalid_image",
                       r.json()["error"]["code"] if r.status_code == 400 else r.status_code))

        # 11 - backpressure: a full queue refuses rather than piling up (R4.3)
        slow = Batcher(version_id="v-slow", backend=_SlowBackend(backend, 0.35),
                       semaphore=asyncio.Semaphore(1), max_batch_size=1, max_wait_ms=0,
                       max_queue_depth=2)
        await slow.start()
        slow_entry = Loaded(version_id="v-slow", model_slug="slow", version=1, config=cfg,
                            backend=slow.backend, batcher=slow, state="ready")
        store.models["v-slow"] = slow_entry
        rejected = await asyncio.gather(*[
            c.post("/v1/infer", headers=headers, json={
                "request_id": f"q{i}", "version_id": "v-slow", "images": [{"b64": b64}],
                "deadline_ms": 20000}) for i in range(10)])
        codes = [x.status_code for x in rejected]
        queue_full = [x.json()["error"]["code"] for x in rejected if x.status_code == 503]
        checks.append(("full queue returns queue_full, not a timeout pileup",
                       503 in codes and all(c == "queue_full" for c in queue_full),
                       f"statuses={sorted(set(codes))}"))

        # 12 - deadline is honoured
        r = await c.post("/v1/infer", headers=headers, json={
            "request_id": "d1", "version_id": "v-slow", "images": [{"b64": b64}],
            "deadline_ms": 50})
        checks.append(("per-request deadline honoured (R3.7)",
                       r.status_code in (504, 503), f"{r.status_code} {r.json()['error']['code']}"))

        # 13 - readyz reports servable versions
        r = await c.get("/readyz")
        checks.append(("readyz lists ready versions",
                       r.status_code == 200 and "v-1" in r.json()["versions_ready"], r.json()))

        # 14 - prometheus metrics exposed
        r = await c.get("/metrics")
        checks.append(("prometheus metrics exposed",
                       "aperture_runtime_requests_total" in r.text
                       and "aperture_runtime_latency_seconds" in r.text, None))

        # 15 - validate endpoint runs the whole pipeline and renders an overlay (R1.7)
        store._fetch_weights = lambda assignment: _resolved(onnx_path)
        r = await c.post("/v1/validate", headers=headers, json={
            "version_id": "cand", "weights_bucket": "b", "weights_key": "k",
            "artifact_format": "onnx", "config": cfg.model_dump(mode="json"),
            "image_b64": b64, "want_overlay": True})
        v = r.json()
        checks.append(("validation runs and returns an overlay + io info",
                       v.get("ok") and v["io"]["class_columns"] == 80
                       and v["overlay_b64"] and v["summary"]["count"] == 1,
                       {k: v.get(k) for k in ("ok", "errors")} if not v.get("ok") else
                       f"class_columns={v['io']['class_columns']}, timings={v['timings']}"))

        # 16 - validation catches a class-list mismatch as a warning, not a crash
        bad = cfg.model_copy(deep=True)
        bad.classes = ["only", "three", "classes"]
        r = await c.post("/v1/validate", headers=headers, json={
            "version_id": "cand2", "weights_bucket": "b", "weights_key": "k",
            "artifact_format": "onnx", "config": bad.model_dump(mode="json"),
            "image_b64": b64, "want_overlay": False})
        v2 = r.json()
        checks.append(("wrong class count still validates and reports columns",
                       v2.get("ok") and v2["io"]["class_columns"] == 80, v2.get("errors")))

        # 17 - a graph pinned to batch 1 never gets a coalesced or multi-image tensor
        static_backend = OnnxBackend(build_yolo_onnx(os.path.join(tmp, "static.onnx"), batch=1),
                                     device="cpu")
        static = Loaded(version_id="v-static", model_slug="static", version=1, config=cfg,
                        backend=static_backend)
        static.batcher = Batcher(version_id="v-static", backend=static_backend,
                                 semaphore=store._semaphore, max_batch_size=8, max_wait_ms=25,
                                 max_queue_depth=16)
        static.batcher.reconfigure({"max_batch_size": 8})
        await static.batcher.start()
        static.state = "ready"
        store.models["v-static"] = static
        responses = await asyncio.gather(*[
            c.post("/v1/infer", headers=headers, json={
                "request_id": f"s{i}", "version_id": "v-static",
                "images": [{"b64": b64, "name": f"{j}.jpg"} for j in range(1 + i % 3)],
                "deadline_ms": 15000}) for i in range(6)])
        counts = [len(x.json().get("results", [])) if x.status_code == 200 else x.text[:120]
                  for x in responses]
        checks.append(("static batch-1 graph serves concurrent and multi-image requests",
                       all(x.status_code == 200 for x in responses)
                       and counts == [1 + i % 3 for i in range(6)]
                       and static.batcher.max_batch_size == 1, counts))

        # 18 - a version that does not fit the memory budget is refused, not loaded
        from runtime.memory import rss_mb
        from runtime.settings import settings as rt_settings

        def _assign(version_id: str) -> Loaded:
            loaded = Loaded(version_id=version_id, model_slug="big", version=1, config=cfg,
                            assignment={"weights_key": "k", "weights_bucket": "b",
                                        "weights_sha256": f"sha-{version_id}",
                                        "artifact_format": "onnx"})
            store.models[version_id] = loaded
            return loaded

        store._fetch_weights = lambda assignment: _resolved(onnx_path)
        rt_settings.memory_budget_mb = rss_mb() + 10  # 10 MB of headroom
        rt_settings.memory_min_version_mb = 512        # and a version that wants 512
        refused = _assign("v-big")
        await store._load("v-big")
        r = await c.get("/readyz")
        checks.append(("over-budget version is refused with the reason",
                       refused.state == "failed" and refused.backend is None
                       and refused.detail.startswith("insufficient memory: needs ~512 MB")
                       and "v-big" in r.json()["versions_failed"]
                       and r.json()["memory"]["budget_mb"] == rt_settings.memory_budget_mb,
                       refused.detail))

        # 19 - with room it loads, and what it cost is measured and remembered
        rt_settings.memory_budget_mb = rss_mb() + 4096
        rt_settings.memory_min_version_mb = 64
        admitted = _assign("v-fits")
        await store._load("v-fits")
        checks.append(("a version that fits loads and its cost is recorded",
                       admitted.state == "ready" and admitted.memory_mb >= 1
                       and store.ledger.get("sha-v-fits") == admitted.memory_mb
                       and admitted.report()["needs_mb"] == 64,
                       {"state": admitted.state, "memory_mb": admitted.memory_mb,
                        "needs_mb": admitted.needs_mb, "detail": admitted.detail}))
        rt_settings.memory_budget_mb = 0

        # 20 - a burst of large images over the request headroom gets queue_full,
        # not an OOM kill, and every reservation is handed back afterwards
        big = Image.fromarray((np.random.default_rng(5).random((1500, 2000, 3)) * 255)
                              .astype(np.uint8))
        bbuf = io.BytesIO(); big.save(bbuf, format="JPEG")
        big_b64 = base64.b64encode(bbuf.getvalue()).decode()
        burst = Batcher(version_id="v-burst", backend=_SlowBackend(backend, 0.3),
                        semaphore=asyncio.Semaphore(1), max_batch_size=1, max_wait_ms=0,
                        max_queue_depth=32)
        await burst.start()
        store.models["v-burst"] = Loaded(version_id="v-burst", model_slug="burst", version=1,
                                         config=cfg, backend=burst.backend, batcher=burst,
                                         state="ready")
        per_request = rt.request_need_mb([(2000, 1500)])  # ~51 MB at the default factor
        rt_settings.memory_budget_mb = rss_mb()  # resident versions have used their budget
        rt_settings.request_memory_ceiling_mb = rss_mb() + per_request * 2 + per_request // 2
        replies = await asyncio.gather(*[
            c.post("/v1/infer", headers=headers, json={
                "request_id": f"m{i}", "version_id": "v-burst",
                "images": [{"b64": big_b64}], "deadline_ms": 30000}) for i in range(8)])
        statuses = sorted(r.status_code for r in replies)
        refused = [r.json()["error"] for r in replies if r.status_code == 503]
        checks.append(("a burst over the request headroom gets queue_full and releases memory",
                       statuses.count(200) >= 1 and len(refused) >= 1
                       and all(e["code"] == "queue_full" and e["details"]["needs_mb"]
                               == per_request for e in refused)
                       and store.requests.reserved_mb == 0 and store.requests.in_flight == 0
                       and set(statuses) <= {200, 503},
                       {"statuses": statuses, "reserved_after": store.requests.reserved_mb}))

        # 21 - one request that could never fit is refused as too large, not retried forever
        rt_settings.memory_budget_mb = rss_mb()
        rt_settings.request_memory_ceiling_mb = rss_mb() + per_request // 2
        r = await c.post("/v1/infer", headers=headers, json={
            "request_id": "m-huge", "version_id": "v-burst", "images": [{"b64": big_b64}],
            "deadline_ms": 30000})
        checks.append(("an image that can never fit is refused as payload_too_large",
                       r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
                       and store.requests.reserved_mb == 0,
                       r.json()["error"]["code"] if r.status_code != 200 else 200))
        rt_settings.memory_budget_mb = 0
        rt_settings.request_memory_ceiling_mb = 0
        await burst.drain()

        # 22 - activation memory is reserved only past what the version's arena already held
        from runtime.memory import Admission
        gate = Admission()
        gate.warmed("v-act", 2)                           # warmed at batch 2
        rt_settings.request_memory_ceiling_mb = rss_mb() + 500
        t1, need1, _ = gate.try_reserve("v-act", 1, 5, 300)  # 1 image: inside the arena
        t2, need2, _ = gate.try_reserve("v-act", 1, 5, 300)  # 2 images: still inside
        t3, need3, _ = gate.try_reserve("v-act", 1, 5, 300)  # 3rd image grows the arena
        t4, need4, _ = gate.try_reserve("v-act", 1, 5, 300)  # 4th would pass the ceiling
        for t in (t1, t2, t3):
            gate.release(t)
        t5, need5, _ = gate.try_reserve("v-act", 3, 5, 300)  # 3 at once now fit the arena
        gate.release(t5)
        checks.append(("activation memory is reserved only beyond the arena's high-water mark",
                       (need1, need2, need3) == (5, 5, 305) and t4 is None and need4 == 305
                       and need5 == 5 and gate.reserved_mb == 0,
                       {"needs": [need1, need2, need3, need4, need5]}))
        rt_settings.request_memory_ceiling_mb = 0

        await slow.drain()
        await entry.batcher.drain()
        await static.batcher.drain()
        await admitted.batcher.drain()

    failed = 0
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   -> {detail}" if detail is not None else ""))
        failed += 0 if ok else 1
    print(f"\n{len(checks)-failed}/{len(checks)} passed")
    raise SystemExit(1 if failed else 0)


class _SlowBackend:
    def __init__(self, inner, delay):
        self.inner, self.delay, self.io = inner, delay, inner.io
    def infer(self, batch, extra_inputs=None):
        time.sleep(self.delay)
        return self.inner.infer(batch, extra_inputs)
    def close(self):
        pass


async def _resolved(path):
    return path


def _resolved(path):  # noqa: F811 - store._fetch_weights is awaited, so return a coroutine
    async def inner():
        return path
    return inner()


if __name__ == "__main__":
    asyncio.run(main())
