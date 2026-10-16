"""Check the Roboflow-compatible response against Roboflow's documented shape.

Roboflow's object-detection response gives each prediction as a CENTRE point
plus width/height, with `confidence` and `class`; classification gives a
`predictions` list with `top`/`confidence`, or a dict keyed by class for a
multi-label head. This asserts all of that, against the real models.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

BASE = os.environ.get("APERTURE_URL", "http://localhost:8080")
TEST_DIR = Path("/Users/fayez/projects/fiasco/cells-detection/data/cls-test")
checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    checks.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   -> {detail}" if detail else ""), flush=True)
    return ok


client = httpx.Client(base_url=BASE, timeout=300.0)
login = client.post("/v1/auth/login", json={"email": "admin@localhost", "password": "aperture"})
login.raise_for_status()
client.headers.update({"authorization": f"Bearer {login.json()['access_token']}"})

image = (TEST_DIR / "cls-test1.jpeg").read_bytes()

# ---------------------------------------------------------------- detection
native = client.post("/v1/models/part-detection/production/predict",
                     files={"images": ("a.jpg", image, "image/jpeg")}).json()
rf = client.post("/v1/models/part-detection/production/predict",
                 params={"format": "roboflow"},
                 files={"images": ("a.jpg", image, "image/jpeg")}).json()

check("a single image answers with one object, not a list", isinstance(rf, dict),
      type(rf).__name__)
check("it carries inference_id, time and image dimensions",
      {"inference_id", "time", "image", "predictions"} <= set(rf),
      str(sorted(rf.keys())))
check("image dimensions match the source",
      rf["image"] == {"width": native["results"][0]["width"],
                      "height": native["results"][0]["height"]},
      str(rf["image"]))

fields = set(rf["predictions"][0]) if rf["predictions"] else set()
check("each prediction has Roboflow's field names",
      {"x", "y", "width", "height", "confidence", "class", "class_id", "detection_id"} <= fields,
      str(sorted(fields)))

native_box = native["results"][0]["detections"][0]["box"]
prediction = rf["predictions"][0]
expected_cx = round((native_box[0] + native_box[2]) / 2, 1)
expected_cy = round((native_box[1] + native_box[3]) / 2, 1)
check("x and y are the box CENTRE, as Roboflow defines them",
      abs(prediction["x"] - expected_cx) < 0.2 and abs(prediction["y"] - expected_cy) < 0.2,
      f"aperture {native_box} -> roboflow x={prediction['x']} y={prediction['y']} "
      f"w={prediction['width']} h={prediction['height']}")
check("width and height are the full extent",
      abs(prediction["width"] - (native_box[2] - native_box[0])) < 0.2
      and abs(prediction["height"] - (native_box[3] - native_box[1])) < 0.2)
check("confidence and class carry the same values as the native response",
      abs(prediction["confidence"] - native["results"][0]["detections"][0]["score"]) < 1e-6
      and prediction["class"] == native["results"][0]["detections"][0]["class_name"],
      f"{prediction['class']} {prediction['confidence']}")
check("detection_id is a distinct id per prediction",
      len({p["detection_id"] for p in rf["predictions"]}) == len(rf["predictions"]))
check("the resolved version still travels, in a header not the body",
      "x-aperture-model-version" in {k.lower() for k in
          client.post("/v1/models/part-detection/production/predict",
                      params={"format": "roboflow"},
                      files={"images": ("a.jpg", image, "image/jpeg")}).headers})

# ------------------------------------------------------------ classification
rf_cls = client.post("/v1/models/damage-groups/production/predict",
                     params={"format": "roboflow"},
                     data={"inputs": json.dumps({"aux": [0.5, 0.5, 0.5, 0.5]}),
                           "overrides": json.dumps({"score_threshold": 0.0})},
                     files={"images": ("a.jpg", image, "image/jpeg")}).json()
check("a multi-label head answers with predictions keyed by class",
      isinstance(rf_cls["predictions"], dict)
      and all({"confidence", "class_id"} <= set(v) for v in rf_cls["predictions"].values()),
      str(list(rf_cls["predictions"])[:4]))
check("predicted_classes lists what crossed 0.5",
      "predicted_classes" in rf_cls
      and all(rf_cls["predictions"][name]["confidence"] >= 0.5
              for name in rf_cls["predicted_classes"]),
      str(rf_cls["predicted_classes"]))

rf_single = client.post("/v1/models/damage-severity/production/predict",
                        params={"format": "roboflow"},
                        files={"images": ("a.jpg", image, "image/jpeg")}).json()
check("a single-label head answers with a list plus top and confidence",
      isinstance(rf_single["predictions"], list) and "top" in rf_single
      and "confidence" in rf_single,
      f"top={rf_single.get('top')} confidence={rf_single.get('confidence')}")

# -------------------------------------------------------------------- batch
rf_batch = client.post("/v1/models/part-detection/production/predict",
                       params={"format": "roboflow"}, files=[
                           ("images", ("a.jpg", image, "image/jpeg")),
                           ("images", ("b.jpg", image, "image/jpeg")),
                       ]).json()
check("a batch answers with one object per image, as a list",
      isinstance(rf_batch, list) and len(rf_batch) == 2
      and all("predictions" in item for item in rf_batch))

# ------------------------------------------------------------------ negative
bad = client.post("/v1/models/part-detection/production/predict",
                  params={"format": "yolo"},
                  files={"images": ("a.jpg", image, "image/jpeg")})
check("an unknown format is refused with a typed error",
      bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_request",
      (bad.json().get("error") or {}).get("message", "")[:90])

# ----------------------------------------------------------------- workflows
wf = client.get("/v1/workflows/damage-report").json()
graph = wf["graph"]
for node in graph["nodes"]:
    if node["data"].get("type") == "output" and node["data"].get("key") == "panels":
        node["data"]["format"] = "roboflow"
client.patch("/v1/workflows/damage-report", json={"graph": graph})
client.post("/v1/workflows/damage-report/publish")
wf_run = client.post("/v1/workflows/damage-report/run",
                     files={"images": ("a.jpg", image, "image/jpeg")}).json()
panels = wf_run["outputs"]["panels"][0]
damage = wf_run["outputs"]["damage"][0]
check("a workflow Output node can emit Roboflow shape",
      "predictions" in panels and "x" in panels["predictions"][0],
      str(list(panels.keys())))
check("another output on the same workflow stays native",
      "classifications" in damage,
      str([k for k in damage.keys() if k != "source"]))

whole_run = client.post("/v1/workflows/damage-report/run", params={"format": "roboflow"},
                        files={"images": ("a.jpg", image, "image/jpeg")}).json()
check("a run-level format applies to every output",
      all("predictions" in entry for entry in whole_run["outputs"]["damage"]),
      str(list(whole_run["outputs"]["damage"][0].keys())))

print("\nroboflow-shaped detection response:")
print(json.dumps({**rf, "predictions": rf["predictions"][:2]}, indent=2)[:700])

failed = sum(1 for _, ok, _ in checks if not ok)
print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
sys.exit(1 if failed else 0)
