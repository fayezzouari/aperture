"""Raw output tensors -> the typed contract in results.py.

Every decoder here is pure numpy and pure function of (outputs, config,
geometry). That is deliberate: the same code path runs in the upload validator
(R1.7), in the playground preview (R6.3) and on the serving runtime, so what an
engineer sees while tuning a config is exactly what a product will get.
"""

from __future__ import annotations

import numpy as np

from aperture_core.configspec import Decoder, ModelConfig, Task
from aperture_core.images import Geometry
from aperture_core.masks import encode_mask
from aperture_core.results import (
    Classification,
    Detection,
    ImageResult,
    Keypoint,
    Mask,
    TextRegion,
)

Outputs = list[np.ndarray]


class DecodeError(ValueError):
    pass


# --------------------------------------------------------------------------- utils

def _pick(outputs: Outputs, cfg: ModelConfig, role: str, default_idx: int) -> np.ndarray:
    idx = cfg.postprocess.output_index.get(role, default_idx)
    if idx >= len(outputs):
        raise DecodeError(
            f"config expects output '{role}' at index {idx} but the model returned "
            f"{len(outputs)} output tensor(s)"
        )
    return outputs[idx]


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50, 50)))


def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    e = np.exp(x - x.max(axis=axis, keepdims=True))
    return e / e.sum(axis=axis, keepdims=True)


def _as_pred_last(arr: np.ndarray, expected_channels: int | None) -> np.ndarray:
    """Normalise a YOLO head to (N, C): heads ship as (C, N) or (N, C) by exporter."""
    if arr.ndim != 2:
        raise DecodeError(f"expected a 2D prediction tensor per image, got shape {arr.shape}")
    a, b = arr.shape
    if expected_channels is not None:
        if b == expected_channels:
            return arr
        if a == expected_channels:
            return arr.T
    # fall back to the shape heuristic: candidate count dwarfs channel count
    return arr.T if a < b else arr


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float, top_k: int) -> np.ndarray:
    """Greedy IoU NMS on xyxy boxes; returns kept indices, highest score first."""
    if boxes.size == 0:
        return np.empty(0, dtype=np.int64)
    order = np.argsort(-scores)[: max(top_k * 4, top_k)]
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    keep: list[int] = []
    while order.size and len(keep) < top_k:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        iou = inter / np.maximum(areas[i] + areas[rest] - inter, 1e-9)
        order = rest[iou <= iou_threshold]
    return np.asarray(keep, dtype=np.int64)


def batched_nms(boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray,
                iou_threshold: float, top_k: int, class_agnostic: bool) -> np.ndarray:
    if class_agnostic or boxes.size == 0:
        return nms(boxes, scores, iou_threshold, top_k)
    # offset per class so boxes of different classes can never suppress each other
    span = float(np.max(boxes) - np.min(boxes)) + 1.0 if boxes.size else 1.0
    shifted = boxes + (classes.astype(np.float32) * span)[:, None]
    return nms(shifted, scores, iou_threshold, top_k)


def _to_xyxy(boxes: np.ndarray, box_format: str) -> np.ndarray:
    if box_format == "xyxy":
        return boxes.astype(np.float32, copy=True)
    if box_format == "xywh":
        x, y, w, h = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        return np.stack([x, y, x + w, y + h], axis=1).astype(np.float32)
    cx, cy, w, h = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    return np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1).astype(np.float32)


def _rescale(boxes: np.ndarray, geom: Geometry, normalized: bool) -> np.ndarray:
    """Model-space boxes -> original-image pixels, undoing letterbox padding."""
    out = boxes.astype(np.float32, copy=True)
    if normalized:
        out[:, [0, 2]] *= geom.net_w
        out[:, [1, 3]] *= geom.net_h
    out[:, [0, 2]] = (out[:, [0, 2]] - geom.pad_x) / geom.scale_x
    out[:, [1, 3]] = (out[:, [1, 3]] - geom.pad_y) / geom.scale_y
    out[:, [0, 2]] = out[:, [0, 2]].clip(0, geom.orig_w)
    out[:, [1, 3]] = out[:, [1, 3]].clip(0, geom.orig_h)
    return out


