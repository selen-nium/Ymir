"""Read positioned text without exposing offline reference annotations to the model."""
import hashlib
import json
import math
import os
import tempfile
import importlib.metadata
import platform
import sys
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def model_hashes(config):
    """Require actual model files and their expected hashes; never download implicitly."""
    models = config.get('models', {})
    if set(models) != {'det', 'rec', 'cls'}:
        raise ValueError('OCR requires pinned det, rec and cls model files')
    result = {}
    for name, item in models.items():
        actual = sha(item['path'])
        if actual != item['sha256']:
            raise ValueError(f'OCR {name} model hash mismatch')
        result[name] = actual
    return result



def environment_identity(config, runner=None):
    """Fingerprint the entire installed distribution inventory and relevant code/config."""
    distributions = sorted((d.metadata.get('Name','').lower(), d.version) for d in importlib.metadata.distributions())
    inventory_hash = hashlib.sha256(json.dumps(distributions,separators=(',',':')).encode()).hexdigest()
    versions = {name: importlib.metadata.version(name) for name in ('rapidocr','onnxruntime') if any(x[0] == name for x in distributions)}
    if runner is None:
        for key,name in (('engine_version','rapidocr'),('onnxruntime_version','onnxruntime')):
            if name not in versions: raise ModuleNotFoundError(name)
            if config.get(key) and config[key] != versions[name]:
                raise ValueError(f'OCR {name} version mismatch: expected {config[key]}, got {versions[name]}')
        import rapidocr
        package_root=Path(rapidocr.__file__).parent
        default_config=package_root/'config.yaml'
        config_hash=sha(default_config)
        package_files={str(p.relative_to(package_root)):sha(p) for p in sorted(package_root.rglob('*'))
                       if p.is_file() and p.suffix in ('.py','.yaml','.yml')}
        package_hash=hashlib.sha256(json.dumps(package_files,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        import onnxruntime
        binary_root=Path(onnxruntime.__file__).parent/'capi'
        binaries={p.name:sha(p) for p in sorted(binary_root.glob('*.so'))}
        if not binaries: raise ValueError('ONNX Runtime native binary unavailable')
        runner_id='rapidocr'
    else:
        # Test/injected transports must never share production cache entries.
        code=getattr(runner,'__code__',None)
        runner_id='injected:'+getattr(runner,'__module__','')+'.'+getattr(runner,'__qualname__',type(runner).__qualname__)
        if code is not None: runner_id+=':'+hashlib.sha256(code.co_code+repr(code.co_consts).encode()).hexdigest()
        config_hash=None; package_hash=None; binaries={}
    return {'inventory_sha256':inventory_hash, 'distribution_count':len(distributions), 'versions':versions,
            'python':sys.version, 'platform':platform.platform(), 'runner':runner_id,
            'rapidocr_default_config_sha256':config_hash, 'rapidocr_package_code_sha256':package_hash,
            'onnxruntime_native_binaries_sha256':binaries, 'reader_sha256':sha(__file__)}


def inventory():
    """Complete installed distribution inventory for a reproducibility receipt."""
    return [{'name':n,'version':v} for n,v in sorted((d.metadata.get('Name','').lower(), d.version) for d in importlib.metadata.distributions())]


def _content_hash(lines):
    return hashlib.sha256(json.dumps(lines,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def affine(transform):
    if isinstance(transform, (list, tuple)):
        if len(transform) != 6: raise ValueError('affine needs six numbers')
        return list(map(float, transform))
    if 'affine' in transform:
        return affine(transform['affine'])
    return [float(transform['scale_x']), 0., 0., float(transform['scale_y']),
            float(transform['origin_x']), float(transform['origin_y'])]


def point(x, y, a):
    return (a[0]*x + a[2]*y + a[4], a[1]*x + a[3]*y + a[5])


def mapped_box(box, a):
    x0,y0,x1,y1 = box
    pts = [point(x,y,a) for x,y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1))]
    return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]


def identifier(view, source, text, box):
    payload = json.dumps([view['source_sha256'], view.get('page'), source, text, box], ensure_ascii=False, separators=(',', ':'))
    return 'text-' + hashlib.sha256(payload.encode()).hexdigest()[:20]


def _record(view, source, text, box, score):
    if not text.strip() or len(box) != 4 or not all(math.isfinite(float(v)) for v in box) or box[0] >= box[2] or box[1] >= box[3]:
        return None
    box = [float(x) for x in box]
    return {'id': identifier(view, source, text, box), 'text': text, 'box': box,
            'source': source, 'confidence': score, 'source_sha256': view['source_sha256'],
            'view_id': view['view_id'], 'page': view.get('page')}


def _native(view):
    if not view.get('pdf_path') or not view.get('pdf_to_page') or not view.get('pdf_sha256'):
        return []
    if sha(view['pdf_path']) != view['pdf_sha256']:
        raise ValueError('PDF source hash mismatch')
    import pymupdf
    # MuPDF reports unrotated text coordinates. The supplied matrix must bind those
    # coordinates to the registered native raster; it includes crop/rotation if needed.
    transform = affine(view['pdf_to_page'])
    bounds = mapped_box([0, 0, view['width'], view['height']], affine(view['transform']))
    lines = []
    with pymupdf.open(view['pdf_path']) as doc:
        for block in doc[int(view.get('page', 0))].get_text('dict')['blocks']:
            for line in block.get('lines', []):
                text = ''.join(span['text'] for span in line.get('spans', []))
                box = mapped_box(line['bbox'], transform)
                if box[2] <= bounds[0] or box[0] >= bounds[2] or box[3] <= bounds[1] or box[1] >= bounds[3]: continue
                row = _record(view, 'native_pdf', text, box, None)
                if row: lines.append(row)
    return lines


def _rapidocr(view, config, runner=None):
    hashes = model_hashes(config)
    if runner is None:
        import numpy as np
        from PIL import Image
        from rapidocr import RapidOCR
        models = config['models']
        engine = RapidOCR(params={'Global.log_level': 'error', 'Det.model_path': models['det']['path'],
                                  'Rec.model_path': models['rec']['path'], 'Cls.model_path': models['cls']['path']})
        result = engine(np.asarray(Image.open(view['path']).convert('RGB')))
        raw = [] if result is None or result.txts is None else list(zip(result.txts, result.boxes, result.scores))
    else:
        raw = runner(view['path'], config)
    result = []
    for text, polygon, score in raw:
        pts = [point(float(p[0]), float(p[1]), affine(view['transform'])) for p in polygon]
        box = [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]
        confidence = float(score)
        if not math.isfinite(confidence) or not 0 <= confidence <= 1: raise ValueError('invalid OCR confidence')
        item = _record(view, 'rapidocr', str(text), box, confidence)
        if item: result.append(item)
    return result, hashes


def read_text(view, method='auto', cursor=0, ocr_enabled=True, cache_dir=None, ocr_config=None, ocr_runner=None):
    """Page through at most 100 lines. Explicit OCR may bypass an unsuitable PDF layer."""
    if method not in ('auto', 'native', 'ocr'): raise ValueError('unknown text method')
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0: raise ValueError('invalid cursor')
    native = _native(view) if method != 'ocr' else []
    source = 'native_pdf' if native else None
    lines = native
    hashes = None
    status = 'ok'
    if not native and method != 'native':
        if not ocr_enabled:
            status = 'ocr_disabled'
        elif not ocr_config:
            status = 'ocr_unavailable_unpinned_models'
        else:
            hashes = model_hashes(ocr_config)
            try:
                environment = environment_identity(ocr_config, ocr_runner)
            except (ImportError, ModuleNotFoundError) as exc:
                return {'status':'ocr_unavailable_dependency', 'reason':str(exc), 'lines':[], 'next_cursor':None, 'remaining':0, 'total':0, 'model_hashes':hashes}
            # Versioned key prevents reuse of old entries without environment identity.
            key_data = {'source': view['source_sha256'], 'page': view.get('page'), 'view': view['view_id'],
                        'image': sha(view['path']), 'transform': view['transform'], 'models': hashes,
                        'config': ocr_config, 'arm': 'ocr_enabled', 'cache_version':2, 'environment':environment}
            key = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
            cache = Path(cache_dir) / (key + '.json') if cache_dir else None
            if cache and cache.exists():
                saved=json.loads(cache.read_text())
                if saved.get('key_data') != key_data or saved.get('lines_sha256') != _content_hash(saved.get('lines')):
                    raise ValueError('OCR cache integrity failure')
                lines=saved['lines']
            else:
                try:
                    lines, hashes = _rapidocr(view, ocr_config, ocr_runner)
                except (ImportError, ModuleNotFoundError) as exc:
                    return {'status':'ocr_unavailable_dependency', 'reason':str(exc), 'lines':[], 'next_cursor':None,
                            'remaining':0, 'total':0, 'model_hashes':hashes}
                if cache:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    fd, name = tempfile.mkstemp(dir=cache.parent, prefix=key + '.', suffix='.tmp')
                    try:
                        with os.fdopen(fd, 'w') as stream:
                            json.dump({'key_data':key_data, 'lines':lines, 'lines_sha256':_content_hash(lines)}, stream, ensure_ascii=False)
                        os.replace(name, cache)
                    finally:
                        if os.path.exists(name): os.unlink(name)
            source = 'rapidocr'
    elif not native:
        status = 'native_text_unavailable'
    if cursor > len(lines): raise ValueError('cursor beyond text coverage')
    end = min(cursor+100, len(lines))
    return {'status':status, 'source':source, 'lines':lines[cursor:end], 'next_cursor':end if end < len(lines) else None,
            'remaining':len(lines)-end, 'total':len(lines), 'model_hashes':hashes}
