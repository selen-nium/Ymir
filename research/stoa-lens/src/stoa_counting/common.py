"""Shared paths, classes and the frozen group split for the floor-plan counting trials.

Datasets stay separate: every model is trained and scored per dataset. The split comes from the
Junction training export (datasets/junction-specialist-v1/original-split-manifest.json); images
and box labels come from the original CubiCasa5K and Swiss Dwellings releases, which carry
geometry the Junction archive does not.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOME = ROOT / 'data/counting-trials'
SOURCES = HOME / 'sources'
MANIFEST = ROOT / 'datasets/junction-specialist-v1/original-split-manifest.json'
TRAIN_JSONL = ROOT / 'datasets/junction-specialist-v1/train.jsonl'
PROTOCOL = 'stoa-counting-trials-v1'

SOURCE_PINS = {
    'cubicasa5k.zip': {'url': 'https://zenodo.org/records/2613548/files/cubicasa5k.zip', 'md5': '0ce0b203d1e3c125b51087b219bd23b9',
                       'license': 'CC BY-NC-SA 4.0', 'version': '1.0'},
    'swiss-dwellings-v3.0.0.zip': {'url': 'https://zenodo.org/records/7788422/files/swiss-dwellings-v3.0.0.zip',
                                   'md5': '3b7915ecd5bf8e492a3e78c598b74123', 'license': 'CC BY 4.0', 'version': '3.0.0'},
}

# Detector classes per dataset (symbol counts only; rooms/apartments are region tasks for later).
CLASSES = {
    'cubicasa': ['door', 'window', 'toilet', 'sink', 'bathtub', 'shower', 'sauna_bench'],
    'swiss': ['door', 'window', 'toilet', 'sink', 'bathtub', 'shower'],
    'floorplancad': ['door', 'window', 'toilet', 'sink', 'bathtub'],  # no shower class in FloorPlanCAD
    'pooled': ['door', 'window', 'toilet', 'sink', 'bathtub', 'shower'],  # run 2 (first design): union training set
}

# Run 2, expanded to every Junction counting class our three datasets can label.
# Symbol detectors work on 640 px tiles; room detectors on the whole page (rooms are large).
SYMBOLS = ['door', 'window', 'toilet', 'sink', 'bathtub', 'shower', 'sauna_bench', 'stairs', 'column', 'wall']
ROOMS = ['bedroom', 'room', 'kitchen', 'bathroom', 'wc_room', 'sauna', 'balcony', 'loggia', 'living_room', 'corridor',
         'storage', 'stairwell', 'other_room', 'apartment', 'floor']
CLASSES.update({
    'cubicasa_s': ['door', 'window', 'toilet', 'sink', 'bathtub', 'shower', 'sauna_bench', 'wall'],
    'swiss_s': ['door', 'window', 'toilet', 'sink', 'bathtub', 'shower', 'stairs', 'column', 'wall'],
    'floorplancad_s': ['door', 'window', 'toilet', 'sink', 'bathtub', 'stairs'],
    'pooled_s': SYMBOLS,
    'cubicasa_r': ['bedroom', 'room', 'kitchen', 'bathroom', 'wc_room', 'sauna', 'balcony', 'living_room', 'corridor',
                   'storage', 'other_room', 'floor'],
    'swiss_r': ['bedroom', 'room', 'kitchen', 'bathroom', 'balcony', 'loggia', 'living_room', 'corridor', 'storage',
                'stairwell', 'other_room', 'apartment'],
    'pooled_r': ROOMS,
    # Outside tests (no box labels, scored against Junction answers): IFC floor plans, Opening Design sheet.
    'ifc_s': SYMBOLS, 'ifc_r': ROOMS, 'od_s': SYMBOLS,
})

# Junction counting families -> detector classes they sum over (per dataset).
FAMILIES = {
    'cubicasa': {'doors': ['door'], 'windows': ['window'], 'toilets': ['toilet'], 'sinks': ['sink'],
                 'showers_and_bathtubs': ['shower', 'bathtub']},
    'swiss': {'doors': ['door'], 'windows': ['window'], 'toilets': ['toilet'], 'washbasins': ['sink'],
              'bathtubs': ['bathtub'], 'showers': ['shower']},
    'floorplancad': {'doors': ['door'], 'windows': ['window'], 'toilets': ['toilet'], 'sinks': ['sink'],
                     'bathtubs': ['bathtub']},
}
_INDOOR = ['bedroom', 'room', 'kitchen', 'bathroom', 'wc_room', 'sauna', 'living_room', 'corridor', 'storage', 'stairwell', 'other_room']
FAMILIES.update({
    'cubicasa_s': {**FAMILIES['cubicasa'], 'sauna_benches': ['sauna_bench'], 'walls': ['wall'],
                   'sanitary_fixtures': ['toilet', 'sink', 'bathtub', 'shower']},
    'swiss_s': {**FAMILIES['swiss'], 'stairs': ['stairs'], 'columns': ['column'], 'walls': ['wall'],
                'sanitary_fixtures': ['toilet', 'sink', 'bathtub', 'shower']},
    'floorplancad_s': {**FAMILIES['floorplancad'], 'stairs': ['stairs'], 'sanitary_fixtures': ['toilet', 'sink', 'bathtub']},
    'cubicasa_r': {'bedrooms': ['bedroom'], 'kitchens': ['kitchen'], 'bathrooms': ['bathroom'], 'wc_rooms': ['wc_room'],
                   'saunas': ['sauna'], 'balconies': ['balcony'], 'floors': ['floor'],
                   'all_rooms': [c for c in _INDOOR if c not in ('loggia', 'stairwell')] + ['balcony']},
    'swiss_r': {'bedrooms': ['bedroom'], 'rooms_labelled_room': ['room'], 'kitchens': ['kitchen'], 'bathrooms': ['bathroom'],
                'balconies': ['balcony'], 'loggias': ['loggia'], 'living_rooms': ['living_room'], 'corridors': ['corridor'],
                'storerooms': ['storage'], 'stairwells': ['stairwell'], 'apartments': ['apartment'],
                'all_rooms': [c for c in _INDOOR if c not in ('wc_room', 'sauna')] + ['balcony', 'loggia']},
})

# COCO-pretrained detectors only know these of our classes (COCO 'toilet' and 'sink').
COCO_NAMES = {'toilet': 'toilet', 'sink': 'sink'}

# Text queries for the open-vocabulary base model (OWLv2), one or more prompts per class.
OWL_QUERIES = {
    'door': ['a door symbol with a swing arc on an architectural floor plan', 'door'],
    'window': ['a window symbol in a wall on an architectural floor plan', 'window'],
    'toilet': ['a toilet symbol on an architectural floor plan', 'toilet'],
    'sink': ['a sink or washbasin symbol on an architectural floor plan', 'sink'],
    'bathtub': ['a bathtub symbol on an architectural floor plan', 'bathtub'],
    'shower': ['a shower tray symbol on an architectural floor plan', 'shower'],
    'sauna_bench': ['a sauna bench symbol on an architectural floor plan', 'sauna bench'],
    'stairs': ['a staircase symbol on an architectural floor plan', 'stairs'],
    'column': ['a structural column symbol on an architectural floor plan', 'column'],
    'wall': ['a wall segment on an architectural floor plan', 'wall'],
    'bedroom': ['a bedroom on a floor plan', 'bedroom'], 'room': ['a room on a floor plan', 'room'],
    'kitchen': ['a kitchen on a floor plan', 'kitchen'], 'bathroom': ['a bathroom on a floor plan', 'bathroom'],
    'wc_room': ['a separate toilet room on a floor plan', 'WC room'], 'sauna': ['a sauna room on a floor plan', 'sauna'],
    'balcony': ['a balcony on a floor plan', 'balcony'], 'loggia': ['a loggia on a floor plan', 'loggia'],
    'living_room': ['a living room on a floor plan', 'living room'], 'corridor': ['a corridor or hallway on a floor plan', 'corridor'],
    'storage': ['a storage room on a floor plan', 'storage room'], 'stairwell': ['a stairwell on a floor plan', 'stairwell'],
    'other_room': ['a utility room on a floor plan', 'room'], 'apartment': ['an apartment unit on a floor plan', 'apartment'],
    'floor': ['a single storey floor plan drawing', 'floor plan'],
}


def sha(path, algo='sha256'):
    h = hashlib.new(algo)
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def dump(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    with os.fdopen(fd, 'w') as f:
        json.dump(obj, f, indent=1, sort_keys=False)
    os.replace(tmp, path)


def load(path):
    return json.loads(Path(path).read_text())


def split_assignments():
    """Junction manifest -> {('cubicasa', '<subset>/<id>') or ('swiss', (site, bldg, floor)): (split, group)}."""
    m = load(MANIFEST)['all_source_assignments']
    out = {}
    for key, v in m.items():
        name = Path(key).stem
        if key.startswith('data/cubicasa5k/'):
            subset, num = name.rsplit('-', 1)
            out[('cubicasa', f'{subset}/{num}')] = (v['split'], v['group_id'])
        elif key.startswith('data/swiss_dwellings/'):
            site, bldg, floor = (int(p[len(t):]) for p, t in zip(name.split('_'), ('site', 'bldg', 'floor')))
            out[('swiss', (site, bldg, floor))] = (v['split'], v['group_id'])
    return out

# ---- Pipeline 3: pipeline 2's symbol detector with door types ---------------------------------------
# 'door' is split into door types; open doorways (a wall gap with no leaf) get their own class and are
# NOT counted as doors, matching Junction's rule ("open doorways without a door leaf do not count").
DOOR_TYPES = ['door_single', 'door_double', 'door_sliding', 'door_folding', 'door_rollup', 'door_revolving']
SYMBOLS3 = DOOR_TYPES + ['open_doorway', 'window', 'toilet', 'sink', 'bathtub', 'shower', 'sauna_bench', 'stairs', 'column', 'wall']
CLASSES.update({
    'cubicasa_d': ['door_single', 'door_double', 'door_sliding', 'door_folding', 'door_rollup', 'open_doorway',
                   'window', 'toilet', 'sink', 'bathtub', 'shower', 'sauna_bench', 'wall'],
    'swiss_d': ['door_single', 'window', 'toilet', 'sink', 'bathtub', 'shower', 'stairs', 'column', 'wall'],
    'floorplancad_d': DOOR_TYPES + ['window', 'toilet', 'sink', 'bathtub', 'stairs'],
    'pooled_d': SYMBOLS3, 'ifc_d': SYMBOLS3, 'od_d': SYMBOLS3,
})


def _door_families(key):
    present = [c for c in DOOR_TYPES if c in CLASSES[key]]
    fam = {'doors': present}
    fam.update({f"{c.split('_', 1)[1]}_doors": [c] for c in present if len(present) > 1})
    if 'open_doorway' in CLASSES[key]:
        fam['open_doorways'] = ['open_doorway']
        fam['doors_incl_open_doorways'] = present + ['open_doorway']
    return fam


for _k, _src in (('cubicasa_d', 'cubicasa_s'), ('swiss_d', 'swiss_s'), ('floorplancad_d', 'floorplancad_s')):
    FAMILIES[_k] = {**_door_families(_k), **{f: m for f, m in FAMILIES[_src].items() if f != 'doors'}}
OWL_QUERIES.update({
    'door_single': ['a single hinged door with a swing arc on a floor plan', 'door'],
    'door_double': ['a double door with two swing arcs on a floor plan', 'double door'],
    'door_sliding': ['a sliding door on a floor plan', 'sliding door'],
    'door_folding': ['a folding door on a floor plan', 'folding door'],
    'door_rollup': ['a roll-up garage door on a floor plan', 'garage door'],
    'door_revolving': ['a revolving door on a floor plan', 'revolving door'],
    'open_doorway': ['an open doorway without a door on a floor plan', 'doorway'],
})
