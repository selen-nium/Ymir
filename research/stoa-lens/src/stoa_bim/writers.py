"""Model → IFC4 (3D BIM), DXF (Junction layers), SVG (2D, PNG pixel frame), GLB (3D preview).

IFC4 content: IfcProject / IfcSite / IfcBuilding / IfcBuildingStorey;
  IfcWall per wall polygon (extruded z0..z1);
  IfcDoor per door gap (0..door head) + IfcWall piece above it (head..storey height);
  IfcWindow per window (sill..head) + IfcWall pieces below and above;
  IfcFurnishingElement per fixture (0..0.9 m);
  IfcSpace per room (with Name = room number, LongName = room name);
  IfcSlab under everything (-slab..0).
Doors also carry a 2D 'Plan' / 'Annotation' representation (leaf and swing curves), as BIM tools draw them in plan.
All heights are model defaults, recorded in property set Stoa_Assumptions on the storey.
"""
import json
import math
import struct

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from .model import poly


# ---------------------------------------------------------------- plan symbols (2D curves stored in the IFC)

def window_symbol(w):
    """The window's plan lines: given ones (reference), else the frame plus two glazing lines along the long axis."""
    if w.get('plan'):
        return w['plan']
    c = Polygon(w['polygon']).minimum_rotated_rectangle.exterior.coords[:4]
    a, b, cc, d = [list(x) for x in c]
    if Polygon(w['polygon']).length and ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) < ((b[0] - cc[0]) ** 2 + (b[1] - cc[1]) ** 2):
        a, b, cc, d = b, cc, d, a
    lerp = lambda p, q, t: [p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])]
    return [[a, b, cc, d, a]] + [[lerp(a, d, t), lerp(b, cc, t)] for t in (1 / 3, 2 / 3)]


def door_symbol(d):
    """The door's plan lines: given ones (reference), else a leaf at one jamb and a quarter-circle swing."""
    if d.get('plan'):
        return d['plan']
    out = [c for c in (d.get('leaf'), d.get('swing')) if c]
    return out


# ---------------------------------------------------------------- IFC

