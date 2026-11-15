"""Read-only readiness audit for the proposed 16-class detector.

This does not certify exposure, completeness, source registration or a dataset
release. In particular, the six-class ``training_admitted`` flag cannot certify
new classes. Inputs and historical evidence are never modified.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path


CLASSES = ('door', 'window', 'column', 'staircase', 'elevator', 'toilet_bowl',
           'urinal', 'bathtub', 'shower', 'sink', 'appliance', 'bed', 'chair',
           'table', 'sofa', 'cabinet_wardrobe')

# These are proposed source correspondences, not completeness assertions.
# Reserved appliance spaces and elevator room regions are deliberately excluded.
CUBI = {
    'Door': 'door', 'Window': 'window', 'Stairs': 'staircase',
    'Toilet': 'toilet_bowl', 'Urinal': 'urinal', 'Bathtub': 'bathtub',
    'BathtubRound': 'bathtub', 'Jacuzzi': 'bathtub', 'Shower': 'shower',
    'ShowerCab': 'shower', 'ShowerPlatform': 'shower',
    **{x: 'sink' for x in ('Sink', 'CornerSink', 'DoubleSink', 'DoubleSinkRight', 'RoundSink', 'SideSink')},
    **{x: 'appliance' for x in ('ElectricalAppliance', 'WashingMachine', 'Dishwasher', 'IntegratedStove', 'GasStove')},
    **{x: 'cabinet_wardrobe' for x in ('BaseCabinet', 'BaseCabinetRound', 'BaseCabinetTriangle', 'Closet', 'ClosetRound', 'ClosetTriangle', 'CoatCloset', 'WallCabinet')},
}


def split_components(records, visual_edges=()):
    """Join family, exact-image and supplied visual edges transitively.

    Split conflicts quarantine the entire component. Edges must already be
    reviewed or conservatively treated as potential duplicates by the caller.
    """
    by_id = {r['id']: r for r in records}
    if len(by_id) != len(records):
        raise ValueError('Duplicate record ID')
    parent = {key: key for key in by_id}

    def find(key):
        while key != parent[key]:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def join(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    seen = {}
    for row in records:
        for key in (('family', row.get('family')), ('image', row.get('image_sha256'))):
            if not key[1]:
                continue
            if key in seen:
                join(row['id'], seen[key])
            else:
                seen[key] = row['id']
    for a, b in visual_edges:
        if a not in by_id or b not in by_id:
            raise ValueError('Visual edge refers to unknown record')
        join(a, b)
    groups = defaultdict(list)
    for row in records:
        groups[find(row['id'])].append(row)
    result = []
    for rows in groups.values():
        ids = sorted(r['id'] for r in rows)
        roles = sorted({r['split'] for r in rows if r.get('split')})
        # Compare inventories only for identical images and the same declared
        # complete class scope. Different annotations on different drawings in
        # a family are not evidence of an inventory conflict.
        exact = defaultdict(list)
        for row in rows:
            if row.get('image_sha256') and row.get('complete_inventory') is not None:
                if row.get('inventory_scope'):
                    exact[row['image_sha256']].append(row)
        conflicts = []
        for candidates in exact.values():
            for i, left in enumerate(candidates):
                for right in candidates[i+1:]:
                    common = set(left['inventory_scope']) & set(right['inventory_scope'])
                    for category in common:
                        inventories = [tuple(sorted(json.dumps(item, sort_keys=True, separators=(',', ':'))
                            for item in record['complete_inventory'] if item.get('class') == category))
                            for record in (left, right)]
                        if inventories[0] != inventories[1]:
                            conflicts.append(sorted((left['id'], right['id'])))
                            break
        result.append({'ids': ids, 'frozen_roles': roles,
                       'inventory_conflicts': sorted(conflicts),
                       'quarantined': len(roles) > 1 or bool(conflicts)})
    return sorted(result, key=lambda x: x['ids'])


def proposed_split(family):
    """Stable 80/10/10 assignment for families without frozen roles."""
    value = int.from_bytes(hashlib.sha256(('stoa-object-detector-v1:' + family).encode()).digest()[:8], 'big') / 2**64
    return 'train' if value < .8 else 'validation' if value < .9 else 'holdout'


def audit_native(path):
    totals = defaultdict(Counter)
    failures = defaultdict(Counter)
    failed_images = defaultdict(set)
    image_counts = Counter()
    skipped = Counter()
    with path.open() as source:
        for line in source:
            row = json.loads(line)
            split = row.get('split', 'unknown')
            legacy = row.get('legacy', {})
            if not row.get('admitted') or legacy.get('historically_exposed'):
                skipped['not_admitted_or_historically_exposed'] += 1
                continue
            if split not in ('train', 'validation') or legacy.get('split') != split:
                skipped['frozen_split_mismatch_or_protected'] += 1
                continue
            if split == 'train' and row.get('upstream_split') != 'train':
                skipped['upstream_protected'] += 1
                continue
            image_counts[split] += 1
            for ann in row.get('annotations', []):
                label = CUBI.get(ann['source_class'])
                if label:
                    totals[split][label] += 1
            for rejection in row.get('rejected', []):
                label = CUBI.get(rejection.get('source_class'))
                if label:
                    failures[split][label] += 1
                    failed_images[split].add(row['id'])
    support = {split: {name: totals[split][name] for name in CLASSES} for split in ('train', 'validation')}
    missing = {split: [name for name in CLASSES if not support[split][name]] for split in support}
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return {'version': 'expanded-native-audit-v1', 'source_path': str(path),
            'source_sha256': digest.hexdigest(),
            'classes': list(CLASSES), 'proposed_source_map': CUBI,
            'candidate_images': dict(image_counts), 'skipped': dict(skipped),
            'candidate_positive_instances': support,
            'expanded_target_extraction_failures': {s: dict(failures[s]) for s in support},
            'images_with_expanded_target_failures': {s: len(failed_images[s]) for s in support},
            'missing_candidate_support': missing,
            'ready_for_training': False,
            'unresolved': ['Candidate counts are not export admission or class completeness.',
                           'Expanded-class extraction failures require re-audit.',
                           'Exposure checks against both primary datasets and historical studies must be established.',
                           'Cross-source family/visual duplicate and frozen-role conflicts must be resolved.',
                           'Per-class complete-reference coverage and train/validation positive support are required.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('records', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite an existing audit')
    result = audit_native(args.records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
