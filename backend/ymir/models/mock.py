"""Stand-in detector until the team's trained models (stoahackjunction src/stoa_counting) are plugged in.

Hand-placed boxes on the CubiCasa sample, with deliberate mistakes so the review step can be tried:
two false windows (a wardrobe and a kitchen counter), one window cut short, and one real window left
below the threshold. Doors are confident, so they answer without a review step.
"""
from __future__ import annotations

from pathlib import Path

from .base import Detection, DetectorResult

_BOXES = {
    "cubi": {
        "window": (0.42, 0.58, [
            ("w1", (54, 21, 186, 37), 0.93),
            ("w2", (647, 250, 667, 382), 0.88),
            ("w3", (650, 40, 667, 150), 0.55),
            ("w4", (551, 40, 577, 182), 0.47),
            ("w5", (312, 250, 492, 292), 0.44),
            ("w6", (19, 276, 35, 314), 0.34),
        ]),
        "door": (0.40, 0.91, [
            ("d1", (124, 262, 162, 302), 0.94),
            ("d2", (177, 277, 214, 318), 0.90),
            ("d3", (253, 331, 306, 436), 0.97),
            ("d4", (520, 183, 620, 233), 0.86),
        ]),
    },
}


class MockDetector:
    name = "YOLO11s (mock)"
    classes = {"window", "door"}

    def supports(self, drawing: dict, cls: str) -> bool:
        return cls in _BOXES.get(drawing.get("id"), {})

    def detect(self, image_path: Path, drawing: dict, classes: list[str]) -> DetectorResult:
        per_class = _BOXES.get(drawing.get("id"), {})
        detections, thresholds, confidence = [], {}, {}
        for cls in classes:
            if cls not in per_class:
                continue
            threshold, conf, boxes = per_class[cls]
            thresholds[cls], confidence[cls] = threshold, conf
            detections += [Detection(i, cls, box, score) for i, box, score in boxes]
        return DetectorResult(detections, thresholds, confidence)
