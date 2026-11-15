"""Read horizontal floor footprints from explicit IfcSpace bodies in SI metres."""
from collections import Counter
import math

from shapely.geometry import Polygon
from shapely.ops import unary_union

from .common import MAX_ROOMS, MAX_VERTICES, Unsupported, checked, record


def floor_polygon(vertices, faces):
    """Use the complete lowest horizontal face of a closed space, preserving holes.

    Projecting every face alone would silently count upper-level overhangs or
    stepped floors. Require that projection to agree with the actual base.
    """
    if not vertices or len(vertices)>MAX_VERTICES or not faces:
        raise Unsupported('Missing or oversized IFC space body.')
    if any(len(p)!=3 or not all(math.isfinite(v) for v in p) for p in vertices):
        raise Unsupported('Nonfinite IFC coordinates.')
    low=min(p[2] for p in vertices); high=max(p[2] for p in vertices)
    if high-low<1e-6:raise Unsupported('IFC space must have a closed body with positive height.')
    edges=Counter(); base=[]; projected=[]
    for face in faces:
        if len(face)!=3 or len(set(face))!=3 or any(i<0 or i>=len(vertices) for i in face):
            raise Unsupported('Malformed IFC triangulation.')
        for a,b in zip(face,face[1:]+face[:1]):edges[tuple(sorted((a,b)))]+=1
        triangle=[vertices[i] for i in face]
        p=Polygon([(v[0],v[1]) for v in triangle])
        if p.area>1e-14:
            projected.append(p)
            if all(abs(v[2]-low)<=1e-6 for v in triangle):base.append(p)
    if not edges or any(count!=2 for count in edges.values()):
        raise Unsupported('IFC space body is open or non-manifold.')
    if not base:raise Unsupported('No horizontal floor face at the lowest space elevation.')
    floor=checked(unary_union(base)); projection=unary_union(projected)
    if floor.symmetric_difference(projection).area>max(1e-8,floor.area*1e-7):
        raise Unsupported('Stepped, sloping or overhanging IFC spaces need a floor-area rule and review.')
    return floor,low


