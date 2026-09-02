#!/usr/bin/env python3
"""What is in the Roboflow workspace, and how each piece lands in Aperture.

The cloud repo names the projects and workflows it uses, but not which versions
exist or what was trained on them - that lives in Roboflow. So this does two
things:

  offline  read the projects and workflows straight out of cloud's fly/.env
           config, so the list matches what production actually calls
  --live   ask Roboflow for every project version, its model type and whether
           weights can be downloaded (needs ROBOFLOW_API_KEY)

Nothing is uploaded anywhere and the key is only ever sent to Roboflow.

    uv run --with httpx python scripts/roboflow_inventory.py
    ROBOFLOW_API_KEY=... uv run --with httpx python scripts/roboflow_inventory.py --live
    ROBOFLOW_API_KEY=... uv run --with roboflow python scripts/roboflow_inventory.py \\
        --download parts-segmentation-eiqne:12 --out ./weights
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

CLOUD = Path(os.environ.get("CLOUD_REPO", Path.home() / "projects/fiasco/cloud"))
API = "https://api.roboflow.com"

# Roboflow's model type -> the Aperture preset that decodes it. The preset sets
# the decoder, the input size and the box convention; only the class names still
# have to come from the dataset.
PRESETS = {
    "yolov8": "yolov8", "yolov8n": "yolov8", "yolov8s": "yolov8", "yolov8m": "yolov8",
    "yolov8l": "yolov8", "yolov8x": "yolov8", "yolov11": "yolov8", "yolo11": "yolov8",
    "yolov5": "yolov5", "yolov7": "yolov5",
    "yolov8-seg": "yolov8-seg", "yolo11-seg": "yolov8-seg", "yolov8n-seg": "yolov8-seg",
    "yolov8-cls": "resnet-imagenet", "yolo11-cls": "resnet-imagenet",
    "yolov8-pose": "yolov8-pose", "yolo11-pose": "yolov8-pose",
    "rfdetr": "rf-detr", "rf-detr": "rf-detr", "rfdetr-base": "rf-detr",
    "rfdetr-seg": "rf-detr-seg", "rf-detr-seg": "rf-detr-seg",
    "rfdetr-seg-preview": "rf-detr-seg",
}

TASK_HINT = {
    "object-detection": "detection",
    "instance-segmentation": "segmentation",
    "semantic-segmentation": "segmentation",
    "single-label-classification": "classification",
    "multi-label-classification": "classification",
    "keypoint-detection": "pose",
}


def declared() -> dict:
    """Projects and workflows the cloud app is configured to use."""
    found: dict[str, dict[str, str]] = {"projects": {}, "workflows": {}, "other": {}}
    for name in ("fly.production.toml", "fly.staging.toml", ".env.example"):
        path = CLOUD / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            match = re.match(r'\s*(ROBOFLOW_[A-Z0-9_]+)\s*=\s*"?([^"\n]+)"?', line)
            if not match:
                continue
            key, value = match.group(1), match.group(2).strip()
            if not value or value.startswith("${"):
                continue
            bucket = ("projects" if key.endswith("_PROJECT_ID")
                      else "workflows" if key.endswith("_WORKFLOW_ID") else "other")
            found[bucket].setdefault(value, key)

    # Some projects are only named in the download tasks, never as an env var -
    # the license-plate model is the one the repo actually pulls weights for, so
    # leaving it out would make this list look complete while missing it.
    tasks = CLOUD / "priv/sofia/roboflow-mise-tasks.toml"
    if tasks.exists():
        for slug in set(re.findall(r'--project\s+"?([a-z0-9][a-z0-9-]+)"?', tasks.read_text())):
            found["projects"].setdefault(slug, "roboflow-mise-tasks.toml (weights download)")
    return found


def live(workspace: str, key: str) -> list[dict]:
    import httpx

    with httpx.Client(timeout=60) as client:
        response = client.get(f"{API}/{workspace}", params={"api_key": key})
        response.raise_for_status()
        projects = response.json().get("workspace", {}).get("projects", [])

        rows = []
        for project in projects:
            pid = (project.get("id") or "").split("/")[-1]
            detail = client.get(f"{API}/{workspace}/{pid}", params={"api_key": key})
            if detail.status_code != 200:
                rows.append({"project": pid, "error": f"HTTP {detail.status_code}"})
                continue
            body = detail.json()
            kind = body.get("project", {}).get("type", "")
            for version in body.get("versions", []):
                model = version.get("model") or {}
                rows.append({
                    "project": pid,
                    "version": str(version.get("id", "")).split("/")[-1],
                    "task": TASK_HINT.get(kind, kind or "?"),
                    "model_type": model.get("name") or model.get("id") or "-",
                    "classes": len(body.get("project", {}).get("classes") or {}) or "?",
                    "images": version.get("images", "?"),
                    "map": (model.get("map") if model else None),
                    "trained": bool(model),
                })
        return rows


def preset_for(model_type: str) -> str:
    slug = (model_type or "").lower().replace(" ", "-")
    for needle, preset in sorted(PRESETS.items(), key=lambda kv: -len(kv[0])):
        if needle in slug:
            return preset
    return "(pick one in the New version dialog)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=os.environ.get("ROBOFLOW_WORKSPACE_ID",
                                                             "fiasco-gmbh"))
    parser.add_argument("--live", action="store_true", help="query Roboflow for versions")
    parser.add_argument("--download", metavar="PROJECT:VERSION",
                        help="download one version's weights, ready to upload")
    parser.add_argument("--out", default="./roboflow-weights")
    args = parser.parse_args()

    if args.download:
        project, _, version = args.download.partition(":")
        key = os.environ.get("ROBOFLOW_API_KEY")
        if not key or not version:
            print("need ROBOFLOW_API_KEY and PROJECT:VERSION", file=sys.stderr)
            return 2
        from roboflow import Roboflow

        target = Path(args.out)
        target.mkdir(parents=True, exist_ok=True)
        model = (Roboflow(api_key=key).workspace(args.workspace)
                 .project(project).version(int(version)).model)
        model.download()  # always lands as ./weights.pt
        produced = Path("weights.pt")
        if not produced.exists():
            print("Roboflow did not produce weights.pt - this version may be hosted-only",
                  file=sys.stderr)
            return 1
        final = target / f"{project}-v{version}.pt"
        produced.replace(final)
        print(f"{final}  ({final.stat().st_size / 1048576:.1f} MB)")
        print("upload it in Aperture: Models -> the model -> New version -> drop this file")
        return 0

    config = declared()
    print(f"workspace: {args.workspace}    (from {CLOUD})\n")
    print("projects the cloud app is wired to (these have trained versions):")
    for value, key in sorted(config["projects"].items()):
        print(f"   {value:34s} {key}")
    print("\nworkflows it calls on Roboflow's hosted inference:")
    for value, key in sorted(config["workflows"].items()):
        print(f"   {value:34s} {key}")
    print("\nother roboflow settings:")
    for value, key in sorted(config["other"].items()):
        print(f"   {value:34s} {key}")

    if not args.live:
        print("\npass --live with ROBOFLOW_API_KEY set to list every version, its model "
              "type and the Aperture preset that reads it.")
        return 0

    key = os.environ.get("ROBOFLOW_API_KEY")
    if not key:
        print("\nROBOFLOW_API_KEY is not set", file=sys.stderr)
        return 2

    rows = live(args.workspace, key)
    print(f"\n{'project':34s} {'ver':>4s} {'task':14s} {'model type':22s} "
          f"{'mAP':>6s}  aperture preset")
    print("-" * 118)
    for row in rows:
        if row.get("error"):
            print(f"{row['project']:34s} {row['error']}")
            continue
        m = row.get("map")
        print(f"{row['project']:34s} {row['version']:>4s} {row['task']:14s} "
              f"{str(row['model_type'])[:22]:22s} {(f'{m:.1f}' if isinstance(m, (int, float)) else '-'):>6s}"
              f"  {preset_for(str(row['model_type']))}")
    trained = [r for r in rows if r.get("trained")]
    print(f"\n{len(trained)} trained version(s) across {len({r['project'] for r in rows})} "
          f"project(s). Download one with:\n"
          f"   --download PROJECT:VERSION --out ./weights")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
