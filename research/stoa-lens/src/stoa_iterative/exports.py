"""L6 exports (DXF, IFC, GeoJSON) and their round-trip checks (PLAN §5.3 rule 4).

Coordinates: metres when the page scale is known (x right, y up, origin at the page's top-left corner), else
pixels (and marked unscaled). Every export is re-read and each room must come back closed with the same area
(within 0.5%).
"""
import json

from shapely.geometry import Polygon

TOL = 0.005


def _xy(g, x, y):
    k = g['frame']['m_per_px'] or 1.0
    return x * k, -y * k


def _area(g, room):
    k = (g['frame']['m_per_px'] or 1.0) ** 2
    return Polygon(room['polygon'], room.get('holes') or []).area * k


def to_dxf(g, path):
    import ezdxf
    doc = ezdxf.new('R2010')
    doc.units = 6 if g['frame']['m_per_px'] else 0  # metres / unitless
    for name in ('ROOM_OUTLINE', 'ROOM_HOLE', 'ROOM_TEXT'):
        doc.layers.add(name)
    msp = doc.modelspace()
    for r in g['rooms']:
        msp.add_lwpolyline([_xy(g, x, y) for x, y in r['polygon']], close=True, dxfattribs={'layer': 'ROOM_OUTLINE'})
        for h in r.get('holes', []):
            msp.add_lwpolyline([_xy(g, x, y) for x, y in h], close=True, dxfattribs={'layer': 'ROOM_HOLE'})
        c = Polygon(r['polygon']).representative_point()
        label = r.get('number') or r['id']
        msp.add_text(str(label), dxfattribs={'layer': 'ROOM_TEXT', 'height': 0.25 if g['frame']['m_per_px'] else 10}).set_placement(_xy(g, c.x, c.y))
    doc.saveas(path)
    return path


def check_dxf(g, path):
    import ezdxf
    doc = ezdxf.readfile(path)
    outlines = [e for e in doc.modelspace().query('LWPOLYLINE[layer=="ROOM_OUTLINE"]')]
    holes = [Polygon(list(e.get_points('xy'))) for e in doc.modelspace().query('LWPOLYLINE[layer=="ROOM_HOLE"]')]
    got = []
    for e in outlines:
        p = Polygon(list(e.get_points('xy')))
        inner = [h for h in holes if p.contains(h.representative_point())]
        got.append((e.closed, p.area - sum(h.area for h in inner)))
    return _compare(g, got)


def to_geojson(g, path):
    feats = []
    for r in g['rooms']:
        rings = [[list(_xy(g, x, y)) for x, y in r['polygon'] + [r['polygon'][0]]]]
        rings += [[list(_xy(g, x, y)) for x, y in h + [h[0]]] for h in r.get('holes', [])]
        feats.append({'type': 'Feature', 'geometry': {'type': 'Polygon', 'coordinates': rings},
                      'properties': {k: r.get(k) for k in ('id', 'type', 'number', 'name', 'source', 'confidence', 'area_m2', 'area_px')}})
    out = {'type': 'FeatureCollection', 'features': feats,
           'properties': {'page': g['frame']['id'], 'units': 'm' if g['frame']['m_per_px'] else 'px'}}
    with open(path, 'w') as f:
        json.dump(out, f)
    return path


def check_geojson(g, path):
    d = json.load(open(path))
    got = []
    for ft in d['features']:
        rings = ft['geometry']['coordinates']
        got.append((rings[0][0] == rings[0][-1], Polygon(rings[0], rings[1:]).area))
    return _compare(g, got)


def to_ifc(g, path, storey_name='Storey', height=2.5):
    """IFC4: project / site / building / storey, one IfcSpace per room (extruded closed profile, holes as voids)."""
    import ifcopenshell
    import ifcopenshell.api
    f = ifcopenshell.file(schema='IFC4')
    project = ifcopenshell.api.run('root.create_entity', f, ifc_class='IfcProject', name=g['frame']['id'])
    ifcopenshell.api.run('unit.assign_unit', f)
    model = ifcopenshell.api.run('context.add_context', f, context_type='Model')
    body = ifcopenshell.api.run('context.add_context', f, context_type='Model', context_identifier='Body',
                                target_view='MODEL_VIEW', parent=model)
    site = ifcopenshell.api.run('root.create_entity', f, ifc_class='IfcSite', name='Site')
    bldg = ifcopenshell.api.run('root.create_entity', f, ifc_class='IfcBuilding', name='Building')
    storey = ifcopenshell.api.run('root.create_entity', f, ifc_class='IfcBuildingStorey', name=storey_name)
    ifcopenshell.api.run('aggregate.assign_object', f, relating_object=project, products=[site])
    ifcopenshell.api.run('aggregate.assign_object', f, relating_object=site, products=[bldg])
    ifcopenshell.api.run('aggregate.assign_object', f, relating_object=bldg, products=[storey])
    origin = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)))
    for r in g['rooms']:
        def poly(ring):
            pts = [f.createIfcCartesianPoint(tuple(float(v) for v in _xy(g, x, y))) for x, y in ring]
            return f.createIfcPolyline(pts + [pts[0]])
        outer = poly(r['polygon'])
        holes = [poly(h) for h in r.get('holes', [])]
        prof = (f.createIfcArbitraryProfileDefWithVoids('AREA', r['id'], outer, holes) if holes
                else f.createIfcArbitraryClosedProfileDef('AREA', r['id'], outer))
        solid = f.createIfcExtrudedAreaSolid(prof, origin, f.createIfcDirection((0.0, 0.0, 1.0)), height)
        rep = f.createIfcShapeRepresentation(body, 'Body', 'SweptSolid', [solid])
        space = ifcopenshell.api.run('root.create_entity', f, ifc_class='IfcSpace', name=str(r.get('number') or r['id']))
        space.LongName = r.get('name')
        space.Representation = f.createIfcProductDefinitionShape(None, None, [rep])
        space.ObjectPlacement = f.createIfcLocalPlacement(None, origin)
        ifcopenshell.api.run('aggregate.assign_object', f, relating_object=storey, products=[space])
    f.write(path)
    return path


def check_ifc(g, path):
    import ifcopenshell
    f = ifcopenshell.open(path)
    got = []
    for sp in f.by_type('IfcSpace'):
        solid = sp.Representation.Representations[0].Items[0]
        prof = solid.SweptArea
        ring = [p.Coordinates for p in prof.OuterCurve.Points]
        closed = ring[0] == ring[-1]
        area = Polygon(ring).area
        for v in getattr(prof, 'InnerCurves', None) or []:
            area -= Polygon([p.Coordinates for p in v.Points]).area
        got.append((closed, area))
    return _compare(g, got)


def _compare(g, got):
    want = sorted(_area(g, r) for r in g['rooms'])
    have = sorted(a for _, a in got)
    closed = sum(1 for c, _ in got if c)
    ok = len(want) == len(have) and all(abs(a - b) <= TOL * max(a, 1e-9) for a, b in zip(want, have))
    return {'rooms': len(want), 'read_back': len(have), 'closed': closed, 'areas_match': ok}
