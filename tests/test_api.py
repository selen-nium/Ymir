import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ymir import qa
from ymir.app import app

client = TestClient(app)
SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def ask(question, drawing="cubi", **context):
    res = client.post("/api/ask", json={"drawing_id": drawing, "question": question, "context": context})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in res.text.splitlines() if line]


def answer(events):
    assert events[-1]["type"] == "answer"
    return events[-1]["answer"]


def last_status(events, node):
    return [e for e in events if e["type"] == "status" and e["id"] == node][-1]


def test_library_lists_samples_with_api_thumbnails():
    lib = client.get("/api/library").json()
    assert [e["id"] for e in lib][:3] == ["swiss", "finna", "cubi"]
    assert lib[2]["thumbnail"] == "api/drawings/cubi/image"


def test_drawing_payload_trace_and_image():
    d = client.get("/api/drawings/cubi").json()
    assert d["image"]["url"] == "api/drawings/cubi/image"
    assert len(d["walls"]) == 14
    assert client.get("/api/drawings/cubi/trace").json()[0]["type"] == "node"
    img = client.get("/api/drawings/cubi/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"


@pytest.mark.parametrize("bad", ["nope", "..", "CUBI", "a" * 70])
def test_unknown_or_malformed_drawing_ids_are_404(bad):
    assert client.get(f"/api/drawings/{bad}").status_code == 404
    assert client.post("/api/ask", json={"drawing_id": bad, "question": "How many doors?"}).status_code == 404


def test_windows_answer_is_low_confidence_with_review_boxes():
    events = ask("How many windows are there?")
    a = answer(events)
    assert a["status"] == "answered" and a["value"] == 5 and a["confidence"] == 0.58
    assert a["review"]["class"] == "window" and a["review"]["threshold"] == 0.42
    assert [b["id"] for b in a["review"]["boxes"]] == ["w1", "w2", "w3", "w4", "w5", "w6"]
    assert len(a["citations"]) == 5
    assert last_status(events, "det")["summary"] == "5 boxes ≥ 0.42"
    assert "⚠" in last_status(events, "ver")["summary"]
    assert last_status(events, "ocr")["state"] == "skipped"
    nodes = [e["id"] for e in events if e["type"] == "node"]
    assert nodes == ["q", "plan", "det", "ocr", "geo", "bim", "vis", "ver", "ans"]


def test_doors_answer_is_confident():
    a = answer(ask("How many doors are there?"))
    assert a["value"] == 4 and a["confidence"] == 0.91


def test_printed_area_of_a_numbered_room():
    a = answer(ask("What is the area of Zimmer 2.09?", drawing="swiss"))
    assert a["value"] == 15.9 and a["unit"] == "m2"
    assert a["citations"][0]["label"] == "Text “15.90 m²”"


def test_answers_use_the_users_scale_and_walls():
    small = answer(ask("Which is the largest room?", m_per_px=0.03))["value"]
    large = answer(ask("Which is the largest room?", m_per_px=0.06))["value"]
    assert large == pytest.approx(4 * small, rel=0.01)
    walls = client.get("/api/drawings/cubi").json()["walls"]
    full = answer(ask("How long are the walls in total?", m_per_px=0.03))
    fewer = answer(ask("How long are the walls in total?", m_per_px=0.03, removed_walls=[walls[0]["id"]]))
    assert fewer["value"] < full["value"]
    added = answer(ask("How long are the walls in total?", m_per_px=0.03, added_walls=[{"id": "u1", "a": [0, 0], "b": [100, 0]}]))
    assert added["value"] == pytest.approx(full["value"] + 3.0, abs=0.01)


def test_unsupported_question_abstains_without_running_tools():
    events = ask("What colour is the roof?")
    assert answer(events)["status"] == "abstained"
    assert all(last_status(events, t)["state"] == "skipped" for t in ("det", "ocr", "geo", "bim", "vis"))


def test_windows_on_a_drawing_without_a_detector_abstain():
    assert answer(ask("How many windows are there?", drawing="finna"))["status"] == "abstained"


def test_a_failing_tool_ends_with_an_error_answer(monkeypatch):
    def boom(self, ctx):
        raise RuntimeError("model crashed")
    monkeypatch.setattr(qa.routes.RoomCount, "run", boom)
    events = ask("How many rooms are there?")
    assert answer(events)["status"] == "error"
    assert last_status(events, "geo")["state"] == "failed"


def test_suggestions_follow_the_drawing():
    cubi = client.get("/api/drawings/cubi/suggestions").json()
    assert cubi[:2] == ["How many windows are there?", "How many doors are there?"]
    swiss = client.get("/api/drawings/swiss/suggestions").json()
    assert any(q.startswith("What is the area of") for q in swiss)


def review_record(**over):
    record = {"schema": "drawing-qa-review-v1", "drawing": {"id": "cubi", "image_size": [687, 455]},
              "question": "How many windows are there?", "class": "window", "user_count": 1,
              "model": {"value": 5, "confidence": 0.58, "threshold": 0.42, "scores": {"w1": 0.93}},
              "objects": [{"id": "w1", "class": "window", "bbox_xyxy": [54, 21, 186, 37], "subtype": "", "status": "accepted", "proposal_id": "w1"}],
              "reviewed_at": "2026-11-14T12:00:00Z"}
    record.update(over)
    return record


def test_reviews_are_stored_and_listed():
    res = client.post("/api/reviews", json=review_record())
    assert res.status_code == 200
    rows = client.get("/api/reviews", params={"drawing_id": "cubi"}).json()
    assert rows[-1]["id"] == res.json()["id"] and rows[-1]["objects"][0]["bbox_xyxy"] == [54, 21, 186, 37]


@pytest.mark.parametrize("over, code", [
    ({"objects": [{"id": "w1", "class": "window", "bbox_xyxy": [1, 1, 5, 5], "status": "accepted", "score": 0.9}]}, 422),
    ({"objects": [{"id": "w1", "class": "window", "bbox_xyxy": [5, 5, 1, 1], "status": "accepted"}]}, 422),
    ({"schema": "something-else"}, 422),
    ({"drawing": {"id": "nope"}}, 404),
])
def test_invalid_reviews_are_rejected(over, code):
    assert client.post("/api/reviews", json=review_record(**over)).status_code == code


def test_upload_builds_a_new_drawing():
    with open(SAMPLES / "cubi.png", "rb") as fh:
        res = client.post("/api/drawings", files={"file": ("my plan.png", fh, "image/png")})
    assert res.status_code == 200, res.text
    entry = res.json()
    assert entry["id"].startswith("u-") and entry["title"] == "my plan"
    d = client.get(f"/api/drawings/{entry['id']}").json()
    assert len(d["walls"]) > 5 and d["rooms"]
    assert client.get(f"/api/drawings/{entry['id']}/image").status_code == 200
    assert entry["id"] in [e["id"] for e in client.get("/api/library").json()]
    assert answer(ask("How many rooms are there?", drawing=entry["id"]))["status"] == "answered"


def test_upload_rejects_a_non_image():
    res = client.post("/api/drawings", files={"file": ("notes.txt", b"not an image", "text/plain")})
    assert res.status_code == 422


def test_frontend_is_served():
    page = client.get("/")
    assert page.status_code == 200 and "<title>Ymir</title>" in page.text
    assert client.get("/shared/api.js").status_code == 200
