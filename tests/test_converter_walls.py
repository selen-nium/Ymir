import math

import cv2
import numpy as np
import pytest

from ymir.build import build_drawing
from ymir.build.floorplan import converter
from ymir.build.floorplan.clean import darken, is_line_drawing

pytestmark = pytest.mark.skipif(not converter.available(), reason="research/stoa-lens converter not present")


def png(img):
    return cv2.imencode(".png", img)[1].tobytes()


def angle(w):
    return math.degrees(math.atan2(w["b"][1] - w["a"][1], w["b"][0] - w["a"][0])) % 180


def test_face_pairing_turns_a_room_outline_into_four_joined_walls():
    mask = np.zeros((600, 800), bool)
    cv2.rectangle(mask.view(np.uint8), (100, 100), (700, 500), 1, 12)
    walls = converter.segments_from_wall_mask(mask, max_thick=30)
    assert len(walls) == 4
    assert all(10 <= w["thickness_px"] <= 14 for w in walls)
    assert sorted(round(angle(w)) % 180 for w in walls) == [0, 0, 90, 90]
    # corners close, so the walls' centrelines form one room
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union
    rooms = list(polygonize(unary_union([LineString([w["a"], w["b"]]) for w in walls])))
    assert len(rooms) == 1 and rooms[0].area == pytest.approx(600 * 400, rel=0.05)


def test_face_pairing_keeps_a_diagonal_wall():
    mask = np.zeros((600, 800), np.uint8)
    box = cv2.boxPoints(((400, 300), (500, 14), 30)).astype(np.int32)
    cv2.fillPoly(mask, [box], 1)
    walls = converter.segments_from_wall_mask(mask.astype(bool), max_thick=30)
    assert len(walls) == 1
    assert angle(walls[0]) == pytest.approx(30, abs=2)
    assert math.dist(walls[0]["a"], walls[0]["b"]) == pytest.approx(500, rel=0.1)


def plan(filled: bool):
    img = np.full((1100, 1600, 3), 255, np.uint8)
    if filled:
        cv2.rectangle(img, (200, 200), (1200, 900), (40, 40, 40), 14)
        cv2.line(img, (700, 200), (700, 900), (40, 40, 40), 12)
    else:  # each wall as two thin face lines 14 px apart, in light grey
        for off in (-7, 7):
            cv2.rectangle(img, (200 + off, 200 + off), (1200 - off, 900 - off), (150, 150, 150), 2)
            cv2.line(img, (700 + off, 200), (700 + off, 900), (150, 150, 150), 2)
    cv2.circle(img, (450, 550), 60, (150, 150, 150), 2)  # a table: a single thin line, not a wall
    return img


def test_line_drawings_are_told_apart_from_filled_walls():
    assert not is_line_drawing(cv2.cvtColor(plan(filled=True), cv2.COLOR_BGR2GRAY))
    assert is_line_drawing(cv2.cvtColor(plan(filled=False), cv2.COLOR_BGR2GRAY))


def test_darken_fills_between_wall_faces_but_not_single_lines():
    out = cv2.cvtColor(darken(plan(filled=False)), cv2.COLOR_BGR2GRAY)
    assert (out[200 - 5:200 + 6, 400:600] < 100).all()  # between the two faces of the top wall
    assert (out[550, 420:480] > 200).all()  # inside the table circle stays paper


@pytest.mark.parametrize("filled", [True, False])
def test_build_uses_the_converter_for_both_drawing_styles(filled):
    built = build_drawing(png(plan(filled)), drawing_id="t", title="t", dataset="test", kind="Plan", file="t.png")
    step = next(e for e in built.trace if e.get("type") == "node" and e["id"] == "walls")
    status = next(e for e in built.trace if e.get("type") == "status" and e["id"] == "walls")
    assert step["tool"] == "Stoa wall model"
    assert ("line drawing darkened" in status["summary"]) == (not filled)
    walls = built.payload["walls"]
    assert 5 <= len(walls) <= 9  # four outer walls and the partition, perhaps split at the T
    assert len(built.payload["rooms"]) == 2  # the partition splits the outline into two rooms