def to_ifc(m, path):
    import ifcopenshell
    import ifcopenshell.api
    import ifcopenshell.guid
    f = ifcopenshell.file(schema='IFC4')
    run = ifcopenshell.api.run
    project = run('root.create_entity', f, ifc_class='IfcProject', name=m['frame']['page'])
    run('unit.assign_unit', f, length={'is_metric': True, 'raw': 'METERS'}, area={'is_metric': True, 'raw': 'SQUARE_METERS'}, volume={'is_metric': True, 'raw': 'CUBIC_METERS'})
    model_ctx = run('context.add_context', f, context_type='Model')
    body = run('context.add_context', f, context_type='Model', context_identifier='Body', target_view='MODEL_VIEW', parent=model_ctx)
    plan_ctx = run('context.add_context', f, context_type='Plan')
    annot = run('context.add_context', f, context_type='Plan', context_identifier='Annotation', target_view='PLAN_VIEW', parent=plan_ctx)
    site = run('root.create_entity', f, ifc_class='IfcSite', name='Site')
    bldg = run('root.create_entity', f, ifc_class='IfcBuilding', name='Building')
    st = m['storey']
    storey = run('root.create_entity', f, ifc_class='IfcBuildingStorey', name=st['name'])
    storey.Elevation = st['elevation']
    run('aggregate.assign_object', f, relating_object=project, products=[site])
    run('aggregate.assign_object', f, relating_object=site, products=[bldg])
    run('aggregate.assign_object', f, relating_object=bldg, products=[storey])
    pset = run('pset.add_pset', f, product=storey, name='Stoa_Assumptions')
    run('pset.edit_pset', f, pset=pset, properties={k: (float(v) if isinstance(v, (int, float)) else str(v)) for k, v in m['assumptions'].items()}
        | {'Source': m['source'], 'SourcePNG': m['frame']['page'], 'MetresPerPixel': float(m['frame']['m_per_px'])})
    origin = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)))
    up = f.createIfcDirection((0.0, 0.0, 1.0))

    def ring(pts):
        P = [f.createIfcCartesianPoint((float(x), float(y))) for x, y in pts]
        return f.createIfcPolyline(P + [P[0]])

    def solid(g, z0, z1, name):
        g = g.buffer(0)
        parts = [p for p in getattr(g, 'geoms', [g]) if p.geom_type == 'Polygon' and p.area > 1e-6]
        items = []
        for p in parts:
            outer = ring(list(p.exterior.coords)[:-1])
            holes = [ring(list(h.coords)[:-1]) for h in p.interiors]
            prof = (f.createIfcArbitraryProfileDefWithVoids('AREA', name, outer, holes) if holes
                    else f.createIfcArbitraryClosedProfileDef('AREA', name, outer))
            pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, float(z0))))
            items.append(f.createIfcExtrudedAreaSolid(prof, pos, up, float(z1 - z0)))
        return items

    def product(cls, name, g, z0, z1, plan_curves=None, contained=True):
        items = solid(g, z0, z1, name)
        if not items:
            return None
        reps = [f.createIfcShapeRepresentation(body, 'Body', 'SweptSolid', items)]
        if plan_curves:
            curves = [f.createIfcPolyline([f.createIfcCartesianPoint((float(x), float(y))) for x, y in c]) for c in plan_curves if c and len(c) >= 2]
            if curves:
                reps.append(f.createIfcShapeRepresentation(annot, 'Annotation', 'Curve2D', curves))
        e = run('root.create_entity', f, ifc_class=cls, name=name)
        e.Representation = f.createIfcProductDefinitionShape(None, None, reps)
        e.ObjectPlacement = f.createIfcLocalPlacement(None, f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0))))
        if contained:
            run('spatial.assign_container', f, relating_structure=storey, products=[e])
        return e

    H = st['height']
    for w in m['walls']:
        product('IfcWall', w['id'], poly(w), w.get('z0', 0.0), w.get('z1', H))
    for d in m['doors']:
        g = Polygon(d['polygon'])
        e = product('IfcDoor', d['id'], g, d.get('z0', 0.0), d.get('z1', 2.1), plan_curves=door_symbol(d))
        if e is not None:
            e.OverallHeight = float(d.get('z1', 2.1) - d.get('z0', 0.0))
        product('IfcWall', d['id'] + '-head', g, d.get('z1', 2.1), H)
    for w in m['windows']:
        g = Polygon(w['polygon'])
        e = product('IfcWindow', w['id'], g, w.get('z0', 0.9), w.get('z1', 2.1), plan_curves=window_symbol(w))
        if e is not None:
            e.OverallHeight = float(w.get('z1', 2.1) - w.get('z0', 0.9))
        product('IfcWall', w['id'] + '-sill', g, 0.0, w.get('z0', 0.9))
        product('IfcWall', w['id'] + '-head', g, w.get('z1', 2.1), H)
    for x in m['fixtures']:
        if x.get('ifc_type'):
            e = product('IfcSanitaryTerminal', x['id'], poly(x), 0.0, x.get('z1', 0.9), plan_curves=x.get('plan'))
            if e is not None:
                e.PredefinedType = x['ifc_type']
                e.ObjectType = x.get('kind')
        else:
            product('IfcFurnishingElement', x['id'], poly(x), 0.0, 0.9)
    for t in m.get('stairs', []):
        product('IfcStair', t['id'], poly(t), 0.0, 0.05, plan_curves=t.get('plan'))
    for s in m['spaces']:
        e = product('IfcSpace', str(s.get('number') or s['id']), poly(s), 0.0, H, contained=False)
        if e is not None:
            e.LongName = s.get('name')
            run('aggregate.assign_object', f, relating_object=storey, products=[e])
    foot = unary_union([poly(e) for k in ('walls', 'spaces') for e in m[k]] + [Polygon(e['polygon']) for k in ('doors', 'windows') for e in m[k]])
    if not foot.is_empty:
        slab_g = unary_union([Polygon(p.exterior) for p in getattr(foot.buffer(0.05), 'geoms', [foot.buffer(0.05)])])
        product('IfcSlab', 'Floor slab', slab_g, -m['assumptions']['slab'], 0.0)
    f.write(str(path))
    return path


# ---------------------------------------------------------------- DXF

