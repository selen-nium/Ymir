"""Confidence measured against people's box reviews, reuse of a person's check, and random spot checks."""
import json
from itertools import count

import pytest
from fastapi.testclient import TestClient

from ymir import calibration, settings
from ymir.app import app
from ymir.models.mock import MockDetector
from ymir.reviews import Review, split_for

client = TestClient(app)
MOCK = calibration.detector_info(MockDetector())

# The mock detector's doors on the CubiCasa sample, all counted at threshold 0.40; it reports 0.91 confidence.
BOX = {"d1": [124, 262, 162, 302], "d2": [177, 277, 214, 318], "d3": [253, 331, 306, 436], "d4": [520, 183, 620, 233]}
SCORES = {"d1": 0.94, "d2": 0.90, "d3": 0.97, "d4": 0.86}


def kept(*ids, box=None):
    return [{"id": i, "class": "door", "bbox_xyxy": box or BOX[i], "subtype": "", "status": "accepted", "proposal_id": i} for i in ids]


def dropped(*ids):
    return [{"id": i, "class": "door", "bbox_xyxy": BOX[i], "subtype": "", "status": "rejected", "proposal_id": i} for i in ids]


def drawn(box):
    return [{"id": "u1", "class": "door", "bbox_xyxy": box, "subtype": "", "status": "accepted"}]


ALL = ("d1", "d2", "d3", "d4")


def record(objects, drawing="cubi", detector=MOCK, scores=SCORES, threshold=0.4, boxes=None):
    model = {"value": sum(s >= threshold for s in scores.values()), "confidence": 0.91, "threshold": threshold, "scores": scores}
    if detector:
        model["detector"] = detector
    if boxes:
        model["boxes"] = boxes
    return {"schema": "drawing-qa-review-v1", "drawing": {"id": drawing, "image_size": [687, 455]},
            "question": "How many doors are there?", "class": "door",
            "user_count": sum(o["status"] == "accepted" for o in objects), "model": model, "objects": objects,
            "reviewed_at": "2026-11-15T12:00:00Z"}


def save(store, rec):
    """Store a review as POST /api/reviews would, for drawings the API doesn't have."""
    rec.setdefault("split", split_for(rec["drawing"]["id"]))
    return store.add(Review.model_validate(rec))


def ask(question, drawing="cubi"):
    res = client.post("/api/ask", json={"drawing_id": drawing, "question": question})
    assert res.status_code == 200
    return [json.loads(line) for line in res.text.splitlines() if line]


def answer(events):
    return events[-1]["answer"]


def last_status(events, node):
    return [e for e in events if e["type"] == "status" and e["id"] == node][-1]


# ---------- Did the person agree with the detector? ----------

def test_agrees_when_the_person_keeps_exactly_the_counted_boxes():
    assert calibration.agrees(record(kept(*ALL))) is True


def test_a_dropped_box_is_a_false_positive():
    assert calibration.agrees(record(kept("d1", "d2", "d3") + dropped("d4"))) is False


def test_a_box_the_person_drew_is_a_missed_object():
    assert calibration.agrees(record(kept(*ALL) + drawn([400, 400, 440, 440]))) is False


def test_counting_a_box_under_the_threshold_is_a_missed_object():
    assert calibration.agrees(record(kept(*ALL), scores={**SCORES, "d4": 0.30})) is False


def test_a_resized_box_matches_unless_it_moved_to_another_object():
    nudged = record(kept("d2", "d3", "d4") + kept("d1", box=[124, 262, 165, 306]), boxes=BOX)
    moved = record(kept("d2", "d3", "d4") + kept("d1", box=[400, 400, 440, 440]), boxes=BOX)
    assert calibration.agrees(nudged) is True
    assert calibration.agrees(moved) is False


def test_reviews_without_a_known_detector_say_nothing():
    assert calibration.agrees(record(kept(*ALL), detector=None)) is None


# ---------- Confidence ----------

@pytest.mark.parametrize("agreed, reviews, expected", [(0, 0, 0.7), (38, 40, 0.9), (20, 40, 0.54)])
def test_confidence_starts_at_the_model_score_and_moves_toward_people(agreed, reviews, expected):
    assert calibration.Calibration(0.7, agreed, reviews).confidence == pytest.approx(expected)


