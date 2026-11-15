"""Phase 0 gate: graphs built from references and from a generator, healed, scored, exported and re-read."""
import json
import tempfile
from pathlib import Path

from stoa_semseg import cpu_arms
from stoa_semseg.common import pages
from stoa_semseg.evaluate import eval_page, read_predictions, summarize
from stoa_semseg.common import CLASSES

from . import exports
from .graph import from_prediction_row, to_prediction_row, validity

HOME = Path(__file__).resolve().parents[2] / 'data/iterative-structuring'


def reference_row(page):
    return {'id': page['id'], 'instances': [{'class': r['class'], 'score': 1.0, 'polygons': [r['polygon']]} for r in page['rooms']]}


def run(dataset, split='validation', generator='eomt-finetuned', thr=None):
    out = {}
    P = pages(dataset, split)
    rep = json.loads((Path(cpu_arms.HOME) / 'report.json').read_text())
    thr = rep[dataset][generator]['threshold'] if thr is None else thr
    preds = read_predictions(cpu_arms.pred_path(generator, dataset, split))
    for name, rows, t in (('reference', {p['id']: reference_row(p) for p in P}, 0.0), (generator, preds, thr)):
        res_raw, res_graph, invalid, rt = [], [], 0, {'dxf': [0, 0], 'ifc': [0, 0], 'geojson': [0, 0]}
        for i, p in enumerate(P):
            row = rows.get(p['id'])
            g = from_prediction_row(p, row, dataset, t, name)
            invalid += sum(1 for r in g['rooms'] if validity(r))
            res_raw.append(eval_page(p, row, t, len(CLASSES[dataset])))
            res_graph.append(eval_page(p, to_prediction_row(g), 0.0, len(CLASSES[dataset])))
            if i < 10:  # export round trip on the first 10 pages
                with tempfile.TemporaryDirectory() as d:
                    for fmt, (w, c) in {'dxf': (exports.to_dxf, exports.check_dxf), 'ifc': (exports.to_ifc, exports.check_ifc),
                                        'geojson': (exports.to_geojson, exports.check_geojson)}.items():
                        path = str(Path(d) / f'g.{fmt}')
                        w(g, path)
                        chk = c(g, path)
                        rt[fmt][0] += 1
                        rt[fmt][1] += int(chk['areas_match'] and chk['closed'] == chk['rooms'] == chk['read_back'])
        a, b = summarize(res_raw, len(CLASSES[dataset])), summarize(res_graph, len(CLASSES[dataset]))
        keys = ['room_f1', 'room_recall', 'room_precision', 'area_within_2', 'area_within_5', 'area_median_ape']
        out[name] = {'raw': {k: a[k] for k in keys}, 'graph': {k: b[k] for k in keys}, 'invalid_after_heal': invalid,
                     'export_round_trip_pages_ok': {k: f'{v[1]}/{v[0]}' for k, v in rt.items()}}
    return out


if __name__ == '__main__':
    import sys
    res = {ds: run(ds) for ds in ('swiss', 'cubicasa')}
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / 'phase0.json').write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
