"""Migration integration without paid inference; all network access fails closed."""
import copy
import importlib
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from stoa_lens.budget import BudgetError, LensBudget
from stoa_lens.model_config import ARGON, DEEPSEEK, SOL, PROVIDERS, resolve
from stoa_lens.transport import OpenRouterTransport, catalog_path, refresh_pricing, validate_catalog
from stoa_lens.streaming import Accumulator
from stoa_lens.registry import Registry
from stoa_lens.detection import DetectionService, DetectionError
from stoa_lens.bound_counter import RoutedBoundDetector
from stoa_lens.service import run
from ymir.documents import DocumentStore
from ymir.drawings import DrawingStore
from ymir.lens_jobs import JobStore


@pytest.fixture(autouse=True)
def isolated_network_and_heads(monkeypatch):
    import requests
    import httpx
    def forbidden(*args, **kwargs):
        raise AssertionError('Migration tests prohibit network, including Verda')
    monkeypatch.setattr(requests.Session, 'request', forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    from stoa_lens.dfine import HEADS
    monkeypatch.setattr('stoa_lens.routing.verified_head_policy', lambda: {'heads': list(HEADS), 'test_only': True})


def catalog(model):
    return {'observed_at': datetime.now(timezone.utc).isoformat(), 'source': 'injected fixture',
            'metadata': {'id': model, 'architecture': {'input_modalities': ['text', 'image']}},
            'selected': {'tag': PROVIDERS[model], 'status': 0, 'service_tier': None,
                         'supported_parameters': ['reasoning_effort', 'response_format', 'max_tokens', 'tools', 'tool_choice'],
                         'max_completion_tokens': 65536, 'context_length': 131072,
                         'pricing': {'prompt': '0.000001', 'completion': '0.000002'}}}


class Pacer:
    def __init__(self): self.events = []
    def acquire(self, *args): self.events.append(('acquire', args)); return 1
    def observe(self, *args): self.events.append(('observe', args))
    def finish(self, *args): self.events.append(('finish', args))


def transport(tmp_path, mode='smart', requester=None, budget=None):
    return OpenRouterTransport(tmp_path, tmp_path / 'job', budget or LensBudget(tmp_path / 'budget.sqlite', 25),
                               configuration=resolve(mode), catalogs={m: catalog(m) for m in PROVIDERS},
                               pacer=Pacer(), requester=requester, credential='fixture-secret-value')


def response(model=DEEPSEEK, cost=.012, message=None, **extra):
    return {'id': 'gen-fixture', 'model': model, 'provider': 'Actual provider',
            'message': message or {'role': 'assistant', 'content': '{}'},
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'cost': cost}, 'finish_reason': 'stop', **extra}


@pytest.mark.parametrize('mode,model', [('smart', DEEPSEEK), ('frontier', SOL)])
def test_pinned_orchestrators_billing_and_once_only(tmp_path, mode, model):
    bodies=[]
    def requester(body, deadline, directory): bodies.append(copy.deepcopy(body)); return response(model)
    t=transport(tmp_path, mode, requester)
    t.complete([{'role':'user','content':'question'}], [], time.monotonic()+10, 0, final=True)
    assert bodies[0]['model']==model
    assert bodies[0]['provider']=={'only':[PROVIDERS[model]],'allow_fallbacks':False,'require_parameters':True}
    assert bodies[0]['max_tokens']==65536 and bodies[0]['reasoning_effort']=='high'
    charge=t.budget.summary()['charges'][0]
    assert charge['actual_usd']==.012 and charge['generation_id']=='gen-fixture'
    assert t.receipts[0]['actual_provider']=='Actual provider'
    with pytest.raises(FileExistsError):t.complete([],[],time.monotonic()+10,0)
    assert len(bodies)==1 and len(t.budget.summary()['charges'])==1


@pytest.mark.parametrize('cost', [None, True, -1, float('inf')])
def test_unknown_or_invalid_billing_holds_full_reservation(tmp_path, cost):
    t=transport(tmp_path, requester=lambda *a:response(cost=cost))
    if cost is None:t.complete([],[],time.monotonic()+10,0)
    else:
        with pytest.raises(ValueError):t.complete([],[],time.monotonic()+10,0)
    charge=t.budget.summary()['charges'][0]
    assert charge['status']=='unknown' and charge['actual_usd'] is None
    assert charge['reserved_usd']>0