def test_one_drawing_in_five_is_held_out_and_always_the_same_one():
    ids = [f"plan-{i}" for i in range(2000)]
    splits = [split_for(i) for i in ids]
    assert set(splits) == {"train", "holdout"}
    assert 0.15 < splits.count("holdout") / len(ids) < 0.25
    assert splits == [split_for(i) for i in ids]


# ---------- Saving reviews ----------

def test_saved_reviews_are_stamped_with_the_detector_and_split_by_the_server():
    sent = record(kept(*ALL), detector={"name": "Something else", "version": "9"})
    assert client.post("/api/reviews", json=sent).status_code == 200
    row = client.get("/api/reviews", params={"drawing_id": "cubi"}).json()[-1]
    assert row["model"]["detector"] == MOCK
    assert row["split"] == split_for("cubi")


# ---------- Answers ----------

def test_people_agreeing_raise_the_confidence_of_later_answers(reviews):
    for i in range(20):
        save(reviews, record(kept(*ALL), drawing=f"plan-{i}"))
    events = ask("How many doors are there?")
    a = answer(events)
    assert a["value"] == 4
    assert a["confidence"] == pytest.approx((20 + 10 * 0.91) / 30, abs=0.001)
    assert a["confidence_basis"] == {"model": 0.91, "reviews": 20, "agreed": 20}
    assert "20/20 checks agreed" in last_status(events, "ver")["summary"]


def test_people_disagreeing_lower_it_below_the_review_cutoff(reviews):
    for i in range(20):
        save(reviews, record(kept("d1", "d2", "d3") + dropped("d4"), drawing=f"plan-{i}"))
    events = ask("How many doors are there?")
    assert answer(events)["confidence"] == pytest.approx(10 * 0.91 / 30, abs=0.001)
    assert "⚠" in last_status(events, "ver")["summary"]


def test_reviews_of_another_detector_or_class_do_not_count(reviews):
    save(reviews, record(dropped(*ALL), drawing="plan-1", detector={"name": "Other model", "version": "1"}))
    other_class = record(dropped(*ALL), drawing="plan-2")
    other_class["class"] = "window"
    save(reviews, other_class)
    a = answer(ask("How many doors are there?"))
    assert a["confidence"] == 0.91 and a["confidence_basis"]["reviews"] == 0


def test_a_detector_trained_on_reviews_is_calibrated_on_held_out_drawings_only(reviews, monkeypatch):
    monkeypatch.setattr(MockDetector, "trained_on_reviews", True, raising=False)
    holdout = next(f"plan-{i}" for i in count() if split_for(f"plan-{i}") == "holdout")
    train = next(f"plan-{i}" for i in count() if split_for(f"plan-{i}") == "train")
    save(reviews, record(kept(*ALL), drawing=holdout))
    save(reviews, record(dropped(*ALL), drawing=train))
    assert answer(ask("How many doors are there?"))["confidence_basis"] == {"model": 0.91, "reviews": 1, "agreed": 1}


def test_a_checked_drawing_is_answered_from_the_persons_boxes():
    assert client.post("/api/reviews", json=record(kept("d1", "d2", "d3") + dropped("d4"))).status_code == 200
    events = ask("How many doors are there?")
    a = answer(events)
    assert a["value"] == 3 and a["confidence"] == 1.0
    assert "checked by a person" in a["text"]
    assert [c["box"] for c in a["citations"]] == [BOX["d1"], BOX["d2"], BOX["d3"]]
    assert "review" not in a  # nothing left for the user to check
    assert last_status(events, "ver")["summary"] == "Checked by a person ✓"


def test_confident_answers_are_sometimes_sent_for_a_spot_check(monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_RATE", 1.0)
    events = ask("How many doors are there?")
    assert answer(events)["review"]["audit"] is True
    assert "spot check" in last_status(events, "ver")["summary"]
    monkeypatch.setattr(settings, "AUDIT_RATE", 0.0)
    assert "audit" not in answer(ask("How many doors are there?"))["review"]


def test_low_confidence_answers_are_reviewed_anyway_so_never_marked_as_spot_checks(monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_RATE", 1.0)
    a = answer(ask("How many windows are there?"))
    assert a["confidence"] == 0.58 and "audit" not in a["review"]
