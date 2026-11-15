"""Phase 2-3: iterative repair of the IFC against the PNG, arm D (rules, no VLM).

Each iteration:
  1. Write the IFC, read it back, cut the plan (section.py) and compare with the PNG (compare.py) -> flags.
       K1: drawn ink the IFC does not explain;  K2: IFC geometry with no ink under it.
  2. For each flag (largest first), rule candidates:
       K1 -> a missed opening (relaxed gap search + classification around the flag), or a missed wall (thick ink)
       K2 -> delete the element under it, or re-orient a door symbol (hinge / swing side)
     A candidate is kept if it lowers the local disagreement (unexplained + phantom px around the flag) on a direct
     raster of the model, drawn exactly as the IFC section draws it.
  3. Write the IFC again, read it back, compare the whole sheet: the iteration is kept only if
     unexplained + phantom ink falls (monotone acceptance); otherwise reverted and the sheet stops.
Reference-free: the reference is only used afterwards for scoring.
"""
import copy
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np
from scipy import ndimage as ndi
from shapely.geometry import Polygon, box

from stoa_semseg.common import image_path, pages

from . import compare as C
from . import generate as G
from . import model as M
from . import phase1 as P1
from . import score as Sc
from . import section as S
from . import writers as Wr

HOME = Path(__file__).resolve().parents[2] / 'data/png-to-bim'
MAX_FLAGS = 60
import os
ARM = os.environ.get('ARM', 'D')  # D: rules only; B: rules propose, DeepSeek (Verda) judges adds and deletes
JUDGE_SYSTEM = ("You check a building model (IFC) generated from an architectural floor plan PNG. You see three images of the same "
                "area: (1) the original drawing, (2) the model BEFORE an edit drawn in blue over the faded drawing, (3) the model AFTER the edit. "
                "Judge only the element in the red box. Answer with JSON {\"accept\": true|false, \"reason\": \"...\"}: accept if AFTER "
                "represents the drawing better than BEFORE (a real door, window, wall, fixture or stair added where one is drawn, or a "
                "wrong one removed). Reject if the edit removes something that is really drawn, or adds something that is not.")


