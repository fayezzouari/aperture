"""End-to-end check against a running Aperture stack.

Walks the path a real team walks - create a model, upload weights in chunks,
upload, publish, deploy, predict, tag, ship a second version, roll back - and
asserts the guarantees the requirements ask for along the way.

    uv run --with httpx --with onnx --with numpy --with pillow python scripts/e2e.py
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_onnx import build_classifier, build_detector, sample_image  # noqa: E402

BASE = os.environ.get("APERTURE_URL", "http://localhost:8080")
EMAIL = os.environ.get("AP_BOOTSTRAP_ADMIN_EMAIL", "admin@localhost")
PASSWORD = os.environ.get("AP_BOOTSTRAP_ADMIN_PASSWORD", "aperture")
SLUG = os.environ.get("E2E_MODEL", "e2e-part-detection")
CLASSES = ["bumper", "door", "fender", "headlight"]

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   -> {detail}" if detail else ""), flush=True)
    return ok


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=180.0)

    # ---------------------------------------------------------------- readiness
    for attempt in range(60):
        try:
            ready = client.get("/v1/meta/instance")
            if ready.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(2)
    else:
        return check("the API answers", False, f"{BASE} never became reachable") or 1
    check("the API answers", True, ready.json()["instance_name"])

    # -------------------------------------------------------------------- auth
    login = client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    if not check("sign in with the bootstrap admin", login.status_code == 200,
                 login.text[:200] if login.status_code != 200 else ""):
        return 1
    token = login.json()["access_token"]
    client.headers.update({"authorization": f"Bearer {token}", "x-aperture-project": "default"})

    bad = client.post("/v1/auth/login", json={"email": EMAIL, "password": "wrong-password"})
    check("a wrong password is refused with a typed error",
          bad.status_code == 401 and bad.json()["error"]["code"] == "invalid_credentials",
          bad.json().get("error", {}).get("code", ""))

    # ------------------------------------------------------------------- model
    # Aperture deliberately refuses to delete a model that is still serving, so
    # a re-run has to unwind the previous one in order: tags, then deployments,
    # then the model itself.
    reset_model(client, SLUG)
    reset_model(client, f"{SLUG}-other")
    reset_model(client, f"{SLUG}-severity")
    created = client.post("/v1/models", json={
        "slug": SLUG, "name": "E2E part detection", "task": "detection",
        "owner": "e2e", "description": "Created by the end-to-end check.",
    })
    if not check("create a model", created.status_code == 201, created.text[:300]):
        return 1

    duplicate = client.post("/v1/models", json={"slug": SLUG, "name": "dupe", "task": "detection"})
    check("a duplicate slug is refused", duplicate.status_code == 409
          and duplicate.json()["error"]["code"] == "slug_taken")

    # ---------------------------------------------------------------- artifacts
    workdir = tempfile.mkdtemp(prefix="aperture-e2e-")
    weights = build_detector(os.path.join(workdir, "detector-v1.onnx"), classes=len(CLASSES))
    image_path = sample_image(os.path.join(workdir, "sample.jpg"))
    with open(image_path, "rb") as fh:
        image_bytes = fh.read()

    # ----------------------------------------------------- version 1: upload
    version = client.post(f"/v1/models/{SLUG}/versions", json={
        "preset": "yolov11", "artifact_format": "onnx",
        "dataset_version": "Part-Detection-V2-10", "training_run_id": "e2e-run-1",
        "metrics": {"mAP50": 0.71, "f1": 0.68}, "changelog": "First version from the e2e check.",
    })
    if not check("create version 1 from the YOLO11 preset", version.status_code == 201,
                 version.text[:300]):
        return 1
    v1 = version.json()

    # fix the class list to match the synthetic head
    config = dict(v1["config"])
    config["classes"] = CLASSES
    patched = client.patch(f"/v1/models/{SLUG}/versions/1", json={"config": config})
    check("edit the inherited config before publishing", patched.status_code == 200,
          patched.text[:200] if patched.status_code != 200 else "")

    with open(weights, "rb") as fh:
        blob = fh.read()
    digest = hashlib.sha256(blob).hexdigest()

    session = client.post(f"/v1/models/{SLUG}/versions/1/uploads", json={
        "filename": "detector-v1.onnx", "total_bytes": len(blob), "sha256": digest,
        "chunk_bytes": 5 * 1024 * 1024,
    })
    if not check("open a chunked upload session", session.status_code == 201, session.text[:200]):
        return 1
    upload_id = session.json()["id"]

    part = client.put(f"/v1/uploads/{upload_id}/parts/1",
                      files={"chunk": ("detector-v1.onnx.part1", blob)})
    check("send a chunk", part.status_code == 200, part.text[:200] if part.status_code != 200 else "")

    status = client.get(f"/v1/uploads/{upload_id}")
    check("a resumed client can ask which parts landed",
          status.status_code == 200 and status.json()["received_parts"] == [1],
          str(status.json().get("received_parts")))

    complete = client.post(f"/v1/uploads/{upload_id}/complete")
    if not check("complete the upload and verify the checksum end to end",
                 complete.status_code == 200 and complete.json()["weights_sha256"] == digest,
                 complete.text[:300] if complete.status_code != 200 else digest[:16]):
        return 1

    # -------------------------------------------------------- the load is the check
    # There is no validation step to pass: checking a model against a reference
    # image needs a dataset, and a synthetic stand-in only judges the stand-in. A
    # version is proven by a runtime loading it, which happens on deploy.
    published = client.post(f"/v1/models/{SLUG}/versions/1/publish")
    check("a draft publishes without any run against a sample image",
          published.status_code == 200 and published.json()["status"] == "ready",
          published.text[:160])

    deployed_first = client.post(f"/v1/models/{SLUG}/deployments", json={"version": 1, "replicas": 1})
    check("a version can be deployed straight away",
          deployed_first.status_code == 201, deployed_first.text[:160])

    loaded = {}
    for attempt in range(20):
        loaded = client.get(f"/v1/models/{SLUG}/versions/1").json()
        if (loaded.get("validation") or {}).get("source") == "runtime-load":
            break
        time.sleep(3)  # the replica is pulling weights and warming up
    check("the runtime's load is what marks the version checked",
          (loaded.get("validation") or {}).get("status") == "passed"
          and (loaded.get("validation") or {}).get("source") == "runtime-load",
          str(loaded.get("validation"))[:160])
    check("nothing invented a prediction count from a stand-in image",
          "summary" not in (loaded.get("validation") or {})
          and "overlay_key" not in (loaded.get("validation") or {}),
          str(sorted((loaded.get("validation") or {}).keys())))

    # that deployment existed only to prove the load; the rest of the run starts
    # from nothing deployed, as a fresh model would
    for deployment in client.get(f"/v1/models/{SLUG}/deployments").json() or []:
        client.post(f"/v1/deployments/{deployment['id']}/stop")
    client.post("/v1/fleet/reconcile")
    time.sleep(2)

    frozen = client.patch(f"/v1/models/{SLUG}/versions/1", json={"config": config})
    check("a published version's config is immutable",
          frozen.status_code == 409 and frozen.json()["error"]["code"] == "version_immutable")

    # ---------------------------------------------------------------- deploy
    undeployed = client.post(f"/v1/models/{SLUG}/1/predict", files={"images": ("a.jpg", image_bytes)})
    check("calling an undeployed version returns model_not_deployed",
          undeployed.status_code == 503
          and undeployed.json()["error"]["code"] == "model_not_deployed",
          undeployed.json().get("error", {}).get("code", ""))

    deployed = client.post(f"/v1/models/{SLUG}/deployments", json={
        "version": 1, "replicas": 1, "max_batch_size": 8, "max_wait_ms": 15,
        "max_queue_depth": 32, "request_timeout_ms": 20000,
    })
    if not check("deploy version 1", deployed.status_code == 201, deployed.text[:300]):
        return 1

    live = wait_for_live(client, SLUG, 1)
    if not check("the replica warms up and reports ready", live, "waited 120 s"):
        return 1

    # --------------------------------------------------------------- predict
    predicted = client.post(f"/v1/models/{SLUG}/1/predict",
                            files={"images": ("sample.jpg", image_bytes, "image/jpeg")})
    if not check("predict on version 1", predicted.status_code == 200, predicted.text[:300]):
        return 1
    payload = predicted.json()
    detections = payload["results"][0]["detections"]
    check("the response carries typed detections with class names",
          len(detections) == 3 and detections[0]["class_name"] in CLASSES,
          f"{len(detections)} detections, top={detections[0]['class_name']} {detections[0]['score']}")
    check("boxes are mapped back to original image pixels",
          all(0 <= d["box"][0] < d["box"][2] <= payload["results"][0]["width"] for d in detections),
          f"first box {detections[0]['box']} in {payload['results'][0]['width']}x{payload['results'][0]['height']}")
    check("the response names the version that served it", payload["model_version"] == 1)
    check("the response carries a request id and a latency breakdown",
          bool(payload["request_id"]) and payload["latency"]["total_ms"] > 0
          and "inference_ms" in payload["latency"],
          str(payload["latency"]))
    check("the response echoes the request id header",
          predicted.headers.get("x-request-id") == payload["request_id"])

    # batch + base64 + overrides
    batched = client.post(f"/v1/models/{SLUG}/1/predict", files=[
        ("images", ("a.jpg", image_bytes, "image/jpeg")),
        ("images", ("b.jpg", image_bytes, "image/jpeg")),
        ("images", ("c.jpg", image_bytes, "image/jpeg")),
    ])
    check("batch inference in one request",
          batched.status_code == 200 and len(batched.json()["results"]) == 3,
          f"batch_size={batched.json().get('batch_size')}")

    as_json = client.post(f"/v1/models/{SLUG}/1/predict", json={
        "image": base64.b64encode(image_bytes).decode(), "include_overlay": True,
    })
    overlay_ok = as_json.status_code == 200 and (as_json.json().get("overlays") or [])
    check("base64 JSON input, with a rendered overlay returned",
          bool(overlay_ok) and base64.b64decode(as_json.json()["overlays"][0])[:4] == b"\x89PNG")

    strict = client.post(f"/v1/models/{SLUG}/1/predict",
                         files={"images": ("a.jpg", image_bytes)},
                         data={"overrides": '{"score_threshold": 0.99}'})
    check("a per-request threshold override changes only this request",
          strict.status_code == 200 and len(strict.json()["results"][0].get("detections", [])) == 0)

    rejected = client.post(f"/v1/models/{SLUG}/1/predict",
                           files={"images": ("a.jpg", image_bytes)},
                           data={"overrides": '{"input_width": 320}'})
    check("a non-tunable field cannot be overridden per request",
          rejected.status_code == 400 and rejected.json()["error"]["code"] == "invalid_request")

    broken = client.post(f"/v1/models/{SLUG}/1/predict", files={"images": ("a.jpg", b"not-an-image")})
    check("a corrupt image returns invalid_image, not a 500",
          broken.status_code == 400 and broken.json()["error"]["code"] == "invalid_image")

    # ------------------------------------------------- roboflow-shaped responses
    rf = client.post(f"/v1/models/{SLUG}/1/predict", params={"format": "roboflow"},
                     files={"images": ("sample.jpg", image_bytes, "image/jpeg")})
    check("a Roboflow-shaped response is available", rf.status_code == 200
          and {"inference_id", "time", "image", "predictions"} <= set(rf.json()),
          str(sorted(rf.json().keys())) if rf.status_code == 200 else rf.text[:200])
    if rf.status_code == 200:
        rf_first = rf.json()["predictions"][0]
        native_box = detections[0]["box"]
        check("its x/y are the box centre and width/height the full extent",
              abs(rf_first["x"] - (native_box[0] + native_box[2]) / 2) < 0.2
              and abs(rf_first["width"] - (native_box[2] - native_box[0])) < 0.2,
              f"x={rf_first['x']} y={rf_first['y']} w={rf_first['width']} h={rf_first['height']}")
        check("it uses Roboflow's field names",
              {"confidence", "class", "class_id", "detection_id"} <= set(rf_first),
              str(sorted(rf_first.keys())))
    rf_batch = client.post(f"/v1/models/{SLUG}/1/predict", params={"format": "roboflow"}, files=[
        ("images", ("a.jpg", image_bytes, "image/jpeg")),
        ("images", ("b.jpg", image_bytes, "image/jpeg")),
    ])
    check("a Roboflow batch answers with one object per image",
          rf_batch.status_code == 200 and isinstance(rf_batch.json(), list)
          and len(rf_batch.json()) == 2)
    bad_format = client.post(f"/v1/models/{SLUG}/1/predict", params={"format": "nonsense"},
                             files={"images": ("a.jpg", image_bytes)})
    check("an unknown format is refused, not guessed",
          bad_format.status_code == 400
          and bad_format.json()["error"]["code"] == "invalid_request")

    # ------------------------------------------------------------------ tags
    tagged = client.put(f"/v1/models/{SLUG}/tags", json={"name": "production", "version": 1})
    check("point 'production' at version 1", tagged.status_code == 200)

    via_tag = client.post(f"/v1/models/{SLUG}/production/predict",
                          files={"images": ("a.jpg", image_bytes)})
    check("predict through the tag",
          via_tag.status_code == 200 and via_tag.json()["model_version"] == 1
          and via_tag.json()["tag"] == "production",
          f"resolved v{via_tag.json().get('model_version')}")

    # --------------------------------------------------- version 2 + rollback
    weights2 = build_detector(os.path.join(workdir, "detector-v2.onnx"), classes=len(CLASSES),
                              boxes=[(320.0, 320.0, 240.0, 240.0, 3, 0.97)])
    v2 = client.post(f"/v1/models/{SLUG}/versions", json={
        "inherit_from_version": 1, "artifact_format": "onnx",
        "changelog": "Second version: one high-confidence headlight.",
        "metrics": {"mAP50": 0.74, "f1": 0.71},
    })
    check("version 2 inherits version 1's config", v2.status_code == 201
          and v2.json()["config"]["classes"] == CLASSES, str(v2.status_code))

    with open(weights2, "rb") as fh:
        blob2 = fh.read()
    single = client.post(f"/v1/models/{SLUG}/versions/2/weights",
                         files={"file": ("detector-v2.onnx", blob2)})
    check("single-shot upload for a small artifact", single.status_code == 200)

    client.post(f"/v1/models/{SLUG}/versions/2/publish")

    client.post(f"/v1/models/{SLUG}/deployments", json={"version": 2, "replicas": 1})
    live2 = wait_for_live(client, SLUG, 2)
    check("version 2 warms up while version 1 keeps serving", live2)

    still_v1 = client.post(f"/v1/models/{SLUG}/production/predict",
                           files={"images": ("a.jpg", image_bytes)})
    check("the tag still resolves to v1 until it is moved",
          still_v1.status_code == 200 and still_v1.json()["model_version"] == 1)

    moved = client.put(f"/v1/models/{SLUG}/tags", json={"name": "production", "version": 2})
    check("move the tag to v2", moved.status_code == 200)

    now_v2 = None
    for attempt in range(10):
        now_v2 = client.post(f"/v1/models/{SLUG}/production/predict",
                             files={"images": ("a.jpg", image_bytes)})
        if now_v2.status_code == 200 and now_v2.json()["model_version"] == 2:
            break
        time.sleep(1)
    check("the retag takes effect without a redeploy",
          now_v2.status_code == 200 and now_v2.json()["model_version"] == 2,
          f"served v{now_v2.json().get('model_version')}")
    check("v2 returns its own predictions",
          len(now_v2.json()["results"][0]["detections"]) == 1
          and now_v2.json()["results"][0]["detections"][0]["class_name"] == "headlight",
          str([d["class_name"] for d in now_v2.json()["results"][0]["detections"]]))

    rolled = client.post(f"/v1/models/{SLUG}/rollback", json={"tag": "production"})
    check("one-click rollback repoints the tag", rolled.status_code == 200)
    back = None
    for attempt in range(10):
        back = client.post(f"/v1/models/{SLUG}/production/predict",
                           files={"images": ("a.jpg", image_bytes)})
        if back.status_code == 200 and back.json()["model_version"] == 1:
            break
        time.sleep(1)
    check("traffic is back on v1 after the rollback",
          back.status_code == 200 and back.json()["model_version"] == 1,
          f"served v{back.json().get('model_version')}")

    # --------------------------------------------------------- api key + limits
    key = client.post("/v1/keys", json={
        "name": "e2e-consumer", "scopes": [SLUG], "rate_limit_rpm": 5, "monthly_quota": 0,
    })
    check("issue a scoped API key", key.status_code == 201 and key.json()["key"].startswith("ap_"))
    raw_key = key.json()["key"]

    keyed = httpx.Client(base_url=BASE, timeout=60.0, headers={"x-api-key": raw_key})
    with_key = keyed.post(f"/v1/models/{SLUG}/production/predict",
                          files={"images": ("a.jpg", image_bytes)})
    check("the key can call the model it is scoped to", with_key.status_code == 200)

    limited = [keyed.post(f"/v1/models/{SLUG}/production/predict",
                          files={"images": ("a.jpg", image_bytes)}) for _ in range(8)]
    codes = {r.status_code for r in limited}
    check("the rate limit refuses excess calls with 429",
          429 in codes and any(r.json()["error"]["code"] == "rate_limited"
                               for r in limited if r.status_code == 429),
          f"statuses={sorted(codes)}")

    other = client.post("/v1/models", json={"slug": f"{SLUG}-other", "name": "Other",
                                            "task": "detection"})
    if other.status_code in (201, 409):
        # a separate key, so the rate-limit check above cannot mask the scope check
        scoped_key = client.post("/v1/keys", json={
            "name": "e2e-scoped", "scopes": [SLUG], "rate_limit_rpm": 600,
        }).json()["key"]
        scoped_client = httpx.Client(base_url=BASE, timeout=60.0,
                                     headers={"x-api-key": scoped_key})
        scoped_out = scoped_client.post(f"/v1/models/{SLUG}-other/production/predict",
                                        files={"images": ("a.jpg", image_bytes)})
        check("the key cannot reach a model outside its scope",
              scoped_out.status_code == 403
              and scoped_out.json()["error"]["code"] == "key_not_scoped_to_model",
              scoped_out.json().get("error", {}).get("code", ""))

    anonymous = httpx.Client(base_url=BASE, timeout=30.0)
    unauth = anonymous.post(f"/v1/models/{SLUG}/production/predict",
                            files={"images": ("a.jpg", image_bytes)})
    check("an unauthenticated call is refused", unauth.status_code == 401)

    revoked = client.post(f"/v1/keys/{key.json()['id']}/revoke")
    after_revoke = keyed.post(f"/v1/models/{SLUG}/production/predict",
                              files={"images": ("a.jpg", image_bytes)})
    check("a revoked key stops working immediately",
          revoked.status_code == 200 and after_revoke.status_code == 401
          and after_revoke.json()["error"]["code"] == "api_key_revoked")

    # ---------------------------------------------------------------- workflows
    # A second model so the workflow has two stages to chain.
    classifier_slug = f"{SLUG}-severity"
    reset_model(client, classifier_slug)
    client.post("/v1/models", json={"slug": classifier_slug, "name": "E2E severity",
                                    "task": "classification", "owner": "e2e"})
    cls_version = client.post(f"/v1/models/{classifier_slug}/versions", json={
        "preset": "resnet-imagenet", "artifact_format": "onnx",
        "changelog": "Synthetic classifier for the workflow check.",
    })
    if cls_version.status_code == 201:
        cls_config = dict(cls_version.json()["config"])
        cls_config["classes"] = ["light", "moderate", "severe"]
        cls_config["preprocess"]["resize"] = {"mode": "stretch", "width": 224, "height": 224,
                                              "pad_value": 0, "interpolation": "bilinear",
                                              "resize_side": None}
        cls_config["postprocess"]["top_k"] = 3
        client.patch(f"/v1/models/{classifier_slug}/versions/1", json={"config": cls_config})
        cls_weights = build_classifier(os.path.join(workdir, "severity.onnx"), classes=3, winner=2)
        with open(cls_weights, "rb") as fh:
            client.post(f"/v1/models/{classifier_slug}/versions/1/weights",
                        files={"file": ("severity.onnx", fh.read())})
        for attempt in range(8):
            checked = client.post(f"/v1/models/{classifier_slug}/versions/1/validate")
            if checked.status_code == 200 and \
                    checked.json()["validation"].get("status") == "passed":
                break
            time.sleep(3)
        client.post(f"/v1/models/{classifier_slug}/versions/1/publish")
        client.post(f"/v1/models/{classifier_slug}/deployments",
                    json={"version": 1, "replicas": 1})
        client.put(f"/v1/models/{classifier_slug}/tags",
                   json={"name": "production", "version": 1})
        wait_for_live(client, classifier_slug, 1)

    workflow_slug = f"{SLUG}-pipeline"
    if client.get(f"/v1/workflows/{workflow_slug}").status_code == 200:
        client.delete(f"/v1/workflows/{workflow_slug}")
    made = client.post("/v1/workflows", json={
        "slug": workflow_slug, "name": "E2E pipeline", "owner": "e2e",
        "description": "Detect, crop to each detection, then grade each crop.",
    })
    check("create a workflow", made.status_code == 201, made.text[:200])
    check("a new workflow starts from a runnable starter graph",
          len(made.json()["graph"]["nodes"]) == 3 and made.json()["status"] == "draft")

    incomplete = client.post(f"/v1/workflows/{workflow_slug}/publish")
    check("a graph with an unset model cannot be published",
          incomplete.status_code == 422,
          (incomplete.json().get("error") or {}).get("code", ""))

    graph = {
        "nodes": [
            {"id": "in", "position": {"x": 0, "y": 100},
             "data": {"type": "input", "label": "Photo in"}},
            {"id": "detect", "position": {"x": 220, "y": 100},
             "data": {"type": "model", "label": "Detect", "model": SLUG, "ref": "production"}},
            {"id": "crop", "position": {"x": 440, "y": 100},
             "data": {"type": "crop", "label": "Crop", "max_crops": 3, "padding": 0.08,
                      "min_score": 0.3}},
            {"id": "geom", "position": {"x": 660, "y": 100},
             "data": {"type": "geometry", "label": "Geometry", "input_name": "aux"}},
            {"id": "grade", "position": {"x": 880, "y": 100},
             "data": {"type": "model", "label": "Grade", "model": classifier_slug,
                      "ref": "production", "overrides": {"score_threshold": 0.0}}},
            {"id": "out", "position": {"x": 1120, "y": 100},
             "data": {"type": "output", "label": "Severity", "key": "severity"}},
            {"id": "out2", "position": {"x": 1120, "y": 240},
             "data": {"type": "output", "label": "Parts", "key": "parts"}},
        ],
        "edges": [
            {"id": "w1", "source": "in", "target": "detect"},
            {"id": "w2", "source": "detect", "target": "crop"},
            {"id": "w3", "source": "crop", "target": "geom"},
            {"id": "w4", "source": "geom", "target": "grade"},
            {"id": "w5", "source": "grade", "target": "out"},
            {"id": "w6", "source": "detect", "target": "out2"},
        ],
    }
    saved_graph = client.patch(f"/v1/workflows/{workflow_slug}", json={"graph": graph})
    check("save a two-stage graph with no problems reported",
          saved_graph.status_code == 200 and not saved_graph.json()["problems"],
          str(saved_graph.json().get("problems"))[:200])

    cyclic = client.patch(f"/v1/workflows/{workflow_slug}", json={"graph": {
        **graph, "edges": graph["edges"] + [{"id": "bad", "source": "grade", "target": "detect"}],
    }})
    check("a cycle is reported rather than accepted",
          any("cycle" in problem for problem in cyclic.json()["problems"]))
    client.patch(f"/v1/workflows/{workflow_slug}", json={"graph": graph})

    published_wf = client.post(f"/v1/workflows/{workflow_slug}/publish")
    check("publish the workflow", published_wf.status_code == 200
          and published_wf.json()["revision"] == 1)

    ran = client.post(f"/v1/workflows/{workflow_slug}/run",
                      files={"images": ("sample.jpg", image_bytes, "image/jpeg")})
    if check("run the workflow on an image",
             ran.status_code == 200 and ran.json()["status"] == "ok",
             json.dumps(ran.json())[:300]):
        wf = ran.json()
        by_label = {stat["label"]: stat for stat in wf["node_stats"]}
        check("both stages ran", wf["model_calls"] == 2,
              " → ".join(f"{s['label']}({s['items_out']})" for s in wf["node_stats"]))
        check("cropping fanned the image out per detection",
              by_label["Crop"]["items_out"] >= 1,
              f"{by_label['Crop']['items_out']} crops from "
              f"{by_label['Detect']['detections']} detections")
        check("the second stage ran once per crop",
              by_label["Grade"]["items_out"] == by_label["Crop"]["items_out"])
        check("both outputs come back under their keys",
              set(wf["outputs"]) == {"severity", "parts"}, str(list(wf["outputs"])))
        check("each graded crop names the detection it came from",
              all(entry.get("source", {}).get("class_name")
                  for entry in wf["outputs"]["severity"]),
              str([e["source"]["class_name"] for e in wf["outputs"]["severity"]]))
        check("every node is timed", all(stat["ms"] >= 0 for stat in wf["node_stats"])
              and wf["total_ms"] > 0, f"total {wf['total_ms']:.0f} ms")

    wf_runs = client.get(f"/v1/workflows/{workflow_slug}/runs")
    check("workflow runs are recorded with their node timings",
          wf_runs.status_code == 200 and wf_runs.json()
          and bool(wf_runs.json()[0]["node_stats"]),
          f"{len(wf_runs.json())} run(s)")

    # ------------------------------------------------------------ observability
    logged = []
    for attempt in range(20):
        logs = client.get("/v1/inferences", params={"limit": 100, "since_hours": 1})
        logged = [row for row in logs.json() if row["model_slug"] == SLUG]
        if len(logged) >= 8:
            break
        time.sleep(1.5)
    check("inference logs land in the explorer", len(logged) >= 8, f"{len(logged)} records")
    check("logs carry the resolved version and a stage breakdown",
          any(row["version_number"] in (1, 2) and row["inference_ms"] > 0 for row in logged))
    check("failed requests are logged with their error code",
          any(row["status"] != "ok" and row["error_code"] for row in logged),
          str({row["error_code"] for row in logged if row["error_code"]}))

    captured = [row for row in logged if row["has_capture"]]
    check("errors are always captured, so the image is there to look at",
          any(row["status"] != "ok" for row in captured) or bool(captured),
          f"{len(captured)} captured")

    if captured:
        detail = client.get(f"/v1/inferences/{captured[0]['id']}")
        check("a captured request exposes a signed link to its image",
              detail.status_code == 200 and bool(detail.json()["image_urls"]))
        flagged = client.post(f"/v1/inferences/{captured[0]['id']}/flag",
                              json={"flagged": True, "label": "false_positive",
                                    "note": "e2e"})
        check("flag a prediction for retraining", flagged.status_code == 200
              and flagged.json()["flagged"])

    summary = client.get("/v1/metrics/summary", params={"window_minutes": 60})
    check("the metrics summary reports traffic and percentiles",
          summary.status_code == 200 and summary.json()["requests"] > 0
          and summary.json()["p95_ms"] is not None,
          f"{summary.json().get('requests')} requests, p95 {summary.json().get('p95_ms')} ms")

    overview = client.get("/v1/overview")
    check("the overview reports a live fleet",
          overview.status_code == 200 and overview.json()["replicas_ready"] >= 1,
          f"{overview.json().get('replicas_ready')}/{overview.json().get('replicas_desired')} replicas")

    audit = client.get("/v1/audit", params={"limit": 300})
    actions = {row["action"] for row in audit.json()}
    check("every mutating action is in the audit log",
          {"model.create", "version.upload_complete", "version.publish", "tag.create",
           "deployment.create", "deployment.rollback", "apikey.create",
           "apikey.revoke", "workflow.create", "workflow.publish"} <= actions,
          f"{len(actions)} distinct actions")

    report = client.post("/v1/reports/run", json={"kind": "usage", "group_by": "consumer",
                                                  "since_hours": 24, "fmt": "json"})
    check("a usage report groups traffic by consumer",
          report.status_code == 200 and len(report.json()["rows"]) >= 1,
          f"{len(report.json().get('rows', []))} rows")

    csv_export = client.post("/v1/reports/run", json={"kind": "usage", "group_by": "model",
                                                      "since_hours": 24, "fmt": "csv"})
    check("the same report exports as CSV",
          csv_export.status_code == 200 and csv_export.text.startswith("label,"))

    pdf_export = client.post("/v1/reports/run", json={"kind": "errors", "group_by": "model",
                                                      "since_hours": 24, "fmt": "pdf"})
    check("and as PDF", pdf_export.status_code == 200 and pdf_export.content[:4] == b"%PDF")

    metrics_endpoint = httpx.get(f"{BASE.replace('8080', '8000')}/metrics", timeout=10) \
        if "8080" in BASE else client.get("/metrics")
    check("prometheus metrics are exposed",
          metrics_endpoint.status_code == 200
          and "aperture_inference_requests_total" in metrics_endpoint.text)

    runtimes = client.get("/v1/runtimes")
    check("runtimes report their loaded versions",
          runtimes.status_code == 200 and any(r["loaded"] for r in runtimes.json()),
          f"{len(runtimes.json())} runtimes")

    # ------------------------------------------------------------------ tidy up
    stop_v2 = client.get(f"/v1/models/{SLUG}/deployments").json()
    for deployment in stop_v2:
        client.post(f"/v1/deployments/{deployment['id']}/stop")

    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


def reset_model(client: httpx.Client, slug: str) -> None:
    if client.get(f"/v1/models/{slug}").status_code != 200:
        return
    for tag in client.get(f"/v1/models/{slug}/tags").json() or []:
        client.delete(f"/v1/models/{slug}/tags/{tag['name']}")
    for deployment in client.get(f"/v1/models/{slug}/deployments").json() or []:
        client.post(f"/v1/deployments/{deployment['id']}/stop")
    for _ in range(20):
        live = client.get(f"/v1/models/{slug}/deployments").json() or []
        if not live:
            break
        time.sleep(2)
    client.delete(f"/v1/models/{slug}")


def wait_for_live(client: httpx.Client, slug: str, version: int, timeout: float = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/v1/models/{slug}/deployments")
        if response.status_code == 200:
            for deployment in response.json():
                if deployment["version"] == version and deployment["replicas_ready"] >= 1:
                    return True
        time.sleep(2)
    return False


if __name__ == "__main__":
    sys.exit(main())
