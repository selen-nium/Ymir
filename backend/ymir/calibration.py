"""Confidence measured against people. Every box review says whether the detector got a count right, so an
answer nobody checks gets a confidence based on how often people agreed with that detector on that class:

    confidence = (agreed + PRIOR × model_confidence) / (reviews + PRIOR)

With no reviews it is the detector's own confidence; as reviews come in it moves toward people's agreement
rate. Reviews count only for the detector (name and version) that answered, so a retrained model starts
again from its own confidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import settings

if TYPE_CHECKING:
    from .models.base import Detector
    from .reviews import ReviewStore

MATCH_IOU = 0.5  # a box the person resized is still the detector's box if they overlap at least this much


def detector_info(det: Detector) -> dict:
    return {"name": det.name, "version": str(getattr(det, "version", ""))}


def iou(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    inter = max(0.0, w) * max(0.0, h)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def agrees(record: dict) -> bool | None:
    """Whether the boxes the detector counted are exactly the ones the person kept: nothing dropped (false
    positive), nothing drawn or counted from under the threshold (missed). None when the record can't tell."""
    model = record.get("model") or {}
    scores, threshold = model.get("scores"), model.get("threshold")
    if not model.get("detector") or not isinstance(scores, dict) or threshold is None:
        return None
    counted = {i for i, s in scores.items() if s is not None and s >= threshold}
    original = model.get("boxes") or {}
    kept = set()
    for o in record.get("objects", []):
        if o.get("status") != "accepted":
            continue
        pid = o.get("proposal_id")
        if pid is None:
            return False
        if pid in original and iou(original[pid], o["bbox_xyxy"]) < MATCH_IOU:
            return False
        kept.add(pid)
    return kept == counted


@dataclass(frozen=True)
class Calibration:
    model: float    # the detector's own confidence for this answer
    agreed: int     # reviews of this detector and class where the person agreed
    reviews: int    # reviews of this detector and class

    @property
    def confidence(self) -> float:
        k = settings.CALIBRATION_PRIOR
        return round((self.agreed + k * self.model) / (self.reviews + k), 3)

    def basis(self) -> dict:
        return {"model": self.model, "reviews": self.reviews, "agreed": self.agreed}


def calibrate(store: ReviewStore, det: Detector, cls: str, model_confidence: float) -> Calibration:
    """A detector that declares `trained_on_reviews = True` is measured on held-out drawings only: reviews it
    learned from would make it look better than it is."""
    info, holdout_only = detector_info(det), bool(getattr(det, "trained_on_reviews", False))
    agreed = reviews = 0
    for r in store.by_class(cls):
        if (r.get("model") or {}).get("detector") != info or (holdout_only and r.get("split") != "holdout"):
            continue
        verdict = agrees(r)
        if verdict is not None:
            reviews += 1
            agreed += verdict
    return Calibration(model_confidence, agreed, reviews)