def _detections(boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray,
                cfg: ModelConfig) -> list[Detection]:
    return [
        Detection(
            box=[round(float(v), 2) for v in box],
            score=round(float(score), 5),
            class_id=int(cid),
            class_name=cfg.class_name(int(cid)),
        )
        for box, score, cid in zip(boxes, scores, classes)
    ]


# ------------------------------------------------------------------------ decoders

def _decode_yolo(pred: np.ndarray, cfg: ModelConfig, geom: Geometry, *, v5: bool
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Returns (xyxy in original px, scores, class ids, kept-row indices)."""
    pp = cfg.postprocess
    nc = len(cfg.classes) or None
    expected = (4 + 1 + nc) if (v5 and nc) else (4 + nc if nc else None)
    p = _as_pred_last(pred, expected)

    if v5:
        obj = p[:, 4]
        cls = p[:, 5:]
        if cls.shape[1] == 0:
            raise DecodeError("yolo_v5 decoder needs at least one class column")
        cls_id = cls.argmax(axis=1)
        scores = obj * cls[np.arange(cls.shape[0]), cls_id]
    else:
        cls = p[:, 4:]
        if cls.shape[1] == 0:
            raise DecodeError("yolo_v8 decoder needs at least one class column")
        if pp.sigmoid_logits:
            cls = _sigmoid(cls)
        cls_id = cls.argmax(axis=1)
        scores = cls[np.arange(cls.shape[0]), cls_id]

    mask = scores >= pp.score_threshold
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return np.empty((0, 4), np.float32), np.empty(0, np.float32), np.empty(0, np.int64), idx

    boxes = _to_xyxy(p[idx, :4], pp.box_format)
    if pp.normalized_boxes:
        boxes[:, [0, 2]] *= geom.net_w
        boxes[:, [1, 3]] *= geom.net_h
    scores_f, cls_f = scores[idx], cls_id[idx]

    if pp.nms:
        keep = batched_nms(boxes, scores_f, cls_f, pp.iou_threshold,
                           min(pp.top_k, pp.max_detections), pp.class_agnostic_nms)
    else:
        keep = np.argsort(-scores_f)[: min(pp.top_k, pp.max_detections)]

    boxes = _rescale(boxes[keep], geom, normalized=False)
    return boxes, scores_f[keep], cls_f[keep], idx[keep]


def decode_detection(outputs: Outputs, cfg: ModelConfig, geoms: list[Geometry],
                     batch: int, v5: bool) -> list[ImageResult]:
    raw = _pick(outputs, cfg, "boxes", 0)
    results = []
    for i in range(batch):
        geom = geoms[i]
        boxes, scores, classes, _ = _decode_yolo(raw[i], cfg, geom, v5=v5)
        results.append(
            ImageResult(index=i, width=geom.orig_w, height=geom.orig_h,
                        detections=_detections(boxes, scores, classes, cfg))
        )
    return results


def _detr_select(raw: np.ndarray, cfg: ModelConfig, pp) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Which (query, class) pairs count as detections?

    The two DETR families disagree here, and picking the wrong rule silently
    changes what the model is reported to have said:

    * softmax DETR spends one distribution per query across classes plus a
      trailing "no object" slot, so one query means at most one detection and an
      argmax over the real classes is right.
    * sigmoid heads (Deformable DETR, RF-DETR) score every class independently.
      A query routinely clears the threshold on more than one class, so the
      reference decoders rank flattened (query, class) pairs and take the best.
      An argmax there drops detections and can name a different class than the
      model's own inference path does.

    Either way the class columns can be flanked by slots that are not classes.
    RF-DETR trains num_classes + 1 logits with the no-object slot last, and its
    ONNX exporter prepends a second dead slot (zero weights, bias -20), so a
    graph exported that way reads [no-class, classes..., no-object]. Keeping
    those columns lets a phantom class win, and counting from the wrong end
    shifts every class id - hence class_offset, and hence trusting the class
    list for the width rather than whatever the tensor happens to be.
    """
    count = len(cfg.classes)
    cap = max(1, min(pp.top_k, pp.max_detections))

    def trim(probs: np.ndarray) -> np.ndarray:
        skip = min(pp.class_offset, max(0, probs.shape[1] - 1))
        if count and probs.shape[1] >= skip + count:
            return probs[:, skip:skip + count]
        return probs[:, skip:]

    if pp.sigmoid_logits:
        probs = trim(_sigmoid(raw))
        flat = probs.reshape(-1)
        if flat.size > cap:
            order = np.argpartition(-flat, cap - 1)[:cap]
            order = order[np.argsort(-flat[order])]
        else:
            order = np.argsort(-flat)
        order = order[flat[order] >= pp.score_threshold]
        return order // probs.shape[1], order % probs.shape[1], flat[order]

    probs = trim(_softmax(raw))
    class_id = probs.argmax(axis=1)
    scores = probs[np.arange(probs.shape[0]), class_id]
    keep = np.flatnonzero(scores >= pp.score_threshold)
    keep = keep[np.argsort(-scores[keep])][:cap]
    return keep, class_id[keep], scores[keep]


def decode_detr(outputs: Outputs, cfg: ModelConfig, geoms: list[Geometry],
                batch: int) -> list[ImageResult]:
    pp = cfg.postprocess
    logits = _pick(outputs, cfg, "logits", 0)
    boxes_t = _pick(outputs, cfg, "boxes", 1)
    if logits.shape[-1] == 4 and boxes_t.shape[-1] != 4:
        # the config named the outputs the wrong way round: both tensors share the
        # query axis, so only the last one (4 box coordinates) tells them apart
        logits, boxes_t = boxes_t, logits
    results = []
    for i in range(batch):
        geom = geoms[i]
        queries, ci, sc = _detr_select(logits[i].astype(np.float32), cfg, pp)
        boxes = _to_xyxy(boxes_t[i][queries].astype(np.float32), pp.box_format)
        boxes = _rescale(boxes, geom, normalized=pp.normalized_boxes)
        if pp.nms and queries.size:
            sel = batched_nms(boxes, sc, ci, pp.iou_threshold,
                              pp.max_detections, pp.class_agnostic_nms)
            boxes, sc, ci = boxes[sel], sc[sel], ci[sel]
        results.append(
            ImageResult(index=i, width=geom.orig_w, height=geom.orig_h,
                        detections=_detections(boxes, sc, ci, cfg))
        )
    return results


def decode_detr_seg(outputs: Outputs, cfg: ModelConfig, geoms: list[Geometry],
                     batch: int) -> list[ImageResult]:
    """A DETR that also emits one mask per query (RF-DETR Seg exports this).

    Boxes and labels decode exactly as for plain DETR; the mask for a kept query
    is then taken from the mask tensor at its own index. There is no NMS and no
    mask-coefficient matrix - the head already produced one mask per query, so
    the only work is thresholding it and putting it back in image pixels.
    """
    pp = cfg.postprocess
    boxes_t = _pick(outputs, cfg, "boxes", 0)
    logits = _pick(outputs, cfg, "logits", 1)
    masks_t = _pick(outputs, cfg, "masks", 2)
    if logits.shape[-1] == 4 and boxes_t.shape[-1] != 4:
        logits, boxes_t = boxes_t, logits  # some exporters swap the two

    results: list[ImageResult] = []
    for index in range(batch):
        geom = geoms[index]
        queries, class_id, scores = _detr_select(logits[index].astype(np.float32), cfg, pp)
        boxes = _to_xyxy(boxes_t[index][queries].astype(np.float32), pp.box_format)
        boxes = _rescale(boxes, geom, normalized=pp.normalized_boxes)

        segments: list[Detection] = []
        for slot, query in enumerate(queries):
            mask = _mask_to_original(masks_t[index, query].astype(np.float32), geom,
                                    pp.mask_threshold)
            encoded = encode_mask(mask, pp.mask_format) if mask.any() else None
            segments.append(
                Detection(
                    box=[round(float(v), 2) for v in boxes[slot]],
                    score=round(float(scores[slot]), 5),
                    class_id=int(class_id[slot]),
                    class_name=cfg.class_name(int(class_id[slot])),
                    mask=Mask.model_validate(encoded) if encoded else None,
                )
            )
        results.append(ImageResult(index=index, width=geom.orig_w, height=geom.orig_h,
                                   segments=segments, detections=segments))
    return results


def decode_classification(outputs: Outputs, cfg: ModelConfig, geoms: list[Geometry],
                          batch: int, activation: str) -> list[ImageResult]:
    logits = _pick(outputs, cfg, "logits", 0)
    if logits.ndim > 2:
        logits = logits.reshape(logits.shape[0], -1)
    if activation == "sigmoid":
        probs = _sigmoid(logits)
    elif activation == "softmax":
        probs = _softmax(logits.astype(np.float32))
    else:  # a second softmax over probabilities flattens every score to ~1/classes
        probs = logits.astype(np.float32)
    pp = cfg.postprocess
    results = []
    for i in range(batch):
        row = probs[i]
        order = np.argsort(-row)[: pp.top_k]
        items = [
            Classification(class_id=int(c), class_name=cfg.class_name(int(c)),
                           score=round(float(row[c]), 5))
            for c in order if row[c] >= pp.score_threshold
        ]
        results.append(ImageResult(index=i, width=geoms[i].orig_w, height=geoms[i].orig_h,
                                   classifications=items))
    return results


def decode_embedding(outputs: Outputs, cfg: ModelConfig, geoms: list[Geometry],
                     batch: int) -> list[ImageResult]:
    emb = _pick(outputs, cfg, "embedding", 0)
    if emb.ndim > 2:
        emb = emb.reshape(emb.shape[0], -1)
    emb = emb.astype(np.float32)
    if cfg.postprocess.normalize_embedding:
        emb = emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-12)
    return [
        ImageResult(index=i, width=geoms[i].orig_w, height=geoms[i].orig_h,
                    embedding=[round(float(v), 6) for v in emb[i]],
                    embedding_dim=int(emb.shape[1]))
        for i in range(batch)
    ]


