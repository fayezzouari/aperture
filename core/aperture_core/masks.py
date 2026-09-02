"""Mask encoding: COCO column-major RLE and polygon outlines.

Polygon extraction traces the boundary of each connected component on a
downsampled copy of the mask (capped at POLY_TRACE_MAX px on the long side) and
scales the result back up - it keeps the response small and the cost bounded,
which matters because this runs on the inference hot path.
"""

from __future__ import annotations

import numpy as np

POLY_TRACE_MAX = 256
MIN_COMPONENT_PX = 8


def encode_rle(mask: np.ndarray) -> dict:
    """COCO-compatible uncompressed RLE (column-major run lengths, starting with 0s)."""
    m = np.asfortranarray(mask.astype(np.uint8))
    flat = m.ravel(order="F")
    if flat.size == 0:
        return {"format": "rle", "size": list(mask.shape), "counts": "0", "area": 0.0}
    changes = np.flatnonzero(np.diff(flat)) + 1
    bounds = np.concatenate(([0], changes, [flat.size]))
    runs = np.diff(bounds).tolist()
    if flat[0] == 1:
        runs = [0] + runs  # RLE always starts with a background run
    return {
        "format": "rle",
        "size": [int(mask.shape[0]), int(mask.shape[1])],
        "counts": " ".join(str(int(r)) for r in runs),
        "area": float(flat.sum()),
    }


def decode_rle(counts: str, size: list[int]) -> np.ndarray:
    runs = [int(v) for v in counts.split()]
    total = size[0] * size[1]
    flat = np.zeros(total, dtype=np.uint8)
    pos, value = 0, 0
    for run in runs:
        if value:
            flat[pos:pos + run] = 1
        pos += run
        value ^= 1
        if pos >= total:
            break
    return flat.reshape((size[0], size[1]), order="F")


def _components(mask: np.ndarray) -> list[np.ndarray]:
    """4-connected labelling with a scanline union-find - no scipy dependency."""
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    parent: list[int] = [0]

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    next_label = 1
    for y in range(h):
        row = mask[y]
        for x in np.flatnonzero(row):
            up = labels[y - 1, x] if y > 0 else 0
            left = labels[y, x - 1] if x > 0 else 0
            if up and left:
                labels[y, x] = min(up, left)
                union(up, left)
            elif up or left:
                labels[y, x] = up or left
            else:
                labels[y, x] = next_label
                parent.append(next_label)
                next_label += 1

    if next_label == 1:
        return []
    roots = np.array([find(i) for i in range(next_label)], dtype=np.int32)
    flat = roots[labels]
    out = []
    for label in np.unique(flat):
        if label == 0:
            continue
        comp = flat == label
        if comp.sum() >= MIN_COMPONENT_PX:
            out.append(comp)
    return out


_MOORE = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


def _trace(comp: np.ndarray) -> list[tuple[int, int]]:
    """Moore-neighbour boundary trace of one component, clockwise."""
    h, w = comp.shape
    ys, xs = np.nonzero(comp)
    start = (int(ys[0]), int(xs[0]))
    contour = [start]
    cur, backtrack = start, 7
    guard = 4 * (h + w) + 16
    for _ in range(guard):
        found = False
        for k in range(8):
            idx = (backtrack + 1 + k) % 8
            dy, dx = _MOORE[idx]
            ny, nx = cur[0] + dy, cur[1] + dx
            if 0 <= ny < h and 0 <= nx < w and comp[ny, nx]:
                backtrack = (idx + 4 + 1) % 8
                cur = (ny, nx)
                contour.append(cur)
                found = True
                break
        if not found or (len(contour) > 2 and cur == start):
            break
    return contour


def _simplify(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker, iterative."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        (x1, y1), (x2, y2) = points[i], points[j]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5 or 1.0
        best, best_d = -1, -1.0
        for k in range(i + 1, j):
            px, py = points[k]
            d = abs(dy * (px - x1) - dx * (py - y1)) / norm
            if d > best_d:
                best, best_d = k, d
        if best_d > tol:
            keep[best] = True
            stack.append((i, best))
            stack.append((best, j))
    return [p for p, k in zip(points, keep) if k]


def _simplify_ring(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """RDP on a closed ring: split at the point farthest from the start first.

    Running plain RDP on a ring collapses it, because the start and end points
    coincide and every vertex sits zero distance from that degenerate baseline.
    """
    if len(points) < 4:
        return points
    if points[0] == points[-1]:
        points = points[:-1]
    x0, y0 = points[0]
    d = [((px - x0) ** 2 + (py - y0) ** 2) for px, py in points]
    k = max(range(len(points)), key=d.__getitem__)
    first = _simplify(points[: k + 1], tol)
    second = _simplify(points[k:] + [points[0]], tol)
    return first[:-1] + second[:-1]


def encode_polygons(mask: np.ndarray, tolerance: float = 1.0) -> dict:
    h, w = mask.shape
    scale = min(1.0, POLY_TRACE_MAX / max(h, w, 1))
    if scale < 1.0:
        sh, sw = max(1, int(h * scale)), max(1, int(w * scale))
        ys = (np.arange(sh) / scale).astype(np.int32).clip(0, h - 1)
        xs = (np.arange(sw) / scale).astype(np.int32).clip(0, w - 1)
        small = mask[np.ix_(ys, xs)]
    else:
        small = mask
        sh, sw = h, w

    inv = 1.0 / scale if scale < 1.0 else 1.0

    def ring_of(component: np.ndarray) -> list[float] | None:
        pts = [(float(x), float(y)) for y, x in _trace(component)]
        pts = _simplify_ring(pts, tolerance)
        if len(pts) < 3:
            # degenerate trace (a hairline component): fall back to its extent box
            ys_c, xs_c = np.nonzero(component)
            if not ys_c.size:
                return None
            x1, y1 = float(xs_c.min()), float(ys_c.min())
            x2, y2 = float(xs_c.max()), float(ys_c.max())
            pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        return [round(v * inv, 2) for p in pts for v in p]

    rings: list[list[float]] = []
    for comp in _components(small.astype(bool)):
        outer = ring_of(comp)
        if outer is None:
            continue
        rings.append(outer)
        # A mask with a hole in it - a wheel arch, a window inside a door - is
        # not described by its outer boundary alone. The enclosed background
        # pockets are emitted as their own rings, and the viewer fills the set
        # even-odd, so a hole stays a hole instead of being painted over.
        padded = np.pad(comp, 1, constant_values=False)
        for pocket in _components(~padded):
            if (pocket[0].any() or pocket[-1].any()
                    or pocket[:, 0].any() or pocket[:, -1].any()):
                continue  # reaches the outside, so it is not enclosed
            if int(pocket.sum()) < MIN_COMPONENT_PX:
                continue
            hole = ring_of(pocket[1:-1, 1:-1])
            if hole is not None:
                rings.append(hole)

    return {
        "format": "polygon",
        "size": [int(h), int(w)],
        "polygons": rings,
        "area": float(mask.sum()),
    }


def encode_mask(mask: np.ndarray, fmt: str) -> dict | None:
    if fmt == "none":
        return None
    if fmt == "polygon":
        return encode_polygons(mask)
    return encode_rle(mask)
