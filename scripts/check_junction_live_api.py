"""Run three exposed Junction questions through an isolated real Ymir API.

No annotations or reference answers are read. This script makes paid calls only
when explicitly invoked; it uses an explicitly supplied existing ledger and a separate API state directory.
Each POST is replayed only with the same idempotency key after completion.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys

YMIR_ROOT=Path(__file__).resolve().parents[1]
STOA=Path(os.environ.get('YMIR_STOA_ROOT',YMIR_ROOT.parent/'stoa')).resolve()
VENDOR=(YMIR_ROOT/'vendor/stoa-runtime/src').resolve()


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def verify_runtime(module):
    actual=Path(module.__file__).resolve()
    if not actual.is_relative_to(VENDOR):
        raise RuntimeError('Imported Lens runtime is not the vendored Ymir runtime: '+str(actual))
    return {'path':str(actual),'sha256':sha(actual)}


def choose_cases(data):
    """First exposed toilet, window and combined shower/bathtub cases in order."""
    cases=data.get('cases')
    if not isinstance(cases,list) or len(cases)<8:raise ValueError('Public manifest must contain the original eight cases')
    cases=cases[:8]
    predicates=(lambda c:c.get('target_kind')=='toilets',lambda c:c.get('target_kind')=='windows',
                lambda c:c.get('target_kind')=='baths' and set(c.get('target_classes',[]))=={'bathtub','shower'})
    selected=[]
    for predicate in predicates:
        item=next((c for c in cases if predicate(c)),None)
        if item is None:raise ValueError('Required public target category is missing')
        if len(item.get('pages',[]))!=1 or not item.get('question'):raise ValueError('Expected one page and an original question')
        if item.get('exposure')!='previously_exposed_diagnostic':raise ValueError('Unexpected exposure status')
        selected.append(item)
    if len({c['case_id'] for c in selected})!=3:raise ValueError('Case IDs must be distinct')
    return selected


def bound_page(case):
    page=case['pages'][0];path=Path(page['image'])
    if not path.is_absolute():path=STOA/path
    path=path.resolve()
    if sha(path)!=page['image_sha256']:raise ValueError('Public source image hash mismatch')
    from PIL import Image
    with Image.open(path) as im:
        if im.size!=(page['width'],page['height']):raise ValueError('Public source dimensions changed')
    return path,page


def write_new(path,value):
    with Path(path).open('x') as f:json.dump(value,f,indent=2,ensure_ascii=False,allow_nan=False);f.write('\n')


def charge_count(ledger,job_id):
    # Other concurrently authorized jobs can share the ledger; inspect only this job's rows.
    with sqlite3.connect(f'file:{ledger}?mode=ro',uri=True) as db:
        return db.execute('SELECT count(*) FROM charges WHERE case_id=?',('lens-'+job_id,)).fetchone()[0]


def read_events(response):
    if response.status_code!=200:raise RuntimeError('API stream returned HTTP '+str(response.status_code))
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def verify_evidence(answer,sheet,width,height):
    if answer.get('status') not in ('answered','needs_review','error'):
        raise ValueError('Unknown public answer status')
    for c in answer.get('citations',[]):
        if c.get('sheet')!=sheet or not c.get('evidence_id') or c.get('provenance')!='source_bound':
            raise ValueError('Citation lacks registered sheet or provenance')
        box=c.get('box')
        if box is not None and (len(box)!=4 or not (0<=box[0]<=box[2]<=width and 0<=box[1]<=box[3]<=height)):
            raise ValueError('Citation falls outside source pixels')
    accepted=set()
    for c in answer.get('counts',[]):
        if c.get('sheet')!=sheet or c.get('provenance')!='host_derived':raise ValueError('Count lacks host/source attribution')
        if c.get('count') is not None and (type(c['count']) is not int or c['count']<0):raise ValueError('Invalid host count')
        ids=c.get('accepted_proposal_ids',[])
        if len(ids)!=len(set(ids)):raise ValueError('Duplicate accepted proposal IDs')
        if c.get('count') is not None and c['count']!=len(ids):raise ValueError('Host count disagrees with accepted proposal IDs')
        accepted.update(ids)
    cited={c.get('proposal_id') for c in answer.get('citations',[]) if c.get('proposal_id')}
    if not accepted.issubset(cited):raise ValueError('Accepted proposals lack source citations')
    reviewed=set()
    for group in answer.get('reviews',[]):
        if group.get('sheet')!=sheet:raise ValueError('Review belongs to another sheet')
        for box in group.get('boxes',[]):
            coords=box.get('box')
            if not isinstance(coords,list) or len(coords)!=4 or not (0<=coords[0]<coords[2]<=width and 0<=coords[1]<coords[3]<=height):raise ValueError('Review proposal outside original source')
            if box.get('accepted'):reviewed.add(box.get('id'))
    if not accepted.issubset(reviewed):raise ValueError('Accepted proposals lack matching review attribution')
    if answer.get('confidence') is not None:raise ValueError('Confidence must not be invented')
    return {'citations':len(answer.get('citations',[])),'count_groups':len(answer.get('counts',[])),
            'review_groups':len(answer.get('reviews',[])),'confidence':None}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--ledger',type=Path,required=True)
    p.add_argument('--budget',type=float,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();manifest=args.manifest.resolve();ledger=args.ledger.resolve();output=args.output.resolve()
    if not manifest.is_file() or not ledger.is_file():raise ValueError('An existing public manifest and shared ledger are required')
    if args.budget!=15:raise ValueError('This diagnostic uses the explicitly authorized $15 ledger ceiling')
    if output.exists():raise FileExistsError('Use a fresh immutable output directory')
    source_manifest=json.loads(manifest.read_text());selected=choose_cases(source_manifest)
    checked=[(case,*bound_page(case)) for case in selected]
    # Redirect all import-time Ymir state away from the live app/ledger before importing it.
    output.mkdir(parents=True,exist_ok=False)
    os.environ['YMIR_DATA_DIR']=str(output/'isolated-state');os.environ['YMIR_DB']=str(output/'isolated-state/ymir.db')
    os.environ.setdefault('YMIR_STOA_ROOT',str(STOA))
    asset_root=Path(os.environ['YMIR_STOA_ROOT']).resolve()
    sys.path[:0]=[str(YMIR_ROOT/'backend'),str(VENDOR)]
    import stoa_lens
    import stoa_lens.service as lens_service
    runtime_proof={'package':verify_runtime(stoa_lens),'service':verify_runtime(lens_service),'asset_root':str(asset_root)}
    from fastapi.testclient import TestClient
    from ymir.drawings import DrawingStore
    from ymir.documents import DocumentStore
    from ymir.lens_jobs import JobStore
    from ymir.lens_intent import bind_count_scope
    from stoa_lens.budget import LensBudget
    from stoa_lens.service import _implementation
    api=importlib.import_module('ymir.app')
    budget=LensBudget(ledger,args.budget)
    state=output/'isolated-state';drawings=DrawingStore(samples=state/'empty-samples',uploads=state/'drawings');documents=DocumentStore(drawings,state/'documents')
    jobs=JobStore(drawings,documents,state/'junction-jobs',budget=budget)
    original=(api.store,api.documents,api.jobs)
    api.store,api.documents,api.jobs=drawings,documents,jobs
    ymir_files=list((YMIR_ROOT/'backend/ymir').rglob('*.py'))+[Path(__file__).resolve()]
    frozen={'schema':'ymir-junction-live-public-v1','source_manifest':str(manifest),'source_manifest_sha256':sha(manifest),
            'selection_policy':'first exposed toilet, window and combined shower/bathtub cases in manifest order',
            'reference_annotations_provided':False,'accuracy_scored':False,'budget_ledger':str(ledger),'budget_cap_usd':args.budget,
            'python':sys.version,'platform':platform.platform(),'ymir_code_sha256':{str(x.relative_to(YMIR_ROOT)):sha(x) for x in sorted(ymir_files)},
            'stoa_runtime':runtime_proof,'stoa_code_sha256':_implementation(asset_root),'cases':[]}
    try:
        for case,path,page in checked:
            # DrawingStore stores a source image; no Build pipeline, crop, model, or reference is used.
            payload={'title':case['case_id'],'source':{'dataset':case['dataset'],'kind':'exposed diagnostic',
                    'sha256':page['image_sha256'],'project_identity':case.get('group_id'),'exposure':case['exposure']},
                    'image':{'size':[page['width'],page['height']]},'scale':{'m_per_px':None,'source':'unavailable'},
                    'walls':[],'rooms':[],'texts':[],'symbols':[],'openings':[],
                    'lens_source':{'image':str(path),'width':page['width'],'height':page['height'],'image_sha256':page['image_sha256'],
                    'source_sha256':page.get('source_sha256',page['image_sha256']),'source_page':1,
                    'dataset':case['dataset'],'exposure':case['exposure'],'project_identity':case.get('group_id')}}
            drawing=drawings.save(payload,[],path.read_bytes(),'png')
            # Question is exactly the original public text; no routing override or extra instruction.
            route=bind_count_scope(case['question'],[payload['lens_source']])
            frozen['cases'].append({'case_id':case['case_id'],'drawing_id':drawing,'dataset':case['dataset'],
                'group_id':case.get('group_id'),'exposure':case['exposure'],'question':case['question'],
                'target_kind':case['target_kind'],'target_classes':case['target_classes'],'image_sha256':page['image_sha256'],
                'image_dimensions':[page['width'],page['height']],'route':'compact_counter' if route else 'general',
                'bound_count_scope':route})
        write_new(output/'public-inputs.json',frozen)
        write_new(output/'budget-before.json',{k:v for k,v in budget.summary().items() if k!='charges'})
        with TestClient(api.app) as client:
            for item in frozen['cases']:
                case_id=item['case_id'];sheet=item['drawing_id']
                body={'request_id':'junction-live-v1-'+case_id,'drawing_id':sheet,'selected_drawing_ids':[sheet],
                      'project_id':'junction-live-'+case_id,'conversation_id':'junction-live-'+case_id,
                      'question':item['question'],'prior_job_ids':[]}
                response=client.post('/api/ask',json=body)
                events=read_events(response)
                if not events or events[0].get('type')!='job':raise ValueError('Missing initial job event')
                job_id=events[0]['job_id']
                (output/(case_id+'.events.jsonl')).write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events))
                lookup=client.get('/api/lens/jobs/'+job_id)
                if lookup.status_code!=200:raise ValueError('Job lookup failed')
                state=lookup.json();answer=state.get('answer') or {}
                evidence=verify_evidence(answer,sheet,*item['image_dimensions'])
                before=charge_count(ledger,job_id)
                repeated=read_events(client.post('/api/ask',json=body))
                after=charge_count(ledger,job_id)
                if not repeated or repeated[0].get('job_id')!=job_id or before!=after:raise ValueError('Idempotent replay changed job or incurred new request')
                original_events=[e for e in events if isinstance(e.get('seq'),int)]
                repeated_events=[e for e in repeated if isinstance(e.get('seq'),int)]
                if repeated_events!=original_events:raise ValueError('Idempotent replay changed stored events')
                replay=read_events(client.get(f'/api/lens/jobs/{job_id}/events?after=2'))
                if replay!=[e for e in original_events if e['seq']>2]:raise ValueError('Event reconnect did not replay the expected tail')
                write_new(output/(case_id+'.receipt.json'),{'case_id':case_id,'job_id':job_id,'status':state['status'],
                    'answer_status':answer.get('status'),'source_sheet_id':sheet,'evidence':evidence,
                    'event_count':len(original_events),'replay_same_job':True,'reconnect_tail_verified':True,
                    'job_ledger_rows_before_after_replay':[before,after], 'accuracy_scored':False})
                print(case_id,state['status'],answer.get('status'),flush=True)
        write_new(output/'budget-after.json',{k:v for k,v in budget.summary().items() if k!='charges'})
    finally:
        api.store,api.documents,api.jobs=original


if __name__=='__main__':main()
