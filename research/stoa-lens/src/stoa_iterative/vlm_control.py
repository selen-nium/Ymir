"""Control: DeepSeek VLM answers each Junction question directly from the sheet PNG (no tools, no structure).

One call per question: the Junction PNG (as shipped, 150 DPI) + the English question; the model returns JSON
{"status", "value", "unit"} as in the Junction specialist export. Scored with Junction's rules (areas within 2%,
counts exact) on the same questions the pipeline is scored on: Swiss area questions (English, label check passed)
and the English count questions of Swiss and CubiCasa, every family including those the pipeline cannot answer.

Endpoint: OpenAI-compatible chat completions. Set VLM_BASE_URL and VLM_MODEL; the key is read from
DEEPSEEK_4_1_FLASH_API_KEY in .env and never logged. Every response is cached (re-runs cost nothing), and the
run stops before a call would take the ledger past VLM_MAX_USD.

python -m stoa_iterative.vlm_control swiss test [--dry-run] [--limit N]
"""
import base64
import hashlib
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from stoa_semseg.common import ROOT, image_path, pages

HOME = Path(__file__).resolve().parents[2] / 'data/iterative-structuring/vlm-control'
SYSTEM = ('Answer the question using only the supplied drawing. Return only a JSON object with status, value, and unit. '
          'Use status="answered" when supported; otherwise use status="needs_review" and value=null. Do not guess.')
SYSTEM_ESTIMATE = ('Answer the question using only the supplied drawing. Return only a JSON object with status, value, and unit. '
                   'Always give your best numeric estimate with status="answered"; never return null. '
                   'Use the drawing\'s labels, printed numbers and scale to measure or count.')
VARIANT = os.environ.get('VLM_VARIANT', 'strict')  # strict: Junction specialist prompt; estimate: must always answer
UNIT = {'area': 'Return JSON with status, value, and unit; use unit="m2".',
        'count': 'Return JSON with status, value, and unit; value is an integer and unit="count".'}


def api_key():
    for line in (ROOT / '.env').read_text().splitlines():
        if line.startswith('DEEPSEEK_4_1_FLASH_API_KEY='):
            return line.split('=', 1)[1].strip().strip('"\'')
    raise KeyError('DEEPSEEK_4_1_FLASH_API_KEY not in .env')


def questions(dataset='swiss', split='test'):
    out = []
    for p in pages(dataset, split):
        for k, q in enumerate(p.get('questions') or []):
            if not q['derived_within_2pct']:
                continue
            printed = None
            if q['family'] == 'room':
                rm = next((r for r in p['rooms'] if r['number'] == q['target']), None)
                printed = rm is not None and rm.get('printed_area_m2') is not None
            out.append({'id': f"{p['id']}#{k}", 'page': p['id'], 'image': str(image_path(p)), 'family': q['family'],
                        'kind': 'area', 'question': q['question'], 'answer': q['answer'], 'room_area_printed': printed})
        for k, q in enumerate(p.get('count_questions') or []):
            out.append({'id': f"{p['id']}#c{k}", 'page': p['id'], 'image': str(image_path(p)), 'family': q['family'],
                        'kind': 'count', 'question': q['question'], 'answer': q['answer'], 'room_area_printed': None})
    return out


def image_b64(path):
    with Image.open(path) as im:
        buf = io.BytesIO()
        im.convert('RGB').save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()


def request_body(q, model):
    return {'model': model, 'temperature': 0, 'max_tokens': 400,
            'messages': [{'role': 'system', 'content': SYSTEM if VARIANT == 'strict' else SYSTEM_ESTIMATE},
                         {'role': 'user', 'content': [
                             {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + image_b64(q['image'])}},
                             {'type': 'text', 'text': q['question'] + '\n' + UNIT[q['kind']]}]}]}


def parse(text):
    m = re.search(r'\{.*\}', text or '', re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    v = d.get('value')
    try:
        return float(str(v).replace(',', '.').replace('m²', '').replace('m2', '').strip()) if v is not None else None
    except ValueError:
        return None


def call(body, base, key, timeout=180):
    import requests
    r = requests.post(base.rstrip('/') + '/chat/completions', json=body, timeout=timeout,
                      headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
    r.raise_for_status()
    return r.json()


def run(dataset, split, dry_run=False, limit=None, workers=8):
    """Calls go through verda.chat (Verda Inference, DeepSeek V4.1 Flash), which owns the ledger, cap and cache."""
    from concurrent.futures import ThreadPoolExecutor
    from . import verda
    qs = questions(dataset, split)[:limit]
    HOME.mkdir(parents=True, exist_ok=True)
    if dry_run:
        fams = sorted({q['family'] for q in qs})
        return {'questions': len(qs), 'by_family': {f: sum(q['family'] == f for q in qs) for f in fams},
                'room_questions_with_printed_area': sum(bool(q['room_area_printed']) for q in qs),
                'endpoint': verda.BASE, 'model': verda.MODEL, 'spent_usd_so_far': round(verda.spent(), 4)}

    def one(q):
        body = request_body(q, verda.MODEL)
        body.pop('model')
        try:
            resp = verda.chat(body, f'control:{VARIANT}:{dataset}:{split}')
        except verda.CapReached as e:
            resp = {'error': f'cap: {e}'}
        v = parse(verda.text_of(resp))
        ok = v is not None and (abs(v - q['answer']) <= 0.02 * q['answer'] if q['kind'] == 'area' else v == q['answer'])
        return {**{k: q[k] for k in ('id', 'page', 'family', 'kind', 'answer', 'room_area_printed')}, 'pred': v,
                'error': resp.get('error'), 'ok': ok, 'usage': resp.get('usage')}

    with ThreadPoolExecutor(workers) as ex:
        rows = list(ex.map(one, qs))
    (HOME / f'{dataset}-{split}-{VARIANT}.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    out = summarize(rows, round(verda.spent(), 4))
    (HOME / f'{dataset}-{split}-{VARIANT}-summary.json').write_text(json.dumps(out, indent=1))
    return out


def summarize(rows, spent=None):
    out = {}
    for f in sorted({r['family'] for r in rows}):
        R = [r for r in rows if r['family'] == f]
        if R:
            out[f] = {'n': len(R), 'correct': round(100 * np.mean([r['ok'] for r in R]), 1),
                      'answered': round(100 * np.mean([r['pred'] is not None for r in R]), 1)}
    R = [r for r in rows if r['family'] == 'room']
    for flag, name in ((True, 'room_area_printed'), (False, 'room_area_not_printed')):
        S = [r for r in R if r['room_area_printed'] is flag]
        if S:
            out[name] = {'n': len(S), 'within_2': round(100 * np.mean([r['ok'] for r in S]), 1)}
    out['errors'] = sum(1 for r in rows if r['error'])
    out['n'] = len(rows)
    out['answered'] = round(100 * np.mean([r['pred'] is not None for r in rows]), 1)
    for kind in ('area', 'count'):
        R = [r for r in rows if r.get('kind') == kind]
        if R:
            out[f'all_{kind}'] = {'n': len(R), 'correct': round(100 * np.mean([r['ok'] for r in R]), 1)}
    out['spent_usd'] = spent
    return out


if __name__ == '__main__':
    ds, split = sys.argv[1], sys.argv[2]
    lim = next((int(a.split('=')[1]) for a in sys.argv if a.startswith('--limit=')), None)
    print(json.dumps(run(ds, split, dry_run='--dry-run' in sys.argv, limit=lim), indent=1))  # key never printed
