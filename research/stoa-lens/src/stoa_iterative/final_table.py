"""Swiss test comparison table with every metric: rooms within 2% / 5%, Junction area questions per family and
pooled, and Junction count questions per family and pooled (exact match, as Junction scores counts)."""
import json
import sys
from pathlib import Path

import numpy as np

from stoa_semseg import cpu_arms
from stoa_semseg.common import CLASSES, pages
from stoa_semseg.evaluate import eval_page, read_predictions

from . import qa, text
from .graph import from_prediction_row, to_prediction_row
from .phase1 import thresholds

HOME = Path(__file__).resolve().parents[2] / 'data/iterative-structuring'
FAMS = ['room', 'apartment', 'label', 'floor']
COUNTS = [f for f, _ in qa.COUNT]


def graphs_from_preds(arm, P, thr):
    rows = read_predictions(cpu_arms.pred_path(arm, 'swiss', 'test'))
    return {p['id']: from_prediction_row(p, rows.get(p['id']), 'swiss', thr, arm) for p in P}


def graphs_from_dir(d, P):
    return {p['id']: json.loads((HOME / 'graphs' / d / 'swiss' / (p['id'].replace('/', '__') + '.json')).read_text()) for p in P}


def score(P, graphs, text_only=False):
    rooms, qs = [], []
    pg = []
    for p in P:
        toks = text.load('swiss', p['id'])
        pg.append((p, graphs.get(p['id']) if graphs else None, toks))
        if graphs is None:
            continue
        r = eval_page(p, to_prediction_row(graphs[p['id']]), 0.0, len(CLASSES['swiss']))
        rooms += [x['ape'] if x['ape'] is not None else np.inf for x in r['rooms']]
        qs += [q for q in r['questions'] if q['label_check_ok']]
    out = {}
    if graphs is not None:
        a = np.array(rooms)
        out['within_2'] = round(100 * float(np.mean(a <= 0.02)), 1)
        out['within_5'] = round(100 * float(np.mean(a <= 0.05)), 1)
        for f in FAMS:
            L = [q for q in qs if q['family'] == f]
            out[f'Q_{f}'] = round(100 * float(np.mean([q['ape'] is not None and q['ape'] <= 0.02 for q in L])), 1)
        out['Q_area_all'] = round(100 * float(np.mean([q['ape'] is not None and q['ape'] <= 0.02 for q in qs])), 1)
    c = qa.score_counts(pg, text_only=text_only)
    for f in COUNTS:
        out[f'Q_{f}'] = c[f]['exact'] if c[f]['answerable'] else None
    ans = [c[f] for f in COUNTS if c[f]['answerable']]
    out['Q_count_answerable'] = round(sum(x['exact'] * x['n'] for x in ans) / sum(x['n'] for x in ans), 1)
    out['n_questions'] = {**{f'Q_{f}': len([q for q in qs if q['family'] == f]) for f in FAMS},
                          **{f'Q_{f}': c[f]['n'] for f in COUNTS}}
    return out


def main():
    P = pages('swiss', 'test')
    th = thresholds('swiss')
    rows = {
        'OCR text only (no rooms; counts only)': score(P, None, text_only=True),
        'EoMT-L + heal (n = 0)': score(P, graphs_from_preds('eomt-finetuned', P, th['eomt-finetuned'])),
        'EoMT-L + heal + arm D, first rules (n = 2)': score(P, graphs_from_dir('armD-noC5-eomt-finetuned', P)),
        'EoMT-L + zoom + heal (n = 0)': score(P, graphs_from_preds('eomt-finetuned-zoom-z2-iou0.8', P, th['eomt-finetuned'])),
        'EoMT-L + zoom + heal + arm D, final (n = 2)': score(P, graphs_from_dir('armD-noC5-eomt-finetuned-zoom-z2-iou0.8-eomt-finetuned-zoom-z2-iou0.8', P)),
    }
    (HOME / 'final-table-swiss-test.json').write_text(json.dumps(rows, indent=1))
    return rows


if __name__ == '__main__':
    print(json.dumps(main(), indent=1))
