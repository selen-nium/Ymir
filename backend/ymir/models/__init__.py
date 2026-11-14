"""Detector registry. Models are listed in YMIR_MODELS as "module:factory" entries, in priority order.

    YMIR_MODELS="my_team.yolo:YoloDetector,ymir.models.mock:MockDetector"

The first detector that supports a class on a drawing answers for it. See README › Add your model.
"""
from __future__ import annotations

import importlib
from functools import lru_cache

from .. import settings
from .base import Detection, Detector, DetectorResult, margin_confidence

__all__ = ["Detection", "Detector", "DetectorResult", "margin_confidence", "detectors", "detector_for", "load"]


def load(spec: str) -> list[Detector]:
    out = []
    for entry in filter(None, (s.strip() for s in spec.split(","))):
        module, _, factory = entry.partition(":")
        if not factory:
            raise ValueError(f"YMIR_MODELS entry {entry!r} must look like 'package.module:Factory'")
        detector = getattr(importlib.import_module(module), factory)()
        if not isinstance(detector, Detector):
            raise TypeError(f"{entry} does not implement ymir.models.base.Detector")
        out.append(detector)
    return out


@lru_cache(maxsize=1)
def detectors() -> tuple[Detector, ...]:
    return tuple(load(settings.MODELS))


def detector_for(drawing: dict, cls: str) -> Detector | None:
    return next((d for d in detectors() if cls in d.classes and d.supports(drawing, cls)), None)
