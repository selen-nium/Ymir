import pytest
from ymir.lens_intent import bind_count_scope

@pytest.fixture(autouse=True)
def injected_trained_head_policy(monkeypatch):
    # Every detector in this module is a fixture; no checkpoint assets in CI.
    from stoa_lens.dfine import HEADS
    monkeypatch.setattr('stoa_lens.routing.verified_head_policy',lambda:{'heads':list(HEADS),'test_only':True})

@pytest.mark.parametrize('question,classes',[
 ('How many doors are shown on this floor plan?',['door']),
 ('How many toilet bowls are drawn on the page?',['toilet_bowl']),
 ('How many windows are there?',['window']),
 ('Count the bathtubs and showers.',['bathtub','shower']),
 ('How many showers and bathtubs are drawn on this floor plan in total?',['shower','bathtub']),
 ('How many windows are there in total?',['window']),
 ('Count sliding doors',['door_sliding']),
])
def test_explicit_one_page_count(question,classes):
    scope=bind_count_scope(question,[{'image':'page.png'}])
    assert scope['page_index']==0 and scope['target_classes']==classes and scope['backend']=='routed'

@pytest.mark.parametrize('question',[
 'How many doors are in the kitchen?',
 'How many bedrooms are there?',
 'How many door types are on the schedule?',
 'How many folding doors are there?',
 'How many doors and windows are shown?',
 'Are there doors?',
 'Count doors on all floors.',
 'Count the doors and explain the layout.',
 'How many baths are there?',
])
def test_ambiguous_requests_remain_general(question):
    assert bind_count_scope(question,[{'image':'page.png'}]) is None

def test_multipage_and_native_preview_remain_general():
    assert bind_count_scope('How many doors are there?',[{},{}]) is None
    assert bind_count_scope('How many doors are there?',[{'native_source':{'native_geometry':{}}}]) is None


def test_job_store_binds_scope_and_reuses_injected_budget(tmp_path):
    import time
    from stoa_lens.budget import LensBudget
    from stoa_lens.registry import Registry
    from ymir.drawings import DrawingStore
    from ymir.documents import DocumentStore
    from ymir.lens_jobs import JobStore
    drawings=DrawingStore(uploads=tmp_path/'drawings');documents=DocumentStore(drawings,tmp_path/'documents')
    shared=LensBudget(tmp_path/'shared.sqlite',15);calls=[]
    def fake(pages,question,folder,**kwargs):
        calls.append(kwargs)
        view=Registry(pages,folder/'views').initial
        return {'status':'needs_review','accepted_proposal_ids':[],'counts':[{'page_id':view['page_id'],'object_class':'door','count':None}],
                'proposals':[],'rooms':[],'readings':[],'text_evidence':[],'measurements':[]}
    jobs=JobStore(drawings,documents,tmp_path/'jobs',runner=fake,budget=shared)
    request={'request_id':'test','drawing_id':'cubi','selected_drawing_ids':['cubi'],
       'question':'How many doors are shown on this floor plan?','project_id':'p','conversation_id':'c','prior_job_ids':[]}
    try:
        ident=jobs.submit(request);deadline=time.monotonic()+10
        while time.monotonic()<deadline and jobs.lookup(ident)['status'] not in ('completed','failed'):time.sleep(.01)
        assert jobs.lookup(ident)['status']=='completed' and calls[0]['budget'] is shared
        assert calls[0]['agent']=='compact_counter' and calls[0]['count_scope']['target_classes']==['door']
        assert jobs.lookup(ident)['manifest']['count_scope']==calls[0]['count_scope']
        assert shared.summary()['cap_usd']==15
    finally:jobs.stop()


