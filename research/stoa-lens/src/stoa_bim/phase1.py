"""Phase 1: PNG → model (G_0 v0, no reference) → IFC / DXF / SVG / GLB → IFC plan section vs PNG; scores vs reference.

Writes data/png-to-bim/phase1/<split>/<page>/{model.json, model.ifc, model.dxf, model.svg, model.glb} and
data/png-to-bim/phase1-<split>.json.
python -m stoa_bim.phase1 [split] [limit]
"""
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from stoa_semseg.common import CLASSES, image_path, pages
from stoa_semseg.evaluate import eval_page

from . import compare as C
from . import generate as G
from . import model as M
from . import score as Sc
from . import section as S
from . import writers as Wr

HOME = Path(__file__).resolve().parents[2] / 'data/png-to-bim'


def rooms_eval(page, m):
    """Score the IfcSpace footprints with the semseg room scorer (Room F1, area within 2%, Junction area questions)."""
    inst = [{'class': 0, 'score': 1.0, 'polygons': [M.to_px(m, s['polygon'])]} for s in m['spaces']]
    return eval_page(page, {'id': page['id'], 'instances': inst}, 0.0, len(CLASSES['swiss']))


def files(m, out):
    out.mkdir(parents=True, exist_ok=True)
    (out / 'model.json').write_text(json.dumps(m))
    Wr.to_ifc(m, out / 'model.ifc')
    Wr.to_dxf(m, out / 'model.dxf')
    Wr.to_svg(m, out / 'model.svg')
    Wr.ifc_to_glb(out / 'model.ifc', out / 'model.glb')


def evaluate(page, m, out, toks):
    """Read the written IFC back, cut its plan, compare with the PNG; score elements against the reference."""
    ink, _ = S.rasterize(S.section(out / 'model.ifc'), m['frame'])
    png = C.png_ink(page, image_path(page), toks)
    cov, flags, _, _ = C.compare(png, ink)
    gray, sat = G._gray(page)
    walls_png, ink_png = G.wall_mask(page, gray, sat, toks)
    ann = G.annotation_mask(page, m, ink_png, walls_png)
    cov_b, _, _, _ = C.compare(png & ~ann, ink)
    cov['building_explained'], cov['building_phantom'] = cov_b['explained'], cov_b['phantom']
    ref = M.swiss_reference(page)
    sc = Sc.score(m, ref)
    r = rooms_eval(page, m)
    cq = {'ref_doors_exact': [1, int(len(m['doors']) == len(ref['doors']))],
          'ref_windows_exact': [1, int(len(m['windows']) == len(ref['windows']))],
          'ref_doors_within1': [1, int(abs(len(m['doors']) - len(ref['doors'])) <= 1)],
          'ref_windows_within1': [1, int(abs(len(m['windows']) - len(ref['windows'])) <= 1)]}
    for q in page.get('count_questions', []):
        fam = q['family']
        kinds = [x.get('kind') for x in m['fixtures']]
        a = {'count_doors': len(m['doors']), 'count_windows': len(m['windows']), 'count_toilets': kinds.count('toilet'),
             'count_washbasins': kinds.count('washbasin'), 'count_bathtubs': kinds.count('bathtub'), 'count_showers': kinds.count('shower')}.get(fam)
        if a is not None:
            cq.setdefault(fam, [0, 0])
            cq[fam][0] += 1
            cq[fam][1] += int(a == q['answer'])
    return {'coverage': cov, 'flags': len(flags), 'score': sc, 'count_questions': cq,
            'rooms': {'n_ref': len(page['rooms']), 'matched': sum(1 for x in r['rooms'] if x['matched_iou'] is not None),
                      'within_2': sum(1 for x in r['rooms'] if x['ape'] is not None and x['ape'] <= 0.02), 'pred': len(m['spaces'])},
            'questions': [q for q in r['questions'] if q['label_check_ok']]}


def _job(args):
    from stoa_iterative import text
    page, split = args
    t = time.time()
    toks = text.load('swiss', page['id']) if text.path('swiss', page['id']).exists() else []
    from . import scale as Scl
    read = Scl.swiss_scale(page, image_path(page))
    if read:  # scale read from the drawing's title block (identical to the true scale on all 92 val+test sheets)
        page = {**page, 'm_per_px': read['m_per_px']}
    m = G.generate(page, toks, split=split)
    m['frame']['scale_source'] = f"read from drawing: {read['text']}" if read else 'given (not read)'
    out = HOME / 'phase1' / split / page['id'].replace('/', '__')
    files(m, out)
    ev = evaluate(page, m, out, toks)
    return {'id': page['id'], 'elements': M.summary(m), **ev, 'seconds': round(time.time() - t, 1)}


def aggregate(rows):
    tot = lambda key: [sum(r['score'][key][k] for r in rows) for k in ('tp', 'pred', 'ref')]
    wall = {k: round(float(np.mean([r['score']['walls'][k] for r in rows])), 1) for k in ('iou', 'precision', 'recall')}
    nref = sum(r['rooms']['n_ref'] for r in rows)
    npred = sum(r['rooms']['pred'] for r in rows)
    matched = sum(r['rooms']['matched'] for r in rows)
    qs = [q for r in rows for q in r['questions']]
    fam = {f: round(100 * float(np.mean([q['ape'] is not None and q['ape'] <= 0.02 for q in qs if q['family'] == f])), 1)
           for f in ('room', 'apartment', 'label', 'floor') if any(q['family'] == f for q in qs)}
    cq = {}
    for r in rows:
        for f, (n, ok) in r['count_questions'].items():
            cq.setdefault(f, [0, 0])
            cq[f][0] += n
            cq[f][1] += ok
    p, rc = 100 * matched / max(1, npred), 100 * matched / max(1, nref)
    return {'pages': len(rows),
            'walls_mean': wall, 'doors': Sc.prf(*tot('doors')), 'windows': Sc.prf(*tot('windows')),
            'spaces': {'room_f1': round(2 * p * rc / max(1e-9, p + rc), 1), 'within_2': round(100 * sum(r['rooms']['within_2'] for r in rows) / max(1, nref), 1)},
            'questions_area_within_2': {**fam, 'all': round(100 * float(np.mean([q['ape'] is not None and q['ape'] <= 0.02 for q in qs])), 1) if qs else None},
            'questions_count_exact': {f: round(100 * ok / n, 1) for f, (n, ok) in cq.items()},
            'png_explained': round(float(np.mean([r['coverage']['explained'] for r in rows])), 2),
            'building_explained': round(float(np.mean([r['coverage']['building_explained'] for r in rows])), 2),
            'building_phantom': round(float(np.mean([r['coverage']['building_phantom'] for r in rows])), 2),
            'stairs': sum(r['elements'].get('stairs', 0) for r in rows),
            'png_phantom': round(float(np.mean([r['coverage']['phantom'] for r in rows])), 2)}


def run(split='validation', limit=None, workers=12):
    P = pages('swiss', split)[:limit]
    with Pool(workers) as pool:
        rows = pool.map(_job, [(p, split) for p in P], chunksize=1)
    summ = aggregate(rows)
    (HOME / f'phase1-{split}.json').write_text(json.dumps({'summary': summ, 'pages': rows}, indent=1, default=float))
    return summ


if __name__ == '__main__':
    split = sys.argv[1] if len(sys.argv) > 1 else 'validation'
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
    print(json.dumps(run(split, lim), indent=1))
