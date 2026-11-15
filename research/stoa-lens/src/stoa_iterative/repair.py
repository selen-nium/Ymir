"""Arm D: rule-based repairs (no VLM), one rule per flag type, greedy acceptance (PLAN §6, arm D).

Every repair produces a new graph through the same heal step. An edit is kept only if it lowers the comparator
score D without breaking the guards:
  * the number of OCR room numbers covered by some room must not fall;
  * the room count may only fall if the edit was a C4-supported merge fix (it never deletes otherwise).
"""
import copy

import cv2
import numpy as np
from scipy import ndimage as ndi
from shapely.geometry import Point, Polygon

from stoa_semseg import geometry as sg
from stoa_semseg.cpu_arms import SNAP_GROW

from . import comparator as C
from .graph import heal

MIN_ROOM = {'swiss': 1500.0, 'cubicasa': 800.0}  # px², same as C1


def _add(graph, poly, source, why):
    g = copy.deepcopy(graph)
    rid = f"r{graph['iteration']}_{len(g['rooms'])}_{abs(hash(why)) % 9999}"
    g['rooms'].append({'id': rid, 'parts': [list(poly.exterior.coords)[:-1]], 'polygon': None, 'type': -1, 'number': None,
                       'name': None, 'source': source, 'confidence': 0.45, 'history': [f'{why}@{graph["iteration"]}']})
    # a new room never overrides existing ones: heal gives priority to higher confidence
    return heal(g)


def _replace(graph, rid, polys, source, why):
    g = copy.deepcopy(graph)
    old = next(r for r in g['rooms'] if r['id'] == rid)
    g['rooms'] = [r for r in g['rooms'] if r['id'] != rid]
    for k, p in enumerate(polys):
        g['rooms'].append({**{k2: old.get(k2) for k2 in ('type', 'number', 'name')}, 'id': f'{rid}.{k}',
                           'parts': [list(p.exterior.coords)[:-1]], 'polygon': None, 'source': source,
                           'confidence': old['confidence'], 'history': old['history'] + [f'{why}@{graph["iteration"]}']})
    return heal(g)


def _polys_from_mask(P, m, min_area):
    out = []
    for ring in sg.mask_to_polygons(m, tuple(P.off), grow=SNAP_GROW[P.dataset]):
        p = Polygon(ring).buffer(0)
        p = C.largest(p)
        if p is not None and p.area >= min_area:
            out.append(p)
    return out


def region_at(P, seed, max_dist=30):
    """The classical door-bridged free-space region containing (or nearest to) a seed point."""
    pt = Point(seed)
    best = None
    for reg in P.regions():
        d = reg.distance(pt)
        if d <= max_dist and (best is None or d < best[0]):
            best = (d, reg)
    return best[1] if best else None


GROW_RADII = (20, 14, 10, 6, 3)


def _region_at_radius(P, seed, r):
    """Door-bridged free-space region around a seed at bridging radius r (cached per page and radius)."""
    from skimage.segmentation import watershed
    cache = P.__dict__.setdefault('_bridged', {})
    if r not in cache:
        # text is not wall: erase OCR token boxes from the ink before bridging doors
        ink = P.ink.copy()
        for t in P.tokens:
            x0, y0, x1, y1 = [int(round(v)) for v in (t['bbox'][0] - P.off[0] - 2, t['bbox'][1] - P.off[1] - 2,
                                                      t['bbox'][2] - P.off[0] + 2, t['bbox'][3] - P.off[1] + 2)]
            ink[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = False
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        cache[r] = ndi.label(~cv2.dilate(ink.astype(np.uint8), k).astype(bool))[0]
    lab = cache[r]
    H, W = lab.shape
    x, y = int(round(seed[0] - P.off[0])), int(round(seed[1] - P.off[1]))
    if not (0 <= x < W and 0 <= y < H):
        return None
    win = lab[max(0, y - r - 12):y + r + 13, max(0, x - r - 12):x + r + 13]
    ids = [i for i in np.unique(win) if i]
    if not ids:
        return None
    vals, counts = np.unique(win[win > 0], return_counts=True)
    comp = lab == vals[np.argmax(counts)]
    if comp[0].any() or comp[-1].any() or comp[:, 0].any() or comp[:, -1].any():
        return None  # touches the crop border: outside the building
    region = cv2.dilate(comp.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 5,) * 2)).astype(bool)
    markers = comp.astype(np.int32)
    grown = watershed(np.zeros(lab.shape, np.uint8), markers, mask=P.free & region) == 1
    m = sg._finish(grown.astype(np.int32), 1)[0]
    if m is None:
        return None
    polys = _polys_from_mask(P, m, 0)
    return max(polys, key=lambda q: q.area) if polys else None


