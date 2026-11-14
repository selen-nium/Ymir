import pytest

from ymir import models
from ymir.models import margin_confidence
from ymir.models.mock import MockDetector


def test_load_detectors_from_a_spec():
    (d,) = models.load("ymir.models.mock:MockDetector")
    assert isinstance(d, MockDetector) and isinstance(d, models.Detector)


@pytest.mark.parametrize("spec, error", [("ymir.models.mock", ValueError), ("ymir.models.base:Detection", TypeError)])
def test_bad_specs_fail_loudly(spec, error):
    with pytest.raises(error):
        models.load(spec)


def test_detector_for_picks_a_detector_that_supports_the_drawing():
    assert isinstance(models.detector_for({"id": "cubi"}, "window"), MockDetector)
    assert models.detector_for({"id": "swiss"}, "window") is None
    assert models.detector_for({"id": "cubi"}, "toilet_bowl") is None


def test_counted_boxes_use_the_class_threshold():
    result = MockDetector().detect(None, {"id": "cubi"}, ["window"])
    assert [d.id for d in result.counted("window")] == ["w1", "w2", "w3", "w4", "w5"]
    assert result.confidence_for("window") == 0.58


def test_margin_confidence_estimate():
    assert margin_confidence([], 0.5) == 0.5
    assert margin_confidence([0.95, 0.9], 0.5) == 1.0
    assert margin_confidence([0.95, 0.55, 0.45, 0.1], 0.5) == 0.5
