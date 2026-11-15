"""No inference charges: injected runner, real snapshots, API streams and SQLite."""
import copy
import importlib
import json
import threading
import time
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from stoa_lens.budget import BudgetError
from stoa_lens.registry import Registry, sha
from stoa_lens.native_text import read_pdf_text
from ymir.documents import DocumentStore, SheetSelection, align_build
from ymir.drawings import DrawingStore
from ymir.lens_jobs import JobStore
from ymir.app import app

api=importlib.import_module('ymir.app')


def wait(jobs,ident):
    end=time.monotonic()+10
    while time.monotonic()<end:
        row=jobs.lookup(ident)
        if row['status'] in ('completed','cancelled','failed','interrupted'):return row
        time.sleep(.01)
    raise AssertionError(jobs.lookup(ident))


def req(key='r1',sheets=None,**kw):
    ids=sheets or ['cubi']
    return {'request_id':key,'drawing_id':ids[0],'selected_drawing_ids':ids,'question':'Count doors','project_id':'test-project','conversation_id':'test-conversation','prior_job_ids':[],**kw}


@pytest.fixture
def integration(tmp_path,monkeypatch):
    drawings=DrawingStore(uploads=tmp_path/'drawings');documents=DocumentStore(drawings,tmp_path/'documents');calls=[]
    def run(pages,question,folder,**hooks):
        calls.append((copy.deepcopy(pages),question,hooks['conversation_context']))
        r=Registry(pages,folder/'views');pid=r.initial['page_id']
        hooks['progress']({'operation':'tool','phase':'started','tool':'detect_objects','call_id':'d1'})
        hooks['progress']({'operation':'tool','phase':'completed','tool':'detect_objects','call_id':'d1','result':{'status':'complete'}})
        return {'status':'needs_review','accepted_proposal_ids':[], 'counts':[{'page_id':pid,'object_class':'door','count':None}],
          'proposals':[{'id':'p1','page_id':pid,'query_class':'door','native_box_xyxy':[10,10,40,40],'confidence':None}], 'rooms':[],'text_evidence':[],'readings':[], 'measurements':[], 'coverage':{'sources':[r.public_source(pid)]}}
    jobs=JobStore(drawings,documents,tmp_path/'lens',runner=run)
    monkeypatch.setattr(api,'store',drawings);monkeypatch.setattr(api,'documents',documents);monkeypatch.setattr(api,'jobs',jobs)
    yield jobs,documents,drawings,calls
    jobs.stop()


def test_durable_api_idempotency_reconnect_review_attribution(integration):
    jobs,documents,drawings,calls=integration
    with TestClient(app) as client:
        res=client.post('/api/ask',json=req());assert res.status_code==200,res.text
        events=[json.loads(x) for x in res.text.splitlines()]
        ident=events[0]['job_id'];assert events[0]['type']=='job'
        assert events[-1]['answer']['status']=='needs_review'
        assert events[-1]['answer']['confidence'] is None
        assert len(calls)==1
        again=client.post('/api/ask',json=req());assert again.text==res.text and len(calls)==1
        replay=client.get(f'/api/lens/jobs/{ident}/events?after=2')
        assert [json.loads(x)['seq'] for x in replay.text.splitlines()]==list(range(3,len(events)+1))
        assert client.post('/api/ask',json=req(question='Changed')).status_code==409
        manifest=client.get(f'/api/lens/jobs/{ident}/artifacts/manifest').json()
        assert 'image' not in manifest['pages'][0]
        assert client.get(f'/api/lens/jobs/{ident}/artifacts/secret').status_code==404
        review={'schema':'drawing-qa-review-v1','job_id':ident,'sheet_id':'cubi','drawing':{'id':'cubi'},'question':'Count doors','class':'door','user_count':1,
                'model':{'detector':{'name':'forged'}},'objects':[{'id':'p1','class':'door','bbox_xyxy':[10,10,40,40],'status':'accepted','proposal_id':'p1'}],'reviewed_at':'forged'}
        assert client.post('/api/reviews',json=review).status_code==200
        saved=client.get('/api/reviews?drawing_id=cubi').json()[-1]
        assert saved['model']['attribution']=='server' and saved['model']['detector']['name']=='Ymir'
        assert saved['reviewer']=='local_user' and saved['server_received_at']!='forged'
        assert jobs.lookup(ident)['answer']['counts'][0]['count'] is None
        review['sheet_id']='swiss';assert client.post('/api/reviews',json=review).status_code==422


