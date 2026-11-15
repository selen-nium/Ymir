"""L2 text layer: OCR on the page crop (PNG only), cached per page.

RapidOCR (PP-OCR models, ONNX) on the crop upscaled 2x, which reads Swiss room numbers better than native
150 DPI. Tokens are typed by pattern: room_number ('2.10', 'U2.07'), area ('15.61 m²' or a number followed
by 'm²'), apartment ('Whg. A' / 'App. B'), word (anything else alphabetic), other.
"""
import json
import re
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from stoa_semseg.common import load_crop, pages

HOME = Path(__file__).resolve().parents[2] / 'data/iterative-structuring'
UPSCALE = 2
ROOM_NUMBER = re.compile(r'^[A-Z]{0,3}\d+\.\d{2,3}$')
AREA = re.compile(r'^(\d+(?:[.,]\d+)?)\s*m[²2]?$')
APT = re.compile(r'^(?:Whg\.|App\.|Appt\.|Apt\.)\s*([A-Z])$')
_ENGINE = None


def engine():
    global _ENGINE
    if _ENGINE is None:
        import logging
        logging.getLogger('RapidOCR').setLevel(logging.ERROR)
        from rapidocr import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


def kind(t):
    t = t.strip()
    if ROOM_NUMBER.match(t):
        return 'room_number'
    if APT.match(t):
        return 'apartment'
    if AREA.match(t) and ('m' in t):
        return 'area'
    if re.search(r'[A-Za-zÀ-ÿ]', t):
        return 'word'
    return 'other'


def ocr_page(page):
    rgb, (x0, y0) = load_crop(page)
    im = rgb.resize((rgb.width * UPSCALE, rgb.height * UPSCALE))
    r = engine()(np.asarray(im))
    toks = []
    if r.txts is not None:
        for box, txt, sc in zip(r.boxes, r.txts, r.scores):
            b = np.asarray(box, float) / UPSCALE + (x0, y0)
            toks.append({'text': txt.strip(), 'kind': kind(txt), 'conf': round(float(sc), 3),
                         'bbox': [round(float(b[:, 0].min()), 1), round(float(b[:, 1].min()), 1),
                                  round(float(b[:, 0].max()), 1), round(float(b[:, 1].max()), 1)]})
    # a bare number followed by a separate 'm²' token on the same line is an area
    for a, b in zip(toks, toks[1:]):
        if a['kind'] == 'other' and re.fullmatch(r'\d+(?:[.,]\d+)?', a['text']) and b['text'] in ('m²', 'm2'):
            a['kind'] = 'area'
    return toks


def path(dataset, page_id):
    return HOME / 'ocr' / dataset / (page_id.replace('/', '__') + '.json')


def _job(args):
    dataset, page = args
    p = path(dataset, page['id'])
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ocr_page(page)))


def run(dataset, splits=('validation', 'test'), workers=6):
    jobs = [(dataset, p) for s in splits for p in pages(dataset, s)]
    with Pool(workers) as pool:
        pool.map(_job, jobs, chunksize=2)
    return len(jobs)


def load(dataset, page_id):
    return json.loads(path(dataset, page_id).read_text())


def number_recall(dataset='swiss', split='validation'):
    """OCR recall/precision of printed room numbers against the reference room table (Swiss only)."""
    hit = ref = false = 0
    for p in pages(dataset, split):
        nums = {t['text'] for t in load(dataset, p['id']) if t['kind'] == 'room_number'}
        want = {r['number'] for r in p['rooms']}
        hit += len(nums & want); ref += len(want); false += len(nums - want)
    return {'room_numbers': ref, 'read': hit, 'recall': round(100 * hit / ref, 1), 'false_tokens': false}


if __name__ == '__main__':
    import sys
    ds = sys.argv[1]
    print(run(ds))
    if ds == 'swiss':
        print(number_recall('swiss', 'validation'), number_recall('swiss', 'test'))
