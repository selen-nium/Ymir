"""Hash-bound, multipage VISTA views for a packet of local drawings."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path

from PIL import Image
from stoa.vision import Archive


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def stable_id(prefix, value):
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return prefix + '-' + hashlib.sha256(payload).hexdigest()[:24]


def _path(root, value):
    p = Path(value)
    return (p if p.is_absolute() else root/p).resolve()


def _pdf_registration(pdf_path, image_path, width, height):
    import pymupdf
    with pymupdf.open(pdf_path) as doc:
        if len(doc) != 1:
            raise ValueError('Packet PDF must contain exactly one page')
        page = doc[0]
        if page.rect.width <= 0 or page.rect.height <= 0:
            raise ValueError('Invalid PDF page extent')
        scale = min(2.0, 8000 / max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, colorspace=pymupdf.csRGB)
        if (pix.width, pix.height) != (width, height):
            raise ValueError('PDF render dimensions do not match packet pixels')
        with Image.open(image_path) as image:
            if image.convert('RGB').tobytes() != pix.samples:
                raise ValueError('PDF render pixels do not match packet image')
        # Text coordinates are unrotated and crop-relative in PyMuPDF. The
        # page rotation and exact raster matrix map them to pixmap coordinates.
        matrix = page.rotation_matrix * pymupdf.Matrix(scale, scale)
        return [matrix.a, matrix.b, matrix.c, matrix.d, matrix.e-pix.x, matrix.f-pix.y]


class Registry:
    def __init__(self, pages, directory, root=None):
        if not pages:
            raise ValueError('At least one page is required')
        self.root = Path(root or Path(__file__).resolve().parents[2]).resolve()
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.sources, self.views, self.initial_views = {}, {}, []
        self.inspection_views = 0
        archive_pages = []
        for index, row in enumerate(pages):
            image = _path(self.root, row['image'])
            with Image.open(image) as im:
                width, height = im.size
            if (width, height) != (row['width'], row['height']):
                raise ValueError('Packet image dimensions changed')
            source_page = row.get('source_page', index+1)
            if type(source_page) is not int or source_page < 1:
                raise ValueError('source_page must be a positive one-based integer')
            image_hash = sha(image)
            if row.get('image_sha256') is not None and row['image_sha256'] != image_hash:
                raise ValueError('Packet image hash does not match its manifest')
            native = row.get('native_source')
            if native is not None:
                if not isinstance(native, dict) or not native.get('native_geometry'):
                    raise ValueError('Prepared native source and registration required')
                native = copy.deepcopy(native)
                if row.get('pdf') or source_page != 1 or native.get('page') != 0:
                    raise ValueError('Native footprint previews require page zero and no packet PDF')
                if _path(self.root,native['path']) != image or (native['width'],native['height']) != (width,height) or native['source_sha256'] != image_hash:
                    raise ValueError('Native preview does not match packet image')
                # This verifies all three assets, preview transform, dimensions,
                # project provenance and actual room geometry before registration.
                from stoa_native_geometry import RegisteredNativeBackend
                RegisteredNativeBackend(native).candidates(native['source_id'],0)
            document_hash = (native['native_geometry']['native_sha256'] if native is not None else row.get('source_sha256') or row.get('sha256'))
            identity = {'image_sha256':image_hash, 'source_page':source_page, 'original_document_sha256':document_hash}
            if native is not None:
                identity['native_source_id'] = native['source_id']
            page_id = stable_id('page', identity)
            if page_id in self.sources:
                raise ValueError('Duplicate packet page identity')
            source = {'page_id':page_id, 'source_id':page_id, 'source_sha256':image_hash,
                      'path':str(image), 'width':width, 'height':height, 'page':source_page-1,
                      'source_page':source_page, 'vista_turn':index}
            if native is not None:
                source.update(native)
                source.update(page_id=page_id,source_page=1,vista_turn=index,path=str(image))
            if document_hash:
                source['original_document_sha256'] = document_hash
            for key in ('project_identity','dataset','exposure','drawing_id','document_id'):
                if key in row:source[key]=row[key]
            if 'document_page_count' in row or 'packet_included_pages' in row:
                total, included = row.get('document_page_count'), row.get('packet_included_pages')
                if type(total) is not int or type(included) is not int or not (1 <= included <= total and source_page <= total):
                    raise ValueError('Invalid packet page coverage')
                source.update(document_page_count=total, packet_included_pages=included)
            if row.get('pdf'):
                pdf = _path(self.root, row['pdf'])
                if row.get('pdf_sha256') is not None and sha(pdf)!=row['pdf_sha256']:
                    raise ValueError('Packet PDF hash does not match its manifest')
                source.update(pdf_path=str(pdf), pdf_sha256=sha(pdf), pdf_page=0,
                              pdf_to_page=_pdf_registration(pdf, image, width, height))
            self.sources[page_id] = source
            archive_pages.append({'image':str(image), 'width':width, 'height':height})
            self.initial_views.append(self._register(source, image, 0, 0, width, height))
        self.archive = Archive(archive_pages)
        self.source = next(iter(self.sources.values()))
        self.initial = self.initial_views[0]

    def _register(self, source, path, x, y, width, height):
        with Image.open(path) as image:
            w, h = image.size
        if w <= 0 or h <= 0 or width <= 0 or height <= 0:
            raise ValueError('Invalid view extent')
        view = {'page_id':source['page_id'], 'source_id':source['source_id'], 'source_sha256':source['source_sha256'],
                'page':source['page'], 'vista_turn':source['vista_turn'], 'path':str(Path(path).resolve()),
                'width':w, 'height':h, 'original_width':source['width'], 'original_height':source['height'],
                'transform':{'scale_x':width/w,'scale_y':height/h,'origin_x':x,'origin_y':y}, 'image_sha256':sha(path)}
        for key in ('pdf_path','pdf_page','pdf_sha256','pdf_to_page','original_document_sha256','rendering_scope','project_identity','dataset','exposure'):
            if key in source:
                view[key] = source[key]
        view['view_id'] = stable_id('view', {k:v for k,v in view.items() if k not in ('path','pdf_path')})
        old = self.views.get(view['view_id'])
        if old is not None:
            if {k:v for k,v in old.items() if k != 'path'} != {k:v for k,v in view.items() if k != 'path'}:
                raise ValueError('View identity collision')
            return old
        self.views[view['view_id']] = view
        return view

    def checked_view(self, view_id):
        view = self.views[view_id]
        source = self.sources[view['page_id']]
        if sha(view['path']) != view['image_sha256'] or sha(source['path']) != source['source_sha256']:
            raise ValueError('Registered source or view pixels changed')
        if source.get('pdf_path') and sha(source['pdf_path']) != source['pdf_sha256']:
            raise ValueError('Registered PDF changed')
        if source.get('native_geometry'):
            native = source['native_geometry']
            if sha(native['path']) != native['native_sha256'] or sha(native['extraction_path']) != native['extraction_sha256']:
                raise ValueError('Registered native source or extraction changed')
        return view

    def public_source(self, page_id):
        """Return provenance without disclosing local asset paths."""
        source = self.sources[page_id]
        fields = ('page_id','source_id','source_sha256','width','height','page','source_page','vista_turn',
                  'original_document_sha256','pdf_sha256','rendering_scope','project_identity','dataset','exposure',
                  'document_page_count','packet_included_pages','drawing_id','document_id')
        result = {key:source[key] for key in fields if key in source}
        if source.get('native_geometry'):
            native = source['native_geometry']
            result['native_geometry'] = {key:native[key] for key in ('schema','native_sha256','extraction_sha256','native_to_page')}
        return result

    def source_for_view(self, view_id):
        return self.sources[self.checked_view(view_id)['page_id']]

    def initial_for(self, page_id):
        return next(v for v in self.initial_views if v['page_id'] == page_id)

    def execute(self, name, args):
        if name not in ('inspect','read_pixels'):
            raise ValueError('Unknown VISTA tool')
        requests = args.get('views', []) if isinstance(args,dict) else []
        count = max(1, len(requests)) if isinstance(requests,list) else 1
        self.inspection_views += count
        if self.inspection_views > 9:
            raise ValueError('Nine VISTA views maximum, including failed requests')
        # Hash check every requested source before upstream image access.
        for request in requests:
            turn = request.get('turn') if isinstance(request,dict) else None
            if type(turn) is not int or not 0 <= turn < len(self.initial_views):
                raise ValueError('Unregistered page turn')
            self.checked_view(self.initial_views[turn]['view_id'])
        result, images = self.archive.execute(name,args)
        if name == 'inspect' and result.get('ok'):
            entries = result['metadata']['views']
            if len(entries) != len(images):
                raise ValueError('VISTA image metadata mismatch')
            registered = []
            for entry, content in zip(entries, images):
                turn = entry.get('turn')
                if type(turn) is not int or not 0 <= turn < len(self.initial_views):
                    raise ValueError('VISTA returned unregistered page')
                data = base64.b64decode(content['image_url']['url'].split(',',1)[1], validate=True)
                digest = hashlib.sha256(data).hexdigest()
                path = self.directory / ('view-'+digest+'.png')
                if path.exists():
                    if sha(path) != digest:
                        raise ValueError('Immutable view file changed')
                else:
                    # Exclusive creation prevents a later crop from overwriting evidence.
                    try:
                        with path.open('xb') as out: out.write(data)
                    except FileExistsError:
                        if sha(path) != digest: raise ValueError('Immutable view file changed')
                region = entry['region']
                source = self.sources[self.initial_views[turn]['page_id']]
                view = self._register(source,path,region['x'],region['y'],region['width'],region['height'])
                if [view['width'],view['height']] != [entry['image_size']['width'],entry['image_size']['height']]:
                    raise ValueError('VISTA image dimensions disagree with receipt')
                registered.append({k:v for k,v in view.items() if k not in ('path','pdf_path')})
            result['registered_views'] = registered
        return result, images
