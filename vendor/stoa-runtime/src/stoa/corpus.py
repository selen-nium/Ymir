"""Prepare an explicitly adapted extraction pilot from released AEC-Bench sheets."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf as fitz
import requests

from .config import DATA

# The source annotations are withheld from the inference function. These questions ask
# what the supplied (sometimes deliberately altered) sheet says, not whether it is safe.
SPECS = [
 ('03add-constructability-01','layers','In WALL SECTION 1 on sheet A5.03 (the far-right wall section), how many insulation layers are specified in its MIN R25 roof-assembly note?',8,'layers','03add','development'),
 ('03add-constructability-01','thickness','In WALL SECTION 1 on A5.03 (the far-right wall section), what is the specified thickness of EACH insulation layer in its MIN R25 roof-assembly note?',2.3,'in','03add','development'),
 ('gfiaa-constructability-01','tolerance','In the note beginning TOP EDGE OF TOP CMU COURSE on the right side of the sheet, what numeric alignment tolerance is stated relative to the existing precast concrete wall?',1/16,'in','gfiaa','evaluation'),
 ('gfiaa-constructability-02','tolerance','What location tolerance is specified for the jib-crane anchor bolts relative to the manufacturer template?',1/16,'in','gfiaa','evaluation'),
 ('gfiaa-performance-01','embedment','On S-502, in the lower-right girt-to-CMU-wall foundation detail, what embedment is explicitly stated for the 1/2-inch-diameter epoxy anchors at 32 inches on center?',1,'in','gfiaa','evaluation'),
 ('nmrc-performance-01','embedment','In Section 4 (detail 6 on S1-0), what embedment into the bond beam is stated for the expansion anchor at each roof-truss clip?',1,'in','nmrc','evaluation'),
 ('nmrc-constructability-01','sequence','In the demolition/construction sequence notes, which step installs the column? Return the step number.',2,'step','nmrc','evaluation'),
 ('rees-constructability-01','specification','What specification section number is attached to the SELF-ADHERING MEMBRANE callout? Return only the three-part section number.','07 25 00','section','rees','development'),
 ('rees-performance-01','rvalue','For EXTERIOR WALL type 581, what continuous XPS rigid insulation R-value is explicitly specified? Return the number after R-.',3.3,'R','rees','development'),
 ('uccs-constructability-01','material','At the head-of-wall deflection detail, what material phrase is used for the continuous long-leg deflection angle? Return the two-word material phrase.','RIGID METAL','text','uccs','evaluation'),
 ('usu-constructability-01','anchor','In detail E1 CANOPY WALL ROD ATTACHMENT, what anchor type is specified for the four anchors per bracket? Return the exact anchor-type phrase.','CAST-IN ANCHORS','text','usu','development'),
 ('usu-performance-01','embedment','In detail 2/S210 COLUMN TO SPOT FOOTING, what MINIMUM embedment is called out for the headed anchor bolts with nut and washer?',2,'in','usu','development'),
 ('usu-performance-02','termination','In detail A1 Foundation Detail @ CMU Wall w/ Concrete Curb, where does the underslab vapor barrier terminate according to its explicit note?','EDGE OF SLAB','text','usu','development'),
]

def prepare():
    root = DATA/'sources/aec-bench/tasks/intrasheet/detail-technical-review'
    if not root.is_dir():
        raise RuntimeError('Clone https://github.com/nomic-ai/aec-bench into data/sources/aec-bench first.')
    dest = DATA/'corpus'
    dest.mkdir(parents=True, exist_ok=True)
    tasks = sorted({s[0] for s in SPECS})

    def download(task):
        entry = json.loads((root/task/'environment/manifest.jsonl').read_text().splitlines()[0])
        pdf = dest/f'{task}.pdf'
        if not pdf.is_file():
            r=requests.get(entry['key'],timeout=90);r.raise_for_status();pdf.write_bytes(r.content)
        doc=fitz.open(pdf)
        page=doc[0]
        png=dest/f'{task}.png'
        if not png.is_file():
            # Full-resolution archive, separately downsampled for the initial model view.
            page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(png)
        return task, {'pdf':str(pdf.relative_to(DATA.parent)), 'image':str(png.relative_to(DATA.parent)),
                      'width':round(page.rect.width*2),'height':round(page.rect.height*2),
                      'page_width':page.rect.width,'page_height':page.rect.height,
                      'source_url':entry['key'],'sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}
    sources = dict(ThreadPoolExecutor(max_workers=4).map(download,tasks))
    public, gold = [], {}
    for task,field,question,value,unit,group,split in SPECS:
        meta=sources[task]
        annotation=json.loads((root/task/'gt.json').read_text())['defects'][0]
        # Source annotation boxes use unrotated PDF coordinates; pixmaps include rotation.
        with fitz.open(DATA.parent/meta['pdf']) as doc:
            box=list(fitz.Rect(*annotation['bbox'])*doc[0].rotation_matrix)
        norm=[box[0]/meta['page_width']*1000,box[1]/meta['page_height']*1000,box[2]/meta['page_width']*1000,box[3]/meta['page_height']*1000]
        ident=f'{task}-{field}'
        # No answer, edited text, target bbox, or original-vs-edited comparison goes here.
        public.append({'id':ident,'question':question,'expected_unit':unit,'group':group,'split':split,
                       'source_kind':'AEC-Bench adapted extraction','pages':[meta]})
        gold[ident]={'value':value,'unit':unit,'bbox':norm,'page':0,'reference_quote':annotation['replacement_text'],
                     'origin_task':task,'reference':'AEC-Bench supplied perturbation annotation; extraction question authored for this pilot'}
        if field=='termination': gold[ident]['accepted_values']=['EDGE OF SLAB','AT EDGE OF SLAB','THE EDGE OF THE SLAB','AT THE EDGE OF THE SLAB','TERMINATE AT EDGE OF SLAB','UNDERSLAB VAPOR BARRIER, TERMINATE AT EDGE OF SLAB']
        if field=='anchor': gold[ident]['accepted_values']=['CAST-IN ANCHORS','CAST-IN ANCHORS (4 PER BRACKET)']
    (dest/'questions.json').write_text(json.dumps(public,indent=2))
    (dest/'gold.json').write_text(json.dumps(gold,indent=2))
    (dest/'provenance.json').write_text(json.dumps({'source_repository':'https://github.com/nomic-ai/aec-bench',
        'task_scope':'Adapted explicit-value extraction, NOT the official AEC-Bench engineering review score',
        'drawings':'Released benchmark variants include deliberate annotation edits.',
        'license_reference':'data/sources/aec-bench/LICENSE','sources':sources},indent=2))
    return public

def questions():
    return json.loads((DATA/'corpus/questions.json').read_text())

def gold():
    return json.loads((DATA/'corpus/gold.json').read_text())
