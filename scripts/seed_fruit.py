"""Build the `fruit` workspace the user guide's screenshots are taken in.

Everything in it is real: the models are published checkpoints (see
scripts/fruit_models.py) and every request runs them on a real photo.

- Fruit bowl segmentation (fruit-bowl-seg): v1 is YOLOv8n-seg, tagged production;
  v2 is YOLOv8s-seg, the same family one size up, tagged staging. Both deployed,
  so Compare has two live versions to put side by side.
- Fruit variety (fruit-variety): ResNet-18 on ImageNet, which names a crop
  Granny Smith, orange, lemon or banana.
- Fruit sorter (fruit-sorter): a workflow that segments the bowl, crops each
  fruit and names its variety.
- Traffic: a few minutes of requests through a scoped key on the fruit-bowl photo
  and crops of it, with every request captured, plus a few deliberate failures so
  the failure lists have something true to show.

    uv run --no-project --with httpx --with pillow python scripts/seed_fruit.py /tmp/aperture-fruit

Re-running replaces the models and the workflow. Override BASE / AP_EMAIL /
AP_PASSWORD through the environment.
"""

from __future__ import annotations

import io
import json
import os
import random
import sys
import time

import httpx
from PIL import Image, ImageEnhance, ImageOps

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BASE = os.environ.get("BASE", "http://localhost:8080")
EMAIL = os.environ.get("AP_EMAIL", "admin@localhost")
PASSWORD = os.environ.get("AP_PASSWORD", "aperture")
MODELS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "aperture-fruit")
PHOTO = os.path.join(ROOT, "docs", "screenshots", "landing", "fruit-bowl.jpg")
WORKSPACE = "fruit"
SEG, VARIETY, FLOW = "fruit-bowl-seg", "fruit-variety", "fruit-sorter"

client = httpx.Client(base_url=BASE, timeout=300)
token = client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}).raise_for_status().json()
client.headers["authorization"] = f"Bearer {token['access_token']}"


def ok(response: httpx.Response, what: str) -> dict | list | None:
    if response.status_code >= 400:
        sys.exit(f"{what}: {response.status_code} {response.text[:300]}")
    print("ok", what)
    return response.json() if response.content else None


def reset_model(slug: str) -> None:
    """Aperture refuses to delete a model that is still serving: tags, then deployments, then the model."""
    if client.get(f"/v1/models/{slug}").status_code != 200:
        return
    for tag in client.get(f"/v1/models/{slug}/tags").json() or []:
        client.delete(f"/v1/models/{slug}/tags/{tag['name']}")
    for deployment in client.get(f"/v1/models/{slug}/deployments").json() or []:
        client.post(f"/v1/deployments/{deployment['id']}/stop")
    for _ in range(30):
        if not client.get(f"/v1/models/{slug}/deployments").json():
            break
        time.sleep(2)
    client.delete(f"/v1/models/{slug}")


def wait_live(slug: str, version: int, timeout: float = 240) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for deployment in client.get(f"/v1/models/{slug}/deployments").json() or []:
            if deployment["version"] == version and deployment["replicas_ready"] >= 1:
                print("ok", f"{slug} v{version} live")
                return
        time.sleep(3)
    sys.exit(f"{slug} v{version} never came up")


def add_version(slug: str, number: int, *, preset: str, weights: str, classes: list[str], meta: dict,
                tweak=None) -> None:
    version = ok(client.post(f"/v1/models/{slug}/versions", json={"preset": preset, "artifact_format": "onnx", **meta}),
                 f"{slug} v{number}")
    config = dict(version["config"])
    config["classes"] = classes
    if tweak:
        tweak(config)
    ok(client.patch(f"/v1/models/{slug}/versions/{number}", json={"config": config}), f"{slug} v{number} config")
    with open(os.path.join(MODELS, weights), "rb") as fh:
        ok(client.post(f"/v1/models/{slug}/versions/{number}/weights", files={"file": (weights, fh.read())}),
           f"{slug} v{number} weights")
    ok(client.post(f"/v1/models/{slug}/versions/{number}/publish"), f"{slug} v{number} publish")


