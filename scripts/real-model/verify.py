"""Serve the real damage classifier through Aperture and compare with PyTorch.

The point is not that it answers - it is that it answers with the *same numbers*
the trained model produces. If Aperture's resize, normalisation or sigmoid decode
differ from the training transform in any way, the deltas below will show it.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("APERTURE_URL", "http://localhost:8080")
HERE = Path(os.environ.get("APERTURE_REAL_DIR", "/tmp/aperture-real"))
REF = json.loads((HERE / "reference.json").read_text())
ONNX = HERE / "damage-classifier.onnx"
TEST_DIR = Path("/Users/fayez/projects/fiasco/cells-detection/data/cls-test")
SLUG = "damage-groups"
GROUPS = REF["groups"]
AUX = REF["aux"]

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    checks.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   -> {detail}" if detail else ""), flush=True)
    return ok


client = httpx.Client(base_url=BASE, timeout=600.0)
login = client.post("/v1/auth/login", json={"email": "admin@localhost", "password": "aperture"})
login.raise_for_status()
client.headers.update({"authorization": f"Bearer {login.json()['access_token']}",
                       "x-aperture-project": "default"})

# tear down a previous run
if client.get(f"/v1/models/{SLUG}").status_code == 200:
    for tag in client.get(f"/v1/models/{SLUG}/tags").json() or []:
        client.delete(f"/v1/models/{SLUG}/tags/{tag['name']}")
    for deployment in client.get(f"/v1/models/{SLUG}/deployments").json() or []:
        client.post(f"/v1/deployments/{deployment['id']}/stop")
    for _ in range(20):
        if not client.get(f"/v1/models/{SLUG}/deployments").json():
            break
        time.sleep(2)
    client.delete(f"/v1/models/{SLUG}")

created = client.post("/v1/models", json={
    "slug": SLUG, "name": "Damage group classifier", "task": "classification",
    "owner": "perception",
    "description": "ConvNeXt-small + self-attention pooling, multi-label over 16 damage groups. "
                   "Takes the crop plus the damage box geometry.",
    "labels": ["real", "multi-label", "multi-input"],
})
check("create the model", created.status_code == 201, created.text[:160])

# The config is the training transform, stated explicitly: a stretch to 224
# (not the ImageNet centre crop), ImageNet mean/std, sigmoid decode over 16
# groups at the 0.5 threshold the training script itself uses.
config = {
    "task": "classification",
    "backend": "onnx",
    "input": {"width": 224, "height": 224, "channels": 3, "layout": "NCHW", "dtype": "float32",
              "color": "RGB", "input_names": ["images", "aux"], "output_names": ["logits"]},
    "preprocess": {
        "resize": {"mode": "stretch", "width": 224, "height": 224, "interpolation": "bilinear"},
        "normalize": {"scale": 1 / 255.0, "mean": REF["mean"], "std": REF["std"]},
        "auto_orient": True,
    },
    "postprocess": {"decoder": "classifier_sigmoid", "score_threshold": 0.5, "top_k": 16},
    "classes": GROUPS,
    "warmup": {"iterations": 2, "batch_sizes": [1]},
    # validation needs the model's second input to be able to run at all
    "extra": {"validation_inputs": {"aux": [AUX]}},
    "notes": "Exported from runs/variant_sweep_cells_v5/part-filled_damage-contour__convnext_small.",
}

version = client.post(f"/v1/models/{SLUG}/versions", json={
    "config": config, "artifact_format": "onnx",
    "dataset_version": "cells-v5 part-filled_damage-contour",
    "training_run_id": "variant_sweep_cells_v5", "metrics": {"macro_f1": 0.0},
    "changelog": "ConvNeXt-small, 16 damage groups, multi-label.",
})
if not check("create version 1 with the training transform", version.status_code == 201,
             version.text[:200]):
    sys.exit(1)

blob = ONNX.read_bytes()
digest = hashlib.sha256(blob).hexdigest()
chunk = 32 * 1024 * 1024
session = client.post(f"/v1/models/{SLUG}/versions/1/uploads", json={
    "filename": "damage-classifier.onnx", "total_bytes": len(blob), "sha256": digest,
    "chunk_bytes": chunk,
})
upload_id = session.json()["id"]
parts = (len(blob) + chunk - 1) // chunk
started = time.perf_counter()
for index in range(parts):
    piece = blob[index * chunk:(index + 1) * chunk]
    client.put(f"/v1/uploads/{upload_id}/parts/{index + 1}",
               files={"chunk": (f"part{index + 1}", piece)})
complete = client.post(f"/v1/uploads/{upload_id}/complete")
elapsed = time.perf_counter() - started
check(f"chunked upload of {len(blob) / 1e6:.0f} MB in {parts} parts",
      complete.status_code == 200 and complete.json()["weights_sha256"] == digest,
      f"{elapsed:.1f}s, checksum verified")

sample = (TEST_DIR / REF["results"][0]["file"]).read_bytes()
validated = None
for attempt in range(8):
    validated = client.post(f"/v1/models/{SLUG}/versions/1/validate",
                            files={"sample": ("sample.jpg", sample, "image/jpeg")})
    if validated.status_code == 200 and validated.json()["validation"].get("status") == "passed":
        break
    time.sleep(5)
validation = (validated.json() or {}).get("validation", {})
if not check("validate the multi-input model through a runtime",
             validation.get("status") == "passed", str(validation.get("errors"))[:300]):
    sys.exit(1)
check("validation reports 16 class columns, matching the group list",
      (validation.get("io") or {}).get("class_columns") == 16,
      f"class_columns={(validation.get('io') or {}).get('class_columns')}")

client.post(f"/v1/models/{SLUG}/versions/1/publish")
client.post(f"/v1/models/{SLUG}/deployments", json={
    "version": 1, "replicas": 1, "max_batch_size": 4, "max_wait_ms": 20,
    "request_timeout_ms": 60000,
})
client.put(f"/v1/models/{SLUG}/tags", json={"name": "production", "version": 1})

live = False
for _ in range(60):
    deployments = client.get(f"/v1/models/{SLUG}/deployments").json()
    if any(d["replicas_ready"] >= 1 for d in deployments):
        live = True
        break
    time.sleep(3)
if not check("the 202 MB model loads, warms up and reports ready", live):
    sys.exit(1)

# ------------------------------------------------------ the actual comparison
worst = 0.0
worst_file = ""
mismatched_labels: list[str] = []
latencies: list[float] = []
compared = 0

for row in REF["results"]:
    image = (TEST_DIR / row["file"]).read_bytes()
    response = client.post(f"/v1/models/{SLUG}/production/predict",
                           files={"images": (row["file"], image, "image/jpeg")},
                           data={"inputs": json.dumps({"aux": AUX}),
                                 "overrides": json.dumps({"score_threshold": 0.0})})
    if response.status_code != 200:
        check(f"predict {row['file']}", False, response.text[:200])
        break

    payload = response.json()
    latencies.append(payload["latency"]["total_ms"])
    served = {item["class_name"]: item["score"]
              for item in payload["results"][0]["classifications"]}
    expected = dict(zip(GROUPS, row["probs"]))

    for group, value in expected.items():
        delta = abs(served.get(group, -1) - value)
        if delta > worst:
            worst, worst_file = delta, row["file"]

    served_above = sorted(g for g, v in served.items() if v >= 0.5)
    if served_above != sorted(row["above_0.5"]):
        mismatched_labels.append(f"{row['file']}: aperture={served_above} torch={row['above_0.5']}")
    compared += 1

check(f"served all {len(REF['results'])} real test images", compared == len(REF["results"]),
      f"{compared} compared")
check("probabilities match PyTorch to within 1e-3",
      worst < 1e-3, f"largest deviation {worst:.2e} (on {worst_file})")
check("the same groups cross the 0.5 threshold as in PyTorch",
      not mismatched_labels, "; ".join(mismatched_labels[:3]))

if latencies:
    latencies.sort()
    print(f"\nlatency over {len(latencies)} real images on CPU: "
          f"p50 {latencies[len(latencies) // 2]:.0f} ms, "
          f"p95 {latencies[int(len(latencies) * 0.95)]:.0f} ms")

# batching a real multi-input model
batch = client.post(f"/v1/models/{SLUG}/production/predict", files=[
    ("images", (row["file"], (TEST_DIR / row["file"]).read_bytes(), "image/jpeg"))
    for row in REF["results"][:4]
], data={"inputs": json.dumps({"aux": AUX})})
check("batch of 4 real images with a shared aux input",
      batch.status_code == 200 and len(batch.json()["results"]) == 4,
      f"batch_size={batch.json().get('batch_size')}" if batch.status_code == 200
      else batch.text[:200])

per_image = client.post(f"/v1/models/{SLUG}/production/predict", files=[
    ("images", (row["file"], (TEST_DIR / row["file"]).read_bytes(), "image/jpeg"))
    for row in REF["results"][:3]
], data={"inputs": json.dumps({"aux": [AUX, [0.2, 0.2, 0.1, 0.1], [0.8, 0.8, 0.3, 0.3]]})})
check("a different aux row per image in one batch",
      per_image.status_code == 200 and len(per_image.json()["results"]) == 3,
      per_image.text[:200] if per_image.status_code != 200 else "")

missing_aux = client.post(f"/v1/models/{SLUG}/production/predict",
                          files={"images": ("a.jpg", sample, "image/jpeg")})
check("omitting a required input fails with a message that says what to send",
      missing_aux.status_code >= 400
      and "aux" in json.dumps(missing_aux.json()).lower(),
      json.dumps(missing_aux.json())[:220])

failed = sum(1 for _, ok, _ in checks if not ok)
print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
sys.exit(1 if failed else 0)