def decode_yolo_seg(outputs: Outputs, cfg: ModelConfig, geoms: list[Geometry],
                    batch: int) -> list[ImageResult]:
    pp = cfg.postprocess
    pred = _pick(outputs, cfg, "boxes", 0)
    proto = _pick(outputs, cfg, "proto", 1)
    nm = proto.shape[1]  # mask coefficient count, normally 32
    nc = len(cfg.classes) or None
    results = []
    for i in range(batch):
        geom = geoms[i]
        p = _as_pred_last(pred[i], (4 + nc + nm) if nc else None)
        det_part = p[:, : p.shape[1] - nm]
        coeff_part = p[:, p.shape[1] - nm:]

        sub = ModelConfig.model_validate(cfg.model_dump())
        boxes, scores, classes, kept = _decode_yolo(det_part, sub, geom, v5=False)

        segments: list[Detection] = []
        if kept.size:
            pr = proto[i].reshape(nm, -1).astype(np.float32)
            mh, mw = proto.shape[2], proto.shape[3]
            masks = _sigmoid(coeff_part[kept].astype(np.float32) @ pr).reshape(-1, mh, mw)
            for j in range(masks.shape[0]):
                full = _mask_to_original(masks[j], geom, pp.mask_threshold)
                x1, y1, x2, y2 = boxes[j]
                crop = np.zeros_like(full)
                xa, ya = int(max(0, np.floor(x1))), int(max(0, np.floor(y1)))
                xb, yb = int(min(geom.orig_w, np.ceil(x2))), int(min(geom.orig_h, np.ceil(y2)))
                crop[ya:yb, xa:xb] = full[ya:yb, xa:xb]  # confine the mask to its box
                enc = encode_mask(crop, pp.mask_format)
                segments.append(
                    Detection(
                        box=[round(float(v), 2) for v in boxes[j]],
                        score=round(float(scores[j]), 5),
                        class_id=int(classes[j]),
                        class_name=cfg.class_name(int(classes[j])),
                        mask=Mask.model_validate(enc) if enc else None,
                    )
                )
        results.append(ImageResult(index=i, width=geom.orig_w, height=geom.orig_h,
                                   segments=segments, detections=segments))
    return results


