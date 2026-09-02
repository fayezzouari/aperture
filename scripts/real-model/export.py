"""Export the trained damage classifier to ONNX and record a torch reference run.

Uses the project's own build_damage_classifier and its exact eval transform, so
whatever Aperture computes later is compared against the real model's output on
the same pixels - not against a re-implementation.
"""
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

REPO = Path("/Users/fayez/projects/fiasco/cells-detection")
sys.path.insert(0, str(REPO))

from scripts.train_damage_classifier import (  # noqa: E402
    IMAGENET_MEAN, IMAGENET_STD, IMG_SIZE, build_damage_classifier,
)
from scripts.render_damage_classifier_dataset_cells import GROUPS  # noqa: E402

CKPT = REPO / "runs/variant_sweep_cells_v5/part-filled_damage-contour__convnext_small/best_model.pt"
BACKBONE = "convnext_small"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
TEST_DIR = REPO / "data/cls-test"

model = build_damage_classifier(BACKBONE, len(GROUPS), dropout=0.0)
state = torch.load(CKPT, map_location="cpu", weights_only=False)
if isinstance(state, dict) and "model" in state and not any(k.startswith("backbone") for k in state):
    state = state["model"]
if isinstance(state, dict) and "state_dict" in state:
    state = state["state_dict"]
# strict: a silently randomly-initialised head would make this whole comparison
# meaningless, so refuse anything that does not match exactly
model.load_state_dict(state, strict=True)
print(f"loaded {CKPT.parent.name} strictly - every weight matched")
model.eval()

# The dataset's eval transform, verbatim: resize to a square (a stretch, not a
# centre crop) then ImageNet normalise.
from torchvision import transforms  # noqa: E402

tf = transforms.Compose([
    transforms.Resize((IMG_SIZE, IMG_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
])

images = sorted(p for p in TEST_DIR.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
print(f"{len(images)} test images in {TEST_DIR}")

# a plausible aux vector: the damage box centred, half the crop's size
aux = torch.tensor([[0.5, 0.5, 0.5, 0.5]], dtype=torch.float32)

reference = []
for path in images:
    tensor = tf(Image.open(path).convert("RGB")).unsqueeze(0)
    with torch.inference_mode():
        logits = model(tensor, aux)
    probs = torch.sigmoid(logits)[0].numpy()
    reference.append({
        "file": path.name,
        "probs": [round(float(v), 6) for v in probs],
        "above_0.5": [GROUPS[i] for i in np.flatnonzero(probs >= 0.5)],
        "top": GROUPS[int(probs.argmax())],
        "top_prob": round(float(probs.max()), 6),
    })

onnx_path = OUT / "damage-classifier.onnx"
torch.onnx.export(
    model,
    (torch.zeros(1, 3, IMG_SIZE, IMG_SIZE), aux),
    str(onnx_path),
    input_names=["images", "aux"],
    output_names=["logits"],
    dynamic_axes={"images": {0: "batch"}, "aux": {0: "batch"}, "logits": {0: "batch"}},
    opset_version=17,
    dynamo=False,
)
print(f"exported {onnx_path} ({onnx_path.stat().st_size / 1e6:.1f} MB)")

(OUT / "reference.json").write_text(json.dumps({
    "checkpoint": str(CKPT),
    "groups": GROUPS,
    "img_size": IMG_SIZE,
    "mean": IMAGENET_MEAN,
    "std": IMAGENET_STD,
    "aux": aux[0].tolist(),
    "results": reference,
}, indent=2))
print(f"wrote reference for {len(reference)} images")
for row in reference[:5]:
    print(f"  {row['file']:24s} top={row['top']} {row['top_prob']:.3f}  above 0.5: {row['above_0.5']}")
