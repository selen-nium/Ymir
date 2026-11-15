"""ResBIM: PNG plan + its source IFC (Junction ifc_models, MIT). Reference = the source IFC's lowest storey, cut at 1.2 m.

Alignment (scoring frame only): the source IFC's wall section is fitted to the PNG's wall fill by scale + translation
(no rotation; ResBIM plans are exported axis-aligned): bounding boxes give the start, then a small grid search on
wall-mask IoU. The fitted scale is also given to the generator as the sheet's true scale, as in every Swiss trial
(the PNG has no "1:n"; reading it from the dimension strings is left for later).
"""
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from shapely.affinity import affine_transform
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from . import model as M
from . import section as S

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'data/png-to-bim/ifcset/junction-dataset/data/ifc_models'
HOME = ROOT / 'data/png-to-bim/resbim'


def png_walls(path):
    im = np.asarray(Image.open(path).convert('RGB')).astype(np.int16)
    gray = im.mean(2)
    ink = gray < 215
    fill = (gray > 120) & (gray < 215)  # Revit's grey wall fill
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    walls = cv2.morphologyEx(fill.astype(np.uint8), cv2.MORPH_OPEN, k).astype(bool)
    return im, ink, walls


def storey_section(ifc_path):
    """Section of the lowest storey at elevation + 1.2 m, metres, IFC XY."""
    import ifcopenshell
    import ifcopenshell.util.unit
    f = ifcopenshell.open(str(ifc_path))
    k = ifcopenshell.util.unit.calculate_unit_scale(f)
    st = sorted(f.by_type('IfcBuildingStorey'), key=lambda s: s.Elevation or 0)
    z0 = (st[0].Elevation or 0) * k if st else 0.0
    return S.section(ifc_path, z=z0 + S.CUT), z0


def align(sec, walls_px, f=0.125):
    """px = sx * X + tx, py = sy * Y + ty with |sx| = |sy| = s (mirrors allowed). Search: scale around the bounding-box
    estimate, the four mirror options, translation by template matching at 1/8 resolution; score = wall-mask IoU."""
    W = unary_union([g for g in sec.get('IfcWall', [])])
    x0, y0, x1, y1 = W.bounds
    ys, xs = np.nonzero(walls_px)
    s0 = ((np.percentile(xs, 99.5) - np.percentile(xs, 0.5)) / (x1 - x0) + (np.percentile(ys, 99.5) - np.percentile(ys, 0.5)) / (y1 - y0)) / 2
    img = cv2.resize(walls_px.astype(np.uint8) * 255, None, fx=f, fy=f, interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    best = None
    for sc in np.linspace(0.8, 1.2, 41):
        s_ = s0 * sc * f
        w, h = int((x1 - x0) * s_) + 3, int((y1 - y0) * s_) + 3
        if w >= img.shape[1] or h >= img.shape[0] or w < 8 or h < 8:
            continue
        for mx in (1, -1):
            for my in (-1, 1):
                t = np.zeros((h, w), np.uint8)
                for g in getattr(W, 'geoms', [W]):
                    for ring in [g.exterior]:
                        X = np.array(ring.coords)
                        u = (X[:, 0] - x0) * s_ if mx == 1 else (x1 - X[:, 0]) * s_
                        v = (y1 - X[:, 1]) * s_ if my == -1 else (X[:, 1] - y0) * s_
                        cv2.fillPoly(t, [np.round(np.stack([u, v], 1) * 16).astype(np.int32)], 1, shift=4)
                tf = t.astype(np.float32)
                r = cv2.matchTemplate(img, tf, cv2.TM_CCORR)
                _, _, _, loc = cv2.minMaxLoc(r)
                win = img[loc[1]:loc[1] + h, loc[0]:loc[0] + w] > 0.5
                iou = (win & (t > 0)).sum() / max(1, (win | (t > 0)).sum())
                if best is None or iou > best[0]:
                    best = (iou, s0 * sc, mx, my, loc)
    iou, S_, mx, my, loc = best
    # px = sx * X + tx with sx = mx * S_, sy = my * S_
    tx = loc[0] / f - (S_ * x0 if mx == 1 else -S_ * x1)
    ty = loc[1] / f - (-S_ * y1 if my == -1 else S_ * y0)
    return {'iou': round(float(iou), 4), 's': float(S_), 'sx': float(mx * S_), 'sy': float(my * S_), 'tx': float(tx), 'ty': float(ty)}


def page_for(n):
    """A 'page' record like the Swiss ones, for ResBIM unit n (crop = the building with a margin)."""
    png = SRC / f'resbim-{n}_plan.png'
    ifc = SRC / f'resbim-{n}_model.ifc'
    im, ink, walls = png_walls(png)
    sec, z0 = storey_section(ifc)
    a = align(sec, walls)
    W = unary_union(sec.get('IfcWall', []))
    x0, y0, x1, y1 = W.bounds
    pad = 0.6 * a['s']
    xs_ = sorted([a['sx'] * x0 + a['tx'], a['sx'] * x1 + a['tx']])
    ys_ = sorted([a['sy'] * y0 + a['ty'], a['sy'] * y1 + a['ty']])
    crop = [int(max(0, xs_[0] - pad)), int(max(0, ys_[0] - pad)), int(min(im.shape[1], xs_[1] + pad)), int(min(im.shape[0], ys_[1] + pad))]
    return {'id': f'resbim/{n}', 'image_abs': str(png), 'ifc': str(ifc), 'width': im.shape[1], 'height': im.shape[0],
            'crop': crop, 'm_per_px': 1 / a['s'], 'align': a, 'storey_z0': z0, 'rooms': [], 'count_questions': []}, sec


def reference(page, sec):
    """Source IFC section → model in the PNG-derived metre frame."""
    a = page['align']
    k = page['m_per_px']
    T = lambda g: affine_transform(g, [a['sx'] * k, 0, 0, -a['sy'] * k, a['tx'] * k, -a['ty'] * k])  # IFC m -> (px*k, -py*k)
    m = M.new_model(page, 'reference: source IFC (ResBIM), section at 1.2 m')
    for i, g in enumerate(sec.get('IfcWall', [])):
        for p in getattr(T(g), 'geoms', [T(g)]):
            if p.geom_type == 'Polygon' and p.area > 1e-4:
                m['walls'].append({'id': f'W{len(m["walls"])}', 'polygon': [list(c) for c in p.exterior.coords[:-1]],
                                   'holes': [[list(c) for c in h.coords[:-1]] for h in p.interiors], 'z0': 0.0, 'z1': 2.8})
    for key, cls in (('doors', 'IfcDoor'), ('windows', 'IfcWindow')):
        for g in sec.get(cls, []):
            g = T(g)
            if g.is_empty:
                continue
            r = (g if g.geom_type == 'Polygon' else g.convex_hull).minimum_rotated_rectangle
            if r.geom_type != 'Polygon':
                r = r.buffer(0.02)
            m[key].append({'id': f'{key[0].upper()}{len(m[key])}', 'polygon': [list(c) for c in r.exterior.coords[:-1]],
                           'z0': 0.0, 'z1': 2.1})
    return m