@pytest.mark.parametrize('status,held', [(429,False),(500,True)])
def test_provider_failures_never_retry_or_fallback(tmp_path, status, held):
    calls=[]
    class Failure(RuntimeError):status_code=status
    def requester(body,*args):calls.append(body['model']);raise Failure('provider failed')
    t=transport(tmp_path, 'frontier', requester)
    with pytest.raises(Failure):t.complete([],[],time.monotonic()+10,0)
    assert calls==[SOL]
    assert t.budget.summary()['charges'][0]['status']==('unknown' if held else 'settled')


def test_partial_stream_billing_is_authoritative(tmp_path):
    def requester(body, deadline, directory):
        (directory/'chunks.jsonl').write_text(json.dumps({'id':'partial-id','usage':{'cost':.03}})+'\n')
        raise TimeoutError('stream closed')
    t=transport(tmp_path, requester=requester)
    with pytest.raises(TimeoutError):t.complete([],[],time.monotonic()+10,0)
    assert t.budget.summary()['charges'][0]['actual_usd']==.03
    assert t.budget.summary()['charges'][0]['generation_id']=='partial-id'


def test_catalog_validation_is_per_route_and_requires_parameters(tmp_path, monkeypatch):
    from stoa_lens.model_config import provider_home
    from ymir.lens_readiness import readiness
    monkeypatch.setenv('YMIR_OPENROUTER_HOME',str(tmp_path/'provider'))
    monkeypatch.setenv('OPENROUTER_API_KEY','fixture')
    monkeypatch.setenv('YMIR_GPU_BACKEND','modal')
    monkeypatch.setattr('stoa_lens.cloud.manifest',lambda:{})
    provider_home().mkdir()
    for model in (DEEPSEEK,ARGON):catalog_path(provider_home(),model).write_text(json.dumps(catalog(model)))
    status=readiness(LensBudget(tmp_path/'budget.sqlite',25))
    assert status['ready'] and status['modes']['smart']['ready']
    assert not status['modes']['frontier']['ready'] and 'remaining_usd' in status['budget']
    record=catalog(SOL);record['selected']['supported_parameters'].remove('tools')
    with pytest.raises(ValueError,match='parameters'):validate_catalog(record)
    record=catalog(ARGON);record['observed_at']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    with pytest.raises(ValueError,match='stale'):validate_catalog(record)
    record=catalog(DEEPSEEK);record['selected']['tag']='other'
    with pytest.raises(ValueError,match='endpoint'):validate_catalog(record)


def test_catalog_refresh_continues_after_one_unavailable_route(tmp_path, monkeypatch):
    def refresh(model, deadline):
        if model==SOL:raise ValueError('unavailable')
        return catalog(model)
    monkeypatch.setattr('stoa_lens.transport.refresh_catalog',refresh)
    result=refresh_pricing(provider_home=tmp_path)
    assert set(result['models'])=={DEEPSEEK,ARGON}
    assert result['errors']=={SOL:'ValueError'}
    assert catalog_path(tmp_path,DEEPSEEK).is_file()


