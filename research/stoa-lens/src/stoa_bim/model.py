"""Building model (the single source of truth that IFC / DXF / SVG / glTF are generated from).

Frame: metres, x right, y up, derived from the PNG pixel frame: x = px * m_per_px, y = -py * m_per_px.
So any element can be drawn back onto the PNG exactly (see to_px).

model = {
  'frame':   {'page', 'width', 'height', 'crop', 'm_per_px'},
  'storey':  {'name', 'elevation', 'height'},
  'walls':   [{'id', 'polygon': [[x, y], ...], 'holes': [...], 'z0', 'z1'}],
  'doors':   [{'id', 'polygon' (gap rectangle), 'leaf': [[x, y], [x, y]] | None, 'swing': [[x, y], ...] | None, 'z0', 'z1'}],
  'windows': [{'id', 'polygon', 'z0', 'z1'}],
  'fixtures':[{'id', 'polygon'}],
  'spaces':  [{'id', 'polygon', 'holes', 'number', 'name'}],
  'assumptions': {...}, 'source': str }
"""
import math

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

DEFAULTS = {'storey_height': 2.80, 'door_head': 2.10, 'window_sill': 0.90, 'window_head': 2.10, 'slab': 0.20}


def new_model(page, source):
    return {'frame': {'page': page['id'], 'width': page['width'], 'height': page['height'], 'crop': page['crop'],
                      'm_per_px': page['m_per_px']},
            'storey': {'name': 'Storey 0', 'elevation': 0.0, 'height': DEFAULTS['storey_height']},
            'walls': [], 'doors': [], 'windows': [], 'fixtures': [], 'spaces': [],
            'assumptions': {**DEFAULTS, 'note': 'Heights are defaults: a floor-plan PNG carries no elevations.'},
            'source': source}


def px_to_m(model, pts):
    k = model['frame']['m_per_px']
    return [[round(x * k, 4), round(-y * k, 4)] for x, y in pts]


def to_px(model, pts):
    k = model['frame']['m_per_px']
    return [[x / k, -y / k] for x, y in pts]


def poly(e):
    return Polygon(e['polygon'], e.get('holes') or [])


# ---------------------------------------------------------------- Swiss reference (scoring only)

def _ring(e):
    return [tuple(map(float, p)) for p in e.get_points('xy')]


def _clusters(geoms, tol):
    """Group geometries that touch within tol (union-find)."""
    n = len(geoms)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    buf = [g.buffer(tol) for g in geoms]
    for i in range(n):
        for j in range(i + 1, n):
            if buf[i].intersects(buf[j]):
                parent[find(i)] = find(j)
    out = {}
    for i in range(n):
        out.setdefault(find(i), []).append(i)
    return list(out.values())


