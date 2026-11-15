"""Conservative SVG reader: selected closed linear shapes and explicit cutouts.

SVG viewport millimetres describe a page, not the building's real-world scale.
The caller must provide confirmed length evidence to obtain square metres.
"""
import math
import re

from defusedxml import ElementTree
from shapely.affinity import affine_transform
from shapely.geometry import Polygon

from .common import MAX_ROOMS, MAX_VERTICES, Unsupported, checked, record

NUMBER = r'[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?'
IDENTITY = (1., 0., 0., 1., 0., 0.)  # SVG a,b,c,d,e,f
SHAPES = {'path', 'polygon', 'polyline', 'rect'}


def tag(node):
    return node.tag.rsplit('}', 1)[-1]


def numbers(value):
    if re.sub(NUMBER, '', value).strip(' ,\t\r\n'):
        raise Unsupported('Unsupported numeric syntax or units in SVG geometry.')
    result = [float(x) for x in re.findall(NUMBER, value)]
    if not all(math.isfinite(x) for x in result):
        raise Unsupported('SVG coordinates must be finite.')
    return result


def multiply(left, right):
    a,b,c,d,e,f = left; g,h,i,j,k,l = right
    return (a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f)


def transform(value):
    matrix = IDENTITY
    rest = value
    while rest.strip(' ,\t\r\n'):
        match = re.match(r'\s*,?\s*(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^()]*)\)', rest)
        if not match:
            raise Unsupported('Unsupported SVG transform.')
        name, raw = match.groups(); p = numbers(raw)
        if name == 'matrix' and len(p) == 6: item = tuple(p)
        elif name == 'translate' and len(p) in (1,2): item = (1,0,0,1,p[0],p[1] if len(p)==2 else 0)
        elif name == 'scale' and len(p) in (1,2): item = (p[0],0,0,p[-1],0,0)
        elif name == 'rotate' and len(p) in (1,3):
            angle = math.radians(p[0]); c,s = math.cos(angle),math.sin(angle)
            item = (c,s,-s,c,0,0)
            if len(p)==3:
                item = multiply(multiply((1,0,0,1,p[1],p[2]), item), (1,0,0,1,-p[1],-p[2]))
        elif name in ('skewX','skewY') and len(p)==1:
            t = math.tan(math.radians(p[0])); item = (1,0,t,1,0,0) if name=='skewX' else (1,t,0,1,0,0)
        else: raise Unsupported('Invalid SVG transform arguments.')
        matrix = multiply(matrix, item); rest = rest[match.end():]
    if not all(math.isfinite(x) for x in matrix) or abs(matrix[0]*matrix[3]-matrix[1]*matrix[2]) < 1e-15:
        raise Unsupported('Singular or nonfinite SVG transform.')
    return matrix


def path_rings(value):
    tokens = re.findall(NUMBER + r'|[A-Za-z]', value)
    if re.sub(NUMBER + r'|[A-Za-z]', '', value).strip(' ,\t\r\n'):
        raise Unsupported('Malformed SVG path.')
    rings=[]; ring=[]; point=[0.,0.]; start=None; command=None; i=0
    def finish(explicit=False):
        if not ring:return
        if len(ring)<3 or (not explicit and math.dist(ring[0],ring[-1])>1e-9):
            raise Unsupported('Open SVG boundary; explicitly close the room outline.')
        rings.append(ring.copy())
    while i<len(tokens):
        if tokens[i].isalpha():
            command=tokens[i]; i+=1
            if command.upper() not in ('M','L','H','V','Z'):
                raise Unsupported('Curved SVG paths need review; curves are not approximated.')
            if command.upper()=='Z':
                finish(True); ring=[]; point=start.copy() if start else point; command=None; continue
        if command is None:raise Unsupported('Missing SVG path command.')
        count=1 if command.upper() in ('H','V') else 2
        if i+count>len(tokens) or any(t.isalpha() for t in tokens[i:i+count]):
            raise Unsupported('Incomplete SVG path command.')
        p=[float(t) for t in tokens[i:i+count]]; i+=count
        if not all(math.isfinite(x) for x in p):raise Unsupported('Nonfinite SVG coordinate.')
        relative=command.islower(); upper=command.upper()
        if upper in ('M','L'): target=[p[j]+(point[j] if relative else 0) for j in range(2)]
        elif upper=='H': target=[p[0]+(point[0] if relative else 0),point[1]]
        else: target=[point[0],p[0]+(point[1] if relative else 0)]
        if upper=='M':
            finish(); ring=[]; start=target.copy(); command='l' if relative else 'L'
        if not ring and upper!='M':raise Unsupported('Each SVG subpath must start with moveto.')
        point=target; ring.append(target)
        if len(ring)>MAX_VERTICES:raise Unsupported('SVG room exceeds the vertex limit.')
    finish()
    if not rings:raise Unsupported('No closed SVG boundary.')
    return rings