def test_stream_assembly_and_continuation_are_lossless(tmp_path):
    acc=Accumulator()
    acc.add({'id':'gen-1','model':SOL,'provider':'OpenAI','choices':[{'delta':{
        'role':'assistant','tool_calls':[{'index':0,'id':'call-1','type':'function','function':{'name':'detect_','arguments':'{"region":'}}],
        'reasoning_details':[{'index':0,'type':'reasoning.encrypted','id':'reason-1','format':'openai-responses-v1','data':'opaque-'}]}}]})
    acc.add({'id':'gen-1','choices':[{'delta':{
        'tool_calls':[{'index':0,'type':'function','function':{'name':'objects','arguments':'[0,0,10,10]}'}}],
        'reasoning_details':[{'index':0,'type':'reasoning.encrypted','id':'reason-1','format':'openai-responses-v1','data':'continuation'}]}}]})
    acc.add({'choices':[{'delta':{},'finish_reason':'tool_calls'}],'usage':{'cost':.01}})
    result=acc.result();message=result['message']
    assert result['id']=='gen-1' and result['provider']=='OpenAI'
    assert message['tool_calls'][0]['function']=={'name':'detect_objects','arguments':'{"region":[0,0,10,10]}'}
    assert message['reasoning_details']==[{'index':0,'type':'reasoning.encrypted','id':'reason-1','format':'openai-responses-v1','data':'opaque-continuation'}]
    bodies=[]
    def requester(body,*args):bodies.append(copy.deepcopy(body));return response(SOL)
    t=transport(tmp_path,'frontier',requester)
    t.complete([message,{'role':'tool','tool_call_id':'call-1','content':'{}'}],[],time.monotonic()+10,1,final=True)
    assert bodies[0]['messages'][0]==message


def test_sdk_has_no_retries_and_retains_reasoning(tmp_path, monkeypatch):
    import asyncio
    import openai
    from stoa_lens.streaming import stream_sdk
    options={}
    class Chunk:
        def model_dump(self, **kwargs):return {'id':'gen','choices':[{'delta':{'reasoning_details':[{'index':0,'type':'reasoning.encrypted','data':'opaque'}],'content':'{}'},'finish_reason':'stop'}],'usage':{'cost':.02}}
    class Stream:
        def __aiter__(self):return self
        async def __anext__(self):
            if getattr(self,'done',False):raise StopAsyncIteration
            self.done=True;return Chunk()
        async def close(self):options['closed']=True
    class Raw:
        headers={'x-request-id':'req'}
        def parse(self):return Stream()
    class Client:
        def __init__(self, **kwargs):options.update(kwargs);self.chat=self.completions=self.with_raw_response=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def create(self,**kwargs):options['body']=kwargs;return Raw()
    monkeypatch.setattr(openai,'AsyncOpenAI',Client)
    body={'provider':{'only':['openai'],'allow_fallbacks':False,'require_parameters':True},'model':SOL}
    result=asyncio.run(stream_sdk(body,'fixture',time.monotonic()+10,tmp_path/'chunks.jsonl',tmp_path/'headers.json'))
    assert options['max_retries']==0 and options['base_url']=='https://openrouter.ai/api/v1'
    assert options['body']['extra_body']['provider']==body['provider']
    assert result['message']['reasoning_details'][0]['data']=='opaque' and options['closed']


def test_historical_ledger_is_unchanged_and_legacy_reservations_rejected(tmp_path):
    path=tmp_path/'budget.sqlite';budget=LensBudget(path,25)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO charges VALUES ('old','kimi-k3-1-pro','legacy',1000000,NULL,'unknown','old-id',NULL)")
        db.execute("INSERT INTO charges VALUES ('paid','deepseek-4-1-flash-1017','legacy',2000000,500000,'settled','paid-id',NULL)")
    before=budget.summary()['charges']
    assert LensBudget(path,25).summary()['charges']==before
    for model in PROVIDERS:budget.reserve(model,'new',.01)
    assert budget.summary()['charges'][:2]==before
    assert budget.summary()['unresolved_reserved_usd']==1.03
    with pytest.raises(ValueError):budget.reserve('kimi-k3-1-pro','bad',.01)
    with pytest.raises(BudgetError):LensBudget(path,30)


class Dfine:
    model='dfine-fixture'
    def __init__(self):self.calls=[]
    def predict(self,path,kind,timeout):
        self.calls.append(kind)
        return {'status':'complete','instances':[{'label':kind,'bbox_2d':[10,10,20,20],'confidence':.8}],'uncertainties':[]},{}
    def close(self):pass


def image(tmp_path):
    path=tmp_path/'page.png';Image.new('RGB',(100,80),'white').save(path)
    return [{'image':str(path),'width':100,'height':80}]


