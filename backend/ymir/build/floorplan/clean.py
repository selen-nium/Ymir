"""Clean a plan image before wall detection: remove text, ignore sheet furniture.

Wall detectors read letters, dimension labels, legends, title blocks and logos as short walls. This pass
works on ink connected components only (no OCR, no learned model):

- Text: isolated components no bigger than a glyph are painted white. Solid square marks, such as
  columns, stay. So do words whose letters touch if they are dense like a filled bar.
- Sheet furniture: lines spanning most of the sheet (frames, title-block dividers) are ignored while
  finding the drawing. The crop is the largest cluster of remaining ink, so title blocks, legends and
  logos beside the plan fall outside it.

Thin lines are never removed: some drawing styles (CubiCasa) draw walls as thin double lines.
"""
from __future__ import annotations

import cv2
import numpy as np

INK = 200  # grey level below which a pixel is ink


def glyph_limit(h: int, w: int) -> int:
    """Largest side, in pixels, of a component that may be a character."""
    return int(np.clip(round(0.02 * min(h, w)), 10, 40))


def text_mask(gray: np.ndarray) -> tuple[np.ndarray, int]:
    """Mask of glyph-sized ink components, and how many there were."""
    ink = (gray < INK).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    if n <= 1:
        return np.zeros(gray.shape, bool), 0
    limit = glyph_limit(*gray.shape)
    x, y, w, h, area = (stats[1:, i] for i in range(5))
    big, small = np.maximum(w, h), np.minimum(w, h)
    fill = area / np.maximum(w * h, 1)
    solid_block = (fill > 0.85) & (small >= 0.5 * big) & (small >= 6)  # columns, filled markers
    glyph = (big <= limit) & ~solid_block
    # Touching letters: one line of text, a few glyphs high, sparse like lettering rather than a filled bar.
    word = (small <= limit) & (big <= 8 * limit) & (fill < 0.45) & (small >= 0.3 * limit) & (small < 0.5 * big)
    candidate = np.concatenate([[False], glyph | word])
    # Hatching inside a wall (short dashes between its two face lines) and the pieces of a broken or
    # dashed wall line look like glyphs. Keep marks that lie mostly inside a wall band or on a line's
    # track; text sits in rooms, beside walls rather than within them.
    band = wall_structure(ink, ink & ~candidate[lab], limit)
    inside = np.bincount(lab.ravel(), weights=band.ravel(), minlength=n) / np.maximum(stats[:, 4], 1)
    remove = candidate & (inside < 0.5)
    return remove[lab], int(remove.sum())


def wall_structure(all_ink: np.ndarray, lines_ink: np.ndarray, limit: int) -> np.ndarray:
    """Where wall ink belongs: between two parallel straight lines at most about a glyph apart (a wall's
    faces), and along straight lines, including broken or dashed ones."""
    long_ = 3 * limit
    across = cv2.morphologyEx(lines_ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (long_, 1)))
    down = cv2.morphologyEx(lines_ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, long_)))
    # Closing across the lines joins a pair of faces; a single line stays a line.
    band_h = cv2.morphologyEx(across, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, limit + 1)))
    band_v = cv2.morphologyEx(down, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (limit + 1, 1)))
    return (band_h | band_v | _line_tracks(all_ink, limit, True) | _line_tracks(all_ink, limit, False)).astype(bool)


def _line_tracks(ink: np.ndarray, limit: int, horizontal: bool) -> np.ndarray:
    """Long thin lines once gaps shorter than a glyph are bridged. A word closes into a block several
    pixels thick and is left out; a broken or dashed line closes into a thin line."""
    along = (limit, 1) if horizontal else (1, limit)
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, along))
    long_ = (3 * limit, 1) if horizontal else (1, 3 * limit)
    runs = cv2.morphologyEx(closed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, long_))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(runs, connectivity=8)
    thin = np.zeros(n, bool)
    thick = stats[1:, cv2.CC_STAT_HEIGHT if horizontal else cv2.CC_STAT_WIDTH]
    thin[1:] = thick <= max(4, limit // 4)
    return cv2.dilate(thin[lab].astype(np.uint8), np.ones((3, 3), np.uint8))


def remove_text(bgr: np.ndarray) -> tuple[np.ndarray, int]:
    """Copy of the image with glyph-sized ink painted white."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    mask, count = text_mask(gray)
    out = bgr.copy()
    out[mask] = 255
    return out, count


def _sheet_lines(ink: np.ndarray) -> np.ndarray:
    """Straight lines spanning most of the sheet: frames, title-block and legend dividers."""
    h, w = ink.shape
    # 75%: a building's own outer walls can span most of an already-cropped image; dividers span it all.
    across = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (int(0.75 * w), 1)))
    down = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(0.75 * h))))
    lines = cv2.dilate(across | down, np.ones((3, 3), np.uint8))
    return lines.astype(bool)


def drawing_box(clean_bgr: np.ndarray) -> tuple[int, int, int, int]:
    """Box around the main drawing: the largest cluster of ink once sheet lines are ignored.
    Clusters that overlap it, such as a balcony across a small gap, are kept with it. The margin
    covers outer walls that were ignored as long lines."""
    h, w = clean_bgr.shape[:2]
    pad = max(20, int(round(0.015 * max(h, w))))
    ink = (cv2.cvtColor(clean_bgr, cv2.COLOR_BGR2GRAY) < INK).astype(np.uint8)
    ink[_sheet_lines(ink)] = 0
    if not ink.any():
        return (0, 0, w, h)
    r = max(3, int(round(0.012 * max(h, w))))
    blobs = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (2 * r + 1, 2 * r + 1)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(blobs, connectivity=8)
    if n <= 1:
        return (0, 0, w, h)
    boxes = [(x, y, x + bw, y + bh) for x, y, bw, bh, _ in stats[1:]]
    areas = [(b[2] - b[0]) * (b[3] - b[1]) for b in boxes]
    main = list(boxes[int(np.argmax(areas))])
    grown = True
    while grown:
        grown = False
        for b in boxes:
            inside = b[0] < main[2] and b[2] > main[0] and b[1] < main[3] and b[3] > main[1]
            if inside and (b[0] < main[0] or b[1] < main[1] or b[2] > main[2] or b[3] > main[3]):
                main = [min(main[0], b[0]), min(main[1], b[1]), max(main[2], b[2]), max(main[3], b[3])]
                grown = True
    # The dilation grew each blob by r on every side; give it back, then add the padding.
    x0, y0, x1, y1 = main[0] + r - pad, main[1] + r - pad, main[2] - r + pad, main[3] - r + pad
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    # A small box means no single drawing dominates the sheet (a sheet of details, or a blank page) and
    # the largest cluster may be the title block: use the whole image.
    if (x1 - x0) * (y1 - y0) < 0.15 * w * h:
        return (0, 0, w, h)
    return (int(x0), int(y0), int(x1), int(y1))


def clean_plan(png: bytes) -> tuple[bytes, dict]:
    """Text removed (PNG bytes, same size) and the drawing's box in that frame."""
    bgr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    cleaned, count = remove_text(bgr)
    box = drawing_box(cleaned)
    ok, buf = cv2.imencode(".png", cleaned)
    if not ok:
        raise ValueError("Could not encode the cleaned drawing")
    return buf.tobytes(), {"text_marks_removed": count, "drawing_box": list(box)}
