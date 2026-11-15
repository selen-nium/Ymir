"""Phase 0 gate: reference model → IFC / DXF / SVG / GLB → read the IFC back → plan section → compare.

Per Swiss sheet:
  fidelity  : IFC plan section vs Junction's source DXF drawn directly (independent path). Tests writer + reader + renderer.
  coverage  : IFC plan section vs the PNG. What a reference-quality model can explain (the DXF lacks stairs etc.).
  readback  : IFC re-read: element counts per class and IfcSpace areas equal the model's; DXF re-read layer counts.
python -m stoa_bim.phase0 [split] [limit]
"""
import json
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import cv2
import numpy as np
from shapely.geometry import Polygon

from stoa_semseg.common import image_path, pages

from . import compare as C
from . import model as M
from . import section as S
from . import writers as Wr

HOME = Path(__file__).resolve().parents[2] / 'data/png-to-bim'


def dxf_raster(page, line_px=2):
    """Junction's source DXF (building layers only) drawn straight into the PNG frame."""
    import ezdxf
    from stoa_semseg.swiss import PX_PER_PT, SRC
    s, tx, ty = page['transform_pt_per_m']
    H, W = page['height'], page['width']
    img = np.zeros((H, W), np.uint8)
    P = lambda pts: np.round(np.array([[(s * x + tx) * PX_PER_PT * 16, (-s * y + ty) * PX_PER_PT * 16] for x, y in pts])).astype(np.int32)
    msp = ezdxf.readfile(SRC / f"{page['id'].split('/', 1)[1]}.dxf").modelspace()
    for e in msp:
        lay = e.dxf.layer
        if lay not in ('WALL', 'DOOR', 'WINDOW', 'FIXTURE'):
            continue
        if e.dxftype() == 'LWPOLYLINE':
            pts = P([q for q in e.get_points('xy')])
            if lay == 'WALL':
                cv2.fillPoly(img, [pts], 1, shift=4)
            cv2.polylines(img, [pts], bool(e.closed), 1, line_px, shift=4)
        elif e.dxftype() in ('ARC', 'CIRCLE'):
            c, r = e.dxf.center, e.dxf.radius
            a0, a1 = (math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)) if e.dxftype() == 'ARC' else (0, 2 * math.pi)
            if a1 < a0:
                a1 += 2 * math.pi
            pts = P([(c.x + r * math.cos(a), c.y + r * math.sin(a)) for a in np.linspace(a0, a1, 32)])
            cv2.polylines(img, [pts], False, 1, line_px, shift=4)
    return img.astype(bool)


def readback(m, ifc_path, dxf_path):
    import ezdxf
    import ifcopenshell
    f = ifcopenshell.open(str(ifc_path))
    want = {'IfcDoor': len(m['doors']), 'IfcWindow': len(m['windows']), 'IfcFurnishingElement': len(m['fixtures']),
            'IfcSpace': len(m['spaces']), 'IfcWall': len(m['walls']) + len(m['doors']) + 2 * len(m['windows'])}
    have = {k: len(f.by_type(k)) for k in want}
    areas_ok = 0
    k2 = {s.get('number') or s['id']: M.poly(s).area for s in m['spaces']}
    for sp in f.by_type('IfcSpace'):
        it = sp.Representation.Representations[0].Items[0]
        a = Polygon([p.Coordinates for p in it.SweptArea.OuterCurve.Points]).area
        areas_ok += abs(a - k2.get(sp.Name, -1)) <= 0.005 * max(a, 1e-9)
    d = ezdxf.readfile(str(dxf_path)).modelspace()
    lay = {}
    for e in d:
        lay[e.dxf.layer] = lay.get(e.dxf.layer, 0) + 1
    return {'ifc_counts_match': have == want, 'ifc_counts': have, 'space_areas_match': areas_ok == len(m['spaces']),
            'ifc_schema': f.schema, 'dxf_layers': lay}


def _job(page):
    from stoa_iterative import text
    t = time.time()
    out = HOME / 'phase0' / page['id'].replace('/', '__')
    out.mkdir(parents=True, exist_ok=True)
    m = M.swiss_reference(page)
    (out / 'model.json').write_text(json.dumps(m))
    Wr.to_ifc(m, out / 'model.ifc')
    Wr.to_dxf(m, out / 'model.dxf')
    Wr.to_svg(m, out / 'model.svg')
    Wr.ifc_to_glb(out / 'model.ifc', out / 'model.glb')
    ink, _ = S.rasterize(S.section(out / 'model.ifc'), m['frame'])
    toks = text.load('swiss', page['id']) if text.path('swiss', page['id']).exists() else []
    png = C.png_ink(page, image_path(page), toks)
    cov, _, _, _ = C.compare(png, ink)
    ref = dxf_raster(page)
    x0, y0, x1, y1 = page['crop']
    crop = np.zeros_like(ref)
    crop[y0:y1, x0:x1] = True
    fid, _, _, _ = C.compare(ref & crop, ink.astype(bool) & crop, tol=1.5)
    rb = readback(m, out / 'model.ifc', out / 'model.dxf')
    return {'id': page['id'], 'elements': M.summary(m), 'coverage': cov, 'fidelity': fid, 'readback': rb,
            'files_kb': {p.suffix: round(p.stat().st_size / 1024) for p in out.iterdir()}, 'seconds': round(time.time() - t, 1)}


def run(split='validation', limit=None, workers=12):
    P = pages('swiss', split)[:limit]
    with Pool(workers) as pool:
        rows = pool.map(_job, P, chunksize=1)
    agg = lambda key, k: round(float(np.mean([r[key][k] for r in rows])), 2)
    summ = {'pages': len(rows),
            'fidelity_explained_mean': agg('fidelity', 'explained'), 'fidelity_phantom_mean': agg('fidelity', 'phantom'),
            'fidelity_explained_min': round(min(r['fidelity']['explained'] for r in rows), 2),
            'coverage_explained_mean': agg('coverage', 'explained'), 'coverage_phantom_mean': agg('coverage', 'phantom'),
            'ifc_counts_match': sum(r['readback']['ifc_counts_match'] for r in rows),
            'space_areas_match': sum(r['readback']['space_areas_match'] for r in rows),
            'elements_total': {k: sum(r['elements'][k] for r in rows) for k in ('walls', 'doors', 'windows', 'fixtures', 'spaces')}}
    (HOME / f'phase0-{split}.json').write_text(json.dumps({'summary': summ, 'pages': rows}, indent=1, default=float))
    return summ


if __name__ == '__main__':
    split = sys.argv[1] if len(sys.argv) > 1 else 'validation'
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
    print(json.dumps(run(split, lim), indent=1))
