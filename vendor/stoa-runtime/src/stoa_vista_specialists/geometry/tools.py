"""Geometry adapters; reference labels and previous model results are never read."""
from __future__ import annotations

import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

from stoa_room_area_specialists import raster, native
from .lifecycle import terminate_and_collect


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def verified_pdf_view_fields(root, source):
    """Return native text registration only for independently registered Swiss pages."""
    root = Path(root).resolve()
    registration = root / 'data/room-area-specialists/native/source-registration.json'
    if not registration.is_file():
        return {'status': 'unavailable', 'reason': 'Native source registration missing', 'fields': {}}
    try:
        record = json.loads(registration.read_text())
        matches = [x for x in record['sources'] if x['source_id'] == source['source_id']]
        if not matches:
            return {'status': 'unavailable', 'reason': 'Unsupported native source', 'fields': {}}
        if len(matches) != 1:
            raise ValueError('Ambiguous native registration')
        item = matches[0]
        if source.get('page', 0) != 0 or item['image_sha256'] != source['source_sha256'] or native.sha(source['path']) != item['image_sha256']:
            raise ValueError('Source raster hash or page differs from registration')
        pdf = item['sources']['pdf']
        pdf_path = root / pdf['path']
        if native.sha(pdf_path) != pdf['sha256']:
            raise ValueError('Registered PDF hash changed')
        expected_matrix = [native.RENDER_SCALE, 0, 0, native.RENDER_SCALE, 0, 0]
        if item['original_render_matrix'] != expected_matrix:
            raise ValueError('Unsupported render registration')
        import pymupdf
        with pymupdf.open(pdf_path) as doc:
            page = doc[0]
            if page.rotation != 0 or page.cropbox != page.mediabox:
                raise ValueError('Unsupported PDF crop or rotation')
            pixmap = page.get_pixmap(dpi=300)
            if (pixmap.width, pixmap.height) != (source['width'], source['height']):
                raise ValueError('PDF render dimensions do not match raster')
        return {'status': 'ready', 'fields': {'pdf_path': str(pdf_path), 'pdf_sha256': pdf['sha256'], 'pdf_to_page': expected_matrix}, 'registration_sha256': native.sha(registration)}
    except (OSError, KeyError, ValueError, IndexError) as exc:
        return {'status': 'uncertain', 'reason': str(exc), 'fields': {}}


@contextmanager
def _gpu_lease(root, deadline):
    path = Path(root) / 'data/vista-specialists/gpu.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError('GPU resource wait exceeded deadline')
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class IsolatedRasterBackend(raster.RasterBackend):
    """Use pinned original assets with new logs and prediction cache."""
    def __init__(self, root, folder):
        super().__init__(root, folder)
        self.cache = Path(folder) / 'cache'
        self.cache.mkdir(parents=True, exist_ok=True)
        self._worker = None
        self._lock = threading.Lock()

    def make_worker(self):
        return _IsolatedWorker(self.root, self.folder)

    def predict(self, view, timeout=120):
        # Existing predictor owns a global worker and historical cache. Override its
        # request path so this study cannot write either of those locations.
        deadline = time.monotonic() + timeout
        ready = self.readiness()
        if not ready.get('ready'):
            raise raster.RasterUnavailable(ready.get('reason', 'Raster2Seq not ready'))
        from PIL import Image
        with Image.open(view['path']) as image:
            if image.size != (view['width'], view['height']):
                raise ValueError('Registered view dimensions do not match image')
        identity = ready['model_identity']
        key = raster.prediction_key(view, identity)
        cached = self.cache / (key + '.json')
        if not self._lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise TimeoutError('Raster backend wait exceeded deadline')
        try:
            hit = cached.exists()
            if hit:
                receipt = json.loads(cached.read_text())
                if receipt.get('request_key') != key or raster.canonical_digest(receipt['payload']) != receipt.get('payload_sha256'):
                    raise ValueError('Cached prediction failed integrity verification')
                raw = receipt['payload']
            else:
                try:
                    with _gpu_lease(self.root, deadline):
                        if self._worker is None:
                            self._worker = self.make_worker()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError('Raster deadline exceeded before inference')
                        raw = self._worker.request(view['path'], timeout=remaining)
                except BaseException:
                    if self._worker is not None:
                        self._worker.close()
                    self._worker = None
                    raise
                if raw['preprocessing'] != raster.letterbox(view['width'], view['height']):
                    raise ValueError('Worker preprocessing disagrees with inverse')
                tmp = cached.with_suffix('.tmp')
                tmp.write_text(json.dumps(dict(request_key=key, payload=raw, payload_sha256=raster.canonical_digest(raw)), allow_nan=False))
                tmp.replace(cached)
        finally:
            self._lock.release()
        if time.monotonic() >= deadline:
            raise TimeoutError('Raster deadline exceeded before export')
        if raw['preprocessing'] != raster.letterbox(view['width'], view['height']) or len(raw['polygons']) != len(raw['labels']):
            raise ValueError('Invalid prediction receipt')
        result = self._export(view, raw, key, hit, identity)
        for candidate in result['candidates']:
            candidate['confidence'] = None
            candidate['provenance']['page'] = view.get('page', 0)
        (self.folder / (key + '.json')).write_text(json.dumps(result, allow_nan=False, indent=2) + '\n')
        return result

    def close(self, timeout=2):
        # A concurrent inference must not make trajectory cleanup wait forever.
        acquired = self._lock.acquire(timeout=max(0, timeout))
        if not acquired:
            worker = self._worker
            if worker is not None:
                worker.close()
            acquired = self._lock.acquire(timeout=1)
            if not acquired:
                raise TimeoutError('Raster worker did not release its operation lock during cleanup')
        try:
            if self._worker is not None:
                self._worker.close()
                self._worker = None
        finally:
            self._lock.release()


