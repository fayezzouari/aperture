"""Synthetic end-to-end checks for every decoder. Run with: uv run python smoke_test.py"""
import numpy as np
from aperture_core import preset_config
from aperture_core.configspec import ModelConfig
from aperture_core.pipeline import run_pipeline
from aperture_core.overlay import render_png
from aperture_core.masks import encode_rle, decode_rle, encode_polygons
from aperture_core.postprocess import confidence_summary

rng = np.random.default_rng(7)
img = (rng.random((480, 640, 3)) * 255).astype(np.uint8)


def fake(shapes):
    return lambda batch: [np.asarray(s(batch.shape[0]), dtype=np.float32) for s in shapes]


def yolo8_out(b, nc=80, n=100):
    out = rng.random((b, 4 + nc, n)).astype(np.float32) * 0.01
    out[:, 0, :5], out[:, 1, :5] = 300, 240        # cx, cy in 640-space
    out[:, 2, :5], out[:, 3, :5] = 120, 90         # w, h
    out[:, 4 + 2, :5] = 0.91                       # class 2 = car
    return out


checks = []

# detection (yolo11 preset)
cfg = preset_config("yolov11")
out = run_pipeline([img], cfg, fake([yolo8_out]))
d = out.results[0].detections
checks.append(("yolo_v8 detection", len(d) == 1 and d[0].class_name == "car"
               and 0 <= d[0].box[0] < d[0].box[2] <= 640, d[0].model_dump() if d else None))

# batch of 3
out3 = run_pipeline([img, img, img], cfg, fake([yolo8_out]))
checks.append(("batch of 3", len(out3.results) == 3 and all(r.detections for r in out3.results), None))

# yolov5 (objectness column)
cfg5 = preset_config("yolov5")
def yolo5_out(b, nc=80, n=60):
    o = rng.random((b, n, 5 + nc)).astype(np.float32) * 0.01
    o[:, :3, 0], o[:, :3, 1], o[:, :3, 2], o[:, :3, 3] = 200, 200, 80, 80
    o[:, :3, 4] = 0.95
    o[:, :3, 5 + 7] = 0.9   # truck
    return o
r5 = run_pipeline([img], cfg5, fake([yolo5_out])).results[0].detections
checks.append(("yolo_v5 detection", len(r5) == 1 and r5[0].class_name == "truck", None))

# RF-DETR
cfgd = preset_config("rf-detr")
def detr_logits(b, q=300, nc=80):
    l = np.full((b, q, nc), -8.0, np.float32); l[:, :2, 3] = 4.0; return l
def detr_boxes(b, q=300):
    bx = np.tile(np.array([0.5, 0.5, 0.25, 0.3], np.float32), (b, q, 1)); return bx
def centred(d):
    cx, cy = (d.box[0] + d.box[2]) / 2, (d.box[1] + d.box[3]) / 2
    return abs(cx - 320) < 2 and abs(cy - 240) < 2
# RF-DETR exports (boxes, logits); the preset has to read them in that order
rd = run_pipeline([img], cfgd, fake([detr_boxes, detr_logits])).results[0].detections
checks.append(("rf-detr detection", len(rd) >= 1 and rd[0].class_name == "motorcycle"
               and centred(rd[0]), rd[0].box if rd else None))
# an export that emits (logits, boxes) is told apart by the 4-wide box axis
rs = run_pipeline([img], cfgd, fake([detr_logits, detr_boxes])).results[0].detections
checks.append(("rf-detr swapped outputs", len(rs) >= 1 and rs[0].class_name == "motorcycle"
               and centred(rs[0]), rs[0].box if rs else None))

# classification
cfgc = preset_config("resnet-imagenet"); cfgc.classes = [f"c{i}" for i in range(1000)]
def logits(b): 
    l = rng.random((b, 1000)).astype(np.float32); l[:, 42] = 20.0; return l
rc = run_pipeline([img], cfgc, fake([logits])).results[0].classifications
checks.append(("classification top-k", len(rc) == 5 and rc[0].class_id == 42, rc[0].model_dump()))

# embedding
cfge = preset_config("clip-image")
re_ = run_pipeline([img], cfge, fake([lambda b: rng.random((b, 512))])).results[0]
norm = float(np.linalg.norm(re_.embedding))
checks.append(("clip embedding l2-normalised", abs(norm - 1.0) < 1e-3 and re_.embedding_dim == 512, norm))

# segmentation
cfgs = preset_config("yolov8-seg")
def seg_pred(b, nc=80, nm=32, n=50):
    o = rng.random((b, 4 + nc + nm, n)).astype(np.float32) * 0.01
    o[:, 0, :2], o[:, 1, :2], o[:, 2, :2], o[:, 3, :2] = 320, 240, 200, 160
    o[:, 4 + 0, :2] = 0.88
    o[:, 4 + nc:, :2] = 0.5
    return o
def proto(b, nm=32, mh=160, mw=160):
    p = np.full((b, nm, mh, mw), 0.05, np.float32); p[:, :, 40:120, 40:120] = 0.9; return p
