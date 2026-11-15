"""Local ingestion for new sponsor PDFs/images, separate from scored benchmark data."""
import hashlib
import json
import re
from pathlib import Path

from PIL import Image,ImageOps
import pymupdf

from .config import DATA,ROOT

def ingest(source: Path,name: str,max_pages=12):
    source=source.resolve()
    if not source.is_file(): raise FileNotFoundError(source)
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}',name):
        raise ValueError('Use a short packet name containing letters, digits, hyphens, or underscores.')
    if not 1<=max_pages<=30: raise ValueError('max_pages must be between 1 and 30')
    dest=DATA/'packets'/name
    if dest.exists(): raise FileExistsError('Packet already exists; use another name to preserve original evidence.')
    dest.mkdir(parents=True)
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    pages=[]
    if source.suffix.lower()=='.pdf':
        with pymupdf.open(source) as doc:
            total=len(doc)
            for i in range(min(total,max_pages)):
                page=doc[i]
                scale=min(2,8000/max(page.rect.width,page.rect.height))
                png=dest/f'page-{i+1:03d}.png';pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False);pix.save(png)
                single=dest/f'page-{i+1:03d}.pdf'
                with pymupdf.open() as out:
                    out.insert_pdf(doc,from_page=i,to_page=i);out.save(single)
                pages.append({'pdf':str(single.relative_to(ROOT)),'image':str(png.relative_to(ROOT)),
                              'width':pix.width,'height':pix.height,'source_page':i+1,'sha256':sha})
    else:
        with Image.open(source) as im:
            im=ImageOps.exif_transpose(im)
            png=dest/'page-001.png';im.convert('RGB').save(png)
            pages.append({'image':str(png.relative_to(ROOT)),'width':im.width,'height':im.height,'source_page':1,'sha256':sha})
        total=1
    payload={'name':name,'source_name':source.name,'source_sha256':sha,'total_source_pages':total,
             'included_pages':len(pages),'pages':pages,'ground_truth':None}
    output=dest/'packet.json';output.write_text(json.dumps(payload,indent=2))
    return output