def _mask_to_original(mask: np.ndarray, geom: Geometry, threshold: float) -> np.ndarray:
    """Resample a model-space mask into original pixels, then threshold it.

    Mask heads work at a fraction of the input resolution - RF-DETR Seg emits
    192x192 for a 768 input - so a 1600-wide photo gets eight image pixels per
    mask cell. Sampling nearest and thresholding leaves that grid visible as
    stair-steps along every boundary, which is the mask cell size, not anything
    the model predicted. Interpolating the mask *before* the threshold puts the
    boundary where the model actually placed it, to the pixel.

    Interpolation happens in probability space, never on raw logits. A mask head
    is confident about background: cells next to an object sit at -20 while the
    object sits at +3, and averaging those pulls the boundary a long way inside
    the object - a one-cell blob loses 44 of its 45 pixels and can vanish
    outright, which reads as a model that returned no mask. Squashing first
    bounds every sample to [0, 1]; the comparison is unchanged because sigmoid is
    monotonic, so the threshold simply moves with it.

    Only the neighbourhood of the positive region is resampled: outside it every
    corner of the interpolation is below the threshold, so it cannot contribute.
    """
    mh, mw = mask.shape
    out = np.zeros((geom.orig_h, geom.orig_w), dtype=np.uint8)

    values, cut = mask, float(threshold)
    if float(mask.min()) < 0.0 or float(mask.max()) > 1.0:
        values = _sigmoid(mask)
        cut = float(_sigmoid(np.asarray(threshold, dtype=np.float32)))

    positive = values > cut
    if not positive.any():
        return out
    rows = np.flatnonzero(positive.any(axis=1))
    cols = np.flatnonzero(positive.any(axis=0))
    # one cell of slack: a pixel can only clear the threshold if one of its four
    # interpolation corners is positive, so this window bounds the whole result
    r0, r1 = max(0, rows[0] - 1), min(mh - 1, rows[-1] + 1)
    c0, c1 = max(0, cols[0] - 1), min(mw - 1, cols[-1] + 1)

    # pixel centres, mapped into mask coordinates
    ys = (np.arange(geom.orig_h, dtype=np.float32) + 0.5) * geom.scale_y + geom.pad_y
    xs = (np.arange(geom.orig_w, dtype=np.float32) + 0.5) * geom.scale_x + geom.pad_x
    ys = ys * (mh / geom.net_h) - 0.5
    xs = xs * (mw / geom.net_w) - 0.5

    yr = np.flatnonzero((ys >= r0) & (ys <= r1))
    xr = np.flatnonzero((xs >= c0) & (xs <= c1))
    if yr.size and xr.size:
        yv = np.clip(ys[yr], 0, mh - 1)
        xv = np.clip(xs[xr], 0, mw - 1)
        y0 = np.floor(yv).astype(np.int32)
        x0 = np.floor(xv).astype(np.int32)
        y1 = np.minimum(y0 + 1, mh - 1)
        x1 = np.minimum(x0 + 1, mw - 1)
        wy = (yv - y0).astype(np.float32)[:, None]
        wx = (xv - x0).astype(np.float32)[None, :]

        top = values[np.ix_(y0, x0)] * (1.0 - wx) + values[np.ix_(y0, x1)] * wx
        bottom = values[np.ix_(y1, x0)] * (1.0 - wx) + values[np.ix_(y1, x1)] * wx
        window = top * (1.0 - wy) + bottom * wy
        out[yr[0]:yr[-1] + 1, xr[0]:xr[-1] + 1] = (window > cut).astype(np.uint8)

    if out.any():
        return out
    # A mask thinner than the gap between pixel centres can fall through
    # interpolation entirely. The model did predict something there, so keep it
    # rather than reporting no mask at all.
    yi = np.clip((ys + 0.5).astype(np.int32), 0, mh - 1)
    xi = np.clip((xs + 0.5).astype(np.int32), 0, mw - 1)
    return positive[np.ix_(yi, xi)].astype(np.uint8)