rs = run_pipeline([img], cfgs, fake([seg_pred, proto])).results[0]
seg_ok = bool(rs.segments) and rs.segments[0].mask is not None and rs.segments[0].mask.format == "rle"
checks.append(("yolo_seg masks", seg_ok, rs.segments[0].mask.area if seg_ok else None))

# pose
cfgp = preset_config("yolov8-pose")
def pose_pred(b, n=40, nk=17):
    o = rng.random((b, 5 + nk * 3, n)).astype(np.float32) * 0.01
    o[:, 0, :1], o[:, 1, :1], o[:, 2, :1], o[:, 3, :1] = 300, 200, 100, 300
    o[:, 4, :1] = 0.93
    for k in range(nk):
        o[:, 5 + k * 3, :1] = 300 + k
        o[:, 5 + k * 3 + 1, :1] = 100 + k * 5
        o[:, 5 + k * 3 + 2, :1] = 0.8
    return o
rp = run_pipeline([img], cfgp, fake([pose_pred])).results[0].detections
checks.append(("pose keypoints", len(rp) == 1 and len(rp[0].keypoints) == 17, None))

# OCR / CTC
cfgo = preset_config("paddleocr-rec")
cs = cfgo.postprocess.charset
def ctc(b, T=40):
    l = np.full((b, T, len(cs) + 1), -6.0, np.float32)
    l[:, :, 0] = 2.0
    for i, ch in enumerate("FIASCO"):
        l[:, i * 3, 0] = -6.0
        l[:, i * 3, cs.index(ch) + 1] = 8.0
    return l
rt = run_pipeline([img], cfgo, fake([ctc])).results[0].texts
checks.append(("ctc ocr decode", bool(rt) and rt[0].text == "FIASCO", rt[0].text if rt else None))

# SAM binary mask -> polygons
cfgm = preset_config("sam")
def sam(b, H=1024, W=1024):
    m = np.full((b, 1, H, W), -5.0, np.float32); m[:, :, 300:700, 200:600] = 5.0; return m
rm = run_pipeline([img], cfgm, fake([sam])).results[0]
poly_ok = bool(rm.segments) and rm.segments[0].mask.format == "polygon" and rm.segments[0].mask.polygons
checks.append(("sam polygon masks", bool(poly_ok), len(rm.segments[0].mask.polygons[0]) if poly_ok else None))

# RF-DETR segmentation: dets + labels + one mask per query
cfgds = preset_config("rf-detr-seg")
cfgds.classes = [f"c{i}" for i in range(80)]
cfgds.postprocess.score_threshold = 0.5
def ds_boxes(b, q=200):
    return np.tile(np.array([0.5, 0.5, 0.3, 0.4], np.float32), (b, q, 1))
def ds_labels(b, q=200, nc=80):
    l = np.full((b, q, nc), -9.0, np.float32); l[:, :3, 5] = 4.0; return l
def ds_masks(b, q=200, h=192, w=192):
    m = np.full((b, q, h, w), -6.0, np.float32); m[:, :3, 60:130, 50:140] = 6.0; return m
rds = run_pipeline([img], cfgds, fake([ds_boxes, ds_labels, ds_masks])).results[0]
ds_ok = (len(rds.segments or []) == 3 and rds.segments[0].class_name == "c5"
         and rds.segments[0].mask is not None and rds.segments[0].mask.area > 0)
checks.append(("detr_seg: one mask per kept query", ds_ok,
               f"{len(rds.segments or [])} segments, area "
               f"{rds.segments[0].mask.area if ds_ok else None}"))

# overrides (R3.6)
tight = cfg.apply_overrides({"score_threshold": 0.99})
checks.append(("threshold override drops boxes",
               len(run_pipeline([img], tight, fake([yolo8_out])).results[0].detections) == 0
               and cfg.postprocess.score_threshold == 0.25, None))

# rle round-trip
m = np.zeros((50, 60), np.uint8); m[10:30, 15:45] = 1
enc = encode_rle(m)
checks.append(("rle round-trip", np.array_equal(decode_rle(enc["counts"], enc["size"]), m), enc["area"]))

# overlay render
png = render_png(img, out.results[0])
checks.append(("overlay png", png[:4] == b"\x89PNG" and len(png) > 1000, len(png)))

# passthrough + confidence summary
cs2 = confidence_summary(out.results)
checks.append(("confidence summary", cs2["count"] == 1 and cs2["classes"] == {"car": 1}, cs2))

# every preset validates
from aperture_core import PRESETS
bad = []
for name in PRESETS:
    try:
        preset_config(name)
    except Exception as e:
        bad.append((name, str(e)))
checks.append(("all presets validate", not bad, bad))

fails = 0
for name, ok, detail in checks:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   -> {detail}" if not ok or detail is not None else ""))
    fails += 0 if ok else 1
print(f"\n{len(checks) - fails}/{len(checks)} passed")
raise SystemExit(1 if fails else 0)