def to_dxf(m, path):
    import ezdxf
    doc = ezdxf.new('R2018')
    doc.units = 6  # metres
    msp = doc.modelspace()
    for name, color in (('WALL', 7), ('DOOR', 3), ('WINDOW', 5), ('ROOM_OUTLINE', 1), ('ROOM_NAME', 7), ('ROOM_NUMBER', 7)):
        doc.layers.add(name, color=color)
    for w in m['walls']:
        h = msp.add_hatch(dxfattribs={'layer': 'WALL'})
        h.paths.add_polyline_path(w['polygon'], is_closed=True)
        for hole in w.get('holes') or []:
            h.paths.add_polyline_path(hole, is_closed=True)
        msp.add_lwpolyline(w['polygon'], close=True, dxfattribs={'layer': 'WALL'})
    for d in m['doors']:
        msp.add_lwpolyline(d['polygon'], close=True, dxfattribs={'layer': 'DOOR'})
        for c in door_symbol(d):
            msp.add_lwpolyline(c, dxfattribs={'layer': 'DOOR'})
    # every element also has one closed outline on its own layer, so a CAD user (or a script) can count it:
    # DOOR / WINDOW / STAIR outlines, FIXTURE_<KIND> outlines; the drawn symbols stay on *_SYMBOL layers
    for name, color in (('WINDOW_SYMBOL', 5), ('FIXTURE_SYMBOL', 8), ('STAIR', 4), ('STAIR_SYMBOL', 4)):
        doc.layers.add(name, color=color)
    for w in m['windows']:
        msp.add_lwpolyline(w['polygon'], close=True, dxfattribs={'layer': 'WINDOW'})
        for c in window_symbol(w):
            msp.add_lwpolyline(c, dxfattribs={'layer': 'WINDOW_SYMBOL'})
    for x in m['fixtures']:
        lay = 'FIXTURE_' + str(x.get('kind') or 'OTHER').upper()
        if lay not in doc.layers:
            doc.layers.add(lay, color=8)
        msp.add_lwpolyline(x['polygon'], close=True, dxfattribs={'layer': lay})
        for c in x.get('plan') or []:
            msp.add_lwpolyline(c, dxfattribs={'layer': 'FIXTURE_SYMBOL'})
    for t in m.get('stairs', []):
        msp.add_lwpolyline(t['polygon'], close=True, dxfattribs={'layer': 'STAIR'})
        for c in t.get('plan') or []:
            msp.add_lwpolyline(c, dxfattribs={'layer': 'STAIR_SYMBOL'})
    for s in m['spaces']:
        msp.add_lwpolyline(s['polygon'], close=True, dxfattribs={'layer': 'ROOM_OUTLINE'})
        c = Polygon(s['polygon']).representative_point()
        g = Polygon(s['polygon'])
        below = (c.x, c.y - 0.3) if g.contains(Point(c.x, c.y - 0.3)) else (c.x, c.y)  # keep the number inside its room
        if s.get('name'):
            msp.add_text(str(s['name']), dxfattribs={'layer': 'ROOM_NAME', 'height': 0.22}).set_placement((c.x, c.y))
        if s.get('number'):
            msp.add_text(str(s['number']), dxfattribs={'layer': 'ROOM_NUMBER', 'height': 0.22}).set_placement(below)
    doc.saveas(str(path))
    return path


# ---------------------------------------------------------------- SVG (PNG pixel frame)

def _x(v):
    from html import escape
    return escape(str(v)) if v else ''


def to_svg(m, path, png_href=None):
    k = m['frame']['m_per_px']
    W, H = m['frame']['width'], m['frame']['height']
    P = lambda pts: ' '.join(f'{x / k:.1f},{-y / k:.1f}' for x, y in pts)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" data-m-per-px="{k:.8f}">',
           '<rect width="100%" height="100%" fill="white"/>']
    if png_href:
        out.append(f'<image href="{png_href}" width="{W}" height="{H}" opacity="0.35"/>')
    out.append('<g id="spaces" fill="#e8f0fb" stroke="#2a78d6" stroke-width="1">')
    for s in m['spaces']:
        out.append(f'<polygon class="space" data-number="{_x(s.get("number"))}" data-name="{_x(s.get("name"))}" points="{P(s["polygon"])}"><title>{_x(s.get("number"))} {_x(s.get("name"))}</title></polygon>')
    out.append('</g><g id="walls" fill="#9a9a9a" stroke="#111" stroke-width="1.5">')
    for w in m['walls']:
        d = ''.join(f'M{P(r)}Z' for r in [w['polygon']] + (w.get('holes') or []))
        out.append(f'<path fill-rule="evenodd" d="{d.replace(" ", " L")}"/>')
    out.append('</g><g id="windows" fill="white" stroke="#111" stroke-width="1">')
    for w in m['windows']:
        out.append(f'<g class="window"><polygon points="{P(w["polygon"])}"/>')
        for c in window_symbol(w):
            out.append(f'<polyline points="{P(c)}" fill="none"/>')
        out.append('</g>')
    out.append('</g><g id="doors" fill="none" stroke="#111" stroke-width="1">')
    for d in m['doors']:
        out.append(f'<g class="door"><polygon points="{P(d["polygon"])}" fill="white"/>')
        for c in door_symbol(d):
            out.append(f'<polyline points="{P(c)}"/>')
        out.append('</g>')
    out.append('</g><g id="fixtures" fill="none" stroke="#111" stroke-width="1">')
    for x in m['fixtures']:
        out.append(f'<g class="fixture" data-kind="{_x(x.get("kind"))}">')
        for c in (x.get('plan') or [x['polygon'] + [x['polygon'][0]]]):
            out.append(f'<polyline points="{P(c)}"/>')
        out.append('</g>')
    for t in m.get('stairs', []):
        out.append('<g class="stair">')
        for c in t.get('plan') or [t['polygon'] + [t['polygon'][0]]]:
            out.append(f'<polyline points="{P(c)}"/>')
        out.append('</g>')
    out.append('</g><g id="labels" font-family="sans-serif" font-size="14" text-anchor="middle" fill="#111">')
    for s in m['spaces']:
        c = Polygon(s['polygon']).representative_point()
        if s.get('number'):
            out.append(f'<text x="{c.x / k:.1f}" y="{-c.y / k:.1f}">{s.get("name") or ""} {s["number"]}</text>')
    out.append('</g></svg>')
    open(path, 'w').write('\n'.join(out))
    return path


