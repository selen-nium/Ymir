"""Zoomed re-segmentation: re-run fine-tuned EoMT-L on a window around each room at about 2x the whole-plan zoom,
and replace the room's outline with the best-overlapping instance from that window (IoU >= 0.5 with the old one).
Mask cells shrink from about 7 to about 3.5 page pixels, while rooms stay near the size seen in training.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from shapely.geometry import Polygon

from stoa_semseg import eomt as E
from stoa_semseg.common import load_crop, pages
from stoa_semseg.cpu_arms import write

import os
ZOOM = float(os.environ.get('ZOOM', '2.0'))
ACCEPT_IOU = float(os.environ.get('ZOOM_ACCEPT_IOU', '0.5'))  # replace a room only if the zoomed instance overlaps it this much


def refine_page(model, page, instances, size, thr):
    rgb, off = load_crop(page)
    W, H = rgb.size
    win = int(max(W, H) / ZOOM)
    out = []
    for inst in [i for i in instances if i['score'] >= thr]:
        g = Polygon(inst['polygons'][0]).buffer(0)
        if g.is_empty:
            continue
        cx, cy = g.centroid.x - off[0], g.centroid.y - off[1]
        w = max(win, int(1.4 * max(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1])))
        x0, y0 = int(np.clip(cx - w / 2, 0, max(0, W - w))), int(np.clip(cy - w / 2, 0, max(0, H - w)))
        sub = rgb.crop((x0, y0, min(W, x0 + w), min(H, y0 + w)))
        cand = E.infer(model, sub, (off[0] + x0, off[1] + y0), size, True, max_inst=60, min_score=0.2)
        best, biou = None, 0.0
        for c in cand:
            p = Polygon(c['polygons'][0]).buffer(0)
            if p.is_empty:
                continue
            inter = p.intersection(g).area
            iou = inter / (p.area + g.area - inter)
            if iou > biou:
                best, biou = c, iou
        out.append({**inst, 'polygons': best['polygons'][:1]} if best is not None and biou >= ACCEPT_IOU else inst)
    return out


def run(dataset, split, base_arm='eomt-finetuned', thr=0.5):
    from transformers import EomtForUniversalSegmentation
    from stoa_semseg.evaluate import read_predictions
    from stoa_semseg import cpu_arms
    model = EomtForUniversalSegmentation.from_pretrained(E.run_dir(dataset) / 'best').cuda().eval()
    model.attn_mask_probs.zero_()
    rows = read_predictions(cpu_arms.pred_path(base_arm, dataset, split))
    out = []
    for p in pages(dataset, split):
        t = time.time()
        inst = refine_page(model, p, rows[p['id']]['instances'], E.SIZE[dataset], thr)
        out.append({'id': p['id'], 'seconds': (rows[p['id']].get('seconds') or 0) + time.time() - t, 'instances': inst})
    tag = '' if (ZOOM, ACCEPT_IOU) == (2.0, 0.5) else f'-z{ZOOM:g}-iou{ACCEPT_IOU:g}'
    return write(f'{base_arm}-zoom{tag}', dataset, split, out)


if __name__ == '__main__':
    print(run(sys.argv[1], sys.argv[2]))
