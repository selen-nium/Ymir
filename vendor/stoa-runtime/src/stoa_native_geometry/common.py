"""Shared checks; source geometry is never repaired or inferred from labels."""
import hashlib
import json
import math
from pathlib import Path

from shapely.geometry import Polygon

VERSION = 'native-geometry-v2'
MAX_ROOMS = 2000
MAX_VERTICES = 100_000
UNITS = {'m': 1., 'cm': .01, 'mm': .001, 'ft': .3048, 'in': .0254}


class Unsupported(ValueError):
    pass


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def checked(polygon):
    if not isinstance(polygon, Polygon) or polygon.is_empty or not polygon.is_valid or not math.isfinite(polygon.area) or polygon.area <= 0:
        raise Unsupported('A unique valid room polygon with valid cutouts is required; no repair was applied.')
    if sum(len(r.coords) for r in [polygon.exterior, *polygon.interiors]) > MAX_VERTICES:
        raise Unsupported('Room boundary exceeds the vertex limit.')
    if not all(math.isfinite(v) for r in [polygon.exterior, *polygon.interiors] for p in r.coords for v in p):
        raise Unsupported('Room coordinates must be finite.')
    return polygon


def record(polygon):
    return {'exterior': [list(p) for p in polygon.exterior.coords][:-1],
            'holes': [[list(p) for p in ring.coords][:-1] for ring in polygon.interiors]}
