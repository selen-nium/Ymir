"""Replay arm D and record, per iteration, which check flagged what, how many flags touch a true error, and
which repairs were kept. The saved graphs hold only the final state, so this re-runs the loop with the current
rules (no C5). Grading against the reference is for reporting only; the loop never sees it.

python -m stoa_iterative.trace <dataset> <split> <n_max> <generator>[,<generator>...]
Writes data/iterative-structuring/trace/<dataset>-<split>-<generator>.json (aggregate) and
trace/pages/<dataset>-<split>-<generator>/<page>.json (per-iteration room outlines and flags, for figures).
"""
import copy
import json
import sys
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

from stoa_semseg import cpu_arms
from stoa_semseg.common import CLASSES, pages
from stoa_semseg.evaluate import eval_page, read_predictions, summarize

from . import comparator as C
from . import qa, repair, text
from .graph import from_prediction_row, to_prediction_row
from .phase1 import OTHER, false_rooms, thresholds
from .phase2 import CHECKS

HOME = Path(__file__).resolve().parents[2] / 'data/iterative-structuring/trace'
LOOP_CHECKS = tuple(c for c in CHECKS if c != 'C5')


def _flag_stats(page, graph, flags, nc):
    scored = eval_page(page, to_prediction_row(graph), 0.0, nc)
    errs = C.true_errors(page, scored)
    fr = false_rooms(page, graph)
    by = {}
    for chk in sorted({f['check'] for f in flags}):
        tp, n, _, _ = C.flag_quality([f for f in flags if f['check'] == chk], errs, fr)
        by[chk] = [n, tp]
    _, _, found, nerr = C.flag_quality(flags, errs, fr)
    return scored, by, found, nerr, len(errs), len(fr)


def record(P, page, g, orow, othr, toks, nc, k):
    """Score one state of the graph: arm-D comparator flags (graded), metrics, every question type."""
    flags = C.compare(P, g, orow, othr, checks=LOOP_CHECKS)
    scored, by, found, nerr, _, _ = _flag_stats(page, g, flags, nc)
    counts = {}
    for q in page.get('count_questions', []):
        a = qa.answer(q, g, toks)
        c = counts.setdefault(q['family'], [0, 0])
        c[0] += 1
        c[1] += int(a is not None and a == q['answer'])
    aq = [q for q in scored.get('questions', []) if q['label_check_ok']]
    rec = {'n': k, 'rooms': len(g['rooms']), 'flags_by_check': by, 'errors': nerr, 'errors_flagged': found,
           'scored': scored, 'counts': counts,
           'area_all': [len(aq), sum(1 for q in aq if q['ape'] is not None and q['ape'] <= 0.02)]}
    snap = {'n': k, 'rooms': [{'id': r['id'], 'polygon': r['polygon'], 'source': r.get('source')} for r in g['rooms']],
            'flags': [{'check': f['check'], 'why': f['why'], 'bbox': f['bbox']} for f in flags]}
    return rec, snap, flags


def text_only_counts(page, toks):
    text_only = {}
    for q in page.get('count_questions', []):
        a = qa.answer_text_only(q, toks)
        c = text_only.setdefault(q['family'], [0, 0])
        c[0] += 1
        c[1] += int(a is not None and a == q['answer'])
    return text_only


def _job(args):
    dataset, split, page, gen, thr, other, othr, n_max = args
    rows = read_predictions(cpu_arms.pred_path(gen, dataset, split))
    orow = read_predictions(cpu_arms.pred_path(other, dataset, split)).get(page['id'])
    toks = text.load(dataset, page['id']) if text.path(dataset, page['id']).exists() else []
    P = C.Page(page, toks, dataset, C.CONFIG[dataset]['closing_r'])
    g = from_prediction_row(page, rows.get(page['id']), dataset, thr, gen)
    nc = len(CLASSES[dataset])
    iters, snaps = [], []
    for k in range(n_max + 1):
        rec, snap, flags = record(P, page, g, orow, othr, toks, nc, k)
        snaps.append(snap)
        if k == n_max:
            rec['applied'] = []
            iters.append(rec)
            break
        g2, applied, _ = repair.iterate(P, g, orow, othr, LOOP_CHECKS)
        rec['applied'] = applied
        iters.append(rec)
        if applied:
            g = g2
        snaps[-1]['applied'] = applied
    iters[0]['text_only'] = text_only_counts(page, toks)
    return page['id'], iters, snaps


