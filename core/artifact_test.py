"""What did someone upload, and can anything serve it?

Runs against whatever real checkpoints are available next door, plus ONNX files
built on the spot, so the matrix is meaningful without shipping fixtures.

    uv run --with numpy --with pillow --with pydantic --with onnx --with-editable . \
        python artifact_test.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

from aperture_core.artifacts import CONVERTIBLE, SERVABLE, WEIGHTS_ONLY, inspect_artifact

CELLS = "/Users/fayez/projects/fiasco/cells-detection"

# (path, expected kind, expected usability, expected family, expected preset)
REAL = [
    (f"{CELLS}/yolo11l-seg.pt", "ultralytics", CONVERTIBLE, "yolo", "yolov8-seg"),
    # RF-DETR weights carry no graph either, but rfdetr rebuilds the model from
    # the layer shapes - so a runtime carrying that package serves them as they
    # are, and converting to ONNX is the alternative rather than the entry fee
    (f"{CELLS}/rf-detr-seg-preview.pt", "state_dict", SERVABLE, "rf-detr", "rf-detr-seg"),
    (f"{CELLS}/state_dict.pt", "state_dict", WEIGHTS_ONLY, "yolo", "yolov8"),
]

failed = skipped = 0
work = tempfile.mkdtemp(prefix="artifact-test-")

from make_onnx import build_classifier, build_detector  # noqa: E402

built = [
    (build_detector(os.path.join(work, "detector.onnx"), classes=4), "onnx", SERVABLE, None, None),
    (build_classifier(os.path.join(work, "classifier.onnx"), classes=3), "onnx", SERVABLE, None, None),
]

for path, kind, usability, family, preset in built + REAL:
    if not os.path.exists(path):
        print(f"SKIP  {os.path.basename(path)} (not present on this machine)")
        skipped += 1
        continue
    info = inspect_artifact(path)
    ok = info.kind == kind and info.usability == usability
    if family:
        ok = ok and info.family == family
    if preset:
        ok = ok and info.suggested_preset == preset
    failed += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {os.path.basename(path):26s} "
          f"{info.kind}/{info.usability} family={info.family} preset={info.suggested_preset}"
          + ("" if ok else f"   expected {kind}/{usability} {family} {preset}"))

# the guarantees that matter: nothing without a graph is ever called servable,
# and every such verdict comes with something the reader can act on
for path, _, usability, _, _ in REAL:
    if not os.path.exists(path) or usability == SERVABLE:
        continue
    info = inspect_artifact(path)
    ok = not info.servable and bool(info.next_step)
    if usability == WEIGHTS_ONLY:
        ok = ok and bool(info.export_snippet)
    failed += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {os.path.basename(path):26s} "
          f"{'refused' if usability == WEIGHTS_ONLY else 'offered a conversion'} "
          "with a next step")

print(f"\n{'all checks passed' if not failed else f'{failed} failed'}"
      + (f", {skipped} skipped" if skipped else ""))
raise SystemExit(1 if failed else 0)