class _IsolatedWorker(raster._Worker):
    def __init__(self, root, folder):
        home = root / 'data/room-area-specialists/raster'
        env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', CUDA_VISIBLE_DEVICES='0', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(Path(__file__).resolve().parents[2]))
        self.log = (Path(folder) / 'worker.log').open('a')
        self._own_group = not bool(os.environ.get('STOA_RASTER_PREFLIGHT_PGROUP'))
        self.process = subprocess.Popen([str(home/'env/bin/python'), '-u', str(Path(raster.__file__).with_name('raster_worker.py')), str(root)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1, env=env, start_new_session=self._own_group)
        self.lock = threading.Lock()

    def close(self):
        # Kill the isolated process group too; native libraries may create children.
        try:
            if self.process.poll() is None:
                try:
                    if self._own_group:
                        os.killpg(self.process.pid, signal.SIGTERM)
                    else:
                        self.process.terminate()
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:
                        if self._own_group:
                            os.killpg(self.process.pid, signal.SIGKILL)
                        else:
                            self.process.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        self.process.wait(timeout=1)
                    except subprocess.TimeoutExpired as exc:
                        raise TimeoutError('Raster worker cleanup unconfirmed after SIGKILL') from exc
                except ProcessLookupError:
                    pass
        finally:
            if not self.log.closed:
                self.log.close()


class GeometryTools:
    """Per-trajectory candidate/evidence registries. All coordinates are page pixels."""
    def __init__(self, root, folder, source_id, source_sha256, page, width, height, raster_backend=None, native_backend=None, native_source=None):
        self.root, self.folder = Path(root).resolve(), Path(folder).resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.source_id, self.source_sha256, self.page = source_id, source_sha256, page
        self.width, self.height = width, height
        self.candidates, self.calibrations, self.evidence = {}, {}, {}
        self.native_source = native_source
        self.raster = raster_backend or IsolatedRasterBackend(self.root, self.folder/'raster')
        if native_source is not None:
            if (native_source['source_id'],native_source['source_sha256'],native_source.get('page',0),native_source['width'],native_source['height']) != (source_id,source_sha256,page,width,height):
                raise ValueError('Registered native source does not match this trajectory.')
            from stoa_native_geometry import RegisteredNativeBackend
            native_backend = RegisteredNativeBackend(native_source)
        if native_backend is None:
            native_folder = self.folder/'native'
            native_folder.mkdir(parents=True, exist_ok=True)
            registration = self.root/'data/room-area-specialists/native/source-registration.json'
            if registration.exists():
                (native_folder/'source-registration.json').write_bytes(registration.read_bytes())
            native_backend = native.NativeBackend(self.root, native_folder)
        self.native = native_backend

    def _register_candidates(self, result):
        pending_candidates, pending_calibrations = {}, {}
        for candidate in result.get('candidates', []):
            provenance = candidate['provenance']
            if provenance.get('source_id') != self.source_id or provenance.get('page', 0) != self.page:
                raise ValueError('Candidate source/page mismatch')
            # Native extraction also independently verifies source hashes against its registration.
            source_hash = provenance.get('source_sha256')
            if source_hash is not None and source_hash != self.source_sha256:
                raise ValueError('Candidate source hash mismatch')
            ident = candidate['candidate_id']
            if (ident in self.candidates and self.candidates[ident] != candidate) or (ident in pending_candidates and pending_candidates[ident] != candidate):
                raise ValueError('Candidate ID collision')
            pending_candidates[ident] = candidate
            if candidate.get('calibration'):
                calibration = dict(candidate['calibration'], source_id=self.source_id, source_sha256=self.source_sha256, page=self.page)
                previous = pending_calibrations.get(calibration['calibration_id'], self.calibrations.get(calibration['calibration_id']))
                if previous is not None and previous != calibration:
                    raise ValueError('Calibration ID collision')
                pending_calibrations[calibration['calibration_id']] = calibration
        self.candidates.update(pending_candidates)
        self.calibrations.update(pending_calibrations)
        return result

    def native_room_candidates(self, source_id, page, room_identifier=None, timeout=None):
        if source_id != self.source_id or page != self.page:
            raise ValueError('Source/page outside registered trajectory')
        if source_id not in self.native.sources:
            return {'status': 'unavailable', 'candidates': [], 'quality_flags': ['unsupported_native_source']}
        if self.native.sources[source_id].get('image_sha256') != self.source_sha256:
            raise ValueError('Native registration does not match source hash')
        if timeout is not None:
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                raise TimeoutError('Native geometry deadline exceeded')
            payload = {'source_id': source_id, 'source_sha256': self.source_sha256, 'page':page, 'width':self.width, 'height':self.height, 'room_identifier':room_identifier,'native_source':self.native_source}
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(Path(__file__).resolve().parents[2]) + (os.pathsep + os.environ['PYTHONPATH'] if os.environ.get('PYTHONPATH') else ''))
            process = subprocess.Popen([sys.executable, '-m', 'stoa_vista_specialists.geometry.native_worker', str(self.root), str(self.folder)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
            try:
                out, err = process.communicate(json.dumps(payload), timeout=timeout)
            except BaseException as exc:
                cleanup = terminate_and_collect(process, exc)
                if isinstance(exc, subprocess.TimeoutExpired):
                    failure = TimeoutError('Native geometry deadline exceeded' + ('' if cleanup['cleanup_confirmed'] else '; cleanup unconfirmed'))
                    failure.receipt = cleanup
                    raise failure from exc
                try:
                    exc.receipt = cleanup
                except (AttributeError, TypeError):
                    pass
                raise
            if process.returncode:
                raise RuntimeError('Native geometry worker failed: ' + err[-4000:])
            return self._register_candidates(json.loads(out))
        if self.native_source is not None or room_identifier is not None:
            return self._register_candidates(self.native.candidates(source_id, page, room_identifier))
        # Enumerate public source text only, never annotation or target selection.
        try:
            registration = json.loads((self.native.folder/'source-registration.json').read_text())
            entry = next(x for x in registration['sources'] if x['source_id'] == source_id)
            pdf = entry['sources']['pdf']
            if native.sha(self.root/pdf['path']) != pdf['sha256']:
                raise ValueError('Registered source hash changed')
            import pymupdf
            with pymupdf.open(self.root/pdf['path']) as doc:
                labels = sorted(native.pdf_labels(doc[page]))
        except (OSError, KeyError, StopIteration, ValueError, IndexError) as exc:
            return {'status': 'uncertain', 'candidates': [], 'quality_flags': [str(exc)]}
        outputs = [self.native.candidates(source_id, page, label) for label in labels]
        result = {'status': 'ready' if any(x['status'] == 'ready' for x in outputs) else 'uncertain',
                  'candidates': [c for x in outputs for c in x.get('candidates', [])],
                  'quality_flags': [], 'label_results': [{'identifier': label, 'status': out['status'], 'quality_flags': out.get('quality_flags', [])} for label, out in zip(labels, outputs)]}
        return self._register_candidates(result)

    def predict_room_polygons(self, view, timeout=120):
        if view['source_id'] != self.source_id or view['source_sha256'] != self.source_sha256 or view.get('page', 0) != self.page:
            raise ValueError('View source/page mismatch')
        return self._register_candidates(self.raster.predict(view, timeout=timeout))

    def register_length_evidence(self, evidence):
        """Register host-supplied visible dimension/scale evidence; areas cannot calibrate."""
        value = dict(evidence)
        if value.get('kind') not in ('visible_dimension', 'visible_scale_bar'):
            raise ValueError('Calibration requires length evidence, never a printed area')
        if (value.get('source_id'), value.get('source_sha256'), value.get('page')) != (self.source_id, self.source_sha256, self.page):
            raise ValueError('Evidence source/page mismatch')
        if not isinstance(value.get('description'), str) or not value['description'].strip():
            raise ValueError('Visible evidence description required')
        if re.search(r'(?i)(?:\b(?:m|cm|mm|ft|in)\s*(?:²|\^\s*2|2)(?!\w)|\b(?:sqm|sq\.?\s*(?:m|ft|in))\b)', value['description']):
            raise ValueError('Printed area cannot calibrate a length')
        ident = 'length-evidence-' + _digest(value)[:24]
        self.evidence[ident] = value
        return ident

    def calibrate(self, evidence_id, p0, p1, length, unit):
        evidence = self.evidence[evidence_id]
        if (evidence.get('p0'), evidence.get('p1'), evidence.get('length'), evidence.get('unit')) != (list(p0), list(p1), length, unit):
            raise ValueError('Endpoints and length must match the recorded evidence')
        factors = {'m': 1, 'cm': .01, 'mm': .001, 'ft': .3048, 'in': .0254}
        if unit not in factors or type(length) not in (int, float) or not math.isfinite(length) or length <= 0:
            raise ValueError('Positive finite length and supported unit required')
        for point in (p0, p1):
            if len(point) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in point) or not (0 <= point[0] < self.width and 0 <= point[1] < self.height):
                raise ValueError('Invalid page endpoint')
        distance = math.dist(p0, p1)
        if distance < 2:
            raise ValueError('Distinct endpoints required')
        value = {'source_id': self.source_id, 'source_sha256': self.source_sha256, 'page': self.page, 'evidence_id': evidence_id, 'evidence': evidence, 'p0': list(p0), 'p1': list(p1), 'length': length, 'unit': unit, 'kind': 'visible_length', 'metres_per_pixel': length*factors[unit]/distance}
        value['calibration_id'] = 'cal-' + _digest(value)[:24]
        self.calibrations[value['calibration_id']] = value
        return {'calibration': value}

    def measure_room(self, candidate_id, calibration_id):
        try:
            candidate, calibration = self.candidates[candidate_id], self.calibrations[calibration_id]
            if candidate.get('candidate_id') != candidate_id or calibration.get('calibration_id') != calibration_id:
                raise ValueError('Candidate or calibration identity mismatch')
            if candidate.get('calibration') and candidate['calibration']['calibration_id']!=calibration_id:
                raise ValueError('Use the native candidate\'s own source-bound calibration')
            provenance = candidate.get('provenance', {})
            if provenance.get('source_id') != self.source_id or provenance.get('page', 0) != self.page or provenance.get('source_sha256', self.source_sha256) != self.source_sha256:
                raise ValueError('Candidate source/page mismatch')
            if (calibration.get('source_id'), calibration.get('source_sha256'), calibration.get('page')) != (self.source_id, self.source_sha256, self.page):
                raise ValueError('Calibration source/page mismatch')
            if candidate.get('quality_flags'):
                raise ValueError('Candidate has quality flags: ' + ', '.join(candidate['quality_flags']))
            from stoa_room_area_specialists.adapter import valid_polygon
            polygon = valid_polygon(candidate['polygon'])
            for ring in [polygon.exterior, *polygon.interiors]:
                if any(not (0 <= x <= self.width and 0 <= y <= self.height) for x, y in ring.coords):
                    raise ValueError('Boundary outside registered page')
            scale = calibration['metres_per_pixel']
            if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
                raise ValueError('Invalid scale')
            is_raster = candidate['provenance'].get('backend') == 'raster2seq'
            return {'status': 'available', 'candidate_id': candidate_id, 'calibration_id': calibration_id, 'pixel_area': polygon.area, 'area_m2': polygon.area*scale**2, 'estimate': is_raster, 'limitations': ['Raster quantization, minimum-region cutoff, and no hole prediction'] if is_raster else []}
        except (KeyError, ValueError, TypeError) as exc:
            return {'status': 'unavailable', 'candidate_id': candidate_id, 'calibration_id': calibration_id, 'area_m2': None, 'reason': str(exc)}

    def close(self):
        self.raster.close()
