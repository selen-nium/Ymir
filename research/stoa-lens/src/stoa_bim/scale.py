"""Read the drawing scale from the PNG (issue 4: scale was given, not read).

Swiss (Junction sheets): the title block prints "Massstab 1:n" (or "Echelle", "Scala", "Scale"). The PNG is the PDF
rendered at 150 DPI, so one pixel is 0.0254 / 150 m of paper and n * 0.0254 / 150 m of building.
RapidOCR runs on the sheet with the plan crop blanked (the title block is outside it); results are cached.
ResBIM (Revit plans, no printed scale): dimension strings. Each horizontal dimension number is matched to the
dimension line under it; the segment between the tick marks either side of the number is measured in pixels;
metres per pixel = value_mm / 1000 / length_px. The median over all dimensions is used.
python -m stoa_bim.scale swiss validation,test | resbim 0 100
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / 'data/png-to-bim/scale-ocr'
RX = re.compile(r'1\s*[:：]\s*(\d{2,4})')
_ocr = None


def ocr(img):
    from stoa_iterative.text import engine
    r = engine()(np.asarray(img))
    if r.txts is None:
        return []
    out = []
    for b, t, s in zip(r.boxes, r.txts, r.scores):
        b = np.asarray(b, float)
        out.append({'text': t.strip(), 'box': [float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 0].max()), float(b[:, 1].max())], 'score': float(s)})
    return out


def swiss_scale(page, image_path):
    f = CACHE / 'swiss' / (page['id'].replace('/', '__') + '.json')
    if f.exists():
        toks = json.loads(f.read_text())
    else:
        im = Image.open(image_path).convert('RGB')
        x0, y0, x1, y1 = page['crop']
        blank = im.copy()
        blank.paste((255, 255, 255), (x0, y0, x1, y1))
        toks = ocr(blank)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(toks))
    for t in toks:
        m = RX.search(t['text'].replace(' ', ''))
        if m:
            n = int(m.group(1))
            if 10 <= n <= 1000:
                return {'denominator': n, 'm_per_px': n * 0.0254 / 150, 'text': t['text']}
    return None


def resbim_scale(png_path, n_id, ink_thr=200):
    """Median metres-per-pixel from dimension strings (horizontal ones)."""
    f = CACHE / 'resbim' / f'{n_id}.json'
    im = Image.open(png_path).convert('L')
    a = np.asarray(im)
    if f.exists():
        toks = json.loads(f.read_text())
    else:
        sc = 0.5
        toks = ocr(im.resize((int(im.width * sc), int(im.height * sc))).convert('RGB'))
        for t in toks:
            t['box'] = [v / sc for v in t['box']]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(toks))
    ink = a < ink_thr
    H, W = ink.shape
    est = []
    for t in toks:
        if not re.fullmatch(r'\d{3,5}', t['text'].strip()):
            continue
        val = int(t['text'].strip())
        x0, y0, x1, y1 = t['box']
        if (x1 - x0) < (y1 - y0):  # vertical text: skip
            continue
        th = y1 - y0
        cx = int((x0 + x1) / 2)
        # the dimension line: first row below the text with a long horizontal ink run through cx
        row = None
        for y in range(int(y1), min(H, int(y1 + 1.5 * th))):
            if ink[y, max(0, cx - 3):cx + 4].any():
                xs = cx
                while xs > 0 and ink[y, xs - 1]:
                    xs -= 1
                xe = cx
                while xe < W - 1 and ink[y, xe + 1]:
                    xe += 1
                if xe - xs > 3 * (x1 - x0) * 0.6:
                    row = y
                    break
        if row is None:
            continue
        # tick marks: columns where ink crosses the line (extends above and below it)
        band_up = ink[max(0, row - 8):row - 2, :].any(0)
        band_dn = ink[row + 3:min(H, row + 9), :].any(0)
        tick = band_up & band_dn & ink[row, :]
        left = np.nonzero(tick[:cx])[0]
        right = np.nonzero(tick[cx:])[0]
        if not len(left) or not len(right):
            continue
        L = (cx + right[0]) - left[-1]
        if L > 10:
            est.append(val / 1000 / L)
    if len(est) < 2:
        return None
    est = np.array(est)
    med = float(np.median(est))
    good = est[np.abs(est - med) < 0.05 * med]
    return {'m_per_px': float(np.median(good)) if len(good) else med, 'n_dims': int(len(est)), 'n_agree': int(len(good))}


if __name__ == '__main__':
    from stoa_semseg.common import image_path, pages
    if sys.argv[1] == 'swiss':
        rows = []
        for split in sys.argv[2].split(','):
            for p in pages('swiss', split):
                r = swiss_scale(p, image_path(p))
                rows.append({'id': p['id'], 'read': r, 'true_m_per_px': p['m_per_px'],
                             'ok': r is not None and abs(r['m_per_px'] - p['m_per_px']) <= 0.01 * p['m_per_px']})
        print(json.dumps({'sheets': len(rows), 'read': sum(r['read'] is not None for r in rows), 'within_1pct': sum(r['ok'] for r in rows)}))
        (ROOT / 'data/png-to-bim/scale-swiss.json').write_text(json.dumps(rows, indent=1))
    else:
        from . import ifcref as R
        first, count = int(sys.argv[2]), int(sys.argv[3])
        rows = []
        ids = sorted(int(p.name.split('-')[1].split('_')[0]) for p in R.SRC.glob('resbim-*_plan.png'))[first:first + count]
        fits = {r['id']: r for r in json.load(open(ROOT / 'data/png-to-bim/resbim-0-100.json'))['units'] if 'error' not in r}
        for n in ids:
            r = resbim_scale(R.SRC / f'resbim-{n}_plan.png', n)
            fit = fits.get(f'resbim/{n}', {}).get('m_per_px')
            rows.append({'id': f'resbim/{n}', 'read': r, 'fitted_m_per_px': fit,
                         'err_pct': None if r is None or fit is None else round(100 * (r['m_per_px'] / fit - 1), 2)})
            print(rows[-1], flush=True)
        e = [abs(r['err_pct']) for r in rows if r['err_pct'] is not None]
        print(json.dumps({'units': len(rows), 'read': len(e), 'median_abs_err_pct': float(np.median(e)) if e else None,
                          'within_1pct': sum(x <= 1 for x in e), 'within_2pct': sum(x <= 2 for x in e)}))
        (ROOT / 'data/png-to-bim/scale-resbim.json').write_text(json.dumps(rows, indent=1))