def region_for_number(P, graph, seed, number, m_per_px):
    """Number-anchored grow: the largest bridging radius whose region holds this room number and no other,
    has a plausible size and does not overlap existing rooms by more than 30%."""
    others = [t for t in P.tokens if t['kind'] == 'room_number' and t['text'] != number]
    pts = [Point((t['bbox'][0] + t['bbox'][2]) / 2, (t['bbox'][1] + t['bbox'][3]) / 2) for t in others]
    rooms = [Polygon(r['polygon']) for r in graph['rooms']]
    k2 = (m_per_px or 0) ** 2
    for r in GROW_RADII:
        reg = _region_at_radius(P, seed, r)
        if reg is None or not reg.contains(Point(seed)):
            continue
        if any(reg.contains(q) for q in pts):
            continue
        if k2 and not (0.1 <= reg.area * k2 <= 80):
            continue
        if sum(reg.intersection(g).area for g in rooms if g.intersects(reg)) > 0.3 * reg.area:
            continue
        return reg
    return None


def split_by_seeds(P, room_poly, seeds, min_area):
    from skimage.segmentation import watershed
    m = P.mask(room_poly) & P.free
    markers = np.zeros(m.shape, np.int32)
    for k, (x, y) in enumerate(seeds, 1):
        cx, cy = int(round(x - P.off[0])), int(round(y - P.off[1]))
        cv2.circle(markers, (cx, cy), 6, k, -1)
    markers[~m] = 0
    if len(np.unique(markers)) - 1 < 2:
        return []
    lab = watershed(P.dist_to_ink.max() - P.dist_to_ink, markers, mask=m)
    parts = []
    for piece in sg._finish(lab, len(seeds)):
        if piece is not None:
            parts += _polys_from_mask(P, piece, min_area)
    return parts


