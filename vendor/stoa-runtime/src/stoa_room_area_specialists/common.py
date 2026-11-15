"""Small durable artifact and identity helpers shared by the coordinator."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOME = ROOT / 'data/room-area-specialists'
OUT = ROOT / 'reports/room-area-specialists'
PREFIX = 'stoa-room-area-specialists-v1|'
OLD_PREFIX = 'stoa-room-area-pilot-v1|'


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def put(path, value):
    """Atomic, fsynced replacement; raw response journals survive interruption."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with temp.open('w') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def obj(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


STR = {'type': 'string'}
NUM = {'type': 'number'}
POINT = dict(type='array', items=NUM, minItems=2, maxItems=2)
SCHEMA = obj(dict(
    status={'type': 'string', 'enum': ['answered', 'needs_review']},
    answer=STR, value={'type': ['number', 'null']}, unit={'type': 'string', 'enum': ['m2']},
    candidate_id={'type': ['string', 'null']}, calibration_id={'type': ['string', 'null']},
    printed_area_m2={'type': ['number', 'null']}, calibration_evidence=STR, boundary_evidence=STR,
    uncertainties={'type': 'array', 'items': STR}))


def tool(name, description, properties):
    return dict(type='function', function=dict(name=name, description=description, parameters=obj(properties)))


CALIBRATE = tool('calibrate', 'Register an independently visible LENGTH, never a room area. '
    'Endpoints [x,y] are ORIGINAL FULL PAGE PIXELS. Cite the visible dimension or scale bar.',
    dict(p0=POINT, p1=POINT, length=NUM, unit={'type': 'string', 'enum': ['m','cm','mm','ft','in']}, evidence=STR))
RASTER = tool('predict_room_polygons', 'Run fixed pretrained Raster2Seq on a registered view_id. '
    'At most TWO predictions; the second must use another registered view. No edits or joins.', dict(view_id=STR))
NATIVE = tool('native_room_candidates', 'Extract source-native room outlines using the public room identifier '
    'and verified units/registration. Page is zero based. Explicit uncertainty means abstain.',
    dict(source_id=STR, page={'type': 'integer', 'minimum': 0}, room_identifier=STR))


def system_prompt(condition):
    condition_text = (
        'Use predict_room_polygons(view_id). You must independently calibrate from visible raster length evidence; '
        'native geometry and native calibration are unavailable. You may predict twice, on different registered views. '
        'The 256-pixel model uses integer vertices and discards polygons below 100 model-pixel squared; '
        'small rooms and clipped predictions can be unreliable.' if condition == 'raster' else
        'Use native_room_candidates(source_id,page,room_identifier). Native receipts verify PDF scale, DXF units '
        'and registration. Use the returned calibration_id for native geometry. Inspect the overlay to establish room identity.')
    return ('Measure the requested room interior floor area using only supplied drawing pixels and host tools. '
        'Printed room areas are a separate cross-check, never a source of calibration or geometry selection. '
        'Use VISTA inspect/read_pixels to resolve labels and boundaries. inspect region is {x,y,width,height} '
        'in ORIGINAL FULL PAGE PIXELS, turn=0. Each inspected image has view_id and exact transform back to the original. '
        'calibrate endpoints are also ORIGINAL PIXELS. Source page is zero based. Include room recesses and exclude walls '
        'and adjacent rooms; furniture and door symbols do not subtract floor. No polygon editing, joining or repair. '
        'Choose only returned valid candidates; clipped or unresolved candidates require needs_review. '
        'Final candidate_id and calibration_id directly bind the measurement; no select_room call exists. '
        'The host computes area with Shapely and preserves your stated value separately. '
        'You have eight model calls, at most three tools per response and a hard ten-minute trajectory deadline. '
        'Inspect candidate overlays before answering. If scale, identity or boundary is unresolved, '
        'return needs_review with value,candidate_id,calibration_id=null. '+condition_text+
        '\nReturn JSON only matching this schema: '+json.dumps(SCHEMA))
