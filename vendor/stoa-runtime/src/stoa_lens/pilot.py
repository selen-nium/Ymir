"""Offline selection and scoring for the unchanged historical nine-case pilot.

Reference counts and AI-reviewed diagnostic inventories are written separately;
workers should receive only the public manifest and never the offline file.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from PIL import Image

PUBLIC='data/rfdetr-vista-deepseek-v1/public.json'
PUBLIC_SHA='b51f6d0d7ae0ab45d642da6f704718df3c2cd6399d6eecc6ac5561fb63fdfaa8'
REFERENCES='data/junction-specialist/evaluation/private/references.json'
REFERENCES_SHA='af93f3b001fdf38a25bc85ea90932dabca9af4de01bf82fcd7124f337f97613c'
DIAGNOSTICS='data/junction-specialist/grounded-count-probe/private/adjudicated-references.json'
DIAGNOSTICS_SHA='2326447a0e18a9583c68005b77676d88619c0493531787278fa676ce5dc8a763'
BASELINE='reports/rfdetr-vista-deepseek-v1/results.json'
BASELINE_SHA='86bc81375116176e4daef4530249ed6eb97c7c36e775b74e03a96358b29a716c'
IDS=('jc-0c852b30d69567ac4c7d-en','jc-22d481a07256eca9f602-en','jc-3abf2d850722b0472fdf-en',
 'jc-699d8fb93c72713cce18-en','jc-cfebaace7eb41a13de25-fi','js-a9ca72a187a43b873d93',
 'js-a368695f69046b4da492','jc-db0877d94e2aac47f791-fi','aecv-Rinker_051-1-cnt1')

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def checked(root,path,digest):
    file=Path(root)/path
    if sha(file)!=digest:raise ValueError('Pinned historical artifact changed: '+path)
    return json.loads(file.read_text())
def _once(path,value):
    payload=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n';path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open('x') as f:f.write(payload)
    except FileExistsError:
        if path.read_text()!=payload:raise ValueError('Existing pilot artifact differs: '+str(path))

def prepare(root,directory):
    """Write deterministic separate public inputs and offline-only reference file."""
    root=Path(root).resolve();directory=Path(directory)
    public=checked(root,PUBLIC,PUBLIC_SHA);official=checked(root,REFERENCES,REFERENCES_SHA)
    diag={x['id']:x for x in checked(root,DIAGNOSTICS,DIAGNOSTICS_SHA)['cases']}
    baseline=checked(root,BASELINE,BASELINE_SHA)
    if tuple(x['id'] for x in public)!=IDS:raise ValueError('Historical case order changed')
    cases=[]; offline={};baseline_rows=baseline['rows']
    for row in public:
        case_id=row['id'];kind=row['target_kind']; classes={'toilets':['toilet_bowl'],'windows':['window'],
           'doors':['door'],'baths':['bathtub'] if 'Swiss' in row['dataset'] else ['bathtub','shower'],
           'footing_type':[]}[kind]
        pages=[]
        for page in row['pages']:
            image=(root/page['image']).resolve()
            if sha(image)!=page['image_sha256']:raise ValueError('Historical page hash changed: '+case_id)
            with Image.open(image) as im:
                if im.size!=(page['width'],page['height']):raise ValueError('Historical page dimensions changed: '+case_id)
            pages.append({**page,'image':str(image)})
        cases.append({'case_id':case_id,'dataset':row['dataset'],'group_id':row['group_id'],
            'exposure':'previously_exposed_diagnostic','question':row['question'],
            'scope_instruction':row['scope_instruction'],'target_kind':kind,'target_classes':classes,
            'task_type':'schedule_count' if kind=='footing_type' else 'object_count',
            'schedule_behavior':'Use visible schedule evidence; object detection does not count schedule rows.' if kind=='footing_type' else None,
            'pages':pages,'baseline':{'study':'rfdetr-vista-deepseek-v1','case_id':case_id,
                                      'comparison':'historical diagnostic; runtime and prompts differ'}})
        ref=official[case_id];inventory=diag[case_id]
        # AI-reviewed boxes are explicitly diagnostic; official labels supply count only.
        box_rows=[i['pixel_bbox'] for i in inventory.get('instances',[]) if 'pixel_bbox' in i]
        offline[case_id]={'official':ref,'diagnostic_inventory':{'boxes_native_xyxy':box_rows,
            'status':inventory.get('status'),'reviewer_kind':inventory.get('reviewer_kind'),
            'origin':'AI-reviewed diagnostic; not official reference boxes'},
            'historical_baseline':[x for x in baseline_rows if x['id']==case_id]}
    manifest={'protocol':'stoa-lens-rfdetr-nine-case-reuse-v1','selection':'Exact historical nine-case RF-DETR/VISTA pilot, including AECV schedule',
      'source_public_sha256':PUBLIC_SHA,'exposure':'Previously exposed diagnostics; no held-out claim','cases':cases}
    references={'protocol':manifest['protocol'],'official_reference_sha256':REFERENCES_SHA,
       'diagnostic_inventory_sha256':DIAGNOSTICS_SHA,'historical_report_sha256':BASELINE_SHA,'cases':offline}
    _once(directory/'public-manifest.json',manifest);_once(directory/'offline-references.json',references)
    return manifest


def _iou(a,b):
    inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=max(0,a[2]-a[0])*max(0,a[3]-a[1])+max(0,b[2]-b[0])*max(0,b[3]-b[1])-inter
    return inter/union if union else 0

def _match(boxes,truth):
    # Maximum bipartite matching; no duplicate prediction may match one reference twice.
    matched={}
    def assign(i,seen):
        for j,t in enumerate(truth):
            if j not in seen and _iou(boxes[i],t)>=.5:
                seen.add(j)
                if j not in matched or assign(matched[j],seen):matched[j]=i;return True
        return False
    return sum(assign(i,set()) for i in range(len(boxes)))

def _extract_count(case,pred):
    if case['task_type']=='schedule_count':
        readings=[r for r in pred.get('readings',[]) if r.get('unit')=='count' and
                  isinstance(r.get('value'),int) and not isinstance(r['value'],bool) and r['value']>=0]
        return readings[0]['value'] if len(readings)==1 else None
    counts=pred.get('counts')
    if counts is not None:
        selected=[c for c in counts if c.get('object_class') in case['target_classes']]
        if len(selected)!=len(case['target_classes']) or {c.get('object_class') for c in selected}!=set(case['target_classes']):return None
        if any(not isinstance(c.get('count'),int) or isinstance(c['count'],bool) or c['count']<0 for c in selected):return None
        ids=[ident for c in selected for ident in c.get('accepted_proposal_ids',[])]
        if len(ids)!=len(set(ids)):return None
        return sum(c['count'] for c in selected)
    return pred.get('count')


def _derive_boxes(pred,case):
    if 'proposals' not in pred:return pred
    result=dict(pred);proposals=[p for p in pred['proposals'] if p.get('query_class') is None or p['query_class'] in case['target_classes']];accepted=set(pred.get('accepted_proposal_ids',[]))
    result.setdefault('raw_boxes',[p['native_box_xyxy'] for p in proposals if p.get('native_box_xyxy')])
    result.setdefault('accepted_boxes',[p['native_box_xyxy'] for p in proposals if p.get('native_box_xyxy') and p.get('id') in accepted])
    return result


def score(manifest,results,references):
    """Score an ID-keyed result dictionary against held-out-from-inference counts."""
    rows=[]
    for case in manifest['cases']:
        ident=case['case_id'];pred=results.get(ident) or {};pred=pred.get('result') or pred;pred=_derive_boxes(pred,case);reference=references['cases'][ident];official=reference['official'];status=pred.get('status','unattempted');count=_extract_count(case,pred)
        answered=status in ('answered','completed') and isinstance(count,int) and not isinstance(count,bool) and count>=0
        row={'case_id':ident,'status':status,'answered_count':count if answered else None,
             'official_count':official['value'],'correct':bool(answered and count in official.get('accepted_values',[official['value']])),
             'diagnostic_boxes':None,'official_box_metrics':None}
        inv=reference['diagnostic_inventory'];truth=inv['boxes_native_xyxy']
        if case['task_type']=='object_count' and inv['status']=='verified' and truth:
            diagnostics={}
            for key in ('raw_boxes','accepted_boxes'):
                if key in pred:
                    boxes=pred[key];n=_match(boxes,truth)
                    diagnostics[key]={'predictions':len(boxes),'reference_boxes':len(truth),'matches_iou50':n,
                        'precision':n/len(boxes) if boxes else None,'recall':n/len(truth),
                        'exact_set':n==len(boxes)==len(truth)}
            if diagnostics:row['diagnostic_boxes']={'provenance':inv['origin'],'metrics':diagnostics}
        rows.append(row)
    return {'planned':len(manifest['cases']),'correct':sum(r['correct'] for r in rows),
            'accuracy':sum(r['correct'] for r in rows)/len(rows) if rows else None,
            'answered':sum(r['answered_count'] is not None for r in rows),'rows':rows,
            'limitations':['Historical cases are exposed diagnostics.','Reference boxes are AI-reviewed diagnostics, not official ground-truth boxes.','Historical RF-DETR and Lens use different orchestration and prompts.']}