def test_exact_class_routing_argon_coordinates_and_validation(tmp_path):
    bodies=[]
    value={'status':'complete','instances':[{'label':'folding doors','bbox_2d':[100,100,200,200],'confidence':.8}], 'uncertainties':[]}
    def requester(body,*args):bodies.append(body);return response(ARGON,message={'role':'assistant','content':json.dumps(value)})
    t=transport(tmp_path,requester=requester);dfine=Dfine()
    registry=Registry(image(tmp_path),tmp_path/'views');detector=DetectionService(dfine,t.argon(),tmp_path/'detections')
    first=detector.detect(registry.initial,[0,0,100,80],'door','Find exact doors',10)
    assert first['routing']['backend']=='dfine' and dfine.calls==['door'] and bodies==[]
    second=detector.detect(registry.initial,[0,0,100,80],'folding door','Find subtype',10)
    assert second['routing']['backend']=='argon' and bodies[0]['model']==ARGON
    assert second['proposals'][0]['native_box_xyxy']==[10,8,20,16]
    assert 'exact object class' in bodies[0]['messages'][0]['content'][0]['text']
    value['instances'][0]['bbox_2d']=[200,100,100,200]
    with pytest.raises(DetectionError):detector.detect(registry.initial,[0,0,100,80],'folding door','Find subtype',10)
    assert len(t.budget.summary()['charges'])==2


@pytest.mark.parametrize('mode', ['smart','frontier'])
@pytest.mark.parametrize('compact', [False,True])
def test_service_uses_recorded_mode_in_both_loops(tmp_path, mode, compact):
    pages=image(tmp_path);cfg=resolve(mode);bodies=[]
    def requester(body,deadline,directory):
        bodies.append(copy.deepcopy(body))
        if len(bodies)==1:
            arguments={'region':[0,0,100,80],'reason':'Find doors'}
            if not compact:
                reg=next(m for m in body['messages'] if m['role']=='user')
                context=json.loads(reg['content'][0]['text'])
                arguments.update(object_class='door',view_id=context['initial_views'][0]['view_id'])
            message={'role':'assistant','content':None,'reasoning_details':[{'type':'reasoning.encrypted','data':'continuation','index':0}],
                     'tool_calls':[{'id':'find','type':'function','function':{'name':'detect_objects','arguments':json.dumps(arguments)}}]}
        else:
            detection=json.loads(next(m for m in body['messages'] if m['role']=='tool')['content'])
            ident=detection['proposals'][0]['id']
            value={'status':'answered','accepted_proposal_ids':[ident]}
            if not compact:
                value.update(count_requests=[{'page_id':detection['proposals'][0]['page_id'],'object_class':'door'}],rooms=[],readings=[],notes=[])
            message={'role':'assistant','content':json.dumps(value)}
        return response(cfg['orchestrator']['model'],message=message)
    t=transport(tmp_path,mode,requester);detector=DetectionService(Dfine(),t.argon(),tmp_path/'detections')
    kwargs={'agent':'compact_counter','count_scope':{'page_index':0,'target_classes':['door'],'description':'doors'}} if compact else {}
    class Geometry:
        calibrations={};candidates={}
    events=[]
    result=run(pages,'Count doors',tmp_path/'service',root=tmp_path,transport=t,detector=detector,geometry=Geometry(),configuration=cfg,progress=events.append,**kwargs)
    assert result['counts'][0]['count']==1
    assert result['configuration']['orchestrator']==cfg['orchestrator']['model']
    assert len(bodies)==2 and all(b['model']==cfg['orchestrator']['model'] for b in bodies)
    assert next(m for m in bodies[1]['messages'] if m['role']=='assistant')['reasoning_details'][0]['data']=='continuation'
    assert all(e['routing_mode']==mode for e in events if e['operation']=='response')
    manifest=json.loads((tmp_path/'service/request-manifest.json').read_text())
    assert manifest['configuration']==cfg and manifest['models']['untrained_detector']['model']==ARGON


