"""Selected model-space DXF boundaries; labels never provide measurement values."""
import math

from shapely.geometry import Polygon

from .common import MAX_ROOMS, MAX_VERTICES, UNITS, Unsupported, checked, record

# INSUNITS values with an unambiguous physical length conversion.
DXF_UNITS = {1: .0254, 2: .3048, 4: .001, 5: .01, 6: 1., 7: 1000.,
             8: .0000000254, 9: .0000254, 10: .9144, 11: 1e-10,
             12: 1e-9, 13: 1e-6, 14: .1, 15: 10., 16: 100.}
SUPPORTED = {'LWPOLYLINE', 'POLYLINE', 'CIRCLE', 'ELLIPSE', 'SPLINE', 'INSERT'}


def inventory(path):
    import ezdxf
    model = ezdxf.readfile(path)
    return [{'id': e.dxf.handle, 'label': f'{e.dxftype()} {e.dxf.handle} · {e.dxf.layer}'}
            for e in model.modelspace() if e.dxftype() in SUPPORTED]


def read(path, element_ids=None, confirmed_unit=None):
    import ezdxf
    from ezdxf.disassemble import recursive_decompose
    from ezdxf.path import make_path

    model = ezdxf.readfile(path)
    declared = int(model.header.get('$INSUNITS', 0))
    factor = DXF_UNITS.get(declared)
    if confirmed_unit is not None:
        if confirmed_unit not in UNITS:
            raise Unsupported('Unsupported confirmed DXF unit.')
        if factor is not None and not math.isclose(factor, UNITS[confirmed_unit]):
            raise Unsupported('Confirmed units conflict with declared DXF units.')
        factor = UNITS[confirmed_unit]
    if declared != 0 and factor is None:
        raise Unsupported('Unsupported DXF declared units; convert the source to metres.')
    if not element_ids:
        raise Unsupported('Select source entities that represent room boundaries.')
    by_id = {e.dxf.handle: e for e in model.modelspace()}
    rooms, rejections = [], []
    for ident in element_ids:
        try:
            if ident not in by_id:
                raise Unsupported('Selected model-space entity was not found.')
            layer=model.layers.get(by_id[ident].dxf.layer)
            if layer.is_off() or layer.is_frozen() or by_id[ident].dxf.get('invisible',0):
                raise Unsupported('Selected entity is hidden by source visibility settings.')
            entities = list(recursive_decompose([by_id[ident]]))
            if len(entities) > MAX_ROOMS:
                raise Unsupported('Selected block exceeds the entity limit.')
            for index, entity in enumerate(entities):
                if entity.dxftype() not in SUPPORTED - {'INSERT'}:
                    # A block may also contain text and dimensions; neither is a boundary.
                    continue
                layer=model.layers.get(entity.dxf.layer)
                if layer.is_off() or layer.is_frozen() or entity.dxf.get('invisible',0):
                    raise Unsupported('Selected block contains hidden boundaries; select explicit visible entities.')
                boundary = make_path(entity)
                if not boundary.is_closed:
                    raise Unsupported('Open DXF boundary requires reconstruction or raster review.')
                distance = .001 / factor if factor else .001
                vertices = []
                for point in boundary.flattening(distance=distance, segments=8):
                    if len(vertices) >= MAX_VERTICES:
                        raise Unsupported('DXF boundary exceeds the vertex limit.')
                    if abs(point.z) > 1e-7:
                        raise Unsupported('Non-planar DXF boundary needs a drawing-plane transform.')
                    vertices.append((point.x, point.y))
                polygon = checked(Polygon(vertices))
                curved = boundary.has_curves
                if len(rooms)>=MAX_ROOMS:raise Unsupported('Too many selected DXF boundaries.')
                rooms.append({'identifier': ident if len(entities) == 1 else f'{ident}:{index}',
                              'label': f'{entity.dxf.layer} · {ident}', 'polygon': record(polygon),
                              'native_area': polygon.area, 'coordinate_space': 'dxf_world_xy',
                              'source_entity': ident, 'layer': entity.dxf.layer,
                              'estimate': curved, 'limitations': ['Curves flattened with maximum chord deviation of 1 mm.'] if curved and factor else
                              ['Curves flattened with chord deviation of 0.001 drawing units.'] if curved else []})
            if not any(r['source_entity'] == ident for r in rooms):
                raise Unsupported('No supported closed boundary in selected entity.')
        except (Unsupported, ValueError, TypeError, ezdxf.DXFError) as error:
            rejections.append({'identifier': ident, 'reason': str(error)})
    if len(rooms) > MAX_ROOMS:
        raise Unsupported('Too many selected DXF boundaries.')
    return {'rooms': rooms, 'rejections': rejections, 'metres_per_unit': factor,
            'units': {'kind': 'dxf_declared_units' if declared else 'human_confirmed_dxf_units',
                      'insunits': declared, 'confirmed_unit': confirmed_unit, 'ezdxf_version': ezdxf.__version__}}