# ---------------------------------------------------------------- GLB from the IFC (3D preview)

COLORS = {'IfcSanitaryTerminal': (0.95, 0.95, 0.97, 1.0), 'IfcStair': (0.70, 0.66, 0.58, 1.0), 'IfcWall': (0.62, 0.62, 0.60, 1.0), 'IfcDoor': (0.55, 0.36, 0.20, 1.0), 'IfcWindow': (0.45, 0.70, 0.90, 0.55),
          'IfcSlab': (0.80, 0.80, 0.78, 1.0), 'IfcFurnishingElement': (0.85, 0.85, 0.85, 1.0), 'IfcSpace': (0.25, 0.50, 0.85, 0.12)}


def ifc_to_glb(ifc_path, path, include_spaces=False):
    """Tessellate the IFC with IfcOpenShell (world coordinates) and write one GLB mesh per IFC class."""
    import ifcopenshell
    import ifcopenshell.geom
    f = ifcopenshell.open(str(ifc_path))
    st = ifcopenshell.geom.settings()
    st.set('use-world-coords', True)
    groups = {}
    for e in f.by_type('IfcProduct'):
        cls = e.is_a()
        if cls not in COLORS or (cls == 'IfcSpace' and not include_spaces) or not e.Representation:
            continue
        try:
            sh = ifcopenshell.geom.create_shape(st, e)
        except Exception:
            continue
        v = np.array(sh.geometry.verts, dtype=np.float32).reshape(-1, 3)
        t = np.array(sh.geometry.faces, dtype=np.uint32).reshape(-1, 3)
        if not len(t):
            continue
        V, Tl = groups.setdefault(cls, ([], []))
        off = sum(len(x) for x in V)
        V.append(v)
        Tl.append(t + off)
    return _write_glb(groups, path)


