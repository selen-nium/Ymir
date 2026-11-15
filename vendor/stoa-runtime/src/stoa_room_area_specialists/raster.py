"""Pinned offline Raster2Seq inference and reversible original-pixel transforms.

This module never reads reference annotations, native receipts or solver transcripts.
Only deterministic predictions are cached across trajectories.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import subprocess
import threading
import time

from PIL import Image, ImageDraw

SOURCE_COMMIT = 'd938188027cd4f2bed9f95c883e5cc6d80b507f5'
CHECKPOINT_REVISION = '58e66d2887a581e4c9750a905872901ea92e4318'
CHECKPOINT_SHA256 = 'f7c0ef9379aa8cf11349da8b2179214e0d495af758383984a2361a1309aab3b7'
SEMANTIC_LABELS = {0:'Outdoor',1:'Kitchen',2:'Living Room',3:'Bed Room',4:'Bath',5:'Entry',6:'Storage',7:'Garage',8:'Undefined',9:'Window',10:'Door'}
_WORKERS = {}
_WORKERS_LOCK = threading.Lock()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def letterbox(width, height, size=256):
    """Match upstream int-truncated ResizeAndPad, including unequal axis scales."""
    if width <= 0 or height <= 0:
        raise ValueError('Positive view dimensions required')
    factor = min(size / height, size / width)
    resized_width, resized_height = int(width * factor), int(height * factor)
    if min(resized_width, resized_height) < 1:
        raise ValueError('View aspect ratio collapses an image dimension')
    return dict(original_width=width, original_height=height, resized_width=resized_width,
                resized_height=resized_height, left=(size-resized_width)//2,
                top=(size-resized_height)//2, model_size=size)


def recover_polygon(vertices, preprocessing, transform):
    """Undo the actual resize/pad then view crop transform. Never clip or repair."""
    p = preprocessing
    for key in ('scale_x', 'scale_y', 'origin_x', 'origin_y'):
        if not math.isfinite(transform[key]):
            raise ValueError('Nonfinite view transform')
    if min(transform['scale_x'], transform['scale_y']) <= 0:
        raise ValueError('Positive view scales required')
    original = []
    clipped = False
    for x, y in vertices:
        if not all(math.isfinite(v) for v in (x, y)):
            raise ValueError('Nonfinite predicted vertex')
        vx = (x-p['left']) * p['original_width']/p['resized_width']
        vy = (y-p['top']) * p['original_height']/p['resized_height']
        # A vertex at the last resized pixel may represent a truncated boundary.
        if x <= p['left'] or y <= p['top'] or x >= p['left']+p['resized_width']-1 or y >= p['top']+p['resized_height']-1:
            clipped = True
        original.append([vx*transform['scale_x']+transform['origin_x'],
                         vy*transform['scale_y']+transform['origin_y']])
    return original, clipped


def prediction_key(view, model_identity):
    # Deliberately ignore answers, question text, caller/model and trajectory folder.
    return canonical_digest(dict(image_sha256=digest(view['path']), source_id=view['source_id'],
                                 source_sha256=view['source_sha256'], width=view['width'], height=view['height'],
                                 transform=view['transform'], original_width=view['original_width'],
                                 original_height=view['original_height'], model_identity=model_identity))


class RasterUnavailable(RuntimeError):
    pass


class _Worker:
    def __init__(self, root):
        home = root/'data/room-area-specialists/raster'
        env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                   CUDA_VISIBLE_DEVICES='0', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
                   PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        self.log = (home/'worker.log').open('a')
        self.process = subprocess.Popen([str(home/'env/bin/python'), '-u',
            str(Path(__file__).with_name('raster_worker.py')), str(root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True,
            bufsize=1, env=env, start_new_session=True)
        self.lock = threading.Lock()
        atexit.register(self.close)

    def request(self, path, timeout=120):
        with self.lock:
            try:
                if self.process.poll() is not None:
                    raise RasterUnavailable('GPU worker exited; inspect raster/worker.log')
                self.process.stdin.write(json.dumps({'path': str(path)})+'\n')
                self.process.stdin.flush()
                with selectors.DefaultSelector() as selector:
                    selector.register(self.process.stdout, selectors.EVENT_READ)
                    if not selector.select(timeout):
                        raise TimeoutError('GPU worker deadline exceeded')
                    line = self.process.stdout.readline()
                if not line:
                    raise RasterUnavailable('GPU worker closed output; inspect worker.log')
                result = json.loads(line)
                if 'error' in result:
                    raise RasterUnavailable(result['error'])
                return result
            except BaseException:
                # An interrupted protocol may leave a reply pending. Terminate the
                # process so no later view can accidentally consume that reply.
                try:
                    self.close()
                except Exception:
                    pass
                raise

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if not self.log.closed:
            self.log.close()


class RasterBackend:
    def __init__(self, root=None, folder=None):
        self.root = Path(root or Path(__file__).resolve().parents[2]).resolve()
        self.home = self.root/'data/room-area-specialists/raster'
        self.folder = Path(folder or self.home/'predictions')
        self.folder.mkdir(parents=True, exist_ok=True)
        self.cache = self.home/'cache'
        self.cache.mkdir(parents=True, exist_ok=True)

    def close(self):
        """Release the schedule's warm worker, including cancellation cleanup."""
        with _WORKERS_LOCK:
            worker = _WORKERS.pop(str(self.root), None)
        if worker is not None:
            worker.close()

    def readiness(self):
        path = self.home/'readiness.json'
        if not path.exists():
            return dict(ready=False, backend='raster2seq', reason='Explicit setup and actual GPU smoke required')
        record = json.loads(path.read_text())
        if not record.get('ready'):
            return record
        for relative, expected in record.get('verified_files', {}).items():
            p = self.root/relative
            if not p.is_file() or digest(p) != expected:
                return dict(ready=False, backend='raster2seq', reason='Pinned asset or adapter changed: '+relative)
        if record.get('checkpoint_sha256') != CHECKPOINT_SHA256 or not record.get('gpu_smoke', {}).get('passed'):
            return dict(ready=False, backend='raster2seq', reason='Missing verified checkpoint or local GPU smoke')
        return record

    def predict(self, view, timeout=120):
        ready = self.readiness()
        if not ready.get('ready'):
            raise RasterUnavailable(ready.get('reason', 'Raster2Seq not ready'))
        with Image.open(view['path']) as image:
            if image.size != (view['width'], view['height']):
                raise ValueError('Registered view dimensions do not match image')
        identity = ready['model_identity']
        key = prediction_key(view, identity)
        cached = self.cache/(key+'.json')
        hit = cached.exists()
        if hit:
            receipt = json.loads(cached.read_text())
            if receipt.get('request_key') != key or canonical_digest(receipt['payload']) != receipt.get('payload_sha256'):
                raise ValueError('Cached prediction receipt failed integrity verification')
            raw = receipt['payload']
        else:
            with _WORKERS_LOCK:
                worker = _WORKERS.get(str(self.root))
                if worker is None:
                    worker = _WORKERS[str(self.root)] = _Worker(self.root)
            try:
                raw = worker.request(view['path'], timeout=timeout)
            except BaseException:
                with _WORKERS_LOCK:
                    if _WORKERS.get(str(self.root)) is worker:
                        _WORKERS.pop(str(self.root))
                # Also covers injected/external workers; closing is idempotent.
                try:
                    worker.close()
                except Exception:
                    pass
                raise
            if raw['preprocessing'] != letterbox(view['width'], view['height']):
                raise ValueError('Worker preprocessing disagrees with exact inverse')
            temp = cached.with_suffix('.tmp')
            receipt = dict(request_key=key, payload=raw, payload_sha256=canonical_digest(raw))
            temp.write_text(json.dumps(receipt, allow_nan=False, indent=2)+'\n')
            temp.replace(cached)
        if raw['preprocessing'] != letterbox(view['width'], view['height']):
            raise ValueError('Cached preprocessing disagrees with exact inverse')
        if len(raw['polygons']) != len(raw['labels']):
            raise ValueError('Prediction polygon/label counts disagree')
        return self._export(view, raw, key, hit, identity)

    def _export(self, view, raw, key, hit, identity):
        from shapely.geometry import Polygon
        candidates = []
        auxiliary = []
        for i, (vertices, label) in enumerate(zip(raw['polygons'], raw['labels'])):
            vertices_original, clipped = recover_polygon(vertices, raw['preprocessing'], view['transform'])
            flags = []
            if int(label) in (9,10):
                flags.append('non_room_semantic_class')
            if clipped:
                flags.append('clipped_at_view_edge')
            if len(vertices) < 3:
                auxiliary.append(dict(model_vertices=vertices, semantic_class=label, quality_flags=['window_or_door_line']))
                continue
            polygon = Polygon(vertices_original)
            if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
                flags.append('invalid_topology')
            if Polygon(vertices).area < 100:
                raise ValueError('Official export minimum violated')
            c = dict(candidate_id='r2s-'+canonical_digest(dict(request=key, index=i, vertices=vertices, label=label))[:24],
                     polygon=dict(exterior=vertices_original, holes=[]), semantic_class=int(label), semantic_label=SEMANTIC_LABELS.get(int(label),'Unknown'),
                     model_vertices=vertices, quality_flags=flags,
                     provenance=dict(backend='raster2seq', source_id=view['source_id'],
                         source_sha256=view['source_sha256'], view_id=view['view_id'], prediction_key=key,
                         model_identity=identity, preprocessing=raw['preprocessing'], view_transform=view['transform'],
                         coordinate_space='original_full_page_pixels'))
            candidates.append(c)
        image = Image.open(view['path']).convert('RGB')
        draw = ImageDraw.Draw(image)
        for c in candidates:
            points = [((x-view['transform']['origin_x'])/view['transform']['scale_x'],
                       (y-view['transform']['origin_y'])/view['transform']['scale_y']) for x,y in c['polygon']['exterior']]
            draw.line(points+[points[0]], fill=(225,25,50), width=max(2,round(image.width/800)))
            draw.text(points[0], c['candidate_id'][-6:], fill=(0,50,220))
        overlay = self.folder/(key+'-overlay.png')
        image.save(overlay)
        result = dict(backend='raster2seq', view_id=view['view_id'], candidates=candidates,
                      auxiliary_predictions=auxiliary, prediction_key=key, cache_hit=hit,
                      preprocessing=raw['preprocessing'], runtime_seconds=raw['runtime_seconds'],
                      overlay=str(overlay), overlays=[str(overlay)],
                      limitations='Official integer-pixel export and 100 model-pixel² minimum limit precision and small-room recall; no holes predicted; no geometry repair.')
        (self.folder/(key+'.json')).write_text(json.dumps(result,allow_nan=False,indent=2)+'\n')
        return result
