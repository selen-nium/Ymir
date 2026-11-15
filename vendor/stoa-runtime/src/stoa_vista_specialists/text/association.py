"""Conservative room/text association with holes and explicit ambiguity."""

def _inside(point, poly):
    x,y = point
    inside = False
    for a,b in zip(poly, poly[1:] + poly[:1]):
        if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            inside = not inside
    return inside


def _clip(poly, axis, bound, keep_greater):
    if not poly: return []
    output = []
    def valid(p): return p[axis] >= bound if keep_greater else p[axis] <= bound
    for a,b in zip(poly[-1:] + poly[:-1], poly):
        va,vb = valid(a),valid(b)
        if va != vb:
            t = (bound-a[axis])/(b[axis]-a[axis])
            output.append([a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])])
        if vb: output.append(list(b))
    return output


def _area(poly):
    return abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(poly,poly[1:]+poly[:1])))/2


def _overlap(poly, box):
    p = list(poly)
    for axis,bound,gt in ((0,box[0],True),(0,box[2],False),(1,box[1],True),(1,box[3],False)):
        p = _clip(p,axis,bound,gt)
    return _area(p)


def associate_text(lines, rooms):
    """Associate only if exactly one polygon contains centre and >= half box area.

    Rooms carry id, polygon (exterior coordinates) and optional holes. Ambiguous
    overlap is preserved, with no forced assignment.
    """
    from shapely.geometry import Polygon, Point, box as shape_box
    polygons=[]
    for room in rooms:
        polygon=Polygon(room['polygon'],room.get('holes',[]))
        if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
            raise ValueError('invalid room polygon')
        polygons.append((room['id'],polygon))
    result=[]
    for line in lines:
        x0,y0,x1,y1=line['box']
        if x1 <= x0 or y1 <= y0: raise ValueError('invalid text box')
        text_box=shape_box(x0,y0,x1,y1); center=Point((x0+x1)/2,(y0+y1)/2)
        candidates=[ident for ident,polygon in polygons
                    if polygon.contains(center) and polygon.intersection(text_box).area/text_box.area >= .5]
        result.append({'text_id':line['id'], 'room_id':candidates[0] if len(candidates)==1 else None,
                       'status':'associated' if len(candidates)==1 else 'ambiguous' if candidates else 'unassociated',
                       'candidate_room_ids':candidates})
    return result
