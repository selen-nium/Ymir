"""L1 room graph: the canonical, editable structure of one page (PLAN §5.2–5.4).

A graph is plain JSON in the page's pixel frame:
    {"frame": {"id", "image", "width", "height", "crop", "m_per_px", "scale_source"},
     "rooms": [{"id", "polygon", "type", "number", "name", "source", "confidence", "history"}],
     "text": [...], "candidates": [...], "iteration": k, "disagreement": float, "log": [...]}

`heal` makes every room a closed, valid, single polygon with no overlaps (PLAN §5.3). Rooms it cannot fix are
dropped from `rooms` and kept in `log` (and flagged by C8). The scorer reads graphs through `to_prediction_row`,
so the semseg scorer is reused unchanged.
"""
import copy

import numpy as np
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

MIN_AREA = 50.0      # px²; smaller pieces are not rooms
PINHOLE = 400.0      # px²; holes smaller than this (text, symbols) are filled
SLIVER = 1.0         # px; opening radius that removes parts thinner than 2 px


def new_graph(page, dataset):
    return {'frame': {'id': page['id'], 'dataset': dataset, 'width': page['width'], 'height': page['height'],
                      'crop': page['crop'], 'm_per_px': page.get('m_per_px'),
                      'scale_source': 'source file (true scale)' if page.get('m_per_px') else None},
            'rooms': [], 'text': [], 'candidates': [], 'iteration': 0, 'disagreement': None, 'log': []}


def from_prediction_row(page, row, dataset, thr, source):
    g = new_graph(page, dataset)
    for k, inst in enumerate(i for i in (row or {}).get('instances', []) if i['score'] >= thr):
        polys = [Polygon(r) for r in inst['polygons'] if len(r) >= 3]
        if not polys:
            continue
        g['rooms'].append({'id': f'r{k}', 'polygon': None, 'parts': [list(p.exterior.coords)[:-1] for p in polys],
                           'type': inst.get('class', -1), 'number': None, 'name': None, 'source': source,
                           'confidence': float(inst['score']), 'history': ['generator@0']})
    return heal(g)


def geom(room):
    return Polygon(room['polygon'])


def _clean(g):
    """Valid polygon parts of a geometry, holes below PINHOLE filled, slivers removed."""
    g = make_valid(g) if not g.is_valid else g
    if g.is_empty:
        return []
    g = g.buffer(-SLIVER, join_style=2).buffer(SLIVER, join_style=2)  # mitred opening keeps rectilinear corners
    parts = []
    for p in getattr(g, 'geoms', [g]):
        if p.geom_type != 'Polygon' or p.area < MIN_AREA:
            continue
        holes = [h for h in p.interiors if Polygon(h).area >= PINHOLE]
        p = Polygon(p.exterior, holes)
        p = p.simplify(0.25, preserve_topology=True)
        if p.is_valid and p.area >= MIN_AREA:
            parts.append(p)
    return parts


def heal(g):
    """Closed, valid, single-part, non-overlapping rooms. Higher confidence wins overlaps; extra fragments become
    candidates (they may be a missed room); unfixable rooms are logged."""
    g = copy.deepcopy(g)
    rooms = sorted(g['rooms'], key=lambda r: -r['confidence'])
    taken = None
    healed = []
    for r in rooms:
        raw = r.pop('parts', None)
        src = unary_union([Polygon(p).buffer(0) for p in raw]) if raw else Polygon(r['polygon'])
        parts = _clean(src)
        if taken is not None and parts:
            parts = _clean(unary_union(parts).difference(taken))
        if not parts:
            g['log'].append({'event': 'dropped_unfixable', 'room': r['id'], 'iteration': g['iteration']})
            continue
        parts.sort(key=lambda p: -p.area)
        keep = parts[0]
        for extra in parts[1:]:
            g['candidates'].append({'polygon': list(extra.exterior.coords)[:-1], 'from': r['id'], 'why': 'fragment'})
        r['polygon'] = [[round(x, 2), round(y, 2)] for x, y in keep.exterior.coords[:-1]]
        r['holes'] = [[[round(x, 2), round(y, 2)] for x, y in h.coords[:-1]] for h in keep.interiors]
        r['area_px'] = round(keep.area, 2)
        r['area_m2'] = round(keep.area * g['frame']['m_per_px'] ** 2, 3) if g['frame']['m_per_px'] else None
        healed.append(r)
        taken = keep if taken is None else taken.union(keep)
    g['rooms'] = healed
    return g


def validity(room):
    """Problems that make a room not a room (C8). Empty list = valid."""
    pts = room.get('polygon') or []
    issues = []
    if len({tuple(p) for p in pts}) < 3:
        return ['fewer than 3 distinct corners']
    p = Polygon(pts)
    if not p.is_valid:
        issues.append('self-intersecting or invalid')
    if p.area < MIN_AREA:
        issues.append('area below minimum')
    return issues


def to_prediction_row(g, seconds=None):
    """Graph → the semseg scorer's prediction row (class -1 when unknown)."""
    inst = [{'class': r['type'] if isinstance(r['type'], int) else -1, 'score': r['confidence'],
             'polygons': [{'exterior': r['polygon'], 'holes': r.get('holes', [])} if r.get('holes') else r['polygon']]}
            for r in g['rooms']]
    return {'id': g['frame']['id'], 'seconds': seconds, 'instances': inst}


def render_svg(g, image_href=None):
    """Layered SVG of the graph over the page (for review and debugging)."""
    W, H = g['frame']['width'], g['frame']['height']
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    if image_href:
        out.append(f'<image xlink:href="{image_href}" x="0" y="0" width="{W}" height="{H}"/>')
    out.append('<g id="rooms" fill-opacity="0.35" stroke-width="1.5">')
    for r in g['rooms']:
        hue = (hash(r['id']) * 47) % 360
        d = 'M' + ' L'.join(f'{x},{y}' for x, y in r['polygon']) + ' Z'
        for h in r.get('holes', []):
            d += ' M' + ' L'.join(f'{x},{y}' for x, y in h) + ' Z'
        label = ' '.join(str(v) for v in (r.get('number'), r.get('name')) if v)
        out.append(f'<path id="{r["id"]}" d="{d}" fill="hsl({hue},70%,55%)" stroke="hsl({hue},70%,30%)" '
                   f'fill-rule="evenodd" data-area-px="{r["area_px"]}" data-source="{r["source"]}"><title>{r["id"]} {label} '
                   f'{r.get("area_m2") or r["area_px"]}</title></path>')
    out.append('</g>')
    flags = g.get('flags') or []
    if flags:
        out.append('<g id="flags" fill="none" stroke="#e00" stroke-width="3" stroke-dasharray="8 4">')
        for f in flags:
            x0, y0, x1, y1 = f['bbox']
            out.append(f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}"><title>{f["check"]}: {f["why"]}</title></rect>')
        out.append('</g>')
    out.append('</svg>')
    return '\n'.join(out)
