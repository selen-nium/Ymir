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


def line_walls(page, gray, tokens):
    """Walls for line-drawn plans (thin or double-line walls, coloured room fills, hatching).

    Dark ink only (fills and pale hatching drop out); long straight horizontal / vertical runs (>= 1 m) survive an
    opening with line kernels (diagonal hatching, stipple, text and furniture curves do not); the two lines of a
    double-line wall are closed into one solid; solid blobs wider than a wall are removed; everything is thickened
    to at least 0.15 m so the IFC walls have body."""
    k = page['m_per_px']
    x0, y0, x1, y1 = page['crop']
    ink = gray < 140
    roi = np.zeros(ink.shape, bool)
    roi[y0:y1, x0:x1] = True
    ink &= roi
    for t in tokens:
        a, b, c, d = [int(round(v)) for v in t['bbox']]
        ink[max(0, b - 2):d + 3, max(0, a - 2):c + 3] = False
    ink8 = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    L = max(9, int(round(1.0 / k)))
    lines = cv2.morphologyEx(ink8, cv2.MORPH_OPEN, np.ones((1, L), np.uint8)) | cv2.morphologyEx(ink8, cv2.MORPH_OPEN, np.ones((L, 1), np.uint8))
    c = max(3, int(round(0.45 / k)) | 1)  # double-line walls up to ~0.45 m thick become one solid
    walls = cv2.morphologyEx(lines, cv2.MORPH_CLOSE, np.ones((c, c), np.uint8))
    b = max(5, int(round(0.9 / k)) | 1)  # anything solid over ~0.9 m across is a fill, not a wall
    blob = cv2.morphologyEx(walls, cv2.MORPH_OPEN, np.ones((b, b), np.uint8))
    walls = walls & ~cv2.dilate(blob, np.ones((3, 3), np.uint8))
    t = max(1, int(round(0.15 / k / 2)))
    walls = cv2.dilate(walls, np.ones((2 * t + 1, 2 * t + 1), np.uint8)) & roi.astype(np.uint8)
    return walls.astype(bool), ink


def on_wall(m, d, walls, min_width=0.6):
    """A door sits in a wall line: at least min_width wide, with wall right next to it (within 0.15 m). Drops
    detector hits on furniture: chair backs are drawn as small arcs (about 0.45 m) inside rooms."""
    k = m['frame']['m_per_px']
    g = Polygon(M.to_px(m, d['polygon']))
    x0, y0, x1, y1 = g.bounds
    if max(x1 - x0, y1 - y0) * k < min_width:
        return False
    r = max(2, int(round(0.15 / k)))
    H, W = walls.shape
    a0, b0 = max(0, int(x0) - r), max(0, int(y0) - r)
    win = walls[b0:min(H, int(y1) + r + 1), a0:min(W, int(x1) + r + 1)]
    return int(win.sum()) >= 2 * max(4, int(round(0.1 / k)) ** 2)


def detect(page, png, out):
    """Doors, windows and sanitary fixtures from the counting trials' RF-DETR (fine-tuned on Swiss Dwellings).
    The image is resized to the detector's training scale (0.0339 m/px) and the boxes mapped back. The boxes are
    written where generate.py reads detections, under a split named after this run; returns that split name."""
    from stoa_counting import detect as Dt
    from .detect_openings import TRAIN_MPP, WEIGHTS
    split = 'user-' + Path(out).name
    f = G.DETECTIONS / f'{split}.json'
    if not f.exists():
        model = Dt.RFDETR('swiss', size='medium', weights=WEIGHTS)
        fct = min(1.0, page['m_per_px'] / TRAIN_MPP)
        im = Image.open(png).convert('RGB')
        small = im.resize((max(1, int(im.width * fct)), max(1, int(im.height * fct))), Image.LANCZOS)
        tmp = Path(out) / 'detector_input.png'
        small.save(tmp)
        dets = Dt.predict_page(model, tmp)
        tmp.unlink()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({'weights': str(WEIGHTS), 'train_mpp': TRAIN_MPP,
                                 'pages': {page['id']: [[c, s, x0 / fct, y0 / fct, x1 / fct, y1 / fct] for c, s, x0, y0, x1, y1 in dets]}}))
    return split


