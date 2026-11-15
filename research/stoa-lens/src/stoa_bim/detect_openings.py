"""Run the counting trials' Swiss door / window detector (RF-DETR, fine-tuned on Swiss train sheets) on Junction's PNGs.

The detector was trained on the counting trials' own Swiss renders (about 0.034 m per pixel). Junction's sheets are drawn
at their own scale (m_per_px, often 0.017), so each PNG is resized to the training scale first, tiled as in training,
and the boxes are mapped back to Junction pixels.
python -m stoa_bim.detect_openings <split>[,<split>]
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from stoa_counting import detect as Dt
from stoa_semseg.common import image_path, pages

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS = ROOT / 'data/counting-trials/runs/rfdetr/swiss/checkpoint_best_total.pth'
OUT = ROOT / 'data/png-to-bim/detections'
TRAIN_MPP = 0.0339  # counting-trials Swiss sheets: 1:200 on an A3 page 2480 px wide


def run(split):
    model = Dt.RFDETR('swiss', size='medium', weights=WEIGHTS)
    out = {}
    tmp = OUT / 'tmp'
    tmp.mkdir(parents=True, exist_ok=True)
    for p in pages('swiss', split):
        f = min(1.0, p['m_per_px'] / TRAIN_MPP)
        im = Image.open(image_path(p)).convert('RGB')
        small = im.resize((max(1, int(im.width * f)), max(1, int(im.height * f))), Image.LANCZOS)
        path = tmp / 'page.png'
        small.save(path)
        dets = Dt.predict_page(model, path)
        out[p['id']] = [[c, s, x0 / f, y0 / f, x1 / f, y1 / f] for c, s, x0, y0, x1, y1 in dets]
        print(p['id'], len(dets), flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'{split}.json').write_text(json.dumps({'weights': str(WEIGHTS), 'train_mpp': TRAIN_MPP, 'pages': out}))


if __name__ == '__main__':
    for s in sys.argv[1].split(','):
        run(s)
