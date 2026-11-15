"""Reference-free PDF/DXF room extraction for the exposed specialist diagnostic.

Only public source identities and room labels enter this module. It never opens
numeric annotations, solver trajectories, or prior area measurements. Geometries
are rejected rather than repaired. Coordinates returned to the adapter are the
original 300-DPI pixel coordinates, using the actual render matrix (not the
rounded pixmap width divided by the PDF page width).
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import sys
import zipfile

RENDER_SCALE = 300 / 72
MAX_RESIDUAL_PT = .25
MAX_RELATIVE_AGREEMENT = .001
VERSION = 'native-room-specialist-v1'


class NativeUncertainty(ValueError):
    """The available source does not establish a unique valid measurement."""


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def put(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    if path.exists() and path.read_text() != payload:
        prior = path.read_bytes()
        history = path.parent / 'history' / (path.stem + '-' + hashlib.sha256(prior).hexdigest() + path.suffix)
        history.parent.mkdir(parents=True, exist_ok=True)
        if not history.exists():
            history.write_bytes(prior)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(payload)
    temp.replace(path)


def polygon_record(poly, factor=1.):
    return {'exterior': [[float(x) * factor, float(y) * factor] for x, y in poly.exterior.coords],
            'holes': [[[float(x) * factor, float(y) * factor] for x, y in ring.coords] for ring in poly.interiors]}


def checked_polygon(exterior, holes=()):
    from shapely.geometry import Polygon
    p = Polygon(exterior, holes)
    if p.is_empty or not p.is_valid or p.area <= 0:
        raise NativeUncertainty('Invalid polygon topology; no geometry repair performed')
    if not all(math.isfinite(v) for v in p.bounds):
        raise NativeUncertainty('Nonfinite polygon coordinates')
    return p


def _same(a, b, tol=1e-5):
    return math.dist(a, b) <= tol


def pdf_path_polygon(path):
    """Parse represented linear rings, including explicit closure with false flag.

    White and near-white fills are deliberately retained. Curves cannot be
    approximated. Multiple rings require unambiguous even-odd or opposite-winding
    hole representation; unrelated nested drawing objects are never subtracted.
    """
    if path.get('fill') is None:
        raise NativeUncertainty('Unfilled drawing is not an explicit room region')
    rings, current = [], []
    for item in path.get('items', []):
        kind = item[0]
        if kind == 're':
            if current:
                rings.append(current); current = []
            r = item[1]
            pts = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
            if len(item) > 2 and item[2] < 0:
                pts.reverse()
            rings.append(pts + [pts[0]])
        elif kind == 'l':
            a, b = tuple(item[1]), tuple(item[2])
            if current and not _same(current[-1], a):
                rings.append(current); current = []
            if not current:
                current = [a]
            current.append(b)
        else:
            raise NativeUncertainty('Unsupported curve or path operator in room outline')
    if current:
        rings.append(current)
    if not rings:
        raise NativeUncertainty('No linear outline')
    closed = []
    for ring in rings:
        if not _same(ring[0], ring[-1]):
            if not path.get('closePath'):
                raise NativeUncertainty('Open path without explicit endpoint closure')
            ring = ring + [ring[0]]
        closed.append(checked_polygon(ring))
    closed.sort(key=lambda p: p.area, reverse=True)
    outer, holes = closed[0], closed[1:]
    for i, hole in enumerate(holes):
        if not outer.contains(hole):
            raise NativeUncertainty('Multiple disconnected or intersecting rings')
        if any(hole.intersects(other) for other in holes[:i]):
            raise NativeUncertainty('Ambiguous nested or intersecting hole topology')
        if not path.get('even_odd') and outer.exterior.is_ccw == hole.exterior.is_ccw:
            raise NativeUncertainty('Ambiguous same-winding hole topology')
    return checked_polygon(outer.exterior.coords, [p.exterior.coords for p in holes])


def similarity_registration(source, target):
    """Least-squares isotropic similarity, explicitly permitting axis reflection."""
    import numpy as np
    x, y = np.asarray(source, dtype=float), np.asarray(target, dtype=float)
    if x.shape != y.shape or x.ndim != 2 or x.shape[1] != 2 or len(x) < 3:
        raise NativeUncertainty('At least three paired 2D registration witnesses required')
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise NativeUncertainty('Nonfinite registration witnesses')
    xc, yc = x - x.mean(axis=0), y - y.mean(axis=0)
    if np.linalg.matrix_rank(xc) < 2:
        raise NativeUncertainty('Degenerate registration witnesses')
    u, singular, vt = np.linalg.svd(xc.T @ yc)
    rotation = u @ vt  # Row-vector convention; determinant may be -1.
    scale = float(singular.sum() / (xc * xc).sum())
    translation = y.mean(axis=0) - scale * x.mean(axis=0) @ rotation
    prediction = scale * x @ rotation + translation
    residual = np.linalg.norm(prediction - y, axis=1)
    matrix = np.eye(3)
    matrix[:2, :2] = (scale * rotation).T
    matrix[:2, 2] = translation
    return {'matrix': matrix.tolist(), 'scale_points_per_native_unit': scale,
            'axis_reflection': bool(np.linalg.det(rotation) < 0),
            'maximum_residual_points': float(residual.max()),
            'rms_residual_points': float(np.sqrt((residual ** 2).mean())),
            'witness_vertices': len(x)}


def transform_points(points, registration):
    import numpy as np
    a = np.asarray(points, dtype=float)
    m = np.asarray(registration['matrix'])
    return (a @ m[:2, :2].T + m[:2, 2]).tolist()


def verify_units(insunits, registration, points_per_metre, pdf_area, dxf_area):
    # DXF header is a hypothesis, independently checked against the visible bar.
    factors = {1: .0254, 2: .3048, 4: .001, 5: .01, 6: 1., 7: 1000.}
    if insunits not in factors:
        raise NativeUncertainty('Unresolved or unsupported DXF units')
    factor = factors[insunits]
    scale = registration['scale_points_per_native_unit']
    scale_error = abs(scale / (points_per_metre * factor) - 1)
    area_error = abs(pdf_area / points_per_metre ** 2 / (dxf_area * factor ** 2) - 1)
    if registration['maximum_residual_points'] > MAX_RESIDUAL_PT:
        raise NativeUncertainty('Registration residual exceeds 0.25 PDF points')
    if scale_error > MAX_RELATIVE_AGREEMENT or area_error > MAX_RELATIVE_AGREEMENT:
        raise NativeUncertainty('DXF units disagree with PDF scale/area by more than 0.1%')
    return {'dxf_insunits': int(insunits), 'metres_per_native_unit': factor,
            'scale_relative_disagreement': scale_error, 'area_relative_disagreement': area_error}


def pdf_scale_bar(page):
    """Read the 0,1,2,5 metre bar from text and its three adjacent rectangles."""
    words = page.get_text('words')
    rectangles = []
    for path in page.get_drawings():
        if path.get('fill') is None:
            continue
        for item in path['items']:
            if item[0] == 're':
                r = item[1]
                if 1 <= r.height <= 10 and r.width > r.height:
                    rectangles.append(r)
    matches = []
    for zero in [w for w in words if w[4] == '0']:
        labels = [zero]
        for value in ('1', '2', '5'):
            choices = [w for w in words if w[4] == value and abs(w[1] - zero[1]) < 1 and w[0] > zero[0]]
            if len(choices) != 1:
                break
            labels.append(choices[0])
        if len(labels) != 4:
            continue
        centers = [(w[0] + w[2]) / 2 for w in labels]
        if not all(centers[i] < centers[i + 1] for i in range(3)):
            continue
        unit = [w for w in words if w[4] == 'm' and 0 < w[0] - centers[-1] < 25 and abs(w[1] - zero[1]) < 20]
        if not unit:
            continue
        segments = []
        for i in range(3):
            rs = [r for r in rectangles if abs(r.x0 - centers[i]) < 2 and abs(r.x1 - centers[i + 1]) < 2 and -1 <= r.y0 - zero[3] <= 20]
            if len(rs) != 1:
                break
            segments.append(rs[0])
        if len(segments) != 3 or max(r.y0 for r in segments) - min(r.y0 for r in segments) > .05:
            continue
        if any(abs(segments[i].x1 - segments[i + 1].x0) > .05 for i in range(2)):
            continue
        lengths = [r.width / metres for r, metres in zip(segments, (1, 1, 3))]
        ppm = sum(lengths) / 3
        if max(abs(x / ppm - 1) for x in lengths) > MAX_RELATIVE_AGREEMENT:
            continue
        matches.append({'points_per_metre': (segments[-1].x1 - segments[0].x0) / 5,
                        'p0_pdf_points': [segments[0].x0, segments[0].y0],
                        'p1_pdf_points': [segments[-1].x1, segments[-1].y0],
                        'length_m': 5., 'evidence': 'Visible 0, 1, 2, 5 m labels and adjacent 1/1/3 metre bar rectangles',
                        'segment_points_per_metre': lengths})
    if len(matches) != 1:
        raise NativeUncertainty('Missing or ambiguous independently evidenced metre scale bar')
    return matches[0]


def pdf_labels(page):
    labels = {}
    for word in page.get_text('words'):
        if re.fullmatch(r'\d+\.\d{2}', word[4]):
            labels.setdefault(word[4], []).append(((word[0] + word[2]) / 2, (word[1] + word[3]) / 2))
    return labels


def dxf_geometry(doc):
    from shapely.geometry import Point
    labels, outlines, rejected = {}, [], []
    for e in doc.modelspace():
        if e.dxftype() == 'TEXT' and e.dxf.layer == 'ROOM_TEXT' and re.fullmatch(r'\d+\.\d{2}', e.dxf.text):
            labels.setdefault(e.dxf.text, []).append((float(e.dxf.insert.x), float(e.dxf.insert.y)))
        if e.dxf.layer != 'ROOM_OUTLINE':
            continue  # Door, fixture, window, wall and unit graphics never subtract floor.
        try:
            if e.dxftype() != 'LWPOLYLINE':
                raise NativeUncertainty('Unsupported room-outline entity or curve')
            pts = list(e.get_points('xyb'))
            if len(pts) < 3:
                # A two-point closed outline is a represented zero-area separator,
                # not a room polygon. Preserve this fact without treating as floor.
                rejected.append({'reason': 'Degenerate room-outline separator', 'vertices': len(pts)})
                continue
            if any(abs(float(p[2])) > 0 for p in pts):
                raise NativeUncertainty('Unsupported curved DXF room outline')
            xy = [(float(p[0]), float(p[1])) for p in pts]
            if not e.closed and not _same(xy[0], xy[-1]):
                raise NativeUncertainty('Open DXF room outline')
            outlines.append(checked_polygon(xy))
        except NativeUncertainty as exc:
            rejected.append({'reason': str(exc), 'bounds': list(e.bbox()) if hasattr(e, 'bbox') else None})
    return labels, outlines, rejected


def unique_containing(polygons, point, name):
    from shapely.geometry import Point
    contained = [p for p in polygons if p.contains(Point(point))]
    if not contained:
        raise NativeUncertainty(f'No {name} outline contains public room label')
    contained.sort(key=lambda p: p.area)
    if len(contained) > 1:
        # Distinct nested room outlines are ambiguous; a full-page background is
        # filtered by the caller. We do not choose a region by expected area.
        raise NativeUncertainty(f'Ambiguous nested {name} outlines around room label')
    return contained[0]


def paired_vertices(dxf_poly, pdf_poly, initial):
    """Choose cyclic vertex association using independently paired room labels."""
    import numpy as np
    source = list(dxf_poly.exterior.coords)[:-1]
    target = list(pdf_poly.exterior.coords)[:-1]
    if len(source) != len(target):
        raise NativeUncertainty('PDF/DXF outline vertices differ; no silent simplification')
    prediction = np.asarray(transform_points(source, initial))
    possible = []
    for orientation in (target, list(reversed(target))):
        for shift in range(len(target)):
            candidate = orientation[shift:] + orientation[:shift]
            error = float(np.linalg.norm(prediction - np.asarray(candidate), axis=1).max())
            possible.append((error, candidate))
    possible.sort(key=lambda x: x[0])
    if len(possible) > 1 and abs(possible[0][0] - possible[1][0]) < 1e-6:
        raise NativeUncertainty('Ambiguous polygon vertex registration')
    return source, possible[0][1]


class NativeBackend:
    def __init__(self, root=None, folder=None):
        self.root = Path(root or Path(__file__).resolve().parents[2]).resolve()
        self.folder = Path(folder or self.root / 'data/room-area-specialists/native').resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.rows = json.loads((self.root / 'data/room-area-pilot/public.json').read_text())
        self.sources = {r['id']: r for r in self.rows if r['id'].startswith('swiss-')}
        self._cache = {}

    def setup(self):
        """Explicit local source registration; copies only four verified DXF/PDFs."""
        archive_path = self.root / 'data/junction-dataset/junction-dataset.zip'
        records = []
        with zipfile.ZipFile(archive_path) as archive:
            for ident, row in self.sources.items():
                stem = Path(row['image']).stem
                entries = {}
                for suffix in ('.pdf', '.dxf'):
                    member = 'junction-dataset/data/swiss_dwellings/' + stem + suffix
                    raw = archive.read(member)
                    path = self.folder / 'sources' / (stem + suffix)
                    expected_hash = hashlib.sha256(raw).hexdigest()
                    if path.exists() and sha(path) != expected_hash:
                        raise NativeUncertainty('Previously registered source changed')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if not path.exists():
                        path.write_bytes(raw)
                    entries[suffix[1:]] = {'path': str(path.relative_to(self.root)), 'archive_member': member, 'sha256': expected_hash}
                original_pdf = (self.root / row['image']).with_suffix('.pdf')
                if sha(original_pdf) != entries['pdf']['sha256']:
                    raise NativeUncertainty('PDF differs from exposed source')
                if sha(self.root / row['image']) != row['image_sha256']:
                    raise NativeUncertainty('Exposed original raster changed')
                records.append({'source_id': ident, 'project_identity': stem, 'sources': entries,
                                'image_sha256': row['image_sha256'], 'original_render_matrix': [RENDER_SCALE, 0, 0, RENDER_SCALE, 0, 0],
                                'exposure': row['exposure'], 'group_id': row['group_id']})
        receipt = {'version': VERSION, 'archive_path': str(archive_path.relative_to(self.root)),
                   'archive_sha256': sha(archive_path), 'sources': records,
                   'licence': 'Swiss Dwellings v3.0.0, Archilyse AG, DOI 10.5281/zenodo.7788422, CC BY 4.0; Junction rendered geometry and synthetic labels',
                   'environment': {'python': sys.version, **{n: importlib.metadata.version(n) for n in ('numpy', 'Shapely', 'ezdxf', 'PyMuPDF', 'Pillow')}}}
        put(self.folder / 'source-registration.json', receipt)
        return receipt

    def _extract(self, source_id, page, room_identifier):
        import ezdxf
        import pymupdf
        from shapely.geometry import Point
        if source_id not in self.sources:
            return {'status': 'unavailable', 'candidates': [], 'quality_flags': ['raster_only_no_verified_native_source']}
        if page != 0:
            raise NativeUncertainty('Only verified original page 0 is registered')
        if not re.fullmatch(r'\d+\.\d{2}', room_identifier):
            raise NativeUncertainty('Exact public room identifier required')
        row = self.sources[source_id]
        registration_path = self.folder / 'source-registration.json'
        if not registration_path.exists():
            raise NativeUncertainty('Run explicit native setup before inference')
        manifest = json.loads(registration_path.read_text())
        source = next(r for r in manifest['sources'] if r['source_id'] == source_id)
        for item in source['sources'].values():
            if sha(self.root / item['path']) != item['sha256']:
                raise NativeUncertainty('Registered native source hash changed')
        if sha(self.root / row['image']) != row['image_sha256']:
            raise NativeUncertainty('Registered original raster hash changed')
        pdf = pymupdf.open(self.root / source['sources']['pdf']['path'])
        p = pdf[page]
        if p.rotation != 0 or p.cropbox != p.mediabox:
            raise NativeUncertainty('Unverified rotated or cropped PDF render transform')
        pix = p.get_pixmap(dpi=300)
        if pix.width != row['width'] or pix.height != row['height']:
            raise NativeUncertainty('Original raster dimensions do not match verified render matrix')
        dl, outlines, dxf_rejections = dxf_geometry(ezdxf.readfile(self.root / source['sources']['dxf']['path']))
        pl = pdf_labels(p)
        if len(dl.get(room_identifier, [])) != 1 or len(pl.get(room_identifier, [])) != 1:
            raise NativeUncertainty('Missing or ambiguous room identifier in PDF/DXF')
        filled, pdf_rejections = [], []
        for drawing in p.get_drawings():
            if drawing.get('fill') is None:
                continue
            try:
                poly = pdf_path_polygon(drawing)
                # Full-page paper background is outside the drawing model.
                if poly.area < .9 * p.rect.get_area():
                    filled.append(poly)
            except NativeUncertainty as exc:
                pdf_rejections.append({'reason': str(exc), 'bbox_pdf_points': list(drawing['rect'])})
        common = sorted(k for k in dl.keys() & pl.keys() if len(dl[k]) == len(pl[k]) == 1)
        if len(common) < 3:
            raise NativeUncertainty('Insufficient unambiguous public labels for registration')
        initial = similarity_registration([dl[k][0] for k in common], [pl[k][0] for k in common])
        x, y, matches = [], [], []
        target_dxf = target_pdf = None
        for label in common:
            try:
                dp = unique_containing(outlines, dl[label][0], 'DXF room')
                pp = unique_containing(filled, pl[label][0], 'PDF filled room')
                a, b = paired_vertices(dp, pp, initial)
                x.extend(a); y.extend(b); matches.append(label)
                if label == room_identifier:
                    target_dxf, target_pdf = dp, pp
            except NativeUncertainty:
                if label == room_identifier:
                    raise
        if target_dxf is None:
            raise NativeUncertainty('Target room has no unique matched native region')
        for other in outlines:
            if other is target_dxf:
                continue
            if target_dxf.contains(other):
                raise NativeUncertainty('Ambiguous separate nested DXF room outline; hole ownership unresolved')
        # Invalid/curved source outlines intersecting the target label or outline
        # cannot be certified by discarding them. Current verified sources contain
        # only degenerate separators among rejected native ROOM_OUTLINE entities.
        if any(r['reason'] != 'Degenerate room-outline separator' for r in dxf_rejections):
            raise NativeUncertainty('Unsupported or invalid native room outline present; source needs review')
        # Unselected hatch/fixture paths may have bounding boxes covering a
        # room label. Their graphics do not redefine an independently matched
        # room outline. Every selected PDF region is itself a valid linear
        # polygon and must match the native room geometry below.
        registration = similarity_registration(x, y)
        registration['matched_public_room_identifiers'] = matches
        if registration['maximum_residual_points'] > MAX_RESIDUAL_PT:
            raise NativeUncertainty('Registration residual exceeds 0.25 PDF points')
        bar = pdf_scale_bar(p)
        doc = ezdxf.readfile(self.root / source['sources']['dxf']['path'])
        units = verify_units(int(doc.header.get('$INSUNITS', 0)), registration, bar['points_per_metre'], target_pdf.area, target_dxf.area)
        if len(target_pdf.interiors) != len(target_dxf.interiors):
            raise NativeUncertainty('PDF/DXF represented hole topology disagrees')
        calibration = {'metres_per_pixel': 1 / (bar['points_per_metre'] * RENDER_SCALE),
                       'kind': 'native_verified_pdf_scale_and_dxf_units',
                       'scale_bar': bar, 'verified_units': units, 'registration': registration,
                       'p0': [v * RENDER_SCALE for v in bar['p0_pdf_points']],
                       'p1': [v * RENDER_SCALE for v in bar['p1_pdf_points']],
                       'length': bar['length_m'], 'unit': 'm'}
        calibration['calibration_id'] = 'native-cal-' + digest({'source': source, 'calibration': calibration})[:20]
        provenance = {'backend': VERSION, 'source_id': source_id, 'page': page, 'room_identifier': room_identifier,
                      'sources': source['sources'], 'source_registration_sha256': sha(registration_path),
                      'project_identity': source['project_identity'], 'group_id': row['group_id'],
                      'exposure': row['exposure'], 'association': 'exact public room labels and polygon containment; global polygon vertex similarity registration',
                      'coordinate_system': 'original_full_page_pixels', 'pdf_points_to_pixels': RENDER_SCALE,
                      'fixture_policy': 'Only represented room outlines; door and fixture graphics do not subtract floor',
                      'native_polygon': polygon_record(target_dxf),
                      'pdf_polygon_points': polygon_record(target_pdf)}
        polygon = polygon_record(target_pdf, RENDER_SCALE)
        candidate = {'candidate_id': 'native-' + digest({'polygon': polygon, 'provenance': provenance})[:24],
                     'polygon': polygon, 'calibration': calibration, 'provenance': provenance,
                     'quality_flags': [],
                     'verification_flags': ['verified_native_units', 'verified_similarity_registration', 'white_fills_retained',
                                            'no_geometry_repair', 'numeric_reference_not_used'],
                     'native_area_m2': target_dxf.area * units['metres_per_native_unit'] ** 2,
                     'pdf_scale_area_m2': target_pdf.area / bar['points_per_metre'] ** 2}
        from PIL import Image, ImageDraw
        overlay_path = self.folder / 'overlays' / (candidate['candidate_id'] + '.png')
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(self.root / row['image']) as image:
            image = image.convert('RGB')
            draw = ImageDraw.Draw(image)
            draw.line([tuple(x) for x in polygon['exterior']], fill='#00aacc', width=8)
            for hole in polygon['holes']:
                draw.line([tuple(x) for x in hole], fill='#ee6600', width=8)
            draw.line([tuple(calibration['p0']), tuple(calibration['p1'])], fill='#cc0000', width=6)
            draw.text(tuple(polygon['exterior'][0]), room_identifier, fill='#0022cc', stroke_width=1)
            image.save(overlay_path)
        candidate['overlay'] = str(overlay_path.relative_to(self.root))
        candidate['overlay_sha256'] = sha(overlay_path)
        return {'status': 'ready', 'candidates': [candidate], 'quality_flags': [],
                'rejected_source_geometry': {'pdf': pdf_rejections, 'dxf': dxf_rejections}}

    def candidates(self, source_id, page, room_identifier):
        try:
            # Reverify source hashes on every call; cached outputs never bypass
            # registration integrity and never contain model context or answers.
            result = self._extract(source_id, page, room_identifier)
        except NativeUncertainty as exc:
            result = {'status': 'uncertain', 'candidates': [], 'quality_flags': [str(exc)]}
        key = digest({'source_id': source_id, 'page': page, 'room_identifier': room_identifier})
        put(self.folder / 'queries' / (key + '.json'), result)
        return result

    def readiness(self):
        rows = []
        for ident, row in self.sources.items():
            room = re.search(r'room (\d+\.\d{2})', row['question']).group(1)
            result = self.candidates(ident, 0, room)
            rows.append({'source_id': ident, 'room_identifier': room, 'ready': result['status'] == 'ready',
                         'status': result['status'], 'quality_flags': result['quality_flags'],
                         'candidate_ids': [c['candidate_id'] for c in result['candidates']]})
        report = {'backend': VERSION, 'ready': bool(rows) and all(r['ready'] for r in rows),
                  'sources': rows, 'raster_only_unavailable': [r['id'] for r in self.rows if r['id'] not in self.sources],
                  'tolerances': {'registration_max_pdf_points': MAX_RESIDUAL_PT, 'scale_area_relative_agreement': MAX_RELATIVE_AGREEMENT}}
        put(self.folder / 'readiness.json', report)
        return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['setup', 'readiness', 'candidates'])
    parser.add_argument('--source-id')
    parser.add_argument('--room-identifier')
    parser.add_argument('--page', type=int, default=0)
    args = parser.parse_args()
    backend = NativeBackend()
    if args.action == 'setup':
        result = backend.setup()
    elif args.action == 'readiness':
        result = backend.readiness()
    else:
        result = backend.candidates(args.source_id, args.page, args.room_identifier)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