# --------------------------------------------------------------------- workspace
made = client.post("/v1/workspaces", json={"slug": WORKSPACE, "name": "Fruit stand",
                                           "description": "Real models on real fruit, for the user guide."})
if made.status_code not in (201, 409):
    sys.exit(f"workspace: {made.text[:200]}")
client.headers["x-aperture-workspace"] = WORKSPACE
ok(client.post(f"/v1/workspaces/{WORKSPACE}/activate"), "activate the fruit workspace")

if client.get(f"/v1/workflows/{FLOW}").status_code == 200:
    client.delete(f"/v1/workflows/{FLOW}")
reset_model(SEG)
reset_model(VARIETY)

coco = json.load(open(os.path.join(MODELS, "coco-classes.json")))
imagenet = json.load(open(os.path.join(MODELS, "imagenet-classes.json")))

# ------------------------------------------------------------ fruit bowl segmentation
ok(client.post("/v1/models", json={
    "slug": SEG, "name": "Fruit bowl segmentation", "task": "segmentation", "owner": "fruit stand",
    "description": "Outlines every piece of fruit in the bowl. YOLOv8 segmentation, COCO-80.",
}), "create fruit-bowl-seg")


def seg_threshold(config: dict) -> None:
    config["postprocess"]["score_threshold"] = 0.25


add_version(SEG, 1, preset="yolov8-seg", weights="yolov8n-seg.onnx", classes=coco, tweak=seg_threshold, meta={
    "dataset_version": "COCO 2017", "training_run_id": "ultralytics-yolov8n-seg",
    "metrics": {"mAP50-95 box": 0.367, "mAP50-95 mask": 0.305},
    "changelog": "YOLOv8n-seg: the nano model, fast on CPU.",
})
add_version(SEG, 2, preset="yolov8-seg", weights="yolov8s-seg.onnx", classes=coco, tweak=seg_threshold, meta={
    "dataset_version": "COCO 2017", "training_run_id": "ultralytics-yolov8s-seg",
    "metrics": {"mAP50-95 box": 0.446, "mAP50-95 mask": 0.368},
    "changelog": "YOLOv8s-seg: one size up, tighter masks for a little more latency.",
})
ok(client.post(f"/v1/models/{SEG}/deployments", json={"version": 1, "replicas": 1}), "deploy v1")
ok(client.post(f"/v1/models/{SEG}/deployments", json={"version": 2, "replicas": 1}), "deploy v2")
wait_live(SEG, 1)
wait_live(SEG, 2)
ok(client.put(f"/v1/models/{SEG}/tags", json={"name": "production", "version": 1}), "production -> v1")
ok(client.put(f"/v1/models/{SEG}/tags", json={"name": "staging", "version": 2}), "staging -> v2")

# ------------------------------------------------------------------- fruit variety
ok(client.post("/v1/models", json={
    "slug": VARIETY, "name": "Fruit variety", "task": "classification", "owner": "fruit stand",
    "description": "Names the fruit in a crop. ResNet-18 on ImageNet-1k.",
}), "create fruit-variety")
add_version(VARIETY, 1, preset="resnet-imagenet", weights="resnet18-imagenet.onnx", classes=imagenet, meta={
    "dataset_version": "ImageNet-1k", "training_run_id": "torchvision-resnet18-v1",
    "metrics": {"acc@1": 0.6976, "acc@5": 0.8908},
    "changelog": "torchvision ResNet-18, IMAGENET1K_V1 weights.",
})
ok(client.post(f"/v1/models/{VARIETY}/deployments", json={"version": 1, "replicas": 1}), "deploy fruit-variety")
wait_live(VARIETY, 1)
ok(client.put(f"/v1/models/{VARIETY}/tags", json={"name": "production", "version": 1}), "fruit-variety production")

# ------------------------------------------------------------------------ workflow
ok(client.post("/v1/workflows", json={"slug": FLOW, "name": "Fruit sorter", "owner": "fruit stand",
                                      "description": "Outline the bowl, crop each fruit, name its variety."}),
   "create the workflow")
