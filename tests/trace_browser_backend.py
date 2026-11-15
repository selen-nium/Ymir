"""No-charge browser fixture: real service/harness, injected model responses.

PYTHONPATH=backend:tests:vendor/stoa-runtime/src YMIR_DATA_DIR=/tmp/ymir-pipeline-qa \
  .venv/bin/python -m uvicorn trace_browser_backend:app --port 8012
"""
import importlib
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.middleware.cors import CORSMiddleware
from stoa_lens.dfine import HEADS
from stoa_lens.routing import ClassRouter
from stoa_lens.service import run

api=importlib.import_module('ymir.app')
app=api.app
app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:3011','http://127.0.0.1:3000'],allow_methods=['*'],allow_headers=['*'])
hold_next=threading.Event();release=threading.Event()


@app.post('/fixture/hold-next')
def hold():
    release.clear();hold_next.set()
    return {'held':True}


@app.post('/fixture/release')
def release_response():
    release.set()
    return {'released':True}


class FixtureModels:
    candidates={}
    calibrations={}
    dfine=SimpleNamespace(model='fixture-dfine')
    argon=SimpleNamespace(model='fixture-argon')

    def __init__(self):self.router=ClassRouter()

    def close(self):pass

    def detect(self,view,region,object_class,reason,timeout):
        time.sleep(1)
        route=self.router.route(object_class)
        boxes=[[40,40,100,100]]
        if object_class=='window':boxes.append([0,20,20,40])
        proposals=[{'id':f'proposal-{i+1}','source_sha256':view['source_sha256'],
                    'view_id':view['view_id'],'native_box_xyxy':box,'confidence':None,
                    'confidence_kind':'browser fixture','model':'fixture-dfine'} for i,box in enumerate(boxes)]
        return {'status':'complete','routing':route,'proposals':proposals}


class FixtureTransport:
    def __init__(self,held=False):self.held=held

    def complete(self,messages,tools,deadline,response_index,final=False):
        context=json.loads(messages[1]['content'][0]['text'])
        view=context['initial_views'][0];question=context['question']
        count='count_scope' in context or 'count' in question.lower()
        # Keep a real durable job running long enough to reload and reconnect.
        if self.held and response_index==0:release.wait(30)
        time.sleep(1)
        if response_index==0:
            name='detect_objects' if count else 'read_pdf_text'
            args={'region':[0,0,view['width'],view['height']],'reason':'Browser fixture count'} if count else {'view_id':view['view_id']}
            return {'role':'assistant','content':None,'tool_calls':[
                {'id':'fixture-tool','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
        if count:
            value={'status':'answered','accepted_proposal_ids':['proposal-1']+(['proposal-2'] if 'window' in question.lower() else [])}
        else:
            value={'status':'answered','accepted_proposal_ids':[],'count_requests':[],'rooms':[],
                   'readings':[{'kind':'other','value':'Floor plan','unit':None,'evidence_ids':[view['view_id']],
                                'description':'Browser fixture reading of the registered sheet'}]}
        return {'role':'assistant','content':json.dumps(value)}


def fixture_runner(pages,question,directory,**hooks):
    held=hold_next.is_set();hold_next.clear()
    with patch('stoa_lens.routing.verified_head_policy',lambda:{'heads':list(HEADS),'browser_fixture':True}):
        return run(pages,question,directory,**hooks,transport=FixtureTransport(held),
                   detector=FixtureModels(),geometry=FixtureModels(),limits={'responses':2})


api.jobs.runner=fixture_runner
api.readiness=lambda budget,mode='smart':{'ready':True,'checks':{'injected_browser_fixture':True},
                             'modes':{m:{'ready':True,'checks':{}} for m in ('smart','frontier')},
                             'budget':{k:v for k,v in budget.summary().items() if k!='charges'}}
