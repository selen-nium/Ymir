"""Read source PDF drawing commands in registered full-page pixel coordinates.

This is a geometry inventory, not room inference. PDF fills, clipping, layers,
and nested decorative paths do not establish room boundaries or physical scale.
"""
import hashlib
import json

from .native_text import _matrix, _point, _sha

VERSION = 'pdf-vectors-v1'


def read_pdf_vectors(view, cursor=0):
    if type(cursor) is not int or cursor < 0:
        raise ValueError('Invalid vector cursor')
    if not view.get('pdf_path'):
        return {'status':'unavailable','paths':[],'next_cursor':None,'reason':'No registered PDF source'}
    if _sha(view['pdf_path']) != view['pdf_sha256']:
        raise ValueError('PDF source hash mismatch')
    if view.get('pdf_page') != 0:
        raise ValueError('Expected registered single-page PDF')
    matrix=_matrix(view['pdf_to_page'])
    import pymupdf
    with pymupdf.open(view['pdf_path']) as doc:
        if len(doc)!=1:raise ValueError('Expected registered single-page PDF')
        drawings=doc[0].get_drawings()
    if cursor > len(drawings):raise ValueError('Cursor beyond vector coverage')
    result=[];budget=2000;index=cursor
    while index<len(drawings) and len(result)<50:
        path=drawings[index]
        if len(path['items'])>2000:
            # Advance with explicit unavailability, never silently truncate geometry.
            result.append({'source_path_index':index,'status':'unavailable','reason':'Path exceeds 2000-command limit'})
            index+=1;continue
        if len(path['items'])>budget:break
        commands=[];points=[]
        for item in path['items']:
            kind=item[0]
            if kind=='re':
                r=item[1];native=[(r.x0,r.y0),(r.x1,r.y0),(r.x1,r.y1),(r.x0,r.y1)]
            elif kind=='qu':
                q=item[1];native=[q.ul,q.ur,q.lr,q.ll]
            elif kind in ('l','c'):native=item[1:]
            else:raise ValueError('Unsupported PDF drawing command: '+kind)
            mapped=[list(_point(p,matrix)) for p in native]
            points.extend(mapped)
            commands.append({'command':kind,'points':mapped,**({'orientation':item[2]} if kind=='re' else {})})
        raw={'source_id':view['source_id'],'source_sha256':view['source_sha256'],
             'pdf_sha256':view['pdf_sha256'],'page':view['page'],'source_path_index':index,
             'commands':commands,'closed':bool(path.get('closePath')),
             'fill_rule':'evenodd' if path.get('even_odd') else 'nonzero',
             'fill':path.get('fill'),'stroke':path.get('color'),'extractor':VERSION}
        raw['geometry_id']='pdf-path-'+hashlib.sha256(json.dumps(raw,sort_keys=True,allow_nan=False).encode()).hexdigest()[:24]
        if points:
            bounds=[min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
            raw['bounds']=bounds
            raw['page_clipped']=bounds[0]<0 or bounds[1]<0 or bounds[2]>view['original_width'] or bounds[3]>view['original_height']
        result.append(raw);budget-=len(path['items']);index+=1
    return {'status':'available' if result else 'unavailable','paths':result,'total_paths':len(drawings),
            'next_cursor':index if index<len(drawings) else None,
            'coordinate_space':'registered_full_page_pixels','view_id':view['view_id'],
            'limitations':['Source drawing commands do not establish rooms, physical scale, clipping visibility, or complete boundary coverage. Cubic control points are preserved without approximation.']}