def choose_walls(page, tokens):
    """'thick' (solid or hatched walls, e.g. Swiss 1:100 sheets) unless it finds almost nothing, finds mostly
    coloured room fill, or finds under a third of what the line finder sees (thin interior walls missed)."""
    gray, sat = G._gray(page)
    thick, ink = G.wall_mask(page, gray, sat, tokens)
    lines, link = line_walls(page, gray, tokens)
    x0, y0, x1, y1 = page['crop']
    T, Lm = thick[y0:y1, x0:x1], lines[y0:y1, x0:x1]
    coloured = (T & (sat[y0:y1, x0:x1] > 40)).sum() / max(1, T.sum())
    if T.mean() < 0.01 or coloured > 0.3 or Lm.mean() > 3 * T.mean():
        return 'lines', (lines, link)
    return 'thick', (thick, ink)


def run(png, out, m_per_px, crop=None, ocr=False, walls='auto', detector=False):
    png, out = Path(png).resolve(), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    W, H = Image.open(png).size
    page = {'id': png.stem, 'width': W, 'height': H, 'crop': list(crop) if crop else [0, 0, W, H],
            'm_per_px': m_per_px, 'image_abs': str(png)}
    tokens = []
    if ocr:
        cache = out / 'ocr.json'
        if cache.exists():
            tokens = json.loads(cache.read_text())
        else:
            from rapidocr import RapidOCR
            r = RapidOCR()(np.asarray(Image.open(png).convert('RGB')))
            for b, t in zip([] if r.boxes is None else r.boxes, [] if r.txts is None else r.txts):
                b = np.asarray(b, float)
                tokens.append({'text': t, 'kind': 'word', 'bbox': [float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 0].max()), float(b[:, 1].max())]})
            cache.write_text(json.dumps(tokens))
    mode = walls
    if walls != 'thick':
        mode, (wmask, wink) = choose_walls(page, tokens) if walls == 'auto' else ('lines', line_walls(page, G._gray(page)[0], tokens))
        if mode == 'lines':
            G.wall_mask = lambda *_a, _w=wmask, _i=wink: (_w, _i)  # the generator's openings and rooms then use these walls
    split = None
    if detector:
        split = detect(page, png, out)
    m = G.generate(page, tokens, split=split)
    m['assumptions']['wall_finder'] = mode
    m['assumptions']['detector'] = bool(detector)
    gray, sat = G._gray(page)
    wmask, _ = G.wall_mask(page, gray, sat, tokens)
    n0 = len(m['doors'])
    m['doors'] = [d for d in m['doors'] if on_wall(m, d, wmask)]
    m['assumptions']['doors_dropped_off_wall'] = n0 - len(m['doors'])
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
    return {'wall_finder': mode, **{k: len(m[k]) for k in ('walls', 'doors', 'windows', 'stairs', 'spaces')}}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('png')
    ap.add_argument('out')
    ap.add_argument('--m-per-px', type=float)
    ap.add_argument('--scale', type=float, default=100, help='drawing scale denominator, e.g. 100 for 1:100')
    ap.add_argument('--dpi', type=float, default=150, help='DPI the PNG was rendered at')
    ap.add_argument('--crop', help='x0,y0,x1,y1: the plan area in pixels (default: whole image)')
    ap.add_argument('--ocr', action='store_true', help='use RapidOCR to mask text and name rooms')
    ap.add_argument('--walls', choices=['auto', 'thick', 'lines'], default='auto',
                    help='thick: solid black walls; lines: thin or double-line walls (also ignores coloured fills); auto picks')
    ap.add_argument('--detector', action='store_true', help='add the RF-DETR door / window / fixture detector (needs stoa_counting and its weights)')
    a = ap.parse_args()
    k = a.m_per_px or a.scale * 0.0254 / a.dpi
    crop = [int(v) for v in a.crop.split(',')] if a.crop else None
    print(json.dumps(run(a.png, a.out, k, crop, a.ocr, a.walls, a.detector)))
