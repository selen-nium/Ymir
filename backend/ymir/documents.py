"""Original-preserving document intake and canonical, source-bound sheets."""
from __future__ import annotations

import copy
import io
import json
import math
import secrets
from pathlib import Path

from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field
from stoa_lens.registry import sha
from stoa_native_geometry.backend import Options
from . import settings

MAX_ORIGINAL_IMAGE_PIXELS = 120_000_000
MAX_SHEET_IMAGE_PIXELS = 30_000_000
MAX_SHEET_IMAGE_SIDE = 8000


def check_image_size(width, height):
    if width * height > MAX_ORIGINAL_IMAGE_PIXELS:
        raise ValueError(f'Image resolution is {width * height / 1_000_000:.1f} megapixels ({width:,} × {height:,} pixels). '
                         f'The limit is {MAX_ORIGINAL_IMAGE_PIXELS / 1_000_000:g} megapixels; use a smaller image or the source PDF.')


def prepare_raster(original, target):
    """Keep the uploaded bytes; bound the working raster and register its source coordinates."""
    with Image.open(original) as im:
        width,height=im.size;check_image_size(width,height)
        orientation=im.getexif().get(274,1)
        image=ImageOps.exif_transpose(im)
        ow,oh=image.size
        ratio=min(1.,MAX_SHEET_IMAGE_SIDE/max(ow,oh),math.sqrt(MAX_SHEET_IMAGE_PIXELS/(ow*oh)))
        size=(max(1,int(ow*ratio)),max(1,int(oh*ratio)))
        if size!=image.size:
            if image.mode in ('1','P'):image=image.convert('RGBA' if 'transparency' in image.info else 'RGB')
            image.thumbnail(size,Image.Resampling.LANCZOS)
        if image.mode in ('RGBA','LA') or 'transparency' in image.info:
            rgba=image.convert('RGBA');white=Image.new('RGBA',image.size,'white');white.alpha_composite(rgba);image=white.convert('RGB')
        else:image=image.convert('RGB')
        image.save(target)
        sw,sh=image.size;sx,sy=sw/ow,sh/oh
    # Continuous image-edge coordinates match the xyxy box convention.
    exif={1:[1,0,0,1,0,0],2:[-1,0,0,1,width,0],3:[-1,0,0,-1,width,height],4:[1,0,0,-1,0,height],
          5:[0,1,1,0,0,0],6:[0,1,-1,0,height,0],7:[0,-1,-1,0,height,width],8:[0,-1,1,0,0,width]}
    a,b,c,d,e,f=exif.get(orientation,exif[1])
    matrix=[a*sx,b*sy,c*sx,d*sy,e*sx,f*sy]
    a,b,c,d,e,f=matrix;det=a*d-b*c
    inverse=[d/det,-b/det,-c/det,a/det,(c*f-d*e)/det,(b*e-a*f)/det]
    return {'original_size':[width,height],'oriented_size':[ow,oh],'sheet_size':[sw,sh],
            'exif_orientation':orientation,'original_to_sheet':matrix,'sheet_to_original':inverse,'resized':size!=(ow,oh)}


class SheetSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pages: list[int] | None = Field(default=None, min_length=1, max_length=200)
    options: Options = Field(default_factory=Options)


def transform_point(p, matrix):
    a, b, c, d, e, f = matrix
    return [a*p[0]+c*p[1]+e, b*p[0]+d*p[1]+f]


def transform_box(box, matrix):
    points = [transform_point(p, matrix) for p in ((box[0],box[1]),(box[2],box[1]),(box[2],box[3]),(box[0],box[3]))]
    return [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]


