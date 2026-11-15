"""Source-pinned extraction, bounded preparation, and VISTA registration."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, field_validator
from shapely.affinity import affine_transform
from shapely.geometry import Polygon

from .common import VERSION, MAX_ROOMS, UNITS, Unsupported, checked, digest, record, sha


class SvgScale(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    p0: tuple[float,float]
    p1: tuple[float,float]
    length: float=Field(gt=0,le=1e9)
    unit: str
    reviewer: str=Field(min_length=1,max_length=120)
    reading: str=Field(min_length=1,max_length=1000)

    @field_validator('reviewer','reading')
    @classmethod
    def nonempty(cls,value):
        if not value.strip():raise ValueError('A reviewer and visible length reading are required.')
        return value.strip()


class Options(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    element_ids: list[str] | None=Field(default=None,min_length=1,max_length=MAX_ROOMS)
    storey_id: str | None=Field(default=None,min_length=1,max_length=100)
    svg_scale: SvgScale | None=None
    dxf_unit: str | None=None

    @field_validator('element_ids')
    @classmethod
    def identifiers(cls,value):
        if value is not None and (len(value)!=len(set(value)) or any(not v.strip() or len(v)>1000 for v in value)):
            raise ValueError('Select unique, nonempty source identifiers.')
        return value


def extract(path, options=None):
    """Return native polygons and measured areas; incomplete scope stays explicit."""
    path=Path(path).resolve(); opts=Options.model_validate(options or {})
    if path.suffix.lower() not in ('.ifc','.svg','.dxf'):raise Unsupported('Use an IFC, SVG or DXF source.')
    if not path.is_file() or not 0<path.stat().st_size<=100*1024*1024:
        raise Unsupported('Native input must be a nonempty file of at most 100 MiB.')
    source_sha=sha(path); kind=path.suffix.lower()[1:]
    result={'schema':VERSION,'format':kind,'native_sha256':source_sha,'options':opts.model_dump(mode='json'),
            'measurement_convention':'Horizontal area inside represented room boundaries, minus represented cutouts. No fixture subtraction or wall-based room inference.',
            'rooms':[],'rejections':[],'quality_flags':[],'status':'unavailable'}
    try:
        if kind!='dxf' and opts.dxf_unit:raise Unsupported('dxf_unit applies only to DXF sources.')
        if kind=='dxf':
            if opts.storey_id or opts.svg_scale:raise Unsupported('DXF uses entity selection and DXF units.')
            from .dxf import read
            data=read(path,opts.element_ids,opts.dxf_unit)
            if data['metres_per_unit'] is None:result['quality_flags'].append('scale_confirmation_required')
        elif kind=='svg':
            if opts.storey_id:raise Unsupported('Select SVG room IDs; storey_id is an IFC selector.')
            from .svg import read
            data=read(path,opts.element_ids)
            if opts.svg_scale:
                s=opts.svg_scale; x,y,w,h=data['viewbox']
                if s.unit not in UNITS:raise Unsupported('Unsupported confirmed length unit.')
                if any(not (x<=p[0]<=x+w and y<=p[1]<=y+h) for p in (s.p0,s.p1)):
                    raise Unsupported('Confirmed length endpoints must be in SVG root coordinates inside the viewBox.')
                distance=math.dist(s.p0,s.p1)
                if distance<=1e-9:raise Unsupported('Two distinct confirmed length endpoints are required.')
                factor=s.length*UNITS[s.unit]/distance
                if not math.isfinite(factor*factor) or factor*factor<=0:raise Unsupported('Invalid confirmed drawing scale.')
                # A length reading is evidence; a printed area cannot establish scale.
                import re
                if re.search(r'(?<![A-Za-z])(?:mm|cm|m|ft|in)\s*(?:²|\^\s*2|2)(?![A-Za-z])|\bsq\.?|\barea\b',s.reading,re.I):
                    raise Unsupported('Confirm a visible length, not a printed area.')
                data['metres_per_unit']=factor
                data['units']['confirmation']={**s.model_dump(mode='json'),'native_sha256':source_sha}
            else:result['quality_flags'].append('scale_confirmation_required')
        else:
            if opts.svg_scale:raise Unsupported('IFC uses verified model units; svg_scale applies only to SVG.')
            from .ifc import read
            data=read(path,opts.element_ids,opts.storey_id)
            levels={round(r['floor_elevation_m'],5) for r in data['rooms']}
            if len(levels)>1:result['quality_flags'].append('multiple_floor_elevations_select_one_level')
        result.update(data)
        for room in result['rooms']:
            room['quality_flags']=[]
            factor=data['metres_per_unit']
            room['area_m2']=room['native_area']*factor**2 if factor is not None else None
        # Distinct physical rooms must not silently overlap in the same floor.
        rooms=result['rooms']
        for i,a in enumerate(rooms):
            p=Polygon(a['polygon']['exterior'],a['polygon']['holes'])
            for b in rooms[i+1:]:
                if kind=='ifc' and abs(a['floor_elevation_m']-b['floor_elevation_m'])>1e-5:continue
                q=Polygon(b['polygon']['exterior'],b['polygon']['holes'])
                if p.intersection(q).area>max(1e-10,min(p.area,q.area)*1e-7):
                    for r in (a,b):
                        if 'overlapping_room_boundaries' not in r['quality_flags']:r['quality_flags'].append('overlapping_room_boundaries')
        if not rooms:result['quality_flags'].append('no_explicit_room_boundaries')
        result['status']='ready' if rooms and not result['rejections'] and not result['quality_flags'] and not any(r['quality_flags'] for r in rooms) else 'needs_review'
    except (Unsupported,ValueError,RuntimeError) as error:
        result['rejections'].append({'identifier':None,'reason':str(error)})
    if sha(path)!=source_sha:raise Unsupported('Native source changed during extraction.')
    return result


def _prepare_worker(request, folder):
    """Only the bounded child opens potentially expensive IFC geometry."""
    folder=Path(folder); path=folder/request['filename']
    if sha(path)!=request['native_sha256']:raise Unsupported('Uploaded native source changed.')
    result=extract(path,request['options'])
    result['source_metadata']=request['metadata']
    artifact=folder/'extraction.json'; artifact.write_text(json.dumps(result,indent=2,allow_nan=False))
    source=None
    if result['rooms']:
        polygons=[Polygon(r['polygon']['exterior'],r['polygon']['holes']) for r in result['rooms']]
        xmin=min(p.bounds[0] for p in polygons); ymin=min(p.bounds[1] for p in polygons)
        xmax=max(p.bounds[2] for p in polygons); ymax=max(p.bounds[3] for p in polygons)
        scale=1536/max(xmax-xmin,ymax-ymin)
        width=max(64,math.ceil((xmax-xmin)*scale)+64); height=max(64,math.ceil((ymax-ymin)*scale)+64)
        flip=result['format'] in ('ifc','dxf')
        matrix=[scale,0,0,-scale if flip else scale,32-xmin*scale,32+(ymax if flip else -ymin)*scale]
        image=Image.new('RGB',(width,height),'white')
        displayed=[affine_transform(p,matrix) for p in polygons]
        for p in displayed:
            mask=Image.new('L',image.size,0); pen=ImageDraw.Draw(mask)
            pen.polygon(list(p.exterior.coords),fill=255)
            for hole in p.interiors:pen.polygon(list(hole.coords),fill=0)
            image.paste('#E8F1F8',(0,0,width,height),mask)
        draw=ImageDraw.Draw(image)
        for room,p in zip(result['rooms'],displayed):
            for ring in [p.exterior,*p.interiors]:draw.line(list(ring.coords),fill='#233B4F',width=2)
            point=p.representative_point(); draw.text((point.x,point.y),room['label'][:50],fill='#142536')
        preview=folder/'rooms.png'; image.save(preview)
        result['preview_registration']={'native_to_page':matrix,'width':width,'height':height,'image_sha256':sha(preview)}
        result['source_metadata']=request['metadata']
        artifact.write_text(json.dumps(result,indent=2,allow_nan=False))
        source={'source_id':'native-'+digest({'sha':request['native_sha256'],'options':request['options'],'metadata':request['metadata']})[:24],
                'source_sha256':sha(preview),'path':str(preview.resolve()),'width':width,'height':height,'page':0,
                **request['metadata'],
                'rendering_scope':'Generated preview of extracted room footprints; not the original drawing or a complete building plan.',
                'native_geometry':{'schema':VERSION,'path':str(path.resolve()),'native_sha256':request['native_sha256'],
                    'extraction_path':str(artifact.resolve()),'extraction_sha256':sha(artifact),'native_to_page':matrix}}
    receipt={'extraction':result,'source':source}
    (folder/'result.json').write_text(json.dumps(receipt,indent=2,allow_nan=False))
    return receipt


def prepare(path, folder, *, options=None, project_identity=None, dataset=None, exposure='user_upload', timeout=120):
    """Snapshot the source and prepare a registered preview in a bounded child."""
    path=Path(path).resolve(); folder=Path(folder).resolve()
    opts=Options.model_validate(options or {}).model_dump(mode='json')
    if path.suffix.lower() not in ('.ifc','.svg','.dxf'):raise Unsupported('Use an IFC, SVG or DXF file.')
    if not path.is_file() or not 0<path.stat().st_size<=100*1024*1024:raise Unsupported('Use a native source of at most 100 MiB.')
    if not math.isfinite(timeout) or timeout<=0:raise ValueError('A positive finite extraction deadline is required.')
    folder.mkdir(parents=True,exist_ok=True)
    if (folder/'request.json').exists():raise FileExistsError('Use a fresh output directory to preserve prior extraction.')
    filename='source'+path.suffix.lower(); target=folder/filename
    shutil.copyfile(path,target)
    request={'filename':filename,'native_sha256':sha(target),'options':opts,
             'metadata':{'project_identity':project_identity or 'upload-'+sha(target)[:20], 'dataset':dataset,'exposure':exposure}}
    (folder/'request.json').write_text(json.dumps(request,indent=2))
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]),OMP_NUM_THREADS='1')
    process=subprocess.Popen([sys.executable,'-m','stoa_native_geometry','--worker',str(folder)],
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,start_new_session=True)
    try:
        out,err=process.communicate(timeout=timeout)
    except BaseException as error:
        from stoa_vista_specialists.geometry.lifecycle import terminate_and_collect
        cleanup=terminate_and_collect(process,error)
        (folder/'failure.json').write_text(json.dumps({'reason':'Extraction interrupted or timed out','cleanup':cleanup}))
        raise
    (folder/'worker.log').write_text(out+err)
    if process.returncode:raise Unsupported('Native extraction failed; original source and worker diagnostics were retained.')
    return json.loads((folder/'result.json').read_text())


class RegisteredNativeBackend:
    """Verify file/preview/extraction identity before exposing candidates to VISTA."""
    def __init__(self, source):
        self.source=copy.deepcopy(source)
        self.sources={source['source_id']:{'image_sha256':source['source_sha256']}}

    def candidates(self, source_id, page, room_identifier=None):
        s=self.source; registration=s['native_geometry']
        if source_id!=s['source_id'] or page!=0 or s.get('page',0)!=0:
            raise ValueError('Native source/page mismatch.')
        for path,expected in [(s['path'],s['source_sha256']),(registration['path'],registration['native_sha256']),
                              (registration['extraction_path'],registration['extraction_sha256'])]:
            if sha(path)!=expected:raise ValueError('Registered native source, preview or extraction changed.')
        with Image.open(s['path']) as im:
            if im.size!=(s['width'],s['height']):raise ValueError('Native preview dimensions changed.')
        data=json.loads(Path(registration['extraction_path']).read_text())
        if data['native_sha256']!=registration['native_sha256']:raise ValueError('Extraction source hash mismatch.')
        if data.get('preview_registration')!={'native_to_page':registration['native_to_page'],'width':s['width'],'height':s['height'],'image_sha256':s['source_sha256']}:
            raise ValueError('Native preview transform differs from its extraction receipt.')
        if data['source_metadata']!={k:s.get(k) for k in ('project_identity','dataset','exposure')}:
            raise ValueError('Native source provenance differs from its extraction receipt.')
        matrix=registration['native_to_page']
        a,b,d,e,xoff,yoff=matrix; scale=math.hypot(a,d)
        if not all(math.isfinite(v) for v in matrix) or scale<=0 or not math.isclose(scale,math.hypot(b,e),rel_tol=1e-9) or abs(a*b+d*e)>scale**2*1e-9:
            raise ValueError('Native preview registration must be a uniform similarity.')
        mpu=data.get('metres_per_unit'); calibration=None
        if mpu is not None:
            calibration={'kind':{'ifc':'native_ifc_units','dxf':'native_dxf_units','svg':'human_confirmed_svg_length'}[data['format']],
                         'metres_per_pixel':mpu/scale,'units':data['units'],'native_sha256':data['native_sha256'],
                         'source_id':source_id,'source_sha256':s['source_sha256'],'page':page}
            calibration['calibration_id']='native-cal-'+digest(calibration)[:24]
        candidates=[]
        rows=[r for r in data['rooms'] if room_identifier is None or r['identifier']==room_identifier]
        for r in rows:
            p=checked(affine_transform(Polygon(r['polygon']['exterior'],r['polygon']['holes']),matrix))
            if not (0<=p.bounds[0]<=p.bounds[2]<=s['width'] and 0<=p.bounds[1]<=p.bounds[3]<=s['height']):
                raise ValueError('Native polygon falls outside its registered preview.')
            flags=r['quality_flags']+[f for f in data['quality_flags'] if f!='scale_confirmation_required']
            candidate={'candidate_id':'native-'+digest({'source_id':source_id,'room':r})[:24],
                'polygon':record(p),'quality_flags':flags,'calibration':calibration,
                'native_area_m2':r['area_m2'],'estimate':r.get('estimate',False),'limitations':r.get('limitations',[]),
                'room_identifier':r['identifier'],'label':r['label'],
                'provenance':{'backend':VERSION,'format':data['format'],'source_id':source_id,'source_sha256':s['source_sha256'],'page':0,
                    'native_source_sha256':data['native_sha256'],'extraction_sha256':registration['extraction_sha256'],
                    'native_to_page':matrix,'coordinate_space':'original_full_page_pixels',
                    'project_identity':s['project_identity'],'dataset':s.get('dataset'),'exposure':s['exposure'],
                    'source_identifier':r['identifier'],'measurement_convention':data['measurement_convention']}}
            candidates.append(candidate)
        return {'status':data['status'] if rows else 'unavailable','candidates':candidates,
                'quality_flags':data['quality_flags'],'rejections':data['rejections'],
                'rendering_scope':s['rendering_scope']}
