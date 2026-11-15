"""Comparator: where the PNG and the room graph disagree, from the PNG alone (PLAN §4.2).

Each check returns flags: {"check", "why", "bbox": [x0, y0, x1, y1] (page px), "room": id or None, "weight", ...}.
Nothing here reads the reference. `true_errors` (validation only) turns the scorer's view into error regions so
each check's precision and recall can be measured (PLAN §4.4).
"""
import cv2
import numpy as np
from scipy import ndimage as ndi
from shapely.geometry import Point, Polygon, box
from shapely.strtree import STRtree

from stoa_semseg import geometry as sg
from stoa_semseg.common import load_crop

from .graph import validity

WEIGHTS = {'C11': 1.0, 'C1': 1.0, 'C2': 1.0, 'C3': 0.5, 'C4': 1.0, 'C5': 1.0, 'C6': 1.0, 'C7': 0.7, 'C8': 1.0, 'PSD': 0.5}
SWISS_NUMBER_ONLY = True


class Page:
    """Pixel maps shared by the checks (all in crop coordinates; `off` converts to page px)."""

    def __init__(self, page, tokens, dataset, closing_r):
        rgb, off = load_crop(page)
        self.page, self.dataset, self.off = page, dataset, np.array(off, float)
        self.rgb = np.asarray(rgb)
        self.free = sg.free_space(rgb)
        self.ink = ~self.free
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        self.walls = cv2.morphologyEx(self.ink.astype(np.uint8), cv2.MORPH_OPEN, k, iterations=2).astype(bool)
        self.dist_to_ink = ndi.distance_transform_edt(self.free)
        self.tokens = tokens
        self.closing_r = closing_r
        self._regions = None

    def regions(self):
        """Classical door-bridged free-space regions (candidate rooms), as polygons in page px."""
        if self._regions is None:
            from PIL import Image
            inst = sg.classical(Image.fromarray(self.rgb), tuple(self.off), self.closing_r)
            self._regions = [Polygon(i['polygons'][0]) for i in inst if i['polygons']]
        return self._regions

    def to_crop(self, poly):
        return np.round((np.asarray(poly.exterior.coords) - self.off) * 4).astype(np.int32)

    def mask(self, geom):
        m = np.zeros(self.free.shape, np.uint8)
        for poly in getattr(geom, 'geoms', [geom]):
            if poly.geom_type != 'Polygon' or poly.is_empty:
                continue
            cv2.fillPoly(m, [self.to_crop(poly)], 1, shift=2)
            for h in poly.interiors:
                cv2.fillPoly(m, [np.round((np.asarray(h.coords) - self.off) * 4).astype(np.int32)], 0, shift=2)
        return m.astype(bool)


def largest(g):
    parts = [p for p in getattr(g, 'geoms', [g]) if p.geom_type == 'Polygon' and not p.is_empty]
    return max(parts, key=lambda p: p.area) if parts else None


def _bbox(g, pad=8):
    x0, y0, x1, y1 = g.bounds
    return [round(x0 - pad, 1), round(y0 - pad, 1), round(x1 + pad, 1), round(y1 + pad, 1)]


def rooms_geom(graph):
    return [(r['id'], Polygon(r['polygon'], r.get('holes') or [])) for r in graph['rooms']]


def c1_unclaimed(P, graph, min_area):
    rooms = rooms_geom(graph)
    union = None
    if rooms:
        from shapely.ops import unary_union
        union = unary_union([g for _, g in rooms])
    out = []
    for reg in P.regions():
        if reg.area < min_area:
            continue
        covered = reg.intersection(union).area / reg.area if union is not None else 0.0
        if covered < 0.3:
            out.append({'check': 'C1', 'why': f'enclosed free space {reg.area:.0f} px², {covered:.0%} covered', 'bbox': _bbox(reg),
                        'room': None, 'seed': list(reg.representative_point().coords[0]), 'region': list(reg.exterior.coords)[:-1]})
    return out


def c2_ink_crossing(P, graph, min_len):
    out = []
    for rid, g in rooms_geom(graph):
        inner = g.buffer(-4, join_style=2)
        if inner.is_empty:
            continue
        m = P.mask(inner) & P.walls
        lab, n = ndi.label(m)
        for i, sl in enumerate(ndi.find_objects(lab), 1):
            ys, xs = sl
            length = max(ys.stop - ys.start, xs.stop - xs.start)
            if length >= min_len:
                bb = [xs.start + P.off[0], ys.start + P.off[1], xs.stop + P.off[0], ys.stop + P.off[1]]
                out.append({'check': 'C2', 'why': f'wall ink {length}px long inside room', 'bbox': bb, 'room': rid})
    return out


