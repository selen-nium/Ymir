"""Phase 1: comparator precision / recall against true errors, per check and combined (validation only)."""
import json
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

from shapely.geometry import Polygon

from stoa_semseg import cpu_arms
from stoa_semseg.common import CLASSES, pages
from stoa_semseg.evaluate import eval_page, read_predictions

from . import comparator as C
from . import text
from .graph import from_prediction_row, to_prediction_row

HOME = Path(__file__).resolve().parents[2] / 'data/iterative-structuring'
GENERATORS = ['eomt-finetuned', 'rfdetr-seg-finetuned', 'raster2seq-base', 'classical']
OTHER = {'eomt-finetuned': 'rfdetr-seg-finetuned'}
CHECKS = ('C1', 'C2', 'C3', 'C4', 'C5', 'C7', 'C8', 'PSD')


def thresholds(dataset):
    rep = json.loads((Path(cpu_arms.HOME) / 'report.json').read_text())[dataset]
    th = {g: rep[g]['threshold'] for g in rep if 'threshold' in rep[g]}
    for z in ('eomt-finetuned-zoom', 'eomt-finetuned-zoom-z2-iou0.8'):
        th[z] = th.get('eomt-finetuned')  # zoom keeps the base arm's instances and scores
    return th


def false_rooms(page, graph):
    refs = [Polygon(r['polygon']).buffer(0) for r in page['rooms']]
    out = []
    for r in graph['rooms']:
        g = Polygon(r['polygon']).buffer(0)
        best = max((g.intersection(x).area / g.union(x).area for x in refs if g.intersects(x)), default=0)
        ign = sum(Polygon(p).buffer(0).intersection(g).area for p in page.get('ignore', []))
        if best < 0.5 and ign < 0.5 * g.area:
            out.append(g)
    return out


def _job(args):
    dataset, page, gen, thr, other, othr = args
    rows = read_predictions(cpu_arms.pred_path(gen, dataset, 'validation'))
    orow = read_predictions(cpu_arms.pred_path(other, dataset, 'validation')).get(page['id']) if other else None
    graph = from_prediction_row(page, rows.get(page['id']), dataset, thr, gen)
    toks = text.load(dataset, page['id']) if text.path(dataset, page['id']).exists() else []
    P = C.Page(page, toks, dataset, C.CONFIG[dataset]['closing_r'])
    flags = C.compare(P, graph, orow, othr, checks=CHECKS)
    scored = eval_page(page, to_prediction_row(graph), 0.0, len(CLASSES[dataset]))
    errs = C.true_errors(page, scored)
    fr = false_rooms(page, graph)
    out = {}
    for chk in CHECKS + ('ALL',):
        fl = [f for f in flags if (f['check'] == chk if chk != 'ALL' else f['check'] != 'PSD')]
        out[chk] = C.flag_quality(fl, errs, fr)
    return out


def run(dataset, workers=12):
    th = thresholds(dataset)
    P = pages(dataset, 'validation')
    res = {}
    for gen in GENERATORS:
        other = OTHER.get(gen, 'eomt-finetuned')
        jobs = [(dataset, p, gen, th[gen], other, th[other]) for p in P]
        with Pool(workers) as pool:
            per = pool.map(_job, jobs, chunksize=1)
        agg = defaultdict(lambda: [0, 0, 0, 0])
        for d in per:
            for k, v in d.items():
                agg[k] = [a + b for a, b in zip(agg[k], v)]
        res[gen] = {k: {'flags': v[1], 'flag_precision': round(100 * v[0] / v[1], 1) if v[1] else None,
                        'errors': v[3], 'error_recall': round(100 * v[2] / v[3], 1) if v[3] else None} for k, v in agg.items()}
        print(dataset, gen, json.dumps(res[gen]['ALL']), flush=True)
    return res


if __name__ == '__main__':
    import sys
    ds = sys.argv[1]
    r = run(ds)
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / f'phase1-{ds}.json').write_text(json.dumps(r, indent=1))