def _crop_views(page, m_before, m_after, png_rgb, bb, shape):
    from PIL import Image, ImageDraw
    import base64, io
    k = page['m_per_px']
    pad = int(1.2 / k)
    H, W = shape
    x0, y0, x1, y1 = max(0, bb[0] - pad), max(0, bb[1] - pad), min(W, bb[2] + pad), min(H, bb[3] + pad)
    base = Image.fromarray(png_rgb[y0:y1, x0:x1])
    s = 512 / max(x1 - x0, y1 - y0)
    size = (max(1, int((x1 - x0) * s)), max(1, int((y1 - y0) * s)))
    urls = []
    for mm in (None, m_before, m_after):
        im = base.copy().convert('RGB')
        if mm is not None:
            faded = Image.blend(im, Image.new('RGB', im.size, (255, 255, 255)), 0.6)
            r = model_raster(mm, shape)[y0:y1, x0:x1]
            arr = np.asarray(faded).copy()
            arr[r] = (30, 90, 220)
            im = Image.fromarray(arr)
        im = im.resize(size)
        d = ImageDraw.Draw(im)
        d.rectangle([(bb[0] - x0) * s, (bb[1] - y0) * s, (bb[2] - x0) * s, (bb[3] - y0) * s], outline=(220, 30, 30), width=3)
        buf = io.BytesIO()
        im.save(buf, format='JPEG', quality=85)
        urls.append('data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode())
    return urls


def judge(page, m_before, m_after, png_rgb, bb, label, shape):
    """DeepSeek decides whether a rule edit is right. Returns (accepted, reason). Errors and cap -> reject."""
    from stoa_iterative import verda
    urls = _crop_views(page, m_before, m_after, png_rgb, bb, shape)
    content = [{'type': 'text', 'text': f'Proposed edit: {label}. (1) drawing:'}, {'type': 'image_url', 'image_url': {'url': urls[0]}},
               {'type': 'text', 'text': '(2) BEFORE:'}, {'type': 'image_url', 'image_url': {'url': urls[1]}},
               {'type': 'text', 'text': '(3) AFTER:'}, {'type': 'image_url', 'image_url': {'url': urls[2]}}]
    body = {'messages': [{'role': 'system', 'content': JUDGE_SYSTEM}, {'role': 'user', 'content': content}],
            'temperature': 0, 'max_tokens': 200, 'response_format': {'type': 'json_object'}}
    try:
        r = verda.chat(body, f'bim-judge:{page["id"]}:{label}', reserve_in=4000, reserve_out=200)
    except verda.CapReached:
        return False, 'cap'
    t = verda.text_of(r) or ''
    try:
        d = json.loads(t[t.index('{'):t.rindex('}') + 1])
        return bool(d.get('accept')), str(d.get('reason', ''))[:200]
    except Exception:
        return False, 'unparsed'


def model_raster(m, shape, line_px=2):
    """The model drawn as the IFC plan section draws it (walls filled; opening outlines and symbols; fixtures)."""
    k = m['frame']['m_per_px']
    img = np.zeros(shape, np.uint8)
    P = lambda pts: np.round(np.array([[x / k * 16, -y / k * 16] for x, y in pts])).astype(np.int32)
    for w in m['walls']:
        cv2.fillPoly(img, [P(w['polygon'])], 1, shift=4)
        for h in w.get('holes') or []:
            cv2.fillPoly(img, [P(h)], 0, shift=4)
        cv2.polylines(img, [P(w['polygon'])], True, 1, line_px, shift=4)
    for d in m['doors']:
        cv2.polylines(img, [P(d['polygon'])], True, 1, line_px, shift=4)
        for c in Wr.door_symbol(d):
            cv2.polylines(img, [P(c)], False, 1, line_px, shift=4)
    for w in m['windows']:
        cv2.polylines(img, [P(w['polygon'])], True, 1, line_px, shift=4)
        for c in Wr.window_symbol(w):
            cv2.polylines(img, [P(c)], False, 1, line_px, shift=4)
    for x in m['fixtures']:
        cv2.polylines(img, [P(x['polygon'])], True, 1, line_px, shift=4)
    return img.astype(bool)


def local_cost(png, ink, bb, margin=40, tol=C.TOL):
    H, W = png.shape
    x0, y0, x1, y1 = max(0, bb[0] - margin), max(0, bb[1] - margin), min(W, bb[2] + margin), min(H, bb[3] + margin)
    a, b = png[y0:y1, x0:x1], ink[y0:y1, x0:x1]
    da = ndi.distance_transform_edt(~a) if a.any() else np.full(a.shape, 1e9)
    db = ndi.distance_transform_edt(~b) if b.any() else np.full(a.shape, 1e9)
    return int((a & (db > tol)).sum() + (b & (da > tol)).sum())


def relaxed_openings(page, walls, ink, bb, m):
    """Gap search around a flag with relaxed limits; returns opening dicts (model metres)."""
    k = page['m_per_px']
    pad = int(1.0 / k)
    H, W = walls.shape
    x0, y0, x1, y1 = max(0, bb[0] - pad), max(0, bb[1] - pad), min(W, bb[2] + pad), min(H, bb[3] + pad)
    sub = np.zeros_like(walls)
    sub[y0:y1, x0:x1] = walls[y0:y1, x0:x1]
    old = (G.OPEN_MIN, G.OPEN_MAX)
    G.OPEN_MIN, G.OPEN_MAX = 0.45, 4.5
    try:
        cands = G.gaps(sub, page)
    finally:
        G.OPEN_MIN, G.OPEN_MAX = old
    out = []
    for rect, axis, thick in cands:
        if not rect.intersects(box(*bb)):
            continue
        x_0, y_0, x_1, y_1 = [int(v) for v in rect.bounds]
        inner = ink[y_0:y_1, x_0:x_1] & ~walls[y_0:y_1, x_0:x_1]
        prof = inner.mean(1) if axis == 'h' else inner.mean(0)
        runs = int(ndi.label(prof > 0.6)[1])
        kind, sym = G.classify(rect, axis, thick, ink, walls)
        pts = M.px_to_m(m, list(rect.exterior.coords)[:-1])
        if kind == 'window' or (kind is None and runs >= 2):
            out.append(('window', {'polygon': pts, 'z0': M.DEFAULTS['window_sill'], 'z1': M.DEFAULTS['window_head']}))
        if kind == 'door':
            if sym is not None:
                leaf, swing = sym
                out.append(('door', {'polygon': pts, 'leaf': M.px_to_m(m, leaf), 'swing': M.px_to_m(m, swing),
                                     'z0': 0.0, 'z1': M.DEFAULTS['door_head']}))
    return out


def arc_doors(page, comp_px, m, walls, thick_m=0.15):
    """Door candidates from a swing arc: fit a circle to unexplained thin ink. Returns door dicts (both closed ends)."""
    k = page['m_per_px']
    ys, xs = np.nonzero(comp_px[1])
    if len(xs) < 25:
        return []
    x = xs + comp_px[0][0]
    y = ys + comp_px[0][1]
    A = np.stack([x, y, np.ones_like(x)], 1).astype(float)
    b = -(x.astype(float) ** 2 + y.astype(float) ** 2)
    try:
        (D, E, F), *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return []
    cx, cy = -D / 2, -E / 2
    r2 = cx * cx + cy * cy - F
    if r2 <= 0:
        return []
    r = math.sqrt(r2)
    d = np.hypot(x - cx, y - cy)
    if not (0.5 / k <= r <= 1.4 / k) or np.median(np.abs(d - r)) > 1.5:
        return []
    ang = np.arctan2(y - cy, x - cx)
    a = np.sort(np.mod(ang - ang[0] + math.pi, 2 * math.pi))
    gaps_ = np.diff(np.concatenate([a, a[:1] + 2 * math.pi]))
    i = int(np.argmax(gaps_))
    span = 2 * math.pi - gaps_[i]
    if not (math.radians(55) <= span <= math.radians(125)):
        return []
    start = a[(i + 1) % len(a)] + ang[0] - math.pi
    ends = [start, start + span]
    H, W = walls.shape
    out = []
    for e_closed, e_open in ((ends[0], ends[1]), (ends[1], ends[0])):
        # snap the closed leaf to the nearest axis: doors sit in axis-aligned walls on these drawings
        dx, dy = math.cos(e_closed), math.sin(e_closed)
        dirx, diry = (np.sign(dx), 0) if abs(dx) >= abs(dy) else (0, np.sign(dy))
        ox_, oy_ = math.cos(e_open), math.sin(e_open)
        # the open leaf is perpendicular to the wall; the hinge is the circle centre
        L = r
        # wall band: wall pixels just beyond the far jamb and just behind the hinge, across the wall
        band = []
        for t_ in (L + 4, L + 8, -4, -8):
            px, py = cx + dirx * t_, cy + diry * t_
            for off in range(-int(0.5 / k), int(0.5 / k) + 1):
                qx, qy = int(round(px + (-diry) * off)), int(round(py + dirx * off))
                if 0 <= qx < W and 0 <= qy < H and walls[qy, qx]:
                    band.append(off)
        if len(band) < 3:
            continue
        b0, b1 = min(band), max(band)
        if (b1 - b0) * k > 0.6:
            continue
        nx, ny = -diry, dirx
        P0 = (cx + nx * b0, cy + ny * b0)
        P1 = (cx + nx * (b1 + 1), cy + ny * (b1 + 1))
        Q0 = (P0[0] + dirx * L, P0[1] + diry * L)
        Q1 = (P1[0] + dirx * L, P1[1] + diry * L)
        rect = Polygon([P0, Q0, Q1, P1])
        # hinge on the wall face the leaf opens to
        side = 1 if (ox_ * nx + oy_ * ny) > 0 else -1
        hx, hy = (P1 if side > 0 else P0)
        tip = (hx + nx * side * L, hy + ny * side * L)
        endc = (hx + dirx * L, hy + diry * L)
        a0 = math.atan2(endc[1] - hy, endc[0] - hx)
        a1 = math.atan2(tip[1] - hy, tip[0] - hx)
        if a1 - a0 > math.pi:
            a1 -= 2 * math.pi
        if a0 - a1 > math.pi:
            a1 += 2 * math.pi
        arc = G._arc_pts((hx, hy), L, a0, a1, 16)
        out.append({'polygon': M.px_to_m(m, list(rect.exterior.coords)[:-1]), 'leaf': M.px_to_m(m, [(hx, hy), tip]),
                    'swing': M.px_to_m(m, arc), 'z0': 0.0, 'z1': M.DEFAULTS['door_head']})
    return out


def has_door_evidence(e, m, ink, walls, line=0.6):
    """A drawn frame line along the gap, or swing-arc ink: the door is in the drawing, never delete it."""
    r = Polygon(M.to_px(m, e['polygon']))
    x0, y0, x1, y1 = [int(v) for v in r.bounds]
    axis = 'h' if (x1 - x0) >= (y1 - y0) else 'v'
    inner = ink[y0:y1 + 1, x0:x1 + 1] & ~walls[y0:y1 + 1, x0:x1 + 1]
    if inner.size == 0:
        return False
    prof = inner.mean(1) if axis == 'h' else inner.mean(0)
    if (prof > line).any():
        return True
    _, sym = G.classify(box(x0, y0, x1, y1), axis, max(1, min(x1 - x0, y1 - y0)), ink, walls)
    return False


def door_variants(d, m):
    """The four hinge / swing orientations of a door in the same gap."""
    k = m['frame']['m_per_px']
    r = Polygon(M.to_px(m, d['polygon']))
    x0, y0, x1, y1 = r.bounds
    axis = 'h' if (x1 - x0) >= (y1 - y0) else 'v'
    W = (x1 - x0) if axis == 'h' else (y1 - y0)
    out = []
    for hinge in (0, 1):
        for side in (-1, 1):
            if axis == 'h':
                c = (x0 if hinge == 0 else x1, y0 if side == -1 else y1)
                end = (x1 if hinge == 0 else x0, c[1])
                tip = (c[0], c[1] + side * W)
            else:
                c = (x0 if side == -1 else x1, y0 if hinge == 0 else y1)
                end = (c[0], y1 if hinge == 0 else y0)
                tip = (c[0] + side * W, c[1])
            a0 = math.atan2(end[1] - c[1], end[0] - c[0])
            a1 = math.atan2(tip[1] - c[1], tip[0] - c[0])
            if a1 - a0 > math.pi:
                a1 -= 2 * math.pi
            if a0 - a1 > math.pi:
                a1 += 2 * math.pi
            arc = G._arc_pts(c, W, a0, a1, 24)
            out.append({**d, 'plan': None, 'leaf': M.px_to_m(m, [c, tip]), 'swing': M.px_to_m(m, arc)})
    return out


def curve_flags(png, cur, walls_png, min_px=25):
    """K1 arcs: unexplained thin ink with straight horizontal / vertical runs removed (mostly door swings left)."""
    un = png & ~ndi.binary_dilation(cur, iterations=3) & ~walls_png
    u8 = un.astype(np.uint8)
    straight = cv2.morphologyEx(u8, cv2.MORPH_OPEN, np.ones((1, 15), np.uint8)) | cv2.morphologyEx(u8, cv2.MORPH_OPEN, np.ones((15, 1), np.uint8))
    curves = un & ~cv2.dilate(straight, np.ones((3, 3), np.uint8)).astype(bool)
    lab, n = ndi.label(curves, structure=np.ones((3, 3)))
    out = []
    for i, sl in enumerate(ndi.find_objects(lab), 1):
        comp = lab[sl] == i
        if comp.sum() >= min_px:
            out.append(((sl[1].start, sl[0].start), comp))
    return out


def iterate(page, m, png, walls_png, ink_png, tokens):
    """One rule-based repair pass over the flags of the current IFC. Returns (new model, applied labels)."""
    shape = png.shape
    cur = model_raster(m, shape)
    applied = []
    m = copy.deepcopy(m)
    nid = len(m['doors']) + len(m['windows']) + len(m['walls'])
    # pass 1: doors from swing arcs anywhere on the sheet
    for (x0, y0), comp in curve_flags(png, cur, walls_png):
        bb = [x0, y0, x0 + comp.shape[1], y0 + comp.shape[0]]
        base = local_cost(png, cur, bb)
        best = None
        for el in arc_doors(page, ((x0, y0), comp), m, walls_png):
            R = Polygon(el['polygon'])
            if any(R.intersects(Polygon(e['polygon'])) and R.intersection(Polygon(e['polygon'])).area > 0.3 * R.area for e in m['doors'] + m['windows']):
                continue
            cand = copy.deepcopy(m)
            nid += 1
            cand['doors'].append({'id': f'Da{nid}', **el})
            c = local_cost(png, model_raster(cand, shape), bb)
            if c < base - 15 and (best is None or c < best[0]):
                best = (c, cand)
        if best is not None:
            if ARM == 'B':
                ok, why = judge(page, m, best[1], PNG_RGB[0], bb, 'K1-add_door_from_arc', shape)
                if not ok:
                    applied.append('rejected:K1-add_door_from_arc')
                    continue
            m = best[1]
            cur = model_raster(m, shape)
            applied.append('K1-add_door_from_arc')
    _, flags, _, _ = C.compare(png, cur)
    for f in flags[:MAX_FLAGS]:
        bb = f['bbox']
        base = local_cost(png, cur, bb)
        best = None
        if f['check'] == 'K1':
            for kind, el in relaxed_openings(page, walls_png, ink_png, bb, m):
                R = Polygon(el['polygon'])
                if any(R.intersects(Polygon(e['polygon'])) and R.intersection(Polygon(e['polygon'])).area > 0.3 * R.area
                       for e in m['doors'] + m['windows']):
                    continue
                cand = copy.deepcopy(m)
                nid += 1
                cand['doors' if kind == 'door' else 'windows'].append({'id': f'{"D" if kind == "door" else "N"}r{nid}', **el})
                c = local_cost(png, model_raster(cand, shape), bb)
                if c < base - 15 and (best is None or c < best[0]):
                    best = (c, cand, f'K1-add_{kind}')
            # door from a swing arc in the unexplained ink
            x0, y0, x1, y1 = bb
            un = png[y0:y1, x0:x1] & ~ndi.binary_dilation(cur[y0:y1, x0:x1], iterations=3) & ~walls_png[y0:y1, x0:x1]
            lab, n = ndi.label(un, structure=np.ones((3, 3)))
            for i in range(1, min(n, 6) + 1):
                for el in arc_doors(page, ((x0, y0), lab == i), m, walls_png):
                    R = Polygon(el['polygon'])
                    if any(R.intersects(Polygon(e['polygon'])) and R.intersection(Polygon(e['polygon'])).area > 0.3 * R.area for e in m['doors']):
                        continue
                    cand = copy.deepcopy(m)
                    nid += 1
                    cand['doors'].append({'id': f'Da{nid}', **el})
                    c = local_cost(png, model_raster(cand, shape), bb)
                    if c < base - 15 and (best is None or c < best[0]):
                        best = (c, cand, 'K1-add_door_from_arc')
            # missed wall: thick ink in the flag, not covered by a wall
            x0, y0, x1, y1 = bb
            sub = np.zeros(shape, bool)
            sub[y0:y1, x0:x1] = ink_png[y0:y1, x0:x1]
            thick = cv2.morphologyEx(sub.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)).astype(bool)
            for p in G.polygons(thick & ~cur, min_px=60):
                cand = copy.deepcopy(m)
                nid += 1
                cand['walls'].append({'id': f'Wr{nid}', 'polygon': M.px_to_m(m, list(p.exterior.coords)[:-1]), 'holes': [],
                                      'z0': 0.0, 'z1': M.DEFAULTS['storey_height']})
                c = local_cost(png, model_raster(cand, shape), bb)
                if c < base - 15 and (best is None or c < best[0]):
                    best = (c, cand, 'K1-add_wall')
        else:
            B = Polygon(M.px_to_m(m, [(bb[0], bb[1]), (bb[2], bb[1]), (bb[2], bb[3]), (bb[0], bb[3])]))
            for key in ('doors', 'windows'):
                for i, e in enumerate(m[key]):
                    if not Polygon(e['polygon']).buffer(0.3).intersects(B):
                        continue
                    variants = door_variants(e, m) if key == 'doors' else []
                    for j, v in enumerate(variants):
                        cand = copy.deepcopy(m)
                        cand[key][i] = v
                        c = local_cost(png, model_raster(cand, shape), bb)
                        if c < base - 15 and (best is None or c < best[0]):
                            best = (c, cand, 'K2-reorient_door')
                    src = str(e.get('source', ''))
                    strong = key == 'windows' and src.startswith('detector') and float(src.split()[1]) >= 0.7
                    if strong or has_door_evidence(e, m, ink_png, walls_png, line=0.6 if key == 'doors' else 0.35):
                        continue  # drawn in the PNG (frame / glazing lines, or a confident detection): never delete
                    cand = copy.deepcopy(m)
                    del cand[key][i]
                    c = local_cost(png, model_raster(cand, shape), bb)
                    if c < base - 15 and (best is None or c < best[0]):
                        best = (c, cand, f'K2-delete_{key[:-1]}')
        if best is not None:
            if ARM == 'B' and not best[2].startswith('K2-reorient'):
                ok, why = judge(page, m, best[1], PNG_RGB[0], bb, best[2], shape)
                if not ok:
                    applied.append('rejected:' + best[2])
                    continue
            m = best[1]
            cur = model_raster(m, shape)
            applied.append(best[2])
    return m, applied


