"""ResBIM (PNG + source IFC pairs): PNG → model → IFC / DXF / SVG / GLB; IFC plan vs PNG; elements vs the source IFC.

python -m stoa_bim.resbim [first] [count]
"""
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from . import compare as C
from . import generate as G
from . import ifcref as R
from . import model as M
from . import phase1 as P1
from . import score as Sc
from . import section as S

HOME = R.HOME


def _job(n):
    t = time.time()
    try:
        page, sec = R.page_for(n)
        ref = R.reference(page, sec)
        from . import scale as Scl
        read = Scl.resbim_scale(page['image_abs'], n)
        gpage = {**page, 'm_per_px': read['m_per_px']} if read else page  # the model is built at the scale read from the drawing
        m = G.generate(gpage, [])
        m['source'] = 'G0 v0 (classical walls + gap scan) on a ResBIM PNG'
        m['frame']['scale_source'] = 'read from dimension strings' if read else 'fitted to source IFC (not read)'
        out = HOME / str(n)
        P1.files(m, out)
        (out / 'reference.json').write_text(json.dumps(ref))
        ink, _ = S.rasterize(S.section(out / 'model.ifc'), m['frame'])
        png = C.png_ink(page, page['image_abs'])
        cov, _, _, _ = C.compare(png, ink)
        gray, sat = G._gray(page)
        walls_png, ink_png = G.wall_mask(gpage, gray, sat, [])
        ann = G.annotation_mask(gpage, m, ink_png, walls_png)
        cov_b, _, _, _ = C.compare(png & ~ann, ink)
        cov['building_explained'], cov['building_phantom'] = cov_b['explained'], cov_b['phantom']
        f = page['m_per_px'] / m['frame']['m_per_px']  # scoring only: map into the reference frame (pixel geometry)
        mm = json.loads(json.dumps(m))
        for key in ('walls', 'doors', 'windows', 'fixtures', 'stairs'):
            for e in mm.get(key, []):
                e['polygon'] = [[x * f, y * f] for x, y in e['polygon']]
                e['holes'] = [[[x * f, y * f] for x, y in h] for h in e.get('holes') or []]
        sc = Sc.score(mm, ref)
        sc['scale_error_pct'] = round(100 * (m['frame']['m_per_px'] / page['m_per_px'] - 1), 2)
        return {'id': page['id'], 'align_iou': page['align']['iou'], 'm_per_px': page['m_per_px'], 'elements': M.summary(m),
                'ref_elements': M.summary(ref), 'coverage': cov, 'score': sc, 'seconds': round(time.time() - t, 1)}
    except Exception as e:
        return {'id': f'resbim/{n}', 'error': f'{type(e).__name__}: {e}'[:300]}


def run(first=0, count=50, workers=10):
    ids = sorted(int(p.name.split('-')[1].split('_')[0]) for p in R.SRC.glob('resbim-*_plan.png'))[first:first + count]
    with Pool(workers) as pool:
        rows = pool.map(_job, ids, chunksize=1)
    ok = [r for r in rows if 'error' not in r]
    tot = lambda key: [sum(r['score'][key][k] for r in ok) for k in ('tp', 'pred', 'ref')]
    summ = {'units': len(rows), 'ok': len(ok), 'errors': [r for r in rows if 'error' in r][:5],
            'align_iou_median': round(float(np.median([r['align_iou'] for r in ok])), 3),
            'walls_mean': {k: round(float(np.mean([r['score']['walls'][k] for r in ok])), 1) for k in ('iou', 'precision', 'recall')},
            'doors': Sc.prf(*tot('doors')), 'windows': Sc.prf(*tot('windows')),
            'door_count_exact': round(100 * float(np.mean([r['score']['counts']['doors'][0] == r['score']['counts']['doors'][1] for r in ok])), 1),
            'window_count_exact': round(100 * float(np.mean([r['score']['counts']['windows'][0] == r['score']['counts']['windows'][1] for r in ok])), 1),
            'png_explained': round(float(np.mean([r['coverage']['explained'] for r in ok])), 2),
            'png_phantom': round(float(np.mean([r['coverage']['phantom'] for r in ok])), 2),
            'building_explained': round(float(np.mean([r['coverage']['building_explained'] for r in ok])), 2),
            'building_phantom': round(float(np.mean([r['coverage']['building_phantom'] for r in ok])), 2),
            'scale_abs_err_median_pct': round(float(np.median([abs(r['score']['scale_error_pct']) for r in ok])), 2),
            'scale_within_1pct': sum(abs(r['score']['scale_error_pct']) <= 1 for r in ok)}
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME.parent / f'resbim-{first}-{count}.json').write_text(json.dumps({'summary': summ, 'units': rows}, indent=1, default=float))
    return summ


if __name__ == '__main__':
    a = [int(x) for x in sys.argv[1:3]] if len(sys.argv) > 2 else [0, 50]
    print(json.dumps(run(*a), indent=1))
