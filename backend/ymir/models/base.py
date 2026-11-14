"""The contract a symbol detector implements to answer counting questions.

A detector receives a drawing and the classes the question needs, and returns boxes in the drawing
image's pixel space with a score, the per-class count threshold, and its confidence in each count.
Class names follow the stoahackjunction annotation schema: door, window, toilet_bowl, sink, bathtub, shower, ...
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Detection:
    id: str                                   # stable within one result, e.g. "w1"
    cls: str                                  # stoahackjunction class name
    box: tuple[float, float, float, float]    # x0, y0, x1, y1 in drawing image pixels
    score: float                              # 0..1


@dataclass
class DetectorResult:
    detections: list[Detection]
    thresholds: dict[str, float]              # a box counts when score >= thresholds[cls]
    confidence: dict[str, float] = field(default_factory=dict)  # per class, 0..1; missing → estimated

    def counted(self, cls: str) -> list[Detection]:
        t = self.thresholds.get(cls, 0.5)
        return [d for d in self.detections if d.cls == cls and d.score >= t]

    def confidence_for(self, cls: str) -> float:
        """The detector's own confidence if it gave one, else a margin-based estimate."""
        if cls in self.confidence:
            return self.confidence[cls]
        return margin_confidence([d.score for d in self.detections if d.cls == cls], self.thresholds.get(cls, 0.5))


def margin_confidence(scores: list[float], threshold: float, margin: float = 0.15) -> float:
    """Placeholder until detectors are calibrated: the share of boxes whose score is not within `margin`
    of the threshold (a box near the threshold could flip the count either way)."""
    if not scores:
        return 0.5
    near = sum(1 for s in scores if abs(s - threshold) < margin)
    return round(1 - near / len(scores), 3)


@runtime_checkable
class Detector(Protocol):
    name: str            # shown on the trace's Detector node, e.g. "YOLO11s"
    classes: set[str]    # classes it can find

    def supports(self, drawing: dict, cls: str) -> bool:
        """Whether it can answer `cls` on this drawing (e.g. only CubiCasa-style rasters)."""
        ...

    def detect(self, image_path: Path, drawing: dict, classes: list[str]) -> DetectorResult:
        """Find `classes` on the drawing image. `drawing` is the drawing payload (walls, rooms, texts, scale)."""
        ...