def test_spending_limit_retains_partial_evidence_without_public_accounting_terms(tmp_path):
    from stoa_lens.bound_counter import BoundCounter
    registry=Registry(image(tmp_path),tmp_path/'views')
    detector=RoutedBoundDetector(DetectionService(Dfine(),None,tmp_path/'detections'),['door'])
    class Transport:
        def complete(self,messages,tools,deadline,index,final=False):
            if index==0:return {'role':'assistant','tool_calls':[{'id':'d','function':{'name':'detect_objects','arguments':'{"region":[0,0,100,80],"reason":"Find doors"}'}}]}
            raise BudgetError('Budget ceiling exhausted or reservations held: $25')
    events=[]
    harness=BoundCounter(registry,detector,Transport(),tmp_path/'counter',page_id=registry.initial['page_id'],target_classes=['door'],description='doors',backend='dfine',progress=events.append)
    result=harness.run('Count doors')
    assert len(result['proposals'])==1 and result['status']=='needs_review'
    assert 'Request incomplete' in result['unavailable_reason']
    assert not any(term in json.dumps(events).lower() for term in ('budget','ceiling','reserved','$25'))
    assert 'Budget ceiling' in (tmp_path/'counter/transcript.json').read_text()


def request(key='request', **extra):
    return {'request_id':key,'drawing_id':'cubi','selected_drawing_ids':['cubi'],'question':'Count doors',
            'project_id':'project','conversation_id':'chat','prior_job_ids':[],**extra}


def wait(store,ident):
    end=time.monotonic()+10
    while time.monotonic()<end:
        job=store.lookup(ident)
        if job['status'] in ('completed','failed','cancelled','interrupted'):return job
        time.sleep(.01)
    raise AssertionError('job did not finish')


def test_queue_snapshot_defaults_conflicts_context_and_legacy_replay(tmp_path):
    drawings=DrawingStore(uploads=tmp_path/'drawings');docs=DocumentStore(drawings,tmp_path/'documents');calls=[]
    def runner(pages,question,folder,**hooks):
        calls.append(copy.deepcopy({k:v for k,v in hooks.items() if k in ('configuration','conversation_context')}))
        hooks['progress']({'operation':'response','phase':'started','response_index':0})
        return {'status':'needs_review','counts':[],'proposals':[]}
    store=JobStore(drawings,docs,tmp_path/'jobs',runner=runner)
    start=store.start;store.start=lambda:None
    body=request();a=store.submit(body);body['routing_mode']='frontier'
    assert store.lookup(a)['manifest']['configuration']==resolve('smart')
    assert 'routing_mode' not in request()
    with pytest.raises(ValueError,match='Request ID'):store.submit(body)
    b=store.submit(request('second',routing_mode='frontier'))
    store.start=start
    try:
        store.start();assert wait(store,a)['status']=='completed';assert wait(store,b)['status']=='completed'
        assert [c['configuration']['routing_mode'] for c in calls]==['smart','frontier']
        assert store.events(b)[1]['status']=='running'
        assert next(e for e in store.events(b) if e['type']=='node')['tool']=='Sol'
        c=store.submit(request('third',routing_mode='frontier'));wait(store,c)
        assert [turn['job_id'] for turn in calls[-1]['conversation_context']]==[a,b]
        # Emulate an untouched pre-migration row and events, including unresolved history.
        with store.db() as db:
            legacy=request('legacy');signature=json.dumps(legacy,sort_keys=True)
            db.execute("UPDATE jobs SET request_id=?,request=?,signature=?,manifest=? WHERE id=?",('legacy',json.dumps(legacy),signature,json.dumps({'models':{'orchestrator':{'provider':'Verda','model':'deepseek-4-1-flash-1017'}}}),a))
        before=store.lookup(a);events=store.events(a);count=len(calls)
        assert store.submit(request('legacy'))==a
        assert store.submit(request('legacy',routing_mode='smart'))==a
        assert store.lookup(a)==before and store.events(a)==events and len(calls)==count
        with pytest.raises(ValueError):store.submit(request('legacy',routing_mode='frontier'))
    finally:store.stop()


