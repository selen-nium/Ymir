"""Build the requested local showcase once. Uses cached sources/outputs; no provider calls.

    PYTHONPATH=backend .venv/bin/python -u scripts/prepare_featured_samples.py /mnt/berstorage/stoa

Legacy samples and reports are retained. Activation happens after every new asset is prepared.
"""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import io
import json
import math
import shutil
import sys
from pathlib import Path
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from ymir.documents import align_build
from ymir.build import build_drawing
from ymir.build.floorplan.extract import normalize
from PIL import Image
import pymupdf

OUT=ROOT/'data/samples'
ASSETS=OUT/'featured'
STOA=Path(sys.argv[1] if len(sys.argv)>1 else '/mnt/berstorage/stoa')
ARCHIVE=STOA/'data/junction-dataset/junction-dataset.zip'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def put(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def trace(name,tool,summary):
    ident='prepared-'+hashlib.sha256(name.encode()).hexdigest()[:10]
    return [{'type':'node','id':ident,'name':name,'tool':tool,'parents':[]},{'type':'status','id':ident,'state':'done','summary':summary}]
def bundle_document(ident,source,fmt,project,dataset,filename=None):
    folder=ASSETS/'documents'/ident;folder.mkdir(parents=True,exist_ok=True)
    target=folder/('original.'+fmt)
    if not target.exists():shutil.copyfile(source,target)
    if sha(target)!=sha(source):raise ValueError('Bundled source changed: '+ident)
    pages=[]
    if fmt=='pdf':
        with pymupdf.open(target) as doc:pages=[{'number':i+1,'rotation':p.rotation,'size':[p.rect.width,p.rect.height],'native_text':bool(p.get_text())} for i,p in enumerate(doc)]
    elif fmt=='png':
        with Image.open(target) as im:pages=[{'number':1,'size':list(im.size),'native_text':False}]
    data={'id':ident,'filename':filename or source.name,'title':project,'format':fmt,'sha256':sha(target),'project_identity':project,'dataset':dataset,'exposure':'prepared_showcase; previously exposed diagnostic',
          'pages':pages,'page_count':len(pages),'selectors':[],'limitations':[]}
    put(folder/'document.json',data);return data,target

def save_sheet(ident,payload,image,events):
    payload={**payload,'id':ident,'image':{**payload['image'],'url':ident+'.png'}}
    if isinstance(image,bytes):(OUT/(ident+'.png')).write_bytes(image)
    else:image.save(OUT/(ident+'.png'),optimize=True)
    payload['lens_source']['image']=ident+'.png';payload['lens_source']['image_sha256']=sha(OUT/(ident+'.png'))
    put(OUT/(ident+'.json'),payload);put(OUT/(ident+'.trace.json'),events)
    return payload

def native_project(archive_member,ident,title,dataset):
    cached=ASSETS/(ident+'-project.json')
    if cached.exists():print('reuse',title,flush=True);return json.loads(cached.read_text())
    print('prepare IFC',title,flush=True)
    import ifcopenshell
    import ifcopenshell.util.element as ue
    import ifcopenshell.util.placement as up
    import ifcopenshell.util.unit as uu
    import numpy as np
    source=ASSETS/'originals'/Path(archive_member).name;source.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(ARCHIVE) as archive:
        source.write_bytes(archive.read(archive_member))
        family=archive_member.split('/')[2]
        licence=archive.read(f'junction-dataset/data/{family}/LICENSE.md').decode()
        credit_line=next(line for line in licence.splitlines() if line.startswith('| '+source.name+' |'))
    parts=[part.strip() for part in credit_line.split('|')]
    source_meta={'dataset':dataset,'file':'/'.join(archive_member.split('/')[2:]),'licence':parts[3],'credit':parts[4],'url':parts[5],'sha256':sha(source)}
    put(ASSETS/(ident+'-attribution.json'),source_meta)
    doc,_=bundle_document('doc-'+ident,source,'ifc',ident,dataset)
    spec=importlib.util.spec_from_file_location('ymir_ifc_renderer',ROOT/'scripts/build_ifc_project.py');renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)
    renderer.PROJECT={'id':ident,'title':title,'source':source_meta}
    def sheet_title(sheet,number,name,top=False):
        w,h=sheet.img.size;y=8 if top else h-46
        sheet.draw.rectangle([w-320,y,w-8,y+38],outline='#555')
        sheet.text((w-310,y+12),number+'  '+name,anchor='lm')
        sheet.text((w-310,y+28),title+' · IFC model',font=renderer.SMALL,anchor='lm',fill='#555')
    renderer.Sheet.title=sheet_title
    model=ifcopenshell.open(str(source));unit=uu.calculate_unit_scale(model)
    products=[el for el in model.by_type('IfcProduct') if not el.is_a('IfcOpeningElement') and not el.is_a('IfcSite') and not el.is_a('IfcAnnotation') and not el.is_a('IfcSpatialStructureElement')]
    geometry=renderer.shapes(model,products+list(model.by_type('IfcSpace')))
    allv=np.vstack([v for v,_ in geometry.values()]);frame=(float(allv[:,0].min()-2),float(allv[:,1].min()-2),float(allv[:,0].max()+2),float(allv[:,1].max()+2))
    storeys=sorted(model.by_type('IfcBuildingStorey'),key=lambda s:up.get_storey_elevation(s)*unit)
    levels=[];sheets=[]
    for i,storey in enumerate(storeys):
        elements=ue.get_decomposition(storey)
        if not any(e.id() in geometry and e.is_a('IfcWall') for e in elements):continue
        z=float(up.get_storey_elevation(storey)*unit);height=float(up.get_storey_elevation(storeys[i+1])*unit-z) if i+1<len(storeys) else 3.
        height=height if .5<height<12 else 3.
        sid=f'{ident}-l{i}';level=f'L{i}';name=storey.Name or f'Level {i}'
        sheet,walls,rooms=renderer.plan_sheet(frame,name,z,elements,geometry,f'A-{i+1:03}',name)
        if not walls:continue
        payload=renderer.payload(sid,name,sheet,f'A-{i+1:03}','floor_plan',walls,rooms,storey=name,z=z)
        for wall in payload['walls']:wall.pop('confidence',None)
        payload['scale'].pop('confidence',None);payload['scale']['m_per_px']=1/renderer.K
        payload['default_height_m']=height
        payload['source'].update(document_id=doc['id'],prepared=True,model_units_to_metres=unit,preparation='Cached IFC geometry',limitations=['Plan image rendered from the preserved IFC model.'])
        payload['lens_source']={'document_id':doc['id'],'source_sha256':doc['sha256'],'project_identity':ident,'dataset':dataset,'exposure':'prepared_showcase; not a holdout','source_page':1,'width':sheet.img.width,'height':sheet.img.height,'ifc_storey_id':storey.GlobalId}
        save_sheet(sid,payload,sheet.img,trace('Load prepared model','IfcOpenShell',f'{len(walls)} walls · {len(rooms)} rooms · native IFC units'))
        levels.append({'id':level,'name':name,'z_m':z,'height_m':height})
        sheets.append({'drawing_id':sid,'views':[{'kind':'floor_plan','kindSure':True,'discipline':'architecture','placement':{'type':'plan','level':level,'transform':[1/renderer.K,0,0,0],'by':'model'}}]})
        print('  cached',name,len(walls),'walls',flush=True)
    if not sheets:raise ValueError('No supported IFC storeys: '+ident)
    # Open a ground-level plan; all source storeys remain placed at their native elevations.
    base=min(range(len(levels)),key=lambda i:abs(levels[i]['z_m']))
    sheets=[sheets[base],*sheets[:base],*sheets[base+1:]]
    project={'id':ident,'title':title,'source':source_meta,'thumbnail':f"api/drawings/{sheets[0]['drawing_id']}/image",'levels':levels,'sheets':sheets}
    put(cached,project);return project


