"""Wall evidence -> clean, joined, axis-aligned wall segments.

Evidence is either a binary/probability wall mask (from a segmentation model or a classical
thick-stroke filter) or raw line proposals. Coordinates are image pixels, x right, y down.
"""
from __future__ import annotations

import math

import cv2
import numpy as np


def _horiz(w) -> bool:
    return abs(w["a"][1] - w["b"][1]) < 1e-6


def merge_collinear(walls: list[dict], tol: float = 4) -> list[dict]:
    """Union axis-aligned walls sharing an axis (within tol px) that overlap or touch."""
    out = []
    for horiz in (True, False):
        segs = []
        for w in walls:
            if _horiz(w) != horiz or (not horiz and abs(w["a"][0] - w["b"][0]) > 1e-6):
                continue
            c = w["a"][1] if horiz else w["a"][0]
            lo, hi = sorted((w["a"][0], w["b"][0]) if horiz else (w["a"][1], w["b"][1]))
            segs.append([c, lo, hi, w.get("thickness_px", 0) or 0, w.get("confidence", 1.0), hi - lo])
        segs.sort()
        groups = []
        for s in segs:
            for g in groups:
                if abs(g[0] - s[0]) <= tol and s[1] <= g[2] + 2 and s[2] >= g[1] - 2:
                    # length-weighted thickness/confidence
                    L = g[5] + s[5]
                    g[3] = (g[3] * g[5] + s[3] * s[5]) / max(L, 1e-6)
                    g[4] = (g[4] * g[5] + s[4] * s[5]) / max(L, 1e-6)
                    g[1], g[2], g[5] = min(g[1], s[1]), max(g[2], s[2]), L
                    break
            else:
                groups.append(list(s))
        for c, lo, hi, t, conf, _ in groups:
            a, b = ([lo, c], [hi, c]) if horiz else ([c, lo], [c, hi])
            out.append({"a": a, "b": b, "thickness_px": t, "confidence": conf})
    return out


def join_corners(walls: list[dict], tol: float = 14) -> list[dict]:
    """Snap each endpoint onto a nearby perpendicular wall axis (extend or trim) so junctions close."""
    for w in walls:
        for end in ("a", "b"):
            p = w[end]
            best = None
            for o in walls:
                if o is w or _horiz(o) == _horiz(w):
                    continue
                if _horiz(o):
                    c = o["a"][1]; lo, hi = sorted((o["a"][0], o["b"][0]))
                    d = abs(p[1] - c); ok = lo - tol <= p[0] <= hi + tol
                else:
                    c = o["a"][0]; lo, hi = sorted((o["a"][1], o["b"][1]))
                    d = abs(p[0] - c); ok = lo - tol <= p[1] <= hi + tol
                if ok and d <= tol and (best is None or d < best[0]):
                    best = (d, o, c)
            if not best:
                continue
            _, o, c = best
            w[end] = [c, p[1]] if _horiz(w) else [p[0], c]
            k = 0 if _horiz(o) else 1
            q = w[end][k]
            lo_end, hi_end = sorted(("a", "b"), key=lambda e: o[e][k])
            if q < o[lo_end][k]:
                o[lo_end] = [q, o[lo_end][1]] if k == 0 else [o[lo_end][0], q]
            if q > o[hi_end][k]:
                o[hi_end] = [q, o[hi_end][1]] if k == 0 else [o[hi_end][0], q]
    return walls