def swiss_reference(page):
    """Junction's DXF for one Swiss floor → model, in the PNG-derived metre frame."""
    import ezdxf
    from stoa_semseg.swiss import PX_PER_PT, SRC
    stem = page['id'].split('/', 1)[1]
    s, tx, ty = page['transform_pt_per_m']
    k = page['m_per_px']
    # DXF metres -> PNG px -> model metres
    T = lambda x, y: (round((s * x + tx) * PX_PER_PT * k, 4), round(-(-s * y + ty) * PX_PER_PT * k, 4))
    msp = ezdxf.readfile(SRC / f'{stem}.dxf').modelspace()
    m = new_model(page, 'reference: Junction DXF')

    walls = []
    for e in msp.query('LWPOLYLINE[layer=="WALL"]'):
        r = _ring(e)
        if len(r) >= 3:
            p = Polygon([T(*q) for q in r]).buffer(0)
            if p.area > 1e-4:
                walls.append(p)
    seen = []
    for i, p in enumerate(walls):  # one IfcWall per DXF wall polygon (HATCH duplicates of the same outline are skipped)
        if p.geom_type != 'Polygon' or any(p.symmetric_difference(q).area < 1e-4 for q in seen):
            continue
        seen.append(p)
        if p.area > 1e-4:
            m['walls'].append({'id': f'W{i}', 'polygon': [list(c) for c in p.exterior.coords[:-1]],
                               'holes': [[list(c) for c in h.coords[:-1]] for h in p.interiors], 'z0': 0.0, 'z1': DEFAULTS['storey_height']})

    wins = [Polygon([T(*q) for q in _ring(e)]).buffer(0) for e in msp.query('LWPOLYLINE[layer=="WINDOW"]') if len(_ring(e)) >= 3]
    wins = [w for w in wins if w.area > 1e-5]
    for i, grp in enumerate(_clusters(wins, 0.01)):
        u = unary_union([wins[j] for j in grp]).minimum_rotated_rectangle
        m['windows'].append({'id': f'N{i}', 'polygon': [list(c) for c in u.exterior.coords[:-1]],
                             'plan': [[list(c) for c in wins[j].exterior.coords] for j in grp],
                             'z0': DEFAULTS['window_sill'], 'z1': DEFAULTS['window_head']})

    # doors: gap rectangle (closed polyline), leaf (2-point line), swing (arc)
    rects, lines, arcs = [], [], []
    for e in msp.query('*[layer=="DOOR"]'):
        if e.dxftype() == 'LWPOLYLINE':
            r = [T(*q) for q in _ring(e)]
            if len(r) >= 4:
                rects.append(Polygon(r).buffer(0))
            elif len(r) == 2:
                lines.append(LineString(r))
        elif e.dxftype() == 'ARC':
            c, rad = e.dxf.center, e.dxf.radius
            a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
            if a1 < a0:
                a1 += 2 * math.pi
            pts = [T(c.x + rad * math.cos(a), c.y + rad * math.sin(a)) for a in np.linspace(a0, a1, 16)]
            arcs.append(LineString(pts))
    rects = [r for r in rects if r.area > 1e-5]
    curves = {i: [] for i in range(len(rects))}
    for g in lines + arcs:  # every leaf and swing goes to the nearest gap rectangle
        if rects:
            j = min(range(len(rects)), key=lambda i: rects[i].distance(g))
            if rects[j].distance(g) < 0.3:
                curves[j].append([list(c) for c in g.coords])
    for i, r in enumerate(rects):
        m['doors'].append({'id': f'D{i}', 'polygon': [list(c) for c in r.minimum_rotated_rectangle.exterior.coords[:-1]],
                           'plan': curves[i], 'z0': 0.0, 'z1': DEFAULTS['door_head']})

    fx = [Polygon([T(*q) for q in _ring(e)]).buffer(0) for e in msp.query('LWPOLYLINE[layer=="FIXTURE"]') if len(_ring(e)) >= 3]
    for e in msp.query('CIRCLE[layer=="FIXTURE"]'):
        fx.append(Point(T(e.dxf.center.x, e.dxf.center.y)).buffer(e.dxf.radius * s * PX_PER_PT * k))
    for i, f in enumerate(f for f in fx if f.area > 1e-5):
        m['fixtures'].append({'id': f'F{i}', 'polygon': [list(c) for c in f.exterior.coords[:-1]]})

    m['doors_raw'], m['windows_raw'] = len(m['doors']), len(m['windows'])
    m['doors'], m['windows'] = dedupe(m['doors']), dedupe(m['windows'])
    for i, r in enumerate(page['rooms']):
        m['spaces'].append({'id': f'S{i}', 'polygon': px_to_m(m, r['polygon']), 'holes': [],
                            'number': r['number'], 'name': r.get('name')})
    return m


def dedupe(items, cover=0.6):
    """Drop openings that repeat one already kept (overlap / smaller area >= cover)."""
    kept = []
    for e in items:
        g = Polygon(e['polygon']).buffer(0)
        if any(g.intersection(k).area >= cover * min(g.area, k.area) for _, k in kept):
            continue
        kept.append((e, g))
    return [e for e, _ in kept]


def summary(m):
    return {'walls': len(m['walls']), 'doors': len(m['doors']), 'windows': len(m['windows']), 'stairs': len(m.get('stairs', [])),
            'fixtures': len(m['fixtures']), 'spaces': len(m['spaces']),
            'wall_area_m2': round(sum(poly(w).area for w in m['walls']), 2),
            'space_area_m2': round(sum(poly(s).area for s in m['spaces']), 2)}
