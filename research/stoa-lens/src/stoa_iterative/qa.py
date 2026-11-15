"""Junction questions by task type, answered from a room graph + its L2 text layer (and scored exactly as Junction:
counts must be exact; areas within 2%).

Swiss count families: rooms labelled X, apartments, doors, windows, toilets, washbasins, bathtubs, showers.
The room graph (rooms only, L1 phase 1) can answer the first two; the others need openings and fixtures in L1 and
are reported as not answerable. `attach_count_questions` adds Junction's English count questions to the prepared
Swiss pages under 'count_questions' (the area 'questions' used by the semseg scorer are untouched).
"""
import json
import re
import unicodedata
from pathlib import Path

from shapely.geometry import Point, Polygon

from stoa_semseg.common import HOME as SEMSEG, JUNCTION, dump, load

COUNT = [
    ('count_rooms_label', re.compile(r"^How many rooms labelled '(.+)' are there on this floor\?$")),
    ('count_apartments', re.compile(r'^How many apartments \(residential units\) are shown on this floor plan\?$')),
    ('count_doors', re.compile(r'^How many doors \(interior doors and entrance doors together\) are drawn')),
    ('count_windows', re.compile(r'^How many windows are drawn on this floor plan\?$')),
    ('count_toilets', re.compile(r'^How many toilets \(WC\) are drawn on this floor plan\?$')),
    ('count_washbasins', re.compile(r'^How many washbasins are drawn on this floor plan\?$')),
    ('count_bathtubs', re.compile(r'^How many bathtubs are drawn on this floor plan\?$')),
    ('count_showers', re.compile(r'^How many showers are drawn on this floor plan\?$')),
]
ANSWERABLE = {'count_rooms_label', 'count_apartments'}

# CubiCasa (Finnish plans): English count questions. Room-type families are answered from OCR room labels
# (vocabulary read off validation sheets); the rest need openings, fixtures or floors and are not answerable.
COUNT_CUBICASA = [
    ('count_windows', re.compile(r'^How many windows are shown')),
    ('count_floors', re.compile(r'^How many floors \(storeys\)')),
    ('count_toilets', re.compile(r'^How many toilet bowls')),
    ('count_sinks', re.compile(r'^How many sinks and wash basins')),
    ('count_showers_bathtubs', re.compile(r'^How many showers and bathtubs')),
    ('count_doors', re.compile(r'^How many doors are shown')),
    ('count_saunas', re.compile(r'^How many saunas')),
    ('count_kitchens', re.compile(r'^How many kitchens')),
    ('count_bedrooms', re.compile(r'^How many bedrooms')),
    ('count_bathrooms', re.compile(r'^How many bathrooms or washrooms')),
    ('count_wc_rooms', re.compile(r'^How many separate WC rooms')),
    ('count_balconies', re.compile(r'^How many balconies')),
]
LABELS_CUBICASA = {
    'count_bedrooms': {'MH', 'MAKUUHUONE'},
    'count_kitchens': {'K', 'KK', 'KT', 'KEITTIÖ', 'KEITTIO', 'AVOK', 'KEITTOKOMERO'},
    'count_bathrooms': {'KH', 'KPH', 'PH', 'PESUH', 'PESUHUONE', 'SH', 'KYLPYHUONE'},
    'count_wc_rooms': {'WC'},
    'count_saunas': {'S', 'SAUNA', 'LS'},
    'count_balconies': {'PARVEKE', 'PARV', 'LPARV'},
}
ANSWERABLE |= set(LABELS_CUBICASA)
APT_TYPE = re.compile(r'\d\s*(H|MH)\s*\+')  # apartment type strings such as "3H+KT+S" describe the flat, not one room


def label_parts(t):
    """Upper-case label parts of one OCR token; empty for area strings and apartment-type strings."""
    s = t['text'].upper()
    if 'M²' in s or 'M2' in s or APT_TYPE.search(s):
        return set()
    return {x for x in re.split(r'[\s/+,.\-()]+', s) if x and not re.search(r'\d', x)}
APT = re.compile(r'^(?:Whg\.|App\.|Appt\.|Apt\.)\s*([A-Z])$')


