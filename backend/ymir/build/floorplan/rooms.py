"""Split rooms that swallowed a neighbour, using room labels as seeds.

A room polygon from the wall network can cover two real rooms when a wall was missed. If OCR
finds two or more room labels inside it, flood the free space from each label (marker watershed)
with the wall mask and door-gap links as barriers, so the rooms meet on walls or in doorways.
Coordinates are image pixels.
"""
from __future__ import annotations

import cv2
import numpy as np
from shapely.geometry import Point, Polygon


def split_rooms(rooms: list[dict], seeds: list[tuple[str, float, float]], barrier: np.ndarray,
                links: list[dict] | None = None, min_area_px: float = 200.0) -> list[dict]:
    """rooms: [{"id", "polygon": [[x, y], ...]}]; seeds: (text, x, y); barrier: uint8 HxW, >0 = wall.

    Returns rooms where each multi-seed room is replaced by one polygon per seed (id suffixed, "split": True).
    """
    H, W = barrier.shape
    wall = (barrier > 0).astype(np.uint8)
    for l in links or []:
        cv2.line(wall, tuple(map(int, np.round(l["a"]))), tuple(map(int, np.round(l["b"]))), 1, 3)
    out = []
    for r in rooms:
        poly = Polygon(r["polygon"])
        inside = [s for s in seeds if poly.contains(Point(s[1], s[2]))]
        if len(inside) < 2:
            out.append(r)
            continue
        x0, y0, x1, y1 = [int(v) for v in poly.bounds]
        x0, y0, x1, y1 = max(0, x0 - 2), max(0, y0 - 2), min(W, x1 + 3), min(H, y1 + 3)
        m = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillPoly(m, [np.round(np.array(r["polygon"]) - [x0, y0]).astype(np.int32)], 1)
        free = (m & (1 - wall[y0:y1, x0:x1])).astype(np.uint8)
        dist = cv2.distanceTransform(free, cv2.DIST_L2, 5)
        elev = (254 - np.clip(dist / max(float(dist.max()), 1.0) * 254, 0, 254)).astype(np.uint8)
        elev[free == 0] = 255  # walls are ridges: rooms meet on walls or in narrow doorways
        markers = np.zeros(m.shape, np.int32)
        markers[m == 0] = 1
        for k, (_, sx, sy) in enumerate(inside):
            cv2.circle(markers, (int(sx - x0), int(sy - y0)), 3, k + 2, -1)
        cv2.watershed(cv2.merge([elev, elev, elev]), markers)
        parts = []
        for k, (text, sx, sy) in enumerate(inside):
            reg = ((markers == k + 2) & (m > 0)).astype(np.uint8)
            reg = cv2.morphologyEx(reg, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            cnts, _ = cv2.findContours(reg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not cnts:
                continue
            c = max(cnts, key=cv2.contourArea)
            if cv2.contourArea(c) < min_area_px:
                continue
            c = cv2.approxPolyDP(c, 1.5, True)[:, 0, :] + [x0, y0]
            if len(c) >= 3:
                parts.append({**r, "id": f"{r['id']}-{k + 1}", "polygon": c.astype(float).tolist(), "split": True})
        out.extend(parts if len(parts) >= 2 else [r])
    return out


def recover_rooms(rooms: list[dict], seeds: list[tuple[str, float, float]], barrier: np.ndarray,
                  links: list[dict] | None = None, max_area_px: float | None = None,
                  min_area_px: float = 200.0) -> list[dict]:
    """Add a room for every seed not inside any room (its walls did not close into a polygon).

    One watershed over the whole image: uncovered seeds flood free space; existing rooms, walls and the
    image border hold them back. A region is kept only if its area is plausible (not leaked outside).
    """
    H, W = barrier.shape
    wall = (barrier > 0).astype(np.uint8)
    for l in links or []:
        cv2.line(wall, tuple(map(int, np.round(l["a"]))), tuple(map(int, np.round(l["b"]))), 1, 3)
    taken = np.zeros((H, W), np.uint8)
    for r in rooms:
        cv2.fillPoly(taken, [np.round(np.array(r["polygon"])).astype(np.int32)], 1)
    free_seeds = [s for s in seeds if 0 <= int(s[2]) < H and 0 <= int(s[1]) < W and not taken[int(s[2]), int(s[1])]]
    if not free_seeds:
        return rooms
    free = ((1 - wall) & (1 - taken)).astype(np.uint8)
    dist = cv2.distanceTransform(free, cv2.DIST_L2, 5)
    elev = (254 - np.clip(dist / max(float(dist.max()), 1.0) * 254, 0, 254)).astype(np.uint8)
    elev[free == 0] = 255
    markers = np.zeros((H, W), np.int32)
    markers[taken > 0] = 1
    markers[0, :] = markers[-1, :] = 1
    markers[:, 0] = markers[:, -1] = 1
    for k, (_, sx, sy) in enumerate(free_seeds):
        cv2.circle(markers, (int(sx), int(sy)), 3, k + 2, -1)
    cv2.watershed(cv2.merge([elev, elev, elev]), markers)
    max_area_px = max_area_px or 0.08 * H * W
    out = list(rooms)
    for k, (text, sx, sy) in enumerate(free_seeds):
        reg = ((markers == k + 2) & (free > 0)).astype(np.uint8)
        reg = cv2.morphologyEx(reg, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        cnts, _ = cv2.findContours(reg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        x, y, w, h = cv2.boundingRect(c)
        if not (min_area_px <= cv2.contourArea(c) <= max_area_px) or x <= 1 or y <= 1 or x + w >= W - 1 or y + h >= H - 1:
            continue  # leaked to the border or implausibly small/large: leave it out rather than guess
        c = cv2.approxPolyDP(c, 1.5, True)[:, 0, :]
        if len(c) >= 3:
            out.append({"id": f"room-{text}", "polygon": c.astype(float).tolist(), "recovered": True,
                        "label": None, "printed_area_m2": None})
    return out
