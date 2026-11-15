"""Phase 2: arm D (rule-based repair loop, no VLM). Per-iteration scores on validation for every generator.

python -m stoa_iterative.phase2 <dataset> [split] [n_max]
"""
import json
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

from stoa_semseg import cpu_arms
from stoa_semseg.common import CLASSES, pages
from stoa_semseg.evaluate import eval_page, read_predictions, summarize

from . import comparator as C
from . import repair, text
from .graph import from_prediction_row, to_prediction_row
from .phase1 import GENERATORS, OTHER, thresholds

HOME = Path(__file__).resolve().parents[2] / 'data/iterative-structuring'
CHECKS = ('C1', 'C2', 'C3', 'C4', 'C5', 'C7', 'C8', 'C11')


def _job(args):
    dataset, split, page, gen, thr, other, othr, n_max, checks, tag = args
    rows = read_predictions(cpu_arms.pred_path(gen, dataset, split))
    orow = read_predictions(cpu_arms.pred_path(other, dataset, split)).get(page['id'])
    toks = text.load(dataset, page['id']) if text.path(dataset, page['id']).exists() else []
    P = C.Page(page, toks, dataset, C.CONFIG[dataset]['closing_r'])
    g = from_prediction_row(page, rows.get(page['id']), dataset, thr, gen)
    nc = len(CLASSES[dataset])
    per_iter = [eval_page(page, to_prediction_row(g), 0.0, nc)]
    ops = Counter()
    t = time.time()
    for k in range(n_max):
        g2, applied, nflags = repair.iterate(P, g, orow, othr, checks)
        if not applied:  # nothing helped: stop (later iterations would repeat this one)
            per_iter += [per_iter[-1]] * (n_max - k)
            break
        g = g2
        ops.update(a.split('-')[0] + '-' + a.split('-')[1] for a in applied)
        per_iter.append(eval_page(page, to_prediction_row(g), 0.0, nc))
    out_dir = HOME / 'graphs' / f'armD{tag}-{gen}' / dataset
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / (page['id'].replace('/', '__') + '.json')).write_text(json.dumps(g))
    return per_iter, ops, time.time() - t


def run(dataset, split='validation', n_max=4, generators=GENERATORS, checks=CHECKS, workers=12, tag=''):
    th = thresholds(dataset)
    nc = len(CLASSES[dataset])
    res = {}
    for gen in generators:
        other = OTHER.get(gen, 'eomt-finetuned')
        jobs = [(dataset, split, p, gen, th[gen], other, th[other], n_max, checks, tag) for p in pages(dataset, split)]
        with Pool(workers) as pool:
            out = pool.map(_job, jobs, chunksize=1)
        curve = []
        for k in range(n_max + 1):
            s = summarize([o[0][k] for o in out], nc)
            row = {m: s[m] for m in ('room_f1', 'room_precision', 'room_recall', 'area_within_2', 'area_within_5', 'area_median_ape')}
            if s.get('questions'):
                row['questions_within_2'] = {f: v['within_2'] for f, v in s['questions'].items()}
            curve.append(row)
        ops = sum((o[1] for o in out), Counter())
        res[gen] = {'curve': curve, 'ops_applied': dict(ops), 'seconds_per_page': round(sum(o[2] for o in out) / len(out), 1)}
        print(dataset, split, gen, 'n=0', curve[0], '\n   n=max', curve[-1], dict(ops), flush=True)
    return res


if __name__ == '__main__':
    ds = sys.argv[1]
    split = sys.argv[2] if len(sys.argv) > 2 else 'validation'
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    no_c5 = '--no-c5' in sys.argv  # ablation: no printed-area text (it can equal Junction's answer)
    checks = tuple(c for c in CHECKS if not (no_c5 and c == 'C5'))
    tag = '-noC5' if no_c5 else ''
    gens = [a.split('=', 1)[1].split(',') for a in sys.argv if a.startswith('--generators=')]
    if gens:
        tag += '-' + '+'.join(gens[0])
    r = run(ds, split, n, checks=checks, tag=tag, generators=gens[0] if gens else GENERATORS)
    (HOME / f'phase2-{ds}-{split}{tag}.json').write_text(json.dumps(r, indent=1))