def test_six_completed_turns_and_current_scope(integration):
    jobs,_,_,calls=integration
    ids=[]
    for i in range(8):
        ident=jobs.submit(req(str(i),sheets=['cubi','swiss'] if i==7 else ['cubi'],prior_job_ids=ids[-6:]));ids.append(ident)
        assert wait(jobs,ident)['status']=='completed'
    assert len(calls[-1][2])==6 and calls[-1][2][0]['job_id']==ids[1]
    manifest=json.loads((jobs.root/'jobs'/ids[-1]/'manifest.json').read_text())
    assert manifest['conversation_context']==calls[-1][2] and len(manifest['pages'])==2
    assert all(Path(p['image']).is_relative_to(jobs.root/'jobs'/ids[-1]) for p in manifest['pages'])
    with pytest.raises(ValueError,match='conversation'):jobs.submit(req('bad',conversation_id='other',prior_job_ids=[ids[0]]))
    with pytest.raises(ValueError,match='three'):jobs.submit(req('too-many',sheets=['cubi','swiss','finna','ac20-eg']))


def test_cancellation_single_worker_and_startup_interruption(integration):
    jobs,_,_,calls=integration;started=threading.Event();release=threading.Event();ops=[]
    def run(pages,q,folder,**hooks):
        started.set();release.wait(3);ops.append('current operation')
        if hooks['cancelled']():
            from stoa_lens.service import Cancelled
            raise Cancelled('cancelled')
        raise AssertionError('No further dispatch')
    jobs.runner=run
    a=jobs.submit(req());assert started.wait(3)
    b=jobs.submit(req('r2'));jobs.cancel(b);jobs.cancel(a);release.set()
    assert wait(jobs,a)['status']=='cancelled' and wait(jobs,b)['status']=='cancelled'
    assert ops==['current operation']
    jobs.stop()
    with jobs.db() as db:db.execute("UPDATE jobs SET status='running' WHERE id=?",(a,))
    jobs.start();assert jobs.lookup(a)['status']=='interrupted';assert len(ops)==1


def test_rotated_pdf_page_coverage_and_native_text(integration):
    jobs,docs,drawings,_=integration
    pdf=pymupdf.open()
    for i in range(5):
        page=pdf.new_page(width=300,height=220);page.insert_text((30,80),f'Sheet {i+1}: visible dimension 5 m');page.set_rotation(90 if i==3 else 0)
    raw=pdf.tobytes();doc=docs.upload(raw,'source.pdf');assert doc['page_count']==5 and doc['pages'][3]['rotation']==90
    result=docs.prepare(doc['id'],SheetSelection(pages=[2,4]))
    ids=[e['id'] for e in result['sheets']]
    ident=jobs.submit(req(sheets=ids));assert wait(jobs,ident)['status']=='completed'
    manifest=json.loads((jobs.root/'jobs'/ident/'manifest.json').read_text());r=Registry(manifest['pages'],jobs.root/'other-views')
    assert [p['source_page'] for p in r.sources.values()]==[2,4]
    assert all(p['document_page_count']==5 and p['packet_included_pages']==2 for p in r.sources.values())
    assert 'Sheet 4' in read_pdf_text(r.initial_views[1])['lines'][0]['text']
    assert docs.original(doc['id']).read_bytes()==raw
    assert drawings.raw(ids[0])['source']['build_to_source']


