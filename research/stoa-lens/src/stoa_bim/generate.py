"""G_0 v0: PNG → building model with no reference (Swiss style: hatched walls, thin-line openings).

Walls    : non-white, unsaturated pixels; a morphological opening removes thin lines (doors, fixtures, text, glazing),
           leaving the wall bands. Contours → polygons (Douglas-Peucker, 1 px).
Openings : gaps in a wall line. The wall mask is closed with horizontal and vertical line kernels up to OPEN_MAX m;
           what the closing adds, inside a band of wall thickness, is an opening candidate. Classified by the ink in it:
           parallel thin lines across the gap → window; otherwise a door if swing-arc ink is found beside the gap
           (the hinge side and swing direction are chosen by the ink they cover); otherwise an open passage (no element).
Rooms    : the frozen room pipeline's graph (EoMT-L + zoom + heal + arm D, n = 2) → IfcSpace; numbers and names from OCR.
"""
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union


from . import model as M

ROOT = Path(__file__).resolve().parents[2]
OPEN_MIN, OPEN_MAX = 0.55, 3.6  # m: door and window widths considered
ROOM_GRAPHS = ROOT / 'data/iterative-structuring/graphs/armD-noC5-eomt-finetuned-zoom-z2-iou0.8-eomt-finetuned-zoom-z2-iou0.8/swiss'


def img_path(page):
    if page.get('image_abs'):
        return page['image_abs']
    from stoa_semseg.common import image_path  # research datasets only; single PNGs pass image_abs
    return image_path(page)


def _gray(page):
    im = np.asarray(Image.open(img_path(page)).convert('RGB')).astype(np.int16)
    return im.mean(2), im.max(2) - im.min(2)


