"""Build and run the real two-stage pipeline as a workflow.

    photo ──▶ part-detection ──▶ crop to each panel ──▶ box geometry ──▶ damage-groups ──▶ result

This is the pipeline the cells-detection scripts do by hand, expressed once as a
graph and served as one endpoint.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("APERTURE_URL", "http://localhost:8080")
TEST_DIR = Path("/Users/fayez/projects/fiasco/cells-detection/data/cls-test")
SLUG = "damage-report"

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    checks.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   -> {detail}" if detail else ""), flush=True)
    return ok


client = httpx.Client(base_url=BASE, timeout=300.0)
login = client.post("/v1/auth/login", json={"email": "admin@localhost", "password": "aperture"})
login.raise_for_status()
client.headers.update({"authorization": f"Bearer {login.json()['access_token']}"})

if client.get(f"/v1/workflows/{SLUG}").status_code == 200:
    client.delete(f"/v1/workflows/{SLUG}")

created = client.post("/v1/workflows", json={
    "slug": SLUG, "name": "Damage report", "owner": "perception",
    "description": "Detect panels, crop to each one, classify its damage group.",
})
if not check("create a workflow", created.status_code == 201, created.text[:200]):
    sys.exit(1)
check("a new workflow opens with a runnable starter graph",
      len(created.json()["graph"]["nodes"]) == 3)

# the starter graph has no model set, so it must refuse to publish
refused = client.post(f"/v1/workflows/{SLUG}/publish")
check("an incomplete graph cannot be published",
      refused.status_code == 422 and "model node" in refused.text,
      (refused.json().get("error") or {}).get("message", "")[:110])

graph = {
    "nodes": [
        {"id": "input", "position": {"x": 0, "y": 140},
         "data": {"type": "input", "label": "Photo in"}},
        {"id": "detect", "position": {"x": 220, "y": 140},
         "data": {"type": "model", "label": "Detect panels", "model": "part-detection",
                  "ref": "production", "overrides": {"score_threshold": 0.3}}},
        {"id": "crop", "position": {"x": 460, "y": 140},
         "data": {"type": "crop", "label": "Crop each panel", "max_crops": 3,
                  "padding": 0.1, "min_score": 0.3}},
        {"id": "geom", "position": {"x": 700, "y": 140},
         "data": {"type": "geometry", "label": "Panel geometry", "input_name": "aux",
                  "format": "cxcywh"}},
        {"id": "classify", "position": {"x": 940, "y": 140},
         # a report wants the top groups whatever their absolute score, so this
         # node lowers the threshold it was deployed with - per node, per run
         "data": {"type": "model", "label": "Classify damage", "model": "damage-groups",
                  "ref": "production",
                  "overrides": {"score_threshold": 0.0, "top_k": 3}}},
        {"id": "out_groups", "position": {"x": 1190, "y": 60},
         "data": {"type": "output", "label": "Damage per panel", "key": "damage"}},
        {"id": "out_panels", "position": {"x": 1190, "y": 240},
         "data": {"type": "output", "label": "Panels found", "key": "panels"}},
    ],
    "edges": [
        {"id": "e1", "source": "input", "target": "detect"},
        {"id": "e2", "source": "detect", "target": "crop"},
        {"id": "e3", "source": "crop", "target": "geom"},
        {"id": "e4", "source": "geom", "target": "classify"},
        {"id": "e5", "source": "classify", "target": "out_groups"},
        {"id": "e6", "source": "detect", "target": "out_panels"},
    ],
}
saved = client.patch(f"/v1/workflows/{SLUG}", json={"graph": graph})
check("save the two-stage graph", saved.status_code == 200
      and not saved.json()["problems"], str(saved.json().get("problems"))[:200])
check("the workflow reports which models it uses",
      saved.json()["models_used"] == ["damage-groups", "part-detection"],
      str(saved.json()["models_used"]))

# a cycle must be refused
cyclic = client.patch(f"/v1/workflows/{SLUG}", json={"graph": {
    **graph, "edges": graph["edges"] + [{"id": "bad", "source": "classify", "target": "detect"}],
}})
check("a cycle is reported as a problem",
      any("cycle" in problem for problem in cyclic.json()["problems"]),
      str(cyclic.json()["problems"])[:120])
client.patch(f"/v1/workflows/{SLUG}", json={"graph": graph})

published = client.post(f"/v1/workflows/{SLUG}/publish")
check("publish it", published.status_code == 200 and published.json()["revision"] == 1,
      published.text[:200] if published.status_code != 200 else "revision 1")

image = (TEST_DIR / "cls-test1.jpeg").read_bytes()
run = client.post(f"/v1/workflows/{SLUG}/run",
                  files={"images": ("cls-test1.jpeg", image, "image/jpeg")})
if not check("run the published workflow on a real photo",
             run.status_code == 200 and run.json()["status"] == "ok",
             json.dumps(run.json())[:400] if run.status_code == 200 else run.text[:300]):
    sys.exit(1)

body = run.json()
stats = {stat["label"]: stat for stat in body["node_stats"]}
check("both models ran, in order", body["model_calls"] == 2,
      " → ".join(f"{s['label']}({s['items_out']})" for s in body["node_stats"]))
check("the detector found panels", stats["Detect panels"]["detections"] >= 1,
      f"{stats['Detect panels']['detections']} detections")
check("cropping fanned one photo out into several images",
      stats["Crop each panel"]["items_out"] >= 1,
      f"{stats['Crop each panel']['items_out']} crops")
check("the classifier ran once per crop",
      stats["Classify damage"]["items_out"] == stats["Crop each panel"]["items_out"],
      f"{stats['Classify damage']['items_out']} classified")
check("both outputs are returned under their keys",
      set(body["outputs"]) == {"damage", "panels"}, str(list(body["outputs"])))
check("per-node timings add up to roughly the total",
      abs(sum(stat["ms"] for stat in body["node_stats"]) - body["total_ms"]) < body["total_ms"] * 0.5,
      f"total {body['total_ms']:.0f} ms, nodes {sum(s['ms'] for s in body['node_stats']):.0f} ms")

damage = body["outputs"]["damage"]
check("each classified crop names the panel it came from",
      all(entry.get("source", {}).get("class_name") for entry in damage),
      str([entry["source"]["class_name"] for entry in damage]))
check("every crop comes back with its top damage groups",
      all(len(entry.get("classifications") or []) == 3 for entry in damage),
      str([[c["class_name"] for c in entry["classifications"]] for entry in damage]))
check("a node-level threshold override took effect",
      all(entry["classifications"][-1]["score"] < 0.5 for entry in damage
          if entry.get("classifications")),
      "groups below 0.5 are returned because the node asked for the top 3")

# a batch of photos through the whole graph
batch = client.post(f"/v1/workflows/{SLUG}/run", files=[
    ("images", (name, (TEST_DIR / name).read_bytes(), "image/jpeg"))
    for name in ("cls-test1.jpeg", "cls-test2.jpeg")
])
check("two photos through the same graph in one request",
      batch.status_code == 200 and batch.json()["status"] == "ok"
      and len(batch.json()["outputs"]["panels"]) == 2,
      f"{batch.json()['total_ms']:.0f} ms for 2 photos" if batch.status_code == 200
      else batch.text[:200])

runs = client.get(f"/v1/workflows/{SLUG}/runs")
check("runs are recorded with their per-node timings",
      runs.status_code == 200 and len(runs.json()) >= 2
      and bool(runs.json()[0]["node_stats"]),
      f"{len(runs.json())} runs recorded")

# the model calls a workflow makes are logged like any other inference
logs = client.get("/v1/inferences", params={"limit": 20, "since_hours": 1})
workflow_calls = [row for row in logs.json() if row["model_slug"] in ("part-detection", "damage-groups")]
check("the workflow's model calls appear in the inference explorer",
      len(workflow_calls) >= 2, f"{len(workflow_calls)} recent calls")

api_key = client.post("/v1/keys", json={"name": "workflow-consumer", "scopes": ["*"],
                                        "rate_limit_rpm": 600}).json()["key"]
keyed = httpx.Client(base_url=BASE, timeout=300.0, headers={"x-api-key": api_key})
as_product = keyed.post(f"/v1/workflows/{SLUG}/run",
                        files={"images": ("a.jpg", image, "image/jpeg")})
check("a product can call it with an API key",
      as_product.status_code == 200 and as_product.json()["status"] == "ok",
      as_product.text[:200] if as_product.status_code != 200 else
      f"{as_product.json()['total_ms']:.0f} ms")

draft_as_product = keyed.post(f"/v1/workflows/{SLUG}/run",
                              files={"images": ("a.jpg", image, "image/jpeg")},
                              data={"draft": "true"})
check("an API key cannot run an unpublished draft",
      draft_as_product.status_code == 403,
      (draft_as_product.json().get("error") or {}).get("code", ""))

print(f"\nthe pipeline, timed:")
for stat in body["node_stats"]:
    bar = "█" * max(1, int(stat["ms"] / max(body["total_ms"], 1) * 40))
    print(f"  {stat['label']:22s} {stat['ms']:7.1f} ms  {bar}")
print(f"  {'total':22s} {body['total_ms']:7.1f} ms")

failed = sum(1 for _, ok, _ in checks if not ok)
print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
sys.exit(1 if failed else 0)