def test_svg_holes_explicit_scale_and_unavailable_geometry(integration,tmp_path):
    _,docs,drawings,_=integration
    raw=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path id="room" fill-rule="evenodd" d="M 5 5 L 95 5 L 95 95 L 5 95 Z M 40 40 L 60 40 L 60 60 L 40 60 Z"/></svg>'
    d=docs.upload(raw,'rooms.svg');assert d['selectors'][0]['id']=='room'
    selected=SheetSelection(options={'element_ids':['room']})
    no_scale=docs.prepare(d['id'],selected);s=drawings.raw(no_scale['sheets'][0]['id'])['lens_source']['native_source']
    from stoa_native_geometry import RegisteredNativeBackend
    c=RegisteredNativeBackend(s).candidates(s['source_id'],0)['candidates'][0]
    assert c['polygon']['holes'] and c['calibration'] is None
    selected=SheetSelection(options={'element_ids':['room'],'svg_scale':{'p0':[5,5],'p1':[95,5],'length':9,'unit':'m','reading':'9 m','reviewer':'Test reviewer'}})
    yes=docs.prepare(d['id'],selected);s=drawings.raw(yes['sheets'][0]['id'])['lens_source']['native_source'];c=RegisteredNativeBackend(s).candidates(s['source_id'],0)['candidates'][0]
    assert c['native_area_m2']==pytest.approx(77)
    assert c['calibration']['kind']=='human_confirmed_svg_length'
    assert isolated_native(s,tmp_path)['candidates'][0]['native_area_m2']==pytest.approx(77)
    bad=docs.upload(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path id="curve" d="M 0 0 C 1 2 3 4 5 6 Z"/></svg>','curves.svg')
    assert docs.prepare(bad['id'],SheetSelection(options={'element_ids':['curve']}))['status']=='unavailable'


def test_source_transform_maps_holes_boxes_and_scale():
    payload={'source':{'crop':[10,20,100,100]},'image':{'size':[90,80]},'scale':{'m_per_px':.1},'walls':[{'a':[0,0],'b':[10,10]}],
      'rooms':[{'polygon':[[0,0],[10,0],[10,10]],'holes':[[[2,2],[3,2],[3,3]]]}],'texts':[{'box':[1,2,3,4]}]}
    out=align_build(payload,(400,400),(200,200))
    assert out['walls'][0]['a']==[20,40] and out['texts'][0]['box']==[22,44,26,48]
    assert out['rooms'][0]['holes'][0][0]==[24,44] and out['scale']['m_per_px']==.05


def test_shared_budget_holds_reservations_and_refuses_over_cap(integration):
    jobs,_,_,_=integration
    key=jobs.budget.reserve('deepseek/deepseek-v4.1-flash-1017','test',24)
    assert jobs.budget.summary()['remaining_usd']==1
    with pytest.raises(BudgetError):jobs.budget.reserve('google/gemini-4-argon','test',2)
    jobs.budget.settle(key,None)
    assert jobs.budget.summary()['unresolved_reserved_usd']==24


def test_ifc_storey_inventory_and_units_are_source_bound(integration,tmp_path):
    """Reuse the native backend's analytic fixture, never benchmark annotations."""
    from native_fixture import make_ifc as fixture
    path=tmp_path/'millimetre-storeys.ifc';ids=fixture(path,millimetres=True,second_floor=True)
    _,docs,drawings,_=integration
    d=docs.upload(path.read_bytes(),path.name)
    assert {s['id'] for s in d['selectors']}==set(ids['storeys'])
    with pytest.raises(ValueError,match='storey'):docs.prepare(d['id'],SheetSelection())
    result=docs.prepare(d['id'],SheetSelection(options={'storey_id':ids['storeys'][1]}))
    source=drawings.raw(result['sheets'][0]['id'])['lens_source']['native_source']
    from stoa_native_geometry import RegisteredNativeBackend
    c=RegisteredNativeBackend(source).candidates(source['source_id'],0)['candidates']
    assert len(c)==1 and c[0]['native_area_m2']==pytest.approx(11)
    assert c[0]['calibration']['kind']=='native_ifc_units'
    assert len(c[0]['polygon']['holes'])==1
    isolated=isolated_native(source,tmp_path)
    assert isolated['candidates'][0]['native_area_m2']==pytest.approx(11)


def test_second_manager_cannot_dispatch_while_worker_owns_lock(integration):
    jobs,docs,drawings,calls=integration
    other=JobStore(drawings,docs,jobs.root,runner=lambda *a,**k:pytest.fail('Second inference worker dispatched'))
    jobs.start();other.start();assert other.thread is None
    ident=other.submit(req('shared-worker'))
    assert wait(jobs,ident)['status']=='completed' and len(calls)==1


def test_snapshot_changes_stop_before_runner_dispatch(integration):
    jobs,_,_,calls=integration
    # Pause only the test's worker startup, so mutation occurs while queued.
    start=jobs.start;jobs.start=lambda:None
    ident=jobs.submit(req('tamper'));jobs.start=start
    folder=jobs.root/'jobs'/ident
    (folder/'sources/0/image.png').write_bytes(b'changed pixels')
    jobs.start();assert wait(jobs,ident)['status']=='failed' and calls==[]


def test_progress_persists_execution_chain_and_detector_identity(integration):
    jobs,_,_,_=integration
    # Use a completed fixture job to exercise projection without paid inference.
    ident=jobs.submit(req('trace-projection'));assert wait(jobs,ident)['status']=='completed'
    with jobs.db() as db:db.execute('DELETE FROM events WHERE job=?',(ident,))
    events=[{'operation':'response','response_index':0,'phase':'started'},
            {'operation':'response','response_index':0,'phase':'completed'},
            {'operation':'tool','tool':'inspect','call_id':'crop1','response_index':0,'phase':'started'},
            {'operation':'tool','tool':'inspect','call_id':'crop1','response_index':0,'phase':'completed'},
            {'operation':'tool','tool':'inspect','call_id':'crop2','response_index':0,'phase':'started'},
            {'operation':'tool','tool':'inspect','call_id':'crop2','response_index':0,'phase':'completed'},
            {'operation':'response','response_index':1,'phase':'started'},
            {'operation':'response','response_index':1,'phase':'completed'},
            {'operation':'tool','tool':'detect_objects','call_id':'doors','response_index':1,'phase':'started','routing':{'backend':'dfine','requested_class':'door'},'model':'d-fine-l-junction-ema'}]
    for event in events:jobs._progress(ident,event)
    by_id={e['id']:e for e in jobs.events(ident) if e['type']=='node'}
    assert list(by_id)==['response-1','tool-1-crop1','tool-1-crop2','response-2','tool-2-doors']
    assert [e['parents'] for e in by_id.values()]==[[],['response-1'],['tool-1-crop1'],['tool-1-crop2'],['response-2']]
    assert by_id['tool-2-doors']['tool']=='D-FINE-L'
    assert by_id['tool-2-doors']['detail']=='d-fine-l-junction-ema'


def test_edit_saved_labels_append_revisions_and_rehydrate_by_job(integration):
    jobs,_,_,calls=integration
    with TestClient(app) as client:
        ident=json.loads(client.post('/api/ask',json=req('editable')).text.splitlines()[0])['job_id']
        machine=copy.deepcopy(jobs.lookup(ident)['answer'])
        review={'schema':'drawing-qa-review-v1','job_id':ident,'sheet_id':'cubi','drawing':{'id':'cubi'},'question':'Count doors','class':'door','user_count':1,
                'model':{},'objects':[{'id':'p1','class':'door','bbox_xyxy':[10,10,40,40],'status':'accepted','proposal_id':'p1'}],'reviewed_at':'2026-11-15'}
        first=client.post('/api/reviews',json=review).json()
        assert first['record']['id']==first['id'] and first['record']['reviewer']=='local_user'
        review.update(revision_of=first['id'],user_count=2)
        review['objects'][0]['bbox_xyxy']=[12,12,42,42]
        review['objects'].append({'id':'u1','class':'door','bbox_xyxy':[50,50,80,80],'status':'accepted'})
        second=client.post('/api/reviews',json=review).json()
        assert second['record']['revision_of']==first['id']
        assert client.post('/api/reviews',json=review).status_code==409
        latest=client.get(f'/api/lens/jobs/{ident}').json()['human_reviews']
        assert len(latest)==1 and latest[0]['user_count']==2 and latest[0]['id']==second['id']
        listed=client.get('/api/lens/jobs?project_id=test-project&conversation_id=test-conversation').json()
        assert listed[0]['human_reviews']==latest
        history=[r for r in client.get('/api/reviews').json() if r.get('job_id')==ident]
        assert len(history)==2 and history[0]['objects'][0]['bbox_xyxy']==[10,10,40,40]
        assert jobs.lookup(ident)['answer']==machine and len(calls)==1
        review['class']='window';assert client.post('/api/reviews',json=review).status_code==422


def test_plain_count_copy_keeps_proposal_provenance():
    from ymir.lens_answers import answer_for
    result={'status':'answered','counts':[{'page_id':'page','object_class':'window','count':3}],
            'proposals':[{'id':'p','page_id':'page','query_class':'window','native_box_xyxy':[1,2,3,4],'confidence':None}],
            'accepted_proposal_ids':['p']}
    answer=answer_for('job',result,{'page':'sheet'})
    assert answer['text']=='I found 3 windows.'
    assert answer['citations'][0]['object_class']=='window' and answer['citations'][0]['proposal_id']=='p'


def isolated_native(source,folder):
    from stoa_vista_specialists.geometry import GeometryTools
    geometry=GeometryTools(folder/'without-source-checkout',folder/'isolated',source['source_id'],source['source_sha256'],source['page'],source['width'],source['height'],native_source=source)
    try:return geometry.native_room_candidates(source['source_id'],source['page'],timeout=20)
    finally:geometry.close()
