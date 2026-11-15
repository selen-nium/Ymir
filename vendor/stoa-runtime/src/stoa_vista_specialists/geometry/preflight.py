"""Bounded, unpaid native geometry lifecycle preflight in the active Python environment."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from .lifecycle import terminate_and_collect


def native_preflight(root, source, timeout=120):
    """Run real enumeration in a disposable child; return explicit failure/timeout.

    The child uses this interpreter and the caller's installed dependencies. Its
    temporary geometry artifacts are created inside root/data and removed.
    """
    root = Path(root).resolve()
    if timeout <= 0:
        return {'ready': False, 'status': 'timeout', 'reason': 'Nonpositive preflight deadline'}
    env = dict(os.environ)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2]) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    started = time.monotonic()
    (root/'data').mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='vista-native-preflight-', dir=root/'data') as folder:
        child = subprocess.Popen([sys.executable, '-m', 'stoa_vista_specialists.geometry.preflight', '--child', str(root), folder], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
        try:
            stdout, stderr = child.communicate(json.dumps(source), timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            cleanup = terminate_and_collect(child, exc)
            return {'ready': False, 'status': 'timeout', 'reason': 'Native lifecycle preflight deadline exceeded', 'elapsed_seconds': time.monotonic()-started, **cleanup}
        except BaseException as exc:
            terminate_and_collect(child, exc)
            raise
    if child.returncode:
        return {'ready': False, 'status': 'failed', 'reason': stderr[-4000:] or 'Native preflight child failed', 'elapsed_seconds': time.monotonic()-started}
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return {'ready': False, 'status': 'failed', 'reason': 'Invalid native preflight child output', 'elapsed_seconds': time.monotonic()-started}
    result['elapsed_seconds'] = time.monotonic()-started
    return result


def raster_preflight(root, view, timeout=120):
    """Exercise actual pinned inference and cleanup under the shared GPU lock."""
    root = Path(root).resolve()
    if timeout <= 0:
        return {'ready': False, 'status': 'timeout', 'reason': 'Nonpositive preflight deadline'}
    env = dict(os.environ)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2]) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    started = time.monotonic()
    env['STOA_RASTER_PREFLIGHT_PGROUP'] = '1'
    with tempfile.TemporaryDirectory(prefix='vista-raster-preflight-', dir=root/'data') as folder:
        child = subprocess.Popen([sys.executable, '-m', 'stoa_vista_specialists.geometry.preflight', '--raster-child', str(root), folder], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
        try:
            stdout, stderr = child.communicate(json.dumps({'view': view, 'timeout': timeout}), timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            cleanup = terminate_and_collect(child, exc)
            return {'ready': False, 'status': 'timeout', 'reason': 'Raster lifecycle preflight deadline exceeded', 'elapsed_seconds':time.monotonic()-started, **cleanup}
        except BaseException as exc:
            terminate_and_collect(child, exc)
            raise
    if child.returncode:
        return {'ready':False,'status':'failed','reason':stderr[-4000:] or 'Raster preflight child failed','elapsed_seconds':time.monotonic()-started}
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return {'ready':False,'status':'failed','reason':'Invalid raster preflight child output','elapsed_seconds':time.monotonic()-started}
    result['elapsed_seconds'] = time.monotonic()-started
    return result


def _raster_child(root, request, folder):
    from .tools import IsolatedRasterBackend
    from stoa_room_area_specialists.raster import digest
    view = request['view']
    if digest(view['path']) != view.get('image_sha256'):
        raise ValueError('Raster preflight view image hash mismatch')
    backend = IsolatedRasterBackend(root, Path(folder)/'raster')
    try:
        prediction = backend.predict(view, timeout=request['timeout'])
        return {'ready':True,'status':'ready','view_id':view['view_id'],'source_sha256':view['source_sha256'],'prediction_key':prediction['prediction_key'],'candidate_count':len(prediction['candidates']), 'candidate_flags':[c.get('quality_flags',[]) for c in prediction['candidates']]}
    finally:
        backend.close()


def _child(root, source, folder):
    from .tools import GeometryTools, verified_pdf_view_fields
    fields = {'status':'ready','kind':'registered_ifc_svg_preview'} if source.get('native_geometry') else verified_pdf_view_fields(root, source)
    if fields['status'] != 'ready':
        return {'ready': False, 'status': fields['status'], 'reason': fields.get('reason'), 'pdf_registration': fields}
    # No archived registration, cache, source or result is changed.
    geo = GeometryTools(root, folder, source['source_id'], source['source_sha256'], source.get('page',0), source['width'], source['height'],native_source=source if source.get('native_geometry') else None)
    try:
        result = geo.native_room_candidates(source['source_id'], source.get('page',0))
        measurements = []
        for candidate in result.get('candidates',[]):
            ident = candidate['candidate_id']; calid = (candidate.get('calibration') or {}).get('calibration_id')
            measured = geo.measure_room(ident, calid)
            expected = candidate['native_area_m2']
            valid = measured['status'] == 'available' and expected is not None and math.isclose(measured['area_m2'], expected, rel_tol=.001, abs_tol=1e-8)
            measurements.append({'candidate_id':ident, 'calibration_id':calid, 'valid':valid})
        missing = bool(measurements) and geo.measure_room(measurements[0]['candidate_id'], 'missing')['status'] == 'unavailable'
        ready = result.get('status')=='ready' and bool(measurements) and all(m['valid'] for m in measurements) and missing
        return {'ready':ready,'status':'ready' if ready else 'failed','source_id':source['source_id'], 'source_sha256':source['source_sha256'], 'pdf_registration':fields,'label_count':len(result.get('label_results',[])),'candidate_count':len(measurements),'measurements':measurements,'missing_scale_unavailable':missing}
    finally:
        geo.close()


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] not in ('--child', '--raster-child'):
        raise SystemExit('Internal child mode only')
    handler = _child if sys.argv[1] == '--child' else _raster_child
    print(json.dumps(handler(Path(sys.argv[2]), json.load(sys.stdin), sys.argv[3]), allow_nan=False))