def raster_sheet(ident,title,source,doc,dataset,source_page=1,prior=None,crop=None):
    cached=OUT/(ident+'.json')
    if cached.exists():print('reuse',title,flush=True);return json.loads(cached.read_text())
    print('prepare sheet',title,flush=True)
    pdf=None;destination=ASSETS/'pages'/(ident+'.pdf');destination.parent.mkdir(parents=True,exist_ok=True)
    if source.suffix.lower()=='.pdf':
        with pymupdf.open(source) as packet,pymupdf.open() as one:
            one.insert_pdf(packet,from_page=source_page-1,to_page=source_page-1);one.save(destination)
            scale=min(2.,8000/max(one[0].rect.width,one[0].rect.height))
            pix=one[0].get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False,colorspace=pymupdf.csRGB)
            raw=pix.tobytes('png');pdf=destination
    else:raw=source.read_bytes()
    with Image.open(io.BytesIO(raw)) as image:size=image.size
    _,normalized=normalize(raw)
    built=build_drawing(raw,drawing_id=ident,title=title,dataset=dataset,kind='Prepared drawing',file=doc['filename'],crop=crop,read_text=False)
    payload=align_build(built.payload,size,(normalized['width'],normalized['height']))
    payload['kind']='floor_plan'
    payload['source'].update(document_id=doc['id'],sha256=doc['sha256'],prepared=True,credit='Aalto University / CubiCasa' if 'CubiCasa' in dataset else 'Archilyse AG · Swiss Dwellings v3.0.0' if 'Swiss' in dataset else 'OpeningDesign · Xcel Sports Complex project team',
                             licence='CC-BY-NC-SA-4.0' if 'CubiCasa' in dataset else 'CC-BY-4.0' if 'Swiss' in dataset else 'CC-BY-SA-4.0',preparation='Cached local geometry; source previously inspected' if prior else 'Cached local geometry')
    payload['lens_source']={'document_id':doc['id'],'source_sha256':doc['sha256'],'project_identity':doc['project_identity'],'dataset':dataset,'exposure':doc['exposure'],'source_page':source_page,'document_page_count':doc['page_count'],'packet_included_pages':1,'width':size[0],'height':size[1]}
    if pdf:
        payload['lens_source']['pdf']=str(pdf.relative_to(OUT));payload['lens_source']['pdf_sha256']=sha(pdf)
        with pymupdf.open(pdf) as document:
            page=document[0];scale=min(2.,8000/max(page.rect.width,page.rect.height));matrix=page.rotation_matrix*pymupdf.Matrix(scale,scale)
            payload['texts']=[{'id':f't{i}','text':block[4].strip(),'box':list(pymupdf.Rect(block[:4])*matrix),'provenance':'native_pdf'} for i,block in enumerate(page.get_text('blocks')) if len(block)>4 and isinstance(block[4],str) and block[4].strip()]
    if prior:
        receipt=json.loads(prior.read_text());payload['source']['prior_run']={'model':receipt.get('model'),'status':receipt.get('status'),'sha256':sha(prior)}
        target=ASSETS/'prior-runs'/(ident+'.json');put(target,{'model':receipt.get('model'),'status':receipt.get('status'),'output':receipt.get('output'),'selection':receipt.get('selection'),'source_sha256':doc['sha256'],'trajectory_sha256':sha(prior)})
        candidate=(receipt.get('selection') or {}).get('candidate')
        if candidate:
            payload['rooms'].append({'id':'prior-'+candidate['candidate_id'],'polygon':candidate['polygon']['exterior'],'holes':candidate['polygon'].get('holes',[]),'label':receipt['output'].get('visible_room_label') or 'Previously selected room','provenance':'saved model prediction; not a reference annotation'})
    events=built.trace
    if prior:events+=trace('Load prior room analysis',receipt.get('model') or 'Saved run','Saved prediction and provenance retained')
    if pdf:events+=trace('Read source text','Native PDF',f"{len(payload['texts'])} text blocks")
    save_sheet(ident,payload,raw,events)
    return payload