def segments_from_mask(mask: np.ndarray, prob: np.ndarray | None = None, min_len: float | None = None,
                       max_thick: float | None = None) -> list[dict]:
    """Axis-aligned wall runs from a binary wall mask (uint8 0/255). Returns centreline segments.

    Uses directional morphological opening: a horizontal run must be at least min_len long.
    min_len adapts to the typical wall thickness so short interior walls survive on small images.
    """
    m = (mask > 127).astype(np.uint8)
    if not m.any():
        return []
    dt = cv2.distanceTransform(m, cv2.DIST_L2, 3)
    ridge = dt[(dt > 0) & (dt >= cv2.dilate(dt, np.ones((3, 3), np.uint8)) - 1e-6)]
    typ = float(np.median(ridge) * 2) if ridge.size else 4.0  # typical wall thickness (px)
    min_len = min_len or max(12.0, 2.2 * typ)
    max_thick = max_thick or max(8.0, 4.0 * typ)
    out = []
    for horiz in (True, False):
        L = int(round(min_len))
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1) if horiz else (1, L))
        opened = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=4)
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            length, thick = (w, h) if horiz else (h, w)
            if length < min_len or thick > max_thick or length < 1.5 * thick:
                continue
            # centre of mass across the run is robust to ragged edges
            ys, xs = np.nonzero(lab[y:y + h, x:x + w] == i)
            c = (y + ys.mean()) if horiz else (x + xs.mean())
            fill = area / float(w * h)
            t_est = area / float(length)
            conf = float(prob[y:y + h, x:x + w][lab[y:y + h, x:x + w] == i].mean()) if prob is not None else fill
            a, b = ([float(x), float(c)], [float(x + w - 1), float(c)]) if horiz else ([float(c), float(y)], [float(c), float(y + h - 1)])
            out.append({"a": a, "b": b, "thickness_px": float(t_est), "confidence": conf})
    return out


def clean(walls: list[dict], typ_thick: float | None = None) -> list[dict]:
    """merge -> join -> drop fragments. Tolerances scale with wall thickness."""
    t = typ_thick or float(np.median([w.get("thickness_px", 4) or 4 for w in walls])) if walls else 4.0
    walls = merge_collinear(walls, tol=max(3.0, 0.6 * t))
    walls = join_corners(walls, tol=max(8.0, 1.6 * t))
    walls = merge_collinear(walls, tol=max(3.0, 0.6 * t))
    return [w for w in walls if math.dist(w["a"], w["b"]) >= max(10.0, 1.5 * t)]


def classical_wall_mask(gray: np.ndarray) -> np.ndarray:
    """Keep strokes noticeably thicker than text/furniture lines (no learning)."""
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))  # fill hatching gaps
    dt = cv2.distanceTransform((ink > 0).astype(np.uint8), cv2.DIST_L2, 3)
    peaks = dt[dt >= cv2.dilate(dt, np.ones((3, 3), np.uint8)) - 1e-6]
    peaks = peaks[peaks > 0]
    r = max(1.6, 0.45 * float(np.percentile(peaks, 97))) if peaks.size else 2.0
    core = (dt >= r).astype(np.uint8)
    k = int(2 * math.ceil(r) + 1)
    return (cv2.dilate(core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))) & (ink > 0)).astype(np.uint8) * 255


def bridge_gaps(walls: list[dict], max_gap: float, touch: float = 0.005, min_gap: float = 0.0) -> list[dict]:
    """Virtual segments across door-sized gaps, used to close rooms (never built as walls).

    From each free wall end, look along the wall's own direction for the first other wall within
    max_gap. Doorways are usually drawn as breaks in the wall line, so without these links the room
    finder leaks from one room into the next. Units follow the input (pixels or metres).
    """
    from shapely.geometry import LineString, Point

    lines = [LineString([w["a"], w["b"]]) for w in walls]
    out, seen = [], set()
    for i, (w, line) in enumerate(zip(walls, lines)):
        for end, other in (("a", "b"), ("b", "a")):
            p = np.array(w[end], float)
            q = np.array(w[other], float)
            if any(j != i and lines[j].distance(Point(p)) < touch for j in range(len(lines))):
                continue  # already joined
            d = p - q
            n = float(np.hypot(*d))
            if n < 1e-9:
                continue
            d /= n
            ray = LineString([p, p + d * max_gap])
            best = None
            for j, o in enumerate(lines):
                if j == i or not ray.intersects(o):
                    continue
                hit = ray.intersection(o)
                pts = [hit] if hit.geom_type == "Point" else [Point(c) for c in getattr(hit, "coords", [])] or \
                      [Point(c) for g in getattr(hit, "geoms", []) for c in g.coords]
                for h in pts:
                    dist = Point(p).distance(h)
                    if dist > max(touch, min_gap) and (best is None or dist < best[0]):
                        best = (dist, (h.x, h.y))
            if best:
                a, b = tuple(np.round(p, 3)), tuple(np.round(best[1], 3))
                key = tuple(sorted((a, b)))
                if key not in seen:
                    seen.add(key)
                    out.append({"a": [float(a[0]), float(a[1])], "b": [float(b[0]), float(b[1])], "gap": round(best[0], 3)})
    return out
