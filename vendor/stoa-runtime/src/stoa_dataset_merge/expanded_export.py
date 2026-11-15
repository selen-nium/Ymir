"""Fail-closed portable export of independently admitted detector references.

This exporter consumes an explicit, reviewed admission document; it does not
infer label completeness, project identity or exposure from candidate counts.
No bundled model outputs or proposals are accepted as reference labels.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import shutil

from PIL import Image

from .expanded_audit import CLASSES, proposed_split, split_components

STATES = {'unknown', 'partial', 'complete', 'verified_absent'}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _checked_file(item):
    path = Path(item['path'])
    if not path.is_file() or sha(path) != item['sha256']:
        raise ValueError(f'Missing or altered input: {path}')
    return path


def validate(admission):
    """Return per-split counts or fail. Admission is not itself a proof audit."""
    if admission.get('version') != 'expanded-admission-v1':
        raise ValueError('Unknown admission version')
    evidence = admission.get('evidence', {})
    for name in ('aecv_exposure', 'aec_exposure', 'historical_exposure', 'visual_duplicates', 'class_policy'):
        item = evidence.get(name, {})
        if item.get('result') not in ('verified_clear', 'reviewed_clear'):
            raise ValueError(f'Missing verified evidence: {name}')
        _checked_file(item)
    rows = admission.get('records', [])
    if not rows:
        raise ValueError('Empty release')
    groups = split_components(rows, admission.get('visual_edges', []))
    if any(group['quarantined'] for group in groups):
        raise ValueError('Conflicting frozen roles or complete inventories')
    by_id = {row['id']: row for row in rows}
    for group in groups:
        members = [by_id[ident] for ident in group['ids']]
        if not any(row.get('frozen_split') for row in members):
            expected = proposed_split(min(row['family'] for row in members))
            if any(row['split'] != expected for row in members):
                raise ValueError('New family has a non-deterministic split')
    support = {split: Counter() for split in ('train', 'validation')}
    evaluable_support = Counter()
    for row in rows:
        for field in ('id', 'family', 'source', 'source_sha256', 'project', 'license', 'transform', 'coverage_evidence'):
            if not row.get(field):
                raise ValueError(f'Missing {field}')
        if row.get('admission') not in ('upstream_reference', 'reviewed_reference') or row.get('protected') is not False or row.get('exposure') != 'clear':
            raise ValueError(f"Source is not admissible: {row['id']}")
        if row.get('split') not in support:
            raise ValueError('Only train and validation are exportable')
        if row.get('frozen_split') and row['frozen_split'] != row['split']:
            raise ValueError('Frozen split changed')
        _checked_file(row['coverage_evidence'])
        image_path = _checked_file({'path': row['image'], 'sha256': row['image_sha256']})
        _checked_file(row['source_file'])
        _checked_file(row['license_file'])
        if row['source_sha256'] != row['source_file']['sha256']:
            raise ValueError('Source hash mismatch')
        with Image.open(image_path) as image:
            if list(image.size) != [row['width'], row['height']]:
                raise ValueError('Image dimensions differ')
            image.verify()
        coverage = row.get('coverage', [])
        if len(coverage) != len(CLASSES) or any(state not in STATES for state in coverage):
            raise ValueError('Invalid class coverage')
        if row.get('coverage_authority') not in ('upstream_reference', 'human_reviewed'):
            raise ValueError('Coverage requires a reference or human review')
        for box in row.get('annotations', []):
            category = box['category_id']
            coords = box['bbox_xyxy']
            if not isinstance(category, int) or isinstance(category, bool) or not 0 <= category < len(CLASSES):
                raise ValueError('Invalid category')
            if len(coords) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in coords):
                raise ValueError('Invalid box')
            x0, y0, x1, y1 = coords
            if not (0 <= x0 < x1 <= row['width'] and 0 <= y0 < y1 <= row['height']):
                raise ValueError('Box outside native image')
            if coverage[category] == 'verified_absent':
                raise ValueError('Positive in verified-absent class')
            if box.get('reference_kind') not in ('upstream_reference', 'human_reviewed'):
                raise ValueError('Unreviewed or model-generated annotation')
            support[row['split']][CLASSES[category]] += 1
            if row['split'] == 'validation' and coverage[category] == 'complete':
                evaluable_support[CLASSES[category]] += 1
    missing = {split: [name for name in CLASSES if not counts[name]] for split, counts in support.items()}
    if any(missing.values()):
        raise ValueError(f'Missing positive support: {missing}')
    missing_eval = [name for name in CLASSES if not evaluable_support[name]]
    if missing_eval:
        raise ValueError(f'Missing complete-reference validation support: {missing_eval}')
    return {split: dict(counts) for split, counts in support.items()}


def export(admission, destination):
    """Export a new release, preserving evidence and never overwriting files."""
    counts = validate(admission)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {'version': 'expanded-detector-v1', 'classes': list(CLASSES),
                'coordinate_convention': 'native pixels; COCO xywh', 'support': counts,
                'records': [], 'visual_edges': admission.get('visual_edges', []), 'evidence': {}, 'checksums': {}}
    for key in ('aecv_exposure', 'aec_exposure', 'historical_exposure', 'visual_duplicates', 'class_policy'):
        item = admission['evidence'][key]
        src = _checked_file(item)
        out = destination / 'evidence' / f'{key}{src.suffix}'
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out)
        manifest['evidence'][key] = {'path': str(out.relative_to(destination)), 'sha256': sha(out), 'result': item['result']}
    for split, dirname in (('train', 'train'), ('validation', 'valid')):
        out = destination / dirname
        out.mkdir()
        coco = {'images': [], 'annotations': [], 'categories': [{'id': i, 'name': name} for i, name in enumerate(CLASSES)]}
        coverage = {}
        for row in sorted((r for r in admission['records'] if r['split'] == split), key=lambda r: r['id']):
            ident = len(coco['images']) + 1
            src = Path(row['image'])
            filename = f'{ident:06d}{src.suffix.lower()}'
            shutil.copyfile(src, out / filename)
            coco['images'].append({'id': ident, 'file_name': filename, 'width': row['width'], 'height': row['height']})
            coverage[str(ident)] = row['coverage']
            for box in row.get('annotations', []):
                x0, y0, x1, y1 = box['bbox_xyxy']
                coco['annotations'].append({'id': len(coco['annotations']) + 1, 'image_id': ident,
                    'category_id': box['category_id'], 'bbox': [x0, y0, x1-x0, y1-y0],
                    'area': (x1-x0)*(y1-y0), 'iscrowd': 0, 'reference_kind': box['reference_kind']})
            meta = {k: row[k] for k in ('id', 'source', 'source_sha256', 'family', 'project', 'license', 'transform', 'coverage_evidence', 'coverage_authority', 'admission', 'exposure', 'protected')}
            meta['frozen_split'] = row.get('frozen_split')
            if row.get('complete_inventory') is not None:
                meta['complete_inventory'] = row['complete_inventory']
                meta['inventory_scope'] = row.get('inventory_scope', [])
            # Include coverage evidence and licences as portable copies.
            for field in ('coverage_evidence', 'license_file'):
                source = _checked_file(row[field])
                dest = destination / 'provenance' / f'{field}-{row[field]["sha256"]}{source.suffix}'
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.copyfile(source, dest)
                meta[field] = {'path': str(dest.relative_to(destination)), 'sha256': sha(dest)}
            meta.update(split=split, coco_image_id=ident, image=f'{dirname}/{filename}', image_sha256=row['image_sha256'])
            manifest['records'].append(meta)
        for name, data in (('_annotations.coco.json', coco), ('coverage.json', coverage)):
            (out / name).write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    for path in sorted(destination.rglob('*')):
        if path.is_file():
            manifest['checksums'][str(path.relative_to(destination))] = sha(path)
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False) + '\n')
    return manifest


def verify_release(root):
    """Verify release integrity and reference support; does not re-audit evidence."""
    root = Path(root).resolve()
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest.get('version') != 'expanded-detector-v1' or manifest.get('classes') != list(CLASSES):
        raise ValueError('Unrecognised release contract')
    actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p.name != 'manifest.json'}
    checksums = manifest.get('checksums', {})
    if actual != set(checksums):
        raise ValueError('Release file inventory differs')
    for rel, expected in checksums.items():
        path = root / rel
        if path.is_symlink() or not path.resolve().is_relative_to(root) or sha(path) != expected:
            raise ValueError(f'Release checksum mismatch: {rel}')
    for key in ('aecv_exposure', 'aec_exposure', 'historical_exposure', 'visual_duplicates', 'class_policy'):
        item = manifest.get('evidence', {}).get(key, {})
        if item.get('result') not in ('verified_clear', 'reviewed_clear') or checksums.get(item.get('path')) != item.get('sha256'):
            raise ValueError(f'Missing evidence: {key}')
    records = manifest['records']
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('Duplicate source record')
    groups = split_components(records, manifest.get('visual_edges', []))
    if any(group['quarantined'] for group in groups):
        raise ValueError('Duplicate component crosses splits')
    by_id = {row['id']: row for row in records}
    for group in groups:
        members = [by_id[ident] for ident in group['ids']]
        if not any(row['frozen_split'] for row in members) and any(
            row['split'] != proposed_split(min(r['family'] for r in members)) for row in members
        ):
            raise ValueError('New family has a non-deterministic split')
    for row in records:
        if row['admission'] not in ('upstream_reference', 'reviewed_reference') or row['exposure'] != 'clear' or row['protected'] is not False:
            raise ValueError('Inadmissible source')
        if row['coverage_authority'] not in ('upstream_reference', 'human_reviewed'):
            raise ValueError('Invalid coverage authority')
        if row['frozen_split'] and row['frozen_split'] != row['split']:
            raise ValueError('Frozen split mismatch')
        for key in ('coverage_evidence', 'license_file'):
            item = row[key]
            if checksums.get(item['path']) != item['sha256']:
                raise ValueError('Missing provenance file')
        if checksums.get(row['image']) != row['image_sha256']:
            raise ValueError('Missing image')
    support = {s: Counter() for s in ('train', 'validation')}
    evaluable = Counter()
    for split, dirname in (('train', 'train'), ('validation', 'valid')):
        coco = json.loads((root / dirname / '_annotations.coco.json').read_text())
        coverage = json.loads((root / dirname / 'coverage.json').read_text())
        if coco['categories'] != [{'id': i, 'name': name} for i, name in enumerate(CLASSES)]:
            raise ValueError('Class map differs')
        images = {item['id']: item for item in coco['images']}
        matching = {(r['coco_image_id'], r['image']) for r in records if r['split'] == split}
        if len(images) != len(coco['images']) or matching != {(i, f'{dirname}/{im["file_name"]}') for i, im in images.items()} or set(coverage) != {str(i) for i in images}:
            raise ValueError('Image and coverage identities differ')
        for im in images.values():
            with Image.open(root / dirname / im['file_name']) as image:
                if list(image.size) != [im['width'], im['height']]:
                    raise ValueError('Image dimensions differ')
                image.verify()
        for ident, states in coverage.items():
            if len(states) != len(CLASSES) or any(s not in STATES for s in states):
                raise ValueError('Invalid coverage')
        if len({ann['id'] for ann in coco['annotations']}) != len(coco['annotations']):
            raise ValueError('Duplicate annotation ID')
        for ann in coco['annotations']:
            ident, category = ann['image_id'], ann['category_id']
            if ident not in images or not isinstance(category, int) or isinstance(category, bool) or not 0 <= category < len(CLASSES):
                raise ValueError('Invalid annotation identity')
            x, y, w, h = ann['bbox']
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (x, y, w, h)) or not (0 <= x < x+w <= images[ident]['width'] and 0 <= y < y+h <= images[ident]['height']):
                raise ValueError('Invalid native box')
            if ann.get('reference_kind') not in ('upstream_reference', 'human_reviewed') or coverage[str(ident)][category] == 'verified_absent':
                raise ValueError('Invalid reference')
            support[split][CLASSES[category]] += 1
            if split == 'validation' and coverage[str(ident)][category] == 'complete':
                evaluable[CLASSES[category]] += 1
    if any(not support[s][c] for s in support for c in CLASSES) or any(not evaluable[c] for c in CLASSES):
        raise ValueError('Insufficient training or complete-reference validation support')
    if {s: dict(counts) for s, counts in support.items()} != manifest['support']:
        raise ValueError('Support counts differ from manifest')
    return {'manifest_sha256': sha(root / 'manifest.json'), 'support': manifest['support'], 'evidence_reaudited': False}