def polygon_from_rings(rings, fill_rule):
    polygons=sorted([checked(Polygon(r)) for r in rings],key=lambda p:p.area,reverse=True)
    outer=polygons[0]; holes=[]
    for i,p in enumerate(polygons[1:]):
        if not outer.contains(p) or any(p.intersects(q) for q in polygons[1:i+1]):
            raise Unsupported('Disconnected, touching or nested SVG subpaths need review.')
        if fill_rule=='evenodd' or p.exterior.is_ccw!=outer.exterior.is_ccw:holes.append(p.exterior.coords)
    return checked(Polygon(outer.exterior.coords,holes))


def shape_polygon(node, fill_rule):
    kind=tag(node)
    if kind=='path':return polygon_from_rings(path_rings(node.get('d','')),fill_rule)
    if kind in ('polygon','polyline'):
        n=numbers(node.get('points',''))
        if len(n)%2:raise Unsupported('Unpaired SVG polygon coordinates.')
        points=list(zip(n[::2],n[1::2]))
        if kind=='polyline' and (len(points)<3 or math.dist(points[0],points[-1])>1e-9):
            raise Unsupported('Open SVG polyline is not a room.')
        return checked(Polygon(points))
    if kind=='rect':
        def value(name,default='0'):
            n=numbers(node.get(name,default))
            if len(n)!=1:raise Unsupported('A numeric SVG rectangle dimension is required.')
            return n[0]
        if value('rx')!=0 or value('ry')!=0:raise Unsupported('Rounded SVG rectangles need review.')
        x,y,w,h=value('x'),value('y'),value('width'),value('height')
        if min(w,h)<=0:raise Unsupported('SVG rectangle has no area.')
        return checked(Polygon([(x,y),(x+w,y),(x+w,y+h),(x,y+h)]))
    raise Unsupported('Select a closed path, polygon, rectangle, or a group containing one boundary.')


