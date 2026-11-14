"""Tiled RapidOCR over a drawing (horizontal text, plus a 90° pass for vertical labels)."""
from __future__ import annotations

from functools import lru_cache


def available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(None)
def _engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _quad_box(q):
    xs = [p[0] for p in q]
    ys = [p[1] for p in q]
    return [min(xs), min(ys), max(xs), max(ys)]


def _iou(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    i = w * h
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


def ocr_image(img, tile=1200, overlap=250, rot90=True):
    """img: BGR ndarray. Returns [{box, text, score}] in image pixels, duplicates across tiles merged."""
    import cv2
    H, W = img.shape[:2]
    eng = _engine()
    dets = []

    def run(sub, ox, oy, rotated=False):
        res, _ = eng(sub)
        for quad, text, score in res or []:
            b = _quad_box(quad)
            if rotated:  # sub was rotated 90° clockwise: (x', y') = (h-1-y, x)
                h = sub.shape[1]
                x0, y0, x1, y1 = b
                b = [y0, h - x1, y1, h - x0]
                if (b[3] - b[1]) < 1.5 * (b[2] - b[0]):
                    continue  # the 90° pass only contributes vertical text
            edge = b[0] < 3 or b[1] < 3 or b[2] > (sub.shape[1] if not rotated else sub.shape[0]) - 3
            dets.append({"box": [b[0] + ox, b[1] + oy, b[2] + ox, b[3] + oy], "text": text, "score": float(score), "edge": edge})

    step = tile - overlap
    for y in range(0, max(1, H - overlap), step):
        for x in range(0, max(1, W - overlap), step):
            sub = img[y:y + tile, x:x + tile]
            if sub.size == 0 or sub.mean() > 252:  # blank tile
                continue
            run(sub, x, y)
            if rot90:
                run(cv2.rotate(sub, cv2.ROTATE_90_CLOCKWISE), x, y, rotated=True)
    dets.sort(key=lambda d: (d["edge"], -d["score"]))
    keep = []
    for d in dets:
        if not any(_iou(d["box"], k["box"]) > 0.4 for k in keep):
            keep.append(d)
    return [{k: d[k] for k in ("box", "text", "score")} for d in keep]
