import cv2
import numpy as np

from ymir.build import build_drawing
from ymir.build.floorplan.clean import clean_plan, drawing_box, remove_text, text_mask


def sheet(w=1600, h=1100):
    return np.full((h, w, 3), 255, np.uint8)


def plan(img, x0=150, y0=150, x1=1050, y1=850):
    """A two-room plan: thick outer walls and one partition, with room labels in the rooms."""
    cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 0), 12)
    cv2.line(img, ((x0 + x1) // 2, y0), ((x0 + x1) // 2, y1), (0, 0, 0), 10)
    for text, at in (("KITCHEN 4.02", (x0 + 60, y0 + 200)), ("LIVING 4.03", ((x0 + x1) // 2 + 60, y0 + 200)), ("WC", (x0 + 80, y1 - 80))):
        cv2.putText(img, text, at, cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    return img


def ink(img, box):
    x0, y0, x1, y1 = box
    return int((cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY) < 200).sum())


def test_room_labels_are_removed_and_walls_kept():
    img = plan(sheet())
    cleaned, count = remove_text(img)
    assert count >= 15  # letters and digits of the three labels
    assert ink(cleaned, (200, 300, 560, 380)) == 0  # where "KITCHEN 4.02" was
    # walls are untouched
    assert ink(cleaned, (140, 140, 1060, 162)) == ink(img, (140, 140, 1060, 162))
    assert ink(cleaned, (590, 150, 610, 850)) == ink(img, (590, 150, 610, 850))


def test_hatching_inside_a_wall_and_a_dashed_wall_line_are_kept():
    img = sheet()
    cv2.line(img, (100, 300), (1400, 300), (0, 0, 0), 2)  # two faces of a 16 px wall
    cv2.line(img, (100, 316), (1400, 316), (0, 0, 0), 2)
    for x in range(110, 1390, 14):  # hatch dashes between the faces
        cv2.line(img, (x, 304), (x + 6, 312), (0, 0, 0), 1)
    cv2.line(img, (100, 600), (1400, 600), (0, 0, 0), 2)  # a wall face drawn as a solid line...
    for x in range(100, 1400, 30):  # ...then continuing as a broken line on the same track
        cv2.line(img, (x, 640), (x + 18, 640), (0, 0, 0), 2)
    cv2.line(img, (100, 640), (700, 640), (0, 0, 0), 2)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask, _ = text_mask(gray)
    assert not mask[302:315, :].any()
    assert not mask[636:645, :].any()


def test_crop_leaves_out_title_block_and_legend():
    img = plan(sheet())
    cv2.rectangle(img, (5, 5), (1594, 1094), (0, 0, 0), 2)  # sheet frame
    cv2.line(img, (1250, 5), (1250, 1094), (0, 0, 0), 2)  # title-block divider
    cv2.rectangle(img, (1300, 80), (1550, 260), (0, 0, 180), -1)  # logo
    for i in range(8):  # title-block table
        cv2.line(img, (1260, 400 + 60 * i), (1590, 400 + 60 * i), (0, 0, 0), 1)
    cv2.rectangle(img, (160, 930), (520, 1060), (0, 0, 0), 2)  # legend box under the plan
    cleaned, _ = remove_text(img)
    x0, y0, x1, y1 = drawing_box(cleaned)
    assert x0 <= 150 and y0 <= 150 and x1 >= 1050 and y1 >= 850  # the whole plan
    assert x1 < 1250  # but not the title block


def test_sheet_without_a_dominant_drawing_keeps_the_whole_image():
    img = sheet()
    for i in range(6):  # six small details
        x, y = 100 + 240 * i, 300
        cv2.rectangle(img, (x, y), (x + 120, y + 120), (0, 0, 0), 4)
    assert drawing_box(remove_text(img)[0]) == (0, 0, 1600, 1100)


def test_build_reports_the_cleaning_and_keeps_the_original_image():
    img = plan(sheet())
    ok, buf = cv2.imencode(".png", img)
    built = build_drawing(buf.tobytes(), drawing_id="t", title="t", dataset="test", kind="Plan", file="t.png", detector="lines")
    plan_step = next(e for e in built.trace if e.get("type") == "status" and e["id"] == "plan")
    assert "text marks removed" in plan_step["summary"]
    assert built.payload["source"]["wall_input"]["text_marks_removed"] >= 15
    shown = cv2.imdecode(np.frombuffer(built.image, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert (shown < 200).sum() > 0 and built.payload["walls"]
    # The image the viewer shows still has its labels; only wall detection used the cleaned copy.
    x0, y0 = built.payload["source"]["crop"][:2]
    assert (shown[300 - y0:380 - y0, 200 - x0:560 - x0] < 200).any()
    unclean = build_drawing(buf.tobytes(), drawing_id="t", title="t", dataset="test", kind="Plan", file="t.png", detector="lines", clean=False)
    assert "wall_input" not in unclean.payload["source"]


def test_clean_plan_keeps_image_size():
    img = plan(sheet())
    ok, buf = cv2.imencode(".png", img)
    out, info = clean_plan(buf.tobytes())
    assert cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR).shape == img.shape
    assert info["text_marks_removed"] > 0 and len(info["drawing_box"]) == 4
