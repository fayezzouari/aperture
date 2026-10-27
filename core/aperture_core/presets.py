"""R1.5 - config presets for architecture families we actually deploy.

A preset is only a *starting point*: it is materialised into the version's own
config at upload time and then edited in the UI. Nothing reads the preset again
after publish, so changing a preset here can never alter a served model.
"""

from __future__ import annotations

from typing import Any

from aperture_core.configspec import ModelConfig

COCO80 = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush",
]

COCO_KEYPOINTS = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder",
    "right_shoulder", "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip",
    "right_hip", "left_knee", "right_knee", "left_ankle", "right_ankle",
]

# PaddleOCR en_dict, abridged to the printable ASCII set it shares with ppocr_keys_v1.
PPOCR_EN_CHARSET = list(
    "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ "
)

IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]
CLIP_MEAN = [0.48145466, 0.4578275, 0.40821073]
CLIP_STD = [0.26862954, 0.26130258, 0.27577711]


def _cfg(**kw: Any) -> dict[str, Any]:
    return kw


PRESETS: dict[str, dict[str, Any]] = {
    # ---------------------------------------------------------------- detection
    "yolov5": {
        "label": "YOLOv5 (Ultralytics)",
        "family": "YOLO",
        "task": "detection",
        "description": "Anchor-based head, output (1, N, 5+nc) with an objectness column.",
        "config": _cfg(
            task="detection",
            backend="onnx",
            input={"width": 640, "height": 640, "layout": "NCHW", "dtype": "float32"},
            preprocess={
                "resize": {"mode": "letterbox", "width": 640, "height": 640, "pad_value": 114},
                "normalize": {"scale": 1 / 255.0, "mean": [0, 0, 0], "std": [1, 1, 1]},
            },
            postprocess={
                "decoder": "yolo_v5",
                "box_format": "cxcywh",
                "score_threshold": 0.25,
                "iou_threshold": 0.45,
                "nms": True,
            },
            classes=COCO80,
        ),
    },
    "yolov8": {
        "label": "YOLOv8 / v9 / v10 (Ultralytics)",
        "family": "YOLO",
        "task": "detection",
        "description": "Anchor-free head, output (1, 4+nc, N), no objectness column.",
        "config": _cfg(
            task="detection",
            backend="onnx",
            input={"width": 640, "height": 640},
            preprocess={
                "resize": {"mode": "letterbox", "width": 640, "height": 640, "pad_value": 114},
                "normalize": {"scale": 1 / 255.0, "mean": [0, 0, 0], "std": [1, 1, 1]},
            },
            postprocess={
                "decoder": "yolo_v8",
                "box_format": "cxcywh",
                "score_threshold": 0.25,
                "iou_threshold": 0.45,
            },
            classes=COCO80,
        ),
    },
    "yolov11": {
        "label": "YOLO11 (Ultralytics)",
        "family": "YOLO",
        "task": "detection",
        "description": "Same decode path as v8; separate preset so version history reads true.",
        "config": _cfg(
            task="detection",
            backend="onnx",
            input={"width": 640, "height": 640},
            preprocess={
                "resize": {"mode": "letterbox", "width": 640, "height": 640, "pad_value": 114},
                "normalize": {"scale": 1 / 255.0, "mean": [0, 0, 0], "std": [1, 1, 1]},
            },
            postprocess={
                "decoder": "yolo_v8",
                "box_format": "cxcywh",
                "score_threshold": 0.25,
                "iou_threshold": 0.45,
            },
            classes=COCO80,
        ),
    },
    "rf-detr": {
        "label": "RF-DETR (Roboflow)",
        "family": "DETR",
        "task": "detection",
        "description": "Query-based head: sigmoid logits + normalised cxcywh boxes, no NMS.",
        "config": _cfg(
            task="detection",
            backend="onnx",
            input={"width": 560, "height": 560},
            preprocess={
                "resize": {"mode": "stretch", "width": 560, "height": 560},
                "normalize": {"scale": 1 / 255.0, "mean": IMAGENET_MEAN, "std": IMAGENET_STD},
            },
            postprocess={
                "decoder": "detr",
                "box_format": "cxcywh",
                "normalized_boxes": True,
                "sigmoid_logits": True,
                "nms": False,
                "score_threshold": 0.5,
                "top_k": 300,
                "output_index": {"boxes": 0, "logits": 1},
            },
            classes=COCO80,
        ),
    },
    "rf-detr-seg": {
        "label": "RF-DETR segmentation (Roboflow)",
        "family": "DETR",
        "task": "segmentation",
        "description": "Query-based head with one mask per query: dets, labels, masks.",
        "config": _cfg(
            task="segmentation",
            backend="onnx",
            input={"width": 768, "height": 768,
                   "input_names": ["input"], "output_names": ["dets", "labels", "masks"]},
            preprocess={
                "resize": {"mode": "stretch", "width": 768, "height": 768},
                "normalize": {"scale": 1 / 255.0, "mean": IMAGENET_MEAN, "std": IMAGENET_STD},
            },
            postprocess={
                "decoder": "detr_seg",
                "box_format": "cxcywh",
                "normalized_boxes": True,
                "sigmoid_logits": True,
                "nms": False,
                "score_threshold": 0.5,
                "top_k": 200,
                "max_detections": 200,
                "mask_format": "rle",
                "mask_threshold": 0.0,
                "output_index": {"boxes": 0, "logits": 1, "masks": 2},
            },
            classes=COCO80,
        ),
    },
    # ----------------------------------------------------------- classification
    "resnet-imagenet": {
        "label": "ResNet (ImageNet)",
        "family": "ResNet",
        "task": "classification",
        "description": "Resize shortest side 256, centre crop 224, ImageNet mean/std.",
        "config": _cfg(
            task="classification",
            backend="onnx",
            input={"width": 224, "height": 224},
            preprocess={
                "resize": {
                    "mode": "center_crop",
                    "width": 224,
                    "height": 224,
                    "resize_side": 256,
                    "interpolation": "bilinear",
                },
                "normalize": {"scale": 1 / 255.0, "mean": IMAGENET_MEAN, "std": IMAGENET_STD},
            },
            postprocess={"decoder": "classifier_softmax", "top_k": 5, "score_threshold": 0.0},
        ),
    },
    "efficientnet": {
        "label": "EfficientNet (ImageNet)",
        "family": "EfficientNet",
        "task": "classification",
        "description": "B0 geometry - bump input to 240/260/300/380 for B1-B4.",
        "config": _cfg(
            task="classification",
            backend="onnx",
            input={"width": 224, "height": 224},
            preprocess={
                "resize": {"mode": "center_crop", "width": 224, "height": 224, "resize_side": 256},
                "normalize": {"scale": 1 / 255.0, "mean": IMAGENET_MEAN, "std": IMAGENET_STD},
            },
            postprocess={"decoder": "classifier_softmax", "top_k": 5},
        ),
    },
    # ------------------------------------------------------------- segmentation
    "yolov8-seg": {
        "label": "YOLOv8 / YOLO11 segmentation",
        "family": "YOLO",
        "task": "segmentation",
        "description": "Detection head plus 32 mask coefficients combined with a proto tensor.",
        "config": _cfg(
            task="segmentation",
            backend="onnx",
            input={"width": 640, "height": 640},
            preprocess={
                "resize": {"mode": "letterbox", "width": 640, "height": 640, "pad_value": 114},
                "normalize": {"scale": 1 / 255.0, "mean": [0, 0, 0], "std": [1, 1, 1]},
            },
            postprocess={
                "decoder": "yolo_seg",
                "score_threshold": 0.25,
                "iou_threshold": 0.45,
                "mask_format": "rle",
                "mask_threshold": 0.5,
                "output_index": {"boxes": 0, "proto": 1},
            },
            classes=COCO80,
        ),
    },
    "sam": {
        "label": "SAM / promptable mask decoder",
        "family": "SAM",
        "task": "segmentation",
        "description": "Single-channel mask logits at model resolution. Prompts go in extra.",
        "config": _cfg(
            task="segmentation",
            backend="onnx",
            input={"width": 1024, "height": 1024},
            preprocess={
                "resize": {"mode": "letterbox", "width": 1024, "height": 1024, "pad_value": 0},
                "normalize": {"scale": 1 / 255.0, "mean": IMAGENET_MEAN, "std": IMAGENET_STD},
            },
            postprocess={"decoder": "binary_mask", "mask_format": "polygon", "mask_threshold": 0.0},
            classes=["mask"],
        ),
    },
    # ------------------------------------------------------------------ keypoints
    "yolov8-pose": {
        "label": "YOLOv8 / YOLO11 pose",
        "family": "YOLO",
        "task": "keypoints",
        "description": "Detection head plus 17 xy+conf keypoint triplets.",
        "config": _cfg(
            task="keypoints",
            backend="onnx",
            input={"width": 640, "height": 640},
            preprocess={
                "resize": {"mode": "letterbox", "width": 640, "height": 640, "pad_value": 114},
                "normalize": {"scale": 1 / 255.0, "mean": [0, 0, 0], "std": [1, 1, 1]},
            },
            postprocess={
                "decoder": "yolo_pose",
                "score_threshold": 0.25,
                "iou_threshold": 0.45,
                "keypoint_names": COCO_KEYPOINTS,
            },
            classes=["person"],
        ),
    },
    # ------------------------------------------------------------------ embedding
    "clip-image": {
        "label": "CLIP image encoder",
        "family": "CLIP",
        "task": "embedding",
        "description": "ViT-B/32 geometry, CLIP mean/std, L2-normalised output vector.",
        "config": _cfg(
            task="embedding",
            backend="onnx",
            input={"width": 224, "height": 224},
            preprocess={
                "resize": {
                    "mode": "center_crop",
                    "width": 224,
                    "height": 224,
                    "resize_side": 224,
                    "interpolation": "bicubic",
                },
                "normalize": {"scale": 1 / 255.0, "mean": CLIP_MEAN, "std": CLIP_STD},
            },
            postprocess={"decoder": "embedding", "normalize_embedding": True},
        ),
    },
    # ----------------------------------------------------------------------- ocr
    "paddleocr-rec": {
        "label": "PaddleOCR recognition (CRNN/CTC)",
        "family": "PaddleOCR",
        "task": "ocr",
        "description": "Fixed height 48, width padded to 320, CTC greedy decode.",
        "config": _cfg(
            task="ocr",
            backend="onnx",
            input={"width": 320, "height": 48, "dynamic_shape": True},
            preprocess={
                # mid-grey, which normalises to ~0 - what PaddleOCR pads with. Black
                # (-1) around a centred word reads as strokes: "JOINT" came back
                # as "cYanmaGnntTlow".
                "resize": {"mode": "letterbox", "width": 320, "height": 48, "pad_value": 127},
                "normalize": {"scale": 1 / 255.0, "mean": [0.5, 0.5, 0.5], "std": [0.5, 0.5, 0.5]},
            },
            postprocess={"decoder": "ctc_greedy", "charset": PPOCR_EN_CHARSET},
        ),
    },
    "custom": {
        "label": "Custom (blank config)",
        "family": "Custom",
        "task": "detection",
        "description": "Start from nothing. Pair with a pre/post-processing hook if needed.",
        "config": _cfg(task="detection", backend="onnx", postprocess={"decoder": "passthrough"}),
    },
}


def preset_config(name: str) -> ModelConfig:
    """Materialise a preset into a validated, editable config."""
    if name not in PRESETS:
        raise KeyError(f"unknown preset '{name}'")
    return ModelConfig.model_validate(PRESETS[name]["config"])


def preset_summaries() -> list[dict[str, Any]]:
    """What the upload wizard renders in its preset picker."""
    return [
        {
            "name": name,
            "label": p["label"],
            "family": p["family"],
            "task": p["task"],
            "description": p["description"],
        }
        for name, p in PRESETS.items()
    ]
