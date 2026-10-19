"""Seed a demo instance: two models, several versions, tags, deployments, traffic.

Run against a fresh instance to have something to look at:

    ./scripts/seed-demo.sh
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_onnx import build_classifier, build_detector, sample_image  # noqa: E402

BASE = os.environ.get("APERTURE_URL", "http://localhost:8080")
EMAIL = os.environ.get("AP_BOOTSTRAP_ADMIN_EMAIL", "admin@localhost")
PASSWORD = os.environ.get("AP_BOOTSTRAP_ADMIN_PASSWORD", "aperture")

PART_CLASSES = ["bumper", "door", "fender", "headlight"]
DAMAGE_CLASSES = ["none", "scratch", "dent", "crack", "shattered"]


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=180.0)
    login = client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    login.raise_for_status()
    client.headers.update({"authorization": f"Bearer {login.json()['access_token']}"})
    # everything below lands in one workspace; a rerun finds it already there
    workspace = client.post("/v1/workspaces", json={
        "slug": "demo", "name": "Demo", "description": "Seeded by scripts/seed_demo.py.",
    })
    if workspace.status_code not in (201, 409):
        print(f"could not create the demo workspace: {workspace.text[:200]}")
        return 1
    client.headers.update({"x-aperture-workspace": "demo"})
    print(f"signed in to {BASE} as {EMAIL}")

    workdir = tempfile.mkdtemp(prefix="aperture-seed-")
    image_path = sample_image(os.path.join(workdir, "sample.jpg"))
    with open(image_path, "rb") as fh:
        image = fh.read()

    detector = seed_model(
        client, workdir, image,
        slug="part-detection", name="Part detection", task="detection",
        preset="yolov11", classes=PART_CLASSES, owner="perception",
        description="Locates body panels on a damage photo. Front bumper cells dataset.",
        versions=[
            {"changelog": "Baseline YOLO11-s export.", "metrics": {"mAP50": 0.683, "f1": 0.641},
             "dataset": "Part-Detection-V2-9", "boxes": None},
            {"changelog": "Retrained with the September cell crops.",
             "metrics": {"mAP50": 0.714, "f1": 0.678}, "dataset": "Part-Detection-V2-10",
             "boxes": [(300.0, 300.0, 220.0, 240.0, 1, 0.94), (520.0, 220.0, 140.0, 150.0, 3, 0.77)]},
        ],
    )

    classifier = seed_model(
        client, workdir, image,
        slug="damage-severity", name="Damage severity", task="classification",
        preset="resnet-imagenet", classes=DAMAGE_CLASSES, owner="perception",
        description="Grades a cropped panel. Feeds the estimate pipeline.",
        versions=[{"changelog": "ResNet-50 head, 5 classes.",
                   "metrics": {"accuracy": 0.881, "f1": 0.842},
                   "dataset": "Damage-Severity-V4", "boxes": None}],
        classifier=True,
    )

    for slug in (detector, classifier):
        if not slug:
            continue
        print(f"generating traffic on {slug}…")
        for _ in range(24):
            client.post(f"/v1/models/{slug}/production/predict",
                        files={"images": ("sample.jpg", image, "image/jpeg")})
        # a handful of failures, so the explorer and the error panels have content
        for _ in range(3):
            client.post(f"/v1/models/{slug}/production/predict",
                        files={"images": ("broken.jpg", b"not-an-image")})
        client.post(f"/v1/models/{slug}/999/predict",
                    files={"images": ("sample.jpg", image)})

    key = client.post("/v1/keys", json={
        "name": "claims-web", "scopes": ["*"], "rate_limit_rpm": 600,
    })
    if key.status_code == 201:
        print(f"\nAPI key for the demo consumer: {key.json()['key']}")

    print(f"\nDone. Open {BASE} and sign in as {EMAIL} / {PASSWORD}")
    return 0


def seed_model(client: httpx.Client, workdir: str, image: bytes, *, slug: str, name: str,
               task: str, preset: str, classes: list[str], owner: str, description: str,
               versions: list[dict], classifier: bool = False) -> str | None:
    existing = client.get(f"/v1/models/{slug}")
    if existing.status_code == 200:
        print(f"{slug} already exists, leaving it alone")
        return slug

    created = client.post("/v1/models", json={
        "slug": slug, "name": name, "task": task, "owner": owner, "description": description,
        "labels": ["demo"],
    })
    if created.status_code != 201:
        print(f"could not create {slug}: {created.text[:200]}")
        return None
    print(f"created model {slug}")

    for index, spec in enumerate(versions, start=1):
        payload = {
            "artifact_format": "onnx",
            "changelog": spec["changelog"],
            "metrics": spec["metrics"],
            "dataset_version": spec["dataset"],
            "training_run_id": f"{slug}-run-{index}",
            "git_commit": f"{random.getrandbits(64):016x}",
        }
        if index == 1:
            payload["preset"] = preset
        else:
            payload["inherit_from_version"] = index - 1
        version = client.post(f"/v1/models/{slug}/versions", json=payload)
        version.raise_for_status()
        number = version.json()["version"]

        config = dict(version.json()["config"])
        config["classes"] = classes
        client.patch(f"/v1/models/{slug}/versions/{number}", json={"config": config})

        path = os.path.join(workdir, f"{slug}-v{number}.onnx")
        if classifier:
            build_classifier(path, classes=len(classes), winner=2)
        else:
            build_detector(path, classes=len(classes), boxes=spec["boxes"])
        with open(path, "rb") as fh:
            client.post(f"/v1/models/{slug}/versions/{number}/weights",
                        files={"file": (os.path.basename(path), fh.read())})

        for attempt in range(10):
            validated = client.post(f"/v1/models/{slug}/versions/{number}/validate",
                                    files={"sample": ("sample.jpg", image, "image/jpeg")})
            if validated.status_code == 200 and \
                    validated.json()["validation"].get("status") == "passed":
                break
            time.sleep(4)
        client.post(f"/v1/models/{slug}/versions/{number}/publish")
        print(f"  v{number} uploaded, validated and published")

    latest = versions and len(versions)
    client.post(f"/v1/models/{slug}/deployments", json={
        "version": latest, "replicas": 1, "max_batch_size": 8, "max_wait_ms": 10,
    })
    client.put(f"/v1/models/{slug}/tags", json={"name": "production", "version": latest})
    if latest > 1:
        client.put(f"/v1/models/{slug}/tags", json={"name": "staging", "version": latest - 1})
    print(f"  deployed v{latest} and pointed 'production' at it")

    for attempt in range(40):
        deployments = client.get(f"/v1/models/{slug}/deployments").json()
        if any(d["replicas_ready"] >= 1 for d in deployments):
            break
        time.sleep(3)
    return slug


if __name__ == "__main__":
    sys.exit(main())
