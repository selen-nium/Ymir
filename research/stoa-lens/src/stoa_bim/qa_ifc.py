"""Does an IFC built from the PNG help answer Junction's questions? Four arms on the same sampled questions.

  png     DeepSeek, sheet PNG + question (the control prompt, strict: "Do not guess")
  digest  DeepSeek, PNG + a text digest read from the IFC (element counts, every IfcSpace with number, name, area, box)
  tools   DeepSeek, PNG + tools over the IFC (ifc_tools.TOOLS), up to 6 rounds, answer through the `answer` tool
  rule    no language model: the answer read from the IFC by fixed rules (ifc_tools.IfcIndex.rule_answer)

Nothing from the reference is used to answer: room numbers and labels are parsed from the question text and matched
against the IfcSpace names the generator wrote (from OCR); scale is the one read from the drawing.
Sampling: at most 100 questions per family, seed 0, same sample for every arm.
Scoring: Junction's rules (areas within 2%, counts exact).

python -m stoa_bim.qa_ifc swiss validation png,digest,tools,rule [--dry-run]
"""
import json
import os
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from stoa_iterative import verda
from stoa_iterative.vlm_control import SYSTEM, UNIT, image_b64, parse, questions

from . import ifc_tools as T

ROOT = Path(__file__).resolve().parents[2]
HOME = ROOT / 'data/png-to-bim/qa'
MODELS = {'swiss': ROOT / 'data/png-to-bim/repairD/{split}', 'cubicasa': ROOT / 'data/png-to-bim/cubicasa/{split}'}
MAX_PER_FAMILY = 100
IFC_NOTE = ('You also have an IFC building model that was generated automatically from this drawing. It can contain '
            'errors (missing or extra elements, wrong room outlines); use it together with the drawing.')
TOOLS_NOTE = ('Use the tools to query the IFC model and to view parts of the drawing with the model drawn on it. '
              'When done, call the answer tool exactly once.')


def sample(dataset, split):
    qs = questions(dataset, split)
    rng = random.Random(0)
    out = []
    for f in sorted({q['family'] for q in qs}):
        Q = [q for q in qs if q['family'] == f]
        out += sorted(rng.sample(Q, MAX_PER_FAMILY), key=lambda q: q['id']) if len(Q) > MAX_PER_FAMILY else Q
    return out


_ix = {}


def index(dataset, split, q, fmt='ifc'):
    from .format_readers import open_index
    key = (fmt, q['page'])
    if key not in _ix:
        d = Path(str(MODELS[dataset]).format(split=split)) / q['page'].replace('/', '__')
        _ix[key] = open_index(fmt, d, q['image']) if (d / f'model.{fmt}').exists() else None
    return _ix[key]


def score(q, v):
    if v is None:
        return False
    return abs(v - q['answer']) <= 0.02 * q['answer'] if q['kind'] == 'area' else v == q['answer']


def user_content(q, extra=''):
    return [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + image_b64(q['image'])}},
            {'type': 'text', 'text': (extra + '\n\n' if extra else '') + q['question'] + '\n' + UNIT[q['kind']]}]


