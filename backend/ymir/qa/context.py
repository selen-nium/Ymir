"""What a question is answered against: the drawing payload plus the user's build decisions (scale, walls)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

AREA = re.compile(r"^(\d+[.,]\d+)\s*m", re.I)   # printed area text, e.g. "15.90 m²"
NUM = re.compile(r"^\d\.\d{2}$")                 # Swiss room number, e.g. "2.09"


def ctr(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def union_box(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def poly_box(p):
    return union_box([[x, y, x, y] for x, y in p])


def poly_area(p):
    a = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(p, p[1:] + p[:1]))
    return abs(a) / 2


def inside_poly(pt, p):
    x, y = pt
    inside = False
    j = len(p) - 1
    for i in range(len(p)):
        xi, yi = p[i]
        xj, yj = p[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def fmt(v: float) -> str:
    s = f"{v:.0f}" if v >= 100 else f"{v:.2f}"
    return s[:-3] if s.endswith(".00") else s


def printed_area(text: str) -> float:
    return float(AREA.match(text).group(1).replace(",", "."))


def cites(n: int) -> str:
    return f"<cite>1</cite>–<cite>{n}</cite>" if n > 1 else "<cite>1</cite>" if n == 1 else ""


@dataclass
class Context:
    drawing: dict
    question: str
    image_path: Path | None = None
    m_per_px: float | None = None          # the scale the user confirmed (or the drawing's estimate)
    scale_confirmed: bool = False
    height_m: float = 2.7
    removed_walls: set[str] = field(default_factory=set)
    added_walls: list[dict] = field(default_factory=list)

    def __post_init__(self):
        d = self.drawing
        for key in ("texts", "rooms", "walls", "symbols", "openings"):
            d.setdefault(key, [])
        if not self.m_per_px:
            self.m_per_px = d["scale"]["m_per_px"]
        self.lower = self.question.lower()

    @property
    def texts(self):
        return self.drawing["texts"]

    @property
    def rooms(self):
        return self.drawing["rooms"]

    def walls(self):
        kept = [w for w in self.drawing["walls"] if w["id"] not in self.removed_walls]
        return kept + [w for w in self.added_walls if w["id"] not in self.removed_walls]

    def room_at(self, pt):
        return next((r for r in self.rooms if inside_poly(pt, r["polygon"])), None)

    def room_area(self, room) -> float:
        return poly_area(room["polygon"]) * self.m_per_px ** 2

    def wall_length(self, w) -> float:
        return math.dist(w["a"], w["b"]) * self.m_per_px

    def printed_below(self, num_box):
        """Printed area text sitting just below a room-number text."""
        cx, cy = ctr(num_box)
        h = num_box[3] - num_box[1]
        near = [(t, ctr(t["box"])) for t in self.texts if AREA.match(t["text"])]
        near = [(t, c) for t, c in near if c[1] > cy and c[1] - cy < 3.5 * h and abs(c[0] - cx) < 45]
        return min(near, key=lambda tc: tc[1][1])[0] if near else None