def main():
    ASSETS.mkdir(parents=True,exist_ok=True)
    projects=[native_project('junction-dataset/data/ifc_bench/ac20_arc.ifc','junction-ac20','AC20 institute building','Junction · IFC-Bench'),
              native_project('junction-dataset/data/ifc_bench/duplex_arc.ifc','junction-duplex','Duplex apartments','Junction · IFC-Bench'),
              native_project('junction-dataset/data/ifc_models/rwth-digitalhub_digitalhub-fm-arc-v2.ifc','junction-digitalhub','DigitalHub office building','Junction · IFC Models')]
    cubi=STOA/'data/raster2seq-cubicasa-eval/raw/high_quality_architectural/2536/F1_scaled.png'
    doc,_=bundle_document('doc-sample-cubicasa-2536',cubi,'png','cubicasa-2536','CubiCasa5K')
    raster_sheet('sample-cubicasa-2536','CubiCasa · apartment 2536',cubi,doc,'CubiCasa5K',prior=STOA/'data/cubicasa-vista-trial/trajectories/cubicasa-test-0001/trajectory.json')
    swiss=STOA/'data/room-area-specialists/native/sources/site584_bldg1298_floor5733.pdf'
    doc,_=bundle_document('doc-sample-swiss-5733',swiss,'pdf','swiss-site584-building1298','Swiss Dwellings')
    raster_sheet('sample-swiss-5733','Swiss Dwellings · fourth floor',swiss,doc,'Swiss Dwellings',prior=STOA/'data/room-area-specialists/trajectories/swiss-site584_bldg1298_floor5733/native-sol/trajectory.json')
    pilot=json.loads((STOA/'data/junction-project-pilot-v1/project.json').read_text());packet=Path(pilot['packet_path'])
    doc,_=bundle_document('doc-sample-sports-complex',packet,'pdf','opening-design-sports-complex','Junction · Opening Design','Xcel Sports Complex · Addendum 1.pdf')
    sheets=[]
    for sheet_id in ['A-101.A','A-101','A-101.B','A-701','A-801']:
        source=next(s for s in pilot['sheets'] if s['id']==sheet_id);sid='sports-'+sheet_id.lower().replace('.','-')
        payload=raster_sheet(sid,source['title'],packet,doc,'Junction · Opening Design',source['packet_page']+1)
        # Overlapping overall/detail views remain references; no fabricated cross-sheet placement.
        plan=sheet_id=='A-101.A'
        sheets.append({'drawing_id':sid,'views':[{'kind':'floor_plan' if plan else 'schedule' if sheet_id=='A-801' else 'detail','kindSure':True,'discipline':'architecture',
            'placement':{'type':'plan','level':'L0','transform':[payload['scale']['m_per_px'],0,0,0],'by':'model'} if plan else {'type':'attached','level':None}}]})
    projects.append({'id':'junction-sports-complex','title':'Xcel Sports Complex','source':{'dataset':'Junction · Opening Design','credit':'OpeningDesign · Xcel Sports Complex project team','licence':'CC-BY-SA-4.0','file':pilot['archive']['path']},
        'thumbnail':'api/drawings/sports-a-101-a/image','levels':[{'id':'L0','name':'First floor','z_m':0,'height_m':3}],'sheets':sheets})
    put(OUT/'featured-projects.json',projects)
    manifest={'drawings':['sample-cubicasa-2536','sample-swiss-5733'],'projects':[p['id'] for p in projects],
        'selection_reason':'User-requested Ymir showcase: Junction IFC-Bench, IFC Models, Opening Design, CubiCasa and Swiss Dwellings. Reuses prior Sol sources where available; no benchmark sampling or new inference.',
        'legacy_samples_preserved':True}
    put(OUT/'featured-catalog.json',manifest)
    print('activated',manifest['drawings'],manifest['projects'],flush=True)

if __name__=='__main__':main()