def read(path, element_ids=None):
    raw=path.read_bytes()
    if re.search(br'<\?xml-stylesheet\b',raw,re.I):raise Unsupported('External SVG stylesheets need review.')
    root=ElementTree.fromstring(raw)
    if tag(root)!='svg':raise Unsupported('The document root must be SVG.')
    viewbox=numbers(root.get('viewBox',''))
    if len(viewbox)!=4 or min(viewbox[2:])<=0:raise Unsupported('A valid SVG viewBox is required for registration.')
    if any(tag(n)=='style' and (n.text or '').strip() for n in root.iter()):
        raise Unsupported('SVG stylesheets need review; export presentation attributes or inline styles.')
    if any(tag(n) in ('script','animate','animateTransform','animateMotion','set','foreignObject') for n in root.iter()):
        raise Unsupported('Dynamic or embedded SVG content needs a static geometry export.')
    parents={child:node for node in root.iter() for child in node}
    by_id={}
    for node in root.iter():
        if node.get('id'):
            if node.get('id') in by_id:raise Unsupported('Duplicate SVG element IDs are ambiguous.')
            by_id[node.get('id')]=node
    if element_ids is None:
        selected=[n for n in root.iter() if n.get('data-room-id') or 'IfcSpace' in n.get('class','').split()]
    else:
        selected=[by_id[x] for x in element_ids if x in by_id]
    if len(selected)>MAX_ROOMS:raise Unsupported('Too many selected room boundaries.')
    rooms=[]; rejected=[]
    for ident in element_ids or []:
        if ident not in by_id:rejected.append({'identifier':ident,'reason':'Selected SVG element was not found.'})
    for selected_node in selected:
        identifier=selected_node.get('data-room-id') or selected_node.get('id') or selected_node.get('data-guid')
        try:
            if not identifier:raise Unsupported('Selected SVG room needs an explicit source identifier.')
            node=selected_node
            if tag(node)=='g':
                choices=[n for n in node.iter() if tag(n) in SHAPES]
                if len(choices)!=1:raise Unsupported('A room group must contain exactly one explicit boundary shape.')
                node=choices[0]
            chain=[]; ancestor=node
            while ancestor is not None:chain.append(ancestor); ancestor=parents.get(ancestor)
            matrix=IDENTITY; fill_rule='nonzero'
            for ancestor in reversed(chain):
                attrs=dict(ancestor.attrib)
                if attrs.get('style'):
                    if '!important' in attrs['style']:raise Unsupported('SVG CSS priority rules need review.')
                    for declaration in attrs['style'].split(';'):
                        if declaration.strip():
                            key,value=declaration.split(':',1); attrs[key.strip()]=value.strip()
                            if key.strip() in ('x','y','width','height','rx','ry','d','translate','rotate','scale','transform-origin','offset-path'):
                                raise Unsupported('CSS geometry properties need review; export SVG geometry attributes.')
                if any(attrs.get(k,'none')!='none' for k in ('clip-path','mask','filter')):
                    raise Unsupported('Clipped, masked or filtered SVG geometry needs review.')
                if attrs.get('display')=='none' or attrs.get('visibility') in ('hidden','collapse') or float(attrs.get('opacity','1'))<=0:
                    raise Unsupported('The selected SVG boundary is hidden.')
                if 'transform' in attrs and attrs.get('style') and re.search(r'(?:^|;)\s*transform\s*:',attrs['style']):
                    raise Unsupported('CSS transforms need review; use the SVG transform attribute.')
                if tag(ancestor) in ('defs','symbol','clipPath','mask') or (tag(ancestor)=='svg' and ancestor is not root):
                    raise Unsupported('Non-rendered or nested SVG viewports need review.')
                matrix=multiply(matrix,transform(attrs.get('transform','')))
                fill_rule=attrs.get('fill-rule',fill_rule)
            if fill_rule not in ('nonzero','evenodd'):raise Unsupported('Unsupported SVG fill rule.')
            p=shape_polygon(node,fill_rule); a,b,c,d,e,f=matrix
            p=checked(affine_transform(p,[a,c,b,d,e,f]))
            x,y,w,h=viewbox
            if not (p.bounds[0]>=x-1e-8 and p.bounds[1]>=y-1e-8 and p.bounds[2]<=x+w+1e-8 and p.bounds[3]<=y+h+1e-8):
                raise Unsupported('Room boundary extends outside the SVG viewBox.')
            rooms.append({'identifier':identifier,'label':selected_node.get('data-name') or selected_node.get('aria-label') or identifier,
                          'polygon':record(p),'native_area':p.area,'coordinate_space':'svg_root_user_units'})
        except (Unsupported,ValueError,TypeError) as error:
            rejected.append({'identifier':identifier,'reason':str(error)})
    ids=[r['identifier'] for r in rooms]
    if len(ids)!=len(set(ids)):raise Unsupported('Duplicate SVG room identifiers are ambiguous.')
    return {'rooms':rooms,'rejections':rejected,'viewbox':viewbox,'metres_per_unit':None,
            'units':{'kind':'svg_user_units','physical_viewport_is_building_scale':False}}
