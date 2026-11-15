"""Analytic IFC geometry fixture adapted from the Stoa native geometry tests."""
import pytest

def make_ifc(path, *, millimetres=True, feet=False, missing_units=False, missing_body=False, second_floor=False, curved=False):
    ifc=pytest.importorskip('ifcopenshell')
    f=ifc.file(schema='IFC4'); unit=1/.3048 if feet else 1000 if millimetres else 1
    def point(*xyz):return f.create_entity('IfcCartesianPoint',Coordinates=tuple(float(v) for v in xyz))
    def axis(x=0,y=0,z=0,rotate=False):
        return f.create_entity('IfcAxis2Placement3D',Location=point(x,y,z),
             Axis=f.create_entity('IfcDirection',DirectionRatios=(0.,0.,1.)),
             RefDirection=f.create_entity('IfcDirection',DirectionRatios=(0.,1.,0.) if rotate else (1.,0.,0.)))
    context=f.create_entity('IfcGeometricRepresentationContext',ContextType='Model',CoordinateSpaceDimension=3,
                            Precision=1e-8,WorldCoordinateSystem=axis())
    length_unit=f.create_entity('IfcSIUnit',UnitType='LENGTHUNIT',Prefix='MILLI' if millimetres and not feet else None,Name='METRE')
    if feet:
        length_unit=f.create_entity('IfcConversionBasedUnit',UnitType='LENGTHUNIT',Name='foot',
            Dimensions=f.create_entity('IfcDimensionalExponents',1,0,0,0,0,0,0),
            ConversionFactor=f.create_entity('IfcMeasureWithUnit',ValueComponent=f.create_entity('IfcLengthMeasure',.3048),UnitComponent=length_unit))
    units=f.create_entity('IfcUnitAssignment',Units=[length_unit])
    project=f.create_entity('IfcProject',GlobalId=ifc.guid.new(),Name='Analytic fixture',RepresentationContexts=[context],UnitsInContext=None if missing_units else units)
    building=f.create_entity('IfcBuilding',GlobalId=ifc.guid.new(),Name='Fixture building',CompositionType='ELEMENT')
    f.create_entity('IfcRelAggregates',GlobalId=ifc.guid.new(),RelatingObject=project,RelatedObjects=[building])
    def ring(points):return f.create_entity('IfcPolyline',Points=[point(x*unit,y*unit) for x,y in points])
    outer=ring([(0,0),(4,0),(4,3),(0,3),(0,0)])
    hole=ring([(1,1),(1,2),(2,2),(2,1),(1,1)])
    profile=f.create_entity('IfcArbitraryProfileDefWithVoids',ProfileType='AREA',OuterCurve=outer,InnerCurves=[hole])
    if curved:
        profile=f.create_entity('IfcCircleProfileDef',ProfileType='AREA',Radius=float(2*unit))
    solid=f.create_entity('IfcExtrudedAreaSolid',SweptArea=profile,Position=axis(),
             ExtrudedDirection=f.create_entity('IfcDirection',DirectionRatios=(0.,0.,1.)),Depth=float(3*unit))
    shape=f.create_entity('IfcProductDefinitionShape',Representations=[f.create_entity('IfcShapeRepresentation',ContextOfItems=context,
              RepresentationIdentifier='Body',RepresentationType='SweptSolid',Items=[solid])])
    spaces=[]; storeys=[]
    for level in range(2 if second_floor else 1):
        placement=f.create_entity('IfcLocalPlacement',RelativePlacement=axis(5*unit,6*unit,(2+level*3)*unit))
        storey=f.create_entity('IfcBuildingStorey',GlobalId=ifc.guid.new(),Name='Level '+str(level),CompositionType='ELEMENT',ObjectPlacement=placement)
        space=f.create_entity('IfcSpace',GlobalId=ifc.guid.new(),Name='R'+str(level),LongName='Analytic room',CompositionType='ELEMENT',
            ObjectPlacement=f.create_entity('IfcLocalPlacement',PlacementRelTo=placement,RelativePlacement=axis(rotate=True)),
            Representation=None if missing_body else shape,PredefinedType='INTERNAL')
        f.create_entity('IfcRelAggregates',GlobalId=ifc.guid.new(),RelatingObject=storey,RelatedObjects=[space])
        quantity=f.create_entity('IfcElementQuantity',GlobalId=ifc.guid.new(),Name='Qto_SpaceBaseQuantities',
             Quantities=[f.create_entity('IfcQuantityArea',Name='NetFloorArea',AreaValue=9999.)])
        f.create_entity('IfcRelDefinesByProperties',GlobalId=ifc.guid.new(),RelatedObjects=[space],RelatingPropertyDefinition=quantity)
        spaces.append(space); storeys.append(storey)
    f.create_entity('IfcRelAggregates',GlobalId=ifc.guid.new(),RelatingObject=building,RelatedObjects=storeys)
    f.write(str(path))
    return {'spaces':[s.GlobalId for s in spaces],'storeys':[s.GlobalId for s in storeys]}
