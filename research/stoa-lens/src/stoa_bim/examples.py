"""Five worked examples: a pure PNG → model → IFC / DXF / SVG / PDF / GLB at every repair iteration n = 0..4.

data/png-to-bim/examples/<arm>/<example>/iter_<n>/{model.json, model.ifc, model.dxf, model.svg, model.pdf, model.glb, metrics.json}
python -m stoa_bim.examples [D|B]
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from stoa_semseg.common import image_path, pages

from . import compare as C
from . import generate as G
from . import ifcref as R
from . import model as M
from . import repair as Rp
from . import score as Sc
from . import section as S
from . import writers as Wr

HOME = Path(__file__).resolve().parents[2] / 'data/png-to-bim/examples'
SWISS = ['swiss/site745_bldg1634_floor7253', 'swiss/site1045_bldg2151_floor9728', 'swiss/site176_bldg363_floor1475']
RESBIM = [90, 45]


def write_all(m, d):
    d.mkdir(parents=True, exist_ok=True)
    (d / 'model.json').write_text(json.dumps(m))
    Wr.to_ifc(m, d / 'model.ifc')
    Wr.to_dxf(m, d / 'model.dxf')
    Wr.to_svg(m, d / 'model.svg')
    Wr.to_pdf(m, d / 'model.pdf')
    Wr.ifc_to_glb(d / 'model.ifc', d / 'model.glb')


def run_one(name, page, m, ref, png, walls_png, ink_png, img, n_max=4, arm='D'):
    out = HOME / arm / name
    Rp.PNG_RGB[0] = np.asarray(Image.open(img).convert('RGB'))
    rows = []
    for n in range(n_max + 1):
        d = out / f'iter_{n}'
        write_all(m, d)
        ink, _ = S.rasterize(S.section(d / 'model.ifc'), m['frame'])  # the written IFC, read back
        cov, _, _, _ = C.compare(png, ink)
        sc = Sc.score(m, ref)
        rec = {'n': n, 'explained': cov['explained'], 'phantom': cov['phantom'], 'walls_iou': sc['walls']['iou'],
               'doors': sc['doors'], 'windows': sc['windows'], 'elements': M.summary(m)}
        if n < n_max:
            m2, applied = Rp.iterate(page, m, png, walls_png, ink_png, [])
            kept = [a for a in applied if not a.startswith('rejected:')]
            if kept:
                cost0 = int((~(np.zeros(1, bool))).sum())
                m = m2
            rec['applied_to_reach_next'] = applied
        (d / 'metrics.json').write_text(json.dumps(rec, indent=1))
        rows.append(rec)
        print(name, n, rec['explained'], rec['phantom'], rec['doors']['f1'], rec['windows']['f1'], flush=True)
    (out / 'curve.json').write_text(json.dumps(rows, indent=1))
    return rows


def main(arm='D'):
    Rp.ARM = arm
    from stoa_iterative import text
    from . import scale as Scl
    only = os.environ.get('EX_ONLY', '')
    for pid in ([] if only == 'resbim' else SWISS):
        page = [p for p in pages('swiss', 'validation') if p['id'] == pid][0]
        toks = text.load('swiss', pid)
        read = Scl.swiss_scale(page, image_path(page))
        page = {**page, 'm_per_px': read['m_per_px']} if read else page
        m = G.generate(page, toks, split='validation')
        png = C.png_ink(page, image_path(page), toks)
        gray, sat = G._gray(page)
        walls_png, ink_png = G.wall_mask(page, gray, sat, toks)
        run_one(pid.replace('/', '__'), page, m, M.swiss_reference(page), png, walls_png, ink_png, image_path(page), arm=arm)
    for n in RESBIM:
        page, sec = R.page_for(n)
        ref = R.reference(page, sec)  # scoring only; the model below uses the scale read from the drawing
        read = Scl.resbim_scale(page['image_abs'], n)
        gpage = {**page, 'm_per_px': read['m_per_px']} if read else page
        m = G.generate(gpage, [])
        f = page['m_per_px'] / gpage['m_per_px']
        if abs(f - 1) > 1e-9:  # score in the reference frame
            pass
        png = C.png_ink(gpage, page['image_abs'])
        gray, sat = G._gray(gpage)
        walls_png, ink_png = G.wall_mask(gpage, gray, sat, [])
        ref_g = json.loads(json.dumps(ref))
        for key in ('walls', 'doors', 'windows'):
            for e in ref_g[key]:
                e['polygon'] = [[x / f, y / f] for x, y in e['polygon']]
                e['holes'] = [[[x / f, y / f] for x, y in h] for h in e.get('holes') or []]
        run_one(f'resbim_{n}', gpage, m, ref_g, png, walls_png, ink_png, page['image_abs'], arm=arm)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'D')