PNG_RGB = [None]


def ifc_check(page, m, out, png):
    Wr.to_ifc(m, out / 'model.ifc')
    ink, _ = S.rasterize(S.section(out / 'model.ifc'), m['frame'])
    res, flags, un, ph = C.compare(png, ink)
    return res, int(un.sum() + ph.sum())


def _job(args):
    from stoa_iterative import text
    page, split, n_max = args
    t = time.time()
    toks = text.load('swiss', page['id']) if text.path('swiss', page['id']).exists() else []
    src = HOME / 'phase1' / split / page['id'].replace('/', '__')
    m = json.loads((src / 'model.json').read_text())
    out = HOME / f'repair{ARM}' / split / page['id'].replace('/', '__')
    out.mkdir(parents=True, exist_ok=True)
    png = C.png_ink(page, image_path(page), toks)
    from PIL import Image as _I
    PNG_RGB[0] = np.asarray(_I.open(image_path(page)).convert('RGB'))
    gray, sat = G._gray(page)
    walls_png, ink_png = G.wall_mask(page, gray, sat, toks)
    ref = M.swiss_reference(page)
    res, cost = ifc_check(page, m, out, png)
    curve, stopped = [], False
    for n in range(n_max + 1):
        sc = Sc.score(m, ref)
        cq = {}
        for q in page.get('count_questions', []):
            a = {'count_doors': len(m['doors']), 'count_windows': len(m['windows'])}.get(q['family'])
            if a is not None:
                cq[q['family']] = [1, int(a == q['answer'])]
        rec = {'n': n, 'coverage': res, 'cost': cost, 'score': sc, 'count_questions': cq, 'applied': []}
        if n < n_max and not stopped:
            m2, applied = iterate(page, m, png, walls_png, ink_png, toks)
            kept = [a for a in applied if not a.startswith('rejected:')]
            res2, cost2 = ifc_check(page, m2, out, png) if kept else (res, cost)
            if kept and cost2 < cost:
                m, res, cost = m2, res2, cost2
                rec['applied'] = applied
            else:
                stopped = True
                Wr.to_ifc(m, out / 'model.ifc')
        curve.append(rec)
    P1.files(m, out)
    return {'id': page['id'], 'curve': curve, 'seconds': round(time.time() - t, 1)}


