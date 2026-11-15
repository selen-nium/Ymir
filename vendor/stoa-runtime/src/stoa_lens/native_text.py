"""Positioned native PDF text for registered Lens views; no raster recognition."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def _sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):digest.update(block)
    return digest.hexdigest()


def _matrix(value):
    if isinstance(value,dict):
        if 'affine' in value:value=value['affine']
        else:value=[value['scale_x'],0,0,value['scale_y'],value['origin_x'],value['origin_y']]
    if not isinstance(value,(list,tuple)) or len(value)!=6 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in value):
        raise ValueError('Expected finite six-element affine transform')
    a,b,c,d,e,f=map(float,value)
    if abs(a*d-b*c)<1e-12:raise ValueError('Singular affine transform')
    return a,b,c,d,e,f


def _point(p,m):
    x,y=p;a,b,c,d,e,f=m
    return a*x+c*y+e,b*x+d*y+f


def _inverse(m):
    a,b,c,d,e,f=m;det=a*d-b*c
    return (d/det,-b/det,-c/det,a/det,(c*f-d*e)/det,(b*e-a*f)/det)


def _clip(poly,axis,bound,greater):
    if not poly:return []
    def inside(p):return p[axis]>=bound if greater else p[axis]<=bound
    out=[]
    for a,b in zip(poly[-1:]+poly[:-1],poly):
        ia,ib=inside(a),inside(b)
        if ia!=ib:
            t=(bound-a[axis])/(b[axis]-a[axis])
            out.append((a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])))
        if ib:out.append(b)
    return out


def _view_overlap(poly,width,height,view_matrix):
    # Transform the text quadrilateral back to view coordinates. Unlike a
    # native axis-aligned bounding box this is correct for rotated views.
    view_poly=[_point(p,_inverse(view_matrix)) for p in poly]
    for axis,bound,greater in ((0,0,True),(0,width,False),(1,0,True),(1,height,False)):
        view_poly=_clip(view_poly,axis,bound,greater)
    area=abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(view_poly,view_poly[1:]+view_poly[:1])))/2
    return area>1e-9


def _positive_number(value,name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
        raise ValueError('Invalid '+name)
    return float(value)


def _id(view,text,box):
    payload=json.dumps([view['source_sha256'],view['page'],text,box],ensure_ascii=False,separators=(',',':'),allow_nan=False)
    return 'text-'+hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]


def read_pdf_text(view,cursor=0):
    """Return at most 100 PDF lines with native-page boxes and provenance.

    ``pdf_page`` is the page in the ingested one-page PDF. ``page`` is the
    original document's zero-based page and remains part of each evidence ID.
    The supplied PDF-to-page matrix must already include page rotation and crop.
    """
    if isinstance(cursor,bool) or not isinstance(cursor,int) or cursor<0:raise ValueError('Invalid cursor')
    pdf=view.get('pdf_path')
    if not pdf:
        if cursor:raise ValueError('Cursor beyond text coverage')
        return {'status':'native_text_unavailable','source':None,'lines':[],'total':0,'remaining':0,'next_cursor':None}
    for key in ('pdf_sha256','pdf_to_page','source_sha256','source_id','page_id','view_id','page','pdf_page','transform','width','height','original_width','original_height'):
        if key not in view or view[key] is None:raise ValueError('Missing registered view field: '+key)
    for key in ('page','pdf_page'):
        if isinstance(view[key],bool) or not isinstance(view[key],int) or view[key]<0:raise ValueError('Invalid '+key)
    if view['pdf_page']!=0:raise ValueError('Ingested single-page PDF requires pdf_page 0')
    for key in ('pdf_sha256','source_sha256'):
        value=view[key]
        if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ValueError('Invalid '+key)
    if _sha(pdf)!=view['pdf_sha256']:raise ValueError('PDF source hash mismatch')
    width=_positive_number(view['width'],'view width');height=_positive_number(view['height'],'view height')
    native_width=_positive_number(view['original_width'],'native width');native_height=_positive_number(view['original_height'],'native height')
    pdf_to_page=_matrix(view['pdf_to_page']);view_to_page=_matrix(view['transform'])
    import pymupdf
    lines=[]
    with pymupdf.open(pdf) as doc:
        if len(doc)!=1:raise ValueError('Expected an ingested single-page PDF')
        for block in doc[view['pdf_page']].get_text('dict')['blocks']:
            for line in block.get('lines',[]):
                text=''.join(span['text'] for span in line.get('spans',[]))
                if not text.strip():continue
                bbox=line.get('bbox',())
                if len(bbox)!=4 or any(not math.isfinite(float(x)) for x in bbox):raise ValueError('Invalid native text position')
                x0,y0,x1,y1=map(float,bbox)
                if x0>=x1 or y0>=y1:raise ValueError('Invalid native text box')
                polygon=[_point(p,pdf_to_page) for p in ((x0,y0),(x1,y0),(x1,y1),(x0,y1))]
                if any(not math.isfinite(x) or not math.isfinite(y) for x,y in polygon):raise ValueError('Invalid mapped text position')
                if not _view_overlap(polygon,width,height,view_to_page):continue
                raw=[min(x for x,y in polygon),min(y for x,y in polygon),max(x for x,y in polygon),max(y for x,y in polygon)]
                box=[max(0.,raw[0]),max(0.,raw[1]),min(native_width,raw[2]),min(native_height,raw[3])]
                if box[0]>=box[2] or box[1]>=box[3]:continue
                lines.append({'id':_id(view,text,box),'text':text,'box':box,'raw_box':raw,'page_clipped':box!=raw,
                    'source':'native_pdf','confidence':None,'source_sha256':view['source_sha256'],
                    'source_id':view['source_id'],'page_id':view['page_id'],'page':view['page'],
                    'pdf_page':view['pdf_page'],'pdf_sha256':view['pdf_sha256'],'view_id':view['view_id']})
    if cursor>len(lines):raise ValueError('Cursor beyond text coverage')
    end=min(cursor+100,len(lines))
    return {'status':'ok' if lines else 'native_text_unavailable','source':'native_pdf' if lines else None,
            'lines':lines[cursor:end],'total':len(lines),'remaining':len(lines)-end,
            'next_cursor':end if end<len(lines) else None}