def c3_boundary(P, graph, max_median):
    out = []
    H, W = P.free.shape
    for rid, g in rooms_geom(graph):
        ring = g.exterior
        n = max(8, int(ring.length / 3))
        pts = np.array([ring.interpolate(i / n, normalized=True).coords[0] for i in range(n)]) - P.off
        xs, ys = np.clip(pts[:, 0].astype(int), 0, W - 1), np.clip(pts[:, 1].astype(int), 0, H - 1)
        d = P.dist_to_ink[ys, xs]
        med = float(np.median(d))
        if med > max_median:
            out.append({'check': 'C3', 'why': f'outline median {med:.1f}px from ink', 'bbox': _bbox(g), 'room': rid})
    return out


def c4_labels(P, graph):
    """Swiss: every printed room number in exactly one room; rooms holding two numbers are merges."""
    nums = [t for t in P.tokens if t['kind'] == 'room_number']
    if not nums:
        return []
    rooms = rooms_geom(graph)
    tree = STRtree([g for _, g in rooms]) if rooms else None
    per_room = {}
    out = []
    for t in nums:
        x0, y0, x1, y1 = t['bbox']
        c = Point((x0 + x1) / 2, (y0 + y1) / 2)
        hit = [int(i) for i in tree.query(c)] if tree is not None else []
        hit = [i for i in hit if rooms[i][1].contains(c)]
        if not hit:
            out.append({'check': 'C4', 'why': f'room number {t["text"]} in no room', 'bbox': [x0 - 30, y0 - 30, x1 + 30, y1 + 30],
                        'room': None, 'seed': [c.x, c.y], 'number': t['text']})
        else:
            per_room.setdefault(rooms[hit[0]][0], []).append((t['text'], [c.x, c.y]))
    for rid, toks in per_room.items():
        if len(toks) >= 2:
            g = dict(rooms)[rid]
            out.append({'check': 'C4', 'why': f'room holds {len(toks)} numbers: ' + ', '.join(t for t, _ in toks), 'bbox': _bbox(g),
                        'room': rid, 'seeds': [p for _, p in toks]})
    return out


def c11_partial(P, graph, grow_min=1.08, grow_max=1.6):
    """A room holding exactly one OCR room number covers only part of the door-bridged region around that number
    (the region holds no other number). Text boxes are erased from the ink first."""
    from . import repair
    nums = [t for t in P.tokens if t['kind'] == 'room_number']
    rooms = rooms_geom(graph)
    out = []
    for rid, g in rooms:
        inside = [t for t in nums if g.contains(Point((t['bbox'][0] + t['bbox'][2]) / 2, (t['bbox'][1] + t['bbox'][3]) / 2))]
        if len(inside) != 1:
            continue
        t = inside[0]
        seed = [(t['bbox'][0] + t['bbox'][2]) / 2, (t['bbox'][1] + t['bbox'][3]) / 2]
        others = {'rooms': [r for r in graph['rooms'] if r['id'] != rid]}
        reg = repair.region_for_number(P, others, seed, t['text'], P.page.get('m_per_px'))
        if reg is None or not (grow_min * g.area <= reg.area <= grow_max * g.area):
            continue
        if g.intersection(reg).area < 0.85 * g.area:
            continue
        out.append({'check': 'C11', 'why': f'room covers {g.area / reg.area:.0%} of the region around its number {t["text"]}',
                    'bbox': _bbox(reg), 'room': rid, 'region': list(reg.exterior.coords)[:-1]})
    return out


def c5_printed_area(P, graph, tol=0.03):
    """Printed 'NN.NN m²' below a room number vs the room's computed area (needs the scale). Reported separately."""
    k = P.page.get('m_per_px')
    if not k:
        return []
    rooms = rooms_geom(graph)
    areas = [t for t in P.tokens if t['kind'] == 'area']
    out = []
    for t in areas:
        x0, y0, x1, y1 = t['bbox']
        c = Point((x0 + x1) / 2, (y0 + y1) / 2)
        try:
            val = float(t['text'].replace('m²', '').replace('m2', '').replace(',', '.').strip())
        except ValueError:
            continue
        for rid, g in rooms:
            if g.contains(c):
                a = g.area * k * k
                if abs(a - val) > tol * val:
                    out.append({'check': 'C5', 'why': f'printed {val} m², outline {a:.2f} m²', 'bbox': _bbox(g), 'room': rid,
                                'printed_m2': val})
                break
    return out


def c7_disagreement(P, graph, other_rows, thr):
    """Rooms the other tool has and this graph lacks (and the reverse), at IoU < 0.5."""
    rooms = rooms_geom(graph)
    others = [Polygon(i['polygons'][0]) for i in (other_rows or {}).get('instances', []) if i['score'] >= thr and len(i['polygons'][0]) >= 3]
    others = [largest(o.buffer(0)) for o in others]
    others = [o for o in others if o is not None and o.area > 0]
    out = []
    tree = STRtree([g for _, g in rooms]) if rooms else None
    for o in others:
        best = 0.0
        if tree is not None:
            for i in tree.query(o):
                g = rooms[int(i)][1]
                inter = o.intersection(g).area
                best = max(best, inter / (o.area + g.area - inter))
        if best < 0.5:
            out.append({'check': 'C7', 'why': f'other tool has a room here (best IoU {best:.2f})', 'bbox': _bbox(o), 'room': None,
                        'candidate': list(o.exterior.coords)[:-1]})
    return out