def attach_count_questions(dataset='swiss'):
    path = SEMSEG / f'datasets/{dataset}/pages.json'
    meta = load(path)
    prefix, fams = {'swiss': ('data/swiss_dwellings/', COUNT), 'cubicasa': ('data/cubicasa5k/', COUNT_CUBICASA)}[dataset]
    qs = {}
    with open(JUNCTION / 'questions.jsonl') as f:
        for line in f:
            q = json.loads(line)
            if q['type'] == 'count' and q['path'].startswith(prefix) and q['question'].startswith('How'):
                qs.setdefault(Path(q['path']).stem, []).append(q)
    n = 0
    for p in meta['pages']:
        out = []
        key = p['id'].split('/', 1)[1] if dataset == 'swiss' else p['id'].split('/', 1)[1].replace('/', '-')
        for q in qs.get(key, []):
            for fam, rx in fams:
                m = rx.match(q['question'])
                if m:
                    out.append({'family': fam, 'target': m.group(1) if m.groups() else None, 'question': q['question'],
                                'answer': q['answer']})
                    break
        p['count_questions'] = out
        n += len(out)
    dump(path, meta)
    return n


def norm(s):
    s = unicodedata.normalize('NFKC', s or '').casefold().strip()
    return re.sub(r'[^\w/ ]+', '', s).strip(' /')


def room_names(graph, tokens):
    """Name tokens bound to rooms: OCR words inside each room polygon (room numbers, areas excluded)."""
    rooms = [(r['id'], Polygon(r['polygon'])) for r in graph['rooms']]
    names = {rid: [] for rid, _ in rooms}
    for t in tokens:
        if t['kind'] != 'word' or APT.match(t['text']):
            continue
        c = Point((t['bbox'][0] + t['bbox'][2]) / 2, (t['bbox'][1] + t['bbox'][3]) / 2)
        for rid, g in rooms:
            if g.contains(c):
                names[rid].append(norm(t['text']))
                break
    return names


BY_CLASS_CUBICASA = {'count_bedrooms': 1, 'count_kitchens': 2}  # CLASSES['cubicasa'] index; chosen on validation


def answer(q, graph, tokens):
    if q['family'] in BY_CLASS_CUBICASA:  # reference classes give 96% / 100% here, OCR labels only 54%
        return sum(1 for r in graph['rooms'] if r.get('type', r.get('class')) == BY_CLASS_CUBICASA[q['family']])
    if q['family'] in LABELS_CUBICASA:
        want = LABELS_CUBICASA[q['family']]
        rooms = [Polygon(r['polygon']).buffer(0) for r in graph['rooms']]
        hit = set()
        for t in tokens:
            if label_parts(t) & want:
                c = Point((t['bbox'][0] + t['bbox'][2]) / 2, (t['bbox'][1] + t['bbox'][3]) / 2)
                k = next((i for i, g in enumerate(rooms) if g.contains(c)), None)
                if k is not None:
                    hit.add(k)
        return len(hit)
    if q['family'] == 'count_rooms_label':
        target = norm(q['target'])
        return sum(1 for ns in room_names(graph, tokens).values() if target in ns)
    if q['family'] == 'count_apartments':
        return len({APT.match(t['text']).group(1) for t in tokens if APT.match(t['text'])})
    return None


def answer_text_only(q, tokens):
    """Control: OCR tokens only, no rooms. Label count = number of matching name tokens on the sheet."""
    if q['family'] in LABELS_CUBICASA:
        return sum(1 for t in tokens if label_parts(t) & LABELS_CUBICASA[q['family']])
    if q['family'] == 'count_rooms_label':
        target = norm(q['target'])
        return sum(1 for t in tokens if t['kind'] == 'word' and norm(t['text']) == target)
    if q['family'] == 'count_apartments':
        return len({APT.match(t['text']).group(1) for t in tokens if APT.match(t['text'])})
    return None


def score_counts(pages_graphs, text_only=False):
    """pages_graphs: [(page, graph, tokens)]. Returns {family: {'n', 'exact'}} with None answers counted wrong."""
    from collections import defaultdict
    agg = defaultdict(lambda: [0, 0, 0])
    for page, graph, tokens in pages_graphs:
        for q in page.get('count_questions', []):
            a = answer_text_only(q, tokens) if text_only else answer(q, graph, tokens)
            agg[q['family']][0] += 1
            agg[q['family']][1] += int(a is not None and a == q['answer'])
            agg[q['family']][2] += int(a is not None)
    return {f: {'n': v[0], 'exact': round(100 * v[1] / v[0], 1), 'answerable': f in ANSWERABLE} for f, v in agg.items()}


if __name__ == '__main__':
    import sys
    print(attach_count_questions(sys.argv[1] if len(sys.argv) > 1 else 'swiss'), 'count questions attached')