def test_api_allowlist_and_route_unavailability_do_not_block_other_mode(tmp_path, monkeypatch):
    api=importlib.import_module('ymir.app')
    drawings=DrawingStore(uploads=tmp_path/'drawings');docs=DocumentStore(drawings,tmp_path/'documents')
    store=JobStore(drawings,docs,tmp_path/'jobs');store.start=lambda:None
    monkeypatch.setattr(api,'jobs',store);monkeypatch.setattr(api,'store',drawings)
    monkeypatch.setattr('ymir.lens_readiness.readiness',lambda b:{'modes':{'smart':{'ready':True},'frontier':{'ready':False}}})
    with TestClient(api.app) as client:
        for extra in ({'routing_mode':'custom'},{'model':SOL},{'routing_mode':SOL}):
            assert client.post('/api/ask',json=request(**extra)).status_code==422
        assert client.post('/api/ask',json=request(routing_mode='frontier')).status_code==503
    assert store.submit(request('smart'))
    assert store.budget.summary()['requests']==0


def test_provider_home_is_in_ymir_and_never_uses_experiment_state(monkeypatch):
    from stoa_lens.model_config import provider_home
    monkeypatch.delenv('YMIR_OPENROUTER_HOME',raising=False)
    monkeypatch.delenv('YMIR_DATA_DIR',raising=False)
    assert provider_home()==Path(__file__).resolve().parents[1]/'var/lens/openrouter'


def test_secret_redaction_and_unexpected_provider_model_fail_closed(tmp_path):
    t=transport(tmp_path,requester=lambda *a:response(SOL,message={'role':'assistant','content':'fixture-secret-value'}))
    with pytest.raises(ValueError,match='unexpected model'):
        t.complete([{'role':'user','content':'fixture-secret-value'}],[],time.monotonic()+10,0)
    assert t.budget.summary()['charges'][0]['actual_usd']==.012
    for path in (tmp_path/'job/api').rglob('*.json'):
        assert 'fixture-secret-value' not in path.read_text()


def test_pre_dispatch_deadline_never_reserves_or_calls(tmp_path):
    t=transport(tmp_path,requester=lambda *a:pytest.fail('Dispatched past deadline'))
    with pytest.raises(TimeoutError):t.complete([],[],time.monotonic()-1,0)
    assert t.budget.summary()['requests']==0


@pytest.mark.parametrize('mode', ['smart','frontier'])
def test_default_service_factories_dispatch_argon_for_untrained_count(tmp_path, monkeypatch, mode):
    # Exercise the actual service factory, including the Modal adapters, while
    # prohibiting all GPU/network calls. No checkpoint or experiment code is loaded.
    monkeypatch.setattr('stoa_lens.cloud.enabled',lambda:True)
    monkeypatch.setattr('stoa_lens.cloud.manifest',lambda:{'models':{'dfine':{'model':'dfine-fixture'},'raster2seq':{'model':'raster-fixture'}}})
    cfg=resolve(mode);bodies=[]
    def requester(body,deadline,directory):
        bodies.append(body['model'])
        if body['model']==ARGON:
            value={'status':'complete','instances':[{'label':'folding door','bbox_2d':[100,100,200,200],'confidence':None}],'uncertainties':[]}
        elif len(bodies)==1:
            return response(body['model'],message={'role':'assistant','tool_calls':[{'id':'d','type':'function',
                 'function':{'name':'detect_objects','arguments':'{"region":[0,0,100,80],"reason":"Find folding doors"}'}}]})
        else:
            result=json.loads(next(m['content'] for m in reversed(body['messages']) if m['role']=='tool'))
            value={'status':'answered','accepted_proposal_ids':[result['proposals'][0]['id']]}
        return response(body['model'],message={'role':'assistant','content':json.dumps(value)})
    t=transport(tmp_path,mode,requester)
    result=run(image(tmp_path),'Count folding doors',tmp_path/'factory',root=tmp_path,budget=t.budget,
               transport=t,configuration=cfg,agent='compact_counter',
               count_scope={'page_index':0,'target_classes':['folding door'],'description':'folding doors'})
    assert bodies==[cfg['orchestrator']['model'],ARGON,cfg['orchestrator']['model']]
    assert result['counts'][0]['count']==1 and result['configuration']['bound_counter']['backend']=='argon'
    assert result['model_calls'][1]['model']==ARGON
