import cv2
import numpy as np
import pytest

from ymir.build.floorplan import converter
from ymir.build.floorplan.clean import remove_fills

M = 0.02  # metres per pixel: a 0.9 m door is 45 px


def wall(a, b, t=10):
    return {"a": list(a), "b": list(b), "thickness_px": t}


def no_ink():
    return np.zeros((400, 800), bool)


def test_a_break_narrower_than_any_opening_is_one_wall():
    walls, found = converter.bridge_collinear([wall((100, 200), (300, 200)), wall((315, 200), (600, 200))], no_ink(), M)
    assert len(walls) == 1 and found == []
    assert sorted((walls[0]["a"][0], walls[0]["b"][0])) == [100, 600]


def test_a_door_width_gap_with_nothing_drawn_is_a_doorway_and_the_wall_runs_through():
    walls, found = converter.bridge_collinear([wall((100, 200), (300, 200)), wall((345, 200), (600, 200))], no_ink(), M)
    assert len(walls) == 1
    assert [o["kind"] for o in found] == ["passage"]
    assert found[0]["box"] == [300, 195, 345, 205]


def test_lines_drawn_along_the_gap_make_it_a_window_or_a_door():
    thin = no_ink()
    for y in (196, 200, 204):  # frame and glazing
        thin[y, 300:400] = True
    _, found = converter.bridge_collinear([wall((100, 200), (300, 200)), wall((400, 200), (600, 200))], thin, M)
    assert [o["kind"] for o in found] == ["window"]
    thin = no_ink()
    thin[200, 300:345] = True  # a door frame line
    _, found = converter.bridge_collinear([wall((100, 200), (300, 200)), wall((345, 200), (600, 200))], thin, M)
    assert [o["kind"] for o in found] == ["door"]


def test_a_wide_bare_gap_and_a_gap_with_a_wall_across_it_stay_open():
    walls, found = converter.bridge_collinear([wall((100, 200), (300, 200)), wall((420, 200), (600, 200))], no_ink(), M)
    assert len(walls) == 2 and found == []  # 2.4 m with nothing drawn
    crossing = [wall((100, 200), (300, 200)), wall((345, 200), (600, 200)), wall((322, 100), (322, 300))]
    walls, found = converter.bridge_collinear(crossing, no_ink(), M)
    assert len(walls) == 3 and found == []


def test_angled_walls_are_bridged_too():
    a, b = (100.0, 100.0), (241.4, 241.4)  # 45 degrees
    c, d = (273.2, 273.2), (400.0, 400.0)  # 45 px further along the same line
    walls, found = converter.bridge_collinear([wall(a, b), wall(c, d)], no_ink(), M)
    assert len(walls) == 1 and [o["kind"] for o in found] == ["passage"]


@pytest.mark.skipif(not converter.available(), reason="research/stoa-lens converter not present")
def test_walls_reach_into_a_pier_and_the_rest_of_it_becomes_a_block():
    mask = np.zeros((500, 700), np.uint8)
    cv2.rectangle(mask, (100, 240), (400, 252), 1, -1)  # wall into the pier from the left
    cv2.rectangle(mask, (430, 100), (442, 220), 1, -1)  # wall into it from above
    cv2.fillPoly(mask, [np.array([[400, 220], [450, 220], [470, 262], [400, 262]], np.int32)], 1)  # splayed corner pier
    walls = converter.segments_from_wall_mask(mask.astype(bool), max_thick=80)
    horizontal = next(w for w in walls if abs(w["a"][1] - w["b"][1]) < 2)
    assert max(horizontal["a"][0], horizontal["b"][0]) > 410  # ran on into the pier
    blocks = converter.wall_blocks(mask.astype(bool), walls, M)
    assert len(blocks) >= 1 and all(len(b["polygon"]) >= 3 for b in blocks)
    big = np.zeros((500, 700), np.uint8)
    cv2.rectangle(big, (100, 100), (400, 400), 1, -1)  # a filled table: thicker than any wall
    assert converter.wall_blocks(big.astype(bool), [], M) == []


def test_room_tints_are_removed_but_grey_walls_and_off_white_paper_are_kept():
    img = np.full((800, 1200, 3), 255, np.uint8)
    cv2.rectangle(img, (200, 200), (700, 600), (205, 222, 205), -1)  # pale green room
    cv2.rectangle(img, (200, 200), (700, 600), (20, 20, 20), 3)  # its outline
    cv2.rectangle(img, (750, 200), (770, 600), (170, 170, 170), -1)  # grey wall band
    out, share = remove_fills(img)
    assert share > 0.1
    assert (out[400, 450] == 255).all()  # the tint is gone
    assert (out[400, 201] < 60).all()  # the outline stays
    assert (out[400, 760] == 170).all()  # so does the grey wall
    paper = np.full((800, 1200, 3), 232, np.uint8)  # a scan on off-white paper
    cv2.line(paper, (100, 100), (1100, 100), (120, 120, 120), 3)
    same, share = remove_fills(paper)
    assert share == 0 and (same == paper).all()