def read(path, element_ids=None, storey_id=None):
    try:
        import ifcopenshell
        import ifcopenshell.geom
        import ifcopenshell.util.element
        import ifcopenshell.util.unit
    except ImportError as error:
        raise Unsupported('IFC support requires the native-geometry optional dependencies.') from error
    model=ifcopenshell.open(str(path))
    projects=model.by_type('IfcProject')
    if len(projects)!=1 or projects[0].UnitsInContext is None:
        raise Unsupported('One IFC project with explicit length units is required.')
    if model.schema!='IFC2X3' and model.by_type('IfcProjectLibrary'):
        raise Unsupported('IFC project-library unit contexts need independent verification.')
    units=[u for u in projects[0].UnitsInContext.Units if getattr(u,'UnitType',None)=='LENGTHUNIT']
    if len(units)!=1:raise Unsupported('IFC length units are missing or ambiguous.')
    unit=units[0]; seen=set()
    while not unit.is_a('IfcSIUnit'):
        if not unit.is_a('IfcConversionBasedUnit') or unit.is_a('IfcConversionBasedUnitWithOffset') or unit.id() in seen:
            raise Unsupported('Unsupported or ambiguous IFC length-unit definition.')
        seen.add(unit.id())
        conversion=unit.ConversionFactor
        value=conversion.ValueComponent.wrappedValue
        if not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise Unsupported('Invalid IFC length-unit conversion factor.')
        unit=conversion.UnitComponent
    if unit.UnitType!='LENGTHUNIT' or unit.Name!='METRE':
        raise Unsupported('IFC length units must resolve to SI metres.')
    factor=ifcopenshell.util.unit.calculate_unit_scale(model,'LENGTHUNIT')
    if not math.isfinite(factor) or factor<=0:raise Unsupported('Invalid IFC length unit conversion.')
    settings=ifcopenshell.geom.settings()
    settings.set('use-world-coords',True)
    settings.set('convert-back-units',False)
    settings.set('weld-vertices',True)
    settings.set('mesher-linear-deflection',1e-6)
    spaces=model.by_type('IfcSpace')
    by_guid={s.GlobalId:s for s in spaces}
    if len(by_guid)!=len(spaces):raise Unsupported('Duplicate IFC space GlobalIds are ambiguous.')
    selected=spaces if element_ids is None else [by_guid[i] for i in element_ids if i in by_guid]
    rooms=[]; rejected=[]
    for ident in element_ids or []:
        if ident not in by_guid:rejected.append({'identifier':ident,'reason':'Selected IfcSpace GlobalId was not found.'})
    for space in selected:
        parent=ifcopenshell.util.element.get_aggregate(space); visited=set()
        while parent is not None and not parent.is_a('IfcBuildingStorey'):
            if parent.id() in visited:raise Unsupported('Cyclic IFC spatial hierarchy.')
            visited.add(parent.id()); parent=ifcopenshell.util.element.get_aggregate(parent)
        sid=parent.GlobalId if parent is not None else None
        if storey_id is not None and sid!=storey_id:continue
        if len(rooms)+len(rejected)>=MAX_ROOMS:raise Unsupported('Too many IFC spaces; select a storey or explicit room IDs.')
        try:
            if space.CompositionType not in ('ELEMENT',None):
                raise Unsupported('Space groups or partial spaces need an explicit measurement scope.')
            if space.Representation is None:raise Unsupported('IfcSpace has no body geometry.')
            curved={'IfcCircle','IfcEllipse','IfcBSplineCurve','IfcTrimmedCurve','IfcIndexedPolyCurve'}
            for entity in model.traverse(space.Representation):
                if entity.is_a('IfcSolidModel') and not (entity.is_a('IfcExtrudedAreaSolid') or entity.is_a('IfcFacetedBrep') or entity.is_a('IfcCsgSolid')):
                    raise Unsupported('Unsupported IFC solid; use a faceted or linear extruded room body.')
                if entity.is_a('IfcCsgPrimitive3D') and not entity.is_a('IfcBlock'):
                    raise Unsupported('Curved IFC primitives need review.')
                if entity.is_a('IfcElementarySurface') and not entity.is_a('IfcPlane'):
                    raise Unsupported('Curved IFC surfaces need review.')
                if entity.is_a('IfcProfileDef') and not (entity.is_a('IfcArbitraryClosedProfileDef') or entity.is_a('IfcRectangleProfileDef')):
                    raise Unsupported('Unsupported IFC space profile; provide a closed linear room boundary.')
                if any((getattr(entity,name,None) or 0)>0 for name in ('RoundingRadius','InnerFilletRadius','OuterFilletRadius')):
                    raise Unsupported('Rounded IFC space profiles need review.')
                if any(entity.is_a(kind) for kind in curved if kind!='IfcIndexedPolyCurve'):
                    raise Unsupported('Curved IFC space boundaries need review; no curve approximation is certified.')
                if entity.is_a('IfcIndexedPolyCurve') and entity.Segments and any(s.is_a('IfcArcIndex') for s in entity.Segments):
                    raise Unsupported('Curved IFC space boundaries need review.')
            shape=ifcopenshell.geom.create_shape(settings,space)
            raw=list(shape.geometry.verts); indices=list(shape.geometry.faces)
            vertices=[raw[i:i+3] for i in range(0,len(raw),3)]
            faces=[indices[i:i+3] for i in range(0,len(indices),3)]
            polygon,elevation=floor_polygon(vertices,faces)
            rooms.append({'identifier':space.GlobalId,'label':space.LongName or space.Name or space.GlobalId,
                          'polygon':record(polygon),'native_area':polygon.area,'coordinate_space':'ifc_world_xy_metres',
                          'storey_id':sid,'floor_elevation_m':elevation,'ifc_entity_id':space.id()})
        except (Unsupported,RuntimeError,ValueError) as error:
            rejected.append({'identifier':space.GlobalId,'reason':str(error)})
    return {'rooms':rooms,'rejections':rejected,'metres_per_unit':1.,
            'ifc_project_global_id':projects[0].GlobalId,'ifc_schema':model.schema,
            'units':{'kind':'ifc_project_length_units','source_unit':str(units[0]),
                     'metres_per_native_model_unit':factor,'geometry_output':'SI metres','ifcopenshell_version':ifcopenshell.version},
            'storey_id':storey_id}