def decode_binary_mask(outputs: Outputs, cfg: ModelConfig, geoms: list[Geometry],
                       batch: int) -> list[ImageResult]:
    pp = cfg.postprocess
    logits = _pick(outputs, cfg, "masks", 0).astype(np.float32)
    if logits.ndim == 3:
        logits = logits[:, None, ...]
    results = []
    for i in range(batch):
        geom = geoms[i]
        segs: list[Detection] = []
        for c in range(logits.shape[1]):
            m = _mask_to_original(logits[i, c], geom, pp.mask_threshold)
            if not m.any():
                continue
            ys, xs = np.nonzero(m)
            enc = encode_mask(m, pp.mask_format)
            segs.append(
                Detection(
                    box=[float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())],
                    score=1.0,
                    class_id=c,
                    class_name=cfg.class_name(c),
                    mask=Mask.model_validate(enc) if enc else None,
                )
            )
        results.append(ImageResult(index=i, width=geom.orig_w, height=geom.orig_h,
                                   segments=segs, detections=segs))
    return results


def decode_pose(outputs: Outputs, cfg: ModelConfig, geoms: list[Geometry],
                batch: int) -> list[ImageResult]:
    pp = cfg.postprocess
    pred = _pick(outputs, cfg, "boxes", 0)
    nkpt = len(pp.keypoint_names) or 17
    results = []
    for i in range(batch):
        geom = geoms[i]
        p = _as_pred_last(pred[i], 4 + 1 + nkpt * 3)
        det_part = p[:, : 4 + 1]
        kpt_part = p[:, 4 + 1:]
        boxes, scores, classes, kept = _decode_yolo(det_part, cfg, geom, v5=False)
        dets = []
        for j, row_idx in enumerate(kept):
            kpts = kpt_part[row_idx].reshape(-1, 3)
            points = []
            for k in range(min(nkpt, kpts.shape[0])):
                x, y = geom.to_original(float(kpts[k, 0]), float(kpts[k, 1]))
                points.append(
                    Keypoint(
                        name=pp.keypoint_names[k] if k < len(pp.keypoint_names) else None,
                        x=round(x, 2), y=round(y, 2), score=round(float(kpts[k, 2]), 4),
                    )
                )
            dets.append(
                Detection(box=[round(float(v), 2) for v in boxes[j]],
                          score=round(float(scores[j]), 5), class_id=int(classes[j]),
                          class_name=cfg.class_name(int(classes[j])), keypoints=points)
            )
        results.append(ImageResult(index=i, width=geom.orig_w, height=geom.orig_h,
                                   detections=dets))
    return results


