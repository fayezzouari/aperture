# Verifying against a real trained model

The suites in `scripts/` and the two `smoke_test.py` files prove the plumbing
with synthetic models. These four scripts prove the *preprocessing*, against a
real checkpoint from the `cells-detection` repo next door.

They hard-code paths into that repo (`/Users/fayez/projects/fiasco/cells-detection`),
so they live here as a record of the check and a starting point for the next one
rather than as part of `make test`.

| Script | What it does |
|---|---|
| `export.py` | Loads `runs/variant_sweep_cells_v5/part-filled_damage-contour__convnext_small/best_model.pt` with the project's own `build_damage_classifier` (strictly — a randomly initialised head would make the comparison meaningless), exports ONNX with both inputs, and records PyTorch's output for all 22 images in `data/cls-test` as `reference.json`. |
| `verify.py` | Uploads that ONNX to Aperture in chunks, validates, deploys, then runs the same 22 images through the API and compares every probability against `reference.json`. |
| `workflow_test.py` | Builds the real two-stage pipeline as a workflow (detect panels → crop → box geometry → classify damage) and asserts the fan-out, the per-node timings and the provenance of each result. |
| `roboflow_format.py` | Asserts `?format=roboflow` matches Roboflow's documented response shape field by field, on the real models. |

Run them in order against a live stack:

```bash
cd ../cells-detection && uv run --no-sync --with onnx python \
  ../aperture/scripts/real-model/export.py /tmp/aperture-real
cd ../aperture
uv run --no-project --with httpx python scripts/real-model/verify.py
uv run --no-project --with httpx python scripts/real-model/workflow_test.py
uv run --no-project --with httpx python scripts/real-model/roboflow_format.py
```

## What it found

Two real gaps, both fixed as a result:

1. **Per-request auxiliary inputs did not exist.** The classifier takes the crop
   *and* its box geometry; Aperture only supported inputs pinned at load time.
2. **Warm-up called the model without them**, so a multi-input model could never
   reach `ready` — it validated fine and then failed to deploy.

## The result

| Check | Outcome |
|---|---|
| Probabilities vs PyTorch, 16 groups × 22 images | largest deviation 5.0 × 10⁻⁶ |
| Groups crossing 0.5 | identical on every image |
| ConvNeXt-small on CPU | p50 272 ms, p95 470 ms |

That deviation is float32 rounding between torch and onnxruntime, not a
preprocessing difference — which is the point of stating the transform in the
version config (`stretch` to 224, ImageNet mean/std, `classifier_sigmoid`)
instead of leaving each caller to guess it.