def _write_glb(groups, path):
    bin_, views, accs, meshes, mats, nodes = b'', [], [], [], [], []
    allv = np.concatenate([np.concatenate(V) for V, _ in groups.values()]) if groups else np.zeros((1, 3), np.float32)
    center = (allv.min(0) + allv.max(0)) / 2
    for i, (cls, (V, Tl)) in enumerate(groups.items()):
        v = np.concatenate(V) - center
        v = np.stack([v[:, 0], v[:, 2], -v[:, 1]], 1).astype(np.float32)  # IFC z-up -> glTF y-up
        t = np.concatenate(Tl).astype(np.uint32).ravel()
        for arr, target in ((v, 34962), (t, 34963)):
            data = arr.tobytes()
            pad = (-len(data)) % 4
            views.append({'buffer': 0, 'byteOffset': len(bin_), 'byteLength': len(data), 'target': target})
            bin_ += data + b'\0' * pad
        accs.append({'bufferView': len(views) - 2, 'componentType': 5126, 'count': len(v), 'type': 'VEC3',
                     'min': v.min(0).tolist(), 'max': v.max(0).tolist()})
        accs.append({'bufferView': len(views) - 1, 'componentType': 5125, 'count': len(t), 'type': 'SCALAR'})
        c = COLORS[cls]
        mats.append({'name': cls, 'pbrMetallicRoughness': {'baseColorFactor': list(c), 'metallicFactor': 0.0, 'roughnessFactor': 0.9},
                     'doubleSided': True, **({'alphaMode': 'BLEND'} if c[3] < 1 else {})})
        meshes.append({'name': cls, 'primitives': [{'attributes': {'POSITION': len(accs) - 2}, 'indices': len(accs) - 1, 'material': len(mats) - 1}]})
        nodes.append({'mesh': len(meshes) - 1, 'name': cls})
    gltf = {'asset': {'version': '2.0', 'generator': 'stoa_bim'}, 'scene': 0, 'scenes': [{'nodes': list(range(len(nodes)))}],
            'nodes': nodes, 'meshes': meshes, 'materials': mats, 'accessors': accs, 'bufferViews': views,
            'buffers': [{'byteLength': len(bin_)}]}
    js = json.dumps(gltf).encode()
    js += b' ' * ((-len(js)) % 4)
    out = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_))
    out += struct.pack('<II', len(js), 0x4E4F534A) + js + struct.pack('<II', len(bin_), 0x004E4942) + bin_
    open(path, 'wb').write(out)
    return path


# ---------------------------------------------------------------- vector PDF (1:100, page fitted to the plan)

def to_pdf(m, path, scale=100, margin_mm=15):
    """Vector PDF of the plan at 1:scale: walls filled grey, openings / fixtures / stairs as lines, rooms with labels."""
    import pymupdf
    from shapely.ops import unary_union
    geoms = [poly(e) for k in ('walls', 'spaces') for e in m[k]] + [Polygon(e['polygon']) for k in ('doors', 'windows') for e in m[k]]
    if not geoms:
        return None
    x0, y0, x1, y1 = unary_union(geoms).bounds
    pt = 72 / 25.4 * 1000 / scale  # points per metre at 1:scale
    mg = margin_mm * 72 / 25.4
    W, H = (x1 - x0) * pt + 2 * mg, (y1 - y0) * pt + 2 * mg + 30
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    P = lambda pts: [pymupdf.Point(mg + (x - x0) * pt, mg + (y1 - y) * pt) for x, y in pts]
    # one optional-content layer per element type (as CAD-exported PDFs have), one path per element
    L = {n: doc.add_ocg(n) for n in ('Rooms', 'Walls', 'Doors', 'Door symbols', 'Windows', 'Window symbols', 'Stairs', 'Room labels')}
    K = {}

    def draw(lines, layer, color=(0.07, 0.07, 0.07), fill=None, width=0.35):
        sh = page.new_shape()
        for c in lines:
            sh.draw_polyline(P(c))
        sh.finish(color=color, fill=fill, width=width, oc=layer)
        sh.commit()
    for s in m['spaces']:
        draw([s['polygon'] + [s['polygon'][0]]], L['Rooms'], (0.16, 0.47, 0.84), (0.91, 0.94, 0.98), 0.4)
    for w in m['walls']:
        draw([w['polygon'] + [w['polygon'][0]]], L['Walls'], fill=(0.62, 0.62, 0.62), width=0.6)
    for d in m['doors']:
        draw([d['polygon'] + [d['polygon'][0]]], L['Doors'])
        if door_symbol(d):
            draw(door_symbol(d), L['Door symbols'])
    for w in m['windows']:
        draw([w['polygon'] + [w['polygon'][0]]], L['Windows'])
        draw(window_symbol(w), L['Window symbols'])
    for x in m['fixtures']:
        name = 'Fixtures: ' + str(x.get('kind') or 'other')
        if name not in K:
            K[name] = doc.add_ocg(name)
        draw(x.get('plan') or [x['polygon'] + [x['polygon'][0]]], K[name])
    for t in m.get('stairs', []):
        draw(t.get('plan') or [t['polygon'] + [t['polygon'][0]]], L['Stairs'])
    for s in m['spaces']:
        c = Polygon(s['polygon']).representative_point()
        label = ' '.join(str(v) for v in (s.get('name'), s.get('number')) if v)
        if label:
            page.insert_text(P([(c.x, c.y)])[0], label, fontsize=6, oc=L['Room labels'])
    page.insert_text((mg, H - 12), f"{m['frame']['page']} - generated from PNG - scale 1:{scale} - {m['source']}"[:160], fontsize=6)
    doc.save(str(path))
    return path
