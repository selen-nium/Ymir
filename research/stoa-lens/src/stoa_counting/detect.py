"""Model wrappers and tiled whole-page inference.

Every model sees the same overlapping tiles of the original page and returns boxes in page
pixels. Tile results are merged class by class: a box is dropped when it overlaps a kept,
higher-scoring box of the same class (IoU >= 0.5, or >= 0.7 of the smaller box, which removes the
partial copies an object leaves at tile edges).
"""
import numpy as np
import torch
from PIL import Image

from .common import CLASSES, COCO_NAMES, OWL_QUERIES

TILE = 640
OVERLAP = 160
KEEP_SCORE = 0.05  # stored predictions; counting thresholds are chosen later on validation
MAX_PER_PAGE = 1000

Image.MAX_IMAGE_PIXELS = None

REVISIONS = {
    'dfine': ('ustc-community/dfine-large-coco', 'aa8f8211545e4cff5adf52ea512f9ac74a9824ec'),
    'owlv2': ('google/owlv2-base-patch16-ensemble', 'cfd3195ba4ea9592eec887ded089f4c08eff231d'),
}


def tile_origins(w, h, tile=TILE, overlap=OVERLAP):
    stride = tile - overlap

    def axis(n):
        if n <= tile:
            return [0]
        xs = list(range(0, n - tile, stride))
        return xs + [n - tile]
    return [(x, y) for y in axis(h) for x in axis(w)]


def merge(dets, iou=0.5, ios=0.7):
    """dets: array (N, 6) = cls, score, x0, y0, x1, y1 in page pixels."""
    if not len(dets):
        return dets
    keep = []
    for c in np.unique(dets[:, 0]):
        d = dets[dets[:, 0] == c]
        d = d[np.argsort(-d[:, 1])]
        boxes = d[:, 2:6]
        area = (boxes[:, 2] - boxes[:, 0]).clip(0) * (boxes[:, 3] - boxes[:, 1]).clip(0)
        alive = np.ones(len(d), bool)
        for i in range(len(d)):
            if not alive[i]:
                continue
            keep.append(d[i])
            xx0 = np.maximum(boxes[i, 0], boxes[i + 1:, 0]); yy0 = np.maximum(boxes[i, 1], boxes[i + 1:, 1])
            xx1 = np.minimum(boxes[i, 2], boxes[i + 1:, 2]); yy1 = np.minimum(boxes[i, 3], boxes[i + 1:, 3])
            inter = (xx1 - xx0).clip(0) * (yy1 - yy0).clip(0)
            union = area[i] + area[i + 1:] - inter
            small = np.minimum(area[i], area[i + 1:]).clip(1e-6)
            kill = (inter / union.clip(1e-6) >= iou) | (inter / small >= ios)
            alive[i + 1:] &= ~kill
    out = np.array(keep)
    return out[np.argsort(-out[:, 1])][:MAX_PER_PAGE]


def predict_page(model, image_path, batch=8):
    im = Image.open(image_path).convert('RGB')
    W, H = im.size
    origins = tile_origins(W, H)
    found = []
    for i in range(0, len(origins), batch):
        chunk = origins[i:i + batch]
        tiles = [im.crop((x, y, min(x + TILE, W), min(y + TILE, H))) for x, y in chunk]
        for (x, y), dets in zip(chunk, model.predict(tiles)):
            for c, s, x0, y0, x1, y1 in dets:
                found.append((c, s, x0 + x, y0 + y, x1 + x, y1 + y))
    dets = merge(np.array(found, dtype=float).reshape(-1, 6))
    return [[int(d[0]), round(float(d[1]), 4), *[round(float(v), 1) for v in d[2:]]] for d in dets]


class RFDETR:
    """RF-DETR. Base = COCO weights (only 'toilet' and 'sink' overlap our classes)."""

    def __init__(self, dataset, size='medium', weights=None, train_classes=None):
        import rfdetr
        from rfdetr.assets.coco_classes import COCO_CLASSES
        cls = {'medium': rfdetr.RFDETRMedium, 'large': rfdetr.RFDETRLarge}[size]
        self.classes = CLASSES[dataset]
        if weights:
            names = train_classes or self.classes  # model's own label order (0-based)
            self.model = cls(pretrain_weights=str(weights), num_classes=len(names))
            self.map = {i: self.classes.index(n) for i, n in enumerate(names) if n in self.classes}
        else:
            self.model = cls()
            by_name = {v: k for k, v in COCO_CLASSES.items()}
            self.map = {by_name[COCO_NAMES[c]]: self.classes.index(c) for c in self.classes if c in COCO_NAMES}

    def predict(self, tiles):
        out = self.model.predict(tiles, threshold=KEEP_SCORE)
        out = out if isinstance(out, list) else [out]
        res = []
        for d in out:
            rows = []
            for box, cid, s in zip(d.xyxy, d.class_id, d.confidence):
                if int(cid) in self.map:
                    rows.append((self.map[int(cid)], float(s), *map(float, box)))
            res.append(rows)
        return res