graph = {  # three columns, so the guide's screenshot of the graph is readable
    "nodes": [
        {"id": "in", "position": {"x": 0, "y": 0}, "data": {"type": "input", "label": "Photo in"}},
        {"id": "segment", "position": {"x": 300, "y": 0},
         "data": {"type": "model", "label": "Segment", "model": SEG, "ref": "production"}},
        {"id": "crop", "position": {"x": 300, "y": 200},
         "data": {"type": "crop", "label": "Crop", "max_crops": 6, "padding": 0.05, "min_score": 0.5}},
        {"id": "variety", "position": {"x": 600, "y": 200},
         "data": {"type": "model", "label": "Classify", "model": VARIETY, "ref": "production",
                  "overrides": {"top_k": 1}}},
        {"id": "out", "position": {"x": 600, "y": 380}, "data": {"type": "output", "label": "Variety", "key": "variety"}},
        {"id": "out2", "position": {"x": 600, "y": 0}, "data": {"type": "output", "label": "Fruit", "key": "fruit"}},
    ],
    "edges": [
        {"id": "w1", "source": "in", "target": "segment"},
        {"id": "w2", "source": "segment", "target": "crop"},
        {"id": "w3", "source": "crop", "target": "variety"},
        {"id": "w4", "source": "variety", "target": "out"},
        {"id": "w5", "source": "segment", "target": "out2"},
    ],
}
saved = ok(client.patch(f"/v1/workflows/{FLOW}", json={"graph": graph}), "save the graph")
if saved.get("problems"):
    sys.exit(f"graph problems: {saved['problems']}")
ok(client.post(f"/v1/workflows/{FLOW}/publish"), "publish the workflow")

# ------------------------------------------------------------------------- traffic
photo = Image.open(PHOTO).convert("RGB")
rng = random.Random(7)


def variant(i: int) -> bytes:
    """The bowl, or a crop of it, sometimes mirrored or relit: real fruit, not one frame on repeat."""
    img = photo
    if i % 3:
        w, h = img.size
        cw, ch = int(w * rng.uniform(0.55, 0.9)), int(h * rng.uniform(0.55, 0.9))
        x, y = rng.randint(0, w - cw), rng.randint(0, h - ch)
        img = img.crop((x, y, x + cw, y + ch))
    if i % 4 == 1:
        img = ImageOps.mirror(img)
    if i % 5 == 2:
        img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.75, 1.2))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    return out.getvalue()


for old in client.get("/v1/keys").json() or []:  # a re-run issues a fresh key and retires the last one
    if old["name"] == "fruit-stand-kiosk" and not old["revoked_at"]:
        client.post(f"/v1/keys/{old['id']}/revoke")
key = ok(client.post("/v1/keys", json={"name": "fruit-stand-kiosk", "scopes": [SEG, VARIETY],
                                       "rate_limit_rpm": 600}), "a scoped key for the kiosk")
kiosk = httpx.Client(base_url=BASE, timeout=300, headers={"x-api-key": key["key"]})

for i in range(36):
    ref = "staging" if i % 6 == 5 else "production"
    kiosk.post(f"/v1/models/{SEG}/{ref}/predict", files={"images": (f"bowl-{i}.jpg", variant(i), "image/jpeg")},
               data={"capture": "true"})
for i in range(8):
    client.post(f"/v1/workflows/{FLOW}/run", files={"images": (f"bowl-{i}.jpg", variant(i), "image/jpeg")})
# failures the console should show for what they are
kiosk.post(f"/v1/models/{SEG}/production/predict", files={"images": ("not-a-photo.jpg", b"this is not a jpeg")},
           data={"capture": "true"})
kiosk.post(f"/v1/models/{SEG}/production/predict", files={"images": ("bowl.jpg", variant(0), "image/jpeg")},
           data={"overrides": json.dumps({"input_width": 320})})
kiosk.post(f"/v1/models/{SEG}/canary/predict", files={"images": ("bowl.jpg", variant(1), "image/jpeg")})
print("ok traffic")
print(f"the fruit workspace is ready at {BASE}")