def split_by_walls(P, room_poly, min_area):
    m = P.mask(room_poly) & ~cv2.dilate(P.walls.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    lab, n = ndi.label(m)
    parts = []
    for i in range(1, n + 1):
        parts += _polys_from_mask(P, lab == i, min_area)
    return parts if len(parts) >= 2 else []


def snap_room(P, room):
    inst = [{'class': room['type'], 'score': 1.0, 'polygons': [room['polygon']]}]
    out = sg.snap(inst, P.rgb, tuple(P.off), grow=SNAP_GROW[P.dataset])
    return [C.largest(Polygon(out[0]['polygons'][0]).buffer(0))] if out else []


def candidates_for(P, graph, f, other_rows, other_thr):
    """Candidate repaired graphs for one flag (each a (graph, label) pair)."""
    ds = P.dataset
    mn = MIN_ROOM[ds]
    rooms = {r['id']: r for r in graph['rooms']}
    rg = lambda rid: Polygon(rooms[rid]['polygon'], rooms[rid].get('holes') or [])
    out = []
    chk = f['check']
    if chk == 'C1' and f.get('region'):
        # Only with corroboration: empty enclosed space is often a shaft, void or courtyard (PLAN §12.2, #2-#3).
        reg = Polygon(f['region']).buffer(0)
        words = [t for t in P.tokens if t['kind'] in ('room_number', 'word') and len(t['text']) >= 2]
        has_text = any(reg.contains(Point((t['bbox'][0] + t['bbox'][2]) / 2, (t['bbox'][1] + t['bbox'][3]) / 2)) for t in words)
        other_sees = any(C.largest(Polygon(i['polygons'][0]).buffer(0)) is not None
                         and C.largest(Polygon(i['polygons'][0]).buffer(0)).intersection(reg).area > 0.5 * reg.area
                         for i in (other_rows or {}).get('instances', []) if i['score'] >= other_thr and len(i['polygons'][0]) >= 3)
        if has_text or other_sees:
            out.append((_add(graph, reg, 'classical-region', 'C1-add'), 'C1-add'))
    elif chk == 'C4' and f.get('seed'):
        reg = region_for_number(P, graph, f['seed'], f.get('number'), P.page.get('m_per_px'))
        if reg is not None:
            out.append((_add(graph, reg, 'number-region', 'C4-grow'), 'C4-grow'))
    elif chk == 'C4' and f.get('seeds') and f['room'] in rooms:
        parts = split_by_seeds(P, rg(f['room']), f['seeds'], mn / 3)
        if len(parts) >= 2:
            out.append((_replace(graph, f['room'], parts, 'split-seeds', 'C4-split'), 'C4-split'))
    elif chk == 'C2' and f['room'] in rooms:
        parts = split_by_walls(P, rg(f['room']), mn / 3)
        if parts:
            out.append((_replace(graph, f['room'], parts, 'split-walls', 'C2-split'), 'C2-split'))
    elif chk == 'C3' and f['room'] in rooms:
        parts = snap_room(P, rooms[f['room']])
        if parts and parts[0] is not None:
            out.append((_replace(graph, f['room'], parts, 'snap', 'C3-snap'), 'C3-snap'))
    elif chk == 'C5' and f['room'] in rooms:
        # outline options for this room; keep the one whose area matches the printed value best
        k2 = P.page['m_per_px'] ** 2
        opts = []
        s = snap_room(P, rooms[f['room']])
        if s and s[0] is not None:
            opts.append(('snap', s[0]))
        reg = region_at(P, Polygon(rooms[f['room']]['polygon']).representative_point().coords[0], max_dist=0)
        if reg is not None:
            opts.append(('region', reg))
        for i in (other_rows or {}).get('instances', []):
            if i['score'] >= other_thr:
                o = C.largest(Polygon(i['polygons'][0]).buffer(0))
                if o is not None and o.intersects(rg(f['room'])) and o.intersection(rg(f['room'])).area > 0.5 * o.area:
                    opts.append(('other-tool', o))
        cur = abs(rg(f['room']).area * k2 - f['printed_m2'])
        best = min(opts, key=lambda o: abs(o[1].area * k2 - f['printed_m2']), default=None)
        if best is not None and abs(best[1].area * k2 - f['printed_m2']) < cur:
            out.append((_replace(graph, f['room'], [best[1]], best[0], 'C5-' + best[0]), 'C5-' + best[0]))
    elif chk == 'C11' and f['room'] in rooms and f.get('region'):
        out.append((_replace(graph, f['room'], [Polygon(f['region']).buffer(0)], 'number-region', 'C11-extend'), 'C11-extend'))
    elif chk == 'C7' and f.get('candidate'):
        cand = C.largest(Polygon(f['candidate']).buffer(0))
        if cand is not None and cand.area >= mn / 3:
            covered = sum(cand.intersection(rg(r)).area for r in rooms) / cand.area
            if covered < 0.2:
                out.append((_add(graph, cand, 'other-tool', 'C7-add'), 'C7-add'))
    return out


def covered_numbers(P, graph):
    gs = [Polygon(r['polygon']) for r in graph['rooms']]
    n = 0
    for t in P.tokens:
        if t['kind'] == 'room_number':
            x0, y0, x1, y1 = t['bbox']
            c = Point((x0 + x1) / 2, (y0 + y1) / 2)
            n += any(g.contains(c) for g in gs)
    return n


def iterate(P, graph, other_rows, other_thr, checks, max_ops=40):
    """One iteration: flag, then try each flag's repair; keep it if D falls and the guards hold."""
    flags = C.compare(P, graph, other_rows, other_thr, checks=checks)
    d0 = C.score(flags)
    applied = []
    order = sorted(flags, key=lambda f: -C.WEIGHTS.get(f['check'], 1.0))
    for f in order[:max_ops]:
        for cand, label in candidates_for(P, graph, f, other_rows, other_thr):
            if covered_numbers(P, cand) < covered_numbers(P, graph):
                continue
            if len(cand['rooms']) < len(graph['rooms']) and label != 'C4-split':
                continue
            d1 = C.score(C.compare(P, cand, other_rows, other_thr, checks=checks))
            if d1 < d0:
                graph, d0 = cand, d1
                applied.append(label)
                break
    graph = copy.deepcopy(graph)
    graph['iteration'] += 1
    graph['disagreement'] = d0
    graph['log'].append({'event': 'iteration', 'iteration': graph['iteration'], 'flags': len(flags), 'applied': applied})
    return graph, applied, len(flags)