def test_grouped_routed_detection_counts_each_backend_invocation(tmp_path,monkeypatch):
    import time
    from PIL import Image
    from stoa_lens.registry import Registry
    from stoa_lens.detection import DetectionService
    from stoa_lens.dfine import HEADS
    monkeypatch.setattr('stoa_lens.routing.verified_head_policy',lambda:{'heads':list(HEADS),'test_only':True})
    from stoa_lens.bound_counter import BoundCounter,RoutedBoundDetector
    image=tmp_path/'p.png';Image.new('RGB',(100,80),'white').save(image)
    registry=Registry([{'image':str(image),'width':100,'height':80}],tmp_path/'views')
    class Model:
        model='dfine-test'
        def __init__(self):self.calls=[]
        def predict(self,path,kind,timeout):
            self.calls.append(kind);b=[10,10,20,20] if kind=='bathtub' else [30,10,40,20]
            return {'status':'complete','instances':[{'label':kind,'bbox_2d':b,'confidence':.8}],'uncertainties':[]},{}
        def close(self):pass
    class Kimi:
        model='kimi-test'
        def predict(self,*args):pytest.fail('No Kimi fallback for trained class')
    model=Model();detector=RoutedBoundDetector(DetectionService(model,Kimi(),tmp_path/'det'),['bathtub','shower'])
    h=BoundCounter(registry,detector,object(),tmp_path/'job',page_id=registry.initial['page_id'],
       target_classes=['bathtub','shower'],description='bathtubs and showers',backend='dfine')
    h.tool_deadline=time.monotonic()+20
    result,_=h.tool('detect_objects',{'region':[0,0,100,80],'reason':'Find bathing fixtures'})
    assert model.calls==['bathtub','shower'] and h.uses['detect_objects']==2
    answer=h.bind({'status':'answered','accepted_proposal_ids':[p['id'] for p in result['proposals']]})
    assert answer['count']==2 and answer['counts'][0]['object_classes']==['bathtub','shower']


def test_compact_progress_projects_actual_dfine_to_job_trace(tmp_path):
    import json
    from PIL import Image
    from stoa_lens.registry import Registry
    from stoa_lens.detection import DetectionService
    from stoa_lens.bound_counter import BoundCounter,RoutedBoundDetector
    from ymir.lens_jobs import JobStore
    image=tmp_path/'p.png';Image.new('RGB',(100,80),'white').save(image)
    registry=Registry([{'image':str(image),'width':100,'height':80}],tmp_path/'views')
    class Model:
        model='d-fine-test'
        def predict(self,path,kind,timeout):return {'status':'complete','instances':[{'label':kind,'bbox_2d':[10,10,20,20],'confidence':.8}],'uncertainties':[]},{}
        def close(self):pass
    class Kimi:
        model='kimi-test'
        def predict(self,*args):pytest.fail('unexpected Kimi')
    class Transport:
        def complete(self,messages,tools,deadline,index,final=False):
            if index==0:return {'role':'assistant','content':None,'tool_calls':[{'id':'x','type':'function','function':{'name':'detect_objects','arguments':json.dumps({'region':[0,0,100,80],'reason':'Find doors'})}}]}
            result=next(json.loads(m['content']) for m in reversed(messages) if m.get('role')=='tool')
            return {'role':'assistant','content':json.dumps({'status':'answered','accepted_proposal_ids':[result['proposals'][0]['id']]})}
    jobs=JobStore(None,None,tmp_path/'jobs');detector=RoutedBoundDetector(DetectionService(Model(),Kimi(),tmp_path/'det'),['door'])
    counter=BoundCounter(registry,detector,Transport(),tmp_path/'counter',page_id=registry.initial['page_id'],target_classes=['door'],
      description='doors',backend='dfine',progress=lambda ev:jobs._progress('trace-test',ev))
    assert counter.run('Count doors')['count']==1
    with jobs.db() as db:events=[json.loads(row['event']) for row in db.execute('SELECT event FROM events ORDER BY seq')]
    nodes=[e for e in events if e['type']=='node' and e.get('tool')=='D-FINE-L']
    assert nodes and nodes[0]['detail']=='d-fine-test'
    activities=[e['activity'] for e in events if e['type']=='progress' and e['activity'].get('tool')=='detect_objects']
    assert all(e['routing']['backend']=='dfine' and e['model']=='d-fine-test' for e in activities)


def test_mixed_group_progress_does_not_claim_single_model(tmp_path):
    from PIL import Image
    from stoa_lens.registry import Registry
    from stoa_lens.detection import DetectionService
    from stoa_lens.bound_counter import BoundCounter,RoutedBoundDetector
    image=tmp_path/'p.png';Image.new('RGB',(10,10),'white').save(image)
    registry=Registry([{'image':str(image),'width':10,'height':10}],tmp_path/'views')
    dfine=type('D',(),{'model':'d-fine-test'})();kimi=type('K',(),{'model':'kimi-test'})()
    detector=RoutedBoundDetector(DetectionService(dfine,kimi,tmp_path/'det'),['door','chair']);events=[]
    counter=BoundCounter(registry,detector,object(),tmp_path/'job',page_id=registry.initial['page_id'],
      target_classes=['door','chair'],description='doors and chairs',backend='mixed',progress=events.append)
    counter._emit(operation='tool',phase='started',tool='detect_objects')
    assert events[0]['routing']['backend']=='mixed' and 'model' not in events[0]
    assert events[0]['models']==['d-fine-test','kimi-test']