def decode_ctc(outputs: Outputs, cfg: ModelConfig, geoms: list[Geometry],
               batch: int) -> list[ImageResult]:
    """Greedy CTC decode. Index 0 is the blank, matching PaddleOCR rec heads."""
    logits = _pick(outputs, cfg, "logits", 0).astype(np.float32)
    if logits.ndim != 3:
        raise DecodeError(f"ctc_greedy expects (B, T, C), got {logits.shape}")
    charset = cfg.postprocess.charset
    # The class axis is the one sized charset + blank. Guessing it from which
    # axis is larger fails exactly when the charset is wrong: a (1, 40, 438)
    # head read with a 95-character charset got transposed, and the decode
    # turned 438 "time steps" of noise into a paragraph instead of an error.
    classes = len(charset) + 1
    if logits.shape[1] == classes and logits.shape[2] != classes:
        logits = np.transpose(logits, (0, 2, 1))
    if logits.shape[2] != classes:
        raise DecodeError(
            f"the model scores {logits.shape[2]} classes per step but the charset has "
            f"{len(charset)} characters (+1 CTC blank = {classes}) - set "
            "postprocess.charset to the dictionary the model was trained with, in order"
        )
    # PaddleOCR exports end in a softmax; a second one flattens every score
    # towards 1/classes while leaving the argmax alone
    already = logits.min() >= 0 and np.allclose(logits.sum(axis=2), 1.0, atol=1e-3)
    probs = logits if already else _softmax(logits)
    results = []
    for i in range(batch):
        idx = probs[i].argmax(axis=1)
        conf = probs[i][np.arange(idx.size), idx]
        chars, scores, prev = [], [], -1
        for t, c in enumerate(idx):
            if c != prev and c != 0:
                pos = int(c) - 1
                chars.append(charset[pos] if pos < len(charset) else "")
                scores.append(float(conf[t]))
            prev = int(c)
        text = "".join(chars)
        score = round(float(np.mean(scores)), 5) if scores else 0.0
        results.append(
            ImageResult(index=i, width=geoms[i].orig_w, height=geoms[i].orig_h,
                        texts=[TextRegion(text=text, score=score)] if text else [])
        )
    return results


