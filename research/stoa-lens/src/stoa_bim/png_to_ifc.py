"""One floor-plan PNG -> IFC (3D building) + DXF + SVG + vector PDF + GLB preview. No reference data needed.

python -m stoa_bim.png_to_ifc plan.png out_dir [--scale 100 --dpi 150 | --m-per-px 0.0169] [--crop x0,y0,x1,y1] [--ocr]

What it builds (stoa_bim.generate, classical part):
  walls     thick dark ink after a morphological opening, as polygons, extruded to the storey height (2.80 m default)
  openings  gaps along each wall run; a gap with glazing lines is a window, one with a swing arc or frame is a door
  stairs    groups of parallel tread lines
  rooms     enclosed free space between the walls (>= 1 m2), as IfcSpace; with --ocr, OCR words inside a room name it
Scale: metres per PNG pixel. Give it directly, or as the drawing scale and the PNG's DPI (1:100 at 150 DPI = 0.01693).
Heights are defaults (stoa_bim.model.DEFAULTS), recorded in the IFC's Stoa_Assumptions property set.
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from shapely.geometry import Point, Polygon

from . import generate as G
from . import model as M
from . import writers as Wr


def simple_rooms(m, walls, page, tokens, min_m2=1.0):
    """Rooms = connected free space enclosed by walls (not touching the plan border)."""
    x0, y0, x1, y1 = page['crop']
    barrier = walls.astype(np.uint8).copy()
    for e in m['doors'] + m['windows']:  # openings close the wall line, so rooms separate
        cv2.fillPoly(barrier, [np.round(np.asarray(M.to_px(m, e['polygon']))).astype(np.int32)], 1)
    k = max(3, int(round(0.15 / page['m_per_px'])) | 1)  # bridge small gaps left between wall pieces
    closed = cv2.morphologyEx(barrier, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8)).astype(bool)
    free = ~closed
    free[:y0, :] = free[y1:, :] = False
    free[:, :x0] = free[:, x1:] = False
    lab, n = ndi.label(free)
    border = set(np.unique(np.concatenate([lab[y0, x0:x1], lab[y1 - 1, x0:x1], lab[y0:y1, x0], lab[y0:y1, x1 - 1]])))
    k2 = page['m_per_px'] ** 2
    out = []
    for i in range(1, n + 1):
        if i in border:
            continue
        mask = (lab == i)
        if mask.sum() * k2 < min_m2:
            continue
        for p in G.polygons(mask):
            p = p.simplify(1.0)
            if p.area * k2 < min_m2:
                continue
            name = next((t['text'] for t in tokens if t.get('kind', 'word') == 'word'
                         and p.contains(Point((t['bbox'][0] + t['bbox'][2]) / 2, (t['bbox'][1] + t['bbox'][3]) / 2))), None)
            out.append({'id': f'S{len(out)}', 'polygon': M.px_to_m(m, list(p.exterior.coords)[:-1]), 'holes': [],
                        'number': None, 'name': name})
    return out


def run(png, out, m_per_px, crop=None, ocr=False):
    png, out = Path(png).resolve(), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    W, H = Image.open(png).size
    page = {'id': png.stem, 'width': W, 'height': H, 'crop': list(crop) if crop else [0, 0, W, H],
            'm_per_px': m_per_px, 'image_abs': str(png)}
    tokens = []
    if ocr:
        from rapidocr import RapidOCR
        r = RapidOCR()(np.asarray(Image.open(png).convert('RGB')))
        for b, t in zip(r.boxes or [], r.txts or []):
            b = np.asarray(b, float)
            tokens.append({'text': t, 'kind': 'word', 'bbox': [b[:, 0].min(), b[:, 1].min(), b[:, 0].max(), b[:, 1].max()]})
    m = G.generate(page, tokens)
    if not m['spaces']:
        gray, sat = G._gray(page)
        walls, _ = G.wall_mask(page, gray, sat, tokens)
        m['spaces'] = simple_rooms(m, walls, page, tokens)
    m['frame']['scale_source'] = 'given on the command line'
    (out / 'model.json').write_text(json.dumps(m))
    Wr.to_ifc(m, out / 'model.ifc')
    Wr.to_dxf(m, out / 'model.dxf')
    Wr.to_svg(m, out / 'model.svg')
    Wr.to_pdf(m, out / 'model.pdf')
    Wr.ifc_to_glb(out / 'model.ifc', out / 'model.glb')
    return {k: len(m[k]) for k in ('walls', 'doors', 'windows', 'stairs', 'spaces')}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('png')
    ap.add_argument('out')
    ap.add_argument('--m-per-px', type=float)
    ap.add_argument('--scale', type=float, default=100, help='drawing scale denominator, e.g. 100 for 1:100')
    ap.add_argument('--dpi', type=float, default=150, help='DPI the PNG was rendered at')
    ap.add_argument('--crop', help='x0,y0,x1,y1: the plan area in pixels (default: whole image)')
    ap.add_argument('--ocr', action='store_true', help='use RapidOCR to mask text and name rooms')
    a = ap.parse_args()
    k = a.m_per_px or a.scale * 0.0254 / a.dpi
    crop = [int(v) for v in a.crop.split(',')] if a.crop else None
    print(json.dumps(run(a.png, a.out, k, crop, a.ocr)))