def pdf_image_page_overflows(page):
    """Find image XObject placements extending beyond the visible PDF page.

    MuPDF reports image rectangles in unrotated page coordinates; page.rect is
    rotated. Transform before comparison so rotation and crop remain consistent.
    This does not certify coverage within the page or inspect clipping paths.
    """
    visible = page.rect
    tolerance = 1e-3
    records = []
    for xref in sorted({image[0] for image in page.get_images(full=True)}):
        for rect in page.get_image_rects(xref):
            displayed = rect * page.rotation_matrix
            if (displayed.x0 < visible.x0-tolerance or displayed.y0 < visible.y0-tolerance or
                displayed.x1 > visible.x1+tolerance or displayed.y1 > visible.y1+tolerance):
                records.append({'xref':xref, 'image_rect_points':list(displayed), 'page_rect_points':list(visible),
                                'coordinate_space':'rotated_page_points'})
    return records


def align_build(payload, source_size, normalized_size):
    """Undo Build crop/resize. Every frontend coordinate uses canonical pixels."""
    out = copy.deepcopy(payload)
    x, y, _, _ = out['source']['crop']
    sx, sy = source_size[0]/normalized_size[0], source_size[1]/normalized_size[1]
    matrix = [sx,0,0,sy,x*sx,y*sy]
    for w in out['walls']:
        for k in ('a','b'): w[k] = transform_point(w[k],matrix)
        if w.get('thickness_px'): w['thickness_px'] *= math.sqrt(sx*sy)
    for r in out['rooms']:
        r['polygon'] = [transform_point(p,matrix) for p in r['polygon']]
        r['holes'] = [[transform_point(p,matrix) for p in ring] for ring in r.get('holes',[])]
        if r.get('label_box'): r['label_box'] = transform_box(r['label_box'],matrix)
    for k in ('texts','symbols','openings','plans'):
        for item in out.get(k,[]):
            if item.get('box'): item['box'] = transform_box(item['box'],matrix)
    out['scale']['m_per_px'] /= math.sqrt(sx*sy)
    out['image']['size'] = list(source_size)
    out['source']['build_to_source'] = matrix
    out['source']['source_to_build'] = [1/sx,0,0,1/sy,-x,-y]
    return out