def c8_geometry(graph):
    out = []
    for r in graph['rooms']:
        issues = validity(r)
        if issues:
            out.append({'check': 'C8', 'why': '; '.join(issues), 'bbox': _bbox(Polygon(r['polygon']).buffer(0)), 'room': r['id']})
    for e in graph['log']:
        if e['event'] == 'dropped_unfixable' and e.get('iteration') == graph['iteration'] and e.get('bbox'):
            out.append({'check': 'C8', 'why': 'room dropped by heal step', 'bbox': e['bbox'], 'room': None})
    return out


def psd_tiles(P, graph, tile=128, top=5):
    """Ablation: per-tile radial power spectra of the PNG vs a line rendering of the rooms (Welch-style, local)."""
    H, W = P.free.shape
    render = np.zeros((H, W), np.uint8)
    for _, g in rooms_geom(graph):
        cv2.polylines(render, [P.to_crop(g)], True, 1, thickness=2, shift=2)
    ink = P.ink.astype(float)
    scores = []
    for y in range(0, H - tile + 1, tile):
        for x in range(0, W - tile + 1, tile):
            a, b = ink[y:y + tile, x:x + tile], render[y:y + tile, x:x + tile].astype(float)
            if a.mean() < 0.01:
                continue
            fa, fb = np.abs(np.fft.fftshift(np.fft.fft2(a - a.mean()))) ** 2, np.abs(np.fft.fftshift(np.fft.fft2(b - b.mean()))) ** 2
            yy, xx = np.indices(fa.shape); rr = np.hypot(yy - tile / 2, xx - tile / 2).astype(int)
            ra = np.bincount(rr.ravel(), fa.ravel()) / np.maximum(np.bincount(rr.ravel()), 1)
            rb = np.bincount(rr.ravel(), fb.ravel()) / np.maximum(np.bincount(rr.ravel()), 1)
            d = float(np.mean((np.log1p(ra[1:tile // 2]) - np.log1p(rb[1:tile // 2])) ** 2))
            scores.append((d, [x + P.off[0], y + P.off[1], x + tile + P.off[0], y + tile + P.off[1]]))
    scores.sort(reverse=True)
    return [{'check': 'PSD', 'why': f'tile spectrum distance {d:.2f}', 'bbox': bb, 'room': None} for d, bb in scores[:top]]


CONFIG = {
    'swiss': {'closing_r': 20, 'c1_min_area': 1500, 'c2_min_len': 40, 'c3_max_median': 3.0},
    'cubicasa': {'closing_r': 20, 'c1_min_area': 800, 'c2_min_len': 30, 'c3_max_median': 3.0},
}


def compare(P, graph, other_rows=None, other_thr=0.5, checks=('C1', 'C2', 'C3', 'C4', 'C5', 'C7', 'C8', 'C11')):
    c = CONFIG[P.dataset]
    flags = []
    if 'C1' in checks: flags += c1_unclaimed(P, graph, c['c1_min_area'])
    if 'C2' in checks: flags += c2_ink_crossing(P, graph, c['c2_min_len'])
    if 'C3' in checks: flags += c3_boundary(P, graph, c['c3_max_median'])
    if 'C4' in checks and P.dataset == 'swiss': flags += c4_labels(P, graph)
    if 'C5' in checks: flags += c5_printed_area(P, graph)
    if 'C11' in checks and P.dataset == 'swiss': flags += c11_partial(P, graph)
    if 'C7' in checks and other_rows is not None: flags += c7_disagreement(P, graph, other_rows, other_thr)
    if 'C8' in checks: flags += c8_geometry(graph)
    if 'PSD' in checks: flags += psd_tiles(P, graph)
    return flags


def score(flags):
    return sum(WEIGHTS.get(f['check'], 1.0) for f in flags)


def true_errors(page, scored):
    """Validation only. Error regions from the scorer's per-page result: missed reference rooms, false predicted
    rooms, and matched rooms whose area is off by more than 2%."""
    errs = []
    matched_pred = set()
    for x in scored['rooms']:
        ref = Polygon(page['rooms'][x['ri']]['polygon']).buffer(0)
        if x['matched_iou'] is None:
            errs.append(('miss', ref))
        elif x['ape'] is not None and x['ape'] > 0.02:
            errs.append(('area', ref))
    return errs


def flag_quality(flags, errs, false_rooms):
    """A flag is correct if its box touches a true error region (miss, area error, or false predicted room).
    Error recall: share of error regions touched by at least one flag."""
    regions = [g for _, g in errs] + false_rooms
    boxes = [box(*f['bbox']) for f in flags]
    tp = sum(1 for b in boxes if any(b.intersects(r) and b.intersection(r).area > 0.1 * min(r.area, b.area) for r in regions))
    found = sum(1 for r in regions if any(b.intersects(r) and b.intersection(r).area > 0.1 * min(r.area, b.area) for b in boxes))
    return tp, len(boxes), found, len(regions)