def arm_png(q, ix, tag):
    body = {'temperature': 0, 'max_tokens': 400,
            'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user_content(q)}]}
    resp = verda.chat(body, tag)
    return parse(verda.text_of(resp)), resp.get('error'), 1


def arm_digest(q, ix, tag, fmt='ifc'):
    if ix is None:
        return None, 'no file', 0
    note = IFC_NOTE if fmt == 'ifc' else IFC_NOTE.replace('an IFC building model', f'a {fmt.upper()} file').replace('use it', 'use it')
    body = {'temperature': 0, 'max_tokens': 400,
            'messages': [{'role': 'system', 'content': SYSTEM + ' ' + note},
                         {'role': 'user', 'content': user_content(q, f'{fmt.upper()} file digest:\n' + ix.digest())}]}
    resp = verda.chat(body, tag, reserve_in=20000)
    return parse(verda.text_of(resp)), resp.get('error'), 1


def arm_tools(q, ix, tag, rounds=6):
    if ix is None:
        return None, 'no ifc', 0
    msgs = [{'role': 'system', 'content': SYSTEM + ' ' + IFC_NOTE + ' ' + TOOLS_NOTE},
            {'role': 'user', 'content': user_content(q)}]
    calls = 0
    for r in range(rounds + 1):
        body = {'temperature': 0, 'max_tokens': 800, 'messages': msgs}
        if r < rounds:
            body['tools'] = T.TOOLS
        else:
            msgs.append({'role': 'user', 'content': 'No more tool calls. Reply now with only the JSON object {status, value, unit}.'})
        resp = verda.chat(body, tag, reserve_in=30000)
        calls += 1
        if resp.get('error'):
            return None, resp['error'], calls
        msg = resp['choices'][0]['message']
        tcs = msg.get('tool_calls') or []
        if not tcs:
            return parse(msg.get('content')), None, calls
        msgs.append({'role': 'assistant', 'content': msg.get('content') or '', 'tool_calls': tcs})
        images = []
        for tc in tcs:
            name = tc['function']['name']
            try:
                args = json.loads(tc['function'].get('arguments') or '{}')
            except json.JSONDecodeError:
                args = {}
            if name == 'answer':
                v = args.get('value')
                try:
                    v = float(v) if v is not None and args.get('status', 'answered') == 'answered' else None
                except (TypeError, ValueError):
                    v = None
                return v, None, calls
            try:
                out, img = T.call_tool(ix, name, args)
            except Exception as e:  # a bad argument is reported back to the model, not raised
                out, img = {'error': str(e)[:200]}, None
            msgs.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': json.dumps(out)[:12000]})
            if img:
                images.append((f'view_region {args.get("region")}:', img))
        if images:
            msgs.append({'role': 'user', 'content': sum(([{'type': 'text', 'text': t}, {'type': 'image_url', 'image_url': {'url': u}}] for t, u in images), [])})
    return None, 'no answer', calls


def arm_rule(q, ix, tag):
    if ix is None:
        return None, 'no file', 0
    return ix.rule_answer(q['family'], q['question']), None, 0


ARMS = {'png': arm_png, 'digest': arm_digest, 'tools': arm_tools, 'rule': arm_rule}
for _f in ('dxf', 'svg', 'pdf'):
    ARMS[f'rule-{_f}'] = arm_rule
    ARMS[f'digest-{_f}'] = (lambda f: lambda q, ix, tag: arm_digest(q, ix, tag, f))(_f)


def fmt_of(arm):
    return arm.split('-', 1)[1] if '-' in arm else 'ifc'


def run(dataset, split, arm, workers=8):
    qs = sample(dataset, split)
    fmt = fmt_of(arm)
    for q in qs:  # load the indexes once, serially (IfcOpenShell is not thread-safe while parsing)
        index(dataset, split, q, fmt)
    fn = ARMS[arm]
    tag = f'qa-ifc:{arm}:{dataset}:{split}'

    def one(q):
        try:
            v, err, calls = fn(q, index(dataset, split, q, fmt), tag)
        except verda.CapReached as e:
            v, err, calls = None, f'cap: {e}', 0
        return {**{k: q[k] for k in ('id', 'page', 'family', 'kind', 'answer')}, 'pred': v, 'ok': score(q, v),
                'error': err, 'calls': calls}

    with ThreadPoolExecutor(1 if arm == 'rule' else workers) as ex:
        rows = list(ex.map(one, qs))
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / f'{dataset}-{split}-{arm}.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    out = summarize(rows)
    out['spent_usd_ledger'] = round(verda.spent(), 4)
    (HOME / f'{dataset}-{split}-{arm}-summary.json').write_text(json.dumps(out, indent=1))
    return out


def summarize(rows):
    out = {}
    for f in sorted({r['family'] for r in rows}):
        R = [r for r in rows if r['family'] == f]
        out[f] = {'n': len(R), 'correct': round(100 * np.mean([r['ok'] for r in R]), 1),
                  'answered': round(100 * np.mean([r['pred'] is not None for r in R]), 1)}
    for kind in ('area', 'count'):
        R = [r for r in rows if r['kind'] == kind]
        if R:
            out[f'all_{kind}'] = {'n': len(R), 'correct': round(100 * np.mean([r['ok'] for r in R]), 1)}
    out['errors'] = sum(1 for r in rows if r['error'])
    out['calls'] = sum(r['calls'] for r in rows)
    return out


if __name__ == '__main__':
    ds, split, arms = sys.argv[1], sys.argv[2], sys.argv[3].split(',')
    if '--dry-run' in sys.argv:
        qs = sample(ds, split)
        print(len(qs), {f: sum(q['family'] == f for q in qs) for f in sorted({q['family'] for q in qs})}, 'spent', round(verda.spent(), 4))
        sys.exit()
    for a in arms:
        print(a, json.dumps(run(ds, split, a)), flush=True)