def run(dataset, split, n_max, gen, workers=int(__import__('os').environ.get('WORKERS', '12'))):
    th = thresholds(dataset)
    other = OTHER.get(gen, 'eomt-finetuned')
    P = pages(dataset, split)
    jobs = [(dataset, split, p, gen, th[gen], other, th[other], n_max) for p in P]
    with Pool(workers) as pool:
        out = pool.map(_job, jobs, chunksize=1)
    return aggregate(dataset, split, gen, other, P, out, n_max)


def aggregate(dataset, split, gen, other, P, out, n_max, extra=None):
    """out: [(page id, per-iteration records, snapshots)]. Writes trace/<dataset>-<split>-<gen>.json and page files."""
    nc = len(CLASSES[dataset])
    pdir = HOME / 'pages' / f'{dataset}-{split}-{gen}'
    pdir.mkdir(parents=True, exist_ok=True)
    curve = []
    for k in range(n_max + 1):
        s = summarize([it[k]['scored'] for _, it, _ in out], nc)
        flags, correct = Counter(), Counter()
        applied = Counter()
        for _, it, _ in out:
            for chk, (n, tp) in it[k]['flags_by_check'].items():
                flags[chk] += n
                correct[chk] += tp
            applied.update(it[k]['applied'])
        row = {'n': k, 'rooms': sum(it[k]['rooms'] for _, it, _ in out),
               'flags': dict(flags), 'flags_correct': dict(correct), 'applied': dict(applied),
               'errors': sum(it[k]['errors'] for _, it, _ in out),
               'errors_flagged': sum(it[k]['errors_flagged'] for _, it, _ in out),
               'pages_changed': sum(1 for _, it, _ in out if it[k]['applied'])}
        row.update({m: s[m] for m in ('room_f1', 'room_precision', 'room_recall', 'area_within_2', 'area_within_5')})
        if s.get('questions'):
            row['questions_within_2'] = {f: v['within_2'] for f, v in s['questions'].items()}
            na = sum(it[k]['area_all'][0] for _, it, _ in out)
            row['questions_within_2']['all'] = round(100 * sum(it[k]['area_all'][1] for _, it, _ in out) / na, 1) if na else None
        cn = Counter(); co = Counter()
        for _, it, _ in out:
            for f, (n, ok) in it[k]['counts'].items():
                cn[f] += n
                co[f] += ok
        row['counts_exact'] = {f: round(100 * co[f] / cn[f], 1) for f in cn if f in qa.ANSWERABLE}
        ans = [f for f in cn if f in qa.ANSWERABLE]
        row['counts_exact']['answerable'] = round(100 * sum(co[f] for f in ans) / sum(cn[f] for f in ans), 1) if ans else None
        curve.append(row)
    cn = Counter(); co = Counter()
    for _, it, _ in out:
        for f, (n, ok) in it[0]['text_only'].items():
            cn[f] += n
            co[f] += ok
    ans = [f for f in cn if f in qa.ANSWERABLE]
    text_only = {f: round(100 * co[f] / cn[f], 1) for f in ans}
    text_only['answerable'] = round(100 * sum(co[f] for f in ans) / sum(cn[f] for f in ans), 1) if ans else None
    n_questions = {f: cn[f] for f in cn}
    n_questions['unanswerable'] = sorted(f for f in cn if f not in qa.ANSWERABLE)
    if curve[0].get('questions_within_2'):
        P0 = {}
        for _, it, _ in out:
            for q in it[0]['scored'].get('questions', []):
                if q['label_check_ok']:
                    P0[q['family']] = P0.get(q['family'], 0) + 1
        n_questions.update({f'area_{f}': v for f, v in P0.items()})
    for pid, it, sn in out:
        (pdir / (pid.replace('/', '__') + '.json')).write_text(json.dumps(
            {'id': pid, 'iterations': [{k: v for k, v in r.items() if k not in ('scored', 'counts', 'text_only')} for r in it], 'snapshots': sn}, default=str))
    res = {'dataset': dataset, 'split': split, 'generator': gen, 'other_tool': other, 'checks': LOOP_CHECKS,
           'pages': len(P), 'curve': curve, 'text_only_control': text_only, 'n_questions': n_questions, **(extra or {})}
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / f'{dataset}-{split}-{gen}.json').write_text(json.dumps(res, indent=1))
    print(dataset, split, gen, json.dumps([{k: r[k] for k in ('n', 'room_f1', 'area_within_2', 'flags', 'applied')} for r in curve]), flush=True)
    return res


if __name__ == '__main__':
    ds, split, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    for gen in sys.argv[4].split(','):
        run(ds, split, n, gen)