class DocumentStore:
    def __init__(self, drawings, root=None):
        self.drawings=drawings
        self.root=Path(root or settings.DATA_DIR/'documents').resolve()

    def folder(self, ident):
        from .drawings import ID
        if not ID.fullmatch(ident):raise KeyError(ident)
        for folder in (self.root/ident,self.drawings.samples/'featured/documents'/ident):
            if (folder/'document.json').is_file():return folder
        raise KeyError(ident)

    def get(self, ident):
        return json.loads((self.folder(ident)/'document.json').read_text())

    def original(self, ident):
        d=self.get(ident)
        path=self.folder(ident)/('original.'+d['format'])
        if sha(path)!=d['sha256']: raise ValueError('Original document hash changed')
        return path

    def upload(self, raw, filename, project_identity=None, dataset='Upload', exposure='user_upload'):
        ext=Path(filename).suffix.lower().lstrip('.')
        if ext=='jpeg':ext='jpg'
        if ext not in ('png','jpg','webp','pdf','ifc','svg'):raise ValueError('Use a PDF, PNG, JPEG, WebP, IFC or SVG file.')
        ident='doc-'+secrets.token_hex(10);folder=self.root/ident;folder.mkdir(parents=True)
        path=folder/('original.'+ext);path.write_bytes(raw)
        data={'id':ident,'filename':Path(filename).name,'title':Path(filename).stem[:80], 'format':ext,'sha256':sha(path),
              'project_identity':project_identity or ident,'dataset':dataset,'exposure':exposure,'pages':[], 'selectors':[], 'limitations':[]}
        # Retain malformed originals as intake failures, never silently replace them.
        try:
            if ext=='pdf':
                import pymupdf
                with pymupdf.open(path) as doc:
                    if doc.needs_pass:raise ValueError('Unlock the PDF before importing it.')
                    if not 0<len(doc)<=1000:raise ValueError('Use a PDF of 1 to 1000 pages.')
                    for i,p in enumerate(doc):
                        clipped = pdf_image_page_overflows(p)
                        data['pages'].append({'number':i+1,'rotation':p.rotation,'size':[p.rect.width,p.rect.height],
                                              'native_text':bool(p.get_text().strip()),'off_page_image_placements':clipped})
                        if clipped:
                            data['limitations'].append(f'Page {i+1} has embedded image placements outside the visible PDF page; the page rendering may omit part or all of those images.')
            elif ext in ('ifc','svg'):
                if ext=='svg':
                    from defusedxml import ElementTree
                    root=ElementTree.fromstring(raw)
                    data['selectors']=[{'id':n.get('id'),'label':n.get('data-name') or n.get('aria-label') or n.get('id')} for n in root.iter() if n.get('id') and n.tag.rsplit('}',1)[-1] in ('path','polygon','polyline','rect','g')]
                    data['limitations']=['Select explicit room boundaries. Curves, stylesheets, masks and unsupported geometry remain unavailable.', 'Measurements require a confirmed visible length in SVG root coordinates.']
                else:
                    import ifcopenshell
                    model=ifcopenshell.open(str(path))
                    data['selectors']=[{'id':s.GlobalId,'label':s.Name or s.GlobalId} for s in model.by_type('IfcBuildingStorey')]
                    data['limitations']=['Only explicit supported IfcSpace bodies are extracted. Select one storey; unsupported geometry remains unavailable.']
            else:
                with Image.open(io.BytesIO(raw)) as im:
                    check_image_size(im.width,im.height)
                    exif=Image.Exif()
                    if im.info.get('exif'):exif.load(im.info['exif'])
                    orientation=exif.get(274,1)
                    size=[im.height,im.width] if orientation in (5,6,7,8) else [im.width,im.height]
                    im.verify()
                    data['pages']=[{'number':1,'size':size,'native_text':False}]
        except Exception as e:
            (folder/'intake-failure.json').write_text(json.dumps({'error_type':type(e).__name__,'reason':str(e)}))
            raise ValueError(str(e)) from e
        data['page_count']=len(data['pages'])
        (folder/'document.json').write_text(json.dumps(data,indent=2))
        return data

    def prepare(self, ident, selection):
        d=self.get(ident);original=self.original(ident)
        run=self.root/ident/('prepare-'+secrets.token_hex(8));run.mkdir(parents=True)
        rows=[];limitations=list(d['limitations'])
        if d['format'] in ('svg','ifc'):
            if selection.pages:raise ValueError('Use room or storey selection for native geometry.')
            if d['format']=='ifc' and d['selectors'] and not selection.options.storey_id:raise ValueError('Select an IFC storey.')
            if d['format']=='svg' and not selection.options.element_ids:raise ValueError('Select SVG room elements.')
            from stoa_native_geometry import prepare
            receipt=prepare(original,run/'native',options=selection.options.model_dump(mode='json'),project_identity=d['project_identity'],dataset=d['dataset'],exposure=d['exposure'])
            extraction=receipt['extraction']
            limitations += extraction['quality_flags']+[r['reason'] for r in extraction['rejections']]
            if not receipt['source']:return {'sheets':[],'status':'unavailable','limitations':limitations}
            source=receipt['source'];image=Path(source['path'])
            rows=[{'image':str(image),'width':source['width'],'height':source['height'],'source_page':1,'native_source':source}]
        else:
            chosen=selection.pages or [p['number'] for p in d['pages']]
            if len(chosen)!=len(set(chosen)) or any(p not in range(1,d['page_count']+1) for p in chosen):raise ValueError('Select unique page numbers within the document.')
            for page in chosen:
                image=run/f'page-{page}.png';pdf=None;registration=None
                if d['format']=='pdf':
                    import pymupdf
                    with pymupdf.open(original) as doc, pymupdf.open() as one:
                        one.insert_pdf(doc,from_page=page-1,to_page=page-1)
                        pdf=run/f'page-{page}.pdf';one.save(pdf)
                        p=one[0];scale=min(2.,8000/max(p.rect.width,p.rect.height))
                        p.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False,colorspace=pymupdf.csRGB).save(image)
                else:
                    registration=prepare_raster(original,image)
                with Image.open(image) as im:w,h=im.size
                row={'image':str(image),'width':w,'height':h,'source_page':page,'document_page_count':d['page_count'],'packet_included_pages':len(chosen)}
                if pdf:row['pdf']=str(pdf)
                if registration:row['image_registration']=registration
                rows.append(row)
        entries=[]
        for row in rows:
            row.update(image_sha256=sha(row['image']),source_sha256=d['sha256'],document_id=ident,project_identity=d['project_identity'],dataset=d['dataset'],exposure=d['exposure'])
            title=d['title']+(f" · page {row['source_page']}" if d['page_count']>1 else '')
            raw=Path(row['image']).read_bytes()
            if row.get('native_source'):
                payload={'title':title,'source':{},'image':{'size':[row['width'],row['height']]},'scale':{'m_per_px':.03,'source':'estimate'},'walls':[],'rooms':[],'texts':[],'symbols':[],'openings':[]}
                trace=[]
                limitations += [row['native_source']['rendering_scope']]
            else:
                from .build import build_drawing
                from .build.floorplan.extract import normalize
                _,normalized=normalize(raw)
                built=build_drawing(raw,drawing_id='upload',title=title,dataset=d['dataset'],kind='Document page',file=d['filename'])
                payload=align_build(built.payload,(row['width'],row['height']),(normalized['width'],normalized['height']))
                trace=built.trace
            payload['source'].update(file=d['filename'],sha256=d['sha256'],dataset=d['dataset'],kind='Room-footprint preview' if row.get('native_source') else 'Document page',
                document_id=ident,source_page=row['source_page'],document_page_count=d['page_count'],rendering_scope=row.get('native_source',{}).get('rendering_scope'),limitations=limitations)
            if row.get('image_registration'):payload['source']['image_registration']=row['image_registration']
            payload['lens_source']=row
            if not row.get('native_source') and payload['scale'].get('source')=='estimate':
                # A printed scale bar beats the size guess. Read on the full page; never from paper size alone.
                from .build import ocr
                if ocr.available():
                    try:
                        import cv2,numpy as np
                        from .build import scale_bar
                        found=scale_bar.read(ocr.ocr_image(cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)))
                    except Exception:found=None
                    if found:
                        payload['scale']={'m_per_px':found['m_per_px'],'source':'scale_bar','confidence':0.8,
                                          'text':' '.join(found['labels'])+(' '+found['unit'] if found['unit'] not in ''.join(found['labels']) else ''),
                                          'replaced_estimate_m_per_px':payload['scale']['m_per_px']}
            drawing_id=self.drawings.save(payload,trace,raw,'png')
            if not row.get('native_source'):
                # The converter's own building model (IFC, GLB, DXF, SVG, PDF) from the full-resolution page.
                from .build.floorplan.model_files import convert
                sx=row['width']/normalized['width'];sy=row['height']/normalized['height']
                x0,y0,x1,y1=built.payload['source']['crop']
                record=convert(row['image'],self.drawings.model_dir(drawing_id),payload['scale']['m_per_px'],
                               [round(x0*sx),round(y0*sy),round(x1*sx),round(y1*sy)],payload['scale'].get('source','estimate'))
                geometry=None
                if record.get('status')=='built' and record['counts']['walls']:
                    # Show what the converter built: its wall outlines and rooms replace Build's re-traced lines.
                    from .build.floorplan.model_files import payload_geometry
                    geometry=payload_geometry(self.drawings.model_dir(drawing_id),image_factor=record.get('converter_image_factor',1.0))
                    record['shown_in_app']={'wall_outlines':len(geometry['wall_blocks']),'rooms':len(geometry['rooms']),'openings':len(geometry['openings']),
                                            'replaced_build_lines':len(payload['walls'])}
                self.drawings.set_model_files(drawing_id,record,geometry)
            entries.append(next(e for e in self.drawings.library() if e['id']==drawing_id))
        (run/'sheets.json').write_text(json.dumps({'rows':rows,'drawing_ids':[e['id'] for e in entries]},indent=2))
        return {'sheets':entries,'status':'prepared','limitations':list(dict.fromkeys(limitations))}