def wall_mask(page, gray, sat, tokens):
    ink = (gray < 215) & (sat < 40)
    x0, y0, x1, y1 = page['crop']
    roi = np.zeros(ink.shape, bool)
    roi[y0:y1, x0:x1] = True
    ink &= roi
    for t in tokens:  # text is not wall
        a, b, c, d = [int(round(v)) for v in t['bbox']]
        ink[max(0, b - 1):d + 1, max(0, a - 1):c + 1] = False
    k = max(3, int(round(0.06 / page['m_per_px'])) | 1)  # walls are at least ~6-8 cm thick on the drawing
    # walls from all dark ink, coloured overlays included (red apartment outlines are drawn over walls)
    dark = (gray < 215) & roi
    for t in tokens:
        a_, b_, c_, d_ = [int(round(v)) for v in t['bbox']]
        dark[max(0, b_ - 1):d_ + 1, max(0, a_ - 1):c_ + 1] = False
    walls = cv2.morphologyEx(dark.astype(np.uint8), cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    walls = cv2.erode(walls, np.ones((3, 3), np.uint8)).astype(bool)  # the outline stroke sits outside the wall face
    lab, n = ndi.label(walls)
    sizes = ndi.sum(walls, lab, range(1, n + 1))
    keep = np.isin(lab, [i + 1 for i, s in enumerate(sizes) if s * page['m_per_px'] ** 2 >= 0.02])
    return keep, ink


def polygons(mask, eps=1.0, min_px=20):
    cs, hier = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    out = []
    if hier is None:
        return out
    for i, c in enumerate(cs):
        if hier[0][i][3] != -1 or cv2.contourArea(c) < min_px:
            continue
        outer = cv2.approxPolyDP(c, eps, True)[:, 0, :].astype(float)
        holes = []
        j = hier[0][i][2]
        while j != -1:
            if cv2.contourArea(cs[j]) >= min_px:
                holes.append(cv2.approxPolyDP(cs[j], eps, True)[:, 0, :].astype(float))
            j = hier[0][j][0]
        if len(outer) >= 3:
            p = Polygon(outer + 0.5, [h + 0.5 for h in holes if len(h) >= 3]).buffer(0)
            for q in getattr(p, 'geoms', [p]):
                if q.geom_type == 'Polygon' and q.area >= min_px:
                    out.append(q)
    return out


def gaps(walls, page):
    """Opening candidates: (rect in px as shapely box, axis 'h'|'v', wall thickness px).

    Scan over wall pieces: for each wall run along an axis, look past each of its two ends along the same band.
    The first wall pixel met (a collinear piece or a perpendicular wall) closes the gap; widths in [OPEN_MIN, OPEN_MAX]."""
    k = page['m_per_px']
    lo, hi = int(OPEN_MIN / k), int(OPEN_MAX / k)
    piece = max(5, int(0.45 / k))
    out, seen = [], []
    H, W = walls.shape
    for axis in ('h', 'v'):
        line = cv2.morphologyEx(walls.astype(np.uint8), cv2.MORPH_OPEN,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (piece, 1) if axis == 'h' else (1, piece))).astype(bool)
        lab, n = ndi.label(line)
        for sl in ndi.find_objects(lab):
            if sl is None:
                continue
            (a0, a1), (b0, b1) = ((sl[1].start, sl[1].stop), (sl[0].start, sl[0].stop)) if axis == 'h' else ((sl[0].start, sl[0].stop), (sl[1].start, sl[1].stop))
            thick = b1 - b0
            if thick < 3 or thick > 0.6 / k:
                continue
            band = slice(b0 + max(1, thick // 4), b1 - max(1, thick // 4))  # inner part of the band
            for end, step in ((a1, 1), (a0 - 1, -1)):
                # walk from the end until a wall pixel fills most of the band
                d = 1
                stop = None
                while d <= hi:
                    x = end + step * d
                    if not (0 <= x < (W if axis == 'h' else H)):
                        break
                    col = walls[band, x] if axis == 'h' else walls[x, band]
                    if col.size and col.mean() > 0.6:
                        stop = x
                        break
                    d += 1
                if stop is None or d - 1 < lo:
                    continue
                g0, g1 = sorted((end + step, stop - step))
                rect = box(g0, b0, g1 + 1, b1) if axis == 'h' else box(b0, g0, b1, g1 + 1)
                if any(rect.intersection(q).area > 0.5 * min(rect.area, q.area) for q in seen):
                    continue
                seen.append(rect)
                out.append((rect, axis, thick))
    return out


def _arc_pts(c, r, a0, a1, n=12):
    return [(c[0] + r * math.cos(a), c[1] + r * math.sin(a)) for a in np.linspace(a0, a1, n)]


def classify(rect, axis, thick, ink, walls):
    """('window', None) | ('door', (leaf, swing) in px) | (None, None)."""
    x0, y0, x1, y1 = [int(v) for v in rect.bounds]
    inner = ink[y0:y1, x0:x1] & ~walls[y0:y1, x0:x1]
    # window: thin lines running along the gap (rows for 'h', columns for 'v')
    prof = inner.mean(1) if axis == 'h' else inner.mean(0)
    runs = int(ndi.label(prof > 0.8)[1])  # separate lines along the gap: door frame 2, window frame + glazing >= 3
    # door: try hinge at either end, swing to either side; score the ink under the quarter arc
    W = (x1 - x0) if axis == 'h' else (y1 - y0)
    best, bs = None, -1.0  # keep a symbol even when no arc ink is found (door drawn by its frame only)
    H_, W_ = ink.shape
    for hinge in (0, 1):
        for side in (-1, 1):
            if axis == 'h':
                hx = x0 if hinge == 0 else x1
                hy = y0 if side == -1 else y1
                tip = (hx, hy + side * W)
                a_start = math.atan2(0, (x1 - x0) * (1 if hinge == 0 else -1))
                end = (x1 if hinge == 0 else x0, hy)
            else:
                hy = y0 if hinge == 0 else y1
                hx = x0 if side == -1 else x1
                tip = (hx + side * W, hy)
                end = (hx, y1 if hinge == 0 else y0)
            c = (hx, hy)
            a0 = math.atan2(end[1] - c[1], end[0] - c[0])
            a1 = math.atan2(tip[1] - c[1], tip[0] - c[0])
            if a1 - a0 > math.pi:
                a1 -= 2 * math.pi
            if a0 - a1 > math.pi:
                a1 += 2 * math.pi
            pts = _arc_pts(c, W, a0, a1, 24)
            hit = 0
            for x, y in pts[2:-2]:
                xi, yi = int(round(x)), int(round(y))
                if 2 <= xi < W_ - 2 and 2 <= yi < H_ - 2 and (ink[yi - 2:yi + 3, xi - 2:xi + 3] & ~walls[yi - 2:yi + 3, xi - 2:xi + 3]).any():
                    hit += 1
            s = hit / max(1, len(pts) - 4)
            if s > bs:
                bs, best = s, ([c, tip], pts)
    if runs >= 3:
        return 'window', best  # best door symbol still returned (used when a detector says this is a door)
    if bs >= 0.65 or runs in (1, 2):  # a swing arc on thin ink, or a door frame drawn along the gap
        return 'door', best
    return None, best


DETECTIONS = ROOT / 'data/png-to-bim/detections'  # counting trials' RF-DETR (Swiss train) run on Junction PNGs (detect_openings.py)
DET_SCORE = 0.5


def detections(page, split):
    f = DETECTIONS / f'{split}.json'
    if not f.exists():
        return []
    d = json.loads(f.read_text())['pages'].get(page['id'], [])
    return [(int(c), float(sc), [x0, y0, x1, y1]) for c, sc, x0, y0, x1, y1 in d if sc >= DET_SCORE and int(c) in (0, 1)]


def door_from_box(b, walls, k):
    """A detected door box covers the opening and its swing. The opening is the box edge that lies on a wall line:
    wall pixels continue just beyond both ends of that edge. Returns (gap rect px, axis) or None."""
    x0, y0, x1, y1 = [int(round(v)) for v in b]
    H, W = walls.shape
    best, bs = None, 0
    t = max(3, int(0.6 / k))
    for edge in ('top', 'bottom', 'left', 'right'):
        if edge in ('top', 'bottom'):
            yc = y0 if edge == 'top' else y1
            ys = slice(max(0, yc - t), min(H, yc + t))
            left, right = walls[ys, max(0, x0 - 6):max(0, x0 - 1)], walls[ys, min(W, x1 + 1):min(W, x1 + 6)]
            sc = left.any(1).sum() + right.any(1).sum()
            rows = np.nonzero(left.any(1) | right.any(1))[0]
            if sc > bs and len(rows) >= 2:
                b0, b1 = ys.start + rows.min(), ys.start + rows.max() + 1
                best, bs = (box(x0, b0, x1, b1), 'h'), sc
        else:
            xc = x0 if edge == 'left' else x1
            xs = slice(max(0, xc - t), min(W, xc + t))
            top, bot = walls[max(0, y0 - 6):max(0, y0 - 1), xs], walls[min(H, y1 + 1):min(H, y1 + 6), xs]
            sc = top.any(0).sum() + bot.any(0).sum()
            cols = np.nonzero(top.any(0) | bot.any(0))[0]
            if sc > bs and len(cols) >= 2:
                b0, b1 = xs.start + cols.min(), xs.start + cols.max() + 1
                best, bs = (box(b0, y0, b1, y1), 'v'), sc
    if best is not None and (best[0].bounds[3] - best[0].bounds[1] if best[1] == 'h' else best[0].bounds[2] - best[0].bounds[0]) * k <= 0.6:
        return best
    return None


def door_by_ink(b, ink, walls, k):
    """Choose the door gap edge of a detector box by drawing the door on each edge (frame + leaf + swing, every
    hinge / swing side) and keeping the drawing that lands best on the PNG's thin ink."""
    x0, y0, x1, y1 = [int(round(v)) for v in b]
    H, W = ink.shape
    pad = 6
    X0, Y0, X1, Y1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
    thin = cv2.dilate((ink & ~walls)[Y0:Y1, X0:X1].astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    h = max(2, int(0.075 / k))
    best, bs = None, -1.0
    for rect, axis in ((box(x0, y0 - h, x1, y0 + h), 'h'), (box(x0, y1 - h, x1, y1 + h), 'h'),
                       (box(x0 - h, y0, x0 + h, y1), 'v'), (box(x1 - h, y0, x1 + h, y1), 'v')):
        a, b_, c, d = rect.bounds
        Wd = (c - a) if axis == 'h' else (d - b_)
        for hinge in (0, 1):
            for side in (-1, 1):
                if axis == 'h':
                    hy = b_ + h
                    hx = a if hinge == 0 else c
                    tip = (hx, hy + side * Wd)
                    end = (c if hinge == 0 else a, hy)
                else:
                    hx = a + h
                    hy = b_ if hinge == 0 else d
                    tip = (hx + side * Wd, hy)
                    end = (hx, d if hinge == 0 else b_)
                a0 = math.atan2(end[1] - hy, end[0] - hx)
                a1 = math.atan2(tip[1] - hy, tip[0] - hx)
                if a1 - a0 > math.pi:
                    a1 -= 2 * math.pi
                if a0 - a1 > math.pi:
                    a1 += 2 * math.pi
                arc = _arc_pts((hx, hy), Wd, a0, a1, 24)
                pts = arc + [tip] + [(hx + t * (tip[0] - hx), hy + t * (tip[1] - hy)) for t in np.linspace(0, 1, 8)]
                hit = sum(1 for x, y in pts if X0 <= x < X1 and Y0 <= y < Y1 and thin[int(y) - Y0, int(x) - X0])
                sc = hit / len(pts)
                if sc > bs:
                    bs, best = sc, (rect, axis, ([(hx, hy), tip], arc))
    return best if bs >= 0.5 else None


def _unused_door_fallback(b, walls, k):
    x0, y0, x1, y1 = [int(round(v)) for v in b]
    H, W = walls.shape
    # last resort: the box edge with the most wall pixels along it, frame 15 cm thick
    h = max(2, int(0.075 / k))
    cands = []
    for edge, rect, axis in (('top', box(x0, y0 - h, x1, y0 + h), 'h'), ('bottom', box(x0, y1 - h, x1, y1 + h), 'h'),
                             ('left', box(x0 - h, y0, x0 + h, y1), 'v'), ('right', box(x1 - h, y0, x1 + h, y1), 'v')):
        a, b_, c, d = [int(v) for v in rect.bounds]
        cands.append((walls[max(0, b_ - 2 * h):min(H, d + 2 * h), max(0, a - 2 * h):min(W, c + 2 * h)].sum(), rect, axis))
    sc, rect, axis = max(cands, key=lambda x: x[0])
    return (rect, axis) if sc > 0 else None


def rooms_for(page, m, tokens):
    f = ROOM_GRAPHS / (page['id'].replace('/', '__') + '.json')
    if not f.exists():
        return []
    g = json.loads(f.read_text())
    out = []
    for i, r in enumerate(g['rooms']):
        P = Polygon(r['polygon'], r.get('holes') or []).buffer(0)
        num = next((t['text'] for t in tokens if t['kind'] == 'room_number'
                    and P.contains(Point((t['bbox'][0] + t['bbox'][2]) / 2, (t['bbox'][1] + t['bbox'][3]) / 2))), None)
        name = next((t['text'] for t in tokens if t['kind'] == 'word'
                     and P.contains(Point((t['bbox'][0] + t['bbox'][2]) / 2, (t['bbox'][1] + t['bbox'][3]) / 2))), None)
        out.append({'id': f'S{i}', 'polygon': M.px_to_m(m, list(P.exterior.coords)[:-1]), 'holes': [M.px_to_m(m, list(h.coords)[:-1]) for h in P.interiors],
                    'number': num, 'name': name})
    return out


def generate(page, tokens, split=None):
    gray, sat = _gray(page)
    walls, ink = wall_mask(page, gray, sat, tokens)
    m = M.new_model(page, 'G0 v0: classical walls and openings from the PNG + room pipeline')
    for i, p in enumerate(polygons(walls)):
        m['walls'].append({'id': f'W{i}', 'polygon': M.px_to_m(m, list(p.exterior.coords)[:-1]),
                           'holes': [M.px_to_m(m, list(h.coords)[:-1]) for h in p.interiors], 'z0': 0.0, 'z1': M.DEFAULTS['storey_height']})
    nd = nw = 0
    for rect, axis, thick in gaps(walls, page):
        kind, sym = classify(rect, axis, thick, ink, walls)
        pts = M.px_to_m(m, list(rect.exterior.coords)[:-1])
        if kind == 'window':
            sym = None
            m['windows'].append({'id': f'N{nw}', 'polygon': pts, 'z0': M.DEFAULTS['window_sill'], 'z1': M.DEFAULTS['window_head']})
            nw += 1
        elif kind == 'door':
            leaf, swing = sym
            m['doors'].append({'id': f'D{nd}', 'polygon': pts, 'leaf': M.px_to_m(m, leaf), 'swing': M.px_to_m(m, swing),
                               'z0': 0.0, 'z1': M.DEFAULTS['door_head']})
            nd += 1
    if split and (DETECTIONS / f'{split}.json').exists():
        k = page['m_per_px']
        dets = detections(page, split)
        gap_doors = [(Polygon(M.to_px(m, d['polygon'])), d) for d in m['doors']]
        gap_wins = [(Polygon(M.to_px(m, w['polygon'])), w) for w in m['windows']]
        doors, wins = [], []
        for c, sc, b in sorted(dets, key=lambda d: -d[1]):
            B = box(*b)
            if c == 0:
                # the detector decides that a door exists; the gap scan (if it found this one) gives its exact gap
                hit = [d for g, d in gap_doors if g.intersects(B) and g.intersection(B).area > 0.5 * g.area]
                if hit:
                    el = dict(hit[0])
                else:
                    g = door_from_box(b, walls, k)
                    if g is not None:
                        rect, axis = g
                        thick = (rect.bounds[3] - rect.bounds[1]) if axis == 'h' else (rect.bounds[2] - rect.bounds[0])
                        _, sym = classify(rect, axis, thick, ink, walls)
                    else:
                        g = door_by_ink(b, ink, walls, k)
                        if g is None:
                            continue
                        rect, axis, sym = g
                    leaf, swing = sym
                    el = {'polygon': M.px_to_m(m, list(rect.exterior.coords)[:-1]), 'leaf': M.px_to_m(m, leaf),
                          'swing': M.px_to_m(m, swing), 'z0': 0.0, 'z1': M.DEFAULTS['door_head']}
                R = Polygon(M.to_px(m, el['polygon']))
                if any(R.intersects(Polygon(M.to_px(m, d['polygon']))) and R.intersection(Polygon(M.to_px(m, d['polygon']))).area > 0.3 * R.area for d in doors):
                    continue
                doors.append({**el, 'id': f'D{len(doors)}', 'source': f'detector {sc:.2f}' + (' + gap scan' if hit else '')})
            else:
                if any(B.intersects(Polygon(M.to_px(m, w['polygon']))) and B.intersection(Polygon(M.to_px(m, w['polygon']))).area > 0.3 * B.area for w in wins):
                    continue
                hit = [w for g, w in gap_wins if g.intersects(B) and g.intersection(B).area > 0.5 * min(g.area, B.area)]
                el = dict(hit[0]) if hit else {'polygon': M.px_to_m(m, list(B.exterior.coords)[:-1]),
                                               'z0': M.DEFAULTS['window_sill'], 'z1': M.DEFAULTS['window_head']}
                wins.append({**el, 'id': f'N{len(wins)}', 'source': f'detector {sc:.2f}' + (' + gap scan' if hit else '')})
        # gap-scan windows are precise (99.6% on validation): keep those the detector missed
        for g, w in gap_wins:
            if not any(g.intersects(Polygon(M.to_px(m, x['polygon']))) for x in wins):
                wins.append({**w, 'id': f'N{len(wins)}', 'source': 'gap scan'})
        m['doors'], m['windows'] = doors, wins
        m['source'] = 'G0 v1: classical walls + RF-DETR door/window detector (counting trials, Swiss train) + gap scan + room pipeline'
    if split and (DETECTIONS / f'{split}.json').exists():
        m['fixtures'] = fixtures_from_detections(page, split, m, ink, walls)
    m['stairs'] = stairs(page, m, ink, walls)
    m['spaces'] = rooms_for(page, m, tokens)
    return m


# ---------------------------------------------------------------- fixtures, stairs (issue 1: representable content)

FIXTURE_CLASSES = {2: ('toilet', 'TOILETPAN', 0.6, 0.40), 3: ('washbasin', 'WASHHANDBASIN', 0.7, 0.85),
                   4: ('bathtub', 'BATH', 0.6, 0.55), 5: ('shower', 'SHOWER', 0.6, 0.10)}  # class: name, IFC type, min score, height m


def trace_outline(ink, walls, b, max_curves=4):
    """The fixture's drawn outline inside its box, as polylines (px)."""
    x0, y0, x1, y1 = [int(round(v)) for v in b]
    sub = (ink & ~walls)[max(0, y0):y1, max(0, x0):x1].astype(np.uint8)
    cs, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cs = sorted(cs, key=cv2.contourArea, reverse=True)[:max_curves]
    return [[(float(p[0][0] + max(0, x0)), float(p[0][1] + max(0, y0))) for p in cv2.approxPolyDP(c, 1.0, True)] for c in cs if len(c) >= 3]


def fixtures_from_detections(page, split, m, ink, walls):
    out = []
    for c, sc, b in detections_all(page, split):
        if c not in FIXTURE_CLASSES or sc < FIXTURE_CLASSES[c][2]:
            continue
        name, ptype, _, h = FIXTURE_CLASSES[c]
        B = box(*b)
        plan = [M.px_to_m(m, cv + [cv[0]]) for cv in trace_outline(ink, walls, b)]
        out.append({'id': f'F{len(out)}', 'kind': name, 'ifc_type': ptype, 'polygon': M.px_to_m(m, list(B.exterior.coords)[:-1]),
                    'plan': plan, 'z1': h, 'source': f'detector {sc:.2f}'})
    return out


def detections_all(page, split):
    f = DETECTIONS / f'{split}.json'
    if not f.exists():
        return []
    d = json.loads(f.read_text())['pages'].get(page['id'], [])
    return [(int(c), float(sc), [x0, y0, x1, y1]) for c, sc, x0, y0, x1, y1 in d]


def stairs(page, m, ink, walls, min_lines=5):
    """Stair flights: at least min_lines parallel thin lines, evenly spaced 0.15-0.40 m, of similar extent."""
    k = page['m_per_px']
    thin = (ink & ~walls).astype(np.uint8)
    L = max(5, int(0.6 / k))
    out = []
    for axis in ('h', 'v'):
        ker = np.ones((1, L), np.uint8) if axis == 'h' else np.ones((L, 1), np.uint8)
        lines = cv2.morphologyEx(thin, cv2.MORPH_OPEN, ker)
        lab, n = ndi.label(lines)
        segs = []
        for sl in ndi.find_objects(lab):
            if sl is None:
                continue
            (a0, a1), (b0, b1) = ((sl[1].start, sl[1].stop), (sl[0].start, sl[0].stop)) if axis == 'h' else ((sl[0].start, sl[0].stop), (sl[1].start, sl[1].stop))
            if b1 - b0 <= max(3, int(0.03 / k)):
                segs.append(((a0 + a1) / 2, a1 - a0, (b0 + b1) / 2, a0, a1))
        segs.sort(key=lambda s: s[2])
        used = set()
        for i, s in enumerate(segs):
            if i in used:
                continue
            run = [i]
            for j in range(i + 1, len(segs)):
                t = segs[j]
                if j in used or abs(t[0] - s[0]) > 0.15 * s[1] or abs(t[1] - s[1]) > 0.2 * s[1]:
                    continue
                gap = (t[2] - segs[run[-1]][2]) * k
                if 0.15 <= gap <= 0.40:
                    run.append(j)
                elif gap > 0.40:
                    break
            if len(run) >= min_lines:
                sp = np.diff([segs[r][2] for r in run]) * k
                if sp.std() <= 0.04:
                    used.update(run)
                    a0 = min(segs[r][3] for r in run)
                    a1 = max(segs[r][4] for r in run)
                    c0, c1 = segs[run[0]][2], segs[run[-1]][2]
                    rect = box(a0, c0, a1, c1) if axis == 'h' else box(c0, a0, c1, a1)
                    plan = [[(segs[r][3], segs[r][2]), (segs[r][4], segs[r][2])] if axis == 'h' else [(segs[r][2], segs[r][3]), (segs[r][2], segs[r][4])] for r in run]
                    out.append({'id': f'T{len(out)}', 'polygon': M.px_to_m(m, list(rect.exterior.coords)[:-1]),
                                'plan': [M.px_to_m(m, p) for p in plan], 'risers': len(run)})
    return out


def annotation_mask(page, m, ink, walls):
    """Ink that is drawing annotation, not building: thin ink outside the building footprint (dimension lines and text,
    grid bubbles) and grid-line dashes inside it (short segments on a row/column that continues outside)."""
    k = page['m_per_px']
    H, W = ink.shape
    foot = np.zeros((H, W), np.uint8)
    for w in m['walls'] + m.get('spaces', []):
        cv2.fillPoly(foot, [np.round(np.array(M.to_px(m, w['polygon'])) * 16).astype(np.int32)], 1, shift=4)
    r = max(3, int(0.3 / k))
    foot = cv2.morphologyEx(foot, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (4 * r + 1, 4 * r + 1)))
    foot = cv2.dilate(foot, cv2.getStructuringElement(cv2.MORPH_RECT, (2 * r + 1, 2 * r + 1))).astype(bool)
    thin = ink & ~walls
    ann = thin & ~foot
    # grid lines: long lines outside the footprint; mask thin dashes on the same row/column inside
    L = max(5, int(1.0 / k))
    for axis in ('h', 'v'):
        ker = np.ones((1, L), np.uint8) if axis == 'h' else np.ones((L, 1), np.uint8)
        long_ = cv2.morphologyEx((thin & ~foot).astype(np.uint8), cv2.MORPH_OPEN, ker).astype(bool)
        idx = np.nonzero(long_.any(1) if axis == 'h' else long_.any(0))[0]
        band = np.zeros((H, W), bool)
        for i in idx:
            if axis == 'h':
                band[max(0, i - 2):i + 3, :] = True
            else:
                band[:, max(0, i - 2):i + 3] = True
        dashes = thin & foot & band
        lab, n = ndi.label(dashes)
        if n:
            sizes = ndi.sum(dashes, lab, range(1, n + 1))
            ext = ndi.find_objects(lab)
            short = [i + 1 for i, sl in enumerate(ext) if sl is not None and max(sl[0].stop - sl[0].start, sl[1].stop - sl[1].start) * k < 0.6]
            ann |= np.isin(lab, short)
    return ann
