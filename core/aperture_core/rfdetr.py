"""Rebuilding an RF-DETR checkpoint from the weights alone.

An RF-DETR checkpoint is a bare `state_dict`: tensors, no graph, no class. The
architecture that gives those tensors meaning has to come from somewhere, and
the only thing shipped alongside them is their own shapes - so this module reads
the architecture back out of the shapes.

Getting this *complete* matters more than it looks. RF-DETR's stock variants
carry defaults (4 decoder layers, 200 queries per group) that a fine-tuned
checkpoint routinely disagrees with, and the package's loader does not treat the
disagreement as an error: unmatched decoder layers are dropped and an oversized
query bank is flat-sliced, both with a warning and a model that still runs. The
result serves, scores lower, and looks like a threshold problem. Every parameter
recovered here is one the loader would otherwise paper over:

    patch_size    the patch-embedding convolution's kernel
    resolution    sqrt(position embeddings - 1) * patch_size
    num_classes   the classification head's width, less the background slot
    dec_layers    the highest decoder layer index present, plus one
    num_queries   the query bank's height divided by the variant's group count
    segmentation  whether a mask head is present at all

`geometry_from_state_dict` is pure - shapes in, numbers out - so it runs in the
API, the converter and the runtime alike. `build` needs torch and the `rfdetr`
package and therefore only runs where those are installed.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("aperture.rfdetr")

_DECODER_LAYER = re.compile(r"transformer\.decoder\.layers\.(\d+)\.")

# Seg checkpoints only load into a Seg class and vice versa; within each family
# the variants differ by backbone size, which the explicit patch_size and
# resolution override anyway.
SEG_VARIANTS = ["RFDETRSegPreview", "RFDETRSegSmall", "RFDETRSegMedium", "RFDETRSegLarge"]
DET_VARIANTS = ["RFDETRBase", "RFDETRSmall", "RFDETRMedium", "RFDETRLarge", "RFDETRNano"]


def tensors_of(state: dict) -> dict:
    """The tensor map, whether the checkpoint nests it under "model" or not."""
    return state.get("model", state) if isinstance(state, dict) else {}


def geometry_from_state_dict(state: dict, *, group_detr: int | None = None) -> dict:
    """Recover the training geometry from the weights themselves.

    `group_detr` is the one number the shapes cannot settle on their own: the
    query bank holds `num_queries * group_detr` rows and only their product is
    visible. Pass the variant's own default (see `group_detr_default`); without
    it `num_queries` is left out rather than guessed.
    """
    tensors = tensors_of(state)
    projection = next(
        (value for key, value in tensors.items()
         if "patch_embeddings.projection.weight" in key), None
    )
    positions = next(
        (value for key, value in tensors.items()
         if key.endswith("embeddings.position_embeddings")), None
    )
    head_width = max(
        (tuple(value.shape)[0] for key, value in tensors.items()
         if "class_embed" in key and key.endswith("weight")),
        default=None,
    )
    layer_indexes = [
        int(match.group(1))
        for key in tensors
        for match in [_DECODER_LAYER.search(key)]
        if match
    ]
    query_bank = next(
        (value for key, value in tensors.items()
         if key in ("refpoint_embed.weight", "query_feat.weight")), None
    )

    detected: dict = {"segmentation": any("segmentation_head" in key for key in tensors)}
    if projection is not None:
        detected["patch_size"] = int(projection.shape[-1])
        detected["backbone_dim"] = int(projection.shape[0])
    if positions is not None and "patch_size" in detected:
        grid = int(round((int(positions.shape[1]) - 1) ** 0.5))
        detected["grid"] = grid
        detected["resolution"] = grid * detected["patch_size"]
    if head_width:
        # the head carries one slot per class plus a background slot
        detected["head_width"] = int(head_width)
        detected["num_classes"] = int(head_width) - 1
    if layer_indexes:
        detected["dec_layers"] = max(layer_indexes) + 1
    if query_bank is not None:
        total = int(query_bank.shape[0])
        detected["total_queries"] = total
        if group_detr and total % group_detr == 0:
            detected["num_queries"] = total // group_detr
    return detected


def group_detr_default(variant: str) -> int | None:
    """The query-group count a variant is configured with, or None if unreadable.

    Read from the package rather than hard-coded: it is a property of the
    variant's own config, and a version bump that changes it should change this
    with it.
    """
    try:
        from rfdetr import config as rfdetr_config
    except ImportError:
        return None
    for name in (f"{variant}Config", f"{variant}Config".replace("RFDETR", "RFDETR")):
        cls = getattr(rfdetr_config, name, None)
        fields = getattr(cls, "model_fields", None)
        if fields and "group_detr" in fields:
            value = fields["group_detr"].default
            return int(value) if isinstance(value, int) else None
    return None


def rebuild_kwargs(detected: dict) -> dict:
    """The constructor arguments that make a stock variant match the checkpoint.

    Only what was actually recovered is passed: an argument the shapes did not
    prove is worse than the variant's own default, because it would be a guess
    presented to the loader as a fact.
    """
    wanted = ("patch_size", "resolution", "num_classes", "dec_layers", "num_queries")
    return {key: detected[key] for key in wanted if key in detected}


def build(path: str, *, device: str = "cpu", detected: dict | None = None):
    """Rebuild the checkpoint into a runnable RF-DETR and return (model, variant, geometry).

    Tries each variant of the right family in turn. `num_queries` depends on the
    variant's group count, so it is recovered per candidate rather than once up
    front.

    Raises RuntimeError with what was tried when nothing accepts the weights.
    """
    import rfdetr
    import torch

    state = torch.load(path, map_location="cpu", weights_only=False)
    base = detected or geometry_from_state_dict(state)

    missing = [key for key in ("patch_size", "resolution", "num_classes") if key not in base]
    if missing:
        raise RuntimeError(
            "could not read " + ", ".join(missing) + " out of these weights, so the "
            "architecture cannot be rebuilt. Export ONNX from your RF-DETR training code."
        )

    candidates = SEG_VARIANTS if base.get("segmentation") else DET_VARIANTS
    errors: list[str] = []
    for name in candidates:
        factory = getattr(rfdetr, name, None)
        if factory is None:
            continue
        geometry = dict(base)
        groups = group_detr_default(name)
        if groups:
            geometry.update(geometry_from_state_dict(state, group_detr=groups))
        kwargs = rebuild_kwargs(geometry)
        try:
            model = factory(pretrain_weights=path, device=device, **kwargs)
        except Exception as exc:
            errors.append(f"{name}: {str(exc)[:160]}")
            continue
        unconsumed = _unconsumed(model, state)
        if unconsumed:
            # A partial load is the failure this module exists to prevent, so it
            # is reported rather than served: the model would run and quietly
            # score lower than the weights deserve.
            errors.append(f"{name}: {unconsumed} checkpoint tensor(s) unused")
            continue
        log.info("rebuilt rf-detr from the weights",
                 extra={"variant": name, **{k: v for k, v in kwargs.items()}})
        return model, name, geometry

    raise RuntimeError(
        "no RF-DETR variant loaded these weights completely. Tried: " + " | ".join(errors[:4])
    )


def _unconsumed(model, state: dict) -> int:
    """How many checkpoint tensors the rebuilt model has no home for."""
    module = inner_module(model)
    if module is None:
        return 0
    built = module.state_dict()
    return sum(1 for key, value in tensors_of(state).items()
               if key not in built or tuple(built[key].shape) != tuple(value.shape))


def inner_module(model):
    """The `nn.Module` inside RF-DETR's wrapper, whose forward returns the head outputs."""
    context = getattr(model, "model", None)
    return getattr(context, "model", None) if context is not None else None


# RF-DETR's head answers as a dict; `rf-detr` presets index outputs positionally
# (`output_index: {boxes: 0, logits: 1, masks: 2}`), so the order is fixed here
# rather than left to dict iteration.
OUTPUT_ORDER = ("pred_boxes", "pred_logits", "pred_masks")