def decode_passthrough(outputs: Outputs, cfg: ModelConfig, geoms: list[Geometry],
                       batch: int) -> list[ImageResult]:
    MAX = 4096
    results = []
    for i in range(batch):
        raw = {}
        for j, out in enumerate(outputs):
            name = (cfg.input.output_names[j] if j < len(cfg.input.output_names) else f"output_{j}")
            sample = out[i] if out.shape[0] >= batch else out
            flat = np.asarray(sample).ravel()
            raw[name] = {
                "shape": list(np.asarray(sample).shape),
                "dtype": str(out.dtype),
                "truncated": bool(flat.size > MAX),
                "values": [round(float(v), 6) for v in flat[:MAX]],
            }
        results.append(ImageResult(index=i, width=geoms[i].orig_w, height=geoms[i].orig_h, raw=raw))
    return results


_DISPATCH = {
    Decoder.yolo_v5: lambda o, c, g, b: decode_detection(o, c, g, b, v5=True),
    Decoder.yolo_v8: lambda o, c, g, b: decode_detection(o, c, g, b, v5=False),
    Decoder.detr: decode_detr,
    Decoder.detr_seg: decode_detr_seg,
    Decoder.yolo_seg: decode_yolo_seg,
    Decoder.yolo_pose: decode_pose,
    Decoder.binary_mask: decode_binary_mask,
    Decoder.classifier_softmax: lambda o, c, g, b: decode_classification(o, c, g, b, "softmax"),
    Decoder.classifier_sigmoid: lambda o, c, g, b: decode_classification(o, c, g, b, "sigmoid"),
    Decoder.classifier_probs: lambda o, c, g, b: decode_classification(o, c, g, b, "none"),
    Decoder.embedding: decode_embedding,
    Decoder.ctc_greedy: decode_ctc,
    Decoder.passthrough: decode_passthrough,
}


def postprocess(outputs: Outputs, cfg: ModelConfig, geoms: list[Geometry]) -> list[ImageResult]:
    batch = len(geoms)
    fn = _DISPATCH.get(cfg.postprocess.decoder)
    if fn is None:
        raise DecodeError(f"decoder '{cfg.postprocess.decoder}' is not implemented")
    if not outputs:
        raise DecodeError("model returned no output tensors")
    results = fn(list(outputs), cfg, geoms, batch)
    if cfg.task == Task.segmentation:
        for r in results:
            if r.segments is None and r.detections is not None:
                r.segments = r.detections
    return results


def confidence_summary(results: list[ImageResult]) -> dict:
    """R5.9 - per-request signals the drift job aggregates over a window."""
    scores: list[float] = []
    classes: dict[str, int] = {}
    for r in results:
        for d in (r.detections or []):
            scores.append(d.score)
            classes[d.class_name] = classes.get(d.class_name, 0) + 1
        for c in (r.classifications or []):
            scores.append(c.score)
            classes[c.class_name] = classes.get(c.class_name, 0) + 1
        for t in (r.texts or []):
            scores.append(t.score)
    arr = np.asarray(scores, dtype=np.float32)
    return {
        "count": int(arr.size),
        "mean": round(float(arr.mean()), 5) if arr.size else None,
        "min": round(float(arr.min()), 5) if arr.size else None,
        "max": round(float(arr.max()), 5) if arr.size else None,
        "p10": round(float(np.percentile(arr, 10)), 5) if arr.size else None,
        "classes": classes,
    }