def run(split='validation', n_max=4, limit=None, workers=12):
    P = pages('swiss', split)[:limit]
    with Pool(workers) as pool:
        rows = pool.map(_job, [(p, split, n_max) for p in P], chunksize=1)
    curve = []
    for n in range(n_max + 1):
        R = [r['curve'][n] for r in rows]
        tot = lambda key: [sum(x['score'][key][k] for x in R) for k in ('tp', 'pred', 'ref')]
        cq = {}
        for x in R:
            for f, (a, b) in x['count_questions'].items():
                cq.setdefault(f, [0, 0])
                cq[f][0] += a
                cq[f][1] += b
        ops = {}
        for x in R:
            for a in x['applied']:
                ops[a] = ops.get(a, 0) + 1
        curve.append({'n': n, 'walls_iou': round(float(np.mean([x['score']['walls']['iou'] for x in R])), 1),
                      'doors': Sc.prf(*tot('doors')), 'windows': Sc.prf(*tot('windows')),
                      'count_exact': {f: round(100 * b / a, 1) for f, (a, b) in cq.items()},
                      'png_explained': round(float(np.mean([x['coverage']['explained'] for x in R])), 2),
                      'png_phantom': round(float(np.mean([x['coverage']['phantom'] for x in R])), 2),
                      'applied': ops, 'sheets_changed': sum(1 for x in R if x['applied'])})
    (HOME / f'repair{ARM}-{split}.json').write_text(json.dumps({'curve': curve, 'pages': rows}, indent=1, default=float))
    return curve


if __name__ == '__main__':
    split = sys.argv[1] if len(sys.argv) > 1 else 'validation'
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    lim = int(sys.argv[3]) if len(sys.argv) > 3 else None
    for r in run(split, n, lim):
        print(json.dumps(r))