class YOLO:
    """Ultralytics YOLO11 (YOLOplan's detector). Base = COCO weights."""

    def __init__(self, dataset, weights='yolo11s.pt'):
        from ultralytics import YOLO as _Y
        self.classes = CLASSES[dataset]
        self.model = _Y(str(weights))
        names = self.model.names
        if 'person' in names.values():  # COCO-pretrained weights
            by_name = {v: k for k, v in names.items()}
            self.map = {by_name[COCO_NAMES[c]]: self.classes.index(c) for c in self.classes if c in COCO_NAMES}
        else:
            self.map = {k: self.classes.index(v) for k, v in names.items() if v in self.classes}

    def predict(self, tiles):
        res = []
        size = TILE if max(max(t.size) for t in tiles) <= TILE else WHOLE_MAX_SIDE
        for r in self.model.predict(tiles, conf=KEEP_SCORE, imgsz=size, verbose=False, max_det=300):
            b = r.boxes
            rows = [(self.map[int(c)], float(s), *map(float, xy)) for xy, c, s in
                    zip(b.xyxy.cpu().numpy(), b.cls.cpu().numpy(), b.conf.cpu().numpy()) if int(c) in self.map]
            res.append(rows)
        return res


class DFINE:
    """D-FINE-L via transformers. Base = COCO weights."""

    def __init__(self, dataset, weights=None, repo=None, rev=None):
        from transformers import AutoImageProcessor, DFineForObjectDetection
        self.classes = CLASSES[dataset]
        repo, rev = (repo, rev) if repo else REVISIONS['dfine']
        src = str(weights) if weights else repo
        kw = {} if weights else {'revision': rev}
        self.processor = AutoImageProcessor.from_pretrained(repo, revision=rev)
        self.model = DFineForObjectDetection.from_pretrained(src, **kw).to('cuda').eval()
        names = self.model.config.id2label
        if weights:
            self.map = {int(k): self.classes.index(v) for k, v in names.items() if v in self.classes}
        else:
            by_name = {v: int(k) for k, v in names.items()}
            self.map = {by_name[COCO_NAMES[c]]: self.classes.index(c) for c in self.classes if c in COCO_NAMES}

    @torch.no_grad()
    def predict(self, tiles):
        inputs = self.processor(images=tiles, return_tensors='pt').to('cuda')
        out = self.model(**inputs)
        sizes = torch.tensor([[t.height, t.width] for t in tiles], device='cuda')
        res = []
        for r in self.processor.post_process_object_detection(out, target_sizes=sizes, threshold=KEEP_SCORE):
            res.append([(self.map[int(c)], float(s), *map(float, b)) for b, c, s in
                        zip(r['boxes'].cpu().numpy(), r['labels'].cpu().numpy(), r['scores'].cpu().numpy())
                        if int(c) in self.map])
        return res


class OWLV2:
    """OWLv2 open-vocabulary detector, text-prompted for every class (zero-shot only)."""

    def __init__(self, dataset, repo=None, rev=None):
        from transformers import Owlv2ForObjectDetection, Owlv2Processor
        self.classes = CLASSES[dataset]
        repo, rev = (repo, rev) if repo else REVISIONS['owlv2']
        self.processor = Owlv2Processor.from_pretrained(repo, revision=rev)
        self.model = Owlv2ForObjectDetection.from_pretrained(repo, revision=rev).to('cuda').eval()
        self.queries, self.qmap = [], []
        for i, c in enumerate(self.classes):
            for q in OWL_QUERIES[c]:
                self.queries.append(q)
                self.qmap.append(i)

    @torch.no_grad()
    def predict(self, tiles):
        res = []
        for t in tiles:
            # OWLv2 pads to a square; target size must be the padded square to map boxes back.
            side = max(t.width, t.height)
            inputs = self.processor(text=[self.queries], images=t, return_tensors='pt').to('cuda')
            out = self.model(**inputs)
            r = self.processor.post_process_grounded_object_detection(out, threshold=KEEP_SCORE,
                                                             target_sizes=torch.tensor([[side, side]], device='cuda'))[0]
            rows = []
            for b, q, s in zip(r['boxes'].cpu().numpy(), r['labels'].cpu().numpy(), r['scores'].cpu().numpy()):
                x0, y0, x1, y1 = b
                rows.append((self.qmap[int(q)], float(s), max(0, x0), max(0, y0), min(t.width, x1), min(t.height, y1)))
            res.append(rows)
        return res


WHOLE_MAX_SIDE = 1024  # room detectors see the whole page, scaled so its long side is at most this


def predict_whole(model, image_path, max_side=WHOLE_MAX_SIDE):
    """Whole-page inference for large regions (rooms, apartments, floors): one scaled image, boxes mapped back."""
    im = Image.open(image_path).convert('RGB')
    W, H = im.size
    f = min(1.0, max_side / max(W, H))
    small = im.resize((max(1, round(W * f)), max(1, round(H * f))), Image.Resampling.LANCZOS) if f < 1 else im
    dets = [(c, s, x0 / f, y0 / f, x1 / f, y1 / f) for c, s, x0, y0, x1, y1 in model.predict([small])[0]]
    dets = merge(np.array(dets, dtype=float).reshape(-1, 6), ios=0.95)  # rooms nest, so only near-duplicates merge
    return [[int(d[0]), round(float(d[1]), 4), *[round(float(v), 1) for v in d[2:]]] for d in dets]
